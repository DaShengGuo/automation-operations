"""
models/account.py
账号模型 — 统一账号队列中的任务单元
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AccountStatus(str, Enum):
    """账号生命周期状态"""
    PENDING = "PENDING"      # 待执行
    LOCKED = "LOCKED"        # 已被设备原子锁定（防重复领取的中间态）
    RUNNING = "RUNNING"      # 执行中
    SUCCESS = "SUCCESS"      # 执行成功
    FAILED = "FAILED"        # 超过最大重试，最终失败
    RETRY = "RETRY"          # 失败待重试（可被再次领取）
    DISABLED = "DISABLED"    # 已禁用

    @property
    def is_claimable(self) -> bool:
        return self in (AccountStatus.PENDING, AccountStatus.RETRY)

    @property
    def is_terminal(self) -> bool:
        return self in (AccountStatus.SUCCESS,
                        AccountStatus.FAILED,
                        AccountStatus.DISABLED)


@dataclass
class Account:
    """一个待执行的任务账号"""
    account: str
    password: str = ""
    id: Optional[int] = None
    status: AccountStatus = AccountStatus.PENDING
    device_serial: str = ""
    retry_count: int = 0
    max_retry: int = 3
    last_error: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    def masked(self) -> str:
        """脱敏显示: abc***123"""
        if len(self.account) <= 6:
            return self.account[:2] + "***"
        return f"{self.account[:3]}***{self.account[-3:]}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "account": self.masked(),
            "status": self.status.value,
            "device_serial": self.device_serial,
            "retry_count": self.retry_count,
            "max_retry": self.max_retry,
            "last_error": self.last_error,
        }
