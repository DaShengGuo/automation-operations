"""
automation/pokemon_go/recovery.py
9 级恢复机制 — 禁止无限恢复

  L1 重新检测页面
  L2 等待页面完成加载
  L3 处理普通弹窗
  L4 Android BACK
  L5 尝试恢复到 Pokémon GO 前台
  L6 重启 Pokémon GO
  L7 重新初始化 uiautomator2
  L8 账号进入 RETRY(返回 False 交由 Worker 处理)
  L9 设备进入 ERROR(返回 False 交由 Worker 处理)

浏览器失败恢复(与浏览器品牌无关):
  记录外部页面 → BACK → 检查回到游戏/登录方式页 → 重新点 PTC → 重新检测
"""
from __future__ import annotations

import logging
import time

from automation.pokemon_go.states import PokemonGoState

logger = logging.getLogger(__name__)


class PokemonGoRecovery:
    """适配器级恢复(Worker 的 watchdog 负责调度与升级)"""

    def __init__(self, adapter):
        self.a = adapter
        self.d = adapter.d
        self.detector = adapter.detector
        self.log = adapter.log

    # ── 单级恢复 ──

    def execute(self, level: int) -> bool:
        """执行指定等级恢复。L8/L9 返回 False(交由 Worker 处理账号/设备)"""
        if level <= 0 or level > 9:
            return False
        handler = getattr(self, f"_level{level}", None)
        if handler is None:
            return False
        try:
            self.log.info(f"[恢复] Level {level}")
            return bool(handler())
        except Exception as e:
            self.log.warning(f"[恢复] Level {level} 异常: {e}")
            return False

    def _level1(self) -> bool:
        """重新检测"""
        state = self.detector.detect()
        return state != PokemonGoState.UNKNOWN

    def _level2(self) -> bool:
        """等待页面完成加载(最长 30s)"""
        deadline = time.time() + 30
        while time.time() < deadline:
            state = self.detector.detect()
            if state != PokemonGoState.UNKNOWN and \
                    state != PokemonGoState.GAME_LOADING:
                return True
            time.sleep(2)
        return self.detector.detect() != PokemonGoState.UNKNOWN

    def _level3(self) -> bool:
        """处理普通弹窗(适配器级)"""
        return self.a.handle_initial_pages() or True

    def _level4(self) -> bool:
        """BACK"""
        self.d.press("back")
        time.sleep(2)
        return self.detector.detect() != PokemonGoState.UNKNOWN or True

    def _level5(self) -> bool:
        """回到 Pokémon GO 前台"""
        try:
            self.d.app_start(self.d.package)
            time.sleep(3)
            return self.detector.wait_game_foreground(timeout=20)
        except Exception:
            return False

    def _level6(self) -> bool:
        """重启 Pokémon GO(不清数据)"""
        try:
            self.d.app_stop(self.d.package)
            time.sleep(3)
            self.d.app_start(self.d.package)
            state = self.detector.wait_for_state(
                [PokemonGoState.RETURNING_PLAYER, PokemonGoState.MAP,
                 PokemonGoState.GAME_LOADING, PokemonGoState.GAME_SPLASH],
                timeout=60)
            return state != PokemonGoState.UNKNOWN
        except Exception:
            return False

    def _level7(self) -> bool:
        """重新初始化 uiautomator2"""
        try:
            self.d.reset()
            return True
        except Exception:
            return False

    def _level8(self) -> bool:
        """账号 RETRY — 交由 Worker"""
        return False

    def _level9(self) -> bool:
        """设备 ERROR — 交由 Worker"""
        return False

    # ── 浏览器失败恢复(规格 55 节) ──

    def recover_web_failure(self, max_rounds: int = 2) -> bool:
        """PTC 网页失败恢复: BACK → 回游戏 → 重新点 PTC → 重新等登录页。

        全程不重启浏览器、不指定浏览器品牌。
        """
        for round_no in range(1, max_rounds + 1):
            self.log.warning(f"[网页恢复] 第 {round_no} 轮: BACK 退出当前外部页面")
            self.a.capture_keyframe("WEB_RECOVERY")
            self.d.press("back")
            time.sleep(2)
            # 检查是否回到游戏
            if not self.detector.wait_game_foreground(timeout=20):
                self.log.warning("[网页恢复] 未回到游戏前台")
                continue
            # 回到 LOGIN_PROVIDER(点击 PTC 的前提)
            state = self.detector.wait_for_state(
                [PokemonGoState.LOGIN_PROVIDER,
                 PokemonGoState.RETURNING_PLAYER], timeout=30)
            if state == PokemonGoState.RETURNING_PLAYER:
                self.a.click_returning_player()
            state = self.detector.wait_for_state(
                [PokemonGoState.LOGIN_PROVIDER], timeout=30)
            if state != PokemonGoState.LOGIN_PROVIDER:
                self.log.warning("[网页恢复] 未能回到登录方式页")
                continue
            # 重新点击 PTC → 系统重新跳转(浏览器无关)
            if not self.a.click_ptc_provider():
                continue
            self.detector.wait_external_context(timeout=60)
            if self.detector.wait_for_state(
                    [PokemonGoState.PTC_LOGIN_PAGE], timeout=60) == \
                    PokemonGoState.PTC_LOGIN_PAGE:
                self.log.info("[网页恢复] PTC 登录页重新就绪")
                return True
        return False

    def run_until_recovered(self, anomaly_level: int,
                            max_escalation: int = 9) -> tuple[bool, int]:
        """从指定等级逐级升级直到成功或达到上限"""
        level = max(1, anomaly_level)
        while level <= max_escalation:
            if self.execute(level):
                return True, level
            level += 1
            time.sleep(1)
        return False, level - 1
