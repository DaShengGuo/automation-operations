"""
desktop/app.py
GUI 入口 — 欢迎使用宝可梦自动化购买脚本。

启动流程:
  DesktopAppController(数据目录分离/迁移/文件日志)
  → QtLogHandler 接入日志 → 主窗口
  ApplicationRunState = STOPPED — 绝不自动运行, 等待用户点击。
"""
from __future__ import annotations

import logging
import sys

from version import APP_TITLE, APP_VERSION


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from core.logger import StructuredFormatter
    from desktop.controller import DesktopAppController
    from desktop.qt_bridge import QtEventBus, QtLogHandler
    from desktop.main_window import MainWindow

    # 客户版硬性要求: 全程无 CMD 黑框 — 在创建任何子进程前安装
    # 全局隐藏补丁(覆盖 uiautomator2/adbutils 内部的裸 subprocess 调用)
    from desktop.process_runner import install_global_hidden_patch
    install_global_hidden_patch()
    # 冻结环境资源补丁(u2.jar 定位等)
    from desktop.frozen_compat import apply_frozen_patches
    apply_frozen_patches()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)

    # 事件总线 + 日志桥(root logger 的所有输出进入 GUI)
    bus = QtEventBus()
    handler = QtLogHandler(bus)
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    controller = DesktopAppController(bus=bus)
    window = MainWindow(controller, bus)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
