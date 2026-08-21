"""
tests/test_pokemon_go.py
Pokémon GO 适配器单元测试 — Mock 设备/OCR, 不依赖真机

说明: 本文件是 Mock 单元测试, 不代表真机测试结果。
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from automation.pokemon_go.detector import PokemonGoPageDetector
from automation.pokemon_go.selectors import PokemonGoSelectors
from automation.pokemon_go.states import (PgoLoginResult, PokemonGoState,
                                          PurchaseMode)
from core.config import ControlConfig

PGO_PKG = "com.nianticlabs.pokemongo"


class FakeController:
    """脚本化设备控制器 — 只实现检测器/适配器用到的接口"""

    def __init__(self, pkg=PGO_PKG, xml="", screen=(1080, 2400)):
        self.serial = "FAKE001"
        self.package = PGO_PKG
        self._pkg = pkg
        self._xml = xml
        self.screen_w, self.screen_h = screen
        self.matcher = None
        self.adb = None
        self.cfg = None
        self.device = None
        self._screens = []

    def current_package(self):
        return self._pkg

    def dump_hierarchy(self):
        return self._xml

    def screenshot(self):
        return np.zeros((self.screen_h, self.screen_w, 3), dtype=np.uint8)

    def save_screenshot(self, path):
        self._screens.append(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake")

    def click(self, x, y):
        self._last_click = (x, y)

    def click_ratio(self, rx, ry):
        return self.click(int(rx * self.screen_w), int(ry * self.screen_h))

    def swipe(self, *a, **k):
        pass

    def swipe_direction(self, direction, distance=0.5):
        pass

    def press(self, key):
        pass

    def app_start(self, package="", activity=None):
        self._pkg = self.package

    def app_stop(self, package=""):
        self._pkg = "com.miui.home"

    def is_app_running(self):
        return True

    def reset(self):
        pass

    def restart_app(self):
        return True


@pytest.fixture
def cfg():
    ControlConfig.reset()
    return ControlConfig.load("pokemon_go")


@pytest.fixture
def selectors(cfg):
    return PokemonGoSelectors(cfg.game)


def make_detector(controller, selectors, monkeypatch, ocr_texts=None):
    """构造检测器并 mock OCR"""
    det = PokemonGoPageDetector(controller, selectors, ocr_cache_sec=0)
    if ocr_texts is not None:
        boxes = [(t, (10, 10, 100, 40)) for t in ocr_texts]
        monkeypatch.setattr("core.ocr.ocr_with_boxes",
                            lambda img, min_conf=0.5: boxes)
    else:
        monkeypatch.setattr("core.ocr.ocr_with_boxes",
                            lambda img, min_conf=0.5: [])
    return det


class TestPokemonGoStates:

    def test_state_groups(self):
        assert PokemonGoState.MAP.is_game_foreground_state
        assert PokemonGoState.PTC_LOGIN_PAGE.is_external_web_state
        assert not PokemonGoState.PTC_LOGIN_PAGE.is_game_foreground_state
        assert PokemonGoState.PURCHASE_PAGE.is_external_web_state

    def test_login_result_values(self):
        assert PgoLoginResult.INVALID_CREDENTIALS.value == \
            "INVALID_CREDENTIALS"
        assert set(PgoLoginResult) >= {
            PgoLoginResult.SUCCESS, PgoLoginResult.INVALID_CREDENTIALS,
            PgoLoginResult.NETWORK_ERROR, PgoLoginResult.WEB_ERROR,
            PgoLoginResult.TIMEOUT, PgoLoginResult.UNKNOWN_ERROR}

    def test_purchase_modes(self):
        assert PurchaseMode("manual") == PurchaseMode.MANUAL
        assert PurchaseMode("dry_run") == PurchaseMode.DRY_RUN


class TestSelectors:

    def test_parse_rules(self, selectors):
        rule = selectors.rule(PokemonGoState.RETURNING_PLAYER)
        assert rule is not None
        assert any("Returning" in g for g in rule.ocr_rules)
        assert "pgo_returning_player_btn" in rule.templates

    def test_ocr_fragment_matching(self, selectors):
        """OCR 误差容忍: '已册的玩家' 命中 [已, 玩家]"""
        rule = selectors.rule(PokemonGoState.RETURNING_PLAYER)
        assert rule.match_ocr(["已册的玩家"]) >= 1
        assert rule.match_ocr(["Returning Player"]) >= 1
        assert rule.match_ocr(["地图", "随便"]) == 0

    def test_login_failed_dialog_requires_retry_word(self, selectors):
        """回归(run 11): 登录方式页底部有「無法登入嗎?」链接,
        不能用「無法登入」判定弹窗 — 必须用弹窗独有词「再試一次」"""
        rule = selectors.rule(PokemonGoState.LOGIN_FAILED_DIALOG)
        assert rule is not None
        # 真弹窗: 無法登入 + 再試一次 + 以其他帳號登入 → 命中
        assert rule.match_ocr(["無法登入", "再試一次", "以其他帳號登入"]) >= 1
        # 登录方式页: 只有页脚「無法登入嗎?」链接 → 不命中
        assert rule.match_ocr(["無法登入嗎？", "以 Google 登入",
                               "寶可夢訓練家中央站"]) == 0

    def test_unknown_state_rule_none(self, selectors):
        # UNKNOWN 无规则
        assert selectors.rule(PokemonGoState.UNKNOWN) is None

    def test_no_fabricated_browser_config(self, selectors):
        """配置中不允许出现浏览器品牌/包名"""
        import yaml
        from pathlib import Path
        text = (Path(__file__).parent.parent /
                "config/game_pokemon_go.yaml").read_text(encoding="utf-8")
        for banned in ("firefox", "Firefox", "chrome", "Chrome",
                       "browser_package", "preferred_browser"):
            assert banned not in text, f"配置含浏览器硬编码: {banned}"


class TestDetector:

    def test_detect_returning_player_via_ocr(self, cfg, selectors,
                                             monkeypatch):
        ctrl = FakeController()
        det = make_detector(ctrl, selectors, monkeypatch,
                            ocr_texts=["已册的玩家", "尚未册的玩家"])
        assert det.detect() == PokemonGoState.RETURNING_PLAYER

    def test_detect_login_provider_via_ocr(self, cfg, selectors, monkeypatch):
        ctrl = FakeController()
        det = make_detector(ctrl, selectors, monkeypatch,
                            ocr_texts=["以 Google 登入", "寶可夢訓練家中史站",
                                       "NIANTIC KIDS"])
        assert det.detect() == PokemonGoState.LOGIN_PROVIDER

    def test_detect_external_ptc_page_by_content(self, cfg, selectors,
                                                 monkeypatch):
        """外部上下文 + PTC 网页内容特征 → PTC_LOGIN_PAGE(浏览器无关)"""
        xml = ('<node text="Email or username" class="android.widget.'
               'EditText"/>'
               '<node text="Log In" class="android.widget.Button"/>')
        ctrl = FakeController(pkg="com.any.browser", xml=xml)
        det = make_detector(ctrl, selectors, monkeypatch)
        assert det.detect() == PokemonGoState.PTC_LOGIN_PAGE

    def test_detect_external_loading(self, cfg, selectors, monkeypatch):
        """外部上下文无 PTC 特征 → PTC_REDIRECTING(白屏/加载中)"""
        ctrl = FakeController(pkg="com.any.browser", xml="<hierarchy/>")
        det = make_detector(ctrl, selectors, monkeypatch)
        assert det.detect() == PokemonGoState.PTC_REDIRECTING

    def test_detect_google_play_purchase(self, cfg, selectors, monkeypatch):
        xml = ('<node text="Google Play" class="android.widget.TextView"/>'
               '<node text="滑動即可購買" class="android.widget.TextView"/>')
        ctrl = FakeController(pkg="com.android.vending", xml=xml)
        det = make_detector(ctrl, selectors, monkeypatch)
        assert det.detect() == PokemonGoState.PURCHASE_PAGE

    def test_detect_purchase_processing(self, cfg, selectors, monkeypatch):
        xml = '<node text="正在處理" class="android.widget.TextView"/>'
        ctrl = FakeController(pkg="com.android.vending", xml=xml)
        det = make_detector(ctrl, selectors, monkeypatch)
        assert det.detect() == PokemonGoState.PURCHASE_PROCESSING

    def test_detect_browser_chooser(self, cfg, selectors, monkeypatch):
        xml = '<node text="仅此一次" class="android.widget.Button"/>'
        ctrl = FakeController(pkg="android", xml=xml)
        det = make_detector(ctrl, selectors, monkeypatch)
        assert det.detect() == PokemonGoState.PTC_REDIRECTING

    def test_detect_unknown_in_game(self, cfg, selectors, monkeypatch):
        ctrl = FakeController(xml="<hierarchy><node text='Game view'/>"
                                  "</hierarchy>")
        det = make_detector(ctrl, selectors, monkeypatch, ocr_texts=[])
        assert det.detect() == PokemonGoState.UNKNOWN

    def test_detect_main_menu(self, cfg, selectors, monkeypatch):
        ctrl = FakeController()
        det = make_detector(ctrl, selectors, monkeypatch,
                            ocr_texts=["設定", "商店", "對戰", "道具"])
        assert det.detect() == PokemonGoState.MAIN_MENU

    def test_detect_settings(self, cfg, selectors, monkeypatch):
        ctrl = FakeController()
        det = make_detector(ctrl, selectors, monkeypatch,
                            ocr_texts=["登出", "版本", "常見問題"])
        assert det.detect() == PokemonGoState.SETTINGS

    def test_wait_for_state(self, cfg, selectors, monkeypatch):
        ctrl = FakeController()
        det = make_detector(ctrl, selectors, monkeypatch,
                            ocr_texts=["已册的玩家"])
        state = det.wait_for_state([PokemonGoState.RETURNING_PLAYER],
                                   timeout=2, interval=0.2)
        assert state == PokemonGoState.RETURNING_PLAYER


class TestWebContext:

    def _make_web(self, cfg, selectors, monkeypatch, ctrl=None):
        from automation.pokemon_go.web_context import ExternalWebContext
        ctrl = ctrl or FakeController()
        det = make_detector(ctrl, selectors, monkeypatch)
        return ExternalWebContext(det, selectors.ptc, ctrl)

    def test_ptc_page_texts_configured(self, selectors):
        """PTC 网页特征必须来自真实录屏(access.pokemon.com 表单)"""
        assert "Email or username" in selectors.ptc["page_texts"]
        assert "Trouble logging in" in selectors.ptc["page_texts"]

    def test_classify_invalid_credentials(self, cfg, selectors, monkeypatch):
        xml = '<node text="Invalid username or password"/>'
        ctrl = FakeController(pkg="com.any.browser", xml=xml)
        web = self._make_web(cfg, selectors, monkeypatch, ctrl)
        assert web.classify_error() == PgoLoginResult.INVALID_CREDENTIALS

    def test_wait_game_return_ticks_heartbeat(self, cfg, selectors,
                                              monkeypatch):
        """回归: 认证等待期间必须周期刷新心跳 — 真机曾等待 120s 心跳停摆,
        调度器误判 WORKER_STALLED 重建 Worker, 登录重试被打断"""
        ctrl = FakeController(pkg="com.any.browser")   # 始终在浏览器(未返回)
        web = self._make_web(cfg, selectors, monkeypatch, ctrl)
        ticks = []
        web.heartbeat_cb = lambda: ticks.append(time.time())
        assert web.wait_game_return(timeout=1.5) is False
        assert len(ticks) >= 2   # 0.5s 一轮, 1.5s 至少 2 次心跳

    def test_submit_login_dismisses_dialog_before_verify(self, cfg,
                                                          selectors,
                                                          monkeypatch):
        """回归: 提交验证循环检查按钮前必须先关密码保存弹窗 — 弹窗遮住
        按钮时 OCR 找不到 Log/In 会误判「按钮消失=提交已生效」放行
        (真机 5/5: 弹窗在认证期间出现即 75s 死锁超时)。关弹窗后发现按钮
        仍在 → 重定位再点一次"""
        from automation.pokemon_go.web_context import ExternalWebContext
        state = {"dialog": False}
        BTN = (130, 1420)     # Log In 按钮中心
        CANCEL = (430, 2130)  # 取消按钮中心

        class DialogCtrl(FakeController):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self._n = 0
                self.clicks = []

            def screenshot(self):
                img = super().screenshot().copy()
                img[0, 0, 0] = self._n % 255  # 每帧不同 → 缓存失效
                self._n += 1
                return img

            def dump_hierarchy(self):
                return ('<node text="智能密码管理"/>'
                        if state["dialog"] else "")

            def click(self, x, y):
                self.clicks.append((x, y))
                if (x, y) == BTN:          # 提交 → 弹窗立即弹出
                    state["dialog"] = True
                elif (x, y) == CANCEL:     # 取消 → 弹窗关闭
                    state["dialog"] = False

        ctrl = DialogCtrl(pkg="com.any.browser")
        det = make_detector(ctrl, selectors, monkeypatch)

        def ocr(img, min_conf=0.5):
            if state["dialog"]:
                return [("取消", (400, 2100, 460, 2160))]
            return [("Log In", (100, 1400, 160, 1440))]

        monkeypatch.setattr("core.ocr.ocr_with_boxes", ocr)
        monkeypatch.setattr(time, "sleep", lambda *a: None)  # 加速
        web = ExternalWebContext(det, selectors.ptc, ctrl)
        assert web.submit_login() is True
        # 收键盘点击 + 初提 + 取消(关弹窗) + 重提
        assert ctrl.clicks == [(540, 528), BTN, CANCEL, BTN]

    def test_wait_game_return_resubmits_once_after_dialog(self, cfg,
                                                          selectors,
                                                          monkeypatch):
        """回归: 密码弹窗在认证期间出现会压死认证(真机 5/5 死锁超时) —
        关闭弹窗后若表单仍在, 有界重提一次(仅一次, 不轰炸)"""
        from automation.pokemon_go.web_context import ExternalWebContext
        state = {"dialog": True}
        CANCEL = (430, 2130)

        class DialogCtrl(FakeController):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self._n = 0
                self.clicks = []

            def screenshot(self):
                img = super().screenshot().copy()
                img[0, 0, 0] = self._n % 255
                self._n += 1
                return img

            def dump_hierarchy(self):
                return ('<node text="智能密码管理"/>'
                        if state["dialog"] else "")

            def click(self, x, y):
                self.clicks.append((x, y))
                if (x, y) == CANCEL:
                    state["dialog"] = False

        ctrl = DialogCtrl(pkg="com.any.browser")   # 始终在浏览器
        det = make_detector(ctrl, selectors, monkeypatch)

        def ocr(img, min_conf=0.5):
            if state["dialog"]:
                return [("取消", (400, 2100, 460, 2160))]
            return [("Log In", (100, 1400, 160, 1440))]

        monkeypatch.setattr("core.ocr.ocr_with_boxes", ocr)
        web = ExternalWebContext(det, selectors.ptc, ctrl)
        resubs = []
        web.submit_login = lambda timeout=20: resubs.append(1) or True
        assert web.wait_game_return(timeout=2.0) is False
        assert CANCEL in ctrl.clicks      # 弹窗被关
        assert len(resubs) == 1           # 表单仍在 → 恰好重提一次

    def test_classify_network_error(self, cfg, selectors, monkeypatch):
        xml = '<node text="无法访问此网站"/>'
        ctrl = FakeController(pkg="com.any.browser", xml=xml)
        web = self._make_web(cfg, selectors, monkeypatch, ctrl)
        assert web.classify_error() == PgoLoginResult.NETWORK_ERROR

    def test_no_browser_branching(self):
        """web_context 源码不允许浏览器品牌分支"""
        from pathlib import Path
        import inspect
        from automation.pokemon_go import web_context
        src = inspect.getsource(web_context)
        for banned in ("firefox", "Firefox", "chrome", "Chrome", "edge",
                       "Edge", "mozilla", "samsung"):
            assert banned not in src, f"源码含浏览器硬编码: {banned}"


class TestShopLogic:

    def test_amount_equals_exact(self):
        from automation.pokemon_go.shop import ShopAutomation as S
        assert S._amount_equals("100", "100")
        assert S._amount_equals("100寶可幣", "100")
        assert S._amount_equals("1,200", "100") is False
        assert S._amount_equals("14,500", "100") is False
        # 千分位逗号归一化: "1,200" 与 "1200" 为同一数量(正确命中)
        assert S._amount_equals("1,200", "1200") is True

    def test_amount_equals_prefix_guard(self):
        from automation.pokemon_go.shop import ShopAutomation as S
        # "100" 不能匹配 "1000..."
        assert S._amount_equals("1000", "100") is False

    def test_extract_price(self):
        from automation.pokemon_go.shop import ShopAutomation as S
        assert "IDR" in S._extract_price(["100寶可幣", "IDR 5,000"])


class TestAdapter:

    def _make_adapter(self, cfg, monkeypatch, ctrl=None,
                      ocr_texts=None):
        from automation.pokemon_go.adapter import PokemonGoAdapter
        ctrl = ctrl or FakeController()
        ctrl.cfg = cfg
        adapter = PokemonGoAdapter(ctrl, cfg)
        if ocr_texts is not None:
            boxes = [(t, (10, 10, 100, 40)) for t in ocr_texts]
            monkeypatch.setattr("core.ocr.ocr_with_boxes",
                                lambda img, min_conf=0.5: boxes)
        else:
            monkeypatch.setattr("core.ocr.ocr_with_boxes",
                                lambda img, min_conf=0.5: [])
        return adapter

    def test_adapter_creation(self, cfg, monkeypatch):
        adapter = self._make_adapter(cfg, monkeypatch)
        assert adapter.package == PGO_PKG
        assert adapter.sel is not None
        assert adapter.detector is not None

    def test_detect_page_mapping(self, cfg, monkeypatch):
        from models.page_state import PageState
        adapter = self._make_adapter(cfg, monkeypatch,
                                     ocr_texts=["已册的玩家"])
        assert adapter.detect_page() == PageState.LOGIN

    def test_detect_page_map_home(self, cfg, monkeypatch):
        from models.page_state import PageState
        # 真机主菜单独有词(商店页不会出现): 圖鑑/對戰
        adapter = self._make_adapter(cfg, monkeypatch,
                                     ocr_texts=["圖鑑", "對戰"])
        assert adapter.detect_page() == PageState.HOME  # MAIN_MENU → HOME

    def test_login_result_mapping(self):
        from automation.pokemon_go.adapter import PokemonGoAdapter as A
        from automation.base_game import LoginResult
        assert A._map_login_result(PgoLoginResult.INVALID_CREDENTIALS) == \
            LoginResult.ACCOUNT_ERROR
        assert A._map_login_result(PgoLoginResult.NETWORK_ERROR) == \
            LoginResult.NETWORK_ERROR
        assert A._map_login_result(PgoLoginResult.SUCCESS) == \
            LoginResult.SUCCESS

    def test_launch_already_on_returning_player(self, cfg, monkeypatch):
        """已在正确页面时不重启(规格: 启动不能假设当前页面)"""
        adapter = self._make_adapter(cfg, monkeypatch,
                                     ocr_texts=["已册的玩家"])
        assert adapter.launch() is True

    def test_launch_starts_game_when_not_running(self, cfg, monkeypatch):
        ctrl = FakeController(pkg="com.miui.home")
        ctrl.cfg = cfg
        adapter = self._make_adapter(cfg, monkeypatch, ctrl=ctrl,
                                     ocr_texts=["已册的玩家"])
        assert adapter.launch() is True

    def test_click_ocr_text(self, cfg, monkeypatch):
        adapter = self._make_adapter(cfg, monkeypatch,
                                     ocr_texts=["已册的玩家"])
        assert adapter.click_ocr_text(["已", "玩家"], timeout=2) is True

    def test_recovery_levels(self, cfg, monkeypatch):
        adapter = self._make_adapter(cfg, monkeypatch)
        rec = adapter.recovery_auto
        # L8/L9 返回 False(交由 Worker)
        assert rec.execute(8) is False
        assert rec.execute(9) is False
        # 非法等级 False
        assert rec.execute(0) is False
        assert rec.execute(99) is False

    def test_shop_enter_fails_without_shop_entry(self, cfg, monkeypatch):
        """无商店入口文本 OCR → 进店失败(不伪造成功)"""
        adapter = self._make_adapter(cfg, monkeypatch, ocr_texts=[])
        assert adapter.shop_auto.enter_shop(timeout=3) is False

    # ── 通用未知弹窗兜底 ──

    def _make_popup_ctrl(self, monkeypatch, ocr):
        """弹出式 FakeController: 每帧唯一截图(击穿 OCR/状态缓存),
        点击/按键可切换 OCR 场景。"""
        class PopupController(FakeController):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self._n = 0
                self.clicks = []
                self.pressed = []

            def screenshot(self):
                img = super().screenshot().copy()
                img[0, 0, 0] = self._n % 255  # 每帧不同 → 缓存失效
                self._n += 1
                return img

            def click(self, x, y):
                super().click(x, y)
                self.clicks.append((x, y))

            def press(self, key):
                self.pressed.append(key)

        ctrl = PopupController()
        ctrl.cfg = cfg
        monkeypatch.setattr("core.ocr.ocr_with_boxes", ocr)
        return ctrl

    def test_unknown_popup_closed_by_generic_close_word(
            self, cfg, monkeypatch):
        """清单外弹窗: 意外页面 → OCR 通用关闭词点击 → 恢复已知状态"""
        from automation.pokemon_go.adapter import PokemonGoAdapter
        state = {"popup": True}
        ctrl = self._make_popup_ctrl(
            monkeypatch,
            lambda img, min_conf=0.5:
                [("限時活動", (10, 10, 200, 60)),
                 ("取消", (400, 500, 520, 580))] if state["popup"]
                else [("圖鑑", (10, 10, 100, 40))])

        def click(x, y):
            ctrl.clicks.append((x, y))
            state["popup"] = False  # 点掉弹窗后回到已知页面

        ctrl.click = click
        adapter = PokemonGoAdapter(ctrl, cfg)
        assert adapter.detector.detect() == PokemonGoState.UNKNOWN
        assert adapter._handle_unknown_popup() is True
        assert ctrl.clicks == [(460, 540)]  # 「取消」按钮中心
        assert adapter.detector.detect() == PokemonGoState.MAIN_MENU

    def test_unknown_popup_back_fallback(self, cfg, monkeypatch):
        """无按钮候选(纯正文) → BACK 守卫拒绝, 截图留档交 RECOVERY。

        回归: 注册选择页检测失败(UNKNOWN 全屏页面, 无弹窗证据)时
        无脑按 BACK = 退出游戏 → watchdog 判 APP_CRASHED 重启,
        形成无限重启循环(客户实况)。守卫只允许「有弹窗特征」的 BACK。
        """
        from automation.pokemon_go.adapter import PokemonGoAdapter
        state = {"popup": True}
        ctrl = self._make_popup_ctrl(
            monkeypatch,
            lambda img, min_conf=0.5:
                [("奇怪的页面没有按钮", (10, 10, 320, 60))]
                if state["popup"] else [("圖鑑", (10, 10, 100, 40))])

        def press(key):
            ctrl.pressed.append(key)
            state["popup"] = False  # BACK 后弹窗消失(守卫拒绝 → 不触发)

        ctrl.press = press
        adapter = PokemonGoAdapter(ctrl, cfg)
        assert adapter._handle_unknown_popup() is False
        assert ctrl.pressed == [], "无弹窗证据不得按 BACK(防误退游戏)"

    def test_unknown_popup_fails_softly_and_captures(self, cfg, monkeypatch,
                                                     tmp_path):
        """通用策略无效 → 截图留档 + 返回 False(交 RECOVERY);
        冷却期内不重复折腾"""
        from automation.pokemon_go.adapter import PokemonGoAdapter
        ctrl = FakeController()
        ctrl.cfg = cfg
        monkeypatch.setattr("core.ocr.ocr_with_boxes",
                            lambda img, min_conf=0.5:
                                [("未知提示", (10, 10, 120, 50))])
        monkeypatch.setattr(cfg, "screenshots_dir", tmp_path)
        adapter = PokemonGoAdapter(ctrl, cfg)
        assert adapter._handle_unknown_popup() is False
        t0 = time.time()
        assert adapter._handle_unknown_popup() is False  # 冷却中直接返回
        assert time.time() - t0 < 1.0
        assert any("UNKNOWN_POPUP" in s for s in ctrl._screens), \
            "应留档 UNKNOWN_POPUP 截图(新弹窗证据)"

    def test_purchase_default_mode_is_safe(self, selectors):
        """默认购买模式必须是安全模式(manual/dry_run), 绝不默认 sandbox"""
        mode = selectors.purchase.get("mode", "manual")
        assert mode in ("manual", "dry_run"), \
            f"默认购买模式不安全: {mode}"
        assert mode != "sandbox"


class TestQQProvider:
    """QQ 群账号解析单元测试(Mock, 不依赖真机 QQ)"""

    def _parse(self, messages):
        from core.qq_provider import pair_messages
        return pair_messages(messages)

    def test_basic_pairing(self):
        """录屏真实样本: 账号消息 + 密码消息"""
        msgs = ["来单来单", "czt24720", "Aa12345.", "好的收到"]
        pairs = self._parse(msgs)
        assert pairs == [("czt24720", "Aa12345.")]

    def test_multiple_pairs(self):
        msgs = ["czt24720", "Aa12345.", "czt2820", "Bb67890!",
                "管理员: 上号"]
        pairs = self._parse(msgs)
        assert len(pairs) == 2
        assert pairs[1] == ("czt2820", "Bb67890!")

    def test_account_without_password_not_paired(self):
        msgs = ["czt24720", "没有密码"]
        assert self._parse(msgs) == []

    def test_noise_filtered(self):
        """昵称/时间戳/系统消息不误判"""
        msgs = ["星期一 11:58", "Lv100 管理员 来单来单",
                "czt99999", "Pw123456!"]
        pairs = self._parse(msgs)
        assert pairs == [("czt99999", "Pw123456!")]

    def test_is_account_text(self):
        from core.qq_provider import is_account_text
        assert is_account_text("czt24720")
        assert is_account_text("TestAcct123")
        assert not is_account_text("Aa12345.")   # 含特殊字符 → 密码
        assert not is_account_text("123456")     # 数字开头
        assert not is_account_text("Lv100 管理员")

    def test_is_password_text(self):
        from core.qq_provider import is_password_text
        assert is_password_text("Aa12345.")
        assert is_password_text("Test123456")   # 大小写混合
        assert is_password_text("R_k@12344")    # 真机新格式: 含下划线
        assert not is_password_text("czt24720")  # 纯小写字母数字 → 账号
        assert not is_password_text("你好世界")

    def test_password_masked_in_logging(self):
        """日志脱敏约定: 密码不打印(provider 只打账号)"""
        from core.qq_provider import pair_messages
        pairs = pair_messages(["abc12345", "Xx12345."])
        assert len(pairs) == 1
        # 约定: 调用方只记录账号, 密码仅入库
        import inspect
        from core.qq_provider import QQAccountProvider
        src = inspect.getsource(QQAccountProvider.fetch_latest)
        assert "pwd" not in src  # 不打印密码

    def test_fetch_latest_masks_accounts(self):
        """日志脱敏约定: 账号也脱敏(实测日志曾出现完整账号名 TestAcct123)"""
        from core.qq_provider import QQAccountProvider

        class _Log:
            def __init__(self):
                self.records = []

            def info(self, msg):
                self.records.append(msg)

            def warning(self, msg):
                pass

            def debug(self, msg):
                pass

        log = _Log()
        provider = QQAccountProvider.__new__(QQAccountProvider)
        provider.log = log
        provider.open_qq = lambda: True
        provider.enter_group = lambda: True
        provider.read_messages = lambda: ["TestAcct123", "Aa12345."]
        pairs = provider.fetch_latest(max_pairs=1)
        assert pairs == [("TestAcct123", "Aa12345.")]
        for r in log.records:
            assert "TestAcct123" not in r, f"日志泄漏完整账号: {r}"
        assert any("Tes***123" in r for r in log.records), \
            f"日志未按脱敏形态输出: {log.records}"


class _FakeQQEl:
    """模拟 u2 元素: 按页面决定是否存在, 点击后切换页面"""

    def __init__(self, dev, kind, bounds=(0, 0, 0, 0)):
        self.dev = dev
        self.kind = kind
        self.bounds = bounds

    def wait(self, timeout=0):
        return self.dev.has(self.kind)

    def click(self):
        self.dev.actions.append(f"click:{self.kind}")
        if self.kind == "search_entry" and self.dev.page == "message_list":
            self.dev.page = "search"
        elif self.kind == "search_bar" and self.dev.page == "message_list":
            self.dev.page = "search"
        elif self.kind == "result":
            self.dev.page = "chat"


class _FakeU2Device:
    """可调用对象: 支持 device(**sel) 与 device.xpath(...)/send_keys/shell"""

    def __init__(self, dev):
        self.dev = dev

    def __call__(self, **sel):
        return _FakeQQEl(self.dev, self.dev.kind_of(sel))

    def xpath(self, xp):
        self.dev.xp = xp
        return self.dev

    def send_keys(self, text):
        self.dev.actions.append(f"send_keys:{text}")

    def shell(self, args):
        self.dev.actions.append("shell:" + " ".join(args))


class _FakeQQDevice:
    """模拟 QQ 页面流转: 消息列表 → 搜索页 → 聊天页(严格流程验证)

    页面结构按真机新鲜 dump 复刻: 消息列表搜索入口是 desc「搜索」可点击
    节点(id 混淆, 本代是 z7v); 搜索页输入框是 TextView(text=搜索指定内容,
    非 EditText), 输入内容不进 hierarchy; 「取消」是绘制按钮不进 hierarchy;
    聊天页有 send_btn 与聊天页独有 desc。
    """

    PAGES = {
        "message_list": (
            '<node content-desc="搜索" clickable="true" '
            'resource-id="com.tencent.mobileqq:id/z7v" '
            'class="android.view.ViewGroup"/>'
            '<node text="页面设置" class="android.widget.TextView"/>'),
        "search": (
            '<node text="搜索指定内容" '
            'resource-id="com.tencent.mobileqq:id/kbs" '
            'class="android.widget.TextView"/>'
            '<node content-desc="删除" class="android.widget.Button"/>'
            '<node text="1335024554、不口嗨(游戏自动化购买)" '
            'class="android.widget.TextView"/>'),
        "chat": ('<node text="游戏自动化购买" class="android.widget.TextView"/>'
                 '<node resource-id="com.tencent.mobileqq:id/send_btn" '
                 'text="发送" class="android.widget.Button"/>'
                 '<node content-desc="听筒模式" class="android.widget.Button"/>'
                 '<node content-desc="聊天设置" class="android.widget.Button"/>'
                 '<node text="TestAcct123" class="android.widget.TextView"/>'
                 '<node text="Rk@12345" class="android.widget.TextView"/>'),
    }

    def __init__(self, page="message_list"):
        self.page = page
        self.actions = []
        self.xp = ""
        self.device = _FakeU2Device(self)

    # QQAccountProvider 依赖的控制器接口
    def dump_hierarchy(self):
        return self.PAGES[self.page]

    def press(self, key):
        self.actions.append(f"press:{key}")
        if key == "back" and self.page != "message_list":
            self.page = "message_list"

    def kind_of(self, sel):
        if (sel.get("description") == "搜索"
                and sel.get("clickable") is True):
            return "search_entry"
        return "none"

    def has(self, kind):
        if kind == "search_entry":
            return self.page == "message_list"
        return False

    # xpath 结果(经 _FakeU2Device.xpath 转接): 按查询内容区分
    def all(self):
        if ('@text="搜索指定内容"' in self.xp
                or 'resource-id="com.tencent.mobileqq:id/kbs"' in self.xp):
            if self.page == "search":
                return [_FakeQQEl(self, "search_bar",
                                  bounds=(43, 368, 1037, 419))]
            return []
        if self.page == "search":
            return [_FakeQQEl(self, "result")]
        return []


class TestQQNavigation:
    """QQ 取号导航严格流程: 已在聊天页零点击 / 搜索路径不误触"""

    def test_enter_group_zero_click_when_already_in_chat(self):
        """QQ 冷启动恢复窗口=聊天页 → 立即成功, 不做任何点击(三条杠根因)"""
        from core.qq_provider import QQAccountProvider
        dev = _FakeQQDevice(page="chat")
        provider = QQAccountProvider(dev)
        assert provider.enter_group() is True
        assert dev.actions == []  # 零 UI 操作

    def test_enter_group_search_flow_strict_sequence(self):
        """消息列表 → 点搜索入口 → 点输入框 → keyevent 清残留+输入群名
        → 点结果 → 聊天页。中间无多余操作(无 back/无其他点击)。"""
        from core.qq_provider import QQAccountProvider, QQ_CLEAR_KEYEVENTS
        dev = _FakeQQDevice(page="message_list")
        provider = QQAccountProvider(dev)
        assert provider.enter_group() is True
        assert dev.actions == [
            "click:search_entry",
            "click:search_bar",
            "shell:input keyevent " + " ".join(QQ_CLEAR_KEYEVENTS),
            "send_keys:游戏自动化购买",
            "click:result",
        ]

    def test_enter_group_from_search_page_no_entry_click(self):
        """已在搜索页(冷启动恢复) → 不点入口, 直接点输入框清残留输入"""
        from core.qq_provider import QQAccountProvider, QQ_CLEAR_KEYEVENTS
        dev = _FakeQQDevice(page="search")
        provider = QQAccountProvider(dev)
        assert provider.enter_group() is True
        assert dev.actions == [
            "click:search_bar",
            "shell:input keyevent " + " ".join(QQ_CLEAR_KEYEVENTS),
            "send_keys:游戏自动化购买",
            "click:result",
        ]

    def test_is_in_chat_chat_page_true(self):
        from core.qq_provider import QQAccountProvider
        dev = _FakeQQDevice(page="chat")
        assert QQAccountProvider(dev)._is_in_chat() is True

    def test_is_in_chat_settings_page_false(self):
        """群设置页(含「群号」等独有词, 即使有发送按钮也不判为聊天页)"""
        from core.qq_provider import QQAccountProvider

        class SettingsDev(_FakeQQDevice):
            PAGES = {"settings": (
                '<node text="群号" class="android.widget.TextView"/>'
                '<node resource-id="com.tencent.mobileqq:id/send_btn" '
                'text="发送" class="android.widget.Button"/>')}

            def dump_hierarchy(self):
                return self.PAGES["settings"]

        dev = SettingsDev(page="settings")
        assert QQAccountProvider(dev)._is_in_chat() is False

    def test_is_in_chat_search_page_false(self):
        """搜索页特征不足(有「表情」tab/「语音搜索」, 无发送/听筒模式)
        → 不是聊天页(真机误判回归: 搜索页曾被当成聊天页)"""
        from core.qq_provider import QQAccountProvider

        class SearchDev(_FakeQQDevice):
            PAGES = {"search": (
                '<node text="搜索指定内容" '
                'resource-id="com.tencent.mobileqq:id/kbs" '
                'class="android.widget.TextView"/>'
                '<node text="取消" class="android.widget.Button"/>'
                '<node text="表情" class="android.widget.TextView"/>'
                '<node content-desc="语音搜索" '
                'class="android.widget.Button"/>')}

            def dump_hierarchy(self):
                return self.PAGES["search"]

        dev = SearchDev(page="search")
        assert QQAccountProvider(dev)._is_in_chat() is False


class TestShopAmountNormalization:
    """0/o 混淆归一化 + 同列价格配对(第4轮真机失败根因)"""

    def test_amount_o_to_zero(self):
        from automation.pokemon_go.shop import ShopAutomation as S
        # 真机: rapidocr 把 "100 PokeCoins" 识别为 "1ooPokeCoins"
        assert S._amount_equals("1ooPokeCoins", "100")
        assert S._amount_equals("1oo寶可", "100")
        assert S._amount_equals("100", "100")

    def test_amount_still_excludes_neighbors(self):
        from automation.pokemon_go.shop import ShopAutomation as S
        assert S._amount_equals("550", "100") is False
        assert S._amount_equals("1,200", "100") is False
        assert S._amount_equals("1000", "100") is False
        assert S._amount_equals("14,500", "100") is False

    def test_same_column_price_pairing(self):
        """商品横排三列: 价格必须与数量文本 x 轴重叠(同列)"""
        from core.config import ControlConfig
        from core.device_manager import DeviceManager
        from automation.pokemon_go.adapter import PokemonGoAdapter
        ControlConfig.reset()
        cfg = ControlConfig.load("pokemon_go")
        m = DeviceManager(cfg)
        c = m.create_controller("FAKE001")
        c.serial = "FAKE001"
        c.connect = lambda: None
        c.package = "com.nianticlabs.pokemongo"
        a = PokemonGoAdapter(c, cfg)
        # 真机布局: 100(x110-280) 550(x454-622) 横排, 价格在同列下方
        boxes = [
            ("100寶可", (110, 1312, 280, 1352)),
            ("550寶可", (454, 1312, 622, 1352)),
            ("US$0.99", (112, 1450, 274, 1490)),   # 100 同列
            ("US$4.99", (456, 1450, 620, 1490)),   # 550 同列
        ]
        a.detector.ocr_boxes = lambda shot=None: boxes
        info = a.shop_auto._detect_product("100")
        assert info is not None and info.matched
        assert info.price == "US$0.99", f"价格配错: {info.price}"


class TestAutoPurchase:
    """自动购买(点击/滑动双形态识别 + 回到商城=完成)"""

    def _make(self):
        from core.config import ControlConfig
        from automation.pokemon_go.adapter import PokemonGoAdapter
        ControlConfig.reset()
        cfg = ControlConfig.load("pokemon_go")
        c = FakeController()  # 完全离线 mock, 不碰 ADB
        c.cfg = cfg
        a = PokemonGoAdapter(c, cfg)
        return a

    def test_click_buy_button_form(self, monkeypatch):
        """形态1: 点击「购买」按钮 → 回到商城 → SUCCESS"""
        a = self._make()
        clicked = []
        a.d.click = lambda x, y: clicked.append((x, y))
        boxes = [("购买", (496, 2244, 586, 2292)),
                 ("一键购买", (400, 2000, 600, 2100))]
        a.detector.ocr_boxes = lambda shot=None: boxes
        # 结果: 先支付页, 后回到商店
        states = ["PURCHASE_PAGE", "SHOP", "SHOP"]
        import itertools
        a.detector.detect = lambda: \
            __import__("automation.pokemon_go.states",
                       fromlist=["PokemonGoState"]).PokemonGoState(
                states.pop(0))
        result = a.shop_auto._auto_purchase()
        assert result == "SUCCESS"
        assert clicked and clicked[0][0] == 541  # (496+586)//2

    def test_slide_buy_form(self, monkeypatch):
        """形态2: 无按钮 → 滑动购买条 → 回到商城 → SUCCESS"""
        a = self._make()
        swiped = []
        a.d.swipe = lambda x1, y1, x2, y2, duration=0.8: \
            swiped.append((x1, y1, x2, y2))
        boxes = [("滑動即可購買", (100, 2244, 900, 2292))]
        a.detector.ocr_boxes = lambda shot=None: boxes
        states = ["PURCHASE_PAGE", "SHOP"]
        a.detector.detect = lambda: \
            __import__("automation.pokemon_go.states",
                       fromlist=["PokemonGoState"]).PokemonGoState(
                states.pop(0))
        result = a.shop_auto._auto_purchase()
        assert result == "SUCCESS"
        assert swiped and swiped[0][0] < swiped[0][2]  # 向右滑动

    def test_no_button_or_slide(self, monkeypatch):
        """找不到购买入口 → NO_PURCHASE_BUTTON(不盲点)"""
        a = self._make()
        a.detector.ocr_boxes = lambda shot=None: [("其他", (0, 0, 10, 10))]
        result = a.shop_auto._auto_purchase()
        assert result == "NO_PURCHASE_BUTTON"

    def test_result_timeout_no_retry(self, monkeypatch):
        """结果超时 → PURCHASE_TIMEOUT(供上层不重试购买)"""
        a = self._make()
        clicked = []
        a.d.click = lambda x, y: clicked.append((x, y))
        boxes = [("购买", (496, 2244, 586, 2292))]
        a.detector.ocr_boxes = lambda shot=None: boxes
        # 一直停留支付页
        a.detector.detect = lambda: \
            __import__("automation.pokemon_go.states",
                       fromlist=["PokemonGoState"]).PokemonGoState(
                "PURCHASE_PAGE")
        # 缩短等待窗口
        a.shop_auto.purchase_cfg["result_timeout"] = 2
        result = a.shop_auto._auto_purchase()
        assert result == "PURCHASE_TIMEOUT"

    def test_sandbox_requires_authorization(self, monkeypatch):
        """未授权时 sandbox 模式被拦截(BLOCKED), 不执行购买"""
        a = self._make()
        a.shop_auto.purchase_cfg["mode"] = "sandbox"
        a.cfg.system["payment"] = {"dry_run": True}
        result = a.shop_auto.handle_purchase(account=None)
        assert result == "BLOCKED"


class TestShopFastScrollToBottom:
    """回归: 100 寶可幣在列表最底部(用户确认) —
    快速滑底过程中商品一出现立即停止(用户真机投诉"到底了还在滑"),
    判底用容差对比(倒计时每秒跳动, 纯哈希判底永远失效)。"""

    def _make(self):
        from core.config import ControlConfig
        from automation.pokemon_go.adapter import PokemonGoAdapter
        ControlConfig.reset()
        cfg = ControlConfig.load("pokemon_go")
        c = FakeController()
        c.cfg = cfg
        a = PokemonGoAdapter(c, cfg)
        return a

    def _product_ocr(self, a, ocr_calls, appear_at=2):
        def fake_ocr_boxes(shot=None):
            ocr_calls.append(1)
            if len(ocr_calls) >= appear_at:
                return [("100寶可", (110, 1312, 280, 1352)),
                        ("US$0.99", (112, 1450, 274, 1490))]
            return []
        a.detector.ocr_boxes = fake_ocr_boxes

    def test_stops_as_soon_as_product_visible(self):
        """规格(2026-08-21 §五): 第一阶段完整滑 6 次期间禁止识别商品;
        滑完才统一识别。列表滚动(判底不触发) → 滑满 6 次 → 识别命中。"""
        a = self._make()
        swipes = []
        # find_product 用精确坐标上滑(y1>y2), mock swipe 记录每次调用
        a.d.swipe = lambda x1, y1, x2, y2, duration=0.3: \
            swipes.append((x1, y1, x2, y2))
        a.d.swipe_direction = lambda direction, distance=0.5: \
            swipes.append((direction, distance))
        # 截图内容持续变化(列表滚动中, 判底不触发)
        state = {"n": 0}

        def screenshot():
            state["n"] += 1
            # 每帧变化幅度 > 容差阈值 → 判底不触发(列表仍在滚动)
            return np.full((a.d.screen_h, a.d.screen_w, 3),
                           min(state["n"] * 10, 255), dtype=np.uint8)

        a.d.screenshot = screenshot
        ocr_calls = []
        # 商品在第一次统一识别即出现(appear_at=1)。滑动中守卫快检不调
        # _detect_product(规格§五: 滑动期间禁止识别), ocr 只在统一识别时
        # 增长 → 滑动完成后识别命中, 不进第二阶段补滑。
        self._product_ocr(a, ocr_calls, appear_at=1)
        info = a.shop_auto.find_product(max_scroll=8)

        assert info is not None and info.matched
        assert info.price == "US$0.99"
        # 精确坐标上滑: y1>y2(1800→400 ratio)
        ups = [s for s in swipes if isinstance(s, tuple) and len(s) == 4
               and s[1] > s[3]]
        downs = [s for s in swipes if isinstance(s, tuple) and len(s) == 4
                 and s[1] < s[3]]
        # 规格§五: 滑动中不识别商品 → 统一识别在滑动完成后。判底连续2帧
        # 无变化提前停是到底优化(可能少于6), 关键是滑动中不识别、滑完才识别
        assert len(ups) >= 2, f"必须有滑动(实际 {len(ups)} 次)"
        assert len(ups) <= 6, f"商品命中后不进补滑, 上滑 ≤ 第一阶段6次(实际 {len(ups)})"
        assert downs == []

    def test_pin_detection_then_search_at_bottom(self):
        """列表钉底(内容稳定, 仅计时器跳动) → 判底提前停 → 识别命中。
        规格§五: 判底是到底提前停优化(非回滚), 钉底说明已在底部,
        商品在最底 → 判底停止后立即识别到。"""
        a = self._make()
        swipes = []
        a.d.swipe = lambda x1, y1, x2, y2, duration=0.3: \
            swipes.append((x1, y1, x2, y2))
        a.d.swipe_direction = lambda direction, distance=0.5: \
            swipes.append((direction, distance))
        # 截图恒定 → 容差判底触发, 滑动提前结束
        a.d.screenshot = lambda: np.zeros(
            (a.d.screen_h, a.d.screen_w, 3), dtype=np.uint8)
        ocr_calls = []
        # 商品在第一次统一识别即出现(appear_at=1)。钉底判底提前停后,
        # 商品在最底 → 统一识别命中。
        self._product_ocr(a, ocr_calls, appear_at=1)
        info = a.shop_auto.find_product(max_scroll=8)

        assert info is not None and info.matched
        assert info.price == "US$0.99"
        ups = [s for s in swipes if isinstance(s, tuple) and len(s) == 4
               and s[1] > s[3]]
        downs = [s for s in swipes if isinstance(s, tuple) and len(s) == 4
                 and s[1] < s[3]]
        assert ups, "必须有上滑"
        assert len(ups) <= 3, f"判底后不再多滑(实际 {len(ups)} 次上滑)"
        # 判底停止后统一识别(商品在最底, 第 1 次统一识别即命中返回)
        assert len(ocr_calls) >= 1
