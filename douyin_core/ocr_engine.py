"""
douyin_core/ocr_engine.py
OCR 引擎封装 — 基于 PaddleOCR 的文字识别 + 时间戳解析
"""
from __future__ import annotations

import re
import cv2
import numpy as np
from typing import Optional

# 延迟加载 PaddleOCR
_ocr_instance = None


def _get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        from paddleocr import PaddleOCR
        _ocr_instance = PaddleOCR(lang="ch", use_angle_cls=True,
                                  show_log=False)
    return _ocr_instance


def region_to_pixels(w: int, h: int,
                     region: tuple[float, float, float, float]
                     ) -> tuple[int, int, int, int]:
    """将比例坐标转换为像素坐标"""
    l = int(w * region[0])
    t = int(h * region[1])
    r = int(w * region[2])
    b = int(h * region[3])
    return l, t, r, b


def crop_and_ocr(image_path: str,
                 region: tuple[float, float, float, float]) -> list[str]:
    img = cv2.imread(image_path)
    if img is None:
        return []
    h, w = img.shape[:2]
    l, t, r, b = region_to_pixels(w, h, region)
    if r <= l or b <= t:
        return []
    crop = img[t:b, l:r]
    if crop.size == 0:
        return []
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    scaled = cv2.resize(binary, None, fx=2, fy=2,
                        interpolation=cv2.INTER_CUBIC)
    ocr = _get_ocr()
    results = ocr.ocr(scaled, cls=True)
    texts = []
    if results and results[0]:
        for line in results[0]:
            texts.append(line[1][0])
    return texts


def ocr_full_screen(image_path: str) -> list[str]:
    img = cv2.imread(image_path)
    if img is None:
        return []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ocr = _get_ocr()
    results = ocr.ocr(gray, cls=True)
    texts = []
    if results and results[0]:
        for line in results[0]:
            texts.append(line[1][0])
    return texts


# ── 评论时间戳解析 ──

TIME_PATTERNS = [
    (r'^刚刚$', 0),
    (r'^(\d+)秒前$', 0),
    (r'^(\d+)分钟前$', 1),
    (r'^(\d+)小时前$', 60),
    (r'^(\d+)天前$', 1440),
]


def parse_comment_time(time_text: str) -> int:
    """解析抖音评论区时间文本，返回距今分钟数。无法解析返回 99999。"""
    time_text = time_text.strip()
    for pattern, multiplier in TIME_PATTERNS:
        m = re.match(pattern, time_text)
        if m:
            if multiplier == 0 and m.groups():
                return 0
            if multiplier == 0:
                return 0
            num = int(m.group(1))
            return num * multiplier
    return 99999


# ── 区域定义 ──
TITLE_REGION = (0.05, 0.78, 0.95, 0.88)
COMMENT_TIME_REGION = (0.55, 0.30, 0.90, 0.85)


def extract_video_title_texts(screenshot_path: str) -> list[str]:
    return crop_and_ocr(screenshot_path, TITLE_REGION)


def extract_comment_times(screenshot_path: str) -> list[str]:
    return crop_and_ocr(screenshot_path, COMMENT_TIME_REGION)
