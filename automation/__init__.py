"""
automation — 游戏适配器层
新增游戏 = 新增一个 BaseGameAutomation 子类 + 一份 game_xxx.yaml 配置
"""
from __future__ import annotations

from typing import Optional, Type

from automation.base_game import BaseGameAutomation, LoginResult
from automation.target_game import DouyinAutomation, TargetGameAutomation

ADAPTERS: dict[str, Type[BaseGameAutomation]] = {
    "douyin": DouyinAutomation,
    "target_game": TargetGameAutomation,
    "generic": BaseGameAutomation,
}


def _register_pokemon_go():
    """延迟注册 Pokémon GO 适配器(避免启动时 import 链过重)"""
    global ADAPTERS
    if "pokemon_go" not in ADAPTERS:
        from automation.pokemon_go.adapter import PokemonGoAdapter
        ADAPTERS["pokemon_go"] = PokemonGoAdapter


def create_automation(adapter_name: str, controller, cfg,
                      ) -> Optional[BaseGameAutomation]:
    """按 game yaml 的 adapter 字段创建自动化实例"""
    if adapter_name == "pokemon_go":
        _register_pokemon_go()
    cls = ADAPTERS.get(adapter_name)
    if cls is None:
        return None
    return cls(controller, cfg)


__all__ = ["BaseGameAutomation", "LoginResult", "DouyinAutomation",
           "TargetGameAutomation", "create_automation", "ADAPTERS"]

