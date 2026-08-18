"""
scripts/doctor.py
环境诊断工具 — 检查 Python/ADB/依赖/设备/应用/存储/配置

用法: python main.py doctor         或 python scripts/doctor.py
"""
from __future__ import annotations

import importlib
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import ControlConfig


class Doctor:
    """环境体检。每项检查返回 (状态, 详情)，状态: PASS/FAIL/WARN/SKIP"""

    def __init__(self, cfg: ControlConfig = None):
        self.cfg = cfg or ControlConfig.load()
        self.results: list[tuple[str, str, str]] = []
        self.adb = None

    def add(self, name: str, ok: bool, detail: str = "",
            warn: bool = False, skip: bool = False):
        status = "SKIP" if skip else ("WARN" if warn
                                      else ("PASS" if ok else "FAIL"))
        self.results.append((name, status, detail))
        return ok

    # ── 各项检查 ──

    def check_python(self):
        v = platform.python_version()
        major, minor = (int(x) for x in v.split(".")[:2])
        self.add("Python 版本", major >= 3 and minor >= 11, f"Python {v}",
                 warn=(major == 3 and minor < 11))

    def check_adb(self):
        from core.adb_manager import AdbManager
        try:
            self.adb = AdbManager(self.cfg.adb_path)
            version = self.adb.version()
            self.add("ADB 可执行文件", True, f"{self.adb.path}")
            self.add("ADB 版本", True, version)
        except Exception as e:
            self.add("ADB 可执行文件", False, str(e))
            return
        try:
            self.adb._run([self.adb.path, "start-server"], timeout=15)
            self.add("ADB Server", True)
        except Exception as e:
            self.add("ADB Server", False, str(e))

    def check_dependencies(self):
        for mod, label in [
            ("uiautomator2", "uiautomator2"),
            ("cv2", "OpenCV"),
            ("yaml", "PyYAML"),
            ("PIL", "Pillow"),
            ("pandas", "pandas"),
            ("openpyxl", "openpyxl"),
            ("fastapi", "FastAPI"),
            ("uvicorn", "uvicorn"),
            ("requests", "requests"),
        ]:
            try:
                m = importlib.import_module(mod)
                version = getattr(m, "__version__", "")
                self.add(f"依赖 {label}", True, version or "已安装")
            except ImportError:
                self.add(f"依赖 {label}", False, "未安装")
        # OCR 可选
        try:
            importlib.import_module("paddleocr")
            self.add("OCR (paddleocr)", True, "可选能力已启用")
        except ImportError:
            self.add("OCR (paddleocr)", True, "未安装(可选，不影响核心功能)",
                     skip=True)

    def check_config(self):
        for name in ("config.yaml", "game.yaml", "devices.yaml"):
            path = self.cfg.config_dir / name
            self.add(f"配置文件 {name}", path.exists(), str(path))

    def check_dirs(self):
        for name, path in [("日志目录", self.cfg.logs_dir),
                           ("截图目录", self.cfg.screenshots_dir),
                           ("模板目录", self.cfg.templates_dir),
                           ("数据目录", self.cfg.data_dir)]:
            try:
                path.mkdir(parents=True, exist_ok=True)
                probe = path / ".doctor_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                self.add(name, True, str(path))
            except Exception as e:
                self.add(name, False, str(e))

    def check_database(self):
        from storage.database import Database
        try:
            db = Database(self.cfg.db_path)
            db.execute("SELECT 1")
            db.close()
            self.add("数据库", True, str(self.cfg.db_path))
        except Exception as e:
            self.add("数据库", False, str(e))

    def check_devices(self) -> list:
        """检查每台设备: ADB 状态/授权/u2/截图/目标应用。返回设备列表。"""
        from core.device_manager import DeviceManager
        manager = DeviceManager(self.cfg)
        devices = manager.scan()
        if not devices:
            self.add("ADB 设备", False, "未检测到任何设备")
            return []
        self.add("ADB 设备", True, f"检测到 {len(devices)} 台")
        package = self.cfg.game_package
        for d in devices:
            tag = f"设备 {d.serial}"
            if not d.is_adb_healthy:
                if d.adb_state == "unauthorized":
                    self.add(tag, False, "未授权 — 手机上点「允许 USB 调试」")
                else:
                    self.add(tag, False, f"adb state={d.adb_state}")
                continue
            self.add(tag, True,
                     f"{d.brand} {d.model} Android {d.android_version} "
                     f"{d.resolution}")
            # u2 连接
            try:
                controller = manager.create_controller(d.serial)
                controller.connect()
                self.add(f"  uiautomator2", True, "连接成功")
            except Exception as e:
                self.add(f"  uiautomator2", False, str(e))
                continue
            # 截图
            try:
                shot = controller.screenshot()
                if shot is None or shot.size == 0:
                    raise RuntimeError("空截图")
                self.add(f"  截图", True, f"{shot.shape[1]}x{shot.shape[0]}")
            except Exception as e:
                self.add(f"  截图", False, str(e))
            # 目标应用
            if package:
                installed = self.adb.is_app_installed(d.serial, package)
                self.add(f"  目标应用 {package}", installed,
                         "已安装" if installed else "未安装")
            else:
                self.add("  目标应用", True, "game.yaml 未配置包名", skip=True)
        return devices

    # ── 运行 ──

    def run(self) -> int:
        print("=" * 70)
        print("Android 多设备游戏自动化中控 — 环境诊断 (doctor)")
        print("=" * 70)
        self.check_python()
        self.check_adb()
        self.check_dependencies()
        self.check_config()
        self.check_dirs()
        self.check_database()
        self.check_devices()

        print()
        for name, status, detail in self.results:
            icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]",
                    "SKIP": "[SKIP]"}[status]
            print(f"{icon} {name}" + (f" — {detail}" if detail else ""))
        print()
        fails = [r for r in self.results if r[1] == "FAIL"]
        warns = [r for r in self.results if r[1] == "WARN"]
        print(f"结果: {len(self.results) - len(fails)} 项通过, "
              f"{len(fails)} 项失败, {len(warns)} 项告警")
        return 1 if fails else 0


def run_doctor() -> int:
    return Doctor().run()


if __name__ == "__main__":
    sys.exit(run_doctor())
