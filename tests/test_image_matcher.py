"""
tests/test_image_matcher.py
图像模板匹配单元测试 — 合成图像，不依赖真机
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from core.image_matcher import ImageMatcher


def make_screen(w=400, h=600):
    """灰底 + 棋盘格方块(在 (300,100) 处 50x50) + 红色圆(在 (100,400))

    模板必须是非均匀图案: TM_CCOEFF_NORMED 对纯色模板会在任何
    均匀背景区域误报高置信度。
    """
    img = np.full((h, w, 3), 128, dtype=np.uint8)
    # 蓝色棋盘格 50x50 (5x5 格)
    for i in range(5):
        for j in range(5):
            if (i + j) % 2 == 0:
                cv2.rectangle(img,
                              (300 + i * 10, 100 + j * 10),
                              (310 + i * 10, 110 + j * 10),
                              (255, 0, 0), -1)
    cv2.circle(img, (125, 425), 30, (0, 0, 255), -1)  # BGR 红
    return img


@pytest.fixture
def matcher(tmp_path):
    screen = make_screen()
    # 截取棋盘格方块作为模板
    tpl = screen[100:150, 300:350]
    cv2.imwrite(str(tmp_path / "blue_square.png"), tpl)
    return ImageMatcher(template_dir=tmp_path, screen_size=(400, 600)), screen


class TestImageMatcher:

    def test_find_returns_center(self, matcher):
        m, screen = matcher
        pos = m.find("blue_square", screen)
        assert pos is not None
        assert abs(pos[0] - 325) <= 2 and abs(pos[1] - 125) <= 2  # 中心(325,125)

    def test_exists(self, matcher):
        m, screen = matcher
        assert m.exists("blue_square", screen)
        assert not m.exists("no_such_template", screen)

    def test_template_missing_returns_none(self, matcher):
        m, screen = matcher
        assert m.find("missing", screen) is None

    def test_threshold_too_high(self, matcher):
        m, screen = matcher
        # 模板在 ROI 外的非模板区域(红圆), 高阈值下不误报
        pos = m.find("blue_square", screen, threshold=0.999,
                     roi=(50, 350, 200, 500))
        assert pos is None

    def test_roi_ratio(self, matcher):
        """ROI 限制在正确区域才能命中"""
        m, screen = matcher
        # 蓝色方块位于 (300..350, 100..150) → 比例 (0.7,0.1,0.95,0.35)
        pos = m.find("blue_square", screen, roi=(0.7, 0.1, 0.95, 0.35))
        assert pos is not None
        # 错误区域 → 找不到
        pos = m.find("blue_square", screen, roi=(0.0, 0.5, 0.6, 1.0))
        assert pos is None

    def test_wait_for(self, matcher):
        m, screen = matcher
        pos = m.wait_for("blue_square", lambda: screen, timeout=2, interval=0.1)
        assert pos is not None

    def test_wait_for_timeout(self, matcher):
        m, screen = matcher
        pos = m.wait_for("missing", lambda: screen, timeout=0.3, interval=0.1)
        assert pos is None

    def test_click(self, matcher):
        m, screen = matcher
        clicked = []

        def click_fn(x, y):
            clicked.append((x, y))

        assert m.click("blue_square", click_fn, lambda: screen) is True
        assert clicked and abs(clicked[0][0] - 325) <= 2

    def test_match_score(self, matcher):
        m, screen = matcher
        score = m.match_score("blue_square", screen)
        assert score > 0.9

    def test_scale_adaptation(self, matcher):
        """0.9 倍缩放截图仍能命中（多尺度匹配）"""
        m, screen = matcher
        small = cv2.resize(screen, (360, 540))
        pos = m.find("blue_square", small)
        assert pos is not None
