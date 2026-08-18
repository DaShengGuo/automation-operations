"""
tests/test_device_manager.py
设备管理单元测试 — Mock ADB/设备信息，不依赖真机

说明: 本文件是「Mock ADB 的单元测试」，不代表真机测试结果。
真机验证结果以 `python main.py doctor` / compat report 为准。
"""
from __future__ import annotations

import numpy as np
import pytest

from core.adb_manager import AdbManager
from core.config import ControlConfig
from core.device_manager import DeviceManager
from models.device import DeviceStatus


class FakeAdb:
    """脚本化 ADB — 只实现 DeviceManager 用到的接口"""

    def __init__(self, rows=None, screen=(1080, 2400, 440),
                 installed=True, screen_on=True):
        self.rows = rows or [
            {"serial": "FAKE001", "state": "device",
             "product": "test", "model": "M1", "device": "test"},
            {"serial": "FAKE002", "state": "offline"},
            {"serial": "FAKE003", "state": "unauthorized"},
        ]
        self.screen = screen
        self.installed = installed
        self.screen_on = screen_on
        self.path = "fake-adb"

    def devices(self):
        return self.rows

    def get_state(self, serial):
        for r in self.rows:
            if r["serial"] == serial:
                return r["state"]
        return "missing"

    def getprop(self, serial, prop):
        return {"ro.product.manufacturer": "Xiaomi",
                "ro.product.brand": "Xiaomi",
                "ro.product.model": "M1",
                "ro.build.version.release": "13",
                "ro.build.version.sdk": "33"}.get(prop, "")

    def shell(self, serial, cmd, timeout=15):
        if cmd.startswith("wm size"):
            return f"Physical size: {self.screen[0]}x{self.screen[1]}"
        if cmd.startswith("wm density"):
            return f"Physical density: {self.screen[2]}"
        if cmd.startswith("dumpsys power"):
            return "mWakefulness=Awake"
        if "dumpsys window" in cmd:
            return "mStableInsets=Rect(0, 96 - 0, 126)"
        if cmd.startswith("dumpsys battery"):
            return "level: 88"
        if cmd.startswith("df /data"):
            return "Filesystem 1K-blocks Used Available Use% /data\n" \
                   "tmpfs 10000000 5000000 5000000 50% /data"
        if cmd.startswith("pidof"):
            return "12345"
        if cmd.startswith("pm list packages"):
            return ("package:com.ss.android.ugc.aweme" if self.installed
                    else "")
        if cmd.startswith("dumpsys input"):
            return "SurfaceOrientation: 0"
        if cmd.startswith("dumpsys activity"):
            return "topResumedActivity: com.test/.MainActivity"
        return ""

    def get_battery(self, serial):
        return 88

    def get_storage_free_gb(self, serial):
        return 5.0

    def is_screen_on(self, serial):
        return self.screen_on

    def wake_and_unlock(self, serial):
        return True

    def is_app_installed(self, serial, package):
        return self.installed

    def get_orientation(self, serial):
        return "portrait"

    def get_stable_insets(self, serial):
        return 96, 126

    def pidof(self, serial, package):
        return 12345

    def get_screen(self, serial):
        return self.screen


class FakeController:
    """脚本化 DeviceController"""

    def __init__(self, serial="FAKE001", fail_connect=False):
        self.serial = serial
        self.fail_connect = fail_connect
        self.device = object()  # 非 None 视为已连接
        self.screen_w, self.screen_h = 1080, 2400
        self.mapper = None
        self.matcher = None

    def connect(self):
        if self.fail_connect:
            raise RuntimeError("mock u2 connect failed")
        self.device = object()

    def reset(self):
        self.device = object()

    def disconnect(self):
        self.device = None

    def is_healthy(self):
        return self.device is not None

    def screenshot(self):
        return np.zeros((2400, 1080, 3), dtype=np.uint8)

    def save_screenshot(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-png")

    def dump_hierarchy(self):
        return "<hierarchy><node text='首页'/></hierarchy>"

    def click(self, x, y):
        pass

    def press(self, key):
        pass


@pytest.fixture
def manager(monkeypatch):
    ControlConfig.reset()  # 每个测试独立配置实例
    cfg = ControlConfig.load()
    manager = DeviceManager(cfg, adb=FakeAdb())
    # 复用 device_profiles.DeviceInfo.from_adb 会被 subprocess 干扰 → mock
    import device_profiles

    def fake_from_adb(serial, adb_path="adb"):
        return device_profiles.DeviceInfo(
            serial=serial, manufacturer="Xiaomi", brand="Xiaomi",
            model="M1", android_version="13", sdk=33,
            width=1080, height=2400, density=440)
    monkeypatch.setattr(device_profiles.DeviceInfo, "from_adb",
                        classmethod(lambda cls, serial, adb_path="adb":
                                   fake_from_adb(serial, adb_path)))
    yield manager
    ControlConfig.reset()


class TestDeviceManager:

    def test_scan_parses_devices(self, manager):
        devices = manager.scan()
        assert len(devices) == 3
        online = [d for d in devices if d.serial == "FAKE001"][0]
        assert online.is_adb_healthy
        assert online.model == "M1"
        assert online.width == 1080 and online.height == 2400
        assert online.battery_level == 88
        assert online.storage_free_gb == 5.0

    def test_scan_marks_offline(self, manager):
        devices = {d.serial: d for d in manager.scan()}
        assert devices["FAKE002"].status == DeviceStatus.OFFLINE
        assert not devices["FAKE002"].is_adb_healthy

    def test_scan_marks_unauthorized(self, manager):
        devices = {d.serial: d for d in manager.scan()}
        assert devices["FAKE003"].status == DeviceStatus.DEVICE_ERROR

    def test_init_passes_on_healthy_device(self, manager, monkeypatch):
        monkeypatch.setattr(manager, "create_controller",
                            lambda s: FakeController(s))
        device = [d for d in manager.scan() if d.serial == "FAKE001"][0]
        report = manager.init_device(device)
        checks = {name: status for name, status, _ in report.checks}
        assert checks["adb_online"] == "PASS"
        assert checks["u2_connect"] == "PASS"
        assert checks["screenshot"] == "PASS"
        assert checks["click"] == "PASS"
        assert checks["app_installed"] == "PASS"  # FakeAdb.installed=True
        assert report.passed
        assert device.status == DeviceStatus.READY

    def test_init_fails_when_u2_cannot_connect(self, manager, monkeypatch):
        monkeypatch.setattr(manager, "create_controller",
                            lambda s: FakeController(s, fail_connect=True))
        device = [d for d in manager.scan() if d.serial == "FAKE001"][0]
        report = manager.init_device(device)
        assert not report.passed
        assert device.status == DeviceStatus.DEVICE_ERROR
        assert "u2_connect" in device.init_error

    def test_init_reports_app_missing(self, manager, monkeypatch):
        manager.adb.installed = False
        monkeypatch.setattr(manager, "create_controller",
                            lambda s: FakeController(s))
        device = [d for d in manager.scan() if d.serial == "FAKE001"][0]
        report = manager.init_device(device)
        checks = {name: status for name, status, _ in report.checks}
        assert checks["app_installed"] == "FAIL"
        # 应用未装不视为设备致命错误 → 仍 READY(运行时会报 AppCrash)
        assert report.passed

    def test_disabled_device_from_config(self, manager, monkeypatch):
        manager.cfg.devices["overrides"] = {
            "FAKE001": {"disabled": True}}
        devices = {d.serial: d for d in manager.scan()}
        assert devices["FAKE001"].status == DeviceStatus.DISABLED

    def test_compat_report_marks_not_tested_for_offline(self, manager,
                                                        monkeypatch):
        monkeypatch.setattr(manager, "create_controller",
                            lambda s: FakeController(s))
        report = manager.compat_report()
        assert "# Device Compatibility Report" in report
        # offline/unauthorized 设备未经实测 → NOT TESTED(不冒充 PASS)
        assert "NOT TESTED" in report
        assert "❔" in report
        assert "FAKE001" in report
        assert "✅" in report  # 健康设备实测通过


class TestAdbManagerParsing:
    """AdbManager 纯解析逻辑（不连真实 ADB）"""

    def test_devices_output_parsing(self):
        import subprocess

        class FakeResult:
            stdout = ("List of devices attached\n"
                      "e98bee5a device product:alioth model:M2012K11AC "
                      "device:alioth transport_id:1\n"
                      "ABC123  offline transport_id:2\n"
                      "XYZ999  unauthorized transport_id:3\n")
            returncode = 0

        adb = AdbManager.__new__(AdbManager)  # 跳过 __init__ 的路径解析
        adb.path = "adb"
        adb._run = lambda args, timeout=15, check=False: FakeResult()
        rows = adb.devices()
        assert len(rows) == 3
        assert rows[0]["serial"] == "e98bee5a"
        assert rows[0]["state"] == "device"
        assert rows[0]["product"] == "alioth"
        assert rows[1]["state"] == "offline"
        assert rows[2]["state"] == "unauthorized"
