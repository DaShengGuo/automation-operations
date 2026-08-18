"""
models/task.py
任务执行结果模型
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TaskRunState(str, Enum):
    """单账号任务执行结果"""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ABORTED = "ABORTED"      # 设备异常/人工停止导致中止


@dataclass
class TaskResult:
    """一次账号执行的结果记录（写入 SQLite / 导出 Excel）"""
    account_id: Optional[int] = None
    account: str = ""
    device_serial: str = ""
    state: TaskRunState = TaskRunState.FAILED
    started_at: float = 0.0
    finished_at: float = 0.0
    failed_step: str = ""
    error: str = ""
    retry_count: int = 0
    screenshot: str = ""     # 失败现场截图路径（相对项目根）

    @property
    def duration_sec(self) -> float:
        if self.finished_at and self.started_at:
            return round(self.finished_at - self.started_at, 1)
        return 0.0

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "account": self.account,
            "device_serial": self.device_serial,
            "state": self.state.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_sec": self.duration_sec,
            "failed_step": self.failed_step,
            "error": self.error,
            "retry_count": self.retry_count,
            "screenshot": self.screenshot,
        }
