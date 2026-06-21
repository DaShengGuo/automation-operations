"""
visual_analysis.py — CLIP 零样本图像分类
判断视频截图的视觉内容：美白护肤 / 医疗疾病 / 其他无关
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import torch
from PIL import Image

from .model_loader import ModelLoader

logger = logging.getLogger(__name__)

# 零样本分类类别（中文，适配 CLIP 中文 tokenizer）
CATEGORIES = [
    "美白护肤皮肤改善相关内容，如肤色变白、皮肤变好、祛斑效果、前后对比",
    "皮肤病医疗药物相关内容，如医院就诊、药膏治疗、疾病诊断、处方药物",
    "其他无关内容，如美食旅游搞笑日常生活宠物运动",
]


@dataclass
class VisualResult:
    scores: list[float]       # softmax 概率 [p_good, p_bad, p_other]
    top_category: str         # 最高概率类别
    confidence: float         # 最高概率值
    caption: Optional[str] = None  # BLIP 标注（可选）


class VisualAnalysis:
    """CLIP 视觉分析器"""

    def __init__(self, model_loader: ModelLoader = None):
        self._ml = model_loader or ModelLoader()

    def classify(self, image_path: str) -> VisualResult:
        """对截图做零样本分类"""
        model, processor = self._ml.get_clip()
        image = Image.open(image_path).convert("RGB")

        inputs = processor(
            text=CATEGORIES,
            images=image,
            return_tensors="pt",
            padding=True,
        ).to(self._ml.device)

        with torch.no_grad():
            outputs = model(**inputs)
            logits_per_image = outputs.logits_per_image
            probs = logits_per_image.softmax(dim=1).cpu().numpy()[0]

        scores = probs.tolist()
        top_idx = int(probs.argmax())

        return VisualResult(
            scores=scores,
            top_category=CATEGORIES[top_idx][:30],
            confidence=float(probs[top_idx]),
        )

    @property
    def is_available(self) -> bool:
        try:
            self._ml.get_clip()
            return True
        except Exception:
            return False
