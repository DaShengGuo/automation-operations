"""
core/coordinate.py
分辨率适配 — 基准分辨率(1080×2400)到任意设备的分辨率映射

原则: 所有「基准坐标」先归一化(0-1), 再乘以设备实际像素。
横屏/竖屏自动处理, 支持状态栏/导航栏安全区域偏移。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple


BASE_WIDTH = 1080
BASE_HEIGHT = 2400


@dataclass
class ScreenInsets:
    """屏幕安全区域(状态栏/导航栏, 像素)"""
    top: int = 0
    bottom: int = 0
    left: int = 0
    right: int = 0

    @classmethod
    def from_adb_dump(cls, dump_text: str) -> "ScreenInsets":
        """从 `adb shell dumpsys window` 输出解析 mStableInsets。

        示例: mStableInsets=Rect(0, 96 - 0, 126) → top=96, bottom=126
        解析失败时返回全 0（不阻塞主流程）。
        """
        insets = cls()
        import re
        m = re.search(r"mStableInsets=Rect\(([-\d]+),\s*([-\d]+)\s*-\s*([-\d]+),\s*([-\d]+)\)",
                      dump_text)
        if m:
            left, top, right, bottom = (max(0, int(v)) for v in m.groups())
            insets.left, insets.top, insets.right, insets.bottom = left, top, right, bottom
        return insets


@dataclass
class CoordinateMapper:
    """基准坐标(1080×2400 竖屏) ↔ 实际设备像素坐标"""
    width: int
    height: int
    base_width: int = BASE_WIDTH
    base_height: int = BASE_HEIGHT
    orientation: str = "portrait"          # portrait / landscape
    insets: ScreenInsets = field(default_factory=ScreenInsets)

    # ── 基准 → 实际 ──

    def map(self, base_x: float, base_y: float) -> Tuple[int, int]:
        """基准坐标(px@1080x2400) → 设备像素坐标"""
        rx = base_x / self.base_width
        ry = base_y / self.base_height
        return self.map_ratio(rx, ry)

    def map_ratio(self, rx: float, ry: float) -> Tuple[int, int]:
        """归一化坐标(0-1) → 设备像素坐标(含安全区域偏移)"""
        x = int(rx * self.width) + self.insets.left
        y = int(ry * self.height) + self.insets.top
        return self._clamp(x, y)

    def map_rect(self, base_rect: Sequence[float]) -> Tuple[int, int, int, int]:
        """基准矩形(left, top, right, bottom @1080x2400) → 设备像素矩形"""
        l, t = self.map(base_rect[0], base_rect[1])
        r, b = self.map(base_rect[2], base_rect[3])
        return l, t, r, b

    def ratio_rect(self, rect: Sequence[float]) -> Tuple[int, int, int, int]:
        """比例矩形(l,t,r,b 均 0-1) → 设备像素矩形(用于 ROI)"""
        l, t = self.map_ratio(rect[0], rect[1])
        r, b = self.map_ratio(rect[2], rect[3])
        return l, t, max(l + 1, r), max(t + 1, b)

    # ── 实际 → 基准(记录用) ──

    def to_base(self, x: int, y: int) -> Tuple[float, float]:
        """设备像素坐标 → 基准坐标(px@1080x2400)"""
        rx = (x - self.insets.left) / max(1, self.width)
        ry = (y - self.insets.top) / max(1, self.height)
        return round(rx * self.base_width, 1), round(ry * self.base_height, 1)

    def _clamp(self, x: int, y: int) -> Tuple[int, int]:
        return (max(0, min(self.width - 1, x)),
                max(0, min(self.height - 1, y)))

    def __repr__(self) -> str:
        return (f"CoordinateMapper({self.width}x{self.height} "
                f"{self.orientation}, base={self.base_width}x{self.base_height}, "
                f"insets={self.insets})")
