"""
comment_bot/scheduler.py
并行任务调度器 — 优先级队列 + timer堆 + 并发控制
"""
from __future__ import annotations

import heapq
import time
from collections import deque
from threading import Lock
from typing import Optional

from comment_bot.fsm import CommentFSM, FSMState
from douyin_core import config as cfg


PRIORITY_MAP = {
    "REPLYING": 0,
    "FOLLOWING": 0,
    "DM_SEND": 0,
    "WAITING_LIKE": 1,
    "WAITING_REPLY": 1,
    "DELETING": 1,
    "POSTING": 2,
    "PENDING": 3,
}


class TaskScheduler:
    def __init__(self, max_active: int = None):
        self.max_active = max_active or cfg.MAX_ACTIVE_TASKS
        self.active_tasks: dict[str, CommentFSM] = {}
        self.pending_queue: deque[CommentFSM] = deque()
        self.timer_heap: list[tuple[float, str]] = []
        self._lock = Lock()

    def enqueue(self, fsm: CommentFSM):
        with self._lock:
            vid = fsm.task.video_id
            if len(self.active_tasks) >= self.max_active:
                self.pending_queue.append(fsm)
            else:
                self.active_tasks[vid] = fsm

    def get_ready_task(self) -> Optional[CommentFSM]:
        with self._lock:
            candidates = list(self.active_tasks.values())
            if not candidates:
                return None
            candidates.sort(key=lambda f: (
                PRIORITY_MAP.get(f.state.name, 99),
                f.task.created_at
            ))
            for fsm in candidates:
                if fsm.state in (FSMState.COMPLETED, FSMState.FAILED):
                    continue
                if fsm.state in (FSMState.WAITING_LIKE, FSMState.WAITING_REPLY):
                    continue
                return fsm
            return None

    def schedule_timer(self, fsm: CommentFSM):
        with self._lock:
            delay = 0
            if fsm.state == FSMState.WAITING_LIKE:
                delay = fsm.remaining_like_wait
            elif fsm.state == FSMState.WAITING_REPLY:
                delay = fsm.remaining_reply_wait
            else:
                return
            expire_at = time.time() + delay
            heapq.heappush(self.timer_heap, (expire_at, fsm.task.video_id))

    def check_timers(self) -> list[str]:
        ready = []
        with self._lock:
            now = time.time()
            while self.timer_heap and self.timer_heap[0][0] <= now:
                _, vid = heapq.heappop(self.timer_heap)
                if vid in self.active_tasks:
                    fsm = self.active_tasks[vid]
                    if fsm.state in (FSMState.WAITING_LIKE, FSMState.WAITING_REPLY):
                        ready.append(vid)
            return ready

    def cleanup_completed(self):
        with self._lock:
            to_remove = [
                vid for vid, fsm in self.active_tasks.items()
                if fsm.state in (FSMState.COMPLETED, FSMState.FAILED)
            ]
            for vid in to_remove:
                del self.active_tasks[vid]
            while len(self.active_tasks) < self.max_active and self.pending_queue:
                new_fsm = self.pending_queue.popleft()
                if new_fsm.task.video_id not in self.active_tasks:
                    self.active_tasks[new_fsm.task.video_id] = new_fsm

    def get_state_summary(self) -> list[dict]:
        with self._lock:
            return [fsm.to_dict() for fsm in list(self.active_tasks.values())]

    @property
    def active_count(self) -> int:
        return len(self.active_tasks)

    @property
    def pending_count(self) -> int:
        return len(self.pending_queue)
