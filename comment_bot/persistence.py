"""
comment_bot/persistence.py
SQLite 状态持久化层
"""
from __future__ import annotations

import sqlite3
import json
import time
from typing import Optional

from comment_bot.fsm import CommentFSM, CommentTask, FSMState


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS comment_tasks (
    video_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    copywriting TEXT NOT NULL,
    image_paths_json TEXT NOT NULL DEFAULT '[]',
    dm_message TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    delete_count INTEGER NOT NULL DEFAULT 0,
    like_timer_start REAL,
    reply_timer_start REAL,
    remaining_like_wait REAL NOT NULL DEFAULT 300,
    remaining_reply_wait REAL NOT NULL DEFAULT 900,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""


class StateDB:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(CREATE_TABLE_SQL)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_state ON comment_tasks(state)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_updated ON comment_tasks(updated_at)")
        self.conn.commit()

    def save(self, fsm: CommentFSM):
        d = fsm.to_dict()
        self.conn.execute("""
            INSERT OR REPLACE INTO comment_tasks
            (video_id, state, copywriting, image_paths_json, dm_message,
             retry_count, delete_count, like_timer_start, reply_timer_start,
             remaining_like_wait, remaining_reply_wait, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            d["video_id"],
            d["state"],
            fsm.task.copywriting,
            json.dumps(fsm.task.image_paths),
            fsm.task.dm_message,
            fsm.retry_count,
            fsm.delete_count,
            fsm.like_timer_start,
            fsm.reply_timer_start,
            fsm.remaining_like_wait,
            fsm.remaining_reply_wait,
            d["created_at"],
            time.time(),
        ))
        self.conn.commit()

    def load(self, task_id: str) -> Optional[CommentFSM]:
        row = self.conn.execute(
            "SELECT * FROM comment_tasks WHERE video_id = ?",
            (task_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_fsm(row)

    def list_active(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT video_id FROM comment_tasks "
            "WHERE state NOT IN ('COMPLETED', 'FAILED')"
        ).fetchall()
        return [r[0] for r in rows]

    def delete(self, task_id: str):
        self.conn.execute(
            "DELETE FROM comment_tasks WHERE video_id = ?", (task_id,)
        )
        self.conn.commit()

    def get_stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT state, COUNT(*) FROM comment_tasks GROUP BY state"
        ).fetchall()
        stats = {r[0].lower(): r[1] for r in rows}
        stats.setdefault("pending", 0)
        stats.setdefault("waiting_like", 0)
        stats.setdefault("waiting_reply", 0)
        stats.setdefault("completed", 0)
        stats.setdefault("failed", 0)
        return stats

    def get_today_count(self) -> int:
        today_start = time.time() - (time.time() % 86400)
        row = self.conn.execute(
            "SELECT COUNT(*) FROM comment_tasks WHERE created_at >= ?",
            (today_start,)
        ).fetchone()
        return row[0] if row else 0

    def close(self):
        self.conn.close()

    def _row_to_fsm(self, row) -> CommentFSM:
        cols = [
            "video_id", "state", "copywriting", "image_paths_json",
            "dm_message", "retry_count", "delete_count", "like_timer_start",
            "reply_timer_start", "remaining_like_wait", "remaining_reply_wait",
            "created_at", "updated_at"
        ]
        d = dict(zip(cols, row))
        image_paths = json.loads(d["image_paths_json"])
        task = CommentTask(
            video_id=d["video_id"],
            copywriting=d["copywriting"],
            image_paths=image_paths,
            dm_message=d["dm_message"],
            created_at=d["created_at"],
        )
        fsm = CommentFSM(task)
        fsm.state = FSMState[d["state"]]
        fsm.retry_count = d["retry_count"]
        fsm.delete_count = d["delete_count"]
        fsm.like_timer_start = d["like_timer_start"]
        fsm.reply_timer_start = d["reply_timer_start"]
        fsm.remaining_like_wait = d["remaining_like_wait"]
        fsm.remaining_reply_wait = d["remaining_reply_wait"]
        return fsm
