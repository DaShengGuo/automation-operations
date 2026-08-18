"""
core/ocr.py
可选 OCR 能力封装 — 多后端自动降级，全部不可用时返回空结果

后端优先级(按识别质量与离线可用性排序):
  1. rapidocr 主包 3.x(内置 PP-OCRv6 模型 — 真机实测
     「100 PokéCoins」全对, 而 PP-OCRv4 误识为「1ooPokeCoins」)
  2. rapidocr_onnxruntime 1.x(PP-OCRv4, wheel 内置, 离线)
  3. paddleocr(模型需联网下载, 3.x 与部分 paddle 版本不兼容)
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_engine = None
_tried = False


def _init_engine():
    """初始化第一个可用的 OCR 后端。返回后端名或 None。"""
    global _engine, _tried
    if _tried:
        return _engine.__class__.__module__ if _engine else None
    _tried = True

    # 1. rapidocr 主包(PP-OCRv6, wheel 内置模型)
    try:
        from rapidocr import RapidOCR
        _engine = RapidOCR()
        logger.info("[OCR] 使用 rapidocr(PP-OCRv6)")
        return "rapidocr_v6"
    except Exception as e:
        logger.info(f"[OCR] rapidocr 不可用({e})，尝试 rapidocr_onnxruntime")

    # 2. rapidocr_onnxruntime 1.x(PP-OCRv4)
    try:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
        logger.info("[OCR] 使用 rapidocr_onnxruntime(PP-OCRv4)")
        return "rapidocr_v4"
    except Exception as e:
        logger.info(f"[OCR] rapidocr_onnxruntime 不可用({e})，尝试 paddleocr")

    # 3. paddleocr(2.x/3.x 均尝试)
    try:
        from paddleocr import PaddleOCR
        try:
            engine = PaddleOCR(lang="ch", use_angle_cls=True, show_log=False)
        except (ValueError, TypeError):
            engine = PaddleOCR(lang="ch")  # 3.x 已移除 show_log
        _engine = engine
        logger.info("[OCR] 使用 paddleocr")
        return "paddleocr"
    except Exception as e:
        logger.info(f"[OCR] paddleocr 不可用({e})，OCR 功能关闭")
        _engine = None
        return None


def ocr_available() -> bool:
    return _init_engine() is not None


def ocr_texts(image_bgr: np.ndarray, min_conf: float = 0.5) -> list[str]:
    """识别截图(BGR)中的全部文本"""
    return [t for t, _ in ocr_with_boxes(image_bgr, min_conf)]


def ocr_with_boxes(image_bgr: np.ndarray, min_conf: float = 0.5
                   ) -> list[tuple[str, tuple[int, int, int, int]]]:
    """识别文本+位置。返回 [(text, (x1, y1, x2, y2)), ...]。OCR 不可用返回 []

    性能优化: 输入最大边 > 1200 时等比缩小(实测 1080x2400 → ~2s/张,
    3 倍加速且识别率不变), 坐标自动还原到原图。
    """
    if _init_engine() is None or image_bgr is None:
        return []
    import cv2
    try:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        scale = 1.0
        max_side = max(h, w)
        if max_side > 1200:
            scale = 1200.0 / max_side
            gray = cv2.resize(gray, (int(w * scale), int(h * scale)))
        module = _engine.__class__.__module__

        # rapidocr 主包 3.x: 返回 RapidOCROutput(txts/boxes/scores)
        if "rapidocr.main" in module or hasattr(_engine, "txts"):
            result = _engine(gray)
            out = []
            for box, text, conf in zip(result.boxes, result.txts,
                                       result.scores):
                if float(conf) < min_conf:
                    continue
                xs = [p[0] / scale for p in box]
                ys = [p[1] / scale for p in box]
                out.append((str(text),
                            (int(min(xs)), int(min(ys)),
                             int(max(xs)), int(max(ys)))))
            return out

        # rapidocr_onnxruntime 1.x: 返回 (result, elapse)
        if "rapidocr_onnxruntime" in module:
            result, _ = _engine(gray)
            if not result:
                return []
            out = []
            for box, text, conf in result:
                if float(conf) < min_conf:
                    continue
                xs = [p[0] / scale for p in box]
                ys = [p[1] / scale for p in box]
                out.append((str(text),
                            (int(min(xs)), int(min(ys)),
                             int(max(xs)), int(max(ys)))))
            return out

        # paddleocr 2.x: results[0] = [(box, (text, conf)), ...]
        results = _engine.ocr(gray, cls=True)
        out = []
        if results and results[0]:
            for line in results[0]:
                box, (text, conf) = line
                if float(conf) < min_conf:
                    continue
                xs = [p[0] / scale for p in box]
                ys = [p[1] / scale for p in box]
                out.append((str(text),
                            (int(min(xs)), int(min(ys)),
                             int(max(xs)), int(max(ys)))))
        return out
    except Exception as e:
        logger.debug(f"OCR 识别异常: {e}")
        return []
