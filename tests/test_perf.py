"""
tests/test_perf.py
生产性能组件单元测试 — wait_fast / 指纹 / 停滞检测 / 统计
"""
from __future__ import annotations

import time

import numpy as np

from core.perf import (PerfStats, PerformanceTracer, StallDetector,
                       screen_fingerprint, wait_fast)


class TestWaitFast:

    def test_returns_immediately_when_condition_true(self):
        t0 = time.time()
        ok = wait_fast(lambda: True, timeout=10)
        assert ok and time.time() - t0 < 0.3

    def test_timeout_returns_false(self):
        t0 = time.time()
        ok = wait_fast(lambda: False, timeout=0.5)
        assert not ok
        assert 0.4 < time.time() - t0 < 1.5

    def test_condition_becomes_true_later(self):
        state = {"ready": False}
        import threading
        threading.Timer(0.4, lambda: state.update(ready=True)).start()
        t0 = time.time()
        ok = wait_fast(lambda: state["ready"], timeout=5)
        assert ok
        # 事件驱动: 条件满足后立即返回(不等到 timeout)
        assert time.time() - t0 < 1.0


class TestScreenFingerprint:

    def test_same_image_same_fp(self):
        img = np.zeros((240, 320, 3), dtype=np.uint8)
        img[100:140, 150:180] = 255
        assert screen_fingerprint(img) == screen_fingerprint(img)

    def test_changed_image_diff_fp(self):
        a = np.zeros((240, 320, 3), dtype=np.uint8)
        b = np.zeros((240, 320, 3), dtype=np.uint8)
        b[50:90, 50:90] = 255
        assert screen_fingerprint(a) != screen_fingerprint(b)

    def test_invalid_image_returns_empty(self):
        assert screen_fingerprint(None) == ""


class TestStallDetector:

    def test_state_budget_exceeded(self):
        d = StallDetector(state_budget={"MAP": 0.1})
        d.check("MAP")
        time.sleep(0.15)
        reason = d.check("MAP")
        assert reason is not None and "STATE_BUDGET" in reason

    def test_screen_stalled(self):
        d = StallDetector(screen_stall_sec=0.1)
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        assert d.check("X", image_bgr=img) is None
        time.sleep(0.15)
        reason = d.check("X", image_bgr=img)
        assert reason is not None and "SCREEN_STALLED" in reason

    def test_screen_change_resets(self):
        d = StallDetector(screen_stall_sec=0.6)
        a = np.zeros((100, 100, 3), dtype=np.uint8)
        b = np.ones((100, 100, 3), dtype=np.uint8) * 255
        d.check("X", image_bgr=a)
        time.sleep(0.1)
        d.check("X", image_bgr=b)  # 画面变化 → 重置计时
        time.sleep(0.2)
        assert d.check("X", image_bgr=b) is None  # 未到 0.6s 阈值

    def test_touch_updates_last_action(self):
        d = StallDetector()
        t0 = d.last_action_ts
        time.sleep(0.01)
        d.touch("click_shop")
        assert d.last_action_ts > t0
        assert d.last_action == "click_shop"


class TestPerfStats:

    def test_summary_percentiles(self):
        s = PerfStats()
        for sec in [50, 52, 55, 60, 200]:
            s.add({"total_sec": sec, "stages": []})
        summary = s.summary()
        assert summary["n"] == 5
        assert summary["avg"] == round((50 + 52 + 55 + 60 + 200) / 5, 1)
        assert summary["p50"] == 55
        assert summary["p90"] == 200
        assert summary["max"] == 200

    def test_top_bottlenecks(self):
        s = PerfStats()
        s.add({"total_sec": 60, "stages": [("A", "B", 10000),
                                           ("B", "C", 1000)]})
        s.add({"total_sec": 61, "stages": [("A", "B", 12000),
                                           ("B", "C", 900)]})
        tops = s.top_bottlenecks(2)
        assert tops[0][0] == "A→B"
        assert tops[0][1] == 11.0


class TestPerformanceTracer:

    def test_stages_and_finish(self):
        t = PerformanceTracer("abc***")
        time.sleep(0.05)
        t.mark("GAME_START")
        time.sleep(0.05)
        report = t.finish()
        assert report["total_sec"] > 0.09
        assert report["account"] == "abc***"
        keys = [f"{f}→{to}" for f, to, _ in report["stages"]]
        assert any("GAME_START" in k for k in keys)
