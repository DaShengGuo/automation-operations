"""
automation/pokemon_go/adapter.py
PokemonGoAdapter — Pokémon GO 业务适配器

完整业务循环(单账号):
  已註冊的玩家 → 点击 → 登录方式页 → 点击寶可夢訓練家中心
  → 系统自动调起该手机默认浏览器(无论品牌)
  → PTC 网页登录(ExternalWebContext, 与浏览器品牌无关)
  → 认证 → 自动返回游戏 → 加载 → 首次流程(存在则处理)
  → MAP → 主菜单 → 商店 → 找 100寶可幣 → Google Play(manual 护栏)
  → 关商店 → 设置 → 登出 → YES → 验证 RETURNING_PLAYER → 下一账号

启动不假设当前页面: detect_current_state() 后按状态继续, 不无脑 force-stop。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from automation.base_game import BaseGameAutomation, LoginResult, TaskOutcome
from automation.pokemon_go.detector import PokemonGoPageDetector
from automation.pokemon_go.logout import LogoutAutomation
from automation.pokemon_go.recovery import PokemonGoRecovery
from automation.pokemon_go.register_recovery import RegisterRecoveryHandler
from automation.pokemon_go.selectors import PokemonGoSelectors
from automation.pokemon_go.shop import ShopAutomation
from automation.pokemon_go.states import PgoLoginResult, PokemonGoState
from automation.pokemon_go.web_context import ExternalWebContext
from models.account import Account
from models.page_state import PageState

logger = logging.getLogger(__name__)


class PokemonGoAdapter(BaseGameAutomation):
    """Pokémon GO 自动化适配器。

    每台设备一个实例(DeviceWorker 线程隔离)。
    """

    # Pokémon GO 包名(基础设施常量, 非浏览器依赖)
    PACKAGE = "com.nianticlabs.pokemongo"
    ACTIVITY = ("com.nianticproject.holoholo.libholoholo.unity"
                ".UnityMainActivity")

    # 通用关闭词(清单外弹窗兜底) — 特异性高的在前: 完整短语先于短词。
    # 英文按小写匹配(OCR 对按钮大小写不稳定)。不含 OK/確定:
    # 那些是「确认」语义, 未知弹窗只点「关闭」语义的词。
    UNKNOWN_CLOSE_WORDS = [
        "稍後再說", "稍后再说", "以後再說", "以后再说",
        "我知道了", "知道了", "稍後", "稍后", "跳過", "跳过",
        "忽略", "取消", "關閉", "关闭",
        "dismiss", "not now", "later", "skip", "close", "cancel",
    ]

    def __init__(self, controller, cfg):
        self.log = logging.getLogger("control.pokemon_go")
        # 游戏包名覆盖(按 game yaml 配置, 默认本类常量)
        if not cfg.game_package:
            cfg.game["package"] = self.PACKAGE
        if not cfg.game_activity:
            cfg.game["activity"] = self.ACTIVITY
        super().__init__(controller, cfg)
        self.sel = PokemonGoSelectors(cfg.game)
        self.detector = PokemonGoPageDetector(controller, self.sel)
        self.web = ExternalWebContext(self.detector, self.sel.ptc,
                                      controller, log=self.log)
        self.shop_auto = ShopAutomation(self)
        self.logout_auto = LogoutAutomation(self)
        self.recovery_auto = PokemonGoRecovery(self)
        # 注册选择页专用恢复(§10): 分级递进, 不含重启
        rcfg = (cfg.game.get("register_recovery") or {})
        self.register_recovery = RegisterRecoveryHandler(
            self,
            max_rounds=int(rcfg.get("max_rounds", 3)),
            click_retries=int(rcfg.get("click_retries", 2)),
            anti_double_click_sec=float(
                rcfg.get("anti_double_click_sec", 3.0)),
            redetect_wait=float(rcfg.get("redetect_wait", 30.0)),
            reenter_wait=float(rcfg.get("reenter_wait", 60.0)),
            settle_wait=float(rcfg.get("settle_wait", 5.0)),
            click_verify_wait=float(rcfg.get("click_verify_wait", 30.0)))
        self._screenshots: list[str] = []
        # 诊断/防连点状态
        self._active_account = ""          # 当前登录账号(脱敏, 供诊断)
        self.last_action = "init"
        self.last_action_ts = time.time()
        self._last_click_ts = 0.0
        self.tracer_cb = None              # Worker 注入(PerformanceTracer.mark)
        # 登出守卫: 仅 PURCHASE_SUCCESS(或任务成功)后才能登出。
        # 防「点中心球误入设置 → 误点登出」导致账号流程被截断。
        self._purchase_ok = False

    # ── 通用接口映射 ──

    def _phase_t0(self):
        """阶段计时起点(login/launch 等流程入口调用, 供检查点日志打相对时间戳)"""
        self._phase_start = time.time()

    def _elapsed(self) -> float:
        return time.time() - getattr(self, "_phase_start", time.time())

    def _checkpoint(self, msg: str):
        """检查点日志(规格十一): [MM:SS] msg — 流程节奏全程可视"""
        sec = int(self._elapsed())
        self.log.info(f"[{sec // 60:02d}:{sec % 60:02d}] {msg}")

    def launch(self) -> bool:
        """智能启动: 已在正确页面时不重启"""
        self._phase_t0()
        state = self.detect_state()
        if state in (PokemonGoState.RETURNING_PLAYER, PokemonGoState.MAP,
                     PokemonGoState.GAME_LOADING,
                     PokemonGoState.LOGIN_PROVIDER):
            self.log.info(f"[launch] 已在 {state.value}, 无需重启")
            return True
        self._checkpoint("启动游戏")
        if self.d.is_app_running():
            # 游戏进程在跑但不在已知页面 → 拉前台(不 force-stop)。
            # 不固定 sleep: wait_for_state 本身就是 0.2s 快速轮询,
            # 进程在前台后第一个 tick 命中即返回(规格: 加载完成立即继续)。
            self.d.app_start(self.package, self.activity)
        else:
            self.d.app_start(self.package, self.activity)
        state = self.detector.wait_for_state(
            [PokemonGoState.GAME_SPLASH, PokemonGoState.RETURNING_PLAYER,
             PokemonGoState.MAP, PokemonGoState.GAME_LOADING],
            timeout=self.cfg.state_timeout("launch"))
        if state != PokemonGoState.UNKNOWN:
            self._checkpoint(f"启动完成, 当前={state.value}")
        else:
            self.log.warning(f"[launch] 启动超时, 当前={state.value}")
        return state != PokemonGoState.UNKNOWN

    def detect_state(self) -> PokemonGoState:
        """当前 Pokémon GO 页面状态(adapter 内部核心)"""
        return self.detector.detect()

    def detect_page(self) -> PageState:
        """映射到通用 PageState(供 Worker 状态机/看板/watchdog 使用)。

        系统弹窗遮挡(密码保存询问/认证失败/退出确认)时映射 POPUP,
        使 Worker 路由到 HANDLE_POPUPS 处理。
        """
        state = self.detect_state()
        try:
            boxes = self.detector.ocr_boxes()
            joined = " ".join(t for t, _ in boxes)
            if ("智能密码" in joined or "自动保存账号密码" in joined
                    or "自動儲存" in joined
                    or ("認證" in joined and "OK" in joined)
                    or (("结束" in joined or "結束" in joined)
                        and "取消" in joined)):
                return PageState.POPUP
        except Exception:
            pass
        mapping = {
            PokemonGoState.UNKNOWN: PageState.UNKNOWN,
            PokemonGoState.GAME_SPLASH: PageState.SPLASH,
            PokemonGoState.RETURNING_PLAYER: PageState.LOGIN,
            PokemonGoState.LOGIN_FAILED_DIALOG: PageState.LOGIN,
            PokemonGoState.LOGIN_PROVIDER: PageState.LOGIN,
            PokemonGoState.PTC_REDIRECTING: PageState.LOGIN_LOADING,
            PokemonGoState.PTC_LOGIN_PAGE: PageState.LOGIN,
            PokemonGoState.PTC_LOGIN_SUBMITTING: PageState.LOGIN_LOADING,
            PokemonGoState.PTC_LOGIN_ERROR: PageState.ERROR,
            PokemonGoState.AUTHORIZING: PageState.LOGIN_LOADING,
            PokemonGoState.RETURNING_TO_GAME: PageState.LOGIN_LOADING,
            PokemonGoState.GAME_LOADING: PageState.LOGIN_LOADING,
            PokemonGoState.WELCOME_PAGE: PageState.POPUP,
            PokemonGoState.PROFESSOR_DIALOG: PageState.POPUP,
            PokemonGoState.INITIAL_PROMPT: PageState.POPUP,
            PokemonGoState.MAP: PageState.HOME,
            PokemonGoState.MAIN_MENU: PageState.HOME,
            PokemonGoState.SHOP: PageState.TASK_PAGE,
            PokemonGoState.SHOP_SEARCHING: PageState.TASK_PAGE,
            PokemonGoState.PRODUCT_FOUND: PageState.TASK_PAGE,
            PokemonGoState.PURCHASE_PAGE: PageState.TASK_RUNNING,
            PokemonGoState.PURCHASE_PROCESSING: PageState.TASK_RUNNING,
            PokemonGoState.PURCHASE_SUCCESS: PageState.TASK_SUCCESS,
            PokemonGoState.PURCHASE_FAILED: PageState.ERROR,
            PokemonGoState.SETTINGS: PageState.TASK_PAGE,
            PokemonGoState.LOGOUT_CONFIRM: PageState.LOGOUT,
            PokemonGoState.LOGGED_OUT: PageState.LOGOUT,
            PokemonGoState.ACCOUNT_FINISHED: PageState.TASK_SUCCESS,
            PokemonGoState.RECOVERY: PageState.ERROR,
        }
        return mapping.get(state, PageState.UNKNOWN)

    def wait_home(self, timeout: float = None) -> bool:
        """登录后等待进入主界面: MAP / 首次流程页面(§二, 步级预算 home_wait)。

        智能页面检测等待(规格: 禁止固定几十秒 sleep):
          - 前 3s 快速轮询(0.5s), 页面一出现立即返回;
          - 之后降频(2s)减少 OCR 空转;
          - 每 5s 打一次检查点进度日志([步骤] 第N次检测 已等Xs state=...);
          - MAP 命中后做二次确认(隔 0.8s 再检一次), 两次都 MAP/首次流程页
            才算真正进入 — 防转场动画/黑屏瞬时误判(规格: 多特征评分防误判)。
        循环内主动处理公告弹窗(弹窗遮挡 MAP 时不必等超时进 RECOVERY)。
        单轮超预算: 截图保存 + 记录日志 + 重启APP(暖启动保会话)重新
        进入, 再来一轮; 仍失败返回 False 交 Worker 恢复 — 绝不无限卡住
        (真机: WAIT_HOME 曾干等 38s 无动作)。
        """
        timeout = timeout or self._step_budget("home_wait", 30)
        rounds = max(1, int(self._step_budget("home_rounds", 2)))
        home_states = (PokemonGoState.MAP,
                       PokemonGoState.INITIAL_PROMPT,
                       PokemonGoState.WELCOME_PAGE,
                       PokemonGoState.PROFESSOR_DIALOG)
        for rnd in range(1, rounds + 1):
            t0 = time.time()
            deadline = t0 + timeout
            checks = 0
            last_log_at = -1.0
            while time.time() < deadline:
                self.tick_heartbeat()
                state = self.detector.detect()
                checks += 1
                elapsed = time.time() - t0
                # 检查点进度日志: 首次 + 每 5s 一次(规格: 详细日志可追节奏)
                if checks == 1 or elapsed - last_log_at >= 5.0:
                    self.log.info(f"[步骤] 第{checks}次检测主页 "
                                  f"(已等{elapsed:.0f}s, "
                                  f"state={state.value}, 第{rnd}轮)")
                    last_log_at = elapsed
                if state in home_states:
                    # 二次确认: 防转场动画/黑屏瞬时误判(规格多特征评分)
                    if self._confirm_home(home_states):
                        self.log.info(f"[步骤] 检测主页成功 "
                                      f"(state={state.value}, "
                                      f"第{checks}次检测, 第{rnd}轮)")
                        self._mark_trace("MAP_FOUND")
                        return True
                    self.log.debug(f"[WAIT_HOME] 首次命中 {state.value} "
                                   f"但二次确认失败(转场/动画), 继续等待")
                self.handle_popups()  # 公告/首次弹窗(内部 OCR 走缓存)
                # 前 3s 快速轮询, 之后降频(规格: 加载完成立即继续)
                iv = 0.5 if (deadline - time.time()) > (timeout - 3.0) else 2.0
                time.sleep(iv)
            # 超预算(§五): 截图保存 + 记录日志 + 重启APP 重新进入
            self.capture_keyframe("HOME_TIMEOUT")
            self.log.warning(f"[WAIT_HOME] 等待主页面超时({timeout}s 预算, "
                             f"{checks}次检测) — 截图留档, 重启APP 重新进入 "
                             f"(第{rnd}/{rounds}轮)")
            if rnd < rounds:
                self.launch()   # 智能启动: 已在地图不重启, 未知页拉前台
        return False

    def _confirm_home(self, home_states, gap: float = 0.8) -> bool:
        """主页二次确认(规格: 多特征评分防加载动画/黑屏误判)。

        首次命中主页状态后, 隔 gap 秒再检测一次:
          - 两次都是主页状态 → 真正进入, 返回 True;
          - 第二次变 UNKNOWN/其它 → 视为转场动画/瞬时误判, 返回 False。
        gap 取 0.8s: 转场动画通常 <0.5s, 真正加载完成的页面稳定保持。
        """
        time.sleep(gap)
        self.detector.bust_caches()   # 强制下轮用最新截图重检
        return self.detector.detect() in home_states

    def _wait_popup_gone(self, trigger_words, timeout: float = 2.0,
                         interval: float = 0.4) -> bool:
        """弹窗点击后等消失(规格 2026-08-21: 禁止盲 sleep, 改验证轮询)。

        旧实现点击后 time.sleep(2) 睡满 — 弹窗已消失仍等, 累积延迟。
        现在每 interval 秒重检 OCR, 触发词消失立即返回(True);
        超时仍在返回 False(调用方按需截图留档)。trigger_words 为
        该弹窗的稳定特征词(如 ["查看","天前"]), 消失即视为关闭成功。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.tick_heartbeat()
            self.detector.bust_caches()   # 点击后画面已变, 清 OCR 缓存
            try:
                joined = " ".join(t for t, _ in self.detector.ocr_boxes())
            except Exception:
                return True   # OCR 失败, 保守视为已消失(不卡流程)
            if not any(w in joined for w in trigger_words):
                return True
            time.sleep(interval)
        return False

        time.sleep(gap)
        self.detector.bust_caches()   # 强制下轮用最新截图重检
        return self.detector.detect() in home_states

    def handle_popups(self) -> int:
        """处理弹窗(通用 PopupHandler + 公告弹窗 + 退出确认 + 首次流程)"""
        handled = super().handle_popups()
        boxes = []
        try:
            boxes = self.detector.ocr_boxes()
        except Exception:
            pass
        joined = " ".join(t for t, _ in boxes)
        # 登录后公告弹窗(真机实测 OCR 误差: 歡迎→歡魔, 查看詳情→查看情)
        # 稳定特征: 「查看」按钮 + 「天前」活动时间同现 → 点弹窗外暗区关闭
        if "查看" in joined and "天前" in joined:
            self.log.info("[弹窗] 公告弹窗 — 点击外部区域关闭")
            self.d.click_ratio(0.5, 0.15)
            self._wait_popup_gone(["查看", "天前"])
            handled += 1
        # 游戏退出确认(真机: 「要結束Pokémon GO嗎? OK/取消」) → 点取消
        elif ("结束" in joined or "結束" in joined) and "取消" in joined:
            self.log.info("[弹窗] 退出确认 — 点击取消(留在游戏)")
            self.click_ocr_text(["取消", "Cancel"], timeout=5)
            self._wait_popup_gone(["结束", "結束"])
            handled += 1
        # 小米智能密码管理弹窗(真机: 登录后询问保存账号密码) → 点取消
        # (账号密码不入系统密码库, 业务密码仅存本地 runtime.db)
        elif ("智能密码管理" in joined or "自动保存账号密码" in joined
              or "智能密码" in joined):
            self.log.info("[弹窗] 系统密码保存询问 — 点击取消(不保存)")
            self.click_ocr_text(["取消"], timeout=5)
            self._wait_popup_gone(["智能密码", "自动保存账号密码"])
            handled += 1
        # 週間大挑戰等活动任务弹窗(真机: 選擇小組/等等再說, 模态弹窗
        # 会吞掉精灵球点击, BACK 也关不掉) → 点「等等再說」跳过
        elif "等等再說" in joined or "等等再说" in joined:
            self.log.info("[弹窗] 活动任务弹窗 — 点击「等等再說」跳过")
            self.click_ocr_text(["等等再說", "等等再说"], timeout=5)
            self._wait_popup_gone(["等等再說", "等等再说"])
            handled += 1
        # 首次登录安全提示(真机: 注意周遭環境/請勿進入危險的地區/OK) → 点 OK
        elif "OK" in joined and ("周遭" in joined or "危險" in joined
                                 or "危险" in joined):
            self.log.info("[弹窗] 安全提示 — 点击 OK")
            self.click_ocr_text(["OK"], timeout=5)
            self._wait_popup_gone(["周遭", "危險", "危险"])
            handled += 1
        # 无法登入弹窗(真机: 遊戲記住上次失敗會話 — 無法登入/再試一次/
        # 以其他帳號登入)。注意: 弹窗描述含「中央站」, 只能在本分支
        # 处理, 不能点「再試一次」(会用残留会话的旧凭据重试)
        elif "無法登入" in joined or "无法登入" in joined:
            self.log.info("[弹窗] 无法登入 — 点击「以其他帳號登入」(清残留会话)")
            if not self.click_ocr_text(["其他", "帳號"], timeout=8,
                                       require_all=True):
                self.click_ocr_text(["其他", "账号"], timeout=5,
                                    require_all=True)
            self._wait_popup_gone(["無法登入", "无法登入"])
            handled += 1
        # 游戏认证失败提示(真机: 無法進行認證...OK) → 点 OK 回登录入口
        elif "認證" in joined and "OK" in joined:
            self.log.info("[弹窗] 认证失败提示 — 点击 OK(回登录入口)")
            self.click_ocr_text(["OK"], timeout=5)
            self._wait_popup_gone(["認證", "认证"])
            handled += 1
        if self.detector.detect() in (PokemonGoState.INITIAL_PROMPT,
                                      PokemonGoState.WELCOME_PAGE):
            self.handle_initial_pages()
            handled += 1
        # 清单外兜底: 未知弹窗/意外页面(清单没登记的新弹窗也尝试自动关)
        if self._handle_unknown_popup():
            handled += 1
        return handled

    def _handle_unknown_popup(self) -> bool:
        """通用未知弹窗兜底 — 清单外的新弹窗也尝试自动关闭。

        触发条件: 游戏前台且连续两次判 UNKNOWN(过滤转场渲染的瞬时误判)。
        处理顺序: OCR 通用关闭词点击 → BACK 键兜底 → 截图留档交 RECOVERY。
        防打扰: 10s 冷却 + 同一屏幕指纹只尝试一次, 反复无效不重复折腾,
        由 Worker 的 DETECT_PAGE 超时 → RECOVERY 分级恢复接管。
        """
        if self.detector.is_external_context():
            return False  # 浏览器/Google Play 有自己的流程, 不在这里按 BACK
        now = time.time()
        if now - getattr(self, "_unknown_try_ts", 0.0) < 10.0:
            return False  # 冷却中 — 等待循环每 2s 调用一次, 避免动作轰炸
        if self.detector.detect() != PokemonGoState.UNKNOWN:
            return False
        # 二次确认过滤转场瞬时 UNKNOWN(2026-08-21 速度优化): 旧 sleep(1.5)
        # 太长 — 转场动画通常 <0.5s。改 0.3s + bust_caches 读最新画面,
        # 仍稳定则真 UNKNOWN(走关闭策略)。
        time.sleep(0.3)
        self.detector.bust_caches()
        if self.detector.detect() != PokemonGoState.UNKNOWN:
            return False
        try:
            shot = self.d.screenshot()
            from core.perf import screen_fingerprint
            fp = screen_fingerprint(shot, shrink=4)
        except Exception:
            shot, fp = None, None
        if fp and fp == getattr(self, "_unknown_tried_fp", None):
            return False
        self._unknown_tried_fp = fp
        self._unknown_try_ts = now
        self.log.info("[弹窗] 未知弹窗/意外页面 — 通用关闭策略启动")

        # 1) 通用关闭词(短文本按钮, 排除正文长句; 特异性高的词优先点)
        for _ in range(3):
            self.tick_heartbeat()
            candidates = []
            for text, bbox in self.detector.ocr_boxes(shot):
                t = text.strip()
                if len(t) > 8:
                    continue
                rank = next((i for i, k in enumerate(self.UNKNOWN_CLOSE_WORDS)
                             if k in t.lower()), None)
                if rank is not None:
                    candidates.append((rank, len(t), t, bbox))
            if not candidates:
                break
            candidates.sort(key=lambda c: (c[0], c[1]))
            t, bbox = candidates[0][2], candidates[0][3]
            x = (bbox[0] + bbox[2]) // 2
            y = (bbox[1] + bbox[3]) // 2
            self.log.info(f"[弹窗] 通用关闭: 点击 {t!r} @({x},{y})")
            try:
                self.d.click(x, y)
            except Exception:
                pass
            time.sleep(2)
            if self.detector.detect() != PokemonGoState.UNKNOWN:
                self.log.info("[弹窗] 通用关闭策略成功")
                return True
            shot = None  # 屏幕已变, 重新截图找下一个候选

        # 2) BACK 兜底(部分系统/游戏弹窗按返回即关)。
        #    守卫(§14): 注册页等全屏页面按 BACK=退出游戏, 无弹窗证据不按 —
        #    否则「检测失败→按BACK→游戏退出→watchdog重启」无限循环。
        self.log.info("[弹窗] 通用关闭词无效 — BACK 兜底(带守卫)")
        if not self.press_back_guarded():
            self.capture_keyframe("UNKNOWN_POPUP_NO_BACK")
            self.log.warning("[弹窗] BACK 被守卫拒绝(全屏页面) — 交注册页恢复流程")
            return False
        if self.detector.detect() != PokemonGoState.UNKNOWN:
            self.log.info("[弹窗] BACK 关闭成功")
            return True

        # 3) 无效: 截图留档(新弹窗证据, 事后补进清单), 交 RECOVERY
        self.capture_keyframe("UNKNOWN_POPUP")
        self.log.warning("[弹窗] 通用关闭策略未奏效 — 截图留档, 交 RECOVERY")
        return False

    def recover(self) -> bool:
        """适配器级恢复: L1-L6 逐级尝试"""
        ok, _ = self.recovery_auto.run_until_recovered(1, max_escalation=6)
        return ok

    def restart(self) -> bool:
        return self.recovery_auto._level6()

    def tick_heartbeat(self):
        """心跳回调(Worker 注入) — 长循环内周期刷新 + 停止检查。

        真机 run 实测: 登录/商店长循环阻塞期间心跳停摆, 调度器误判
        WORKER_STALLED 重建 Worker, 当前账号周期被中断 + 白冷却 2 分钟。
        所有长等待循环每轮调用本方法, 告知调度器线程未卡死。

        2026-08-21 停止按钮失效修复: 同时检查 stop_cb(Worker 注入)。
        stop_event 置位 → 抛 WorkerStopRequested(BaseException) 协作式
        中断当前长循环。所有长循环每轮间隔 ≤2s(滑动 0.4s/登录 0.5-2s),
        保证 GUI 点停止后 1 秒内停止手机操作。
        """
        stop_cb = getattr(self, "stop_cb", None)
        if stop_cb and stop_cb():
            from core.stop_error import WorkerStopRequested
            raise WorkerStopRequested("停止指令生效, 中断当前长循环")
        cb = getattr(self, "heartbeat_cb", None)
        if cb:
            cb()

    # ── 登录编排 ──

    def login(self, account: Account) -> LoginResult:
        """完整登录: RETURNING_PLAYER → PTC → 网页登录 → 等游戏返回。

        返回通用 LoginResult(Worker 状态机消费)。
        """
        self._active_account = account.masked()
        self.last_action = "login_start"
        self.last_action_ts = time.time()
        self._phase_t0()   # 登录流程计时起点(规格十一检查点日志)
        self._purchase_ok = False   # 新账号周期: 未购买前禁止登出
        # 已在地图 = 已登录
        if self.detect_state() == PokemonGoState.MAP:
            return LoginResult.ALREADY_LOGGED_IN

        # 0. 无法登入弹窗(残留失败会话) → 先点「以其他帳號登入」
        #    回到登录方式页, 再走正常 PTC 流程。
        #    不能点「再試一次」: 会用残留会话的旧凭据重试, 不可控。
        if self.detect_state() == PokemonGoState.LOGIN_FAILED_DIALOG:
            self.log.info("[登录] 无法登入弹窗 — 点击「以其他帳號登入」")
            ok = self.click_ocr_text(["其他", "帳號"], timeout=10,
                                     require_all=True)
            if not ok:
                ok = self.click_ocr_text(["其他", "账号"], timeout=5,
                                         require_all=True)
            if not ok:
                self.log.warning("[登录] 未能点击以其他帳號登入")
                return LoginResult.UNKNOWN
            time.sleep(2)

        # 1. 已註冊的玩家(§14: 点击失败不直接重启 — 先走注册页分级恢复)
        self._checkpoint("等待注册页面(已註冊/未註冊)")
        if not self.click_returning_player():
            if not self.register_recovery.recover():
                self.log.error("[登录] 注册选择页分级恢复失败, 交由 Worker 预算决定")
                return LoginResult.TIMEOUT
        self._checkpoint("已点击已注册, 等待中央站/登录入口")

        # 2. 登录方式页 → 点寶可夢訓練家中心。
        #    真机观察: 游戏记住上次登录方式(PTC)时, RETURNING_PLAYER 之后
        #    加载完直接自动跳浏览器, 方式页可能不出现 — 两条路径都接受。
        #    ptc_provider 预算 8s(人工 <1s; 规格 §三 0.3/0.5/1/2s 渐进轮询,
        #    页面一出现立即点击, 不固定 sleep)。
        deadline = time.time() + self.sel.timeout("ptc_provider", 8)
        state = PokemonGoState.GAME_LOADING
        # 渐进轮询间隔(规格 §三): 0.3→0.5→1→2s, 中央站人工 <1s 出现
        intervals = [0.3, 0.5, 1.0, 2.0]
        iv_idx = 0
        while time.time() < deadline:
            state = self.detect_state()
            if state == PokemonGoState.LOGIN_PROVIDER:
                break
            if self.detector.is_external_context():
                break  # 已自动跳浏览器
            time.sleep(intervals[min(iv_idx, len(intervals) - 1)])
            iv_idx += 1
        if state == PokemonGoState.LOGIN_PROVIDER:
            self._checkpoint("检测到中央站, 点击寶可夢訓練家中心")
            self._mark_trace("LOGIN_PROVIDER_FOUND")
            if not self.click_ptc_provider(timeout=15):
                return LoginResult.UNKNOWN

        # 3. 系统跳转浏览器(浏览器无关) — ptc_redirect 预算 15s(人工 ~3s)
        if not self.web.wait_leave_game(
                timeout=self.sel.timeout("ptc_redirect", 15)):
            return LoginResult.TIMEOUT

        # 4. 等 PTC 登录页(允许慢加载/白屏) — ptc_page_loading 预算 12s(人工 ~3s)
        if not self.web.wait_ptc_login_page(
                timeout=self.sel.timeout("ptc_page_loading", 12)):
            # 尝试一次网页失败恢复
            if not self.recovery_auto.recover_web_failure():
                return LoginResult.WEB_ERROR
            # 恢复后重试表单
            if not self.web.wait_ptc_login_page(
                    timeout=self.sel.timeout("ptc_page_loading", 12)):
                return LoginResult.WEB_ERROR

        self._checkpoint("账号密码页面就绪")
        self.capture_keyframe("PTC_LOGIN_PAGE")

        # 5. 填用户名
        result = self.web.fill_username(account.account)
        if not result.ok:
            self.log.warning(f"[登录] 用户名填充失败: {result.detail}")
            return LoginResult.UNKNOWN
        self.log.info(f"[登录] 用户名输入成功({result.method})")

        # 6. 填密码
        result = self.web.fill_password(account.password)
        if not result.ok:
            self.log.warning(f"[登录] 密码填充失败: {result.detail}")
            return LoginResult.UNKNOWN
        self.log.info("[登录] 密码输入成功(脱敏)")

        # 7. 提交
        if not self.web.submit_login():
            return LoginResult.UNKNOWN
        self._checkpoint("已提交 Login, 等待游戏资源加载回主页(≥30s 勿误判卡死)")
        self.log.info("[登录] 已提交, 等待认证与游戏返回")

        # 8. 等待返回游戏 — auth_return 预算 50s(人工 ≥30s 资源加载; 规格 §五)
        if not self.web.wait_game_return(
                timeout=self.sel.timeout("auth_return", 50)):
            error = self.web.classify_error()
            if error is not None:
                self.capture_keyframe("PTC_LOGIN_ERROR")
                return self._map_login_result(error)
            self.log.error("[AUTH_RETURN_TIMEOUT] 截图记录")
            self.capture_keyframe("AUTH_RETURN_TIMEOUT")
            return LoginResult.TIMEOUT
        self._checkpoint("登录完成, 已返回游戏")

        return LoginResult.SUCCESS

    @staticmethod
    def _map_login_result(result: PgoLoginResult) -> LoginResult:
        """PgoLoginResult → 通用 LoginResult(Worker 状态机语义)"""
        mapping = {
            PgoLoginResult.SUCCESS: LoginResult.SUCCESS,
            PgoLoginResult.INVALID_CREDENTIALS: LoginResult.ACCOUNT_ERROR,
            PgoLoginResult.NETWORK_ERROR: LoginResult.NETWORK_ERROR,
            PgoLoginResult.WEB_ERROR: LoginResult.NETWORK_ERROR,
            PgoLoginResult.TIMEOUT: LoginResult.TIMEOUT,
            PgoLoginResult.UNKNOWN_ERROR: LoginResult.UNKNOWN,
        }
        return mapping.get(result, LoginResult.UNKNOWN)

    # ── 登录子步骤 ──

    def click_returning_player(self, timeout: float = 30) -> bool:
        """点击「已註冊的玩家」并验证页面前进(§6)。

        成功标准: LOGIN_PROVIDER / GAME_LOADING 出现(或外部上下文 —
        游戏记住上次登录方式直接跳浏览器)。点击本身失败返回 False,
        由调用方(login)转入注册页分级恢复, 不在此重启。
        """
        if self.detect_state() in (PokemonGoState.LOGIN_PROVIDER,
                                   PokemonGoState.GAME_LOADING):
            return True
        # or_states: 页面已在后继状态(人工点过/竞态) → 立即返回, 不白等(§5)
        state = self.detector.wait_for_state(
            [PokemonGoState.REGISTER_SELECT], timeout=timeout,
            or_states=(PokemonGoState.LOGIN_PROVIDER,
                       PokemonGoState.GAME_LOADING))
        if state in (PokemonGoState.LOGIN_PROVIDER,
                     PokemonGoState.GAME_LOADING):
            return True
        if state != PokemonGoState.REGISTER_SELECT:
            self.log.warning(f"[登录] 未出现已註冊的玩家页(当前={state.value})")
            return False
        self.capture_keyframe("RETURNING_PLAYER")
        self._mark_trace("RETURNING_PLAYER_FOUND")
        if not self.click_existing_account():
            self.log.warning("[登录] 未能定位/点击已註冊的玩家")
            return False
        # §6: 点击后必须验证页面变化, 不默认成功
        return self.register_recovery.wait_login_page(timeout=timeout)

    def click_existing_account(self) -> bool:
        """点击「已注册」— 点击阶梯(§11): 定位(OCR 跨块→模板→校准坐标)
        × 传输(u2 click→adb tap)。每次只发一次点击, 返回是否发出。

        防连点(§12): anti_double_click_sec 内同一入口拒绝重复点击,
        连点会让注册页动画/状态异常。页面是否前进由调用方验证(§6)。
        """
        now = time.time()
        if now - self._last_click_ts < \
                self.register_recovery.anti_double_click_sec:
            self.log.debug("[点击] 防连点: 距上次点击不足 3s, 跳过")
            return False
        pos = self._locate_returning_player_button()
        if pos is None:
            return False
        x, y = pos
        self.log.info(f"[点击] 已注册 @({x},{y})")
        if not self._tap(x, y):
            self.log.warning("[点击] 全部点击通道失败(u2/adb)")
            return False
        self._last_click_ts = time.time()
        self.last_action = f"click_existing@({x},{y})"
        self.last_action_ts = time.time()
        self._mark_trace("REGISTER_CLICKED")
        return True

    def _locate_returning_player_button(self):
        """定位「已注册」按钮中心坐标。OCR(跨块)→模板→校准坐标(仅配置)。"""
        box = self.detector.find_text_box(
            self._entry_texts("returning_player") or ["已", "玩家"],
            require_all=True)
        if box is not None:
            return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
        matcher = getattr(self.d, "matcher", None)
        if matcher is not None:
            try:
                shot = self.d.screenshot()
                pos = matcher.find("pgo_returning_player_btn", shot)
                if pos is not None:
                    return pos
            except Exception:
                pass
        # 校准坐标: 仅 operator 在 yaml 显式配置过才用(禁止编造坐标)
        ratio = self.sel.ptc.get("returning_player_ratio")
        if ratio and len(ratio) == 2:
            w = getattr(self.d, "screen_w", 0)
            h = getattr(self.d, "screen_h", 0)
            if w > 0 and h > 0:
                return int(ratio[0] * w), int(ratio[1] * h)
        return None

    def _tap(self, x: int, y: int) -> bool:
        """点击传输阶梯: u2 click → adb input tap(§11)"""
        try:
            self.d.click(x, y)
            return True
        except Exception:
            pass
        adb = getattr(self.d, "adb", None)
        if adb is not None and hasattr(adb, "tap"):
            try:
                adb.tap(self.d.serial, x, y)
                return True
            except Exception:
                pass
        return False

    def _mark_trace(self, event: str):
        """性能事件(§17): Worker 注入 tracer_cb 后写入 PerformanceTracer"""
        cb = getattr(self, "tracer_cb", None)
        if cb:
            try:
                cb(event)
            except Exception:
                pass

    def recover_login_page(self) -> bool:
        """登录页(注册选择/登录方式)专用恢复 — Worker 页面级恢复优先调用。

        不重启、不按 BACK(§14): 注册页 BACK 会退出游戏。
        """
        if self.detect_state() in (PokemonGoState.LOGIN_PROVIDER,
                                   PokemonGoState.GAME_LOADING):
            return True
        return self.register_recovery.recover()

    def back_safe(self) -> bool:
        """BACK 安全守卫(§7/§14): 全屏主页面按 BACK = 退出游戏, 禁止。

        UNKNOWN 页仅当存在弹窗特征(短文本按钮候选)才允许 BACK。
        注册页检测失败(UNKNOWN)且无弹窗特征 → 拒绝 BACK, 交给
        注册页恢复流程, 绝不误退游戏。

        商城滑动保护(规格§九 2026-08-21): shop_auto.scrolling 期间一律
        拒绝 BACK — 滑动中状态可能短暂 UNKNOWN(Ocr 未识别商品文字),
        此时按 BACK 会退出商城(客户「滑几次异常退出商城」疑似根因之一)。
        """
        if getattr(self.shop_auto, "scrolling", False):
            self.log.warning("[BACK守卫] 商城滑动中(scrolling=True) "
                             "禁止 BACK — 防误退商城")
            return False
        state = self.detect_state()
        if state in (PokemonGoState.RETURNING_PLAYER,
                     PokemonGoState.GAME_SPLASH, PokemonGoState.MAP,
                     PokemonGoState.MAIN_MENU, PokemonGoState.SETTINGS,
                     PokemonGoState.SHOP):
            self.log.warning(f"[BACK守卫] 全屏页面 {state.value} 不按 BACK")
            return False
        if state == PokemonGoState.UNKNOWN:
            try:
                boxes = self.detector.ocr_boxes()
            except Exception:
                boxes = []
            has_buttonish = any(0 < len(t.strip()) <= 8
                                for t, _ in boxes)
            if not has_buttonish:
                self.log.warning("[BACK守卫] UNKNOWN 页无弹窗特征, 不按 BACK")
                return False
        return True

    def press_back_guarded(self) -> bool:
        """带守卫的 BACK — 所有恢复路径统一入口"""
        if not self.back_safe():
            return False
        try:
            self.d.press("back")
        except Exception:
            return False
        time.sleep(2)
        return True

    def click_ptc_provider(self, timeout: float = 30) -> bool:
        """点击「寶可夢訓練家中央站」。成功标准: 离开游戏(外部上下文)"""
        state = self.detector.wait_for_state(
            [PokemonGoState.LOGIN_PROVIDER], timeout=timeout)
        if state != PokemonGoState.LOGIN_PROVIDER:
            self.log.warning(f"[登录] 未进入登录方式页(当前={state.value})")
            return False
        self.capture_keyframe("LOGIN_PROVIDER")
        ptc_texts = self._entry_texts("ptc_provider")
        clicked = self.click_ocr_text(ptc_texts, timeout=10)
        if not clicked:
            clicked = self.click_template(
                self.sel.ptc.get("provider_template"), timeout=5)
        if not clicked:
            self.log.warning("[登录] 未找到寶可夢訓練家中央站入口")
            return False
        self.log.info("[登录] 已点击 PTC — 等待系统自动调起浏览器")
        return True

    def _entry_texts(self, key: str) -> list:
        cfg = self.sel.ptc.get("entry_texts", {}) or {}
        return cfg.get(key) or []

    def _step_budget(self, key: str, default: float) -> float:
        """步级看门狗预算(秒) — 从 game yaml budgets 读取(§五)。

        每步超过预算: 截图留档 + 记录日志 + 重启APP(暖启动) +
        重新执行当前步骤。禁止任何无意义几十秒等待。
        """
        try:
            return float((self.cfg.game.get("budgets") or {}).get(
                key, default))
        except Exception:
            return default

    # ── 首次流程(存在则处理) ──

    def handle_initial_pages(self, max_steps: int = 8) -> bool:
        """欢迎页/博士对话/LET'S GO — 存在则处理, 不存在则跳过"""
        for _ in range(max_steps):
            state = self.detect_state()
            if state == PokemonGoState.INITIAL_PROMPT:
                self.log.info("[首次] LET'S GO 提示")
                self.click_ocr_text(["LET'S", "GO"], timeout=8,
                                    require_all=True) or \
                    self.click_ocr_text(["GO"], timeout=5)
                continue
            if state == PokemonGoState.WELCOME_PAGE:
                self.log.info("[首次] 欢迎页")
                self.click_ocr_text(self.sel.initial.get("welcome_texts")
                                    or ["下一步", "OK", "繼續", "继续"],
                                    timeout=8)
                continue
            if state == PokemonGoState.PROFESSOR_DIALOG:
                self.log.info("[首次] 博士对话")
                self.click_ocr_text(self.sel.initial.get("dialog_texts")
                                    or ["繼續", "继续", "下一步", "OK"],
                                    timeout=8)
                continue
            return True  # 无首次流程页面 → 正常
        self.log.warning("[首次] 达到最大处理步数")
        return True

    # ── 任务(商店+购买) ──

    def execute_task(self, account: Account) -> TaskOutcome:
        """商店任务: 主菜单 → 商店 → 找 100寶可幣 → 购买(mode) → 关商店"""
        self._phase_t0()   # 任务流程计时起点(规格十一检查点日志)
        try:
            # 先清弹窗: 模态弹窗(週間大挑戰等)会吞掉主菜单的精灵球点击
            self.handle_popups()
            # MAP → 主菜单(步级预算 menu_open)
            self.log.info("[MAP] 点击菜单(精灵球)")
            if self.detect_state() != PokemonGoState.MAIN_MENU:
                if not self.logout_auto.open_main_menu(
                        timeout=self._step_budget("menu_open", 15)):
                    return TaskOutcome(False, "OPEN_MAIN_MENU",
                                       "无法打开主菜单")
            # 商店(步级预算 shop_entry)
            self.log.info("[MENU] 点击商城")
            if not self.shop_auto.enter_shop():
                return TaskOutcome(False, "ENTER_SHOP", "无法进入商店")
            self.log.info("[SHOP] 商城确认成功, 立即开始大幅滑动")
            self.capture_keyframe("SHOP")
            info = self._find_product_with_guards()
            if info is None:
                return TaskOutcome(False, "PRODUCT_NOT_FOUND",
                                   "滚动结束未找到目标商品")
            self._checkpoint(f"找到目标商品: {info.name} ({info.price})")
            self.capture_keyframe("PRODUCT_FOUND")
            # 点击 → Google Play
            if not self.shop_auto.click_product(info):
                return TaskOutcome(False, "OPEN_PURCHASE_PAGE",
                                   "点击商品后未出现 Google Play 页")
            # 商品校验
            if not self.shop_auto.verify_product_on_purchase_page(info):
                self.d.press("back")
                return TaskOutcome(False, "PRODUCT_MISMATCH",
                                   "购买页商品与目标不符")
            # 购买(mode 护栏)
            result = self.shop_auto.handle_purchase(account)
            self.log.info(f"[任务] 购买流程结果: {result}")
            if result == "FAILED":
                self.capture_keyframe("PURCHASE_FAILED")
                return TaskOutcome(False, "PURCHASE", "购买失败")
            if result == "PURCHASE_TIMEOUT":
                # 结果未确认: 不自动重试购买(避免重复扣款),
                # 记录证据交人工核查商城余额
                self.log.warning("[任务] 购买结果未确认(超时) — 不重试购买,"
                                 "请人工核查商城余额")
                self.capture_keyframe("PURCHASE_UNCONFIRMED")
            if result == "SUCCESS":
                self.capture_keyframe("PURCHASE_SUCCESS")
                self._checkpoint("购买完成")
            # 关商店回地图
            self._checkpoint("关闭商城, 返回主页面")
            self.shop_auto.close_shop()
            state = self.detect_state()
            self._checkpoint(f"主页面恢复: state={state.value}")
            # 任务成功(含 dry_run 只读完成/manual 人工购买完成) →
            # 解锁登出守卫: 只有走到这里才允许 LOGOUT
            self._purchase_ok = True
            return TaskOutcome(True)
        except Exception as e:
            self.log.error(f"[任务] 异常: {e}", exc_info=True)
            return TaskOutcome(False, "EXCEPTION", str(e))

    def _find_product_with_guards(self):
        """找商品(规格 2026-08-21 §七: 删除自动重新进入商城)。

        旧实现: 滑动后 detect 到 MAP/主菜单 → 自动重进商城 ≤2 次 —
        真机证实该判断不可靠(商城页红色商品图标误判 MAP), 导致
        商城没退出却反复重进。现改为:
          - 退出判定由 shop._shop_still_open 四条件强证据把关
            (商城特征消失 + 连续两次确认), 滑动中命中才真退出;
          - find_product 返回 None(真退出/未找到) → 直接返回,
            不再自动重进商城(交给上层正常流程处理)。
        """
        return self.shop_auto.find_product()

    def verify_result(self) -> Optional[bool]:
        """任务结果验证: 购买成功标记或安全跳过"""
        mode = str(self.sel.purchase.get("mode", "manual"))
        if mode == "dry_run":
            return None  # 只读模式, 无需验证
        # 检测购买结果状态
        state = self.detect_state()
        if state == PokemonGoState.PURCHASE_SUCCESS:
            return True
        if state == PokemonGoState.PURCHASE_FAILED:
            return False
        # manual 人工取消等场景: 视为流程完成(由 execute_task 结果决定)
        return None

    # ── 退出登录 ──

    def logout(self, force: bool = False) -> bool:
        """设置 → 登出 → YES → 验证 RETURNING_PLAYER

        登出守卫: 非强制登出必须已满足「任务成功」(_purchase_ok)。
        防「点中心球误入设置 → 误点登出」截断账号流程 —
        残留会话登出(_reset_residual_session)走 force=True。
        """
        if not force and not self._purchase_ok:
            self.log.error("[登出守卫] WRONG_LOGOUT_ATTEMPT: 任务未成功"
                           "(purchase_ok=False), 拒绝退出账号")
            self.capture_keyframe("WRONG_LOGOUT_ATTEMPT")
            return False
        return self.logout_auto.run()

    # ── 完整单账号流程 ──

    def run_account(self, account: Account) -> dict:
        """完整业务循环(独立运行/测试用): 登录 → 任务 → 退出。"""
        t0 = time.time()
        result = {"account": account.account, "ok": False,
                  "steps": [], "error": ""}
        login_result = self.login(account)
        result["steps"].append(f"login={login_result.value}")
        if login_result not in (LoginResult.SUCCESS,
                                LoginResult.ALREADY_LOGGED_IN):
            result["error"] = f"login failed: {login_result.value}"
            return result
        state = self.detector.wait_for_state(
            [PokemonGoState.MAP, PokemonGoState.INITIAL_PROMPT,
             PokemonGoState.WELCOME_PAGE, PokemonGoState.PROFESSOR_DIALOG],
            timeout=self.cfg.state_timeout("home"))
        result["steps"].append(f"after_login={state.value}")
        self.handle_initial_pages()
        outcome = self.execute_task(account)
        result["steps"].append(f"task={'ok' if outcome.ok else outcome.failed_step}")
        self.logout()
        result["ok"] = outcome.ok
        result["duration"] = round(time.time() - t0, 1)
        return result

    # ── 通用点击/截图工具 ──

    def click_ocr_text(self, keywords: list, timeout: float = 8,
                       require_all: bool = False,
                       click_offset: tuple = (0, 0)) -> bool:
        """OCR 定位关键词并点击(轮询直到超时)。

        require_all=True  → 所有片段同现(如 [已,玩家] 匹配 已註冊的玩家)
        require_all=False → 任一候选命中(如 [商店,Shop])
        click_offset     → 点击位置偏移(文字标签在上、图标在下时用 (0,+N))
        """
        if not keywords:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            box = self.detector.find_text_box(keywords,
                                              require_all=require_all)
            if box is not None:
                x = (box[0] + box[2]) // 2 + click_offset[0]
                y = (box[1] + box[3]) // 2 + click_offset[1]
                self.d.click(x, y)
                self.log.info(f"[点击] OCR 文本 {keywords} @({x},{y})"
                              f" 偏移{click_offset}")
                return True
            time.sleep(0.5)
        return False

    def click_template(self, name: Optional[str], timeout: float = 5) -> bool:
        """模板匹配点击"""
        if not name or not self.d.matcher:
            return False
        return self.d.matcher.click(name, self.d.click, self.d.screenshot,
                                    timeout=timeout)

    def capture_keyframe(self, tag: str):
        """关键页面自动截图(用于证据/调试)"""
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            day = time.strftime("%Y-%m-%d")
            folder = (self.cfg.screenshots_dir / day /
                      f"device_{self.d.serial}" / "keyframes")
            path = folder / f"{tag}_{ts}.png"
            self.d.save_screenshot(path)
            self._screenshots.append(tag)
            self.log.debug(f"[截图] {tag} → {path.name}")
        except Exception as e:
            self.log.debug(f"[截图] {tag} 失败: {e}")
