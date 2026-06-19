"""
comment_bot/fsm.py
评论状态机 — 管理每条评论的完整生命周期
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class FSMState(Enum):
    PENDING = auto()
    POSTING = auto()
    WAITING_LIKE = auto()
    WAITING_REPLY = auto()
    REPLYING = auto()
    FOLLOWING = auto()
    DM_SEND = auto()
    COMPLETED = auto()
    FAILED = auto()
    DELETING = auto()


@dataclass
class CommentTask:
    """评论任务数据"""
    video_id: str
    copywriting: str
    image_paths: list[str] = field(default_factory=list)
    dm_message: str = "看到你评论问我，我是吃药加照光弄好的"
    created_at: float = field(default_factory=time.time)


class CommentFSM:
    """单条评论状态机"""

    def __init__(self, task: CommentTask):
        self.task = task
        self.state = FSMState.PENDING
        self.retry_count = 0
        self.delete_count = 0
        self.like_timer_start: Optional[float] = None
        self.reply_timer_start: Optional[float] = None
        self.remaining_like_wait: float = 300
        self.remaining_reply_wait: float = 900
        self._state_history: list[tuple[FSMState, float]] = []

    def transition(self, new_state: FSMState):
        self._state_history.append((self.state, time.time()))
        self.state = new_state

    def can_transition_to(self, target: FSMState) -> bool:
        valid_transitions = {
            FSMState.PENDING: {FSMState.POSTING},
            FSMState.POSTING: {FSMState.WAITING_LIKE, FSMState.PENDING, FSMState.FAILED},
            FSMState.WAITING_LIKE: {FSMState.WAITING_REPLY, FSMState.DELETING},
            FSMState.WAITING_REPLY: {FSMState.REPLYING, FSMState.DELETING},
            FSMState.REPLYING: {FSMState.FOLLOWING},
            FSMState.FOLLOWING: {FSMState.DM_SEND},
            FSMState.DM_SEND: {FSMState.COMPLETED},
            FSMState.DELETING: {FSMState.PENDING},
            FSMState.FAILED: set(),
            FSMState.COMPLETED: set(),
        }
        return target in valid_transitions.get(self.state, set())

    def mark_posted(self):
        self.transition(FSMState.WAITING_LIKE)
        self.like_timer_start = time.time()
        self.remaining_like_wait = 300

    def mark_post_failed(self):
        self.retry_count += 1
        if self.retry_count >= 3:
            self.transition(FSMState.FAILED)
        else:
            self.transition(FSMState.PENDING)

    def check_likes(self, has_likes: bool):
        if has_likes:
            self.transition(FSMState.WAITING_REPLY)
            self.reply_timer_start = time.time()
            self.remaining_reply_wait = 900
        else:
            self.delete_count += 1
            self.transition(FSMState.DELETING)

    def check_replies(self, has_replies: bool):
        if has_replies:
            self.transition(FSMState.REPLYING)
        else:
            self.delete_count += 1
            self.transition(FSMState.DELETING)

    def mark_deleted(self):
        self.retry_count = 0
        self.like_timer_start = None
        self.reply_timer_start = None
        self.transition(FSMState.PENDING)

    def mark_completed(self):
        self.transition(FSMState.COMPLETED)

    def apply_pause_compensation(self, pause_seconds: float) -> bool:
        """Apply pause time compensation. Returns True if immediately due."""
        immediate = False
        if self.state == FSMState.WAITING_LIKE:
            self.remaining_like_wait -= pause_seconds
            if self.remaining_like_wait <= 0:
                immediate = True
        elif self.state == FSMState.WAITING_REPLY:
            self.remaining_reply_wait -= pause_seconds
            if self.remaining_reply_wait <= 0:
                immediate = True
        return immediate

    @property
    def is_active(self) -> bool:
        return self.state not in (FSMState.COMPLETED, FSMState.FAILED)

    @property
    def is_waiting(self) -> bool:
        return self.state in (FSMState.WAITING_LIKE, FSMState.WAITING_REPLY)

    def to_dict(self) -> dict:
        return {
            "video_id": self.task.video_id,
            "state": self.state.name,
            "copywriting": self.task.copywriting[:50],
            "retry_count": self.retry_count,
            "delete_count": self.delete_count,
            "remaining_like_wait": int(self.remaining_like_wait),
            "remaining_reply_wait": int(self.remaining_reply_wait),
            "created_at": self.task.created_at,
        }
