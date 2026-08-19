"""
core/task_scheduler.py
任务调度器 — 多设备并发 Worker 池 + 全局状态看板 + 卡死账号恢复

一台设备 = 一个独立 DeviceWorker 线程(daemon)。
一台设备卡死不影响其他设备。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from automation import create_automation
from core.config import ControlConfig
from core.device_manager import DeviceManager
from core.device_worker import DeviceWorker, WorkerRuntime
from models.device import DeviceStatus
from storage.database import Database
from storage.repositories import AccountRepository, TaskResultRepository

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.account_queues import ManualDeviceQueueManager

logger = logging.getLogger(__name__)


class TaskScheduler:
    """中控调度器：唯一入口，CLI/API 都通过它控制"""

    def __init__(self, cfg: ControlConfig = None,
                 queue_manager: Optional["ManualDeviceQueueManager"] = None):
        self.cfg = cfg or ControlConfig.load()
        self.adb_db = Database(self.cfg.db_path)
        self.accounts = AccountRepository(
            self.adb_db,
            stale_minutes=float(self.cfg.get("stale_recover_minutes", 10)))
        self.results = TaskResultRepository(self.adb_db)
        self.devices = DeviceManager(self.cfg)
        # 人工账号队列(v1.2.0): 提供时 Worker 走队列模式, 账号不落
        # SQLite accounts 表(历史记录仍写 task_results)
        self.queue_manager = queue_manager

        self._workers: dict[str, DeviceWorker] = {}
        self._runtimes: dict[str, WorkerRuntime] = {}
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._stale_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.started_at: float = 0.0
        self.running = False
        self._last_scan_ts: float = 0.0

    # ── 自动化工厂 ──

    def _automation_factory(self, serial: str):
        controller = self.devices.create_controller(serial)
        if controller.device is None:
            controller.connect()
        automation = create_automation(self.cfg.game_adapter, controller,
                                       self.cfg)
        if automation is None:
            raise RuntimeError(
                f"未知 adapter: {self.cfg.game_adapter}（请检查 game.yaml）")
        return automation

    # ── 生命周期 ──

    def start(self, serials: Optional[list[str]] = None,
              max_workers: Optional[int] = None) -> dict:
        """扫描设备 → 初始化 → 为每台设备启动 Worker。返回启动统计。"""
        with self._lock:
            if self.running:
                return self._start_summary()
            self.running = True
            self.started_at = time.time()
            self._stop_event.clear()
            self._pause_event.clear()

            # 程序意外退出后卡在 LOCKED/RUNNING 的账号恢复
            recovered = self.accounts.recover_stale()
            if recovered:
                logger.info(f"[恢复] 恢复了 {recovered} 个卡死账号")

            devices = self.devices.scan()
            started, skipped = 0, []
            target_serials = set(serials) if serials else None
            max_workers = max_workers or self.cfg.max_workers

            for device in devices:
                if target_serials and device.serial not in target_serials:
                    continue
                if device.status == DeviceStatus.DISABLED:
                    skipped.append(f"{device.serial}(disabled)")
                    continue
                if not device.is_adb_healthy:
                    skipped.append(f"{device.serial}(adb:{device.adb_state})")
                    continue
                if started >= max_workers:
                    # 并发上限(workers.max / --workers): 其余设备待命
                    skipped.append(f"{device.serial}(max_workers={max_workers})")
                    continue
                # 初始化（失败仅标记 DEVICE_ERROR，不阻塞其他设备）
                report = self.devices.init_device(device)
                if not report.passed:
                    skipped.append(f"{device.serial}(init failed)")
                    continue
                self._spawn_worker(device.serial)
                started += 1

            self._stale_thread = threading.Thread(
                target=self._stale_recovery_loop, daemon=True,
                name="stale-recovery")
            self._stale_thread.start()
            logger.info(f"[调度器] 启动完成: {started} 台设备运行, "
                        f"跳过: {skipped or '无'}")
            return self._start_summary()

    def _spawn_worker(self, serial: str, prefetched_account=None):
        runtime = WorkerRuntime(serial=serial)
        self._runtimes[serial] = runtime
        queue = registry = None
        if self.queue_manager is not None:
            # 队列模式: Worker 直接从本设备队列取号(断线重连后仍是
            # 同一队列实例 — 队列以 serial 为 Key 常驻内存)
            queue = self.queue_manager.queue_for(serial)
            registry = self.queue_manager.execution_registry
        worker = DeviceWorker(serial, self.cfg, self.devices, self.accounts,
                              self.results, self._automation_factory,
                              self._stop_event, self._pause_event, runtime,
                              prefetched_account=prefetched_account,
                              queue=queue, execution_registry=registry)
        self._workers[serial] = worker
        worker.start()
        logger.info(f"[调度器] Worker 已启动: {serial}")

    def stop(self):
        """优雅停止：所有 Worker 归还账号后退出"""
        with self._lock:
            self._stop_event.set()
            for w in self._workers.values():
                w.join(timeout=30)
            self._workers.clear()
            self.running = False
        logger.info("[调度器] 已停止")

    def pause(self):
        self._pause_event.set()
        logger.info("[调度器] 已暂停（Worker 完成当前步骤后挂起）")

    def resume(self):
        self._pause_event.clear()
        logger.info("[调度器] 已恢复")

    def _stale_recovery_loop(self):
        """周期清扫卡在 LOCKED/RUNNING 的账号 + Worker 心跳检测。

        Worker 线程卡死(页面 watchdog 都救不了)时: 标记 + 重建 Worker。
        """
        interval = float(self.cfg.get("stale_recover_interval", 30))
        while not self._stop_event.is_set():
            time.sleep(interval)
            try:
                recovered = self.accounts.recover_stale()
                if recovered:
                    logger.warning(f"[恢复] 清扫了 {recovered} 个卡死账号")
            except Exception as e:
                logger.debug(f"[恢复] 清扫异常: {e}")
            self._check_worker_heartbeats()

    def _check_worker_heartbeats(self):
        """检测 Worker 线程卡死(heartbeat 停止)并重建"""
        heartbeat_timeout = float(
            self.cfg.get("performance", {}).get("worker_heartbeat_timeout", 90))
        for serial, worker in list(self._workers.items()):
            idle_ago = time.time() - worker.last_action_ts
            # 长阻塞状态(登录/任务执行/恢复)合法耗时远超 90s — 用放宽阈值,
            # 避免误判重建打断在途账号(真机曾登录 120s 等待被误杀, 重试被打断)
            state = getattr(getattr(worker, "fsm", None), "state", None)
            limit = heartbeat_timeout
            if state is not None and state.value in (
                    "LOGIN", "EXECUTE_TASK", "RECOVERY"):
                limit = max(limit, 480)
            if idle_ago > limit and worker.is_alive():
                logger.error(f"[WORKER_STALLED] {serial} 心跳停止 "
                             f"{idle_ago:.0f}s — 尝试重建 Worker")
                # 无法安全 kill 线程: 标记设备错误并重建(新线程接管)
                self.stop_device(serial)
                self.start_device(serial)
                break  # 一次处理一台, 避免连锁

    # ── 设备级控制（API 使用）──

    def start_device(self, serial: str, account_id: Optional[int] = None
                     ) -> dict:
        """启动单台设备 Worker。account_id 提供时先确定性领取该账号
        (桌面版「停止后继续」)。"""
        with self._lock:
            if serial in self._workers:
                return {"ok": False, "error": "worker already running"}
            device = self.devices.get_device(serial)
            if device is None:
                self.devices.scan()
                device = self.devices.get_device(serial)
            if device is None:
                return {"ok": False, "error": "device not found"}
            if not device.is_adb_healthy:
                return {"ok": False, "error": f"adb state={device.adb_state}"}
            report = self.devices.init_device(device)
            if not report.passed:
                return {"ok": False, "error": device.init_error}
            prefetched = None
            if account_id is not None and self.queue_manager is None:
                # 仅 SQLite 模式支持确定性领取; 队列模式由 INTERRUPTED
                # 队首任务自然优先恢复(停止后继续, 第 27 节)
                prefetched = self.accounts.claim_specific(account_id, serial)
                if prefetched is None:
                    return {"ok": False,
                            "error": "account not claimable (not PENDING)"}
            self._spawn_worker(serial, prefetched_account=prefetched)
            return {"ok": True}

    def resume_from_state(self, serial: str, worker_state) -> dict:
        """桌面版「从选择步骤重新开始」: 强制 Worker 状态机到指定状态。
        前置校验(真实页面匹配)由 DeskController 完成。"""
        worker = self._workers.get(serial)
        if worker is None:
            return {"ok": False, "error": "worker not running"}
        worker.fsm.force(worker_state)
        logger.info(f"[调度器] {serial} 强制状态 → {worker_state.value}")
        return {"ok": True}

    def detect_device_page(self, serial: str):
        """桌面版「重新识别当前步骤」: 识别手机真实页面(页面检测器)。
        返回 (PokemonGoState, error)。"""
        try:
            controller = self.devices.create_controller(serial)
            if controller.device is None:
                controller.connect()
            automation = create_automation(self.cfg.game_adapter, controller,
                                           self.cfg)
            if automation is None or not hasattr(automation, "detector"):
                return None, "adapter 无页面检测器"
            return automation.detector.detect(), ""
        except Exception as e:
            return None, str(e)

    def stop_device(self, serial: str, reason: str = "") -> dict:
        """停止单台设备 Worker。

        reason 传给 Worker 的 _shutdown, 作为在途账号的归还原因
        (如 "DEVICE_RESET")。join 在锁外执行: 最长 30s 的等待
        不阻塞其他设备的 start/stop(单设备停止/重置不影响他机)。
        """
        with self._lock:
            worker = self._workers.pop(serial, None)
            self._runtimes.pop(serial, None)   # 清临时运行状态
            if worker is None:
                return {"ok": False, "error": "worker not running"}
            # 让 Worker 退出循环；其 finally 会归还账号
            worker.request_stop(reason)
        worker.join(timeout=30)
        with self._lock:
            device = self.devices.get_device(serial)
            if device is not None and device.status == DeviceStatus.RUNNING:
                device.status = DeviceStatus.READY
        return {"ok": True}

    def restart_device(self, serial: str) -> dict:
        self.stop_device(serial)
        return self.start_device(serial)

    # ── 状态快照 ──

    def snapshot(self) -> dict:
        """CLI 看板 / Web API 的统一状态快照"""
        # 30 秒内完全复用缓存; 之后仅快速重扫 adb 在线状态(不打 getprop)
        if not self.devices._devices:
            self.devices.scan()
            self._last_scan_ts = time.time()
        elif time.time() - self._last_scan_ts > 30:
            self.devices.scan(fast=True)
            self._last_scan_ts = time.time()
        devices = []
        for d in list(self.devices._devices.values()):
            rt = self._runtimes.get(d.serial)
            # 以 Worker 线程存活为准: stop()/stop_device() 清掉 _workers 后,
            # 残留的 runtime 状态不得再被报为「运行中」(否则 GUI 计数永不归零)
            alive = d.serial in self._workers
            # 队列块(独立于 Worker 存活: 停止后待执行队列保留, 第 27 节)
            queue_block = None
            if self.queue_manager is not None:
                q = self.queue_manager.get(d.serial)
                if q is not None:
                    qs = q.snapshot()
                    queue_block = {
                        "pending_total": qs["pending_total"],
                        "waiting": qs["waiting"],
                        "retry": qs["retry"],
                        "interrupted": qs["interrupted"],
                        "success": qs["success"],
                        "failed": qs["failed"],
                    }
            elapsed = 0.0
            if rt is not None and alive and rt.account_started_at:
                elapsed = round(time.time() - rt.account_started_at, 1)
            devices.append({
                "serial": d.serial,
                "model": d.model or "-",
                "resolution": d.resolution,
                "status": d.status.value,
                "worker_state": rt.state if rt and alive else "-",
                "page": rt.page if rt and alive else "-",
                "account": rt.account if rt and alive else "",
                "account_elapsed": elapsed,
                "error": rt.error if rt and alive else d.init_error,
                "success_count": rt.success_count if rt and alive else 0,
                "fail_count": rt.fail_count if rt and alive else 0,
                "last_duration": rt.last_duration if rt and alive else 0,
                "queue": queue_block,
            })
        account_stats = self.accounts.stats()
        # 吞吐量统计(每设备 + 总览)
        perf_devices = {}
        for serial, rt in self._runtimes.items():
            w = self._workers.get(serial)
            if w is not None:
                perf_devices[serial] = w._perf_stats.summary()
        totals = []
        for s in perf_devices.values():
            totals.extend(s.get("n", 0) and [s["avg"]] or [])
        overall = {}
        if totals:
            overall = {
                "completed_total": sum(s.get("n", 0)
                                       for s in perf_devices.values()),
                "avg_sec": round(sum(totals) / len(totals), 1),
                "stalls": sum(s.get("stalls", 0)
                              for s in perf_devices.values()),
                "failures": sum(s.get("failures", 0)
                                for s in perf_devices.values()),
            }
        return {
            "system": {
                "running": self.running,
                "paused": self._pause_event.is_set(),
                "started_at": self.started_at,
                "max_workers": self.cfg.max_workers,
                "workers": len(self._workers),
            },
            "devices": devices,
            "accounts": account_stats,
            "throughput": {"per_device": perf_devices, "overall": overall},
        }

    def _start_summary(self) -> dict:
        return {"ok": True, "workers": len(self._workers),
                "accounts_pending": self.accounts.stats().get("PENDING", 0)}
