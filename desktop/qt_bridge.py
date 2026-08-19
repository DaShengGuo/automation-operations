"""
desktop/qt_bridge.py
Qt 事件桥 — Worker 线程 / 日志系统 → Qt 信号(GUI 线程消费)。

原则:
  - Worker 只产生事件(日志/快照), GUI 异步消费 — Worker 绝不因 GUI 阻塞
  - 日志经 logging.Handler 接入: 跨线程 emit 由 Qt 队列连接自动处理
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal


class QtEventBus(QObject):
    """桌面应用事件总线(唯一)。GUI 只订阅这些信号。"""

    log = Signal(str)                     # 实时日志行(已格式化)
    error_report = Signal(dict)           # 错误事件(设备/账号/状态/错误)
    app_state = Signal(str)               # ApplicationRunState.value
    toast = Signal(str, str)              # (级别, 消息) — 弹窗/状态栏提示
    devices_changed = Signal(list)        # 设备注册表热插拔变化(record dict 列表)
    vpn_warning = Signal(str, str)        # (serial, detail) — 设备 VPN 未检测到
    queue_changed = Signal(str)           # (serial) — 账号队列变化(第 58 节: 事件驱动刷新)


class QtLogHandler(logging.Handler):
    """把项目日志转发到 GUI(实时运行日志)。

    GUI 只保留最近 N 行(2000~5000), 完整日志永久写文件/数据库。
    """

    def __init__(self, bus: QtEventBus, max_lines: int = 3000):
        super().__init__()
        self.bus = bus
        self.max_lines = max_lines
        self._lines: list[str] = []

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        try:
            self.bus.log.emit(msg)
        except RuntimeError:
            pass  # 应用关闭中, GUI 已销毁
        self._lines.append(msg)
        if len(self._lines) > self.max_lines:
            self._lines = self._lines[-self.max_lines:]

    def recent(self) -> list[str]:
        return list(self._lines)
