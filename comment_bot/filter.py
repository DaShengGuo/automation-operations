"""
comment_bot/filter.py
视频筛选器 — OCR 内容判断 + 评论区时效分析
"""
from __future__ import annotations

from enum import Enum, auto

from douyin_core import config as cfg
from douyin_core.ocr_engine import crop_and_ocr, parse_comment_time


class FilterResult(Enum):
    PASS = auto()
    SKIP_EXCLUDED = auto()
    SKIP_INACTIVE = auto()
    SKIP_IRRELEVANT = auto()
    SKIP_ERROR = auto()


class VideoFilter:
    def __init__(self,
                 exclude_keywords: list[str] = None,
                 target_keywords: list[str] = None,
                 freshness_threshold: float = None,
                 sample_count: int = None):
        self.exclude_keywords = exclude_keywords or cfg.VIDEO_EXCLUDE_KEYWORDS
        self.target_keywords = target_keywords or cfg.VIDEO_TARGET_KEYWORDS
        self.freshness_threshold = freshness_threshold or cfg.FRESHNESS_THRESHOLD
        self.sample_count = sample_count or cfg.COMMENTS_TO_SAMPLE

    def check_content(self, screenshot_path: str) -> FilterResult:
        """OCR video title area, check content relevance."""
        try:
            texts = crop_and_ocr(screenshot_path, (0.05, 0.78, 0.95, 0.90))
        except Exception:
            return FilterResult.SKIP_ERROR

        if not texts:
            return FilterResult.SKIP_IRRELEVANT

        if self._contains_any(texts, self.exclude_keywords):
            return FilterResult.SKIP_EXCLUDED

        if not self._contains_any(texts, self.target_keywords):
            return FilterResult.SKIP_IRRELEVANT

        return FilterResult.PASS

    def calc_freshness_score(self, comment_minutes: list[int]) -> float:
        """Calculate comment freshness score (0-1)."""
        if not comment_minutes:
            return 0.0
        total = len(comment_minutes)
        in_5min = sum(1 for t in comment_minutes if t <= 5)
        in_15min = sum(1 for t in comment_minutes if t <= 15)
        return (in_5min / total) * 0.6 + (in_15min / total) * 0.4

    def parse_comment_times(self, comment_time_texts: list[str]) -> list[int]:
        """Parse OCR'd time text into minutes-since-posting."""
        result = []
        for text in comment_time_texts:
            minutes = parse_comment_time(text)
            if minutes < 99999:
                result.append(minutes)
        return result

    def should_comment(self, fresh_score: float) -> FilterResult:
        """Decide whether to comment based on freshness score."""
        if fresh_score >= self.freshness_threshold:
            return FilterResult.PASS
        return FilterResult.SKIP_INACTIVE

    @staticmethod
    def _contains_any(texts: list[str], keywords: list[str]) -> bool:
        for text in texts:
            for kw in keywords:
                if kw in str(text):
                    return True
        return False
