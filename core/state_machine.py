"""
core/state_machine.py
Worker 状态机 — 每台设备的账号执行流程

INIT → CHECK_DEVICE → START_GAME → DETECT_PAGE → LOGIN → WAIT_HOME
     → HANDLE_POPUPS → EXECUTE_TASK → VERIFY_TASK → LOGOUT → CLEANUP
     → NEXT_ACCOUNT → (START_GAME | IDLE)
任意状态超时/异常 → RECOVERY → 恢复成功回 DETECT_PAGE，失败按级别升级。
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Optional


class WorkerState(str, Enum):
    INIT = "INIT"
    CHECK_DEVICE = "CHECK_DEVICE"
    START_GAME = "START_GAME"
    DETECT_PAGE = "DETECT_PAGE"
    LOGIN = "LOGIN"
    WAIT_HOME = "WAIT_HOME"
    HANDLE_POPUPS = "HANDLE_POPUPS"
    EXECUTE_TASK = "EXECUTE_TASK"
    VERIFY_TASK = "VERIFY_TASK"
    LOGOUT = "LOGOUT"
    CLEANUP = "CLEANUP"
    NEXT_ACCOUNT = "NEXT_ACCOUNT"
    RECOVERY = "RECOVERY"
    IDLE = "IDLE"
    STOPPED = "STOPPED"


# 合法迁移表（RECOVERY 可从任意运行态进入）
TRANSITIONS: dict[WorkerState, set[WorkerState]] = {
    WorkerState.INIT: {WorkerState.CHECK_DEVICE, WorkerState.STOPPED},
    WorkerState.CHECK_DEVICE: {WorkerState.START_GAME, WorkerState.RECOVERY,
                               WorkerState.STOPPED},
    WorkerState.START_GAME: {WorkerState.DETECT_PAGE, WorkerState.RECOVERY},
    WorkerState.DETECT_PAGE: {WorkerState.LOGIN, WorkerState.WAIT_HOME,
                              WorkerState.HANDLE_POPUPS, WorkerState.RECOVERY,
                              WorkerState.IDLE},
    WorkerState.LOGIN: {WorkerState.WAIT_HOME, WorkerState.CLEANUP,
                        WorkerState.NEXT_ACCOUNT, WorkerState.RECOVERY},
    WorkerState.WAIT_HOME: {WorkerState.HANDLE_POPUPS, WorkerState.RECOVERY},
    # 弹窗处理后按页面状态路由(device_worker): HOME→EXECUTE_TASK,
    # LOGIN→LOGIN, 其他→DETECT_PAGE(run 12 实测崩溃: 非法状态迁移)
    WorkerState.HANDLE_POPUPS: {WorkerState.EXECUTE_TASK,
                                WorkerState.DETECT_PAGE,
                                WorkerState.LOGIN, WorkerState.RECOVERY},
    WorkerState.EXECUTE_TASK: {WorkerState.VERIFY_TASK, WorkerState.RECOVERY},
    WorkerState.VERIFY_TASK: {WorkerState.LOGOUT, WorkerState.CLEANUP,
                              WorkerState.NEXT_ACCOUNT, WorkerState.RECOVERY},
    WorkerState.LOGOUT: {WorkerState.CLEANUP, WorkerState.RECOVERY},
    WorkerState.CLEANUP: {WorkerState.NEXT_ACCOUNT, WorkerState.IDLE,
                          WorkerState.STOPPED},
    WorkerState.NEXT_ACCOUNT: {WorkerState.CHECK_DEVICE, WorkerState.IDLE,
                               WorkerState.STOPPED},
    WorkerState.RECOVERY: {WorkerState.DETECT_PAGE, WorkerState.CHECK_DEVICE,
                           WorkerState.CLEANUP, WorkerState.IDLE,
                           WorkerState.STOPPED},
    WorkerState.IDLE: {WorkerState.CHECK_DEVICE, WorkerState.STOPPED},
    WorkerState.STOPPED: set(),
}

# 允许从任意状态进入 RECOVERY
for _src in list(TRANSITIONS):
    if _src not in (WorkerState.RECOVERY, WorkerState.STOPPED):
        TRANSITIONS[_src].add(WorkerState.RECOVERY)


class WorkerStateMachine:
    """带超时的状态机。expired() 由 Worker 主循环检查。"""

    def __init__(self):
        self.state = WorkerState.INIT
        self.entered_at: float = time.time()
        self.deadline: Optional[float] = None
        self.timeout_sec: float = 60.0
        self.history: list[tuple[WorkerState, float]] = []
        self.stay_counter: dict[WorkerState, int] = {}

    def transition(self, target: WorkerState) -> bool:
        """校验迁移合法性并切换状态。返回是否成功。"""
        if target not in TRANSITIONS.get(self.state, set()):
            raise ValueError(f"非法状态迁移: {self.state} → {target}")
        self.history.append((self.state, time.time()))
        self.state = target
        self.entered_at = time.time()
        self.deadline = None
        self.stay_counter[target] = self.stay_counter.get(target, 0) + 1
        return True

    def force(self, target: WorkerState):
        """强制切换（RECOVERY 兜底）"""
        self.history.append((self.state, time.time()))
        self.state = target
        self.entered_at = time.time()
        self.deadline = None

    def set_timeout(self, seconds: float):
        self.timeout_sec = float(seconds)
        self.deadline = time.time() + float(seconds)

    def expired(self) -> bool:
        """当前状态是否超时（未设置超时永不过期）"""
        return self.deadline is not None and time.time() > self.deadline

    @property
    def elapsed(self) -> float:
        return time.time() - self.entered_at

    def to_dict(self) -> dict:
        return {"state": self.state.value,
                "elapsed": round(self.elapsed, 1),
                "timeout_sec": self.timeout_sec}
