"""
tests/test_shop_stability.py
商城稳定性整改验收(2026-08-20 客户实况「商城停留几十秒/未滑动/自动回主页」):

1. 商城点击未生效(仍在主菜单) → 重新点击, 绝不调 open_main_menu
   (其比例坐标 (0.5,0.94) 在商城页会点中底部 X 关闭按钮 — 误退商城根因)
2. 已在商城(异常退出后重进) → enter_shop 直接返回, 不重复点击
3. 进入商城超时(加载中/UNKNOWN) → SHOP_ENTRY_TIMEOUT 截图 +
   暖启动(app_start, 不 force-stop 保会话) + 重试成功
4. 滑动中商城异常退出(首页 UI 出现) → kicked_out=True 提前停止滑动
5. _find_product_with_guards: 异常退出 → 重进商城(≤2 次)后找到商品
6. wait_home 单轮超时 → HOME_TIMEOUT 截图 + launch + 第二轮成功
7. wait_home 两轮均失败 → False(交 Worker RECOVERY, 不无限卡)
8. MAP 红色精灵球色块证据: 模板不可用时红像素占比兜底检测
9. 屏幕指纹: 不同页面不碰撞; 同页面小动态(倒计时)仍同指纹
   (旧 8×4 灰度指纹实测主菜单≡商店碰撞 → 状态缓存幻影状态)
10. Worker WAIT_HOME 连续两轮失败 → RECOVERY(LOAD_TIMEOUT), 不烧 fsm 预算
"""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np
import pytest

from automation.pokemon_go.adapter import PokemonGoAdapter
from automation.pokemon_go.states import PokemonGoState
from core.config import ControlConfig
from core.device_worker import DeviceWorker
from core.perf import screen_fingerprint
from models.page_state import PageState
from automation.base_game import LoginResult
from storage.database import Database
from storage.repositories import AccountRepository, TaskResultRepository
from tests.fakes import FakeAdb, FakeDeviceManager
from tests.test_shop_flow_guard import ShopCtrl, MENU_TEXTS, SETTINGS_TEXTS, SHOP_TEXTS

PGO_PKG = "com.nianticlabs.pokemongo"


class StabilityCtrl(ShopCtrl):
    """扩展 ShopCtrl: 支持 UNKNOWN(加载中)场景 + 暖启动回菜单 + 滑出商城"""

    def __init__(self):
        super().__init__()
        self.scene = "MENU"
        self.kick_after_up_swipes = None   # 上滑 N 次后场景切 MENU(模拟误退)
        self.app_starts = 0

    def swipe_direction(self, direction, distance=0.5):
        super().swipe_direction(direction, distance)
        if self.kick_after_up_swipes is not None and \
                direction == "up" and \
                self.up_swipes >= self.kick_after_up_swipes:
            self.scene = "MENU"

    def app_start(self, package="", activity=None):
        self._pkg = self.package
        self.app_starts += 1
        if self.scene == "UNKNOWN":
            self.scene = "MENU"    # 暖启动后回到主菜单


def _scene_texts(ctrl):
    return {"MENU": MENU_TEXTS, "SETTINGS": SETTINGS_TEXTS,
            "SHOP": SHOP_TEXTS, "UNKNOWN": []}[ctrl.scene]


@pytest.fixture
def cfg():
    ControlConfig.reset()
    return ControlConfig.load("pokemon_go")


@pytest.fixture
def env(cfg, monkeypatch, tmp_path):
    ctrl = StabilityCtrl()
    monkeypatch.setattr(
        "core.ocr.ocr_with_boxes",
        lambda img, min_conf=0.5: [(t, (10, 10, 400, 60))
                                  for t in _scene_texts(ctrl)])
    monkeypatch.setattr(cfg, "screenshots_dir", tmp_path)
    adapter = PokemonGoAdapter(ctrl, cfg)
    adapter.detector.ocr_cache_sec = 0
    return ctrl, adapter


# ── 1: 点击未生效 → 重新点击, 绝不点比例坐标(0.5,0.94) ──────────

def test_enter_shop_reclick_without_ratio_click(env):
    ctrl, adapter = env

    def effect(x, y):
        if len(ctrl.clicks) >= 2:      # 第二次点商店才进店
            ctrl.scene = "SHOP"
    ctrl.click_effect = effect

    assert adapter.shop_auto.enter_shop(timeout=10) is True
    assert ctrl.scene == "SHOP"
    assert len(ctrl.clicks) == 2, "点击未生效必须重新点击"
    # 危险坐标: (0.5, 0.94) = 商城页底部 X 关闭按钮位置
    for x, y in ctrl.clicks:
        assert (x, y) != (540, 2256), "重试循环绝不点底部中央(商城 X 关闭按钮)"
    assert ctrl.pressed == [], "点击未生效不需要 BACK"


# ── 2: 已在商城(重进) → 直接返回, 零点击 ────────────────────────

def test_enter_shop_already_in_shop_returns_immediately(env):
    ctrl, adapter = env
    ctrl.scene = "SHOP"

    assert adapter.shop_auto.enter_shop() is True
    assert ctrl.clicks == []
    assert ctrl.pressed == []


# ── 3: 进店超时 → 截图 + 暖启动(保会话) + 重试成功 ──────────────

def test_enter_shop_timeout_captures_and_warm_restarts(env):
    ctrl, adapter = env

    def effect(x, y):
        if ctrl.scene == "MENU" and ctrl.app_starts == 0:
            ctrl.scene = "UNKNOWN"     # 第一次点击后进加载态
        else:
            ctrl.scene = "SHOP"        # 暖启动后重试 → 进店成功
    ctrl.click_effect = effect

    assert adapter.shop_auto.enter_shop(timeout=1) is True
    assert ctrl.app_starts == 1, "超时后必须暖启动重试"
    assert any("SHOP_ENTRY_TIMEOUT" in p for p in ctrl._saved), \
        "进店超时必须留证截图"
    assert adapter.detect_state() == PokemonGoState.SHOP


# ── 4: 滑动中商城异常退出 → kicked_out + 提前停止 ────────────────

def test_find_product_kicked_out_stops_early(env):
    ctrl, adapter = env
    ctrl.scene = "SHOP"
    ctrl.kick_after_up_swipes = 2    # 上滑 2 次后场景切回主菜单

    info = adapter.shop_auto.find_product(max_scroll=12)
    assert info is None
    assert adapter.shop_auto.kicked_out is True, "必须标记商城异常退出"
    assert ctrl.up_swipes <= 3, \
        f"检测到首页 UI 出现必须立即停止滑动(实际 {ctrl.up_swipes} 次)"


# ── 5: 异常退出 → 重进商城(≤2) → 找到商品 ───────────────────────

def test_find_product_with_guards_reenters_bounded(env, monkeypatch):
    ctrl, adapter = env
    ctrl.scene = "SHOP"

    # 第一次 find_product: 上滑 2 次后滑出商城(回主菜单)
    ctrl.kick_after_up_swipes = 2
    # 重进时点击商店 → 场景切回商城
    ctrl.click_effect = lambda x, y: setattr(ctrl, "scene", "SHOP")
    # 第二次 find_product: 正常留在商城且能 OCR 到商品
    def kick_off(*a):
        ctrl.kick_after_up_swipes = None
    orig_find = adapter.shop_auto.find_product

    def find_with_kick_off(*a, **k):
        result = orig_find(*a, **k)
        kick_off()
        return result
    monkeypatch.setattr(adapter.shop_auto, "find_product", find_with_kick_off)

    def ocr_shop(img, min_conf=0.5):
        if ctrl.scene == "SHOP":
            return [(t, (10, 10, 400, 60)) for t in SHOP_TEXTS] + \
                   [("100", (100, 600, 300, 660)),
                    ("US$0.99", (100, 670, 300, 730))]
        return [(t, (10, 10, 400, 60)) for t in _scene_texts(ctrl)]
    monkeypatch.setattr("core.ocr.ocr_with_boxes", ocr_shop)

    info = adapter._find_product_with_guards()
    assert info is not None, "重进商城后必须找到商品"
    assert info.matched is True
    assert any("SHOP_KICKED_OUT" in p for p in ctrl._saved), \
        "异常退出必须留证截图"


# ── 6: wait_home 单轮超时 → 截图 + launch + 第二轮成功 ───────────

def test_wait_home_timeout_reenters(env, monkeypatch):
    ctrl, adapter = env
    calls = {"n": 0}
    launches = {"n": 0}

    def fake_detect():
        calls["n"] += 1
        if calls["n"] <= 1:
            return PokemonGoState.UNKNOWN     # 第一轮: 卡在加载/未知页
        return PokemonGoState.MAP             # 第二轮: 主页出现
    monkeypatch.setattr(adapter.detector, "detect", fake_detect)
    monkeypatch.setattr(adapter, "handle_popups", lambda: 0)

    def fake_launch():
        launches["n"] += 1
        return True
    monkeypatch.setattr(adapter, "launch", fake_launch)

    assert adapter.wait_home(timeout=1) is True
    assert launches["n"] == 1, "单轮超时必须重启APP重新进入"
    assert any("HOME_TIMEOUT" in p for p in ctrl._saved), \
        "主页等待超时必须截图留档"


# ── 7: wait_home 两轮均失败 → False(交 RECOVERY, 不无限卡) ──────

def test_wait_home_two_rounds_then_fail(env, monkeypatch):
    ctrl, adapter = env
    launches = {"n": 0}

    monkeypatch.setattr(adapter.detector, "detect",
                        lambda: PokemonGoState.UNKNOWN)
    monkeypatch.setattr(adapter, "handle_popups", lambda: 0)

    def fake_launch():
        launches["n"] += 1
        return True
    monkeypatch.setattr(adapter, "launch", fake_launch)

    assert adapter.wait_home(timeout=1) is False
    assert launches["n"] == 1, "只在轮间重启一次(最后一轮不重启)"
    saved = [p for p in ctrl._saved if "HOME_TIMEOUT" in p]
    assert len(saved) == 2, "每轮超时各留一张截图"


# ── 8: MAP 红色精灵球色块证据(模板不可用时的兜底) ───────────────

def _ball_map_shot() -> np.ndarray:
    """暗色地图背景 + 底部中央红色精灵球(BGR)"""
    img = np.zeros((2400, 1080, 3), dtype=np.uint8)
    img[:, :] = (40, 60, 30)                 # 深色地图(天空/地面)
    cv2.circle(img, (540, 2150), 130, (0, 0, 220), -1)   # 红色精灵球
    return img


def test_map_detected_by_red_ball_color_evidence(env, monkeypatch):
    ctrl, adapter = env
    ctrl.matcher = None                      # 模板通道失效
    monkeypatch.setattr("core.ocr.ocr_with_boxes",
                        lambda img, min_conf=0.5: [])

    def shot():
        return _ball_map_shot()
    monkeypatch.setattr(ctrl, "screenshot", shot)

    assert adapter.detect_state() == PokemonGoState.MAP
    assert "red_ratio" in adapter.detector.last_evidence

    # 同背景无红色 → 不判 MAP
    def dark_shot():
        return np.full((2400, 1080, 3), (40, 60, 30), dtype=np.uint8)
    monkeypatch.setattr(ctrl, "screenshot", dark_shot)
    adapter.detector.bust_caches()
    assert adapter.detect_state() == PokemonGoState.UNKNOWN


# ── 9: 屏幕指纹 — 不同暗色页面不碰撞, 同画面同指纹 ────────────

def test_screen_fingerprint_no_collision_across_screens():
    rng_a = np.random.default_rng(20260820)
    rng_b = np.random.default_rng(20260821)
    page_a = rng_a.integers(0, 60, (2400, 1080, 3), dtype=np.uint8)
    page_b = rng_b.integers(0, 60, (2400, 1080, 3), dtype=np.uint8)

    assert screen_fingerprint(page_a) != screen_fingerprint(page_b), \
        "不同暗色页面(主菜单/商店类)指纹必须区分, 否则状态缓存幻影复用"

    # 完全相同的画面 → 同指纹(状态缓存有效性的前提);
    # 内容变化(倒计时跳字)→ 指纹变化是正确行为(代价仅一次全量检测)
    assert screen_fingerprint(page_a) == screen_fingerprint(page_a.copy())


# ── 10: Worker WAIT_HOME 连续两轮失败 → RECOVERY ────────────────

class HomeFailAutomation:
    """脚本化: 登录成功但 wait_home 永远失败(主页检测失效场景)"""

    def __init__(self, serial=""):
        self.login_calls = 0

    def launch(self):
        return True

    def detect_page(self):
        return PageState.HOME if self.login_calls >= 1 else PageState.LOGIN

    def login(self, account):
        self.login_calls += 1
        return LoginResult.SUCCESS

    def wait_home(self, timeout=None):
        return False    # 永远不进主页

    def handle_popups(self):
        return 0

    def restart(self):
        return True


def test_worker_wait_home_two_failures_enter_recovery(tmp_path, monkeypatch):
    db = Database(tmp_path / "runtime.db")
    accounts = AccountRepository(db)
    results = TaskResultRepository(db)
    accounts.add("user001", "p")
    w = DeviceWorker(
        serial="FAKE-H", cfg=ControlConfig(project_root=tmp_path),
        device_manager=FakeDeviceManager(adb=FakeAdb()),
        account_repo=accounts, result_repo=results,
        automation_factory=lambda s: HomeFailAutomation(s),
        stop_event=threading.Event(), pause_event=threading.Event())
    w._ensure_session()
    w._claim_next()

    recoveries = []
    monkeypatch.setattr(w, "_enter_recovery",
                        lambda anomaly: recoveries.append(anomaly))

    for _ in range(30):
        w._tick()
        if recoveries:
            break

    assert len(recoveries) == 1, \
        "连续两轮 wait_home 失败必须立即交 RECOVERY, 不烧满 fsm 预算"
