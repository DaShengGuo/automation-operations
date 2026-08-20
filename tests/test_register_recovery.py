"""
tests/test_register_recovery.py
REGISTER_SELECT(已注册/未注册选择页) 卡死修复验收测试 — §19 五个场景

全部 Mock 设备/OCR, 不依赖真机。真机验证以 main.py doctor / 真机 run 为准。

场景:
  1. 正常: 进入 REGISTER_SELECT → 点击已注册 → 进入 LOGIN        PASS
  2. 页面加载慢: REGISTER_SELECT 等待后出现 → 点击成功, 不重启
  3. 点击无效: 第一次点击失败 → 恢复重试第二次成功               PASS
  4. 真正卡死: 页面无变化 → 进入分级 Recovery, 不是立即 restart
  5. 恢复失败: 超过恢复预算 → 账号 RETRY, 继续下一账号
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from automation.pokemon_go.adapter import PokemonGoAdapter
from automation.pokemon_go.states import PokemonGoState
from core.config import ControlConfig
from core.device_worker import DeviceWorker
from core.state_machine import WorkerState
from core.watchdog import AnomalyType
from models.account import AccountStatus
from storage.database import Database
from storage.repositories import AccountRepository, TaskResultRepository
from tests.fakes import FakeAdb, FakeDeviceManager, ScriptedAutomation

PGO_PKG = "com.nianticlabs.pokemongo"
REGISTER_TEXTS = ["已註冊的玩家", "尚未註冊的玩家"]
PROVIDER_TEXTS = ["寶可夢訓練家中央站", "Facebook 登入"]


class ScriptedCtrl:
    """脚本化设备控制器 — OCR 文本可编程, 点击效果可编程"""

    def __init__(self):
        self.serial = "FAKE-REG"
        self.package = PGO_PKG
        self.screen_w, self.screen_h = 1080, 2400
        self.matcher = None
        self.adb = None
        self.device = None
        self._pkg = PGO_PKG
        self.ocr_texts: list[str] = []
        self.click_effect = None     # callable(x, y) 或 None
        self.clicks: list[tuple] = []
        self.back_count = 0
        self.app_starts = 0
        self.app_stops = 0
        self._shot = 0
        self._saved: list[str] = []

    def current_package(self):
        return self._pkg

    def dump_hierarchy(self):
        return ""

    def screenshot(self):
        # 每帧随机噪声: 屏幕指纹必须逐帧变化, 否则检测器状态缓存
        # (fp 相同 → 复用上次状态)会吞掉 OCR 脚本的页面切换。
        self._shot += 1
        rng = np.random.default_rng(self._shot)
        return rng.integers(0, 255, (2400, 1080, 3), dtype=np.uint8)

    def save_screenshot(self, path):
        self._saved.append(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.click_effect:
            self.click_effect(x, y)

    def press(self, key):
        self.back_count += 1

    def app_start(self, package="", activity=None):
        self.app_starts += 1
        self._pkg = self.package

    def app_stop(self, package=""):
        self.app_stops += 1
        self._pkg = "com.miui.home"

    def is_app_running(self):
        return True

    def reset(self):
        pass


def _boxes_for(texts):
    return [(t, (10, 10, 400, 60)) for t in texts]


@pytest.fixture
def cfg():
    ControlConfig.reset()
    return ControlConfig.load("pokemon_go")


@pytest.fixture
def env(cfg, monkeypatch, tmp_path):
    """构造真实 adapter + 脚本化控制器 + 可编程 OCR"""
    ctrl = ScriptedCtrl()
    monkeypatch.setattr(
        "core.ocr.ocr_with_boxes",
        lambda img, min_conf=0.5: _boxes_for(ctrl.ocr_texts))
    monkeypatch.setattr(cfg, "screenshots_dir", tmp_path)
    adapter = PokemonGoAdapter(ctrl, cfg)
    adapter.detector.ocr_cache_sec = 0
    # 缩短恢复等待, 测试快速执行
    r = adapter.register_recovery
    r.max_rounds = 1
    r.redetect_wait = 2
    r.reenter_wait = 2
    r.settle_wait = 1
    r.click_verify_wait = 1
    r.anti_double_click_sec = 0.2
    return ctrl, adapter


# ── 场景 1: 正常流程 ─────────────────────────────────────────────

def test_scenario1_normal_click_passes(env):
    ctrl, adapter = env
    ctrl.ocr_texts = list(REGISTER_TEXTS)

    def effect(x, y):
        ctrl.ocr_texts = list(PROVIDER_TEXTS)
    ctrl.click_effect = effect

    t0 = time.time()
    ok = adapter.click_returning_player(timeout=10)
    elapsed = time.time() - t0
    assert ok
    assert len(ctrl.clicks) == 1, "正常流程只应点击一次"
    assert ctrl.app_stops == 0, "正常流程绝不重启"
    assert elapsed < 5.0, f"页面出现必须立即处理(实际 {elapsed:.1f}s)"
    assert adapter.detect_state() == PokemonGoState.LOGIN_PROVIDER


# ── 场景 2: 页面加载慢, 等待后出现, 不重启 ────────────────────────

def test_scenario2_slow_page_waits_no_restart(env):
    ctrl, adapter = env
    t0 = time.time()
    clicked = {"done": False}

    def slow_ocr(img, min_conf=0.5):
        # 页面加载慢: 2s 后才出现注册页; 点击后进入登录方式页
        if clicked["done"]:
            return _boxes_for(PROVIDER_TEXTS)
        if time.time() - t0 > 2.0:
            return _boxes_for(REGISTER_TEXTS)
        return []

    import core.ocr as ocr_mod
    ocr_mod.ocr_with_boxes = slow_ocr

    def effect(x, y):
        clicked["done"] = True
    ctrl.click_effect = effect

    ok = adapter.click_returning_player(timeout=90)
    assert ok
    assert ctrl.app_stops == 0, "加载慢只应等待, 绝不重启"
    assert len(ctrl.clicks) == 1


# ── 场景 3: 第一次点击无效, 恢复后第二次成功 ─────────────────────

def test_scenario3_first_click_ineffective_retry_ok(env):
    ctrl, adapter = env
    ctrl.ocr_texts = list(REGISTER_TEXTS)

    def effect(x, y):
        if len(ctrl.clicks) >= 2:   # 第二次点击才生效
            ctrl.ocr_texts = list(PROVIDER_TEXTS)
    ctrl.click_effect = effect

    ok = adapter.click_returning_player(timeout=3)
    assert not ok, "第一次点击无效果 → 点击验证失败"
    assert ctrl.app_stops == 0

    # login() 的 step 1 失败路径: 转入注册页分级恢复(不重启)
    recovered = adapter.register_recovery.recover()
    assert recovered, "第二次点击应成功进入登录方式页"
    assert len(ctrl.clicks) == 2
    assert ctrl.app_stops == 0, "点击无效恢复流程绝不重启"
    assert adapter.detect_state() == PokemonGoState.LOGIN_PROVIDER


# ── 场景 4: 真正卡死 → 分级 Recovery, 不是立即 restart ────────────

def test_scenario4_stuck_goes_recovery_not_restart(env):
    ctrl, adapter = env
    ctrl.ocr_texts = list(REGISTER_TEXTS)
    ctrl.click_effect = None  # 点击永远无效, 页面永远不变

    ok = adapter.click_returning_player(timeout=2)
    assert not ok
    # 全程恢复(重检/重dump/重点击/暖切前台)都不 restart
    recovered = adapter.register_recovery.recover()
    assert not recovered, "真卡死: 分级恢复也救不回 → 交 Worker 预算"
    assert ctrl.app_stops == 0, "恢复 handler 绝不 force-stop/restart"
    assert len(ctrl.clicks) >= 2, "应尝试过点击阶梯重试"
    assert ctrl.back_count == 0, "恢复流程不按 BACK(注册页 BACK=退出游戏)"
    assert ctrl._saved, "卡死应保存诊断截图(§1)"


# ── 场景 5: 恢复预算超限 → 账号 RETRY, 继续下一账号 ───────────────

def test_scenario5_budget_exhausted_account_retry(tmp_path):
    db = Database(tmp_path / "runtime.db")
    accounts = AccountRepository(db)
    results = TaskResultRepository(db)
    try:
        accounts.add("user001", "p")
        w = DeviceWorker(
            serial="FAKE-B", cfg=ControlConfig(project_root=tmp_path),
            device_manager=FakeDeviceManager(adb=FakeAdb()),
            account_repo=accounts, result_repo=results,
            automation_factory=lambda s: ScriptedAutomation(s),
            stop_event=__import__("threading").Event(),
            pause_event=__import__("threading").Event())
        w._ensure_session()
        w._claim_next()
        assert w.account is not None

        # 页级异常恢复: 每轮 watchdog L1 重检"成功"回 DETECT_PAGE
        # (真实流程: 下一轮异常再次 _enter_recovery 进入 RECOVERY),
        # 但预算只允许 3 轮 — 第 4 轮必须账号 RETRY(§15/§16)
        for _ in range(6):
            if w.fsm.state != WorkerState.RECOVERY:
                w._enter_recovery(AnomalyType.PAGE_STUCK)
            w._recovery_step()
            if w.account is None:
                break

        assert w.account is None, "预算耗尽必须释放账号"
        acc = accounts.get_by_account("user001")
        assert acc.status in (AccountStatus.RETRY, AccountStatus.FAILED), \
            f"账号应 RETRY/FAILED, 实际 {acc.status}"
        rows = results.list()
        assert rows and rows[0]["failed_step"] in (
            "RECOVERY", "RECOVERY_FAILED", "")
    finally:
        db.close()
