"""
core/account_queues.py
按设备独立的人工账号队列 — v1.2.0 生产取号模型(替代 QQ 群取号)。

核心设计:
  一台手机 = 一个 DeviceAccountQueue, 唯一 Key = ADB Serial(禁止用
  model 做 Key — 同型号手机可能多台)。

  DeviceAccountQueue         每台设备独立的线程安全 FIFO 队列
  AccountTask                队列任务单元(复用账号任务模型字段)
  QueueAccountStatus         WAITING/RUNNING/SUCCESS/FAILED/RETRY/
                             INTERRUPTED/CANCELLED
  GlobalAccountExecutionRegistry
                             最后一道保护: 同一 username 不得同时在
                             两台设备 RUNNING(即使人工强制跨设备重复添加)
  ManualDeviceQueueManager   dict[serial, DeviceAccountQueue] + 全局统计
  ManualDeviceQueueProvider  AccountProvider 子类(新默认生产账号来源)

边界:
  - 队列只存在于内存: 关闭 EXE = 队列清空(规格第 28 节), 历史记录由
    SQLite 永久保留; 设备断线(ADB offline)→ 队列保留(第 53 节)。
  - 禁止跨设备自动偷号: 严格 STRICT_DEVICE_QUEUE(第 19 节)。
  - 密码永不进入日志/snapshot/SQLite 历史 — 只存在于内存 AccountTask。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from itertools import count
from typing import Callable, Optional

from core.logger import mask_account

logger = logging.getLogger(__name__)

# 队列内账号最大失败重试次数(与 accounts 表默认一致)
DEFAULT_MAX_RETRY = 3

# 进程内全局任务 ID 计数器(时间戳毫秒会碰撞 — 同一毫秒加两个号就重号)
_id_seq = count(1)


class QueueAccountStatus(str, Enum):
    """账号队列生命周期状态(规格第 17 节)。"""
    WAITING = "WAITING"            # 等待执行
    RUNNING = "RUNNING"            # 执行中(当前账号)
    SUCCESS = "SUCCESS"            # 执行成功
    FAILED = "FAILED"              # 超过最大重试, 最终失败
    RETRY = "RETRY"                # 失败待重试
    INTERRUPTED = "INTERRUPTED"    # 被停止/重置打断, 恢复时优先执行
    CANCELLED = "CANCELLED"        # 已取消

    @property
    def display(self) -> str:
        """GUI 中文显示(规格第 17 节)。"""
        return {
            QueueAccountStatus.WAITING: "等待",
            QueueAccountStatus.RUNNING: "运行中",
            QueueAccountStatus.SUCCESS: "完成",
            QueueAccountStatus.FAILED: "失败",
            QueueAccountStatus.RETRY: "待重试",
            QueueAccountStatus.INTERRUPTED: "已中断",
            QueueAccountStatus.CANCELLED: "已取消",
        }[self]

    @property
    def is_claimable(self) -> bool:
        """可被 Worker 领取执行。"""
        return self in (QueueAccountStatus.WAITING,
                        QueueAccountStatus.RETRY,
                        QueueAccountStatus.INTERRUPTED)

    @property
    def is_terminal(self) -> bool:
        return self in (QueueAccountStatus.SUCCESS,
                        QueueAccountStatus.FAILED,
                        QueueAccountStatus.CANCELLED)

    @property
    def is_removable(self) -> bool:
        """可被人工删除(规格第 32 节): 仅 WAITING/RETRY。
        INTERRUPTED 是「停止恢复」要保留的当前账号, 不可删。"""
        return self in (QueueAccountStatus.WAITING, QueueAccountStatus.RETRY)

    @property
    def is_editable(self) -> bool:
        """可人工编辑(规格第 35 节): 仅 WAITING。"""
        return self == QueueAccountStatus.WAITING


@dataclass
class AccountTask:
    """队列任务单元(规格第 16 节: 复用已有账号任务模型)。

    adapter 只读 .account/.password/.masked() — 不关心账号来源。
    """
    id: int
    username: str
    password: str = ""
    device_serial: str = ""
    status: QueueAccountStatus = QueueAccountStatus.WAITING
    retry_count: int = 0
    max_retry: int = DEFAULT_MAX_RETRY
    last_error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    @property
    def account(self) -> str:
        """adapter 兼容别名(login 使用 account.account)。"""
        return self.username

    def masked(self) -> str:
        """脱敏显示: abc***123(与 models.account.Account 一致)。"""
        return mask_account(self.username)

    def to_row(self) -> dict:
        """GUI 队列表格行 — 绝不包含密码。"""
        return {
            "id": self.id,
            "username": self.username,
            "masked": self.masked(),
            "status": self.status.value,
            "status_display": self.status.display,
            "retry_count": self.retry_count,
            "created_at": self.created_at,
        }


class DeviceAccountQueue:
    """单台设备的账号队列(线程安全, 锁范围小, 不阻塞自动化)。

    FIFO: 先输入先执行。内部 deque 只放可领取状态的任务;
    RUNNING 任务在 current 槽; 终态只累计计数(第 15 节字段)。
    """

    def __init__(self, device_serial: str,
                 id_factory: Optional[Callable[[], int]] = None,
                 on_change: Optional[Callable[[], None]] = None):
        self.device_serial = device_serial
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._deque: deque[AccountTask] = deque()
        self.current: Optional[AccountTask] = None
        self.completed_count = 0     # 本次会话累计成功(第 15 节)
        self.failed_count = 0        # 本次会话累计最终失败
        self._id_factory = id_factory or (lambda: next(_id_seq))
        self._on_change = on_change

    # ── 入队(人工 GUI 调用) ──

    def add_task(self, username: str, password: str = "",
                 to_front: bool = False) -> tuple[Optional[AccountTask], bool]:
        """加入队列。返回 (task, added) — 重复账号 added=False。

        to_front: 紧急插队(第 47 节) — 当前账号完成后的下一条。
        """
        username = (username or "").strip()
        with self._lock:
            dup = self.find_by_username(username)
            if dup is not None:
                return dup, False
            task = AccountTask(id=self._id_factory(),
                               username=username, password=password,
                               device_serial=self.device_serial)
            if to_front:
                self._deque.appendleft(task)
            else:
                self._deque.append(task)
            self._notify()
            logger.info("[队列] %s 账号 %s 加入(等待 %d)",
                        self.device_serial, task.masked(),
                        len(self._deque))
            return task, True

    def add_batch(self, pairs: list[tuple[str, str]],
                  to_front: bool = False) -> tuple[int, int]:
        """批量加入 [(username, password), ...] → (新增, 重复跳过)。"""
        added = skipped = 0
        for username, password in pairs:
            _, ok = self.add_task(username, password, to_front=to_front)
            if ok:
                added += 1
            else:
                skipped += 1
        return added, skipped

    # ── 领取/唤醒(Worker 调用) ──

    def pop_next(self) -> Optional[AccountTask]:
        """领取下一个任务 → RUNNING。空队列返回 None。

        领取顺序(与 accounts 表 claim_next 一致):
        INTERRUPTED/WAITING 先于 RETRY(RETRY 账号延后, 坏账号不饿死
        正常账号); 同优先级按 FIFO。紧急插队(appendleft)自然优先。

        并发消费保护: current 仍被占用时(心跳重建/陈旧 Worker 领取后
        未归还), 原任务按 INTERRUPTED 插回队首并立即收回 — 绝不静默
        丢号(生产上每设备一个 Worker, 但心跳重建时新旧 Worker 可能
        短暂共存)。
        """
        with self._lock:
            if self.current is not None:
                stale = self.current
                stale.status = QueueAccountStatus.INTERRUPTED
                stale.last_error = stale.last_error or "被新 Worker 接管"
                stale.started_at = None
                self.current = None
                self._deque.appendleft(stale)
            if not self._deque:
                return None
            task = None
            for i, t in enumerate(self._deque):
                if t.status != QueueAccountStatus.RETRY:
                    if i:
                        self._deque.rotate(-i)     # 目标移到队首
                    task = self._deque.popleft()
                    break
            if task is None:
                task = self._deque.popleft()       # 只剩 RETRY
            task.status = QueueAccountStatus.RUNNING
            task.started_at = time.time()
            task.last_error = ""
            self.current = task
            self._notify()
            return task

    def wait_for_task(self, timeout: float) -> bool:
        """空队列等待 — 新账号加入立即唤醒(毫秒级, 第 23/61 节)。

        返回 True=有可领取任务; False=超时(Worker 循环重检)。
        """
        with self._cond:
            if self._deque:
                return True
            self._cond.wait(timeout)
            return bool(self._deque)

    def defer_task(self, task_id: int) -> bool:
        """移回队尾(执行锁冲突时跳过该账号, 不阻塞队列)。"""
        with self._lock:
            for i, t in enumerate(self._deque):
                if t.id == task_id:
                    self._deque.rotate(-i)     # 目标移到队首
                    self._deque.popleft()
                    self._deque.append(t)
                    return True
            if self.current is not None and self.current.id == task_id:
                self.current.status = QueueAccountStatus.WAITING
                self.current.started_at = None
                self._deque.append(self.current)
                self.current = None
            return True

    def front_interrupted(self) -> Optional[AccountTask]:
        """队首 INTERRUPTED 任务(停止恢复: 重启后优先继续它)。"""
        with self._lock:
            for t in self._deque:
                if t.status == QueueAccountStatus.INTERRUPTED:
                    return t
            return None

    # ── 状态流转(Worker 调用, 签名与 AccountRepository 对齐) ──

    def mark_running(self, task_id: int, device_serial: str = ""):
        """领取后标记(幂等 — pop_next 已置 RUNNING)。"""
        with self._lock:
            if self.current is not None and self.current.id == task_id:
                if self.current.status != QueueAccountStatus.RUNNING:
                    self.current.status = QueueAccountStatus.RUNNING
                    self.current.started_at = time.time()

    def mark_success(self, task_id: int, device_serial: str = ""):
        with self._lock:
            t = self._take_current(task_id)
            if t is None:
                return
            t.status = QueueAccountStatus.SUCCESS
            t.finished_at = time.time()
            self.completed_count += 1
            self._notify()

    def mark_failed(self, task_id: int, device_serial: str = "",
                    error: str = ""):
        with self._lock:
            t = self._take_current(task_id)
            if t is None:
                return
            t.status = QueueAccountStatus.FAILED
            t.finished_at = time.time()
            t.last_error = error or t.last_error
            self.failed_count += 1
            self._notify()

    def mark_retry(self, task_id: int, device_serial: str = "",
                   error: str = "") -> Optional[QueueAccountStatus]:
        """失败 → RETRY/FAILED(按最大重试), 返回最终状态。

        语义与 accounts 表一致: max_retry=3 表示最多重试 3 次。
        RETRY 排到队尾(坏账号不饿死正常账号 — 与旧领取顺序一致)。
        """
        with self._lock:
            t = self._take_current(task_id)
            if t is None:
                return None
            t.retry_count += 1
            t.last_error = error or t.last_error
            if t.retry_count > t.max_retry:
                t.status = QueueAccountStatus.FAILED
                t.finished_at = time.time()
                self.failed_count += 1
            else:
                t.status = QueueAccountStatus.RETRY
                t.started_at = None
                self._deque.append(t)          # 队尾重试
            self._notify()
            return t.status

    def mark_interrupted(self, task_id: int, reason: str = ""):
        """停止/重置打断 → INTERRUPTED, 插回队首(下次启动优先恢复)。"""
        with self._lock:
            t = self._take_current(task_id)
            if t is None:
                return
            t.status = QueueAccountStatus.INTERRUPTED
            t.last_error = reason or t.last_error
            t.started_at = None
            self._deque.appendleft(t)          # 队首优先恢复
            self._notify()

    def release(self, task_id: int, reason: str = ""):
        """未开始执行就释放回 WAITING(队首, 不烧重试)。"""
        with self._lock:
            t = self._take_current(task_id)
            if t is None:
                return
            t.status = QueueAccountStatus.WAITING
            t.last_error = reason or t.last_error
            t.started_at = None
            self._deque.appendleft(t)
            self._notify()

    def _take_current(self, task_id: int) -> Optional[AccountTask]:
        """从 current 槽取出(状态流转统一入口)。"""
        if self.current is None or self.current.id != task_id:
            # 不在 current(已被释放) — 幂等忽略
            return None
        t = self.current
        self.current = None
        return t

    # ── 人工队列编辑(GUI 调用) ──

    def find_by_username(self, username: str) -> Optional[AccountTask]:
        """查当前队列(等待 + 运行中)是否有该账号(第 36 节重复检测)。"""
        with self._lock:
            if self.current is not None and \
                    self.current.username == username:
                return self.current
            for t in self._deque:
                if t.username == username:
                    return t
            return None

    def remove_task(self, task_id: int) -> bool:
        """删除待执行账号 — 仅 WAITING/RETRY(第 32 节)。"""
        with self._lock:
            for i, t in enumerate(self._deque):
                if t.id == task_id and t.status.is_removable:
                    del self._deque[i]
                    self._notify()
                    logger.info("[队列] %s 删除账号 %s",
                                self.device_serial, t.masked())
                    return True
            return False

    def update_task(self, task_id: int, username: str, password: str
                    ) -> tuple[bool, str]:
        """编辑 WAITING 账号(第 35 节)。返回 (ok, error)。"""
        username = (username or "").strip()
        with self._lock:
            for t in self._deque:
                if t.id == task_id:
                    if not t.status.is_editable:
                        return False, "仅「等待」状态的账号可以编辑"
                    if not username:
                        return False, "账号不能为空"
                    dup = next((x for x in self._deque
                                if x.username == username and x.id != task_id),
                               None)
                    if dup is not None:
                        return False, "该账号已在此设备队列中"
                    t.username = username
                    t.password = password
                    self._notify()
                    logger.info("[队列] %s 编辑账号 → %s",
                                self.device_serial, t.masked())
                    return True, ""
            return False, "账号不存在"

    def clear_waiting(self) -> int:
        """清空待执行(第 33 节): 仅 WAITING/RETRY, 不动当前账号。
        INTERRUPTED 是停止保留的当前账号, 也不清。"""
        with self._lock:
            kept = deque(t for t in self._deque
                         if not t.status.is_removable)
            removed = len(self._deque) - len(kept)
            self._deque = kept
            if removed:
                self._notify()
                logger.info("[队列] %s 清空待执行 %d 个",
                            self.device_serial, removed)
            return removed

    def move_task(self, task_id: int, direction: str) -> bool:
        """上移/下移待执行账号(第 46 节; WAITING/RETRY 可移)。"""
        if direction not in ("up", "down"):
            return False
        with self._lock:
            idx = next((i for i, t in enumerate(self._deque)
                        if t.id == task_id and t.status.is_removable), None)
            if idx is None:
                return False
            target = idx - 1 if direction == "up" else idx + 1
            if not (0 <= target < len(self._deque)):
                return False
            self._deque[idx], self._deque[target] = \
                self._deque[target], self._deque[idx]
            self._notify()
            return True

    def move_to_front(self, task_id: int) -> bool:
        """插到队首(第 47 节)。不打断当前 RUNNING 账号。"""
        with self._lock:
            idx = next((i for i, t in enumerate(self._deque)
                        if t.id == task_id and t.status.is_removable), None)
            if idx is None or idx == 0:
                return idx == 0
            t = self._deque[idx]
            del self._deque[idx]
            self._deque.appendleft(t)
            self._notify()
            return True

    def get_task(self, task_id: int) -> Optional[AccountTask]:
        with self._lock:
            if self.current is not None and self.current.id == task_id:
                return self.current
            for t in self._deque:
                if t.id == task_id:
                    return t
            return None

    # ── 查询/统计(快照绝不带密码) ──

    def counts(self) -> dict:
        with self._lock:
            c = {s.value: 0 for s in QueueAccountStatus}
            for t in self._deque:
                c[t.status.value] += 1
            if self.current is not None:
                c[QueueAccountStatus.RUNNING.value] += 1
            c["waiting"] = len(self._deque)
            c["pending_total"] = len(self._deque)
            c["completed"] = self.completed_count
            c["failed_total"] = self.failed_count
            return c

    def pending_pairs(self) -> list[tuple[str, str]]:
        """等待中账号的 (username, password) — Provider 只读视图。"""
        with self._lock:
            return [(t.username, t.password) for t in self._deque]

    def pending_total(self) -> int:
        with self._lock:
            return len(self._deque)

    def has_claimable(self) -> bool:
        with self._lock:
            return bool(self._deque)

    def snapshot(self) -> dict:
        """GUI 快照: 当前账号 + 有序队列(#1..#N), 无密码。"""
        with self._lock:
            rows = []
            if self.current is not None:
                rows.append(self.current.to_row())
            for t in self._deque:
                rows.append(t.to_row())
            c = {s.value: 0 for s in QueueAccountStatus}
            for t in self._deque:
                c[t.status.value] += 1
            if self.current is not None:
                c[QueueAccountStatus.RUNNING.value] += 1
            return {
                "tasks": rows,
                "current": self.current.to_row()
                           if self.current is not None else None,
                "waiting": c[QueueAccountStatus.WAITING.value],
                "retry": c[QueueAccountStatus.RETRY.value],
                "interrupted": c[QueueAccountStatus.INTERRUPTED.value],
                "running": c[QueueAccountStatus.RUNNING.value],
                "success": self.completed_count,
                "failed": self.failed_count,
                "pending_total": len(self._deque),
            }

    def clear_all(self):
        """应用关闭: 清空队列(第 28 节 — 历史由 SQLite 保留)。"""
        with self._lock:
            self._deque.clear()
            self.current = None
            self.completed_count = 0
            self.failed_count = 0
            self._notify()

    def _notify(self):
        self._cond.notify_all()
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception as e:  # GUI 回调异常不影响队列
                logger.debug("[队列] on_change 回调异常: %s", e)


class GlobalAccountExecutionRegistry:
    """全局账号执行锁(第 38/39 节) — 最后一道保护:

    同一 username 不得同时在两台设备 RUNNING, 即使操作员跨设备
    强制重复添加。Worker 执行前原子获取, 完成后释放。
    """

    def __init__(self):
        self._lock = threading.Lock()
        # username → (device_serial, task_id, started_at)
        self._active: dict[str, tuple[str, int, float]] = {}

    def try_acquire(self, username: str, device_serial: str,
                    task_id: int) -> bool:
        """原子获取执行权。同设备重复获取幂等成功。"""
        with self._lock:
            holder = self._active.get(username)
            if holder is None:
                self._active[username] = (device_serial, task_id, time.time())
                return True
            if holder[0] == device_serial:
                self._active[username] = (device_serial, task_id,
                                          time.time())
                return True
            return False

    def release(self, username: str, device_serial: str = "") -> bool:
        with self._lock:
            holder = self._active.get(username)
            if holder is None:
                return False
            if device_serial and holder[0] != device_serial:
                return False     # 只能释放自己的锁
            self._active.pop(username, None)
            return True

    def owner_of(self, username: str) -> Optional[str]:
        with self._lock:
            holder = self._active.get(username)
            return holder[0] if holder else None

    def release_all_for(self, device_serial: str) -> int:
        """释放某设备持有的全部锁(Worker 退出/重置)。"""
        with self._lock:
            victims = [u for u, (s, _, _) in self._active.items()
                       if s == device_serial]
            for u in victims:
                self._active.pop(u, None)
            return len(victims)

    def active_snapshot(self) -> list[dict]:
        """诊断用: [{username_hash, serial, started_at}] — 不泄露账号。"""
        with self._lock:
            return [{"username_hash": mask_account(u),
                     "serial": s, "started_at": ts}
                    for u, (s, _, ts) in sorted(self._active.items())]


class ManualDeviceQueueManager:
    """按设备管理队列的容器(线程安全) — Key 必须是 ADB Serial(第 4 节)。

    设备断线/重连、DeviceProfile 重建都不影响队列(第 51/52 节);
    只有应用关闭才清空(第 28 节)。
    """

    def __init__(self, on_change: Optional[Callable[[str], None]] = None):
        self._lock = threading.Lock()
        self._queues: dict[str, DeviceAccountQueue] = {}
        # itertools.count 在 CPython 下每次 next() 原子 — 避免在队列锁内
        # 再取管理器锁(锁顺序反转 → 潜在死锁)。
        self._id_counter = count(1)
        self._on_change = on_change
        self.execution_registry = GlobalAccountExecutionRegistry()

    def _next_id(self) -> int:
        return next(self._id_counter)

    def queue_for(self, serial: str) -> DeviceAccountQueue:
        """取(或自动创建)该设备的队列 — 断线重连返回同一队列。"""
        with self._lock:
            q = self._queues.get(serial)
            if q is None:
                q = DeviceAccountQueue(
                    serial, id_factory=self._next_id,
                    on_change=(lambda s=serial: self._emit(s)))
                self._queues[serial] = q
            return q

    def get(self, serial: str) -> Optional[DeviceAccountQueue]:
        """不创建地查队列(快照用)。"""
        with self._lock:
            return self._queues.get(serial)

    def iter_queues(self) -> list[DeviceAccountQueue]:
        with self._lock:
            return list(self._queues.values())

    def find_device_of_username(self, username: str) -> Optional[str]:
        """跨设备重复检测(第 37 节): 返回已分配该账号的设备 Serial。"""
        with self._lock:
            for serial, q in self._queues.items():
                if q.find_by_username(username) is not None:
                    return serial
            return None

    def pending_total(self) -> int:
        with self._lock:
            return sum(q.pending_total() for q in self._queues.values())

    def totals(self) -> dict:
        """全局统计(第 31 节): 等待/运行中/完成/失败。"""
        with self._lock:
            waiting = running = success = failed = 0
            for q in self._queues.values():
                c = q.counts()
                waiting += c["pending_total"]
                running += c[QueueAccountStatus.RUNNING.value]
                success += c["completed"]
                failed += c["failed_total"]
            return {"waiting": waiting, "running": running,
                    "success": success, "failed": failed,
                    "devices_with_queue": len(self._queues)}

    def clear_all(self):
        """应用关闭: 清空全部队列(第 28 节)。"""
        with self._lock:
            for q in self._queues.values():
                q.clear_all()
            self._queues.clear()

    def _emit(self, serial: str):
        if self._on_change is not None:
            try:
                self._on_change(serial)
            except Exception as e:
                logger.debug("[队列] on_change 回调异常: %s", e)


class ManualDeviceQueueProvider:
    """新默认生产账号来源(第 2 节) — AccountProvider 子类。

    生产流程: 操作员按设备人工输入账号密码 → 进入该设备独立队列 →
    DeviceWorker 从自己的队列 FIFO 取号执行。本类保留 AccountProvider
    的 pull 视角(供旧导入/API 路径兼容), 实际生产取号走队列 push。
    """

    def __init__(self, manager: ManualDeviceQueueManager):
        self.manager = manager

    def fetch_accounts(self) -> list[tuple[str, str]]:
        """当前全部等待中的 (username, password) — 只读视图。"""
        pairs = []
        for q in self.manager.iter_queues():
            pairs.extend(q.pending_pairs())
        return pairs
