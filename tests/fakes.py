"""
tests/fakes.py
共享 Fake 组件 — 脚本化 ADB / 设备控制器 / 设备管理 / 自动化

调度器、集成、API 测试复用同一套 Fake, 不依赖真机。
"""
from __future__ import annotations

import numpy as np

from automation.base_game import LoginResult, TaskOutcome
from core.device_manager import InitReport
from core.exceptions import SelectorNotConfiguredError
from models.device import AndroidDevice, DeviceStatus
from models.page_state import PageState


class FakeAdb:
    """脚本化 ADB — Worker/Watchdog 用到的最小接口"""

    def __init__(self):
        self.path = "fake-adb"

    def get_state(self, serial):
        return "device"

    def pidof(self, serial, package):
        return 12345

    def _run(self, args, timeout=15):
        return ""


class FakeController:
    """脚本化设备控制器 — 截图内容每次变化, 避免 Watchdog 误判页面停滞"""

    def __init__(self, serial):
        self.serial = serial
        self.device = AndroidDevice(serial=serial)
        self._shots = 0
        self.saved: list[str] = []

    def is_healthy(self):
        return True

    def connect(self):
        pass

    def disconnect(self):
        pass

    def screenshot(self):
        self._shots += 1
        return np.full((2, 2, 3), self._shots % 256, dtype=np.uint8)

    def save_screenshot(self, path):
        self.saved.append(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-png")

    def click(self, x, y):
        pass

    def press(self, key):
        pass


class FakeDeviceManager:
    """脚本化设备管理 — scan/init_device/create_controller 均可编程"""

    def __init__(self, adb=None, rows=None, init_fail_serials=()):
        self.adb = adb or FakeAdb()
        self.rows = rows or []
        self.init_fail_serials = set(init_fail_serials)
        self._devices = {}
        self.controllers = {}

    def scan(self, fast=False):
        self._devices = {d.serial: d for d in self.rows}
        return self.rows

    def init_device(self, device, target_package=""):
        report = InitReport(serial=device.serial)
        report.add("adb_online", "PASS")
        if device.serial in self.init_fail_serials:
            report.add("screenshot", "FAIL", "no screencap")
            report.passed = False
            device.status = DeviceStatus.DEVICE_ERROR
            device.init_error = "screenshot — no screencap"
        else:
            report.passed = True
            device.status = DeviceStatus.READY
        return report

    def create_controller(self, serial):
        c = FakeController(serial)
        self.controllers[serial] = c
        return c

    def get_device(self, serial):
        return self._devices.get(serial)


class ScriptedAutomation:
    """脚本化自动化: DETECT_PAGE 看到 LOGIN, 登录后回 HOME, 全流程成功"""

    def __init__(self, serial=""):
        self.serial = serial
        self.login_done = False
        self.calls: list[str] = []

    def launch(self):
        self.calls.append("launch")
        return True

    def detect_page(self):
        self.calls.append("detect_page")
        return PageState.HOME if self.login_done else PageState.LOGIN

    def wait_home(self, timeout):
        return True

    def handle_popups(self):
        return 0

    def restart(self):
        return True

    def recover(self):
        return True

    def login(self, account):
        self.calls.append(f"login:{account.masked()}")
        self.login_done = True
        return LoginResult.SUCCESS

    def execute_task(self, account):
        self.calls.append("execute_task")
        return TaskOutcome(True)

    def verify_result(self):
        return True

    def logout(self, force=False):
        self.calls.append("logout")
        self.login_done = False  # 登出后会话结束, 回到登录页(真机语义)
        return True


class BrokenAutomation(ScriptedAutomation):
    """选择器未标定时在 launch 阶段抛 SelectorNotConfiguredError"""

    def launch(self):
        raise SelectorNotConfiguredError("login 输入框选择器未配置")
