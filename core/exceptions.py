"""
core/exceptions.py
统一异常体系 — 每种异常对应明确的可恢复策略
"""
from __future__ import annotations


class ControlCenterError(Exception):
    """中控系统基础异常"""
    recover_level: int = 1  # 对应 Watchdog 恢复等级


class AdbError(ControlCenterError):
    """ADB 命令失败 / 设备离线"""
    recover_level = 7


class UiAutomatorError(ControlCenterError):
    """uiautomator2 连接/操作失败"""
    recover_level = 6


class AppCrashError(ControlCenterError):
    """目标应用闪退/未运行"""
    recover_level = 5


class PageTimeoutError(ControlCenterError):
    """页面等待超时"""
    recover_level = 1


class PageUnknownError(ControlCenterError):
    """页面长时间无法识别"""
    recover_level = 2


class LoginError(ControlCenterError):
    """登录失败（密码错误/账号异常/超时）"""
    recover_level = 1

    def __init__(self, reason: str = ""):
        self.reason = reason
        super().__init__(reason or "登录失败")


class TaskVerifyError(ControlCenterError):
    """任务结果验证失败"""
    recover_level = 1


class PaymentBlockedError(ControlCenterError):
    """真实支付操作被 dry_run 拦截"""
    recover_level = 0  # 不可自动恢复，必须人工介入


class SelectorNotConfiguredError(ControlCenterError):
    """UI 选择器未标定(UNKNOWN_SELECTOR) — 需开发者用 u2 dump 完成标定"""
    recover_level = 0  # 不可自动恢复，属于配置缺失


class DeviceInitError(ControlCenterError):
    """设备初始化失败，设备被隔离为 DEVICE_ERROR"""
    recover_level = 8
