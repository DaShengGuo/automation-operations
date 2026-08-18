"""
automation/base_game.py
游戏自动化基类 — 配置驱动 + 可选子类覆写

设计原则:
  - 所有游戏特定信息(包名/页面规则/按钮/步骤)集中在 config/game.yaml
  - 核心复杂流程仍允许子类用 Python 覆写
  - 支付类动作默认 dry_run 拦截
"""
from __future__ import annotations

import logging
import time
from abc import ABC
from enum import Enum
from typing import Optional

from core.actions import ActionExecutor
from core.exceptions import PaymentBlockedError, SelectorNotConfiguredError
from core.popup_handler import PopupHandler
from core.ui_detector import UiDetector
from models.account import Account
from models.page_state import PageState

logger = logging.getLogger(__name__)


class LoginResult(str, Enum):
    """登录流程结果分类"""
    SUCCESS = "SUCCESS"
    ALREADY_LOGGED_IN = "ALREADY_LOGGED_IN"
    WRONG_PASSWORD = "WRONG_PASSWORD"
    ACCOUNT_ERROR = "ACCOUNT_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


class TaskOutcome:
    """execute_task 的结果"""
    def __init__(self, ok: bool, failed_step: str = "", error: str = ""):
        self.ok = ok
        self.failed_step = failed_step
        self.error = error


class BaseGameAutomation(ABC):
    """游戏自动化统一接口。

    子类只需覆写 game-specific 方法；通用流程(页面识别/弹窗/步骤执行)
    由配置驱动，无需重写。
    """

    def __init__(self, controller, cfg):
        self.d = controller
        self.cfg = cfg
        self.package = cfg.game_package
        self.activity = cfg.game_activity
        self.detector = UiDetector(controller, cfg.game_pages,
                                   controller.matcher)
        self.actions = ActionExecutor(controller, self.detector,
                                      controller.matcher)
        self.popups = PopupHandler(self.detector, self.actions,
                                   cfg.game_popups)
        # 支付护栏状态注入
        self.actions.payment_allowed = cfg.payment_allowed
        self.actions.popups = self.popups

    # ── 统一接口 ──

    def launch(self) -> bool:
        """启动游戏并等待离开闪屏/未知页"""
        if self.d.is_app_running():
            self.d.app_stop()
            time.sleep(2)
        self.d.app_start(self.package, self.activity)
        deadline = time.time() + self.cfg.state_timeout("launch")
        while time.time() < deadline:
            page = self.detect_page()
            if page not in (PageState.UNKNOWN, PageState.SPLASH):
                logger.info(f"[launch] 启动完成, 当前页面={page.value}")
                return True
            time.sleep(2)
        logger.warning(f"[launch] 启动超时, 最后页面={self.detect_page().value}")
        return False

    def detect_page(self) -> PageState:
        return self.detector.detect_page()

    def wait_home(self, timeout: float) -> bool:
        """等待进入首页/主界面。Pokémon GO 等适配器可覆写。"""
        return self.detector.wait_page(PageState.HOME.value, timeout=timeout)

    def handle_popups(self) -> int:
        return self.popups.handle()

    def restart(self) -> bool:
        """force-stop → 重启 → 等待进入已知页面"""
        if not self.d.restart_app():
            return False
        deadline = time.time() + self.cfg.state_timeout("launch")
        while time.time() < deadline:
            if self.detect_page() != PageState.UNKNOWN:
                return True
            time.sleep(2)
        return False

    def recover(self) -> bool:
        """回首页(Level 4)：系统 Home 键 → 重新拉起应用 → 等 HOME"""
        self.d.press("home")
        time.sleep(1.5)
        self.d.app_start(self.package, self.activity)
        return self.detector.wait_page(PageState.HOME.value,
                                       timeout=self.cfg.state_timeout("home"))

    # ── 登录 ──

    def login(self, account: Account) -> LoginResult:
        """统一登录接口。选择器来自 config/game.yaml 的 login 段。

        登录前确认页面 → 定位账号框 → 清空 → 输入 → 密码框 → 输入
        → 点击登录 → 等待并分类结果。
        """
        login_cfg = self.cfg.game_login
        if not login_cfg:
            logger.warning("[login] 未配置 login 段，登录跳过")
            return LoginResult.UNKNOWN

        # 1. 已登录检测
        page = self.detect_page()
        if page == PageState.HOME:
            logger.info(f"[login] 检测到已登录(HOME)，账号 {account.masked()} "
                        f"无需登录")
            return LoginResult.ALREADY_LOGGED_IN

        # 2. 未在登录页 → 执行入口动作进入登录页
        if page != PageState.LOGIN:
            for entry in login_cfg.get("entry", []):
                result = self.actions.execute(entry)
                logger.debug(f"[login] entry 动作: {result.detail}")
                time.sleep(1)
            page = self.detect_page()
            if page != PageState.LOGIN:
                logger.warning(f"[login] 无法进入登录页(当前={page.value})")
                return LoginResult.UNKNOWN

        # 3. 填账号
        if not self._fill_input(login_cfg.get("account_input"), account.account):
            return LoginResult.UNKNOWN

        # 4. 填密码
        if not self._fill_input(login_cfg.get("password_input"),
                                account.password):
            logger.warning("[login] 密码框未找到或未配置")
            return LoginResult.UNKNOWN

        # 5. 点击登录
        btn = login_cfg.get("login_button") or {}
        result = self.actions.execute({"action": "click_text", **btn}
                                      if "text" in btn else
                                      {"action": "click_desc", **btn})
        if not result.ok and btn:
            return LoginResult.UNKNOWN
        logger.info(f"[login] 已提交登录 {account.masked()}")

        # 6. 等待并分类结果
        return self._wait_login_result(login_cfg, account)

    def _fill_input(self, selector: Optional[dict], text: str) -> bool:
        """定位输入框 → 点击 → 清空 → 输入"""
        if not selector:
            logger.warning("[login] 输入框选择器未标定(UNKNOWN_SELECTOR)，"
                           "无法填写。请用 uiautomator2 dump 完成标定。")
            raise SelectorNotConfiguredError("login 输入框选择器未配置")
        ok, el = self.detector.find_element(selector, timeout=8)
        if not ok:
            logger.warning(f"[login] 输入框未找到: {selector}")
            return False
        try:
            el.click()
            time.sleep(0.8)
            el.set_text(text)  # set_text 内部先清空
            time.sleep(0.5)
            return True
        except Exception as e:
            logger.warning(f"[login] 输入失败: {e}")
            return False

    def _wait_login_result(self, login_cfg: dict,
                           account: Account) -> LoginResult:
        error_texts = login_cfg.get("error_texts", {})
        deadline = time.time() + self.cfg.state_timeout("login")
        while time.time() < deadline:
            page = self.detect_page()
            if page == PageState.HOME:
                logger.info(f"[login] {account.masked()} 登录成功")
                return LoginResult.SUCCESS
            if page == PageState.NETWORK_ERROR:
                return LoginResult.NETWORK_ERROR
            for kind, texts in error_texts.items():
                for t in texts:
                    ok, _ = self.detector.find_element({"text": t}, timeout=1)
                    if ok:
                        logger.warning(f"[login] {account.masked()} "
                                       f"登录失败: {kind}({t})")
                        return LoginResult(kind.upper())
            time.sleep(2)
        logger.warning(f"[login] {account.masked()} 登录超时")
        return LoginResult.TIMEOUT

    # ── 任务 ──

    def execute_task(self, account: Account) -> TaskOutcome:
        """按 config/game.yaml steps 顺序执行任务步骤"""
        steps = self.cfg.game_steps
        if not steps:
            return TaskOutcome(False, "NO_STEPS",
                               "game.yaml 未配置 steps（游戏任务步骤未标定）")
        for step in steps:
            result = self.actions.execute(step)
            if not result.ok:
                logger.error(f"[task] 步骤失败: {result.detail}")
                return TaskOutcome(False, str(step.get("action", "")),
                                   result.detail)
            logger.info(f"[task] 步骤完成: {result.detail}")
            time.sleep(float(self.cfg.game.get("automation", {})
                             .get("step_interval", 1)))
        return TaskOutcome(True)

    def verify_result(self) -> Optional[bool]:
        """验证任务结果。game.yaml 配置 verify 段则按规则验证；
        未配置返回 None（Worker 记录为跳过验证）。
        """
        verify = self.cfg.game.get("verify")
        if not verify:
            return None
        if "text" in verify and verify["text"]:
            ok, _ = self.detector.find_element({"text": verify["text"]},
                                               timeout=verify.get("timeout", 10))
            return ok
        if "image" in verify and verify["image"]:
            pos = self.d.matcher.wait_for(verify["image"],
                                          self.d.screenshot,
                                          timeout=verify.get("timeout", 10))
            return pos is not None
        if "page" in verify and verify["page"]:
            return self.detector.wait_page(verify["page"],
                                           timeout=verify.get("timeout", 30))
        return None

    # ── 退出登录 ──

    def logout(self) -> bool:
        """退出当前账号。配置 game.yaml logout 段(入口+确认动作)。"""
        logout_cfg = self.cfg.game_logout
        if not logout_cfg:
            logger.info("[logout] 未配置 logout 段，跳过退出登录")
            return True
        for entry in logout_cfg.get("entry", []):
            self.actions.execute(entry)
            time.sleep(1)
        for confirm in logout_cfg.get("confirm", []):
            result = self.actions.execute(confirm)
            if result.ok:
                logger.info("[logout] 已退出登录")
                return True
            time.sleep(1)
        logger.warning("[logout] 退出登录确认失败（可能已退出）")
        return True

    # ── 支付安全 ──

    def confirm_payment(self, product: str = "", amount: str = ""):
        """真实支付确认。默认 dry_run 拦截；仅明确授权时执行。"""
        if not self.cfg.payment_allowed:
            raise PaymentBlockedError(
                f"真实支付被 dry_run 拦截(商品={product}, 金额={amount})。"
                f"默认不允许自动支付。")

    def read_product_info(self, name_text: str = "",
                          price_text: str = "") -> bool:
        """只读商品信息(dry_run 安全)：验证商品页、读名称与金额。"""
        result = self.actions.execute({
            "action": "read_product",
            "name_text": name_text,
            "price_text": price_text,
        })
        if result.ok:
            logger.info(f"[product] {result.detail}")
        return result.ok
