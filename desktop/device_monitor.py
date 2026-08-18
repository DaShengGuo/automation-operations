"""
desktop/device_monitor.py
DeviceMonitor — 设备热插拔监控线程。

周期任务(后台守护线程, 不阻塞 GUI):
  - 每 5s: adb start-server(需要时) + `adb devices -l`(重试×3)
    → DeviceRegistry.refresh_from_adb — 热插拔/断线即时反映到 GUI
  - 每 60s(运行中 300s): 全量硬件扫描(getprop 回填型号/品牌/分辨率)
    — 避免在自动化运行期间高频打 getprop 干扰自动化
  - 变化时通过 QtEventBus.devices_changed 通知 GUI 刷新设备卡片

所有 adb 调用经 AdbLocator → run_hidden_process, 全程无 CMD 黑框。
"""
from __future__ import annotations

import logging
import threading
import time

from desktop.adb_locator import AdbLocator
from desktop.device_registry import DeviceRegistry

logger = logging.getLogger(__name__)

FAST_INTERVAL = 5.0     # 连接状态热插拔检测周期(秒)
IDLE_FULL_INTERVAL = 60.0    # 空闲时硬件信息全量刷新周期(秒)
RUNNING_FULL_INTERVAL = 300.0  # 运行中硬件信息刷新周期(秒, 不干扰自动化)
VPN_CHECK_INTERVAL = 120.0  # 每设备 VPN 在线检测周期(秒, 状态变化才提醒)


class DeviceMonitor:
    """设备热插拔监控(守护线程)。"""

    def __init__(self, registry: DeviceRegistry, bus=None,
                 device_manager=None, adb_locator: AdbLocator | None = None):
        self.registry = registry
        self.bus = bus
        self.device_manager = device_manager   # DeviceManager(懒建)
        self.locator = adb_locator or AdbLocator()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_full = 0.0
        self._last_state_key = ""
        # VPN 检测节流 + 状态(每设备): 状态变化才发 bus 提醒
        self._next_vpn_check: dict[str, float] = {}
        self._vpn_ok: dict[str, bool] = {}
        # 运行中判断回调(避免导入循环): controller 注入
        self.is_running = lambda: False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="device-monitor", daemon=True)
        self._thread.start()
        logger.info("[DeviceMonitor] 已启动(热插拔监控 5s / 硬件刷新 "
                    "60s)")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None

    # ── 主循环 ──

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.debug("[DeviceMonitor] 周期任务异常: %s", e)
            self._stop.wait(FAST_INTERVAL)

    def _tick(self) -> None:
        # 1. adb server 保活(不健康/首次才显式 start-server)
        try:
            self.locator.start_server()
        except Exception as e:
            logger.debug("[DeviceMonitor] start-server 异常: %s", e)

        # 2. 连接状态热插拔检测(重试×3 吸收瞬时空窗)
        rows = self.locator.devices(retries=3, delay=1.5)
        summary = self.registry.refresh_from_adb(rows)

        # 3. 硬件信息全量刷新(节流)
        full_interval = (RUNNING_FULL_INTERVAL if self.is_running()
                         else IDLE_FULL_INTERVAL)
        if time.time() - self._last_full >= full_interval:
            self._refresh_hardware()
            self._last_full = time.time()

        # 4. VPN 在线检测(每设备节流 120s; 状态变化才提醒 GUI)
        now = time.time()
        for r in self.registry.records():
            if not r.is_connected:
                continue
            if now < self._next_vpn_check.get(r.serial, 0.0):
                continue
            self._next_vpn_check[r.serial] = now + VPN_CHECK_INTERVAL
            self._check_vpn(r.serial)

        # 5. 变化通知 GUI
        state_key = self._state_key()
        if summary["added"] or summary["removed"] or \
                summary["state_changed"] or state_key != self._last_state_key:
            self._last_state_key = state_key
            if self.bus is not None:
                self.bus.devices_changed.emit(
                    [r.to_dict() for r in self.registry.records()])

    def _refresh_hardware(self) -> None:
        """全量扫描: getprop 回填型号/品牌/分辨率 + READY 门禁状态。"""
        if self.device_manager is None:
            return
        try:
            devices = self.device_manager.scan(fast=False)
        except Exception as e:
            logger.warning("[DeviceMonitor] 硬件扫描失败: %s", e)
            return
        for d in devices:
            self.registry.update_hardware_info(
                d.serial,
                model=d.model, brand=d.brand,
                manufacturer=getattr(d, "manufacturer", ""),
                android_version=getattr(d, "android_version", ""),
                resolution=getattr(d, "resolution", ""),
            )
            # 扫描即标记 READY(u2 会话将在运行前初始化, 此处反映
            # "可运行"状态; 真正的 u2_connect 结果由运行门禁回填)
            if d.is_adb_healthy and d.status.value not in (
                    "OFFLINE", "DEVICE_ERROR", "DISABLED"):
                self.registry.mark_ready(
                    d.serial, True,
                    f"ADB 正常 {d.brand} {d.model} {d.resolution}")
            else:
                self.registry.mark_ready(d.serial, False, d.init_error)

    def _check_vpn(self, serial: str) -> None:
        """检测单台设备 VPN; 缺失时(首次/由开转关)经 bus 提醒 GUI 弹窗。"""
        try:
            from desktop.vpn_check import check_vpn
            ok, detail = check_vpn(self.locator.path, serial)
        except Exception as e:
            logger.debug("[VPN] 检测异常 %s: %s", serial, e)
            return
        prev = self._vpn_ok.get(serial)
        self._vpn_ok[serial] = ok
        logger.info("[VPN] 设备 %s VPN=%s (%s)", serial,
                    "在线" if ok else "未检测到", detail)
        if not ok and prev is not False:
            # 首次检测缺失(prev=None) / 由开转关(prev=True) → 弹窗提醒;
            # 已知缺失(prev=False)不重复弹窗。注意必须用 `is` 判身份:
            # 若写成 `prev is not True`, False is not True 恒真 → 每轮重复弹。
            if self.bus is not None:
                self.bus.vpn_warning.emit(serial, detail)

    def _state_key(self) -> str:
        return ",".join(
            f"{r.serial}:{r.adb_state}:{int(r.ready)}"
            f":{int(r.worker_running)}"
            for r in self.registry.records())

    def refresh_now(self) -> None:
        """立即执行一次周期任务(GUI 点"重新检测设备"时)。"""
        try:
            self._tick()
        except Exception as e:
            logger.warning("[DeviceMonitor] 手动刷新失败: %s", e)
