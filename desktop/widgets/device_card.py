"""
desktop/widgets/device_card.py
设备卡片 — 每台手机一张: 型号/Serial/状态/账号/步骤/耗时 + 单设备停止。
兼容任意设备数量(1/2/3/4+, 不写死三台)。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

STATUS_STYLES = {
    "online": ("在线", "#2e7d32", "#e8f5e9"),
    "idle": ("空闲", "#5d4037", "#efebe9"),
    "running": ("运行中", "#1565c0", "#e3f2fd"),
    "stopping": ("正在停止", "#e65100", "#fff3e0"),
    "stopped": ("已停止", "#616161", "#eeeeee"),
    "error": ("错误", "#c62828", "#ffebee"),
    "offline": ("ADB离线", "#c62828", "#ffebee"),
    "unauthorized": ("未授权", "#c62828", "#ffebee"),
    "connecting": ("连接中", "#1565c0", "#e3f2fd"),
}

STATUS_BY_SNAPSHOT = {
    # snapshot status.value → 展示 key
    "ONLINE": "online",
    "READY": "idle",
    "RUNNING": "running",
    "DEVICE_ERROR": "error",
    "DISABLED": "stopped",
}


class DeviceCard(QFrame):
    """单台设备的实时状态卡片。update_snapshot() 由主窗口定时调用。"""

    stop_requested = Signal(str)      # serial — 点击「停止」

    def __init__(self, serial: str, index: int, parent=None):
        super().__init__(parent)
        self.serial = serial
        self.index = index
        self.setObjectName("deviceCard")
        self.setStyleSheet(
            "QFrame#deviceCard { border: 1px solid #bdbdbd; "
            "border-radius: 6px; background: #fafafa; }")
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # 标题行: 设备编号 + 型号 + 状态徽章
        top = QHBoxLayout()
        self.title = QLabel(f"设备{self.index:02d}")
        self.title.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.model = QLabel("—")
        self.model.setStyleSheet("color: #616161;")
        self.badge = QLabel("未连接")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setMinimumWidth(64)
        self.badge.setStyleSheet(
            "padding: 2px 8px; border-radius: 9px; background: #eeeeee;")
        top.addWidget(self.title)
        top.addWidget(self.model, 1)
        top.addWidget(self.badge)
        layout.addLayout(top)

        # 信息网格
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        self.serial_label = QLabel("Serial: —")
        self.serial_label.setStyleSheet("color: #616161; font-size: 12px;")
        grid.addWidget(self.serial_label, 0, 0, 1, 2)
        self.account_label = QLabel("账号: —")
        self.step_label = QLabel("步骤: —")
        self.elapsed_label = QLabel("耗时: —")
        grid.addWidget(self.account_label, 1, 0)
        grid.addWidget(self.step_label, 1, 1)
        grid.addWidget(self.elapsed_label, 2, 0, 1, 2)
        layout.addLayout(grid)

        # 卡死提示 + 停止按钮
        bottom = QHBoxLayout()
        self.stall_label = QLabel("")
        self.stall_label.setWordWrap(True)
        self.stall_label.setStyleSheet("color: #e65100; font-size: 12px;")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setFixedWidth(64)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(
            lambda: self.stop_requested.emit(self.serial))
        bottom.addWidget(self.stall_label, 1)
        bottom.addWidget(self.stop_btn)
        layout.addLayout(bottom)

    def update_snapshot(self, snap: dict, index: int):
        """snap: scheduler.snapshot()['devices'] 中的一条"""
        self.index = index
        self.title.setText(f"设备{index:02d}")
        model = snap.get("model") or "-"
        self.model.setText(model)
        self.serial_label.setText(f"Serial: {snap.get('serial', '—')}")

        adb_state = snap.get("adb_state", "")
        status = snap.get("status", "")
        key = STATUS_BY_SNAPSHOT.get(status, "offline")
        # 有账号在跑 → 运行中优先
        if snap.get("account") or snap.get("worker_running"):
            key = "running"
        elif adb_state == "device" and snap.get("ready"):
            key = "idle"      # ADB 在线且初始化通过 → READY
        elif adb_state == "device":
            key = "online"    # ADB 在线(初始化未做/进行中)
        elif adb_state == "unauthorized":
            key = "unauthorized"
        elif adb_state in ("offline", "no permissions"):
            key = "offline"
        elif adb_state == "missing":
            key = "offline"
        text, fg, bg = STATUS_STYLES[key]
        self.badge.setText(text)
        self.badge.setStyleSheet(
            f"padding: 2px 8px; border-radius: 9px; "
            f"color: {fg}; background: {bg}; font-weight: bold;")

        account = snap.get("account") or "—"
        self.account_label.setText(f"账号: {account}")
        state = snap.get("worker_state") or "-"
        self.step_label.setText(f"步骤: {state}")
        self.elapsed_label.setText(f"耗时: {self._fmt(snap.get('last_duration', 0))}")

        # 非 device 状态显示具体原因/操作提示(unauthorized/offline 等)
        reject = snap.get("reject_reason") or ""
        error = snap.get("error") or ""
        if reject:
            self.stall_label.setText(f"⚠ {reject}")
        elif error:
            self.stall_label.setText(f"⚠ {error}")
        else:
            self.stall_label.setText("")
        # 停止按钮: 只有 worker 在跑才可点
        self.stop_btn.setEnabled(bool(snap.get("account"))
                                 or status == "RUNNING")

    @staticmethod
    def _fmt(sec: float) -> str:
        if not sec or sec <= 0:
            return "—"
        return f"{sec:.1f}秒"
