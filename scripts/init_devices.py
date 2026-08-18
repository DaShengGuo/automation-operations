"""
scripts/init_devices.py
初始化所有(或指定)设备: 亮屏/解锁/分辨率/截图/点击等基础检查

用法: python scripts/init_devices.py [--device SERIAL]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import ControlConfig
from core.device_manager import DeviceManager
from core.logger import setup_logging


def init_devices(serial: str = "") -> int:
    cfg = ControlConfig.load()
    setup_logging(cfg.logs_dir, cfg.log_level)
    manager = DeviceManager(cfg)
    devices = manager.scan()

    targets = [d for d in devices
               if (not serial or d.serial == serial)]
    if not targets:
        print(f"未找到设备" + (f": {serial}" if serial else ""))
        return 1

    exit_code = 0
    for d in targets:
        if not d.is_adb_healthy:
            print(f"[SKIP] {d.serial} ADB 状态异常: {d.adb_state}")
            exit_code = 1
            continue
        report = manager.init_device(d)
        print(report.format())
        if not report.passed:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化 Android 设备")
    parser.add_argument("--device", default="", help="指定设备 serial")
    args = parser.parse_args()
    sys.exit(init_devices(args.device))
