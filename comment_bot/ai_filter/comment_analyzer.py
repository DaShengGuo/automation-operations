"""
comment_analyzer.py — 评论区深度分析
BGE 语义匹配 + 互动质量评分
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .model_loader import ModelLoader
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class CommentAnalysis:
    """评论区分析结果"""
    total_comments: int = 0
    fresh_5min: int = 0
    fresh_15min: int = 0
    freshness_score: float = 0.0

    # 语义分析
    topic_relevance: float = 0.0     # 评论内容与目标主题的相关度
    medical_risk: float = 0.0        # 评论内容含医疗/疾病风险
    engagement_quality: float = 0.0  # 互动质量 (0-1)

    # 建议
    should_comment: bool = False
    reason: str = ""


class CommentAnalyzer:
    """评论区分析器"""

    def __init__(self, model_loader: ModelLoader = None, vector_store: VectorStore = None):
        self._ml = model_loader or ModelLoader()
        self._vs = vector_store or VectorStore()

    def analyze(
        self,
        comment_texts: list[str],
        comment_minutes: list[int],
    ) -> CommentAnalysis:
        """分析评论区：新鲜度 + 语义匹配 + 互动质量"""
        result = CommentAnalysis()
        result.total_comments = len(comment_minutes)

        if not comment_minutes:
            result.reason = "无评论数据"
            return result

        # 1. 新鲜度
        result.fresh_5min = sum(1 for t in comment_minutes if t <= 5)
        result.fresh_15min = sum(1 for t in comment_minutes if t <= 15)
        total = len(comment_minutes)
        result.freshness_score = (
            (result.fresh_5min / total) * 0.6
            + (result.fresh_15min / total) * 0.4
        )

        # 2. 语义匹配（如果 BGE 可用）
        if comment_texts and self._vs.is_seeded:
            try:
                combined = " ".join(comment_texts[:10])  # 取前10条
                bge = self._ml.get_bge()
                vec = bge.encode([combined], normalize_embeddings=True)[0]

                targets, excludes = self._vs.search_both(vec, top_k=3)
                result.topic_relevance = (
                    max([t["score"] for t in targets]) if targets else 0.0
                )
                result.medical_risk = (
                    max([e["score"] for e in excludes]) if excludes else 0.0
                )
            except Exception as e:
                logger.debug(f"[CommentAnalyzer] 语义分析失败: {e}")

        # 3. 互动质量评分
        result.engagement_quality = self._calc_engagement(
            result.freshness_score,
            result.topic_relevance,
            result.medical_risk,
        )

        # 4. 决策
        if result.freshness_score < 0.3:
            result.should_comment = False
            result.reason = f"新鲜度不足({result.freshness_score:.2f})"
        elif result.medical_risk > 0.5:
            result.should_comment = False
            result.reason = f"医疗风险高({result.medical_risk:.2f})"
        elif result.topic_relevance > 0.3 or result.freshness_score > 0.5:
            result.should_comment = True
            result.reason = (
                f"主题相关({result.topic_relevance:.2f}) "
                f"+ 新鲜({result.freshness_score:.2f})"
            )
        else:
            result.should_comment = False
            result.reason = "信号不足"

        return result

    @staticmethod
    def _calc_engagement(freshness: float, relevance: float, risk: float) -> float:
        """互动质量评分: 新鲜度 + 相关性 - 风险"""
        score = freshness * 0.4 + relevance * 0.4 - risk * 0.2
        return max(0.0, min(1.0, score))
