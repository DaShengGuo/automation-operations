"""
core/ui_detector.py
页面状态识别 — 综合 UI 层级(resource-id/text/content-desc) + 图像模板 + OCR(可选)

检测优先级(按 config/game.yaml 的 pages 定义顺序):
  1. resource-id 精确匹配
  2. text / content-desc 匹配
  3. 图像模板匹配
  4. OCR 关键词(可选, paddleocr 未安装时自动跳过)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from models.page_state import PageState

logger = logging.getLogger(__name__)


@dataclass
class PageRule:
    """一个页面状态的识别规则（config/game.yaml pages 段解析而来）"""
    name: str
    texts: list = field(default_factory=list)
    descs: list = field(default_factory=list)
    resource_ids: list = field(default_factory=list)
    templates: list = field(default_factory=list)
    ocr_keywords: list = field(default_factory=list)
    requires_all: bool = False   # True=全部命中才匹配; False=命中任意一条即匹配


class UiDetector:
    """页面检测器：每个 DeviceController 一个实例（线程隔离）"""

    def __init__(self, controller, pages_config: dict, matcher=None):
        self.d = controller
        self.matcher = matcher
        self.rules: list[PageRule] = self._parse_rules(pages_config)
        self._last_xml: str = ""
        self._last_page: PageState = PageState.UNKNOWN

    @staticmethod
    def _parse_rules(pages_config: dict) -> list[PageRule]:
        rules = []
        for name, cfg in (pages_config or {}).items():
            if not isinstance(cfg, dict):
                continue
            rules.append(PageRule(
                name=name,
                texts=[str(t) for t in (cfg.get("texts") or [])],
                descs=[str(t) for t in (cfg.get("descs") or [])],
                resource_ids=[str(t) for t in (cfg.get("resource_ids") or [])],
                templates=[str(t) for t in (cfg.get("templates") or [])],
                ocr_keywords=[str(t) for t in (cfg.get("ocr_keywords") or [])],
                requires_all=bool(cfg.get("requires_all", False)),
            ))
        return rules

    # ── UI 层级 ──

    def dump_hierarchy(self) -> str:
        """获取 UI 层级 XML（带缓存失效保护）"""
        try:
            self._last_xml = self.d.dump_hierarchy()
        except Exception as e:
            logger.debug(f"dump_hierarchy 失败: {e}")
            self._last_xml = ""
        return self._last_xml

    def find_element(self, selector: dict, timeout: float = 5.0
                     ) -> tuple[bool, Optional[object]]:
        """按 {text|desc|resource_id} 找控件。返回 (是否找到, 元素)"""
        if not selector:
            return False, None
        if "text" in selector and selector["text"]:
            el = self.d.device(text=selector["text"])
        elif "desc" in selector and selector["desc"]:
            el = self.d.device(description=selector["desc"])
        elif "resource_id" in selector and selector["resource_id"]:
            el = self.d.device(resourceId=selector["resource_id"])
        else:
            return False, None
        try:
            if el.wait(timeout=timeout):
                return True, el
        except Exception as e:
            logger.debug(f"find_element 异常: {e}")
        return False, None

    def find_xpath(self, xpath: str, timeout: float = 5.0
                   ) -> tuple[bool, Optional[object]]:
        if not xpath:
            return False, None
        try:
            el = self.d.device.xpath(xpath)
            if el.wait(timeout=timeout):
                return True, el
        except Exception as e:
            logger.debug(f"find_xpath 异常: {e}")
        return False, None

    # ── 页面检测 ──

    def detect_page(self) -> PageState:
        """综合判断当前页面状态。无法识别返回 UNKNOWN。"""
        xml = self.dump_hierarchy()
        for rule in self.rules:
            if self._rule_matches(rule, xml):
                self._last_page = self._as_page_state(rule.name)
                logger.debug(f"[页面识别] {rule.name}")
                return self._last_page
        self._last_page = PageState.UNKNOWN
        return PageState.UNKNOWN

    def wait_page(self, page: str, timeout: float = 30.0,
                  interval: float = 1.0) -> bool:
        """等待页面变为指定状态"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.detect_page() == PageState(page):
                return True
            time.sleep(interval)
        return False

    def _rule_matches(self, rule: PageRule, xml: str) -> bool:
        hits = []
        if rule.resource_ids:
            hits.append(any(rid in xml for rid in rule.resource_ids))
        if rule.texts:
            hits.append(any(f'text="{t}"' in xml for t in rule.texts))
        if rule.descs:
            hits.append(any(f'content-desc="{d}"' in xml for d in rule.descs))
        if rule.templates and self.matcher is not None:
            hits.append(self._templates_match(rule.templates))
        if rule.ocr_keywords:
            hits.append(self._ocr_match(rule.ocr_keywords))
        if not hits:
            return False
        return all(hits) if rule.requires_all else any(hits)

    def _templates_match(self, templates: list) -> bool:
        try:
            shot = self.d.screenshot()
        except Exception:
            return False
        return any(self.matcher.exists(t, shot) for t in templates)

    def _ocr_match(self, keywords: list) -> bool:
        """OCR 关键词匹配（可选能力，未装 paddleocr 时恒 False）"""
        try:
            from core.ocr import ocr_texts  # 延迟导入，无 paddleocr 抛 ImportError
        except ImportError:
            return False
        try:
            texts = ocr_texts(self.d.screenshot())
        except Exception as e:
            logger.debug(f"OCR 异常: {e}")
            return False
        joined = "".join(texts)
        return any(k in joined for k in keywords)

    @staticmethod
    def _as_page_state(name: str) -> PageState:
        try:
            return PageState(name.upper())
        except ValueError:
            return PageState.UNKNOWN

    @property
    def last_page(self) -> PageState:
        return self._last_page
