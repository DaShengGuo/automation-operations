"""
core/image_matcher.py
统一图像模板匹配器 — 全部 OpenCV 代码集中在此，业务代码禁止直接调用 cv2

能力: find / exists / wait_for / click / match_score
参数: threshold(阈值) / roi(区域) / scales(多尺度) / timeout(等待)
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

import cv2
import numpy as np


class ImageMatcher:
    """OpenCV 模板匹配封装。

    截图约定: BGR numpy 数组(与 cv2.imread 一致)。
    """

    def __init__(self,
                 template_dir: Path | str,
                 screen_size: Tuple[int, int] = (0, 0),
                 scales: Sequence[float] = (1.0, 0.9, 1.1),
                 default_threshold: float = 0.8,
                 cache_templates: bool = True):
        self.template_dir = Path(template_dir)
        self.screen_w, self.screen_h = int(screen_size[0]), int(screen_size[1])
        self.scales = tuple(scales)
        self.default_threshold = default_threshold
        self._cache: dict[str, np.ndarray] = {} if cache_templates else None

    # ── 模板加载 ──

    def _resolve_path(self, name: str) -> Path:
        """按名字找模板: 自动补 .png 后缀"""
        p = Path(name)
        if p.suffix:
            return p if p.is_absolute() else self.template_dir / p
        return self.template_dir / f"{name}.png"

    def load_template(self, name: str) -> Optional[np.ndarray]:
        """加载模板(带缓存)，文件不存在返回 None"""
        cache_key = str(name)
        if self._cache is not None and cache_key in self._cache:
            return self._cache[cache_key]
        path = self._resolve_path(name)
        if not path.exists():
            return None
        # cv2.imread 不支持中文路径 → imdecode + fromfile
        img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8),
                           cv2.IMREAD_COLOR)
        if img is None:
            return None
        if self._cache is not None:
            self._cache[cache_key] = img
        return img

    def clear_cache(self):
        if self._cache is not None:
            self._cache.clear()

    # ── 核心匹配 ──

    def match_score(self, name: str, screenshot: np.ndarray,
                    roi: Optional[Sequence[float]] = None,
                    scales: Optional[Sequence[float]] = None) -> float:
        """返回模板在截图中的最高置信度(0-1)，找不到返回 0.0"""
        tpl = self.load_template(name)
        if tpl is None:
            return 0.0
        return self._best_match(tpl, screenshot, roi, scales)[0]

    def find(self, name: str, screenshot: np.ndarray,
             threshold: Optional[float] = None,
             roi: Optional[Sequence[float]] = None,
             scales: Optional[Sequence[float]] = None
             ) -> Optional[Tuple[int, int]]:
        """在截图中查找模板，返回中心像素坐标，未找到返回 None。

        roi 约定: 值<=1 → 比例(l,t,r,b)自动乘屏幕尺寸；值>1 → 像素(l,t,r,b)
        """
        tpl = self.load_template(name)
        if tpl is None:
            return None
        threshold = self.default_threshold if threshold is None else threshold
        val, pos = self._best_match(tpl, screenshot, roi, scales)
        if val >= threshold and pos is not None:
            return pos
        return None

    def exists(self, name: str, screenshot: np.ndarray,
               threshold: Optional[float] = None,
               roi: Optional[Sequence[float]] = None) -> bool:
        return self.find(name, screenshot, threshold=threshold, roi=roi) is not None

    # ── 组合能力 ──

    def wait_for(self, name: str,
                 screenshot_fn: Callable[[], np.ndarray],
                 timeout: float = 10.0,
                 interval: float = 1.0,
                 threshold: Optional[float] = None,
                 roi: Optional[Sequence[float]] = None
                 ) -> Optional[Tuple[int, int]]:
        """轮询等待模板出现，超时返回 None。screenshot_fn 每次返回新截图。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                shot = screenshot_fn()
            except Exception:
                time.sleep(interval)
                continue
            pos = self.find(name, shot, threshold=threshold, roi=roi)
            if pos is not None:
                return pos
            time.sleep(interval)
        return None

    def click(self, name: str,
              click_fn: Callable[[int, int], None],
              screenshot_fn: Callable[[], np.ndarray],
              threshold: Optional[float] = None,
              timeout: float = 5.0,
              roi: Optional[Sequence[float]] = None) -> bool:
        """截图→匹配→点击。找不到/超时返回 False。"""
        pos = self.wait_for(name, screenshot_fn, timeout=timeout,
                            threshold=threshold, roi=roi)
        if pos is None:
            return False
        click_fn(*pos)
        return True

    # ── 内部实现 ──

    def _roi_pixels(self, roi: Optional[Sequence[float]]
                    ) -> Optional[Tuple[int, int, int, int]]:
        """roi(像素或比例) → 像素 (x, y, w, h)"""
        if roi is None:
            return None
        l, t, r, b = (float(v) for v in roi)
        if max(l, t, r, b) <= 1.0:
            if self.screen_w <= 0 or self.screen_h <= 0:
                return None
            x, y = int(l * self.screen_w), int(t * self.screen_h)
            w = int((r - l) * self.screen_w)
            h = int((b - t) * self.screen_h)
            return (x, y, w, h)
        return (int(l), int(t), int(r - l), int(b - t))

    def _best_match(self, tpl: np.ndarray, screenshot: np.ndarray,
                    roi: Optional[Sequence[float]],
                    scales: Optional[Sequence[float]]
                    ) -> Tuple[float, Optional[Tuple[int, int]]]:
        """多尺度模板匹配核心，返回(最高置信度, 中心坐标)"""
        if screenshot is None:
            return 0.0, None
        th, tw = tpl.shape[:2]
        sh, sw = screenshot.shape[:2]

        # 截图裁剪 ROI
        r = self._roi_pixels(roi)
        if r is not None:
            x, y, w, h = r
            if w <= 0 or h <= 0 or x < 0 or y < 0:
                return 0.0, None
            x2, y2 = min(sw, x + w), min(sh, y + h)
            if x2 - x < 10 or y2 - y < 10:
                return 0.0, None
            screen = screenshot[y:y2, x:x2]
            offset_x, offset_y = x, y
        else:
            screen = screenshot
            offset_x, offset_y = 0, 0

        if tw > screen.shape[1] or th > screen.shape[0]:
            return 0.0, None

        use_scales = self.scales if scales is None else tuple(scales)
        best_val, best_pos = 0.0, None

        for scale in use_scales:
            nw, nh = int(tw * scale), int(th * scale)
            if (nw > screen.shape[1] or nh > screen.shape[0]
                    or nw < 10 or nh < 10):
                continue
            resized = cv2.resize(tpl, (nw, nh))
            result = cv2.matchTemplate(screen, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_val:
                best_val = float(max_val)
                best_pos = (offset_x + max_loc[0] + nw // 2,
                            offset_y + max_loc[1] + nh // 2)

        return best_val, best_pos
