"""
automation/pokemon_go/selectors.py
多语言页面选择器 — 从 config/game_pokemon_go.yaml 加载

禁止编造 selector: 所有文案来自真实录屏/真机截图(OCR 提取)与
真机 dump hierarchy。未标定的留空, 运行时明确报错。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from automation.pokemon_go.states import PokemonGoState


@dataclass
class StateRule:
    """一个页面状态的识别规则"""
    state: PokemonGoState
    # OCR 关键词规则: 每条=关键词组(组内 AND), 组间 OR。
    # 用片段匹配(应对 OCR 对繁体字的识别误差, 如 註→册)
    ocr_rules: list[list[str]] = field(default_factory=list)
    # UI hierarchy 文本/描述(hierarchy 可读时用, 游戏内 Unity 几乎无)
    hierarchy_texts: list[str] = field(default_factory=list)
    hierarchy_descs: list[str] = field(default_factory=list)
    # 模板图片名(不含 .png)
    templates: list[str] = field(default_factory=list)
    template_threshold: Optional[float] = None
    # 色块证据: 指定 ROI 内红色像素占比超过阈值即命中一条证据
    # (MAP 底部精灵球 — 模板失效/设备渲染差异时的兜底, 真机实测
    #  地图 0.048 vs 商店/菜单 ≤0.013)
    red_ratio_threshold: Optional[float] = None
    red_ratio_roi: tuple = (0.35, 0.82, 0.65, 0.97)
    # 需要命中多少条独立证据(默认 1)
    min_hits: int = 1

    def match_ocr(self, texts: list[str]) -> int:
        """返回命中的 OCR 规则数(关键词强制 str, 防御 YAML 布尔化)"""
        joined = " ".join(texts)
        hits = 0
        for group in self.ocr_rules:
            if all(str(k) in joined for k in group):
                hits += 1
        return hits

    def match_hierarchy(self, xml: str) -> int:
        hits = 0
        for t in self.hierarchy_texts:
            if f'text="{t}"' in xml:
                hits += 1
        for d in self.hierarchy_descs:
            if f'content-desc="{d}"' in xml:
                hits += 1
        return hits


class PokemonGoSelectors:
    """全部页面选择器集合(从 yaml 构建)"""

    def __init__(self, config: dict):
        self.rules: dict[PokemonGoState, StateRule] = {}
        states_cfg = config.get("states", {}) or {}
        for name, cfg in states_cfg.items():
            try:
                state = PokemonGoState(name.upper())
            except ValueError:
                continue
            self.rules[state] = StateRule(
                state=state,
                ocr_rules=[list(g) for g in (cfg.get("ocr_rules") or [])],
                hierarchy_texts=list(cfg.get("hierarchy_texts") or []),
                hierarchy_descs=list(cfg.get("hierarchy_descs") or []),
                templates=list(cfg.get("templates") or []),
                template_threshold=cfg.get("template_threshold"),
                red_ratio_threshold=cfg.get("red_ratio_threshold"),
                red_ratio_roi=tuple(cfg.get("red_ratio_roi")
                                    or (0.35, 0.82, 0.65, 0.97)),
                min_hits=int(cfg.get("min_hits", 1)),
            )
        # 从通用 page 配置迁移的兼容读取
        self.ptc = config.get("ptc", {}) or {}
        self.shop = config.get("shop", {}) or {}
        self.purchase = config.get("purchase", {}) or {}
        self.logout = config.get("logout", {}) or {}
        self.initial = config.get("initial", {}) or {}
        self.timeouts = config.get("timeouts", {}) or {}

    def rule(self, state: PokemonGoState) -> Optional[StateRule]:
        return self.rules.get(state)

    def timeout(self, key: str, default: float) -> float:
        return float(self.timeouts.get(key, default))
