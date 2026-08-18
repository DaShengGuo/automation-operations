"""
core/adb_manager.py
ADB 命令管理层 — 所有 subprocess 调用集中在此，业务代码不直接调 adb

复用 device_profiles.DeviceInfo.from_adb 的设备信息读取逻辑。

所有 ADB 命令经 desktop.process_runner.run_hidden_process 隐藏执行
(客户版要求: 全程无 CMD 黑框); ADB 路径经 AdbLocator 统一解析
(捆绑优先, 注入 ADBUTILS_ADB_PATH 防止多版本 adb 打架)。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from core.exceptions import AdbError

logger = logging.getLogger(__name__)


class AdbManager:
    """ADB 封装：设备发现 / 属性读取 / 输入 / 截图"""

    def __init__(self, adb_path: Optional[str] = None):
        self.path = adb_path or self._resolve_adb()

    @staticmethod
    def _resolve_adb() -> str:
        """ADB 路径: 统一走 AdbLocator(捆绑 > ADB_PATH > PATH)"""
        try:
            from desktop.adb_locator import AdbLocator
            return AdbLocator.resolve()
        except Exception:
            # 兜底(desktop 层不可用时): 保留原候选链
            project = Path(__file__).resolve().parent.parent
            candidates = [
                os.environ.get("ADB_PATH", ""),
                str(project / "adb" / "platform-tools" / "adb.exe"),
                str(project / "adb" / "platform-tools" / "adb"),
                "adb",
            ]
            for p in candidates:
                if p and (p == "adb" or Path(p).exists()):
                    return p
            return "adb"

    # ── 基础 ──

    def _run(self, args: list, timeout: float = 15, check: bool = False
             ) -> "subprocess.CompletedProcess":
        import subprocess
        from desktop.process_runner import run_hidden_process
        try:
            # ADB 在中文 Windows 输出 GBK 字节，强制按 UTF-8 + replace 解码;
            # run_hidden_process 注入 STARTF_USESHOWWINDOW/SW_HIDE
            # + CREATE_NO_WINDOW, 全程无 CMD 黑框。
            r = run_hidden_process(args, timeout=timeout,
                                   encoding="utf-8", errors="replace")
        except FileNotFoundError:
            raise AdbError(f"ADB 可执行文件不存在: {self.path}")
        except subprocess.TimeoutExpired:
            raise AdbError(f"ADB 命令超时: {' '.join(args[:4])}...")
        if check and r.returncode != 0:
            raise AdbError(
                f"ADB 命令失败({r.returncode}): {' '.join(args[:4])}... "
                f"{r.stderr.strip()[:200]}")
        return r

    def version(self) -> str:
        r = self._run([self.path, "version"], timeout=10)
        return r.stdout.strip().splitlines()[0] if r.stdout.strip() else "unknown"

    # ── 设备发现 ──

    def devices(self) -> list[dict]:
        """解析 `adb devices -l` → [{serial, state, product, model, device}]

        解析逻辑在 desktop.adb_locator.parse_devices_output(纯函数,
        CRLF/Unicode/空格/编码已覆盖测试)。
        """
        from desktop.adb_locator import parse_devices_output
        r = self._run([self.path, "devices", "-l"], timeout=10)
        return parse_devices_output(r.stdout)

    def get_state(self, serial: str) -> str:
        """device / offline / unauthorized / missing"""
        r = self._run([self.path, "-s", serial, "get-state"], timeout=8)
        return r.stdout.strip() or "missing"

    def wait_online(self, serial: str, timeout: float = 60) -> bool:
        """等待设备回到 device 状态（用于 ADB 断连恢复）"""
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.get_state(serial)
            if state == "device":
                return True
            time.sleep(2)
        return False

    # ── 属性 / 信息 ──

    def shell(self, serial: str, cmd: str, timeout: float = 15) -> str:
        r = self._run([self.path, "-s", serial, "shell"] + cmd.split(),
                      timeout=timeout)
        return r.stdout.strip()

    def getprop(self, serial: str, prop: str) -> str:
        return self.shell(serial, f"getprop {prop}", timeout=10)

    def get_screen(self, serial: str) -> tuple[int, int, int]:
        """→ (width, height, density)"""
        w = h = d = 0
        wm = self.shell(serial, "wm size", timeout=10)
        if "x" in wm:
            try:
                w, h = (int(v) for v in wm.split(":")[-1].strip().split("x"))
            except ValueError:
                pass
        density = self.shell(serial, "wm density", timeout=10)
        try:
            d = int(density.split(":")[-1].strip())
        except ValueError:
            pass
        return w, h, d

    def get_orientation(self, serial: str) -> str:
        dump = self.shell(serial, "dumpsys input", timeout=10)
        # SurfaceOrientation: 0/2=portrait 1/3=landscape
        for line in dump.splitlines():
            if "SurfaceOrientation" in line:
                val = line.split(":")[-1].strip()
                return "landscape" if val in ("1", "3") else "portrait"
        return "portrait"

    def get_battery(self, serial: str) -> int:
        """电池电量百分比，失败返回 -1"""
        out = self.shell(serial, "dumpsys battery", timeout=10)
        for line in out.splitlines():
            if "level:" in line:
                try:
                    return int(line.split(":")[1].strip())
                except ValueError:
                    pass
        return -1

    def get_storage_free_gb(self, serial: str) -> float:
        """/data 剩余空间(GB)，失败返回 -1"""
        out = self.shell(serial, "df /data", timeout=10)
        # Filesystem ... /dev/block/... 110G 80G 30G 73% /data
        parts = out.split()
        if len(parts) >= 6:
            try:
                kb = float(parts[3].rstrip("KMG"))
                unit = parts[3][-1]
                mult = {"K": 1e-6, "M": 1e-3, "G": 1.0}.get(unit, 1e-3)
                return round(kb * mult, 1)
            except ValueError:
                pass
        return -1.0

    def get_stable_insets(self, serial: str) -> tuple[int, int]:
        """状态栏/导航栏高度 (top, bottom)，失败 (0,0)"""
        out = self.shell(serial, "dumpsys window", timeout=15)
        import re
        m = re.search(
            r"mStableInsets=Rect\(([-\d]+),\s*([-\d]+)\s*-\s*([-\d]+),\s*([-\d]+)\)",
            out)
        if m:
            return max(0, int(m.group(2))), max(0, int(m.group(4)))
        return 0, 0

    # ── 电源 / 屏幕 ──

    def is_screen_on(self, serial: str) -> bool:
        out = self.shell(serial, "dumpsys power", timeout=10)
        for line in out.splitlines():
            if "mWakefulness=" in line:
                return "Awake" in line
            if "Display Power: state=" in line:
                return "ON" in line
        return True  # 无法判断时假定亮屏，避免误隔离

    def wake_and_unlock(self, serial: str) -> bool:
        """亮屏 + 滑动解锁（无锁屏密码的设备）"""
        try:
            self.shell(serial, "input keyevent KEYCODE_WAKEUP", timeout=8)
            self.shell(serial, "wm dismiss-keyguard", timeout=8)
            return True
        except Exception:
            return False

    # ── 应用管理 ──

    def is_app_installed(self, serial: str, package: str) -> bool:
        out = self.shell(serial, f"pm list packages {package}", timeout=15)
        return package in out

    def force_stop(self, serial: str, package: str):
        self.shell(serial, f"am force-stop {package}", timeout=10)

    def launch_app(self, serial: str, package: str, activity: str = ""):
        """am start 启动应用"""
        target = f"{package}/{activity}" if activity else package
        self.shell(serial, f"am start -n {target}", timeout=15)

    def current_app(self, serial: str) -> str:
        """当前前台应用包名，失败返回空串"""
        out = self.shell(serial, "dumpsys activity activities", timeout=15)
        for line in out.splitlines():
            if "topResumedActivity" in line or "mResumedActivity" in line:
                # ... com.xxx/.MainActivity 或 org.mozilla.firefox/.Activity ...
                parts = line.split()
                for p in parts:
                    # 组件名约定 package/activity。不限 com. 前缀 —
                    # Firefox 是 org.mozilla.firefox, 部分厂商包名也非 com.
                    pkg = p.split("/")[0]
                    if "/" in p and "." in pkg and "{" not in p:
                        return pkg
        return ""

    def pidof(self, serial: str, package: str) -> int:
        """进程 PID，未运行返回 0"""
        out = self.shell(serial, f"pidof {package}", timeout=8)
        try:
            return int(out.split()[0])
        except (ValueError, IndexError):
            return 0

    # ── 输入 / 按键 ──

    def press_key(self, serial: str, keycode: int):
        self.shell(serial, f"input keyevent {keycode}", timeout=8)

    def press_back(self, serial: str):
        self.press_key(serial, 4)

    def press_home(self, serial: str):
        self.press_key(serial, 3)

    def input_text(self, serial: str, text: str):
        """adb input text（仅 ASCII，中文走 uiautomator2）"""
        escaped = text.replace(" ", "%s").replace("'", "\\'")
        self.shell(serial, f"input text {escaped}", timeout=8)

    def tap(self, serial: str, x: int, y: int):
        self.shell(serial, f"input tap {x} {y}", timeout=8)

    def swipe(self, serial: str, x1: int, y1: int, x2: int, y2: int,
              duration_ms: int = 300):
        self.shell(serial, f"input swipe {x1} {y1} {x2} {y2} {duration_ms}",
                   timeout=10)

    # ── 截图 ──

    def screenshot(self, serial: str, dest: Path) -> Path:
        """exec-out screencap 截图到本地文件（u2 断连时的兜底）"""
        import subprocess
        from desktop.process_runner import run_hidden_process
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = run_hidden_process([self.path, "-s", serial, "exec-out",
                                "screencap", "-p"], timeout=20)
        if r.returncode != 0 or not r.stdout:
            raise AdbError(f"设备 {serial} ADB 截图失败")
        dest.write_bytes(r.stdout)
        return dest
