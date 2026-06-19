"""
comment_bot/interrupt.py
中断控制器 — 暂停/恢复/时间补偿
"""
from __future__ import annotations

import time
from enum import Enum, auto
from threading import Lock


class BotState(Enum):
    RUNNING = auto()
    PAUSED = auto()
    STOPPED = auto()


class InterruptController:
    def __init__(self):
        self.state = BotState.RUNNING
        self.pause_time: float = 0.0
        self._lock = Lock()

    def pause(self) -> bool:
        with self._lock:
            if self.state != BotState.RUNNING:
                return False
            self.state = BotState.PAUSED
            self.pause_time = time.time()
            return True

    def resume(self) -> float:
        """Resume and return duration paused in seconds, 0 if already running."""
        with self._lock:
            if self.state != BotState.PAUSED:
                return 0.0
            duration = time.time() - self.pause_time
            self.state = BotState.RUNNING
            self.pause_time = 0.0
            return duration

    def stop(self) -> bool:
        with self._lock:
            if self.state == BotState.STOPPED:
                return False
            self.state = BotState.STOPPED
            return True

    @property
    def is_running(self) -> bool:
        return self.state == BotState.RUNNING

    @property
    def is_paused(self) -> bool:
        return self.state == BotState.PAUSED

    @staticmethod
    def compute_compensation(
        tasks: dict[str, dict], pause_duration: float
    ) -> tuple[list[str], dict[str, float]]:
        """
        Compute time compensation for all FSM tasks.
        tasks: {video_id: {state: str, remaining: float}}
        Returns: (immediate_task_ids, delayed_tasks_dict)
        """
        immediate = []
        delayed = {}
        for vid, info in tasks.items():
            remaining = info.get("remaining", 99999)
            new_remaining = remaining - pause_duration
            if new_remaining <= 0:
                immediate.append(vid)
            else:
                delayed[vid] = new_remaining
        return immediate, delayed
