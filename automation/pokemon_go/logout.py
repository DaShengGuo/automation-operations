"""
automation/pokemon_go/logout.py
退出登录流程 — 设置 → 登出 → YES 确认 → 验证 RETURNING_PLAYER 重新出现

成功标准不是点击成功, 而是 RETURNING_PLAYER 重新出现。
"""
from __future__ import annotations

import logging
import time

from automation.pokemon_go.states import PokemonGoState

logger = logging.getLogger(__name__)


class LogoutAutomation:
    """退出登录(账号切换前的最后一步)"""

    def __init__(self, adapter):
        self.a = adapter
        self.d = adapter.d
        self.detector = adapter.detector
        self.sel = adapter.sel
        self.logout_cfg = self.sel.logout
        self.log = adapter.log

    # ── 进入设置 ──

    def open_main_menu(self, timeout: float = 20) -> bool:
        """MAP → 点击底部 Poké Ball → MAIN_MENU。成功标准: MAIN_MENU 出现

        规格 2026-08-21 §四: 点击后验证商城入口出现; 失败重试 ≤2 次,
        不等待。模板常因渲染延迟失败 → 快速落比例坐标(0.5,0.94)。
        """
        if self.detector.detect() == PokemonGoState.MAIN_MENU:
            return True
        ball_tpl = self.logout_cfg.get("ball_template")
        per_try = max(2.0, timeout / 2.0) if timeout else 4.0
        for attempt in range(2):   # 规格: 最多 2 次
            self.a.tick_heartbeat()
            # 模板快试 0.5s(渲染延迟常失败), 失败立即比例坐标 — 不浪费 2s
            ok = self.a.click_template(ball_tpl, timeout=0.5)
            if not ok:
                self.d.click_ratio(0.5, 0.94)
            self.detector.bust_caches()
            state = self.detector.wait_for_state(
                [PokemonGoState.MAIN_MENU], timeout=per_try)
            if state == PokemonGoState.MAIN_MENU:
                return True
            self.log.info(f"[退出] 点球后未进主菜单(当前={state.value}) "
                          f"— 重试第 {attempt + 1}/2 次")
        return False

    def go_settings(self, timeout: float = 30) -> bool:
        """MAIN_MENU → 点击設定 → SETTINGS。成功标准: SETTINGS 出现

        真机实测: 主菜单顶部横幅遮挡设定文字, 齿轮图标在右上角 —
        OCR 找不到文字时用齿轮比例坐标兜底。
        """
        if self.detector.detect() == PokemonGoState.SETTINGS:
            return True
        settings_texts = self.logout_cfg.get("settings_texts") or \
            ["設定", "设置", "Settings"]
        clicked = self.a.click_ocr_text(settings_texts, timeout=4)
        if not clicked:
            icon = self.logout_cfg.get("settings_icon_ratio")
            if icon:
                self.d.click_ratio(*icon)
                self.log.info("[退出] 用右上齿轮坐标进入设置")
                clicked = True
        if not clicked:
            self.log.warning("[退出] 未找到設定入口")
            return False
        state = self.detector.wait_for_state([PokemonGoState.SETTINGS],
                                             timeout=timeout)
        return state == PokemonGoState.SETTINGS

    # ── 滚动找登出 ──

    def find_sign_out(self, max_scroll: int = 6) -> bool:
        """滚动直到出现登出按钮(非固定次数)"""
        sign_out_texts = self.logout_cfg.get("signout_texts") or \
            ["登出", "退出登录", "Sign Out", "Sign out"]
        for i in range(max_scroll + 1):
            box = self.detector.find_text_box(sign_out_texts)
            if box is not None:
                self.log.info(f"[退出] 找到登出按钮(滚动 {i} 次)")
                return True
            if i < max_scroll:
                self.d.swipe_direction("up", distance=0.5)
                time.sleep(1.5)
        self.log.warning("[退出] 滚动结束仍未找到登出按钮")
        return False

    def click_sign_out(self) -> bool:
        sign_out_texts = self.logout_cfg.get("signout_texts") or \
            ["登出", "退出登录", "Sign Out", "Sign out"]
        box = self.detector.find_text_box(sign_out_texts)
        if box is None:
            return False
        x = (box[0] + box[2]) // 2
        y = (box[1] + box[3]) // 2
        self.d.click(x, y)
        return True

    # ── 确认与验证 ──

    def confirm_logout(self, timeout: float = 15) -> bool:
        """等待确认弹窗 → 点 YES/是"""
        state = self.detector.wait_for_state([PokemonGoState.LOGOUT_CONFIRM],
                                             timeout=timeout)
        if state != PokemonGoState.LOGOUT_CONFIRM:
            self.log.warning(f"[退出] 未出现确认弹窗(当前={state.value})")
            return False
        yes_texts = self.logout_cfg.get("yes_texts") or ["YES", "Yes", "是"]
        if self.a.click_ocr_text(yes_texts, timeout=8):
            self.log.info("[退出] 已确认退出登录")
            return True
        self.log.warning("[退出] 未找到 YES 确认按钮")
        return False

    def verify_logged_out(self, timeout: float = 60) -> bool:
        """必须验证: RETURNING_PLAYER 重新出现才算退出成功"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.detector.detect()
            if state == PokemonGoState.RETURNING_PLAYER:
                self.log.info("[退出] 已验证: 回到已註冊的玩家页面")
                return True
            time.sleep(2)
        self.log.error("[退出] 验证超时: RETURNING_PLAYER 未出现")
        return False

    def _ensure_on_map(self, max_back: int = 6) -> bool:
        """从任意状态归位到 MAP(退出商店/设置/网页/菜单/外部 app)。

        启动不能假设当前页面 — 用状态检测驱动归位。
        """
        for _ in range(max_back):
            # 外部 app(QQ/桌面/浏览器/Google Play): 直接拉游戏回前台
            pkg = self.d.current_package()
            if pkg and pkg != self.d.package:
                self.d.app_start(self.d.package)
                time.sleep(3)
                continue
            state = self.detector.detect()
            if state == PokemonGoState.MAP:
                return True
            if state == PokemonGoState.MAIN_MENU:
                self.d.click_ratio(0.5, 0.92)  # 点球关闭菜单
                time.sleep(2)
                continue
            self.d.press("back")  # 商店/设置/网页/Google Play 一律 BACK
            time.sleep(1.5)
        return self.detector.detect() == PokemonGoState.MAP

    def run(self, timeout: float = 150) -> bool:
        """完整退出流程(自动从任意状态归位到 MAP 开始)"""
        if not self._ensure_on_map():
            self.log.warning("[退出] 无法归位到 MAP")
            return False
        if not self.open_main_menu():
            return False
        if not self.go_settings():
            return False
        if not self.find_sign_out():
            return False
        self.a.capture_keyframe("SETTINGS")
        if not self.click_sign_out():
            return False
        self.a.capture_keyframe("LOGOUT_CONFIRM")
        if not self.confirm_logout():
            return False
        ok = self.verify_logged_out(timeout=timeout)
        if ok:
            self.a._mark_trace("LOGOUT_SUCCESS")
        return ok
