"""
models/device.py
设备模型 — ADB 检测到的 Android 设备
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DeviceStatus(str, Enum):
    """设备生命周期状态"""
    ONLINE = "ONLINE"            # ADB 在线（刚检测到）
    INITIALIZING = "INITIALIZING"  # 正在初始化
    READY = "READY"              # 初始化完成，可接任务
    RUNNING = "RUNNING"          # 执行任务中
    IDLE = "IDLE"                # 等待领取账号
    DEVICE_ERROR = "DEVICE_ERROR"  # 初始化/运行失败，已隔离
    OFFLINE = "OFFLINE"          # ADB 离线
    DISABLED = "DISABLED"        # 配置中禁用


@dataclass
class AndroidDevice:
    """一台 Android 设备（ADB 自动读取）"""
    serial: str
    manufacturer: str = ""
    brand: str = ""
    model: str = ""
    android_version: str = ""
    sdk: int = 0
    width: int = 0
    height: int = 0
    dpi: int = 0
    orientation: str = "portrait"
    adb_state: str = "device"       # device / offline / unauthorized
    battery_level: int = -1         # -1 表示未知
    storage_free_gb: float = -1.0   # -1 表示未知
    status: DeviceStatus = DeviceStatus.ONLINE
    init_error: str = ""
    app_installed: bool = False

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def is_adb_healthy(self) -> bool:
        return self.adb_state == "device"

    def to_dict(self) -> dict:
        return {
            "serial": self.serial,
            "manufacturer": self.manufacturer,
            "brand": self.brand,
            "model": self.model,
            "android_version": self.android_version,
            "sdk": self.sdk,
            "width": self.width,
            "height": self.height,
            "dpi": self.dpi,
            "orientation": self.orientation,
            "adb_state": self.adb_state,
            "battery_level": self.battery_level,
            "storage_free_gb": self.storage_free_gb,
            "status": self.status.value,
            "init_error": self.init_error,
            "app_installed": self.app_installed,
        }
