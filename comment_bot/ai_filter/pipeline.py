"""
pipeline.py — AI 筛选管线编排器
Stage 0: OCR → Stage 1: 关键词快筛 → Stage 2: CLIP+BGE+Qdrant → Stage 3: LLM
"""
from __future__ import annotations

import logging
from typing import Optional

from douyin_core import config as cfg
from douyin_core.ocr_engine import crop_and_ocr
from comment_bot.filter import FilterResult

from .model_loader import ModelLoader
from .vector_store import VectorStore
from .visual_analysis import VisualAnalysis, VisualResult
from .semantic_match import SemanticMatch, SemanticResult

logger = logging.getLogger(__name__)

TITLE_REGION = (0.05, 0.78, 0.95, 0.90)


class AIPipeline:
    """多 Stage AI 筛选管线"""

    def __init__(
        self,
        exclude_keywords: list[str] = None,
        target_keywords: list[str] = None,
        model_loader: ModelLoader = None,
        vector_store: VectorStore = None,
    ):
        self.exclude_keywords = exclude_keywords or cfg.VIDEO_EXCLUDE_KEYWORDS
        self.target_keywords = target_keywords or cfg.VIDEO_TARGET_KEYWORDS
        self._ml = model_loader or ModelLoader()
        self._vs = vector_store or VectorStore()
        self._visual: Optional[VisualAnalysis] = None
        self._semantic: Optional[SemanticMatch] = None
        self._llm = None  # lazy, Phase 3
        self._use_ai = cfg.AI_ENABLED
        self._confidence_threshold = cfg.AI_CONFIDENCE_THRESHOLD
        self._exclude_threshold = cfg.AI_SIMILARITY_EXCLUDE_THRESHOLD

    @property
    def is_healthy(self) -> bool:
        return self._use_ai

    def screen(self, screenshot_path: str) -> FilterResult:
        """
        主入口：四阶段管线，失败降级到关键词。
        """
        # Stage 0: OCR
        ocr_texts = self._stage0_ocr(screenshot_path)

        # Stage 1: 关键词快筛
        kw_result = self._stage1_keyword_gate(ocr_texts)
        if kw_result is not None:
            return kw_result  # 关键词明确排除或无关

        # Stage 2: CLIP + BGE
        try:
            stage2 = self._stage2_visual_semantic(screenshot_path, ocr_texts)
            decision = self._decision_gate(stage2)
            if decision is not None:
                return decision
        except Exception as e:
            logger.warning(f"[Pipeline] Stage2 失败: {e}, 降级到关键词")

        # Stage 3: LLM (Phase 3 实现)
        if cfg.AI_USE_LLM and self._llm:
            try:
                return self._stage3_llm_decision(ocr_texts, stage2)
            except Exception as e:
                logger.warning(f"[Pipeline] Stage3 失败: {e}")

        # 最终降级: 关键词 PASS
        if ocr_texts:
            return FilterResult.PASS
        return FilterResult.SKIP_IRRELEVANT

    # ── Stage 0 ──

    def _stage0_ocr(self, screenshot_path: str) -> list[str]:
        try:
            return crop_and_ocr(screenshot_path, TITLE_REGION)
        except Exception:
            return []

    # ── Stage 1 ──

    def _stage1_keyword_gate(self, texts: list[str]) -> Optional[FilterResult]:
        """快速关键词门控。返回 None 表示需要通过 AI。"""
        if not texts:
            return FilterResult.SKIP_IRRELEVANT
        if self._contains_any(texts, self.exclude_keywords):
            return FilterResult.SKIP_EXCLUDED
        if not self._contains_any(texts, self.target_keywords):
            return FilterResult.SKIP_IRRELEVANT
        # 关键词通过 → 需要 AI 确认
        return None

    # ── Stage 2 ──

    def _stage2_visual_semantic(self, screenshot_path: str, ocr_texts: list[str]):
        """CLIP 视觉 + BGE 语义"""
        if self._visual is None:
            self._visual = VisualAnalysis(self._ml)
        if self._semantic is None:
            self._semantic = SemanticMatch(self._ml, self._vs)

        visual = self._visual.classify(screenshot_path)
        semantic = self._semantic.search(ocr_texts)
        logger.info(
            f"[Pipeline] CLIP={visual.confidence:.2f}({visual.top_category}) "
            f"BGE target={semantic.target_max_sim:.2f} exclude={semantic.exclude_max_sim:.2f}"
        )
        return (visual, semantic)

    # ── Decision Gate ──

    def _decision_gate(self, stage2: tuple) -> Optional[FilterResult]:
        """基于 CLIP+BGE 做决策"""
        visual, semantic = stage2

        # 高置信 exclusion
        if visual.scores[1] > 0.7 or semantic.exclude_max_sim > self._exclude_threshold:
            return FilterResult.SKIP_EXCLUDED

        # 高置信 pass
        if visual.scores[0] > self._confidence_threshold and semantic.exclude_max_sim < 0.3:
            return FilterResult.PASS

        # 模糊 → 需要 LLM
        return None

    # ── Stage 3 (stub, Phase 3 实现) ──

    def _stage3_llm_decision(self, ocr_texts, stage2) -> FilterResult:
        """LLM 决策 (Phase 3 实现完整版, 现在规则兜底)"""
        visual, semantic = stage2
        # 规则兜底: target 信号强于 exclude
        if visual.scores[0] > visual.scores[1] and semantic.target_max_sim > semantic.exclude_max_sim:
            return FilterResult.PASS
        return FilterResult.SKIP_IRRELEVANT

    # ── Helpers ──

    @staticmethod
    def _contains_any(texts: list[str], keywords: list[str]) -> bool:
        for text in texts:
            for kw in keywords:
                if kw in str(text):
                    return True
        return False
