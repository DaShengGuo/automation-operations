"""
desktop/state_registry.py
PokemonStateRegistry — 步骤唯一定义源。

GUI 下拉菜单/恢复逻辑全部从这里读, 禁止 GUI 手写第二套状态。
步骤按现有 WorkerState 流水线对齐(恢复点), 细粒度页面作为展示。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from automation.pokemon_go.states import PokemonGoState
from core.state_machine import WorkerState


@dataclass(frozen=True)
class StepEntry:
    key: str                          # 唯一标识(WorkerState.value)
    display_name: str                 # GUI 显示名
    order: int                        # 流程顺序
    worker_state: WorkerState         # 恢复时 fsm.force 目标
    required_pages: tuple = field(default_factory=tuple)
    # 该步骤要求的手机真实页面(PokemonGoState); 空 = 不校验
    detail: str = ""                  # 说明文字


class PokemonStateRegistry:
    """流程步骤注册表 — 数据源唯一。"""

    AUTO = "AUTO"  # 「自动识别当前步骤」虚拟项

    STEPS: tuple[StepEntry, ...] = (
        StepEntry("CHECK_DEVICE", "01 获取账号", 1,
                  WorkerState.CHECK_DEVICE, (), "领取/导入下一个账号"),
        StepEntry("START_GAME", "02 打开 Pokémon GO", 2,
                  WorkerState.START_GAME, (), "启动游戏"),
        StepEntry("DETECT_PAGE", "03 检测当前页面", 3,
                  WorkerState.DETECT_PAGE, (), "识别手机真实页面并路由"),
        StepEntry("LOGIN", "04 登录账号", 4,
                  WorkerState.LOGIN,
                  (PokemonGoState.RETURNING_PLAYER,
                   PokemonGoState.LOGIN_PROVIDER,
                   PokemonGoState.PTC_LOGIN_PAGE,
                   PokemonGoState.PTC_REDIRECTING),
                  "已註冊的玩家 / 登录方式 / PTC 网页登录"),
        StepEntry("WAIT_HOME", "05 等待游戏主页", 5,
                  WorkerState.WAIT_HOME,
                  (PokemonGoState.RETURNING_TO_GAME,
                   PokemonGoState.GAME_LOADING), "登录后等待进入游戏"),
        StepEntry("HANDLE_POPUPS", "06 处理首次弹窗", 6,
                  WorkerState.HANDLE_POPUPS,
                  (PokemonGoState.WELCOME_PAGE,
                   PokemonGoState.PROFESSOR_DIALOG,
                   PokemonGoState.INITIAL_PROMPT,
                   PokemonGoState.MAP), "欢迎页/博士对话/LET'S GO"),
        StepEntry("EXECUTE_TASK", "07 执行购买任务", 7,
                  WorkerState.EXECUTE_TASK,
                  (PokemonGoState.MAP, PokemonGoState.MAIN_MENU,
                   PokemonGoState.SHOP, PokemonGoState.SHOP_SEARCHING,
                   PokemonGoState.PRODUCT_FOUND),
                  "主地图 → 商店 → 查找商品 → 购买页"),
        StepEntry("VERIFY_TASK", "08 验证购买结果", 8,
                  WorkerState.VERIFY_TASK,
                  (PokemonGoState.PURCHASE_SUCCESS,
                   PokemonGoState.PURCHASE_FAILED, PokemonGoState.MAP),
                  "确认购买结果"),
        StepEntry("LOGOUT", "09 退出登录", 9,
                  WorkerState.LOGOUT,
                  (PokemonGoState.MAP, PokemonGoState.SETTINGS,
                   PokemonGoState.LOGOUT_CONFIRM),
                  "设置 → 退出登录 → 回到已註冊的玩家页"),
        StepEntry("CLEANUP", "10 完成清理", 10,
                  WorkerState.CLEANUP, (), "结果落库/截图归档"),
        StepEntry("NEXT_ACCOUNT", "11 下一账号", 11,
                  WorkerState.NEXT_ACCOUNT, (), "领取下一个账号"),
    )

    @classmethod
    def ordered_steps(cls) -> list[StepEntry]:
        return sorted(cls.STEPS, key=lambda s: s.order)

    @classmethod
    def by_key(cls, key: str) -> Optional[StepEntry]:
        for s in cls.STEPS:
            if s.key == key:
                return s
        return None

    @classmethod
    def by_worker_state(cls, state: WorkerState) -> Optional[StepEntry]:
        for s in cls.STEPS:
            if s.worker_state == state:
                return s
        return None

    @classmethod
    def suggest_for_page(cls, page: PokemonGoState) -> Optional[StepEntry]:
        """根据手机真实页面建议应继续的步骤(恢复时优先真实页面)。"""
        for s in cls.STEPS:
            if page in s.required_pages:
                return s
        return None

    @classmethod
    def validate(cls, step: StepEntry,
                 actual_page: PokemonGoState) -> Optional[str]:
        """校验选择步骤与手机真实页面是否匹配。返回 None=匹配, 否则错误提示。"""
        if not step.required_pages:
            return None
        if actual_page in step.required_pages:
            return None
        pages = " / ".join(p.value for p in step.required_pages)
        return (f"当前页面与选择步骤不匹配\n\n"
                f"当前识别: {actual_page.value}\n"
                f"选择步骤: {step.display_name}\n"
                f"该步骤要求: {pages}\n\n"
                f"建议: 使用「自动识别当前步骤」")
