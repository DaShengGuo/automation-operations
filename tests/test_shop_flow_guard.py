"""
tests/test_shop_flow_guard.py
商城/登出业务流程守卫 + 事件驱动验收测试(§二十三 测试1-6 的子集)

1. 主菜单点击商店正常进店
2. 点击商店误入设置 → WRONG_PAGE_DETECTED → BACK → 重试成功, 绝不点登出
3. 商城到底动态判定 → 停止滑动(非固定次数)
4. 登出守卫: 任务未成功(未购买)禁止登出; 成功后允许
5. 队列: 完成后的账号可重新加入执行(Round 2)
6. 认证失败弹窗 → WAIT_HOME 快速重走登录(不进 RECOVERY)

全部 Mock 设备/OCR, 不依赖真机。
"""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from automation.pokemon_go.adapter import PokemonGoAdapter
from automation.pokemon_go.states import PokemonGoState
from core.config import ControlConfig
from core.device_worker import DeviceWorker
from core.account_queues import DeviceAccountQueue, QueueAccountStatus
from core.state_machine import WorkerState
from models.page_state import PageState
from automation.base_game import LoginResult, TaskOutcome
from storage.database import Database
from storage.repositories import AccountRepository, TaskResultRepository
from tests.fakes import FakeAdb, FakeDeviceManager

PGO_PKG = "com.nianticlabs.pokemongo"
MENU_TEXTS = ["圖鑑", "商店", "設定"]
SETTINGS_TEXTS = ["設定", "登出", "版本"]
SHOP_TEXTS = ["新手禮盒", "看看别的貼圖"]


class ShopCtrl:
    """脚本化控制器 — 场景可切换, 截图随滑动次数变化"""

    def __init__(self):
        self.serial = "FAKE-SHOP"
        self.package = PGO_PKG
        self.screen_w, self.screen_h = 1080, 2400
        self.matcher = None
        self.adb = None
        self.device = None
        self._pkg = PGO_PKG
        self.scene = "MENU"          # MENU / SETTINGS / SHOP
        self.clicks: list[tuple] = []
        self.clicks_in_settings = 0
        self.pressed: list[str] = []
        self.up_swipes = 0
        self.down_swipes = 0
        self.click_effect = None     # callable(x, y)
        self._saved: list[str] = []

    def current_package(self):
        return self._pkg

    def dump_hierarchy(self):
        return ""

    def screenshot(self):
        # 以 (滑动次数, 场景) 为种子生成噪声图:
        #  - 同键同图 → 到底判底可用(连续两帧差 0 < 4)且稳定
        #  - 场景切换/滑动 → 图像变化 → 屏幕指纹变化 → 检测器状态
        #    缓存失效, 场景切换可见
        # (纯色+图案方案不可行: 本环境 cv2.resize 在 ≥10 缩放比下
        #  会把稀疏/高频图案平均成均匀图, 指纹恒定 — 实测坑)
        import zlib
        key = (min(self.up_swipes, 3), self.scene)
        seed = zlib.crc32(f"{key}".encode())
        rng = np.random.default_rng(seed)
        return rng.integers(0, 255, (2400, 1080, 3), dtype=np.uint8)

    def save_screenshot(self, path):
        self._saved.append(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.scene == "SETTINGS":
            self.clicks_in_settings += 1
        if self.click_effect:
            self.click_effect(x, y)

    def click_ratio(self, rx, ry):
        x, y = int(rx * self.screen_w), int(ry * self.screen_h)
        self.click(x, y)
        return x, y

    def press(self, key):
        self.pressed.append(key)

    def swipe_direction(self, direction, distance=0.5):
        if direction == "up":
            self.up_swipes += 1
        else:
            self.down_swipes += 1

    def swipe(self, *a, **k):
        pass

    def app_start(self, package="", activity=None):
        self._pkg = self.package

    def is_app_running(self):
        return True


def _scene_texts(ctrl):
    return {"MENU": MENU_TEXTS, "SETTINGS": SETTINGS_TEXTS,
            "SHOP": SHOP_TEXTS}[ctrl.scene]


@pytest.fixture
def cfg():
    ControlConfig.reset()
    return ControlConfig.load("pokemon_go")


@pytest.fixture
def env(cfg, monkeypatch, tmp_path):
    ctrl = ShopCtrl()
    monkeypatch.setattr(
        "core.ocr.ocr_with_boxes",
        lambda img, min_conf=0.5: [(t, (10, 10, 400, 60))
                                  for t in _scene_texts(ctrl)])
    monkeypatch.setattr(cfg, "screenshots_dir", tmp_path)
    adapter = PokemonGoAdapter(ctrl, cfg)
    adapter.detector.ocr_cache_sec = 0
    return ctrl, adapter


# ── 测试 1: 主菜单 → 商店 正常进店 ──────────────────────────────

def test_enter_shop_normal(env):
    ctrl, adapter = env

    def effect(x, y):
        ctrl.scene = "SHOP"
    ctrl.click_effect = effect

    assert adapter.shop_auto.enter_shop(timeout=10) is True
    assert ctrl.scene == "SHOP"
    assert ctrl.pressed == [], "正常进店不按 BACK"


# ── 测试 2: 误入设置 → WRONG_PAGE → BACK → 重试, 绝不点登出 ──────

def test_enter_shop_wrong_page_settings_guarded(env):
    ctrl, adapter = env

    def effect(x, y):
        if ctrl.scene == "SETTINGS":
            ctrl.scene = "MENU"      # BACK 后回主菜单
        elif len(ctrl.clicks) >= 2:  # 第二次点商店 → 正确进店
            ctrl.scene = "SHOP"
        else:                        # 第一次点商店 → 误入设置
            ctrl.scene = "SETTINGS"

    def press(key):
        ctrl.pressed.append(key)
        ctrl.scene = "MENU"          # 设置页按 BACK → 回主菜单

    ctrl.click_effect = effect
    ctrl.press = press

    assert adapter.shop_auto.enter_shop(timeout=10) is True
    assert ctrl.pressed == ["back"], "误入设置必须 BACK 回主菜单"
    assert ctrl.clicks_in_settings == 0, \
        "设置在设置页的任何点击(尤其登出)都被禁止"
    assert any("WRONG_PAGE_SETTINGS" in p for p in ctrl._saved), \
        "误入设置必须留证截图"
    assert adapter.detect_state() == PokemonGoState.SHOP


# ── 测试 3: 到底动态判定, 停止滑动(非固定次数) ──────────────────

def test_find_product_bottom_reached_stops(env):
    ctrl, adapter = env
    ctrl.scene = "SHOP"

    info = adapter.shop_auto.find_product(max_scroll=12)
    assert info is None                    # 脚本里没有商品
    assert ctrl.up_swipes < 12, \
        f"必须动态判底停止(实际 {ctrl.up_swipes} 次上滑)"
    assert ctrl.up_swipes <= 6, \
        f"到底后不得继续上滑(实际 {ctrl.up_swipes} 次)"


# ── 测试 4: 登出守卫(未购买禁止登出) ────────────────────────────

def test_logout_guard_blocks_before_purchase(env, monkeypatch):
    ctrl, adapter = env
    monkeypatch.setattr(adapter.logout_auto, "run",
                        lambda timeout=150: True)

    # 未购买: logout() 必须拒绝
    assert adapter._purchase_ok is False
    assert adapter.logout() is False
    assert any("WRONG_LOGOUT_ATTEMPT" in p for p in ctrl._saved)

    # 任务成功后: 允许登出
    adapter._purchase_ok = True
    assert adapter.logout() is True


# ── 测试 5: 完成后的账号可重新加入执行(Round 2) ─────────────────

def test_queue_completed_account_can_be_readded():
    q = DeviceAccountQueue(device_serial="FAKE-Q")
    t1, ok1 = q.add_task("userA", "p1")
    assert ok1
    t2 = q.pop_next()
    assert t2 is not None
    q.mark_running(t2.id, "FAKE-Q")
    q.mark_success(t2.id, "FAKE-Q")
    assert t2.status == QueueAccountStatus.SUCCESS

    # 完成后任务移出队列 → 同账号重新加入 = Round 2
    t3, ok3 = q.add_task("userA", "p2")
    assert ok3, "完成后重新添加必须允许"
    t4 = q.pop_next()
    assert t4 is not None and t4.id == t3.id
    assert t4.password == "p2"


# ── 测试 6: 认证失败 → WAIT_HOME 快速重走登录(不进 RECOVERY) ─────

class AuthFailAutomation:
    """脚本化: 登录成功但 HOME 等待中认证弹窗被点掉回到登录页"""

    def __init__(self, serial=""):
        self.login_calls = 0
        self._page = PageState.LOGIN
        self.calls: list[str] = []

    def launch(self):
        self.calls.append("launch")
        return True

    def detect_page(self):
        self.calls.append("detect_page")
        return self._page

    def wait_home(self, timeout):
        # 认证失败: 弹窗 OK 后回到登录页 → 要求快速重走登录
        if self.login_calls >= 2:
            self._page = PageState.HOME
            return True
        self._page = PageState.LOGIN
        return False

    def handle_popups(self):
        return 0

    def restart(self):
        self.calls.append("restart")
        return True

    def recover(self):
        self.calls.append("recover")
        return True

    def login(self, account):
        self.login_calls += 1
        self._page = PageState.HOME
        return LoginResult.SUCCESS

    def execute_task(self, account):
        self.calls.append("execute_task")
        return TaskOutcome(True)

    def verify_result(self):
        return True

    def logout(self, force=False):
        self.calls.append("logout")
        return True


def test_wait_home_auth_failed_fast_relogin(tmp_path):
    db = Database(tmp_path / "runtime.db")
    accounts = AccountRepository(db)
    results = TaskResultRepository(db)
    try:
        accounts.add("user001", "p")
        w = DeviceWorker(
            serial="FAKE-A", cfg=ControlConfig(project_root=tmp_path),
            device_manager=FakeDeviceManager(adb=FakeAdb()),
            account_repo=accounts, result_repo=results,
            automation_factory=lambda s: AuthFailAutomation(s),
            stop_event=threading.Event(), pause_event=threading.Event())
        w._ensure_session()
        w._claim_next()

        for _ in range(60):
            w._tick()
            if w.runtime.success_count >= 1:
                break

        assert w.runtime.success_count == 1
        assert w.automation.login_calls == 2, \
            "认证失败后必须快速重走登录(恰好 2 次)"
        states = [s.value for s, _ in w.fsm.history]
        assert "RECOVERY" not in states, \
            "认证失败重登录不得进入 RECOVERY 阶梯"
        # 出现过 WAIT_HOME → DETECT_PAGE 的快速分流
        for i, s in enumerate(states[:-1]):
            if s == "WAIT_HOME" and states[i + 1] == "DETECT_PAGE":
                break
        else:
            raise AssertionError("WAIT_HOME 失败后必须立即转 DETECT_PAGE")
    finally:
        db.close()
