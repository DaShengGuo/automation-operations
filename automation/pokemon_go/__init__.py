"""
automation/pokemon_go — Pokémon GO 业务适配器

浏览器无关的 PTC 登录: 点击「寶可夢訓練家中央站」后由 Android 系统
自动调用该手机默认浏览器(无论品牌), 业务层只感知 ExternalWebContext,
不感知浏览器品牌、不维护浏览器白名单。
"""
from automation.pokemon_go.adapter import PokemonGoAdapter
from automation.pokemon_go.states import PokemonGoState, PgoLoginResult

__all__ = ["PokemonGoAdapter", "PokemonGoState", "PgoLoginResult"]
