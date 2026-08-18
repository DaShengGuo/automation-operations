"""
tests/test_coordinate_mapper.py
分辨率适配单元测试 — 不依赖任何真机
"""
from __future__ import annotations

from core.coordinate import (BASE_HEIGHT, BASE_WIDTH, CoordinateMapper,
                             ScreenInsets)


class TestCoordinateMapper:

    def test_same_resolution_identity(self):
        """同分辨率映射为恒等"""
        m = CoordinateMapper(width=1080, height=2400)
        assert m.map(540, 1200) == (540, 1200)
        assert m.map(0, 0) == (0, 0)
        assert m.map(1080, 2400) == (1079, 2399)  # 越界钳制

    def test_scale_to_720p(self):
        """1080x2400 → 720x1600 等比缩放"""
        m = CoordinateMapper(width=720, height=1600)
        assert m.map(BASE_WIDTH, BASE_HEIGHT) == (719, 1599)
        assert m.map(540, 1200) == (360, 800)  # 中心点

    def test_scale_to_1440p(self):
        """1080x2400 → 1440x3200 等比缩放"""
        m = CoordinateMapper(width=1440, height=3200)
        assert m.map(540, 1200) == (720, 1600)

    def test_insets_offset(self):
        """状态栏/导航栏偏移"""
        m = CoordinateMapper(width=1080, height=2400,
                             insets=ScreenInsets(top=96, bottom=126))
        assert m.map_ratio(0.5, 0.5) == (540, 1200 + 96)

    def test_landscape(self):
        """横屏设备仍按归一化映射"""
        m = CoordinateMapper(width=2400, height=1080,
                             orientation="landscape")
        assert m.map_ratio(0.5, 0.5) == (1200, 540)

    def test_ratio_rect_to_pixels(self):
        m = CoordinateMapper(width=1000, height=2000)
        l, t, r, b = m.ratio_rect((0.1, 0.2, 0.5, 0.6))
        assert (l, t, r, b) == (100, 400, 500, 1200)

    def test_to_base_roundtrip(self):
        m = CoordinateMapper(width=720, height=1600)
        bx, by = m.to_base(360, 800)
        assert abs(bx - 540) < 1 and abs(by - 1200) < 1

    def test_insets_parse_from_adb_dump(self):
        dump = ("... mStableInsets=Rect(0, 96 - 0, 126) ...")
        insets = ScreenInsets.from_adb_dump(dump)
        assert insets.top == 96
        assert insets.bottom == 126

    def test_insets_parse_failure_returns_zero(self):
        insets = ScreenInsets.from_adb_dump("no insets here")
        assert insets.top == 0 and insets.bottom == 0
