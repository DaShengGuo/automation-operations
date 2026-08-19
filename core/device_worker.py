"""
core/device_worker.py
DeviceWorker — 每台设备一个独立线程，独立 u2 会话、独立日志、独立状态

Worker 生命周期:
  领取账号 → 状态机执行(启动→识别→登录→任务→验证→退出) → 记录结果
  → 领取下一个账号 → 循环。任何异常进入 Watchdog 分级恢复。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.config import ControlConfig
from core.device_manager import DeviceController, DeviceManager
from core.exceptions import (AdbError, SelectorNotConfiguredError,
                             UiAutomatorError)
from core.logger import ContextLogger, get_logger, mask_account
from core.state_machine import WorkerState, WorkerStateMachine
from core.watchdog import AnomalyType, Watchdog
from models.account import Account, AccountStatus
from models.device import DeviceStatus
from models.page_state import PageState
from models.task import TaskResult, TaskRunState
from storage.repositories import AccountRepository, TaskResultRepository

from typing import TYPE_CHECKING
if TYPE_CHECKING:  # 仅类型标注(队列模式 v1.2.0)
    from core.account_queues import (DeviceAccountQueue,
                                     GlobalAccountExecutionRegistry)

logger = logging.getLogger(__name__)


@dataclass
class WorkerRuntime:
    """Worker 对外可见的运行状态（CLI 看板 / Web API 读取）"""
    serial: str = ""
    state: str = WorkerState.INIT.value
    page: str = PageState.UNKNOWN.value
    account: str = ""
    account_id: Optional[int] = None
    error: str = ""
    screenshot: str = ""
    account_started_at: float = 0.0
    last_duration: float = 0.0
    success_count: int = 0
    fail_count: int = 0

    def to_dict(self) -> dict:
        return {
            "serial": self.serial, "state": self.state, "page": self.page,
            "account": self.account, "account_id": self.account_id,
            "error": self.error, "screenshot": self.screenshot,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "last_duration": self.last_duration,
        }


class DeviceWorker(threading.Thread):
    """单设备账号流水线执行线程"""

    def __init__(self,
                 serial: str,
                 cfg: ControlConfig,
                 device_manager: DeviceManager,
                 account_repo: AccountRepository,
                 result_repo: TaskResultRepository,
                 automation_factory: Callable[[str], object],
                 stop_event: threading.Event,
                 pause_event: threading.Event,
                 runtime: Optional[WorkerRuntime] = None,
                 prefetched_account: Optional[Account] = None,
                 queue: Optional["DeviceAccountQueue"] = None,
                 execution_registry:
                 Optional["GlobalAccountExecutionRegistry"] = None):
        super().__init__(name=f"worker-{serial}", daemon=True)
        self.serial = serial
        self.cfg = cfg
        self.devices = device_manager
        # 队列模式(v1.2.0 人工账号队列): self.accounts 直接指向本设备队列,
        # 方法签名与 AccountRepository 对齐(mark_running/mark_success/
        # mark_retry/mark_failed/release) — 状态机代码零改动复用。
        self._queue_mode = queue is not None
        self.accounts = queue if queue is not None else account_repo
        self.execution_registry = execution_registry
        self.results = result_repo
        self.automation_factory = automation_factory
        self.stop_event = stop_event          # 全局停止
        self._local_stop = threading.Event()  # 单设备停止（API 用）
        self.pause_event = pause_event
        self.runtime = runtime or WorkerRuntime(serial=serial)
        self.log: ContextLogger = get_logger("control.worker",
                                             device_serial=serial)

        self.fsm = WorkerStateMachine()
        self.controller: Optional[DeviceController] = None
        self.automation = None
        self.watchdog: Optional[Watchdog] = None
        self.account: Optional[Account] = None
        self._result: Optional[TaskResult] = None
        self._task_retries_left = 0
        self._login_retries_left = 0
        self._last_anomaly = AnomalyType.NONE
        self._last_qq_fetch_ts: float = 0.0
        self._qq_fetch_cooldown: float = 60.0  # QQ 取号冷却(秒)
        self._cycle_started_at: float = time.time()  # 本轮循环测试起点
        # 生产性能组件
        self._prefetched_account = prefetched_account  # 账号预取槽位
        # (不能叫 _next_account — 会遮蔽同名方法, 真机曾因此卡死循环)
        self._stop_reason = ""                   # request_stop 的归还原因
        self._tracer = None                     # PerformanceTracer(懒创建)
        self._slow_logged = False               # SLOW_ACCOUNT 已提示
        self.last_action_ts = time.time()       # heartbeat
        self.last_action = "init"
        from core.perf import PerfStats
        self._perf_stats = PerfStats()          # 本设备跨账号统计

    # ── 主循环 ──

    def request_stop(self, reason: str = ""):
        """请求本 Worker 退出（停止单台设备时使用）。

        reason 记录在途账号的归还原因(如 "DEVICE_RESET"),
        空则用默认 "worker interrupted"。
        """
        self._stop_reason = reason
        self._local_stop.set()

    def run(self):
        self.log.info("Worker 启动")
        try:
            while not (self.stop_event.is_set() or self._local_stop.is_set()):
                if self.pause_event.is_set():
                    self._set_state(WorkerState.IDLE)
                    time.sleep(1)
                    continue
                try:
                    self._tick()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except SelectorNotConfiguredError as e:
                    self._fail_account_immediately(str(e))
                except (AdbError, UiAutomatorError) as e:
                    self._handle_critical_exception(e)
                except Exception as e:
                    self.log.error(f"Worker 异常: {e}", exc_info=True)
                    self._recover_with_watchdog(
                        AnomalyType.PAGE_UNKNOWN, from_exception=str(e))
                    self._sleep_after_error()
        finally:
            self._shutdown()

    def _tick(self):
        # heartbeat(调度器检测线程卡死)
        self.last_action_ts = time.time()

        # 无账号 → 领取
        if self.account is None:
            self._claim_next()
            if self.account is None:
                self._set_state(WorkerState.IDLE)
                if self._queue_mode:
                    # 队列模式: 空队列 → 等待账号(规格第 24 节)。
                    # 条件变量等待 — 新账号加入毫秒级唤醒(规格第 61 节),
                    # 不再轮询空转。5s 超时兜底重检。
                    self.accounts.wait_for_task(5.0)
                else:
                    time.sleep(float(self.cfg.get("poll_interval", 2)))
                return

        # 账号时间预算(生产吞吐量保护: 坏账号不拖死设备)
        self._check_time_budget()

        # 超时兜底（任何状态都不允许无限等待）
        if self.fsm.expired():
            self.log.warning(f"状态 {self.fsm.state.value} 超时"
                             f"({self.fsm.timeout_sec}s)")
            self._enter_recovery(AnomalyType.LOAD_TIMEOUT)
            return

        # 周期巡检（应用应运行的状态）
        if self.fsm.state in self._APP_RUNNING_STATES:
            anomaly = self._ensure_watchdog().check(self._page())
            if anomaly != AnomalyType.NONE:
                self._enter_recovery(anomaly)
                return

        self._step()

    # 注意: 不含 START_GAME — 该状态的任务正是 launch 游戏, 进入状态时
    # 进程尚未启动属正常(冷启动), 巡检会误报 APP_CRASHED 白进 RECOVERY
    _APP_RUNNING_STATES = {
        WorkerState.DETECT_PAGE, WorkerState.LOGIN,
        WorkerState.WAIT_HOME, WorkerState.HANDLE_POPUPS,
        WorkerState.EXECUTE_TASK, WorkerState.VERIFY_TASK, WorkerState.LOGOUT,
    }

    # ── 账号领取/释放 ──

    def _claim_next(self):
        if self._queue_mode:
            account = self._claim_from_queue()
        else:
            # 预取槽位优先(上一账号完成时已锁定, 零等待切换)
            account = self._prefetched_account
            self._prefetched_account = None
            if account is None:
                account = self.accounts.claim_next(self.serial)
            if account is None and self.cfg.account_provider == "qq_ui":
                # 队列为空 → 从本机 QQ 群取号(带冷却, 避免无新号时反复切 QQ)
                now = time.time()
                if now - self._last_qq_fetch_ts >= self._qq_fetch_cooldown:
                    self._last_qq_fetch_ts = now
                    self._fetch_accounts_from_qq()
                    account = self.accounts.claim_next(self.serial)
        if account is None:
            self.account = None
            return
        self.account = account
        self.accounts.mark_running(account.id, self.serial)
        self.runtime.account = mask_account(account.account)
        self.runtime.account_id = account.id
        self.runtime.account_started_at = time.time()
        self.log = get_logger("control.worker", device_serial=self.serial,
                              account=account.account)
        # 性能追踪器(每账号新建)
        from core.perf import PerformanceTracer
        self._tracer = PerformanceTracer(account.masked(), log=self.log)
        self._slow_logged = False
        self._result = TaskResult(account_id=account.id,
                                  account=account.account,
                                  device_serial=self.serial,
                                  started_at=time.time(),
                                  retry_count=account.retry_count)
        self._task_retries_left = self.cfg.retry_for("task")
        self._login_retries_left = self.cfg.retry_for("login")
        self._login_done = False            # 本周期是否已登录本账号
        self._session_reset_attempts = 0    # 残留会话登出尝试(防死循环)
        self._ensure_session()
        self.log.info(f"领取账号 {account.masked()} "
                      f"(重试次数={account.retry_count})")
        self.fsm.force(WorkerState.CHECK_DEVICE)
        self.fsm.set_timeout(self.cfg.state_timeout("check_device"))
        self._set_state(WorkerState.CHECK_DEVICE)

    def _claim_from_queue(self):
        """队列模式取号: 本设备队列 FIFO 领取, 禁跨设备偷号(第 19 节)。

        全局执行锁(第 38/39 节): 同 username 已在其他设备执行 →
        本设备跳过该账号(退回队尾), 锁在账号完成/释放时归还。
        """
        task = self.accounts.pop_next()
        if task is None:
            return None
        if self.execution_registry is not None and \
                not self.execution_registry.try_acquire(task.username,
                                                        self.serial,
                                                        task.id):
            holder = self.execution_registry.owner_of(task.username)
            self.log.warning(f"[执行锁] {task.masked()} 正在设备 "
                             f"{holder or '?'} 执行, 退回队尾等待")
            self.accounts.defer_task(task.id)
            return None
        return task

    def _check_time_budget(self):
        """账号最大占用时间保护。

        soft(默认 75s): 打印 SLOW_ACCOUNT 一次
        hard(默认 120s): 非关键状态直接终止, 释放设备处理下一账号
        """
        perf = self.cfg.get("performance", {}) or {}
        soft = float(perf.get("soft_account_timeout", 75))
        hard = float(perf.get("hard_account_timeout", 120))
        if self._result is None:
            return
        elapsed = time.time() - self._result.started_at
        if elapsed > soft and not self._slow_logged:
            self.log.warning(f"[SLOW_ACCOUNT] {self.account.masked()} "
                             f"已耗时 {elapsed:.0f}s(预算 {soft}s)")
            self._slow_logged = True
        if elapsed > hard and self.fsm.state not in (
                WorkerState.LOGOUT, WorkerState.CLEANUP,
                WorkerState.RECOVERY, WorkerState.IDLE):
            self.log.error(f"[TIME_BUDGET_EXCEEDED] {self.account.masked()} "
                           f"{elapsed:.0f}s > {hard}s, 释放设备")
            self._capture_evidence("TIME_BUDGET_EXCEEDED",
                                   f"elapsed={elapsed:.0f}s state="
                                   f"{self.fsm.state.value}")
            self._mark_account_retry("TIME_BUDGET_EXCEEDED")

    def _fetch_accounts_from_qq(self):
        """队列空时从本机 QQ 群读取账号(上条消息=账号, 下条=密码)。

        每台设备操作自己手机上已登录的 QQ, 互不干扰;
        账号入库去重, 不会重复执行。
        """
        from core.qq_provider import QQAccountProvider
        self._ensure_session()
        provider = QQAccountProvider(self.controller,
                                     self.cfg.qq_group_name, log=self.log)
        self.log.info(f"[账号] 队列为空, 从 QQ 群「{self.cfg.qq_group_name}」取号")
        try:
            pairs = provider.fetch_latest()
        except Exception as e:
            self.log.error(f"[账号] QQ 取号失败: {e}")
            return
        # add_batch 返回 (新增, 跳过): 已入库账号不计入"取到"
        added, skipped = self.accounts.add_batch(pairs)
        # 无论是否读到账号都切回游戏(避免手机卡在 QQ)
        back = provider.back_to_game(self.cfg.game_package)
        self.log.info(f"[账号] QQ 取号 新增 {added} 组, "
                      f"重复跳过 {skipped} 组, 回到游戏={back}")

    def _fail_account_immediately(self, error: str):
        """配置缺失类错误：直接 FAILED，不重试"""
        if self.account:
            self.log.error(f"账号 {self.account.masked()} 配置缺失失败: {error}")
            self._capture_evidence("CONFIG_ERROR", error)
            self.accounts.mark_failed(self.account.id, self.serial, error)
            self._finish_result(TaskRunState.FAILED, "CONFIG", error)
        self._next_account()

    def _mark_account_retry(self, error: str):
        """失败 → RETRY/FAILED（按最大重试次数），并释放当前账号。
        不调用 _next_account 会在同一账号上无限重跑任务
        (真机: retry_count 烧到 9, 靠 480s 硬预算才脱身)。"""
        if not self.account:
            return
        final = self.accounts.mark_retry(self.account.id, self.serial, error)
        self._finish_result(TaskRunState.FAILED,
                            self.fsm.state.value, error)
        if final is not None and final.value == "FAILED":
            self.log.error(f"账号 {self.account.masked()} 超过最大重试，"
                           f"最终失败: {error}")
            self.runtime.fail_count += 1
        else:
            self.log.warning(f"账号 {self.account.masked()} 进入 RETRY: {error}")
        self._next_account()

    def _next_account(self):
        # 归还全局执行锁(账号已离开本 Worker — 队列模式才有)
        self._release_execution_lock(self.account)
        self.account = None
        self._result = None
        self.runtime.account = ""
        self.runtime.account_id = None
        self.fsm.force(WorkerState.NEXT_ACCOUNT)
        self._set_state(WorkerState.NEXT_ACCOUNT)
        # 账号预取: 立即锁定下一账号, 消除切换空档
        # (锁定时间短; worker 异常退出由 recover_stale 兜底释放)。
        # 队列模式无预取(队列领取本身零等待, 且任务只在内存)。
        if not self._queue_mode and self._prefetched_account is None:
            self._prefetched_account = self.accounts.claim_next(self.serial)

    # ── 状态机 ──

    # WorkerState → PerformanceTracer 事件
    TRACER_EVENTS = {
        WorkerState.START_GAME: "GAME_START",
        WorkerState.LOGIN: "RETURNING_PLAYER_FOUND",
        WorkerState.WAIT_HOME: "GAME_RETURNED",
        WorkerState.EXECUTE_TASK: "MENU_FOUND",
        WorkerState.VERIFY_TASK: "PURCHASE_RESULT",
        WorkerState.LOGOUT: "SETTINGS_FOUND",
        WorkerState.CLEANUP: "ACCOUNT_FINISHED",
    }

    def _enter_state(self, state: WorkerState):
        """进入状态(幂等)：已在目标状态时只刷新超时"""
        if self.fsm.state != state:
            self.fsm.transition(state)
        timeout = self.cfg.state_timeout(state.value.lower())
        self.fsm.set_timeout(timeout)
        # 性能追踪
        event = self.TRACER_EVENTS.get(state)
        if event and self._tracer is not None:
            self._tracer.mark(event)
        self._set_state(state)

    def _set_state(self, state: WorkerState):
        self.runtime.state = state.value
        self.runtime.page = self._page().value
        self.log.info(f"状态: {state.value} (超时={self.fsm.timeout_sec}s)")

    def _page(self) -> PageState:
        if self.automation is not None:
            try:
                return self.automation.detect_page()
            except Exception:
                pass
        return PageState.UNKNOWN

    def _step(self):
        state = self.fsm.state

        if state == WorkerState.CHECK_DEVICE:
            self._ensure_session()
            device = self.devices.get_device(self.serial)
            if device is not None:
                device.status = DeviceStatus.RUNNING
            self._enter_state(WorkerState.START_GAME)

        elif state == WorkerState.START_GAME:
            self.automation.launch()
            self._enter_state(WorkerState.DETECT_PAGE)

        elif state == WorkerState.DETECT_PAGE:
            page = self._page()
            self.runtime.page = page.value
            if page == PageState.LOGIN:
                self._enter_state(WorkerState.LOGIN)
            elif page == PageState.HOME:
                if self._login_done:
                    self._enter_state(WorkerState.WAIT_HOME)
                else:
                    # 桌面版「停止后继续」: 残留会话即本账号(DeskController
                    # 在恢复时按设备序列号注入 resume 配置) → 跳过登出/
                    # 重登录, 直接继续任务。账号不匹配则走登出(归属安全)。
                    resume = (self.cfg.get("resume") or {}).get(
                        self.serial) or {}
                    if (resume.get("trust_residual_session")
                            and self.account is not None
                            and resume.get("account") == self.account.account):
                        self.log.info("[恢复] 残留会话即本账号 — "
                                      "跳过登出直接继续任务")
                        self._login_done = True
                        self._enter_state(WorkerState.WAIT_HOME)
                    else:
                        # 残留会话: 游戏仍登录着上一个账号(异常中断遗留) —
                        # 必须先登出再走本账号 LOGIN, 否则账号归属错乱
                        self._reset_residual_session()
            elif page.is_popup or page == PageState.NETWORK_ERROR:
                self._enter_state(WorkerState.HANDLE_POPUPS)
            elif page == PageState.UNKNOWN:
                # 弹窗遮挡是最常见 UNKNOWN 原因(如登录后公告弹窗)
                try:
                    handled = self.automation.handle_popups()
                    if handled:
                        self.log.info(f"[弹窗] 处理 {handled} 个后重新检测")
                except Exception as e:
                    self.log.debug(f"[弹窗] 处理异常: {e}")
                time.sleep(2)  # 仍未识别 → 等超时兜底进 RECOVERY
            else:
                time.sleep(2)  # SPLASH 等加载

        elif state == WorkerState.LOGIN:
            result = self.automation.login(self.account)
            if result.value in ("SUCCESS", "ALREADY_LOGGED_IN"):
                self.log.info(f"登录成功({result.value})")
                self._login_done = True
                self._enter_state(WorkerState.WAIT_HOME)
            elif result.value in ("WRONG_PASSWORD", "ACCOUNT_ERROR"):
                error = f"登录失败: {result.value}"
                self._capture_evidence("LOGIN_FAILED", error)
                self.accounts.mark_failed(self.account.id, self.serial, error)
                self._finish_result(TaskRunState.FAILED, "LOGIN", error)
                self.runtime.fail_count += 1
                self._next_account()
            else:
                self._login_retries_left -= 1
                if self._login_retries_left > 0:
                    self.log.warning(f"登录结果 {result.value}，"
                                     f"剩余重试 {self._login_retries_left}")
                    if result.value == "TIMEOUT":
                        # 认证超时时浏览器可能仍开在 PTC 页、游戏在后台 —
                        # 先暖拉回游戏(不 force-stop, 省 60-100s 冷启动),
                        # 再重走 DETECT_PAGE→LOGIN; 浏览器会话已建立时
                        # 重试通常 30-40s 即过。真机实测: 不拉回游戏时
                        # DETECT_PAGE 只看到外部浏览器 → UNKNOWN 死循环
                        self.log.info("[登录] 超时重试: 暖切回游戏, 不冷重启")
                        if not self.automation.launch():
                            self.log.warning("[登录] 暖切回游戏失败, 改用冷重启")
                            self.automation.restart()
                    else:
                        self.automation.restart()
                    self.fsm.force(WorkerState.DETECT_PAGE)
                    self._enter_state(WorkerState.DETECT_PAGE)
                else:
                    self._mark_account_retry(f"登录失败: {result.value}")

        elif state == WorkerState.WAIT_HOME:
            if self.detector_wait_home():
                self._enter_state(WorkerState.HANDLE_POPUPS)
            else:
                time.sleep(2)  # 超时兜底

        elif state == WorkerState.HANDLE_POPUPS:
            self.automation.handle_popups()
            # 按处理后的页面状态路由(避免 HOME→HANDLE_POPUPS→DETECT 死循环)
            page = self._page()
            if page == PageState.HOME:
                if self._login_done:
                    self._enter_state(WorkerState.EXECUTE_TASK)
                else:
                    # 弹窗遮挡下的残留会话: 同样不得直接执行任务
                    self._reset_residual_session()
            elif page == PageState.LOGIN:
                self._enter_state(WorkerState.LOGIN)
            elif page.is_popup:
                # 仍有弹窗 → 本状态内重试(超时兜底进 RECOVERY)
                time.sleep(2)
            else:
                self._enter_state(WorkerState.DETECT_PAGE)

        elif state == WorkerState.EXECUTE_TASK:
            outcome = self.automation.execute_task(self.account)
            if outcome.ok:
                self._enter_state(WorkerState.VERIFY_TASK)
            else:
                self._task_retries_left -= 1
                if self._task_retries_left > 0:
                    self.log.warning(f"任务失败({outcome.failed_step})，"
                                     f"剩余重试 {self._task_retries_left}")
                    self._enter_recovery(AnomalyType.PAGE_UNKNOWN)
                else:
                    self._mark_account_retry(
                        f"任务失败[{outcome.failed_step}]: {outcome.error}")

        elif state == WorkerState.VERIFY_TASK:
            verified = self.automation.verify_result()
            if verified is None:
                self.log.info("结果验证跳过(未配置 verify 规则或人工模式)")
                self._enter_state(WorkerState.LOGOUT)
            elif verified:
                self.log.info("任务结果验证通过")
                self._enter_state(WorkerState.LOGOUT)
            else:
                self._mark_account_retry("任务结果验证失败")

        elif state == WorkerState.LOGOUT:
            if self.cfg.get("logout_required", True):
                self.automation.logout()
            else:
                self.log.info("logout_required=false，跳过退出登录")
            self._enter_state(WorkerState.CLEANUP)

        elif state == WorkerState.CLEANUP:
            self.accounts.mark_success(self.account.id, self.serial)
            self._finish_result(TaskRunState.SUCCESS, "", "")
            self.runtime.success_count += 1
            self.runtime.last_duration = self._result.duration_sec if self._result else 0
            self.log.info(f"账号 {self.account.masked()} 执行成功 "
                          f"(耗时 {self.runtime.last_duration}s)")
            # 性能报告 → 进程级统计(看板吞吐量)
            if self._tracer is not None:
                report = self._tracer.finish()
                self._perf_stats.add(report)
                self._tracer = None
            # 测试循环: 成功次数未达 test_cycles → 重置 PENDING 再跑
            # (队列模式不适用: 人工队列按输入顺序一次一账号, 第 18 节)
            cycles = 0 if self._queue_mode else \
                int(self.cfg.get("test_cycles", 0) or 0)
            if cycles > 0:
                done = self.results.count_success(
                    self.account.id, since=self._cycle_started_at)
                if done < cycles:
                    self.accounts.release(
                        self.account.id,
                        f"test cycle {done}/{cycles}")
                    self.log.info(f"[测试循环] 账号 {self.account.masked()} "
                                  f"完成 {done}/{cycles} 轮, 重置待跑")
                else:
                    self.log.info(f"[测试循环] 账号 {self.account.masked()} "
                                  f"已完成全部 {cycles} 轮")
            self._next_account()

        elif state == WorkerState.RECOVERY:
            self._recovery_step()

        elif state in (WorkerState.IDLE, WorkerState.STOPPED):
            time.sleep(1)

    def _reset_residual_session(self):
        """发现残留 HOME 会话(本周期尚未登录) → 登出后重新检测走 LOGIN。

        真机教训: 异常中断后游戏仍登录着上一个账号, 新账号若直接执行任务
        则结果归属错乱。登出重试有上限(2 次), 超限释放账号避免死循环。
        """
        if self._session_reset_attempts >= 2:
            self.log.error("[会话] 残留会话登出 2 次仍未回到登录页, 释放账号")
            self._capture_evidence("SESSION_RESET_FAILED",
                                   "残留会话登出失败, 无法进入本账号登录")
            self._mark_account_retry("SESSION_RESET_FAILED")
            return
        self._session_reset_attempts += 1
        self.log.warning(f"[会话] 检测到残留 HOME 会话(非本账号), "
                         f"第 {self._session_reset_attempts} 次登出")
        self.automation.logout()
        self.fsm.force(WorkerState.DETECT_PAGE)
        self._enter_state(WorkerState.DETECT_PAGE)

    def detector_wait_home(self) -> bool:
        return self.automation.wait_home(
            timeout=min(self.fsm.timeout_sec, 30))

    # ── Watchdog 恢复 ──

    def _ensure_session(self):
        """惰性建立设备会话（含自动化实例）"""
        if self.controller is None:
            self.controller = self.devices.create_controller(self.serial)
        if self.controller.device is None or not self.controller.is_healthy():
            self.controller.connect()
        if self.automation is None:
            self.automation = self.automation_factory(self.serial)
            self._wire_automation()
        if self.watchdog is None:
            self._build_watchdog()

    def _wire_automation(self):
        """把心跳回调注入自动化长等待循环(登录/认证可达 60-120s)。

        真机 run 实测: 登录阻塞期间心跳停摆 → 调度器误判 WORKER_STALLED
        重建 Worker → 重试被打断 + 账号白冷却 2 分钟 + 全程 6 分钟卡在
        选号页。长等待循环每轮调用回调刷新 last_action_ts 即可消除误判。
        """
        cb = lambda: setattr(self, "last_action_ts", time.time())
        for obj in (self.automation,
                    getattr(self.automation, "web", None),
                    getattr(self.automation, "detector", None)):
            if obj is not None:
                obj.heartbeat_cb = cb

    def _ensure_watchdog(self) -> Watchdog:
        """获取 watchdog（首次调用时构建并注入恢复回调）"""
        if self.watchdog is None:
            self._build_watchdog()
        return self.watchdog

    def _build_watchdog(self):
        wd = Watchdog(self.serial, self.controller,
                      self.devices.adb, self.cfg.game_package)
        # 回调全部经 self.automation 延迟解析，automation 重建后仍有效
        wd.on_redetect = lambda: (self.automation.detect_page(), True)[1]
        wd.on_back = lambda: (self.controller.press("back"), True)[1]
        wd.on_popups = lambda: (self.automation.handle_popups(), True)[1]
        wd.on_go_home = lambda: self.automation.recover()
        wd.on_restart_app = lambda: self.automation.restart()
        wd.on_reset_u2 = self._reset_u2
        wd.on_reconnect_adb = self._reconnect_adb
        self.watchdog = wd

    def _reset_u2(self) -> bool:
        """Level 6: 重连 uiautomator2 并重建自动化实例"""
        try:
            self.controller.reset()
            self.automation = self.automation_factory(self.serial)
            self._wire_automation()
            return True
        except Exception as e:
            self.log.warning(f"u2 重连失败: {e}")
            return False

    def _reconnect_adb(self) -> bool:
        """Level 7: 重新连接 ADB"""
        try:
            self.devices.adb._run([self.devices.adb.path, "reconnect"],
                                  timeout=20)
            if not self.devices.adb.wait_online(self.serial, timeout=30):
                return False
            self.controller.connect()
            self.automation = self.automation_factory(self.serial)
            self._wire_automation()
            return True
        except Exception as e:
            self.log.warning(f"ADB 重连失败: {e}")
            return False

    def _enter_recovery(self, anomaly: AnomalyType):
        self._last_anomaly = anomaly
        self.fsm.force(WorkerState.RECOVERY)
        self._set_state(WorkerState.RECOVERY)

    def _recovery_step(self):
        ok, level = self._ensure_watchdog().recover(self._last_anomaly)
        if ok:
            self.log.info(f"恢复成功(Level {level})，重新识别页面")
            self.fsm.force(WorkerState.DETECT_PAGE)
            self._enter_state(WorkerState.DETECT_PAGE)
        else:
            self.log.error(f"恢复失败(Level {level})，设备标记 DEVICE_ERROR")
            self._capture_evidence("RECOVERY_FAILED",
                                   f"watchdog level {level}")
            device = self.devices.get_device(self.serial)
            if device is not None:
                device.status = DeviceStatus.DEVICE_ERROR
            if self.account:
                self.accounts.release(self.account.id,
                                      "device recovery failed")
                self._finish_result(TaskRunState.ABORTED, "RECOVERY",
                                    "watchdog 恢复失败")
            self.runtime.error = "watchdog recovery failed"
            self._next_account()
            self._set_state(WorkerState.IDLE)

    def _recover_with_watchdog(self, anomaly: AnomalyType,
                               from_exception: str = ""):
        if from_exception:
            self.log.warning(f"异常触发恢复: {from_exception}")
        self._enter_recovery(anomaly)
        self._recovery_step()

    def _handle_critical_exception(self, e):
        self.log.error(f"设备级异常: {e}")
        anomaly = (AnomalyType.ADB_DISCONNECTED if isinstance(e, AdbError)
                   else AnomalyType.U2_DISCONNECTED)
        self._recover_with_watchdog(anomaly, str(e))
        self._sleep_after_error()

    def _sleep_after_error(self):
        time.sleep(3)

    # ── 证据 / 结果 ──

    def _capture_evidence(self, reason: str, detail: str = "") -> Path:
        """失败现场截图（禁止覆盖：文件名含时间戳）"""
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            day = time.strftime("%Y-%m-%d")
            account_tag = f"account_{self.account.id}" if self.account else "no_account"
            folder = (self.cfg.screenshots_dir / day /
                      f"device_{self.serial}" / account_tag)
            safe_reason = "".join(c if c.isalnum() or c in "-_" else "_"
                                  for c in reason)[:40]
            path = folder / f"{safe_reason}_{ts}.png"
            self.controller.save_screenshot(path)
            rel = path.relative_to(self.cfg.project_root)
            self.runtime.screenshot = str(rel)
            if detail:
                (folder / f"{safe_reason}_{ts}.txt").write_text(
                    detail, encoding="utf-8")
            self.log.info(f"[证据] 已保存失败现场: {rel}")
            return path
        except Exception as e:
            self.log.warning(f"[证据] 截图失败: {e}")
            return Path()

    def _finish_result(self, state: TaskRunState, failed_step: str,
                       error: str):
        if self._result is None:
            return
        self._result.state = state
        self._result.finished_at = time.time()
        self._result.failed_step = failed_step
        self._result.error = error[:500]
        self._result.screenshot = self.runtime.screenshot
        self.runtime.error = error[:200]
        self.results.save(self._result)

    # ── 退出 ──

    def _shutdown(self):
        self.log.info("Worker 退出")
        if self.account is not None:
            if self._queue_mode:
                # 队列模式: 在途账号 → INTERRUPTED 插回队首(下次启动
                # 优先恢复当前账号, 规格第 27/56 节), 不烧重试。
                self.accounts.mark_interrupted(
                    self.account.id,
                    self._stop_reason or "worker interrupted")
            else:
                # 运行中被打断 → 归还队列（避免账号卡死在 RUNNING）。
                # 设备环境重置等场景由 request_stop(reason) 传入专属原因。
                self.accounts.mark_retry(
                    self.account.id, self.serial,
                    self._stop_reason or "worker interrupted")
            self._release_execution_lock(self.account)
        if self._prefetched_account is not None:
            # 预取账号已 LOCKED 但从未开始执行 → 释放回 PENDING(不烧
            # 重试次数), 否则停止/重置后该账号被占满 stale 清扫周期
            self.accounts.release(
                self._prefetched_account.id,
                self._stop_reason or "worker stopped before start")
            self._prefetched_account = None
        if self._queue_mode and self.execution_registry is not None:
            # 兜底: 清掉本设备残留的全部执行锁(防泄漏)
            self.execution_registry.release_all_for(self.serial)
        if self.controller is not None:
            self.controller.disconnect()

    def _release_execution_lock(self, account):
        """归还某账号的全局执行锁(队列模式)。"""
        if (self._queue_mode and self.execution_registry is not None
                and account is not None):
            self.execution_registry.release(account.account, self.serial)
