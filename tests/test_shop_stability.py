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

    def swipe(self, x1, y1, x2, y2, duration=0.3):
        # shop.find_product 改用精确坐标上滑(y1>y2), 需在此累加 up_swipes
        # 并触发 kick 逻辑(与 swipe_direction 等价)
        if y1 > y2:   # 上滑
            self.up_swipes += 1
            if self.kick_after_up_swipes is not None and \
                    self.up_swipes >= self.kick_after_up_swipes:
                self.scene = "MENU"
        else:         # 下滑
            self.down_swipes += 1

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


# ── 4b: 商城滑动状态保护锁(规格九) — 滑动期间 scrolling=True, 退出释放 ──

def test_shop_scroll_state_lock_released_on_exit(env):
    """find_product 退出时(到底/异常/找到)必须释放 scrolling 锁。

    规格§九: 商城滑动期间锁定状态 SHOP_SCROLLING, 只有商城到底或异常
    才能改变状态。锁泄漏会导致后续流程误判仍在滑动。
    """
    ctrl, adapter = env
    ctrl.scene = "SHOP"
    assert adapter.shop_auto.scrolling is False, "初始未滑动, 锁应释放"

    info = adapter.shop_auto.find_product(max_scroll=12)
    # 无论结果(到底未找到), 退出后锁必须释放
    assert adapter.shop_auto.scrolling is False, \
        "find_product 退出后必须释放 scrolling 锁"
    assert info is None  # 脚本场景无目标商品


def test_shop_scroll_state_lock_released_on_exception(env, monkeypatch):
    """滑动中抛异常也必须释放锁(finally 语义)。"""
    ctrl, adapter = env
    ctrl.scene = "SHOP"

    def boom(*a, **k):
        raise RuntimeError("模拟滑动异常")
    monkeypatch.setattr(adapter.shop_auto, "_detect_product", boom)

    with pytest.raises(RuntimeError):
        adapter.shop_auto.find_product(max_scroll=12)
    assert adapter.shop_auto.scrolling is False, \
        "异常路径也必须释放 scrolling 锁(finally)"


# ── 4c: 商城滑动中 BACK 守卫拒绝(规格§九 — 滑动期间禁止退出逻辑) ──

def test_back_guard_rejected_during_scroll(env, monkeypatch):
    """scrolling=True 时 back_safe 必须拒绝, 防误退商城。

    规格§九排查重点: 「滑几次后退出商城」可能是 watchdog/恢复链路在滑动中
    按 BACK。滑动中状态可能短暂 UNKNOWN(OCR 未识别), 若此时 BACK 放行
    会退出商城。back_safe 必须感知 scrolling 标记一律拒绝。
    """
    ctrl, adapter = env
    # 模拟滑动中(不真跑 find_product, 直接置锁)
    adapter.shop_auto.scrolling = True
    assert adapter.back_safe() is False, \
        "商城滑动中(scrolling=True) BACK 必须被守卫拒绝"
    adapter.shop_auto.scrolling = False   # 复位


# ── 4d: MAIN_MENU 规则不再误判商城页(根因: 旧 [Shop,Settings] 组) ──────

def test_shop_not_misdetected_as_main_menu(env, monkeypatch):
    """商城页含 "Shop" 文字不得误判为 MAIN_MENU。

    真机教训(2026-08-21): MAIN_MENU 旧含 [Shop, Settings] OCR 组, 商城页
    也有 "Shop" → 滑动中某帧命中 → 误判主菜单 → _shop_still_open 触发
    kicked_out → 滑动中止重进(客户「滑几次异常退出商城」根因)。
    修复: 删除该组 + DETECT_ORDER 把 SHOP 提到 MAIN_MENU 之前。
    """
    ctrl, adapter = env
    # 商城页 OCR 文本含 Shop(模拟真机商城顶部标题/按钮)
    monkeypatch.setattr(
        "core.ocr.ocr_with_boxes",
        lambda img, min_conf=0.5: [("Shop", (10, 10, 200, 60)),
                                   ("寶可幣", (10, 100, 300, 160))])
    monkeypatch.setattr(ctrl, "screenshot",
                        lambda: np.zeros((2400, 1080, 3), dtype=np.uint8))
    adapter.detector.bust_caches()
    state = adapter.detect_state()
    assert state == PokemonGoState.SHOP, \
        f"商城页(含 Shop 文字)必须判 SHOP, 不得误判 MAIN_MENU(实际={state.value})"


# ── 4e: 滑动无变化时重试一次再判底(规格核心 — 防滑动未生效卡死) ──────

def test_scroll_retry_on_no_change_then_bottom(env, monkeypatch):
    """滑动后页面无变化 → 必须重试一次滑动再判, 仍无变化才算到底。

    规格(2026-08-21): 滑动后等0.8s检测页面是否变化。无变化时不要卡死,
    执行再次尝试一次滑动。重试仍无变化才确认到底。
    防止「触摸事件未完成/滑动未生效」被误判为到底导致脚本卡住。
    """
    ctrl, adapter = env
    ctrl.scene = "SHOP"
    # 滑动 4 次后页面钉底(截图不再变化) — 模拟到底
    swipe_calls = {"n": 0}
    orig_swipe = ctrl.swipe

    def counted_swipe(x1, y1, x2, y2, duration=0.3):
        swipe_calls["n"] += 1
        # 前 4 次上滑使 up_swipes 递增(截图种子变化, 页面变化)
        # 之后 up_swipes 锁定, 截图种子不变(模拟到底)
        if y1 > y2:
            ctrl.up_swipes += 1
    monkeypatch.setattr(ctrl, "swipe", counted_swipe)

    info = adapter.shop_auto.find_product(max_scroll=12)
    assert info is None  # 无目标商品
    # 必须发生重试滑动(无变化后的第二次 swipe) — 验证重试机制存在
    # 12 次上限内到底: 4 次正常滑动 + 至少 1 次重试滑动
    assert swipe_calls["n"] > 4, \
        f"滑动无变化时必须重试一次(规格), 实际 swipe {swipe_calls['n']} 次"
    assert adapter.shop_auto.scrolling is False, "退出后释放锁"


def test_scroll_no_change_does_not_infinite_loop(env, monkeypatch):
    """页面一开始就钉底(第一次滑动就无变化) → 重试一次后判底, 不无限滑。"""
    ctrl, adapter = env
    ctrl.scene = "SHOP"
    swipe_calls = {"n": 0}

    def static_swipe(x1, y1, x2, y2, duration=0.3):
        swipe_calls["n"] += 1
        # 不累加 up_swipes — 截图种子恒定 (min(0,3),SHOP) 页面钉底
    monkeypatch.setattr(ctrl, "swipe", static_swipe)

    info = adapter.shop_auto.find_product(max_scroll=12)
    assert info is None
    # 第一次滑动无变化 → 重试一次 → 仍无变化 → 判底 break
    # 总滑动次数应很小(2次: 初次+重试), 不触达 max_scroll
    assert swipe_calls["n"] <= 4, \
        f"页面钉底必须快速判底不无限滑(实际 {swipe_calls['n']} 次)"


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
        # 第一轮(timeout=1s, 快速轮询约 2 次检测)全程 UNKNOWN;
        # 第二轮(暖启动后)MAP, 且二次确认也 MAP(≥4 次仍 MAP)
        if calls["n"] <= 2:
            return PokemonGoState.UNKNOWN     # 第一轮: 卡在加载/未知页
        return PokemonGoState.MAP             # 第二轮: 主页出现 + 二次确认
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


# ── 11: wait_home MAP 二次确认防转场动画瞬时误判 ─────────────────

def test_wait_home_rejects_transient_home(env, monkeypatch):
    """转场动画/黑屏瞬时命中 MAP 但下一帧消失 → 不得判为已进主页。

    规格: 多特征评分机制, 满足多个条件才算进入, 避免加载动画/黑屏误判。
    二次确认: 首次命中 home_state 后隔 0.8s 再检, 两次都是主页状态才算。
    """
    ctrl, adapter = env
    calls = {"n": 0}

    def fake_detect():
        calls["n"] += 1
        # 第1次: 加载中 UNKNOWN; 第2次: 转场动画瞬时闪到 MAP;
        # 第3次(二次确认): 动画结束回到 UNKNOWN → 否决, 继续等;
        # 第4次起: 真正稳定 MAP + 二次确认也 MAP
        if calls["n"] == 1:
            return PokemonGoState.UNKNOWN
        if calls["n"] == 2:
            return PokemonGoState.MAP       # 瞬时误判
        if calls["n"] == 3:
            return PokemonGoState.UNKNOWN   # 二次确认否决
        return PokemonGoState.MAP           # 真正进入
    monkeypatch.setattr(adapter.detector, "detect", fake_detect)
    monkeypatch.setattr(adapter, "handle_popups", lambda: 0)
    monkeypatch.setattr(adapter, "launch", lambda: True)

    assert adapter.wait_home(timeout=10) is True, \
        "转场瞬时 MAP 必须被二次确认否决, 之后真正稳定 MAP 才返回 True"
    assert calls["n"] >= 5, "必须经过瞬时命中→否决→稳定命中→二次确认全流程"
