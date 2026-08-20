"""
automation/pokemon_go/states.py
Pokémon GO 页面状态机

所有业务操作根据 detect_state() 的结果决定下一步，
禁止 click()+sleep() 盲串联。
"""
from __future__ import annotations

from enum import Enum


class PokemonGoState(str, Enum):
    """Pokémon GO 完整页面状态"""
    UNKNOWN = "UNKNOWN"
    GAME_SPLASH = "GAME_SPLASH"                # 启动闪屏(版权页)
    RETURNING_PLAYER = "RETURNING_PLAYER"      # 已註冊的玩家 / 尚未註冊的玩家
    # 注册选择页别名: 业务术语「已注册/未注册选择页」。与 RETURNING_PLAYER
    # 同一状态(值相同), 用于诊断日志/恢复代码表达意图, 不引入平行状态。
    REGISTER_SELECT = "RETURNING_PLAYER"
    LOGIN_FAILED_DIALOG = "LOGIN_FAILED_DIALOG"  # 無法登入弹窗(残留失败会话, 真机: 無法登入/再試一次/以其他帳號登入)
    LOGIN_PROVIDER = "LOGIN_PROVIDER"          # 登录方式选择页
    PTC_REDIRECTING = "PTC_REDIRECTING"        # 已点击PTC, 系统跳转浏览器中
    PTC_LOGIN_PAGE = "PTC_LOGIN_PAGE"          # 网页登录表单已就绪
    PTC_LOGIN_SUBMITTING = "PTC_LOGIN_SUBMITTING"  # 已提交, 认证中
    PTC_LOGIN_ERROR = "PTC_LOGIN_ERROR"        # 网页登录错误
    AUTHORIZING = "AUTHORIZING"                # 网页授权处理中
    RETURNING_TO_GAME = "RETURNING_TO_GAME"    # 已回到游戏前台
    GAME_LOADING = "GAME_LOADING"              # 游戏加载(全屏标题)
    WELCOME_PAGE = "WELCOME_PAGE"              # 首次欢迎页(存在则处理)
    PROFESSOR_DIALOG = "PROFESSOR_DIALOG"      # 博士对话(存在则处理)
    INITIAL_PROMPT = "INITIAL_PROMPT"          # LET'S GO 提示
    MAP = "MAP"                                # 主地图
    MAIN_MENU = "MAIN_MENU"                    # Poké Ball 主菜单
    SHOP = "SHOP"                              # 商店
    SHOP_SEARCHING = "SHOP_SEARCHING"          # 商店内寻找目标商品
    PRODUCT_FOUND = "PRODUCT_FOUND"            # 已定位目标商品
    PURCHASE_PAGE = "PURCHASE_PAGE"            # Google Play 购买页
    PURCHASE_PROCESSING = "PURCHASE_PROCESSING"  # 购买处理中
    PURCHASE_SUCCESS = "PURCHASE_SUCCESS"      # 购买成功
    PURCHASE_FAILED = "PURCHASE_FAILED"        # 购买失败
    SETTINGS = "SETTINGS"                      # 设置页
    LOGOUT_CONFIRM = "LOGOUT_CONFIRM"          # 退出确认弹窗
    LOGGED_OUT = "LOGGED_OUT"                  # 已登出(黑屏转场)
    ACCOUNT_FINISHED = "ACCOUNT_FINISHED"      # 账号流程完成(回到RETURNING_PLAYER)
    RECOVERY = "RECOVERY"                      # 异常恢复中

    # ── 分组 ──

    @property
    def is_game_foreground_state(self) -> bool:
        """游戏内页面(与外部网页上下文区分)"""
        return self in (
            PokemonGoState.GAME_SPLASH, PokemonGoState.RETURNING_PLAYER,
            PokemonGoState.LOGIN_FAILED_DIALOG, PokemonGoState.LOGIN_PROVIDER,
            PokemonGoState.RETURNING_TO_GAME,
            PokemonGoState.GAME_LOADING, PokemonGoState.WELCOME_PAGE,
            PokemonGoState.PROFESSOR_DIALOG, PokemonGoState.INITIAL_PROMPT,
            PokemonGoState.MAP, PokemonGoState.MAIN_MENU,
            PokemonGoState.SHOP, PokemonGoState.SHOP_SEARCHING,
            PokemonGoState.PRODUCT_FOUND, PokemonGoState.PURCHASE_SUCCESS,
            PokemonGoState.PURCHASE_FAILED, PokemonGoState.SETTINGS,
            PokemonGoState.LOGOUT_CONFIRM, PokemonGoState.LOGGED_OUT,
            PokemonGoState.ACCOUNT_FINISHED,
        )

    @property
    def is_external_web_state(self) -> bool:
        """外部上下文(浏览器网页 / Google Play 支付页)"""
        return self in (
            PokemonGoState.PTC_REDIRECTING, PokemonGoState.PTC_LOGIN_PAGE,
            PokemonGoState.PTC_LOGIN_SUBMITTING, PokemonGoState.PTC_LOGIN_ERROR,
            PokemonGoState.AUTHORIZING,
            PokemonGoState.PURCHASE_PAGE, PokemonGoState.PURCHASE_PROCESSING,
        )


class PgoLoginResult(str, Enum):
    """PTC 网页登录结果分类"""
    SUCCESS = "SUCCESS"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"   # 用户名/密码错误 — 不无限重试
    NETWORK_ERROR = "NETWORK_ERROR"
    WEB_ERROR = "WEB_ERROR"                        # 网页白屏/加载失败
    TIMEOUT = "TIMEOUT"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class PurchaseMode(str, Enum):
    """购买执行模式(安全护栏)"""
    MANUAL = "manual"        # 默认: 到支付页暂停, 人工完成, 脚本检测结果
    DRY_RUN = "dry_run"      # 只读商品信息, 不进入支付
    SANDBOX = "sandbox"      # 仅测试环境自动执行(需双重开关)
