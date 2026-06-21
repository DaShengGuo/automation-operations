"""
semantic_match.py — BGE 文本嵌入 + Qdrant 语义相似度搜索
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .model_loader import ModelLoader
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class SemanticResult:
    target_max_sim: float = 0.0
    target_avg_sim: float = 0.0
    exclude_max_sim: float = 0.0
    exclude_avg_sim: float = 0.0
    target_matches: list[dict] = field(default_factory=list)
    exclude_matches: list[dict] = field(default_factory=list)


class SemanticMatch:
    """BGE 语义匹配器"""

    def __init__(self, model_loader: ModelLoader = None, vector_store: VectorStore = None):
        self._ml = model_loader or ModelLoader()
        self._vs = vector_store or VectorStore()

    def embed(self, texts: list[str]) -> np.ndarray:
        """BGE 嵌入文本"""
        model = self._ml.get_bge()
        return model.encode(texts, normalize_embeddings=True)

    def search(self, ocr_texts: list[str], blip_caption: str = None) -> SemanticResult:
        """OCR 文本 + 可选 BLIP 标注 → Qdrant 搜索"""
        if not self._vs.is_seeded:
            logger.warning("[Semantic] Qdrant 未注入种子数据, 返回空结果")
            return SemanticResult()

        # 合并 OCR + BLIP 文本
        combined = " ".join(ocr_texts) if ocr_texts else ""
        if blip_caption:
            combined += " " + blip_caption
        if not combined.strip():
            return SemanticResult()

        # 嵌入
        vector = self.embed([combined])[0]

        # 搜索两个 Collection
        targets, excludes = self._vs.search_both(vector, top_k=3)

        target_scores = [t["score"] for t in targets] if targets else [0]
        exclude_scores = [e["score"] for e in excludes] if excludes else [0]

        return SemanticResult(
            target_max_sim=max(target_scores),
            target_avg_sim=sum(target_scores) / len(target_scores),
            exclude_max_sim=max(exclude_scores),
            exclude_avg_sim=sum(exclude_scores) / len(exclude_scores),
            target_matches=targets,
            exclude_matches=excludes,
        )

    @property
    def is_available(self) -> bool:
        try:
            self._ml.get_bge()
            return self._vs.is_seeded
        except Exception:
            return False
