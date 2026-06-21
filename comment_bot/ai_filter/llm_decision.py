"""
llm_decision.py — LLM 最终决策（Qwen2.5-1.5B GGUF）
+ 规则兜底（LLM 不可用时）
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from douyin_core import config as cfg
from .model_loader import ModelLoader
from .visual_analysis import VisualResult
from .semantic_match import SemanticResult
from comment_bot.filter import FilterResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个抖音视频内容审核助手。
判断视频是否关于"皮肤美白/改善/护肤"主题，而非"皮肤病/医疗/药物"内容。

规则：
1. 如果视频关于皮肤美白、祛斑、肤色改善、护肤经验分享 → PASS
2. 如果视频关于皮肤病治疗、医院就诊、药物推荐、疾病诊断 → SKIP
3. 如果视频与皮肤完全无关（美食、旅游、搞笑）→ SKIP
4. 仅当确信是护肤美白相关内容时才 PASS

请以JSON格式输出：{"decision": "PASS"|"SKIP", "reasoning": "...", "confidence": 0.0-1.0}"""


@dataclass
class LLMDecisionResult:
    decision: str          # "PASS" | "SKIP"
    reasoning: str         # 中文解释
    confidence: float      # 0.0-1.0
    raw_response: str = ""


class LLMDecision:
    """LLM 决策器"""

    def __init__(self, model_loader: ModelLoader = None):
        self._ml = model_loader or ModelLoader()

    def decide(
        self,
        ocr_texts: list[str],
        visual_result: VisualResult,
        semantic_result: SemanticResult,
    ) -> LLMDecisionResult:
        """LLM 决策, 失败则规则兜底"""
        llm = self._ml.get_llm()
        if llm is None:
            return self._fallback_decision(visual_result, semantic_result)

        prompt = self._build_prompt(ocr_texts, visual_result, semantic_result)
        try:
            raw = llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=256,
            )
            response_text = raw["choices"][0]["message"]["content"]
            return self._parse_response(response_text)
        except Exception as e:
            logger.warning(f"[LLM] 推理失败: {e}")
            return self._fallback_decision(visual_result, semantic_result)

    def to_filter_result(self, llm_result: LLMDecisionResult) -> FilterResult:
        """LLM 结果 → FilterResult"""
        if llm_result.decision == "PASS":
            return FilterResult.PASS
        return FilterResult.SKIP_EXCLUDED

    def _build_prompt(
        self,
        ocr_texts: list[str],
        visual: VisualResult,
        semantic: SemanticResult,
    ) -> str:
        ocr = " ".join(ocr_texts) if ocr_texts else "(无OCR文本)"
        return f"""视频截图OCR文本: {ocr}

视觉分析: {visual.top_category} (置信度 {visual.confidence:.2f})
语义匹配: 正样本最大相似度 {semantic.target_max_sim:.2f}, 负样本最大相似度 {semantic.exclude_max_sim:.2f}

请判断: PASS(美白护肤相关内容) 还是 SKIP(医疗疾病/无关内容)?"""

    def _parse_response(self, raw: str) -> LLMDecisionResult:
        # 尝试提取 JSON
        json_match = re.search(r'\{[^}]+\}', raw)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return LLMDecisionResult(
                    decision=data.get("decision", "SKIP"),
                    reasoning=data.get("reasoning", ""),
                    confidence=float(data.get("confidence", 0.5)),
                    raw_response=raw,
                )
            except (json.JSONDecodeError, ValueError):
                pass
        # JSON 解析失败 → 规则兜底
        return LLMDecisionResult(
            decision="SKIP",
            reasoning=f"JSON解析失败: {raw[:100]}",
            confidence=0.3,
            raw_response=raw,
        )

    def _fallback_decision(
        self,
        visual: VisualResult,
        semantic: SemanticResult,
    ) -> LLMDecisionResult:
        """规则兜底"""
        # target 信号强且 exclude 弱 → PASS
        if (
            visual.scores[0] > visual.scores[1]
            and semantic.target_max_sim > semantic.exclude_max_sim
            and visual.scores[0] > cfg.AI_CONFIDENCE_THRESHOLD
        ):
            return LLMDecisionResult(
                decision="PASS",
                reasoning=f"规则兜底: CLIP={visual.scores[0]:.2f} BGE_target={semantic.target_max_sim:.2f}",
                confidence=visual.scores[0],
            )
        return LLMDecisionResult(
            decision="SKIP",
            reasoning=f"规则兜底: 信号不足",
            confidence=0.5,
        )
