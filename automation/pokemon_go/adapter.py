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
        self._screenshots: list[str] = []

    # ── 通用接口映射 ──

    def launch(self) -> bool:
        """智能启动: 已在正确页面时不重启"""
        state = self.detect_state()
        if state in (PokemonGoState.RETURNING_PLAYER, PokemonGoState.MAP,
                     PokemonGoState.GAME_LOADING,
                     PokemonGoState.LOGIN_PROVIDER):
            self.log.info(f"[launch] 已在 {state.value}, 无需重启")
            return True
        if self.d.is_app_running():
            # 游戏进程在跑但不在已知页面 → 拉前台(不 force-stop)
            self.d.app_start(self.package, self.activity)
            time.sleep(2)
        else:
            self.d.app_start(self.package, self.activity)
        state = self.detector.wait_for_state(
            [PokemonGoState.GAME_SPLASH, PokemonGoState.RETURNING_PLAYER,
             PokemonGoState.MAP, PokemonGoState.GAME_LOADING],
            timeout=self.cfg.state_timeout("launch"))
        self.log.info(f"[launch] 启动完成, 当前={state.value}")
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

    def wait_home(self, timeout: float) -> bool:
        """登录后等待进入主界面: MAP / 首次流程页面。

        循环内主动处理公告弹窗(弹窗遮挡 MAP 时不必等超时进 RECOVERY)。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.detector.detect()
            if state in (PokemonGoState.MAP, PokemonGoState.INITIAL_PROMPT,
                         PokemonGoState.WELCOME_PAGE,
                         PokemonGoState.PROFESSOR_DIALOG):
                return True
            self.handle_popups()  # 公告/首次弹窗(内部 OCR 走缓存)
            time.sleep(2)
        return False

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
            time.sleep(2)
            handled += 1
        # 游戏退出确认(真机: 「要結束Pokémon GO嗎? OK/取消」) → 点取消
        elif ("结束" in joined or "結束" in joined) and "取消" in joined:
            self.log.info("[弹窗] 退出确认 — 点击取消(留在游戏)")
            self.click_ocr_text(["取消", "Cancel"], timeout=5)
            time.sleep(1.5)
            handled += 1
        # 小米智能密码管理弹窗(真机: 登录后询问保存账号密码) → 点取消
        # (账号密码不入系统密码库, 业务密码仅存本地 runtime.db)
        elif ("智能密码管理" in joined or "自动保存账号密码" in joined
              or "智能密码" in joined):
            self.log.info("[弹窗] 系统密码保存询问 — 点击取消(不保存)")
            self.click_ocr_text(["取消"], timeout=5)
            time.sleep(1.5)
            handled += 1
        # 週間大挑戰等活动任务弹窗(真机: 選擇小組/等等再說, 模态弹窗
        # 会吞掉精灵球点击, BACK 也关不掉) → 点「等等再說」跳过
        elif "等等再說" in joined or "等等再说" in joined:
            self.log.info("[弹窗] 活动任务弹窗 — 点击「等等再說」跳过")
            self.click_ocr_text(["等等再說", "等等再说"], timeout=5)
            time.sleep(2)
            handled += 1
        # 首次登录安全提示(真机: 注意周遭環境/請勿進入危險的地區/OK) → 点 OK
        elif "OK" in joined and ("周遭" in joined or "危險" in joined
                                 or "危险" in joined):
            self.log.info("[弹窗] 安全提示 — 点击 OK")
            self.click_ocr_text(["OK"], timeout=5)
            time.sleep(2)
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
            time.sleep(2)
            handled += 1
        # 游戏认证失败提示(真机: 無法進行認證...OK) → 点 OK 回登录入口
        elif "認證" in joined and "OK" in joined:
            self.log.info("[弹窗] 认证失败提示 — 点击 OK(回登录入口)")
            self.click_ocr_text(["OK"], timeout=5)
            time.sleep(2)
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
        time.sleep(1.5)
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

        # 2) BACK 兜底(部分系统/游戏弹窗按返回即关)
        self.log.info("[弹窗] 通用关闭词无效 — BACK 兜底")
        try:
            self.d.press("back")
        except Exception:
            pass
        time.sleep(2)
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
        """心跳回调(Worker 注入) — 长循环内周期刷新。

        真机 run 实测: 登录/商店长循环阻塞期间心跳停摆, 调度器误判
        WORKER_STALLED 重建 Worker, 当前账号周期被中断 + 白冷却 2 分钟。
        所有长等待循环每轮调用本方法, 告知调度器线程未卡死。
        """
        cb = getattr(self, "heartbeat_cb", None)
        if cb:
            cb()

    # ── 登录编排 ──

    def login(self, account: Account) -> LoginResult:
        """完整登录: RETURNING_PLAYER → PTC → 网页登录 → 等游戏返回。

        返回通用 LoginResult(Worker 状态机消费)。
        """
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

        # 1. 已註冊的玩家
        if not self.click_returning_player():
            return LoginResult.TIMEOUT

        # 2. 登录方式页 → 点寶可夢訓練家中心。
        #    真机观察: 游戏记住上次登录方式(PTC)时, RETURNING_PLAYER 之后
        #    加载完直接自动跳浏览器, 方式页可能不出现 — 两条路径都接受。
        deadline = time.time() + self.sel.timeout("ptc_provider", 60)
        state = PokemonGoState.GAME_LOADING
        while time.time() < deadline:
            state = self.detect_state()
            if state == PokemonGoState.LOGIN_PROVIDER:
                break
            if self.detector.wait_external_context(2):
                break  # 已自动跳浏览器
            time.sleep(2)
        if state == PokemonGoState.LOGIN_PROVIDER:
            if not self.click_ptc_provider(timeout=15):
                return LoginResult.UNKNOWN

        # 3. 系统跳转浏览器(浏览器无关)
        if not self.web.wait_leave_game(
                timeout=self.sel.timeout("ptc_redirect", 60)):
            return LoginResult.TIMEOUT

        # 4. 等 PTC 登录页(允许慢加载/白屏)
        if not self.web.wait_ptc_login_page(
                timeout=self.sel.timeout("ptc_page_loading", 60)):
            # 尝试一次网页失败恢复
            if not self.recovery_auto.recover_web_failure():
                return LoginResult.WEB_ERROR
            # 恢复后重试表单
            if not self.web.wait_ptc_login_page(
                    timeout=self.sel.timeout("ptc_page_loading", 60)):
                return LoginResult.WEB_ERROR

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
        self.log.info("[登录] 已提交, 等待认证与游戏返回")

        # 8. 等待返回游戏
        if not self.web.wait_game_return(
                timeout=self.sel.timeout("auth_return", 120)):
            error = self.web.classify_error()
            if error is not None:
                self.capture_keyframe("PTC_LOGIN_ERROR")
                return self._map_login_result(error)
            self.log.error("[AUTH_RETURN_TIMEOUT] 截图记录")
            self.capture_keyframe("AUTH_RETURN_TIMEOUT")
            return LoginResult.TIMEOUT

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
        """点击「已註冊的玩家」。成功标准: LOGIN_PROVIDER 出现"""
        if self.detect_state() == PokemonGoState.LOGIN_PROVIDER:
            return True
        state = self.detector.wait_for_state(
            [PokemonGoState.RETURNING_PLAYER], timeout=timeout)
        if state != PokemonGoState.RETURNING_PLAYER:
            self.log.warning(f"[登录] 未出现已註冊的玩家页(当前={state.value})")
            return False
        self.capture_keyframe("RETURNING_PLAYER")
        clicked = self.click_ocr_text(
            self._entry_texts("returning_player") or
            ["已", "玩家"], timeout=10, require_all=True)
        if not clicked:
            clicked = self.click_template("pgo_returning_player_btn",
                                          timeout=5)
        if not clicked:
            self.log.warning("[登录] 未能点击已註冊的玩家")
            return False
        state = self.detector.wait_for_state(
            [PokemonGoState.LOGIN_PROVIDER, PokemonGoState.GAME_LOADING],
            timeout=30)
        return state in (PokemonGoState.LOGIN_PROVIDER,
                         PokemonGoState.GAME_LOADING)

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
        try:
            # 先清弹窗: 模态弹窗(週間大挑戰等)会吞掉主菜单的精灵球点击
            self.handle_popups()
            # MAP → 主菜单
            if self.detect_state() != PokemonGoState.MAIN_MENU:
                if not self.logout_auto.open_main_menu():
                    return TaskOutcome(False, "OPEN_MAIN_MENU",
                                       "无法打开主菜单")
            # 商店
            if not self.shop_auto.enter_shop():
                return TaskOutcome(False, "ENTER_SHOP", "无法进入商店")
            self.capture_keyframe("SHOP")
            # 找商品
            info = self.shop_auto.find_product()
            if info is None:
                return TaskOutcome(False, "PRODUCT_NOT_FOUND",
                                   "滚动结束未找到目标商品")
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
            # 关商店回地图
            self.shop_auto.close_shop()
            return TaskOutcome(True)
        except Exception as e:
            self.log.error(f"[任务] 异常: {e}", exc_info=True)
            return TaskOutcome(False, "EXCEPTION", str(e))

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

    def logout(self) -> bool:
        """设置 → 登出 → YES → 验证 RETURNING_PLAYER"""
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
