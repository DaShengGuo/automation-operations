"""
core/perf.py
生产性能组件:
  - PerformanceTracer: 账号生命周期阶段计时 + 统计(AVG/P50/P90/P95/MAX)
  - wait_fast: 快速轮询等待(前 3s 用 0.15s 间隔, 之后降频 0.5s)
  - ScreenFingerprint: 屏幕变化指纹(缩小灰度感知哈希) — 停滞检测
  - StallDetector: 页面停滞检测(A-E 五条件)
  - WorkerHeartbeat: worker 存活心跳(主调度器检测线程卡死)

原则: 正常路径零额外开销; 等待型接口全部事件驱动(页面一出现立即继续)。
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ── PerformanceTracer ────────────────────────────────────────

class PerformanceTracer:
    """单账号生命周期计时器。

    用法:
        tracer = PerformanceTracer(account, log)
        tracer.mark("PTC_CLICKED")
        ...
        report = tracer.finish("ACCOUNT_FINISHED")  # 输出阶段耗时汇总
    """

    EVENTS = [
        "ACCOUNT_START", "GAME_START", "RETURNING_PLAYER_FOUND",
        "LOGIN_PROVIDER_FOUND", "PTC_CLICKED", "PTC_PAGE_FOUND",
        "USERNAME_FINISHED", "PASSWORD_FINISHED", "LOGIN_CLICKED",
        "GAME_RETURNED", "MAP_FOUND", "MENU_FOUND", "SHOP_FOUND",
        "PRODUCT_FOUND", "PURCHASE_PAGE_FOUND", "PURCHASE_RESULT",
        "MAP_RETURNED", "SETTINGS_FOUND", "LOGOUT_CLICKED",
        "RETURNING_PLAYER_FOUND", "ACCOUNT_FINISHED",
    ]

    def __init__(self, account: str, log=None):
        self.account = account
        self.log = log or logger
        self._times: dict[str, float] = {"ACCOUNT_START": time.time()}
        self.recovery_seconds = 0.0
        self.retry_count = 0

    def mark(self, event: str):
        self._times[event] = time.time()

    def add_recovery(self, seconds: float):
        self.recovery_seconds += seconds

    def elapsed(self) -> float:
        return time.time() - self._times["ACCOUNT_START"]

    def stages(self) -> list[tuple[str, str, float]]:
        """相邻事件间耗时 [(from, to, elapsed_ms)]"""
        order = [e for e in self.EVENTS if e in self._times]
        out = []
        for i in range(1, len(order)):
            frm, to = order[i - 1], order[i]
            ms = (self._times[to] - self._times[frm]) * 1000
            out.append((frm, to, ms))
        return out

    def finish(self) -> dict:
        self.mark("ACCOUNT_FINISHED")
        report = {
            "account": self.account,
            "total_sec": round(self.elapsed(), 1),
            "recovery_sec": round(self.recovery_seconds, 1),
            "retry_count": self.retry_count,
            "stages": [(f, t, round(ms)) for f, t, ms in self.stages()],
        }
        self.log.info(f"[性能] 账号 {self.account} 总耗时 "
                      f"{report['total_sec']}s "
                      f"(恢复 {report['recovery_sec']}s, "
                      f"重试 {report['retry_count']})")
        for frm, to, ms in report["stages"]:
            self.log.info(f"[性能]   {frm} → {to}: {ms / 1000:.1f}s")
        return report


class PerfStats:
    """跨账号统计: AVG/P50/P90/P95/MAX"""

    def __init__(self):
        self.samples: list[float] = []
        self.stage_samples: dict[str, list[float]] = {}
        self.stalls = 0
        self.failures = 0
        self._lock = None

    def add(self, report: dict):
        self.samples.append(report["total_sec"])
        for frm, to, ms in report["stages"]:
            key = f"{frm}→{to}"
            self.stage_samples.setdefault(key, []).append(ms / 1000)

    def percentile(self, p: float) -> Optional[float]:
        if not self.samples:
            return None
        s = sorted(self.samples)
        idx = min(len(s) - 1, int(len(s) * p))
        return s[idx]

    def summary(self) -> dict:
        if not self.samples:
            return {"n": 0}
        return {
            "n": len(self.samples),
            "avg": round(sum(self.samples) / len(self.samples), 1),
            "p50": round(self.percentile(0.50), 1),
            "p90": round(self.percentile(0.90), 1),
            "p95": round(self.percentile(0.95), 1),
            "max": round(max(self.samples), 1),
            "stalls": self.stalls,
            "failures": self.failures,
        }

    def top_bottlenecks(self, top_n: int = 5) -> list[tuple[str, float]]:
        """阶段平均耗时排序(Top N 瓶颈)"""
        avgs = [(k, sum(v) / len(v)) for k, v in self.stage_samples.items()]
        avgs.sort(key=lambda x: -x[1])
        return [(k, round(v, 1)) for k, v in avgs[:top_n]]


# ── wait_fast ────────────────────────────────────────────────

def wait_fast(condition: Callable[[], bool],
              timeout: float = 10.0,
              fast_interval: float = 0.15,
              slow_interval: float = 0.5,
              fast_phase: float = 3.0,
              on_timeout: Callable[[], None] = None) -> bool:
    """快速轮询: 前 fast_phase 秒用 fast_interval, 之后降频 slow_interval。

    事件驱动 — 条件一满足立即返回, 不等满 timeout。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if condition():
                return True
        except Exception:
            pass
        interval = fast_interval if (deadline - time.time()) > \
            (timeout - fast_phase) else slow_interval
        time.sleep(interval)
    if on_timeout:
        try:
            on_timeout()
        except Exception:
            pass
    return False


# ── ScreenFingerprint ────────────────────────────────────────

def screen_fingerprint(image_bgr, shrink: int = 8) -> str:
    """缩小→灰度→感知哈希: 判断画面是否变化(不用于设备身份)。"""
    import cv2
    import numpy as np
    try:
        h, w = image_bgr.shape[:2]
        small = cv2.resize(image_bgr, (shrink * 2, shrink),
                           interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        avg = gray.mean()
        bits = (gray > avg).flatten()
        return hashlib.md5(bits.tobytes()).hexdigest()[:16]
    except Exception:
        return ""


# ── StallDetector ────────────────────────────────────────────

class StallDetector:
    """页面停滞检测(五条件)。

    A. 同一 state 超过预算时间
    B. 屏幕指纹长时间不变(默认 8s)
    C. hierarchy digest 长时间不变
    D. 点击后目标状态未出现(由调用方 wait_fast 超时表达)
    E. last_action 长时间未更新(heartbeat, 由调度器检查)
    """

    def __init__(self, screen_stall_sec: float = 8.0,
                 state_budget: dict = None):
        self.screen_stall_sec = screen_stall_sec
        self.state_budget = state_budget or {}
        self.last_fp = ""
        self.fp_since = 0.0
        self.last_state = ""
        self.state_since = 0.0
        self.last_action_ts = time.time()
        self.last_action = "init"
        self.stall_reports: list[dict] = []

    def touch(self, action: str):
        """每次实际动作后调用(点击/滑动/输入)"""
        self.last_action_ts = time.time()
        self.last_action = action

    def check(self, current_state: str, image_bgr=None,
              hierarchy_xml: str = "", now: float = None
              ) -> Optional[str]:
        """返回停滞原因(字符串)或 None"""
        now = now or time.time()
        reasons = []

        # A. 状态超预算
        if current_state != self.last_state:
            self.last_state = current_state
            self.state_since = now
        budget = self.state_budget.get(current_state)
        if budget and now - self.state_since > budget:
            reasons.append(f"STATE_BUDGET:{current_state}")

        # B. 屏幕不变
        if image_bgr is not None:
            fp = screen_fingerprint(image_bgr)
            if fp != self.last_fp:
                self.last_fp = fp
                self.fp_since = now
            elif now - self.fp_since > self.screen_stall_sec:
                reasons.append("SCREEN_STALLED")

        # C. hierarchy 不变(与屏幕同判, 取或)
        if hierarchy_xml:
            h = hashlib.md5(hierarchy_xml.encode("utf-8")).hexdigest()[:12]
            if not hasattr(self, "_last_h"):
                self._last_h = h
                self._h_since = now
            elif h != self._last_h:
                self._last_h = h
                self._h_since = now
            elif now - self._h_since > self.screen_stall_sec:
                reasons.append("HIERARCHY_STALLED")

        if not reasons:
            return None
        reason = "+".join(sorted(set(reasons)))
        self.stall_reports.append({
            "ts": now, "state": current_state, "reason": reason,
            "last_action": self.last_action,
        })
        if len(self.stall_reports) <= 20:
            logger.warning(f"[STALLED] {reason} state={current_state} "
                           f"last_action={self.last_action}")
        return reason


# ── WorkerHeartbeat ──────────────────────────────────────────

class WorkerHeartbeat:
    """worker 存活心跳(内存) — 调度器检测线程卡死"""

    def __init__(self, worker_id: str, timeout_sec: float = 60.0):
        self.worker_id = worker_id
        self.timeout_sec = timeout_sec
        self.last_beat = time.time()
        self.account = ""
        self.state = ""
        self.last_action = ""
        self.stalled_count = 0

    def beat(self, account: str = "", state: str = "",
             action: str = ""):
        self.last_beat = time.time()
        if account:
            self.account = account
        if state:
            self.state = state
        if action:
            self.last_action = action

    def is_stalled(self, now: float = None) -> bool:
        now = now or time.time()
        return now - self.last_beat > self.timeout_sec

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "account": self.account,
            "state": self.state,
            "last_action": self.last_action,
            "last_beat_ago": round(time.time() - self.last_beat, 1),
            "stalled_count": self.stalled_count,
        }
