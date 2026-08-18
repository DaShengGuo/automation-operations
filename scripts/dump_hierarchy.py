"""
scripts/dump_hierarchy.py
导出设备当前页面的 UI 层级 XML — 用于游戏页面标定

用法:
  python scripts/dump_hierarchy.py <SERIAL> [--out PATH]

在导出的 XML 中搜索按钮文本/描述:
  <node text="登录" resource-id="com.xxx:id/login_btn" content-desc="..." .../>
把 text / resource-id / content-desc 填入 config/game.yaml。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import ControlConfig
from core.device_manager import DeviceManager


def dump_hierarchy(serial: str, out: Path) -> int:
    cfg = ControlConfig.load()
    manager = DeviceManager(cfg)
    controller = manager.create_controller(serial)
    controller.connect()
    xml = controller.dump_hierarchy()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(xml, encoding="utf-8")
    print(f"[OK] UI 层级已导出: {out} ({len(xml)} 字符)")
    print(f"提示: 在其中搜索 text= / content-desc= / resource-id= "
          f"填入 config/game.yaml")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导出设备 UI 层级 XML")
    parser.add_argument("serial", help="设备序列号(adb devices 查看)")
    parser.add_argument("--out", default="", help="输出路径")
    args = parser.parse_args()
    out = Path(args.out) if args.out else Path("data") / f"ui_{args.serial}.xml"
    sys.exit(dump_hierarchy(args.serial, out))
