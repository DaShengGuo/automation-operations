"""
scripts/check_devices.py
检测所有 ADB 设备并打印信息

用法: python scripts/check_devices.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import ControlConfig
from core.device_manager import DeviceManager
from core.logger import setup_logging


def check_devices() -> int:
    cfg = ControlConfig.load()
    setup_logging(cfg.logs_dir, cfg.log_level)
    manager = DeviceManager(cfg)
    devices = manager.scan()

    print("=" * 70)
    print("ADB 设备检测")
    print("=" * 70)
    if not devices:
        print("未检测到任何 ADB 设备。请检查:")
        print("  1. 手机已开启 USB 调试")
        print("  2. 数据线可传数据(非纯充电线)")
        print("  3. 手机弹出的授权弹窗已点「允许」")
        return 1

    print(f"共检测到 {len(devices)} 台设备:\n")
    for d in devices:
        state = {"device": "✅ 在线", "offline": "❌ 离线",
                 "unauthorized": "⚠️ 未授权",
                 "missing": "❓ 未知"}.get(d.adb_state, d.adb_state)
        print(f"  [{state}] {d.serial}")
        if d.is_adb_healthy:
            print(f"    {d.brand} {d.model}  Android {d.android_version} "
                  f"({d.sdk})  {d.resolution}  dpi={d.dpi}  "
                  f"方向={d.orientation}")
            if d.battery_level >= 0:
                print(f"    电池={d.battery_level}%  剩余存储="
                      f"{d.storage_free_gb}GB")
        elif d.adb_state == "unauthorized":
            print("    → 请在手机上点击「允许 USB 调试」")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(check_devices())
