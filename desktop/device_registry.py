"""
desktop/device_registry.py
DeviceRegistry — 全应用唯一设备状态源。

GUI / DesktopAppController / DeviceManager / WorkerManager 共享同一份
设备事实: serial / adb 连接状态 / 型号 / READY(u2 会话) / worker 状态 /
最近发现时间 / 拒绝原因。

原则:
  - DEVICE_DISCOVERY 日志必须带 REJECT_REASON, 禁止静默 continue
  - 状态区分 device / offline / unauthorized / no permissions
  - unauthorized → GUI 提示"等待手机授权, 请点击允许USB调试"
  - offline → GUI 提示"ADB OFFLINE 正在尝试重新连接"
  - 线程安全(GUI 线程 / 监控线程 / worker 线程并发读写)
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 连接状态 → 拒绝/提示原因(DEVICE_DISCOVERY REJECT_REASON)
_STATE_REASONS = {
    "unauthorized": "等待手机授权, 请点击允许USB调试",
    "offline": "ADB OFFLINE, 正在尝试重新连接",
    "no permissions": "缺少 USB 权限(no permissions), 请检查 USB 驱动/数据线",
    "missing": "设备已断开(拔线或关闭USB调试)",
}


@dataclass
class DeviceRecord:
    """单台设备的注册记录(GUI/Controller 共用)。"""
    serial: str
    adb_state: str = "unknown"        # device/offline/unauthorized/no permissions/missing
    model: str = ""                   # getprop ro.product.model(真实型号)
    brand: str = ""
    manufacturer: str = ""
    android_version: str = ""
    resolution: str = ""
    ready: bool = False               # u2 会话初始化通过(READY 设备)
    ready_detail: str = ""            # 初始化摘要 / 失败原因
    worker_running: bool = False
    worker_state: str = "-"
    reject_reason: str = ""           # 非 device 状态时的原因
    # 设备环境重置(人工触发): "" / RESETTING / RESET_FAILED
    reset_state: str = ""
    reset_detail: str = ""            # 失败步骤/原因(GUI 显示)
    last_seen: float = field(default_factory=time.time)
    discovered_at: float = field(default_factory=time.time)

    @property
    def is_connected(self) -> bool:
        return self.adb_state == "device"

    def to_dict(self) -> dict:
        return {
            "serial": self.serial,
            "adb_state": self.adb_state,
            "model": self.model or "-",
            "brand": self.brand or "-",
            "resolution": self.resolution or "-",
            "android_version": self.android_version or "-",
            "ready": self.ready,
            "ready_detail": self.ready_detail,
            "worker_running": self.worker_running,
            "worker_state": self.worker_state,
            "reject_reason": self.reject_reason,
            "reset_state": self.reset_state,
            "reset_detail": self.reset_detail,
            "last_seen": self.last_seen,
        }


class DeviceRegistry:
    """单一设备状态源(线程安全)。"""

    def __init__(self):
        self._records: dict[str, DeviceRecord] = {}
        self._lock = threading.RLock()

    # ── 查询 ──

    def records(self) -> list[DeviceRecord]:
        with self._lock:
            return list(self._records.values())

    def get(self, serial: str) -> DeviceRecord | None:
        with self._lock:
            return self._records.get(serial)

    def counts(self) -> dict:
        """GUI 三个指标: detected / ready / running。"""
        with self._lock:
            detected = sum(1 for r in self._records.values()
                           if r.adb_state != "missing")
            ready = sum(1 for r in self._records.values() if r.ready)
            running = sum(1 for r in self._records.values()
                          if r.worker_running)
        return {"detected": detected, "ready": ready, "running": running}

    # ── 更新 ──

    def refresh_from_adb(self, rows: list[dict]) -> dict:
        """用 `adb devices -l` 结果刷新连接状态(热插拔检测)。

        rows: [{serial, state, ...}]
        返回本次变化摘要 {added, removed, state_changed}。
        """
        now = time.time()
        summary = {"added": [], "removed": [], "state_changed": []}
        seen: set[str] = set()
        with self._lock:
            for row in rows:
                serial, state = row["serial"], row["state"]
                seen.add(serial)
                rec = self._records.get(serial)
                if rec is None:
                    rec = DeviceRecord(serial=serial, adb_state=state)
                    self._records[serial] = rec
                    summary["added"].append(serial)
                    logger.info("[DEVICE_DISCOVERY] 新设备 %s state=%s",
                                serial, state)
                elif rec.adb_state != state:
                    old = rec.adb_state
                    rec.adb_state = state
                    summary["state_changed"].append(
                        f"{serial}:{old}->{state}")
                    logger.info("[DEVICE_DISCOVERY] 设备 %s 状态变化 "
                                "%s -> %s", serial, old, state)
                rec.last_seen = now
                if state == "device":
                    rec.reject_reason = ""
                    # 型号等硬件信息稍后由全量扫描回填(update_hardware_info)
                    for key in ("product", "device"):
                        if key in row and not rec.model:
                            rec.model = row[key]
                else:
                    reason = _STATE_REASONS.get(
                        state, f"未知 ADB 状态: {state}")
                    if rec.reject_reason != reason:
                        logger.warning(
                            "[DEVICE_DISCOVERY] REJECT_REASON %s: %s",
                            serial, reason)
                    rec.reject_reason = reason
                    rec.ready = False
            # 已消失设备 → missing(保留记录, GUI 显示"已断开")
            for serial, rec in list(self._records.items()):
                if serial not in seen and rec.adb_state != "missing":
                    old = rec.adb_state
                    rec.adb_state = "missing"
                    rec.reject_reason = _STATE_REASONS["missing"]
                    summary["removed"].append(serial)
                    logger.info("[DEVICE_DISCOVERY] 设备 %s 断开(%s)",
                                serial, old)
                    rec.last_seen = now
        return summary

    def update_hardware_info(self, serial: str, **info) -> None:
        """全量扫描(getprop)后回填型号/品牌/分辨率等硬件信息。"""
        with self._lock:
            rec = self._records.get(serial)
            if rec is None:
                rec = DeviceRecord(serial=serial)
                self._records[serial] = rec
            for key in ("model", "brand", "manufacturer",
                        "android_version", "resolution"):
                if key in info and info[key]:
                    setattr(rec, key, info[key])
            rec.last_seen = time.time()

    def mark_ready(self, serial: str, ok: bool, detail: str = "") -> None:
        """设备初始化(u2 会话)结果 — READY 门禁。"""
        with self._lock:
            rec = self._records.get(serial)
            if rec is None:
                rec = DeviceRecord(serial=serial)
                self._records[serial] = rec
            changed = (rec.ready, rec.ready_detail) != (ok, detail)
            rec.ready = ok
            rec.ready_detail = detail
            if changed:
                level = logging.INFO if ok else logging.WARNING
                logger.log(level, "[DEVICE_READY] %s ready=%s %s",
                           serial, ok, detail or "")

    def mark_worker(self, serial: str, running: bool,
                    state: str = "-") -> None:
        """worker 生命周期状态(WorkerManager 启动/停止时更新)。"""
        with self._lock:
            rec = self._records.get(serial)
            if rec is None:
                rec = DeviceRecord(serial=serial)
                self._records[serial] = rec
            if rec.worker_running != running:
                logger.info("[WORKER] 设备 %s worker %s(state=%s)",
                            serial, "启动" if running else "停止", state)
            rec.worker_running = running
            rec.worker_state = state if running else "-"

    def mark_resetting(self, serial: str, state: str, detail: str = ""
                       ) -> None:
        """设备环境重置状态(人工触发): "" / RESETTING / RESET_FAILED。

        detail: RESET_FAILED 时的「步骤 — 原因」, GUI 卡片显示。
        """
        with self._lock:
            rec = self._records.get(serial)
            if rec is None:
                rec = DeviceRecord(serial=serial)
                self._records[serial] = rec
            if (rec.reset_state, rec.reset_detail) != (state, detail):
                logger.info("[RESET] 设备 %s 重置状态 %s%s",
                            serial, state or "清除",
                            f" — {detail}" if detail else "")
            rec.reset_state = state
            rec.reset_detail = detail

    def drop(self, serial: str) -> None:
        with self._lock:
            self._records.pop(serial, None)
