"""
tests/test_state_machine.py
Worker 状态机单元测试 — 迁移合法性 / 超时 / 恢复
"""
from __future__ import annotations

import time

import pytest

from core.state_machine import TRANSITIONS, WorkerState, WorkerStateMachine


class TestStateMachine:

    def test_init_state(self):
        fsm = WorkerStateMachine()
        assert fsm.state == WorkerState.INIT
        assert fsm.deadline is None
        assert not fsm.expired()

    def test_happy_path_transitions(self):
        fsm = WorkerStateMachine()
        path = [WorkerState.CHECK_DEVICE, WorkerState.START_GAME,
                WorkerState.DETECT_PAGE, WorkerState.LOGIN,
                WorkerState.WAIT_HOME, WorkerState.HANDLE_POPUPS,
                WorkerState.EXECUTE_TASK, WorkerState.VERIFY_TASK,
                WorkerState.LOGOUT, WorkerState.CLEANUP,
                WorkerState.NEXT_ACCOUNT]
        for state in path:
            fsm.transition(state)
        assert fsm.state == WorkerState.NEXT_ACCOUNT

    def test_invalid_transition_raises(self):
        fsm = WorkerStateMachine()
        with pytest.raises(ValueError):
            fsm.transition(WorkerState.LOGIN)  # INIT → LOGIN 非法

    def test_recovery_allowed_from_running_state(self):
        fsm = WorkerStateMachine()
        fsm.transition(WorkerState.CHECK_DEVICE)
        fsm.transition(WorkerState.START_GAME)
        fsm.transition(WorkerState.DETECT_PAGE)
        fsm.transition(WorkerState.RECOVERY)  # 任意运行态可进入
        assert fsm.state == WorkerState.RECOVERY

    def test_recovery_back_to_detect(self):
        fsm = WorkerStateMachine()
        fsm.transition(WorkerState.CHECK_DEVICE)
        fsm.transition(WorkerState.START_GAME)
        fsm.transition(WorkerState.RECOVERY)
        fsm.transition(WorkerState.DETECT_PAGE)
        assert fsm.state == WorkerState.DETECT_PAGE

    def test_force_always_works(self):
        fsm = WorkerStateMachine()
        fsm.force(WorkerState.IDLE)  # 非法路径也可以 force
        assert fsm.state == WorkerState.IDLE

    def test_timeout_expires(self):
        fsm = WorkerStateMachine()
        fsm.force(WorkerState.LOGIN)
        fsm.set_timeout(0.05)
        assert not fsm.expired()
        time.sleep(0.08)
        assert fsm.expired()

    def test_no_timeout_never_expires(self):
        fsm = WorkerStateMachine()
        fsm.force(WorkerState.LOGIN)
        assert not fsm.expired()  # 未设置超时永不过期

    def test_transition_resets_deadline(self):
        fsm = WorkerStateMachine()
        fsm.force(WorkerState.LOGIN)
        fsm.set_timeout(0.05)
        time.sleep(0.08)
        assert fsm.expired()
        fsm.transition(WorkerState.RECOVERY)
        assert not fsm.expired()  # 切换后清空 deadline

    def test_elapsed_and_history(self):
        fsm = WorkerStateMachine()
        fsm.transition(WorkerState.CHECK_DEVICE)
        time.sleep(0.01)
        assert fsm.elapsed >= 0
        assert len(fsm.history) == 1

    def test_idle_stop_transitions(self):
        fsm = WorkerStateMachine()
        fsm.force(WorkerState.IDLE)
        fsm.transition(WorkerState.CHECK_DEVICE)
        fsm.transition(WorkerState.STOPPED)
        assert fsm.state == WorkerState.STOPPED

    def test_next_account_to_idle(self):
        fsm = WorkerStateMachine()
        fsm.force(WorkerState.NEXT_ACCOUNT)
        fsm.transition(WorkerState.IDLE)
        assert fsm.state == WorkerState.IDLE

    def test_transition_table_allows_recovery_from_all_active(self):
        """RECOVERY 可从所有非终态进入"""
        active_states = {s for s in WorkerState
                         if s not in (WorkerState.RECOVERY,
                                      WorkerState.STOPPED)}
        for s in active_states:
            assert WorkerState.RECOVERY in TRANSITIONS[s], \
                f"{s} 应允许进入 RECOVERY"

    def test_popup_routes_after_handling(self):
        """回归(run 12): 弹窗处理后按页面状态路由 —
        HANDLE_POPUPS → DETECT_PAGE/LOGIN 必须合法(曾崩溃: 非法状态迁移)"""
        routes = (WorkerState.DETECT_PAGE, WorkerState.LOGIN,
                  WorkerState.EXECUTE_TASK)
        for target in routes:
            fsm = WorkerStateMachine()
            fsm.force(WorkerState.HANDLE_POPUPS)
            fsm.transition(target)
            assert fsm.state == target
