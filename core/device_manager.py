"""
core/device_manager.py
设备管理 — ADB 检测 / 设备初始化 / 每设备 uiautomator2 会话(DeviceController)

复用 device_profiles.DeviceInfo.from_adb 读取设备硬件信息。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import uiautomator2 as u2

from core.adb_manager import AdbManager
from core.config import ControlConfig
from core.coordinate import CoordinateMapper, ScreenInsets
from core.exceptions import DeviceInitError, UiAutomatorError
from core.image_matcher import ImageMatcher
from models.device import AndroidDevice, DeviceStatus

logger = logging.getLogger(__name__)


# ── 每设备 uiautomator2 会话 ──

class DeviceController:
    """单台设备的控制面：u2 会话 + 坐标映射 + 模板匹配。

    每个 DeviceWorker 线程持有独立实例，禁止跨线程共享。
    """

    def __init__(self, serial: str, cfg: ControlConfig,
                 adb: Optional[AdbManager] = None):
        self.serial = serial
        self.cfg = cfg
        self.adb = adb or AdbManager(cfg.adb_path)
        self.device: Optional[u2.Device] = None
        self.mapper: Optional[CoordinateMapper] = None
        self.matcher: Optional[ImageMatcher] = None
        self.screen_w = self.screen_h = 0
        self.package = cfg.game_package

    # ── 连接 ──

    def connect(self) -> None:
        """建立 uiautomator2 会话 + 初始化坐标映射/模板匹配"""
        state = self.adb.get_state(self.serial)
        if state != "device":
            raise DeviceInitError(
                f"设备 {self.serial} ADB 状态异常: {state}")
        try:
            self.device = u2.connect(self.serial)
            # u2 3.6-: set_new_command_timeout；3.7+: wait_timeout 属性
            if hasattr(self.device, "set_new_command_timeout"):
                self.device.set_new_command_timeout(120)
            else:
                self.device.wait_timeout = 120.0
        except Exception as e:
            raise UiAutomatorError(
                f"uiautomator2 连接失败 {self.serial}: {e}")
        self._rebuild_geometry()

    def _rebuild_geometry(self):
        w, h = self.device.window_size()
        self.screen_w, self.screen_h = int(w), int(h)
        orientation = self.adb.get_orientation(self.serial)
        top, bottom = self.adb.get_stable_insets(self.serial)
        self.mapper = CoordinateMapper(
            width=self.screen_w, height=self.screen_h,
            orientation=orientation,
            insets=ScreenInsets(top=top, bottom=bottom),
        )
        self.matcher = ImageMatcher(
            self.cfg.templates_dir,
            screen_size=(self.screen_w, self.screen_h),
            default_threshold=self.cfg.game_template_threshold,
        )

    def reset(self) -> None:
        """重连 uiautomator2（Level 6 恢复）"""
        try:
            if self.device is not None:
                self.device.reset_uiautomator()
        except Exception:
            pass
        self.connect()

    def disconnect(self):
        try:
            if self.device is not None:
                self.device = None
        except Exception:
            pass

    def is_healthy(self) -> bool:
        """u2 会话存活检查（带超时）"""
        if self.device is None:
            return False
        try:
            self.device.window_size()
            return True
        except Exception:
            return False

    # ── 截图 ──

    def screenshot(self) -> np.ndarray:
        """截图 → BGR numpy 数组（供模板匹配）。

        真机实测: u2 截图在浏览器 Custom Tab 场景下持续失败
        (adbutils screencap error) — 改用 ADB screencap 优先(底层通道),
        u2 截图作为兜底。
        """
        import tempfile
        tmp = Path(tempfile.mktemp(suffix=".png"))
        try:
            try:
                self.adb.screenshot(self.serial, tmp)
                img = cv2.imdecode(np.fromfile(str(tmp), dtype=np.uint8),
                                   cv2.IMREAD_COLOR)
                if img is not None and img.size > 0:
                    return img
            except Exception as e:
                logger.debug(f"[截图] ADB screencap 失败({e}), 降级 u2")
            # u2 兜底
            pil_img = self.device.screenshot()
            return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        finally:
            tmp.unlink(missing_ok=True)

    def save_screenshot(self, path: Path) -> Path:
        """截图保存文件。u2 失败时回退 ADB screencap。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.device.screenshot(str(path))
        except Exception:
            self.adb.screenshot(self.serial, path)
        return path

    # ── UI 层级 ──

    def dump_hierarchy(self) -> str:
        return self.device.dump_hierarchy()

    # ── 点击 / 滑动 ──

    def click(self, x: int, y: int):
        self.device.click(x, y)

    def click_ratio(self, rx: float, ry: float) -> tuple[int, int]:
        x, y = self.mapper.map_ratio(rx, ry)
        self.device.click(x, y)
        return x, y

    def click_base(self, base_x: float, base_y: float) -> tuple[int, int]:
        """按 1080×2400 基准坐标点击（自动适配分辨率）"""
        x, y = self.mapper.map(base_x, base_y)
        self.device.click(x, y)
        return x, y

    def swipe(self, x1: int, y1: int, x2: int, y2: int,
              duration: float = 0.3):
        self.device.swipe(x1, y1, x2, y2, duration=duration)

    def swipe_direction(self, direction: str, distance: float = 0.5):
        """方向滑动: up/down/left/right"""
        w, h = self.screen_w, self.screen_h
        cx, cy = w // 2, h // 2
        d = int(distance * min(w, h))
        routes = {
            "up": (cx, cy + d // 2, cx, cy - d // 2),
            "down": (cx, cy - d // 2, cx, cy + d // 2),
            "left": (cx + d // 2, cy, cx - d // 2, cy),
            "right": (cx - d // 2, cy, cx + d // 2, cy),
        }
        if direction not in routes:
            raise ValueError(f"未知滑动方向: {direction}")
        self.device.swipe(*routes[direction], duration=0.3)

    # ── 按键 / 文本 ──

    def press(self, key: str):
        key = key.lower()
        if key in ("back", "home", "enter"):
            self.device.press(key)
        elif key == "wakeup":
            self.adb.shell(self.serial, "input keyevent KEYCODE_WAKEUP")
        else:
            raise ValueError(f"未知按键: {key}")

    def send_text(self, text: str):
        """向当前焦点输入文本（u2 send_keys 支持中文）"""
        self.device.send_keys(text)

    # ── 应用 ──

    def current_package(self) -> str:
        """当前前台应用包名(ADB 读取, 失败返回空串)"""
        try:
            return self.adb.current_app(self.serial)
        except Exception:
            return ""

    def is_app_running(self) -> bool:
        return self.adb.pidof(self.serial, self.package) > 0

    def app_start(self, package: str = "", activity: str = ""):
        self.device.app_start(package or self.package,
                              activity or None, wait=False)

    def app_stop(self, package: str = ""):
        self.device.app_stop(package or self.package)

    def restart_app(self) -> bool:
        """force-stop → 等待 → 重新启动 → 等待进程出现"""
        self.app_stop()
        time.sleep(2)
        self.app_start()
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.is_app_running():
                return True
            time.sleep(1)
        return False

    def wake_and_unlock(self) -> bool:
        return self.adb.wake_and_unlock(self.serial)


# ── 初始化报告 ──

@dataclass
class InitReport:
    """单台设备初始化检查报告"""
    serial: str
    checks: list = field(default_factory=list)   # [(name, status, detail)]
    passed: bool = False

    def add(self, name: str, status: str, detail: str = ""):
        self.checks.append((name, status, detail))

    def format(self) -> str:
        lines = [f"[设备初始化] {self.serial}"]
        for name, status, detail in self.checks:
            lines.append(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
        return "\n".join(lines)


# ── 设备管理器 ──

class DeviceManager:
    """全局设备管理：扫描 / 初始化 / 会话创建 / 兼容性报告"""

    def __init__(self, cfg: ControlConfig, adb: Optional[AdbManager] = None):
        self.cfg = cfg
        self.adb = adb or AdbManager(cfg.adb_path)
        self._devices: dict[str, AndroidDevice] = {}
        self._controllers: dict[str, DeviceController] = {}

    # ── 扫描 ──

    def scan(self, fast: bool = False) -> list[AndroidDevice]:
        """检测所有 ADB 设备并读取完整信息。

        fast=True(看板周期刷新): 只更新 adb 在线状态,
        复用上次缓存的硬件信息, 避免每 30 秒打一轮 getprop 干扰自动化。
        """
        if fast:
            return self._fast_rescan()
        self._devices = {}
        for row in self.adb.devices():
            device = self._build_device(row)
            self._devices[device.serial] = device
        return list(self._devices.values())

    def _fast_rescan(self) -> list[AndroidDevice]:
        states = {r["serial"]: r.get("state") for r in self.adb.devices()}
        for serial, device in self._devices.items():
            state = states.get(serial)
            if state is None:
                device.adb_state = "missing"
                device.status = DeviceStatus.OFFLINE
            elif state != "device" and device.adb_state == "device":
                device.adb_state = state
                device.status = DeviceStatus.OFFLINE
            elif state == "device" and not device.is_adb_healthy:
                device.adb_state = state
                device.status = (device.status if device.status !=
                                 DeviceStatus.OFFLINE else DeviceStatus.ONLINE)
        # 新出现的设备(冷插)补充完整扫描
        known = set(self._devices)
        new = [r for r in states if r not in known]
        if new:
            self.scan()
        return list(self._devices.values())

    def _build_device(self, row: dict) -> AndroidDevice:
        """复用 device_profiles.DeviceInfo.from_adb 读取硬件信息"""
        from device_profiles import DeviceInfo  # 已有能力复用
        device = AndroidDevice(serial=row["serial"],
                               adb_state=row.get("state", "unknown"))
        if device.adb_state != "device":
            device.status = (DeviceStatus.OFFLINE
                             if device.adb_state == "offline"
                             else DeviceStatus.DEVICE_ERROR)
            return device
        try:
            info = DeviceInfo.from_adb(device.serial, self.adb.path)
            device.manufacturer = info.manufacturer
            device.brand = info.brand
            device.model = info.model
            device.android_version = info.android_version
            device.sdk = info.sdk
            device.width, device.height = info.width, info.height
            device.dpi = info.density
            device.orientation = info.orientation
        except Exception as e:
            logger.warning(f"[设备] {device.serial} 信息读取失败: {e}")
            device.status = DeviceStatus.DEVICE_ERROR
            device.init_error = str(e)
            return device
        device.battery_level = self.adb.get_battery(device.serial)
        device.storage_free_gb = self.adb.get_storage_free_gb(device.serial)
        if self.cfg.is_device_disabled(device.serial):
            device.status = DeviceStatus.DISABLED
        return device

    def get_device(self, serial: str) -> Optional[AndroidDevice]:
        return self._devices.get(serial)

    # ── 初始化 ──

    def init_device(self, device: AndroidDevice,
                    target_package: str = "") -> InitReport:
        """对单台设备执行完整初始化检查。失败只标记 DEVICE_ERROR，不抛异常。"""
        package = target_package or self.cfg.game_package
        report = InitReport(serial=device.serial)
        device.status = DeviceStatus.INITIALIZING
        report.add("adb_online", "PASS" if device.is_adb_healthy else "FAIL",
                   f"state={device.adb_state}")

        controller = self.create_controller(device.serial)
        try:
            controller.connect()
            report.add("u2_connect", "PASS",
                       f"{device.resolution} Android {device.android_version}")
        except Exception as e:
            report.add("u2_connect", "FAIL", str(e))
            self._finish_init(device, report, str(e))
            return report

        # 屏幕点亮
        screen_on = self.adb.is_screen_on(device.serial)
        report.add("screen_on", "PASS" if screen_on else "WARN", "屏幕未点亮")
        if not screen_on:
            controller.wake_and_unlock()
            time.sleep(1)

        # 解锁状态
        lock_dump = self.adb.shell(device.serial,
                                   "dumpsys window | grep mDreamingLockscreen")
        locked = "isShowing=true" in lock_dump
        report.add("unlocked", "PASS" if not locked else "WARN",
                   "检测到锁屏，需人工解锁" if locked else "")

        # 分辨率
        w, h, dpi = self.adb.get_screen(device.serial)
        if w > 0 and h > 0:
            device.width, device.height, device.dpi = w, h, dpi
            report.add("resolution", "PASS", f"{w}x{h} dpi={dpi}")
        else:
            report.add("resolution", "FAIL", "无法读取分辨率")

        # 目标应用
        if package:
            device.app_installed = self.adb.is_app_installed(device.serial,
                                                             package)
            report.add("app_installed",
                       "PASS" if device.app_installed else "FAIL",
                       f"package={package}")
        else:
            report.add("app_installed", "SKIP", "未配置目标应用包名")

        # 截图测试
        try:
            shot = controller.screenshot()
            if shot is None or shot.size == 0:
                raise RuntimeError("空截图")
            report.add("screenshot", "PASS",
                       f"{shot.shape[1]}x{shot.shape[0]}")
        except Exception as e:
            report.add("screenshot", "FAIL", str(e))

        # 点击测试（轻点屏幕中央，验证 input 通道）
        try:
            controller.click(device.width // 2, device.height // 2)
            report.add("click", "PASS", f"tap({device.width // 2},"
                                        f"{device.height // 2})")
        except Exception as e:
            report.add("click", "FAIL", str(e))

        self._finish_init(device, report, "")
        return report

    def _finish_init(self, device: AndroidDevice, report: InitReport,
                     error: str):
        failed = [c for c in report.checks if c[1] == "FAIL"]
        critical = [c[0] for c in failed
                    if c[0] in ("adb_online", "u2_connect", "screenshot")]
        report.passed = not critical
        if report.passed:
            device.status = DeviceStatus.READY
            device.init_error = ""
        else:
            device.status = DeviceStatus.DEVICE_ERROR
            names = "; ".join(critical)
            device.init_error = f"{names} — {error}" if error else names
        logger.info(report.format())

    # ── 会话创建 ──

    def create_controller(self, serial: str) -> DeviceController:
        """获取(或新建)设备的控制会话。每个 Worker 线程各自调用。"""
        if serial not in self._controllers:
            self._controllers[serial] = DeviceController(serial, self.cfg,
                                                         self.adb)
        return self._controllers[serial]

    # ── 兼容性报告 ──

    def compat_report(self, target_package: str = "") -> str:
        """生成设备兼容性报告(Markdown)。未实测的能力标注 NOT TESTED。"""
        devices = self.scan()
        lines = ["# Device Compatibility Report", "",
                 f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}", "",
                 "| Serial | 品牌 | 型号 | Android | 分辨率 | DPI | ADB连接 | "
                 "UIAutomator | 截图测试 | 点击测试 | 兼容状态 |",
                 "|---|---|---|---|---|---|---|---|---|---|---|"]
        for d in devices:
            report = self.init_device(d, target_package) if \
                d.is_adb_healthy else None
            checks = {}
            if report:
                for name, status, _ in report.checks:
                    checks[name] = status
            def st(key):
                v = checks.get(key, "NOT TESTED")
                return {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️",
                        "SKIP": "➖"}.get(v, "❔" if v == "NOT TESTED" else v)
            overall = "✅ 兼容" if report and report.passed else \
                ("❌ 异常" if report else "❔ NOT TESTED")
            lines.append(
                f"| {d.serial} | {d.brand or '-'} | {d.model or '-'} | "
                f"{d.android_version or '-'} | {d.resolution or '-'} | "
                f"{d.dpi or '-'} | {st('adb_online')} | {st('u2_connect')} | "
                f"{st('screenshot')} | {st('click')} | {overall} |")
        lines.append("")
        lines.append("> 说明: ✅=实测通过 ❌=实测失败 ⚠️=有告警 "
                     "❔=未实测(NOT TESTED)。无真机时不标记 PASS。")
        return "\n".join(lines)
