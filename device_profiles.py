"""
设备自适应系统 — RuntimeDeviceProfile + 自动检测 + 归一化坐标

架构:
  ADB 连接 → DeviceInfo(自动读取) → RuntimeDeviceProfile(自动生成)
  → 已有精确配置则使用, 否则自动计算

不再要求用户手动编辑 device_profiles.py。
"""
from __future__ import annotations

import logging
import subprocess
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 已验证设备精确覆盖（按 serial 索引）──
# 这些是作者实际测试过的机型, 坐标经过实机验证
VERIFIED_PROFILES = {
    "89U899UOYPAQXSCI": {
        "model": "pond", "brand": "Redmi",
        "notes": "Redmi 14C, 抖音38.1.0, 已实测验证"
    },
    "RS5XCI7XDYXWMFFI": {
        "model": "pond", "brand": "Redmi",
        "notes": "Redmi 14C 抖音旧版, 已实测验证"
    },
}
_HONOR_VERIFIED = {
    "model": "KOZ-AL00", "brand": "Honor",
    "notes": "Honor KOZ-AL00 抖音37.8.0, hierarchy无底部元素, 纯坐标, 已实测验证"
}
for _s in ["AADE9X3919W01912","AADE9X3A13W01108","AADE9X3A20W00120",
           "AADE9X3A21W05111","AADE9X3A19W01593","AADE9X3824W00603"]:
    VERIFIED_PROFILES[_s] = dict(_HONOR_VERIFIED)


@dataclass
class DeviceInfo:
    """从 ADB 自动读取的设备信息"""
    serial: str
    manufacturer: str = ""
    brand: str = ""
    model: str = ""
    android_version: str = ""
    sdk: int = 0
    width: int = 0
    height: int = 0
    density: int = 0
    orientation: str = "portrait"

    @classmethod
    def from_adb(cls, serial: str, adb_path: str = "adb") -> "DeviceInfo":
        """通过 ADB 自动读取设备完整信息"""
        import os as _os
        info = cls(serial=serial)
        # 确保使用绝对路径（subprocess.run 对相对路径敏感）
        _adb = _os.path.abspath(adb_path) if not _os.path.isabs(adb_path) else adb_path

        def _shell(cmd: str) -> str:
            try:
                r = subprocess.run(
                    [_adb, "-s", serial, "shell"] + cmd.split(),
                    capture_output=True, text=True, timeout=10
                )
                return r.stdout.strip()
            except Exception:
                return ""

        # 基础属性
        info.manufacturer = _shell("getprop ro.product.manufacturer")
        info.brand = _shell("getprop ro.product.brand")
        info.model = _shell("getprop ro.product.model")
        info.android_version = _shell("getprop ro.build.version.release")
        try: info.sdk = int(_shell("getprop ro.build.version.sdk"))
        except: pass

        # 屏幕参数
        wm = _shell("wm size")
        if "x" in wm:
            try:
                parts = wm.split(":")[-1].strip().split("x")
                info.width, info.height = int(parts[0]), int(parts[1])
            except: pass

        try:
            density_str = _shell("wm density")
            # "Physical density: 440" → 440
            info.density = int(density_str.split(":")[-1].strip())
        except: pass

        # 横竖屏
        dump = _shell("dumpsys input | grep SurfaceOrientation")
        if "1" in dump or "3" in dump:
            info.orientation = "landscape"

        logger.info(
            f"[DeviceInfo] {info.brand} {info.model} "
            f"Android {info.android_version} "
            f"{info.width}x{info.height} dpi={info.density} "
            f"orientation={info.orientation}"
        )
        return info


@dataclass
class ScreenInsets:
    """屏幕安全区域（状态栏/导航栏）"""
    top: int = 0
    bottom: int = 0
    left: int = 0
    right: int = 0

    @property
    def usable_width(self) -> int: return 0
    @property
    def usable_height(self) -> int: return 0


@dataclass
class RuntimeDeviceProfile:
    """运行时自动计算的设备配置。
    所有坐标使用归一化比例(0-1)，运行时乘以屏幕实际像素。
    """
    serial: str
    width: int
    height: int
    density: int
    is_verified: bool = False
    verified_notes: str = ""

    # ── 抖音底部 Tab 横坐标 ──
    tab_home: float = 0.10
    tab_friends: float = 0.30
    tab_create: float = 0.50
    tab_messages: float = 0.70
    tab_me: float = 0.90
    tab_y: float = 0.96

    # ── 评论输入区 ──
    img_btn_coord: tuple = (0.078, 0.904)
    send_btn_coord: tuple = (0.89, 0.90)
    input_coord: tuple = (0.497, 0.797)
    plus_btn: tuple = (0.30, 0.76)
    circle1: tuple = (0.58, 0.23)       # 相册选图第1张
    circle2: tuple = (0.91, 0.23)       # 相册选图第2张

    # ── UI 元素定位（优先使用，坐标只是兜底）──
    input_rids: list = field(default_factory=lambda: ["erc", "ern", "ej3"])
    img_btn_descs: list = field(default_factory=lambda: ["插入图片", "图片"])

    # ── OCR 区域（归一化）──
    title_region: tuple = (0.05, 0.78, 0.95, 0.88)
    comment_time_region: tuple = (0.55, 0.30, 0.90, 0.85)

    # ── 置信度 ──
    confidence: float = 0.7  # 0=未校准 0.7=自动计算 1.0=已实测验证

    def to_pixels(self, rx: float, ry: float) -> tuple:
        """归一化坐标 → 像素坐标"""
        return (int(self.width * rx), int(self.height * ry))

    def to_dict(self) -> dict:
        return {
            "serial": self.serial,
            "width": self.width, "height": self.height, "density": self.density,
            "is_verified": self.is_verified, "confidence": self.confidence,
            "tab_y": self.tab_y,
            "img_btn": list(self.img_btn_coord),
            "send_btn": list(self.send_btn_coord),
            "input": list(self.input_coord),
            "plus_btn": list(self.plus_btn),
        }


class DeviceProfileManager:
    """设备配置管理器 — 统一入口"""

    _cache: dict[str, RuntimeDeviceProfile] = {}

    @classmethod
    def resolve(cls, serial: str, adb_path: str = "adb",
                device_info: DeviceInfo = None) -> RuntimeDeviceProfile:
        """解析设备配置: 已验证 > 自动计算"""
        if serial in cls._cache:
            return cls._cache[serial]

        # 1. 读取设备信息
        if device_info is None:
            device_info = DeviceInfo.from_adb(serial, adb_path)

        # 2. 自动生成 profile
        profile = cls._build_profile(device_info)

        # 3. 检查是否有已验证覆盖
        verified = VERIFIED_PROFILES.get(serial, {})
        if verified:
            profile.is_verified = True
            profile.verified_notes = verified.get("notes", "")
            profile.confidence = 1.0

        cls._cache[serial] = profile
        return profile

    @classmethod
    def _build_profile(cls, info: DeviceInfo) -> RuntimeDeviceProfile:
        """根据设备信息自动计算最佳配置"""
        w, h = info.width, info.height

        # 确保竖屏 (width < height)
        if w > h:
            w, h = h, w

        profile = RuntimeDeviceProfile(
            serial=info.serial,
            width=w, height=h, density=info.density,
        )

        # ── 根据分辨率档位微调 ──
        # 720p 档 (720x1600~1640): 使用基础默认值
        if w <= 720:
            profile.img_btn_coord = (0.078, 0.904)
            profile.circle1 = (0.58, 0.23)
            profile.circle2 = (0.91, 0.23)
            profile.input_coord = (0.497, 0.797)

        # 1080p 档 (1080x1920~2460): 等比缩放
        elif w <= 1080:
            # 1080p 下按钮比例基本一致
            profile.img_btn_coord = (0.078, 0.904)
            profile.circle1 = (0.58, 0.23)
            profile.circle2 = (0.91, 0.23)
            # 输入框在 1080p 上略微靠上
            if h > 2200:
                profile.input_coord = (0.50, 0.82)
                profile.tab_y = 0.97
            else:
                profile.input_coord = (0.50, 0.78)

        # 1440p 档: 宽屏适配
        else:
            scale_x = 1440 / w if w > 0 else 1.0
            profile.img_btn_coord = (0.078 * scale_x, 0.91)
            profile.circle1 = (0.58, 0.24)
            profile.circle2 = (0.91, 0.24)
            profile.input_coord = (0.50, 0.83)
            profile.tab_y = 0.97

        # ── Honor KOZ-AL00 特殊处理（hierarchy 不可靠, 纯坐标模式）──
        if info.model and "KOZ-AL00" in info.model:
            profile.confidence = 1.0  # 已验证
            profile.input_rids = []   # 不使用 resource-id（hierarchy 不可靠）

        return profile

    @classmethod
    def list_verified(cls) -> dict:
        """返回所有已验证设备"""
        return dict(VERIFIED_PROFILES)


# ── 兼容旧接口 ──
def get_profile(device_serial: str, adb_path: str = "adb") -> dict:
    """
    兼容旧 device_profiles 接口, 返回 dict 格式。
    新代码应直接使用 DeviceProfileManager.resolve()。
    """
    profile = DeviceProfileManager.resolve(device_serial, adb_path)
    return {
        "input_rids": list(profile.input_rids),
        "img_btn_descs": list(profile.img_btn_descs),
        "img_btn_fallback": profile.img_btn_coord,
        "img_btn_coord": profile.img_btn_coord,
        "plus_btn": profile.plus_btn,
        "send_btn_coord": profile.send_btn_coord,
        "circle1": profile.circle1,
        "circle2": profile.circle2,
        "input_coord": profile.input_coord,
        "confidence": profile.confidence,
        "is_verified": profile.is_verified,
        "width": profile.width,
        "height": profile.height,
        "density": profile.density,
    }


def save_profile(device_serial: str, **kwargs):
    """保存用户自定义配置（本地 JSON, 不提交 Git）"""
    user_dir = Path(__file__).parent.parent / "data" / "user_profiles"
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / f"{device_serial}.json"
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    existing.update(kwargs)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    logger.info(f"[Profile] 已保存用户配置: {path}")
