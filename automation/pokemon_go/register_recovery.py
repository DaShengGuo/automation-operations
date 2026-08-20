"""
automation/pokemon_go/register_recovery.py
REGISTER_SELECT(已注册/未注册选择页) 专用恢复 — 五级递进, 不含重启

设计约束(§7/§14):
  - 本 handler 绝不 restart_app — 重启是 Worker 按恢复预算决定的最后手段。
  - 卡死判定(§8): 状态持续时间 + 截图指纹 + UI dump 三条件, 单一超时
    绝不判死(登录页可能真的在慢加载)。
  - 设备隔离(§18): 每台设备一个实例(adapter 持有), 线程内执行。

恢复等级:
  L1 重新检测页面, 等页面稳定出现(默认 30s)
  L2 清缓存 → 重新 dump UI + 截图 → 重新 detect_state()
  L3 重新点击「已注册」(≤click_retries 次, 3s 防连点, 点击阶梯), 每次点击后验证
  L4 暖切回游戏前台重新进入登录流程(不 force-stop)
  全部失败 → 返回 False, 由 Worker 恢复预算决定 restart / 账号 RETRY
"""
from __future__ import annotations

import logging
import time

from automation.pokemon_go.states import PokemonGoState

logger = logging.getLogger(__name__)

# L3/L4 点击后「成功」的合法后继状态(已越过注册页)
_GOAL_STATES = (PokemonGoState.LOGIN_PROVIDER, PokemonGoState.GAME_LOADING)


class RegisterRecoveryHandler:
    """REGISTER_SELECT 异常恢复(不含重启)。"""

    def __init__(self, adapter, max_rounds: int = 3,
                 click_retries: int = 2,
                 anti_double_click_sec: float = 3.0,
                 redetect_wait: float = 30.0,
                 reenter_wait: float = 60.0,
                 settle_wait: float = 5.0,
                 click_verify_wait: float = 30.0):
        self.a = adapter
        self.d = adapter.d
        self.detector = adapter.detector
        self.log = adapter.log
        self.max_rounds = max_rounds
        self.click_retries = click_retries
        self.anti_double_click_sec = anti_double_click_sec
        self.redetect_wait = redetect_wait
        self.reenter_wait = reenter_wait
        self.settle_wait = settle_wait
        self.click_verify_wait = click_verify_wait
        self._rounds = 0
        # 诊断状态跟踪(§1/§8)
        self._seen_state = PokemonGoState.UNKNOWN
        self._seen_since = time.time()
        self._last_fp = ""
        self._fp_since = 0.0

    # ── 基础 ──

    def detect(self) -> PokemonGoState:
        state = self.detector.detect()
        self._note_state(state)
        return state

    def _note_state(self, state: PokemonGoState):
        if state != self._seen_state:
            self._seen_state = state
            self._seen_since = time.time()

    def _note_screen(self):
        """记录屏幕指纹 — 与状态持续时间联合判定真卡死(§8)"""
        try:
            from core.perf import screen_fingerprint
            shot = self.d.screenshot()
            fp = screen_fingerprint(shot, shrink=4)
            if fp != self._last_fp:
                self._last_fp = fp
                self._fp_since = time.time()
        except Exception:
            pass

    def is_stalled(self, stall_sec: float = 120.0) -> bool:
        """真卡死: 状态持续超时 + 屏幕指纹长时间不变 + 无动作成功"""
        state_stuck = (self._seen_state != PokemonGoState.UNKNOWN
                       and time.time() - self._seen_since > stall_sec)
        screen_stuck = (self._last_fp
                        and time.time() - self._fp_since > stall_sec)
        if state_stuck and screen_stuck:
            self.log.error(f"[STALLED][REGISTER_SELECT] 状态 "
                           f"{self._seen_state.value} 持续 "
                           f"{time.time() - self._seen_since:.0f}s 且屏幕 "
                           f"{time.time() - self._fp_since:.0f}s 无变化")
            return True
        return False

    def wait_login_page(self, timeout: float) -> bool:
        """等待进入登录方式页/游戏加载(点击「已注册」后的成功标准, §6)。

        外部上下文(游戏记住上次登录方式直接跳浏览器)同样视为成功。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.a.tick_heartbeat()
            state = self.detect()
            self._note_screen()
            if state in _GOAL_STATES:
                return True
            if self.detector.is_external_context():
                return True
            time.sleep(1)
        return False

    # ── 分级恢复 ──

    def recover(self) -> bool:
        """分级恢复: L1→L2→L3→L4。全部失败返回 False(不重启)。"""
        if self.wait_login_page(timeout=self.settle_wait):
            return True  # 目标已达成(竞态兜底)
        if self._rounds >= self.max_rounds:
            self.capture_debug("RECOVERY_BUDGET_EXCEEDED")
            self.log.error(f"[注册恢复] 超过预算 {self.max_rounds} 轮, 放弃")
            return False
        self._rounds += 1
        self.log.warning(f"[注册恢复] 第 {self._rounds}/{self.max_rounds} 轮")

        # L1: 重新检测, 等页面稳定出现(§7 一级: 等待, 不动作)
        self.capture_debug("L1_REDETECT")
        state = self.detector.wait_for_state(
            [PokemonGoState.REGISTER_SELECT], timeout=self.redetect_wait,
            or_states=_GOAL_STATES)
        if state in _GOAL_STATES:
            self.log.info("[注册恢复] L1: 页面已前进, 无需动作")
            return True
        if state != PokemonGoState.REGISTER_SELECT:
            self.log.warning(f"[注册恢复] L1: 页面不是注册页"
                             f"({state.value}), 交通用恢复")
            return False

        # L2: 清缓存重新 dump/截图/检测(§7 二级)
        self.capture_debug("L2_REDUMP")
        self.detector.bust_caches()
        state = self.detect()
        if state in _GOAL_STATES:
            self.log.info("[注册恢复] L2: 重新检测发现页面已前进")
            return True

        # L3: 重试点击「已注册」(≤2 次, 每次点击后验证, §7 三级)
        if self.retry_click_existing():
            return True

        # L4: 暖切回游戏前台重新进入登录流程(§7 四级, 不 force-stop)
        return self._level4_reenter()

    def retry_click_existing(self) -> bool:
        """L3: 重试点击已注册。每次点击后必须验证页面变化(§6)。"""
        for attempt in range(1, self.click_retries + 1):
            self.log.info(f"[注册恢复] L3: 重试点击已注册 第 {attempt}"
                          f"/{self.click_retries} 次")
            if self.a.click_existing_account():
                if self.wait_login_page(timeout=self.click_verify_wait):
                    self.log.info("[注册恢复] L3: 点击后页面已前进")
                    return True
                self.log.warning("[注册恢复] L3: 点击后页面未变化")
            else:
                self.capture_debug(f"L3_CLICK_DISPATCH_FAILED_{attempt}")
            # 防连点(§12): 同一入口 3s 内不重复点击
            self.a.tick_heartbeat()
            time.sleep(self.anti_double_click_sec)
        return False

    def _level4_reenter(self) -> bool:
        """L4: 暖切回游戏前台重新进入登录流程(不 force-stop, §7 四级)"""
        self.capture_debug("L4_WARM_REENTER")
        try:
            self.d.app_start(self.a.package, self.a.activity)
            time.sleep(3)
        except Exception as e:
            self.log.warning(f"[注册恢复] L4: 切回前台失败: {e}")
            return False
        state = self.detector.wait_for_state(
            [PokemonGoState.REGISTER_SELECT,
             PokemonGoState.LOGIN_PROVIDER, PokemonGoState.GAME_LOADING],
            timeout=self.reenter_wait)
        if state in _GOAL_STATES:
            self.log.info("[注册恢复] L4: 重新进入后页面已前进")
            return True
        if state == PokemonGoState.REGISTER_SELECT:
            return self.retry_click_existing()
        return False

    # ── 诊断(§1) ──

    def capture_debug(self, reason: str) -> dict:
        """卡住诊断: 时间/串号/机型/账号/状态/动作/时长 + 截图 + UI dump。

        记录完整现场(不只「timeout/restart」), 供离线复盘:
          - 截图: keyframes/REGISTER_STUCK_<reason>_<ts>.png
          - UI dump + OCR 文本: 写日志
        """
        now = time.time()
        state = self.detect()
        try:
            model = getattr(getattr(self.d, "device", None), "model", "") \
                or "-"
        except Exception:
            model = "-"
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "serial": getattr(self.d, "serial", "?"),
            "model": model,
            "account": getattr(self.a, "_active_account", "") or "-",
            "state": state.value,
            "state_duration": round(now - self._seen_since, 1),
            "last_action": getattr(self.a, "last_action", "-"),
            "last_action_ago": round(
                now - getattr(self.a, "last_action_ts", now), 1),
            "recovery_round": self._rounds,
            "reason": reason,
        }
        self.log.warning(f"[诊断][REGISTER_SELECT] {entry}")
        try:
            self.a.capture_keyframe(f"REGISTER_STUCK_{reason}")
        except Exception:
            pass
        try:
            xml = self.d.dump_hierarchy()
            self.log.warning(f"[诊断] UI dump({len(xml)}B): {xml[:300]}")
        except Exception as e:
            self.log.warning(f"[诊断] UI dump 失败: {e}")
        try:
            texts = [t for t, _ in self.detector.ocr_boxes()]
            self.log.warning(f"[诊断] OCR 文本: {texts}")
        except Exception:
            pass
        return entry
