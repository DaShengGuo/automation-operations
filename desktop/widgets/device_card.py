"""
desktop/widgets/device_card.py
设备卡片(v1.2.0) — 每台手机一张全宽卡片:
  状态徽章/Serial/型号 + 当前账号/步骤/耗时
  + 本设备人工账号队列表格(序号/账号/状态/加入时间/重试)
  + 加号输入行(密码默认隐藏 👁 可切换, 支持插到队首)
  + 批量添加/编辑/删除/上移/下移/置顶/清空待执行
  + [开始][停止][重新识别][重置设备环境]

安全: 队列表格绝不显示密码明文(第 14-17 节) — 密码仅存在于
内存 AccountTask, 快照不携带。
兼容任意设备数量(1/2/3/4+, 不写死三台)。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

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
    # 设备环境重置(人工触发) — 优先于其他状态显示
    "resetting": ("正在重置环境...", "#e65100", "#fff3e0"),
    "reset_failed": ("重置失败", "#c62828", "#ffebee"),
    # 设备运行模式(第 57 节)
    "waiting_account": ("等待账号", "#6a1b9a", "#f3e5f5"),
    "paused": ("已暂停", "#e65100", "#fff3e0"),
}

STATUS_BY_SNAPSHOT = {
    # snapshot status.value → 展示 key
    "ONLINE": "online",
    "READY": "idle",
    "RUNNING": "running",
    "DEVICE_ERROR": "error",
    "DISABLED": "stopped",
}

RUN_MODE_STYLES = {
    "DISABLED": "stopped",
    "WAITING_FOR_ACCOUNT": "waiting_account",
    "RUNNING_ACCOUNT": "running",
    "PAUSED": "paused",
    "RESETTING": "resetting",
    "ERROR": "error",
}

# 队列表格列
TABLE_COLUMNS = ["#", "账号", "状态", "加入时间", "重试"]


class DeviceCard(QFrame):
    """单台设备的实时状态卡片 + 人工账号队列(第 5-47 节)。"""

    start_requested = Signal(str)        # serial — 点击「开始」
    stop_requested = Signal(str)         # serial — 点击「停止」
    reidentify_requested = Signal(str)   # serial — 点击「重新识别」
    reset_requested = Signal(str)        # serial — 点击「重置设备环境」
    add_requested = Signal(str, str, str, bool)   # (serial, user, pwd, to_front)
    batch_requested = Signal(str)        # serial — 批量添加
    edit_requested = Signal(str, int)    # (serial, task_id)
    delete_requested = Signal(str, int)  # (serial, task_id)
    move_requested = Signal(str, int, str)   # (serial, task_id, up/down)
    to_front_requested = Signal(str, int)    # (serial, task_id)
    clear_requested = Signal(str)        # serial — 清空待执行

    def __init__(self, serial: str, index: int, parent=None):
        super().__init__(parent)
        self.serial = serial
        self.index = index
        self._notice = ""                  # show_notice 的临时提示
        self._notice_until = 0.0           # 过期时间戳(秒)
        self._table_fingerprint = ""       # 队列表格内容指纹(防每帧重建)
        self._rows: dict[int, int] = {}    # task_id → 表格行号
        self.setObjectName("deviceCard")
        self.setStyleSheet(
            "QFrame#deviceCard { border: 1px solid #bdbdbd; "
            "border-radius: 6px; background: #fafafa; }")
        self._build()

    # ── 布局 ──

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

        # 信息网格: Serial / 账号 / 步骤 / 耗时
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

        # 队列统计(第 30 节: 每设备 等待/成功/失败)
        self.queue_stats = QLabel("队列: 等待 0 | 待重试 0 | 完成 0 | 失败 0")
        self.queue_stats.setStyleSheet("color: #1565c0; font-size: 12px;")
        layout.addWidget(self.queue_stats)

        # 队列表格(第 6 节: 当前账号/步骤 + 等待账号列表)
        self.table = QTableWidget(0, len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setMinimumHeight(96)
        self.table.setMaximumHeight(200)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self._on_row_double_clicked)
        layout.addWidget(self.table)

        # 加号行(第 5 节): 账号 / 密码(默认隐藏 + 👁) / 插到队首 / 添加
        add_row = QHBoxLayout()
        self.add_user = QLineEdit()
        self.add_user.setPlaceholderText("账号")
        self.add_user.setMinimumWidth(150)
        self.add_user.returnPressed.connect(self._on_add_clicked)
        self.add_pwd = QLineEdit()
        self.add_pwd.setPlaceholderText("密码")
        self.add_pwd.setEchoMode(QLineEdit.Password)   # 默认隐藏(第 7 节)
        self.add_pwd.setMinimumWidth(150)
        self.add_pwd.returnPressed.connect(self._on_add_clicked)
        self.pwd_eye = QPushButton("👁")
        self.pwd_eye.setFixedWidth(34)
        self.pwd_eye.setCheckable(True)
        self.pwd_eye.toggled.connect(self._on_eye_toggled)
        self.front_check = QCheckBox("插到队首")
        self.front_check.setToolTip(
            "插到队首: 当前账号完成后的下一条执行(不打断当前账号)")
        self.add_btn = QPushButton("添加")
        self.add_btn.clicked.connect(self._on_add_clicked)
        self.batch_btn = QPushButton("批量添加")
        self.batch_btn.clicked.connect(
            lambda: self.batch_requested.emit(self.serial))
        add_row.addWidget(self.add_user, 2)
        add_row.addWidget(self.add_pwd, 2)
        add_row.addWidget(self.pwd_eye)
        add_row.addWidget(self.front_check)
        add_row.addWidget(self.add_btn)
        add_row.addWidget(self.batch_btn)
        layout.addLayout(add_row)

        # 队列编辑行(作用于表格选中行)
        edit_row = QHBoxLayout()
        self.edit_btn = QPushButton("编辑")
        self.edit_btn.clicked.connect(self._on_edit_clicked)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        self.up_btn = QPushButton("上移")
        self.up_btn.clicked.connect(lambda: self._on_move_clicked("up"))
        self.down_btn = QPushButton("下移")
        self.down_btn.clicked.connect(lambda: self._on_move_clicked("down"))
        self.to_front_btn = QPushButton("插到队首")
        self.to_front_btn.clicked.connect(self._on_to_front_clicked)
        self.clear_btn = QPushButton("清空待执行")
        self.clear_btn.setStyleSheet(
            "color: #c62828; border: 1px solid #ef9a9a; border-radius: 3px;")
        self.clear_btn.clicked.connect(
            lambda: self.clear_requested.emit(self.serial))
        for b in (self.edit_btn, self.delete_btn, self.up_btn, self.down_btn,
                  self.to_front_btn, self.clear_btn):
            b.setFixedHeight(24)
        edit_row.addWidget(QLabel("选中行:"))
        edit_row.addWidget(self.edit_btn)
        edit_row.addWidget(self.delete_btn)
        edit_row.addWidget(self.up_btn)
        edit_row.addWidget(self.down_btn)
        edit_row.addWidget(self.to_front_btn)
        edit_row.addWidget(self.clear_btn)
        edit_row.addStretch(1)
        layout.addLayout(edit_row)

        # 提示 + 开始/停止/重新识别/重置按钮
        bottom = QHBoxLayout()
        self.stall_label = QLabel("")
        self.stall_label.setWordWrap(True)
        self.stall_label.setStyleSheet("color: #e65100; font-size: 12px;")
        self.start_btn = QPushButton("开始")
        self.start_btn.setFixedWidth(56)
        self.start_btn.setEnabled(False)
        self.start_btn.setStyleSheet(
            "background: #2e7d32; color: white; border-radius: 3px; "
            "font-weight: bold;")
        self.start_btn.clicked.connect(
            lambda: self.start_requested.emit(self.serial))
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setFixedWidth(56)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(
            lambda: self.stop_requested.emit(self.serial))
        self.reidentify_btn = QPushButton("重新识别")
        self.reidentify_btn.setFixedWidth(68)
        self.reidentify_btn.setEnabled(False)
        self.reidentify_btn.clicked.connect(
            lambda: self.reidentify_requested.emit(self.serial))
        self.reset_btn = QPushButton("重置设备环境")
        self.reset_btn.setFixedWidth(96)
        self.reset_btn.setEnabled(False)
        self.reset_btn.setStyleSheet(
            "color: #c62828; border: 1px solid #ef9a9a; border-radius: 3px;")
        self.reset_btn.clicked.connect(
            lambda: self.reset_requested.emit(self.serial))
        bottom.addWidget(self.stall_label, 1)
        bottom.addWidget(self.start_btn)
        bottom.addWidget(self.stop_btn)
        bottom.addWidget(self.reidentify_btn)
        bottom.addWidget(self.reset_btn)
        layout.addLayout(bottom)

    # ── 加号行 ──

    def _on_eye_toggled(self, checked: bool):
        """👁 切换密码可见性(第 7 节)。"""
        self.add_pwd.setEchoMode(
            QLineEdit.Normal if checked else QLineEdit.Password)

    def _on_add_clicked(self):
        username = self.add_user.text().strip()
        password = self.add_pwd.text()
        self.add_requested.emit(self.serial, username, password,
                                self.front_check.isChecked())

    def clear_add_inputs(self):
        self.add_user.clear()
        self.add_pwd.clear()

    # ── 表格 ──

    def _on_row_double_clicked(self, index):
        task_id = self._row_task_id(index.row())
        if task_id is not None:
            self.edit_requested.emit(self.serial, task_id)

    def _selected_task_id(self) -> int | None:
        row = self.table.currentRow()
        return self._row_task_id(row)

    def _row_task_id(self, row: int) -> int | None:
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.UserRole)
        return int(data) if data is not None else None

    def _on_edit_clicked(self):
        task_id = self._selected_task_id()
        if task_id is not None:
            self.edit_requested.emit(self.serial, task_id)

    def _on_delete_clicked(self):
        task_id = self._selected_task_id()
        if task_id is not None:
            self.delete_requested.emit(self.serial, task_id)

    def _on_move_clicked(self, direction: str):
        task_id = self._selected_task_id()
        if task_id is not None:
            self.move_requested.emit(self.serial, task_id, direction)

    def _on_to_front_clicked(self):
        task_id = self._selected_task_id()
        if task_id is not None:
            self.to_front_requested.emit(self.serial, task_id)

    # ── 状态刷新 ──

    def update_snapshot(self, snap: dict, index: int,
                        queue_snap: dict | None = None):
        """snap: controller.snapshot()['devices'] 中的一条
        queue_snap: controller.queue_snapshot(serial)(含无密码任务行)"""
        self.index = index
        self.title.setText(f"设备{index:02d}")
        model = snap.get("model") or "-"
        self.model.setText(model)
        self.serial_label.setText(f"Serial: {snap.get('serial', '—')}")

        adb_state = snap.get("adb_state", "")
        status = snap.get("status", "")
        reset_state = snap.get("reset_state", "")
        run_mode = snap.get("run_mode")
        alive = snap.get("worker_state", "-") not in ("-", "STOPPED")
        # 显示优先级: 重置维护 > 设备错误 > ADB 异常 > 运行模式(第 57 节)
        #             > Worker 存活(兼容旧快照) > 空闲
        if reset_state == "RESETTING":
            key = "resetting"
        elif reset_state == "RESET_FAILED":
            key = "reset_failed"
        elif status == "DEVICE_ERROR":
            key = "error"
        elif adb_state == "unauthorized":
            key = "unauthorized"
        elif adb_state in ("offline", "missing", "no permissions"):
            key = "offline"
        elif run_mode in RUN_MODE_STYLES:
            key = RUN_MODE_STYLES[run_mode]
        elif alive:
            key = "running"
        elif status in STATUS_BY_SNAPSHOT:
            key = STATUS_BY_SNAPSHOT[status]
        elif adb_state == "device" and snap.get("ready"):
            key = "idle"      # ADB 在线且初始化通过 → READY
        elif adb_state == "device":
            key = "online"    # ADB 在线(初始化未做/进行中)
        else:
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
        elapsed = snap.get("account_elapsed") or 0
        if elapsed:
            self.elapsed_label.setText(f"耗时: {self._fmt(elapsed)}")
        else:
            self.elapsed_label.setText(
                f"耗时: {self._fmt(snap.get('last_duration', 0))}")

        # 队列统计(第 30 节)
        qb = snap.get("queue") or {}
        if qb:
            self.queue_stats.setText(
                f"队列: 等待 {qb.get('waiting', 0)} | "
                f"待重试 {qb.get('retry', 0)} | "
                f"完成 {qb.get('success', 0)} | 失败 {qb.get('failed', 0)}")

        # 队列表格(第 6 节)
        if queue_snap is not None:
            self._update_queue_table(queue_snap)

        # 非 device 状态显示具体原因/操作提示(unauthorized/offline 等)
        reject = snap.get("reject_reason") or ""
        error = snap.get("error") or ""
        if reset_state == "RESET_FAILED":
            detail = snap.get("reset_detail") or ""
            self.stall_label.setText(f"⚠ 重置失败: {detail}" if detail
                                     else "⚠ 重置失败")
        elif reset_state == "RESETTING":
            self.stall_label.setText("正在重置设备环境, 请勿断开手机...")
        elif self._notice and time.time() < self._notice_until:
            self.stall_label.setText(self._notice)
        elif reject:
            self.stall_label.setText(f"⚠ {reject}")
        elif error:
            self.stall_label.setText(f"⚠ {error}")
        else:
            self.stall_label.setText("")

        # 按钮状态
        worker_alive = snap.get("worker_state", "-") not in ("-", "STOPPED")
        online = adb_state != "missing"
        self.stop_btn.setEnabled(worker_alive)
        # 开始: 全局运行中 + 本机 Worker 未跑 + 设备在线(第 25 节)
        can_start = (snap.get("system_running") and not worker_alive
                     and online and reset_state != "RESETTING")
        self.start_btn.setEnabled(bool(can_start))
        self.reidentify_btn.setEnabled(online)
        self.reset_btn.setEnabled(online and reset_state != "RESETTING")

    def _update_queue_table(self, queue_snap: dict):
        tasks = queue_snap.get("tasks") or []
        fingerprint = "|".join(
            f"{t['id']}:{t['status']}:{t['retry_count']}"
            for t in tasks)
        if fingerprint == self._table_fingerprint:
            return
        self._table_fingerprint = fingerprint
        self.table.setRowCount(0)
        self._rows.clear()
        for i, t in enumerate(tasks, start=1):
            self.table.insertRow(i - 1)
            # 序号列(存 task_id 供按钮操作)
            num_item = QTableWidgetItem(f"{i:02d}")
            num_item.setData(Qt.UserRole, t["id"])
            num_item.setForeground(Qt.gray)
            self.table.setItem(i - 1, 0, num_item)
            # 账号列(完整账号名可见, 密码绝不显示 — 第 14 节)
            user_item = QTableWidgetItem(t["username"])
            if t["status"] == "RUNNING":
                user_item.setText(f"▶ {t['username']}")
                user_item.setForeground(Qt.darkBlue)
            self.table.setItem(i - 1, 1, user_item)
            # 状态列(中文, 第 17 节)
            status_item = QTableWidgetItem(t["status_display"])
            if t["status"] in ("RUNNING",):
                status_item.setForeground(Qt.darkBlue)
            elif t["status"] in ("FAILED",):
                status_item.setForeground(Qt.darkRed)
            self.table.setItem(i - 1, 2, status_item)
            # 加入时间
            ts = t.get("created_at") or 0
            joined = time.strftime("%H:%M:%S",
                                   time.localtime(ts)) if ts else "—"
            self.table.setItem(i - 1, 3, QTableWidgetItem(joined))
            # 重试次数
            retry = t.get("retry_count", 0)
            self.table.setItem(i - 1, 4,
                               QTableWidgetItem(
                                   str(retry) if retry else "—"))
            self._rows[t["id"]] = i - 1

    def show_notice(self, text: str, seconds: float = 5.0):
        """外部事件提示(重新识别结果等) — 显示数秒后随刷新恢复常态。"""
        self._notice = text
        self._notice_until = time.time() + seconds
        self.stall_label.setText(text)

    @staticmethod
    def _fmt(sec: float) -> str:
        if not sec or sec <= 0:
            return "—"
        return f"{sec:.1f}秒"
