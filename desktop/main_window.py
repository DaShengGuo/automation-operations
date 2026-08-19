"""
desktop/main_window.py
主窗口 — 欢迎使用宝可梦自动化购买脚本。

职责: 界面/事件/控制命令/状态显示。自动化全部经 DesktopAppController
在 Worker 线程执行, 本窗口绝不阻塞。
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from desktop.controller import DesktopAppController
from desktop.runtime_state import ApplicationRunState
from desktop.state_registry import PokemonStateRegistry
from desktop.widgets.batch_dialog import BatchAddDialog
from desktop.widgets.device_card import DeviceCard
from desktop.widgets.edit_dialog import EditAccountDialog
from desktop.widgets.reset_dialog import ResetConfirmDialog
from version import APP_TITLE, APP_VERSION_TAG

GUI_MAX_LOG_LINES = 3000  # GUI 只保留最近行, 完整日志写文件


class MainWindow(QMainWindow):
    def __init__(self, controller: DesktopAppController, bus, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.bus = bus
        self.setWindowTitle(APP_TITLE)
        self.resize(1080, 820)
        self._cards: dict[str, DeviceCard] = {}
        self._env_cache: dict = {}
        self._env_cache_ts = 0.0
        self._placeholder: QLabel | None = None
        self._build()
        self._wire_bus()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_snapshot)
        self._refresh_timer.start(1000)
        # 启动即读设备(ApplicationRunState = STOPPED, 不自动运行)
        self._refresh_snapshot()

    # ── 布局 ──

    def _build(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # 标题行
        title_row = QHBoxLayout()
        title = QLabel(APP_TITLE)
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        self.version_label = QLabel(f"当前版本：{APP_VERSION_TAG}")
        self.version_label.setStyleSheet("color: #1565c0; font-size: 14px;")
        self.state_badge = QLabel("已停止")
        self.state_badge.setAlignment(Qt.AlignCenter)
        self.state_badge.setMinimumWidth(70)
        self._paint_state_badge(ApplicationRunState.STOPPED)
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(self.version_label)
        title_row.addWidget(self.state_badge)
        root.addLayout(title_row)

        # ── 账号来源 ──
        # v1.2.0: QQ群/聊天框取号已移除(第 1 节)。账号由各设备卡片上的
        # 人工账号队列输入, 见 DeviceCard 加号行/批量添加。
        root.addWidget(QLabel(
            "账号由每台设备的「人工账号队列」输入 — 在下方设备卡片中添加账号密码, "
            "账号仅绑定该手机(第 3 节)。"))


        # ── 系统状态行(设备数拆三个指标: 检测到 / READY / 运行中) ──
        status_row = QHBoxLayout()
        self.adb_label = QLabel("ADB: 检测中")
        self.detected_label = QLabel("检测到设备: 0")
        self.ready_label = QLabel("READY设备: 0")
        self.running_workers_label = QLabel("运行中Worker: 0")
        self.status_font = "color: #424242;"
        for lb in (self.adb_label, self.detected_label, self.ready_label,
                   self.running_workers_label):
            lb.setStyleSheet(self.status_font)
            status_row.addWidget(lb)
        status_row.addStretch(1)
        root.addLayout(status_row)

        # ── 设备区 ──
        devices_box = QGroupBox("已连接手机")
        devices_layout = QVBoxLayout(devices_box)
        self.devices_scroll = QScrollArea()
        self.devices_scroll.setWidgetResizable(True)
        self.devices_scroll.setMinimumHeight(170)
        self.devices_container = QWidget()
        self.devices_grid = QGridLayout(self.devices_container)
        self.devices_grid.setContentsMargins(4, 4, 4, 4)
        self.devices_grid.setSpacing(10)
        self.devices_scroll.setWidget(self.devices_container)
        devices_layout.addWidget(self.devices_scroll)
        root.addWidget(devices_box, 1)

        # ── 运行控制 ──
        ctrl_box = QGroupBox("运行控制")
        ctrl_layout = QGridLayout(ctrl_box)
        self.start_btn = QPushButton("开始全部")
        self.start_btn.clicked.connect(self._on_start)
        self.stop_all_btn = QPushButton("停止全部")
        self.stop_all_btn.setEnabled(False)
        self.stop_all_btn.clicked.connect(self._on_stop_all)
        self.rescan_btn = QPushButton("重新检测设备")
        self.rescan_btn.clicked.connect(self._on_rescan)
        self.diag_btn = QPushButton("复制诊断信息")
        self.diag_btn.clicked.connect(self._on_copy_diagnostics)
        ctrl_layout.addWidget(self.start_btn, 0, 0)
        ctrl_layout.addWidget(self.stop_all_btn, 0, 1)
        ctrl_layout.addWidget(self.rescan_btn, 0, 2)
        ctrl_layout.addWidget(self.diag_btn, 0, 3)

        ctrl_layout.addWidget(QLabel("当前设备:"), 1, 0)
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(260)
        ctrl_layout.addWidget(self.device_combo, 1, 1, 1, 2)

        ctrl_layout.addWidget(QLabel("从第几步开始:"), 2, 0)
        self.step_combo = QComboBox()
        self.step_combo.addItem("自动识别当前步骤", PokemonStateRegistry.AUTO)
        for s in PokemonStateRegistry.ordered_steps():
            self.step_combo.addItem(s.display_name, s.key)
        self.step_combo.setMinimumWidth(260)
        ctrl_layout.addWidget(self.step_combo, 2, 1, 1, 2)

        self.reidentify_btn = QPushButton("重新识别当前步骤")
        self.reidentify_btn.clicked.connect(self._on_reidentify)
        self.resume_btn = QPushButton("从选择步骤重新开始")
        self.resume_btn.clicked.connect(self._on_resume_from_step)
        ctrl_layout.addWidget(self.reidentify_btn, 3, 0)
        ctrl_layout.addWidget(self.resume_btn, 3, 1)
        self.reidentify_result = QLabel("")
        self.reidentify_result.setWordWrap(True)
        ctrl_layout.addWidget(self.reidentify_result, 3, 2)
        root.addWidget(ctrl_box)

        # ── 运行统计(第 31 节: 设备/运行中/等待账号总数/当前执行/本次完成/失败) ──
        stats_box = QGroupBox("运行统计")
        stats_layout = QHBoxLayout(stats_box)
        self.stats_labels: dict[str, QLabel] = {}
        for key, name in (("devices", "设备"), ("running", "运行中"),
                          ("waiting", "等待账号"), ("active", "当前执行"),
                          ("success", "本次完成"), ("failed", "失败")):
            lb = QLabel(f"{name}: 0")
            lb.setStyleSheet("font-weight: bold;")
            stats_layout.addWidget(lb)
            self.stats_labels[key] = lb
        self.perf_label = QLabel("最近5分钟完成: —    平均耗时: —")
        self.perf_label.setStyleSheet("color: #616161;")
        stats_layout.addWidget(self.perf_label)
        stats_layout.addStretch(1)
        root.addWidget(stats_box)

        # ── 实时日志 ──
        log_box = QGroupBox("实时运行日志")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(GUI_MAX_LOG_LINES)
        self.log_view.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 12px;")
        log_layout.addWidget(self.log_view)
        root.addWidget(log_box, 2)

        # ── 底部 ──
        bottom = QHBoxLayout()
        self.history_btn = QPushButton("历史记录")
        self.history_btn.clicked.connect(self._on_history)
        self.logdir_btn = QPushButton("打开日志目录")
        self.logdir_btn.clicked.connect(self._on_open_logdir)
        self.update_btn = QPushButton("检查更新")
        self.update_btn.clicked.connect(self._on_check_update)
        bottom.addWidget(self.history_btn)
        bottom.addWidget(self.logdir_btn)
        bottom.addWidget(self.update_btn)
        bottom.addStretch(1)
        bottom.addWidget(QLabel("日志/历史/错误记录永久保存在本机, "
                                "关闭软件不会删除"))
        root.addLayout(bottom)

        self.setCentralWidget(central)

    # ── 事件桥 ──

    def _wire_bus(self):
        if self.bus is None:
            return
        self.bus.log.connect(self._append_log)
        self.bus.app_state.connect(self._on_app_state)
        self.bus.toast.connect(self._on_toast)
        self.bus.devices_changed.connect(self._on_devices_changed)
        self.bus.vpn_warning.connect(self._on_vpn_warning)
        self.bus.queue_changed.connect(self._on_queue_changed)

    def _on_devices_changed(self, records: list):
        """DeviceMonitor 热插拔事件 → 立即刷新设备卡片与计数。"""
        devices = []
        for r in records:
            devices.append({
                "serial": r["serial"],
                "model": r["model"],
                "brand": r["brand"],
                "resolution": r["resolution"],
                "adb_state": r["adb_state"],
                "status": ("ONLINE" if r["adb_state"] == "device"
                           else "DEVICE_ERROR"),
                "ready": r["ready"],
                "ready_detail": r["ready_detail"],
                "reject_reason": r["reject_reason"],
                "worker_state": r["worker_state"],
                "worker_running": r["worker_running"],
                "reset_state": r["reset_state"],
                "reset_detail": r["reset_detail"],
                "page": "-", "account": "", "error": "",
                "success_count": 0, "fail_count": 0, "last_duration": 0,
            })
        self._update_device_cards(devices)
        counts = self.controller.registry.counts()
        self._paint_counts(counts)

    def _append_log(self, line: str):
        self.log_view.appendPlainText(line)

    def _on_app_state(self, value: str):
        try:
            state = ApplicationRunState(value)
        except ValueError:
            return
        self._paint_state_badge(state)
        running = state in (ApplicationRunState.RUNNING,
                            ApplicationRunState.STARTING)
        stopped = state in (ApplicationRunState.STOPPED,
                            ApplicationRunState.ERROR)
        stopping = state == ApplicationRunState.STOPPING
        self.start_btn.setEnabled(stopped)
        self.stop_all_btn.setEnabled(running and not stopping)
        # 每张卡片的「开始」按钮跟随全局状态(第 25 节: 开始=启用消费)
        self._refresh_snapshot()

    def _on_toast(self, level: str, message: str):
        if level == "error":
            QMessageBox.critical(self, "错误", message)
        else:
            QMessageBox.information(self, "提示", message)

    def _paint_state_badge(self, state: ApplicationRunState):
        colors = {
            ApplicationRunState.STOPPED: ("#616161", "#eeeeee"),
            ApplicationRunState.STARTING: ("#e65100", "#fff3e0"),
            ApplicationRunState.RUNNING: ("#2e7d32", "#e8f5e9"),
            ApplicationRunState.STOPPING: ("#e65100", "#fff3e0"),
            ApplicationRunState.ERROR: ("#c62828", "#ffebee"),
        }
        fg, bg = colors[state]
        self.state_badge.setText(state.display)
        self.state_badge.setStyleSheet(
            f"padding: 2px 10px; border-radius: 10px; "
            f"color: {fg}; background: {bg}; font-weight: bold;")

    # ── 定时刷新 ──

    def _refresh_snapshot(self):
        snap = self.controller.snapshot()
        self._update_device_cards(snap["devices"])
        self._update_status_row(snap)
        self._update_stats(snap)
        self._update_device_combo(snap["devices"])

    def _update_device_cards(self, devices: list):
        system_running = self.controller.state in (
            ApplicationRunState.RUNNING, ApplicationRunState.STARTING)
        seen = set()
        for i, dev in enumerate(devices):
            serial = dev["serial"]
            seen.add(serial)
            card = self._cards.get(serial)
            if card is None:
                card = DeviceCard(serial, i + 1)
                card.stop_requested.connect(self._on_stop_device)
                card.reidentify_requested.connect(self._on_reidentify_device)
                card.reset_requested.connect(self._on_reset_device)
                card.start_requested.connect(self._on_start_device)
                card.add_requested.connect(self._on_add_account)
                card.batch_requested.connect(self._on_batch_add)
                card.edit_requested.connect(self._on_edit_account)
                card.delete_requested.connect(self._on_delete_account)
                card.move_requested.connect(self._on_move_account)
                card.to_front_requested.connect(self._on_to_front_account)
                card.clear_requested.connect(self._on_clear_queue)
                self._cards[serial] = card
                self.devices_grid.addWidget(card, i, 0)   # 单列全宽卡片
            dev["system_running"] = system_running
            card.update_snapshot(dev, i + 1,
                                 self.controller.queue_snapshot(serial))
        # 移除已拔掉的设备
        for serial in list(self._cards):
            if serial not in seen:
                card = self._cards.pop(serial)
                self.devices_grid.removeWidget(card)
                card.deleteLater()
        if not devices:
            if self._placeholder is None:
                self._placeholder = QLabel(
                    "未检测到 Android 设备。\n\n"
                    "请确认:\n"
                    "1. USB 数据线已连接\n"
                    "2. 手机已开启 USB 调试\n"
                    "3. 已允许本电脑 USB 调试授权\n"
                    "4. Windows 已正确安装该手机 USB 驱动")
                self._placeholder.setStyleSheet(
                    "color: #9e9e9e; padding: 12px;")
                self.devices_grid.addWidget(self._placeholder, 0, 0)
            self._placeholder.show()
        elif self._placeholder is not None:
            self._placeholder.hide()

    def _update_status_row(self, snap: dict):
        # check_environment 含设备扫描 — 停止状态 10s 只查一次(不刷屏)
        if self.controller.scheduler is None and \
                time.time() - self._env_cache_ts > 10:
            self._env_cache = self.controller.check_environment()
            self._env_cache_ts = time.time()
        adb_ok = any(name == "ADB" and ok
                     for name, ok, _ in self._env_cache.get("items", []))
        if self.controller.scheduler is not None:
            adb_ok = True
        self.adb_label.setText(f"ADB: {'正常' if adb_ok else '异常'}")
        self._paint_counts(snap.get("counts") or {})

    def _paint_counts(self, counts: dict):
        """三指标设备计数(检测到 / READY / 运行中Worker)。"""
        self.detected_label.setText(f"检测到设备: {counts.get('detected', 0)}")
        self.ready_label.setText(f"READY设备: {counts.get('ready', 0)}")
        self.running_workers_label.setText(
            f"运行中Worker: {counts.get('running', 0)}")

    def _update_stats(self, snap: dict):
        counts = snap.get("counts") or {}
        qt = snap.get("queue_totals") or {}
        values = {
            "devices": len(snap.get("devices") or []),
            "running": counts.get("running", 0),
            "waiting": qt.get("waiting", 0),
            "active": qt.get("running", 0),
            "success": qt.get("success", 0),
            "failed": qt.get("failed", 0),
        }
        for key, lb in self.stats_labels.items():
            lb.setText(f"{lb.text().split(':')[0]}: {values.get(key, 0)}")
        overall = snap.get("throughput", {}).get("overall") or {}
        if overall:
            self.perf_label.setText(
                f"累计完成: {overall.get('completed_total', 0)}    "
                f"平均耗时: {overall.get('avg_sec', '—')}秒")

    def _update_device_combo(self, devices: list):
        current = self.device_combo.currentData()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for dev in devices:
            self.device_combo.addItem(
                f"{dev['serial']} - {dev['model']}", dev["serial"])
        if current:
            idx = self.device_combo.findData(current)
            if idx >= 0:
                self.device_combo.setCurrentIndex(idx)
        self.device_combo.blockSignals(False)

    # ── 账号队列操作(第 5-47 节) ──

    def _on_queue_changed(self, serial: str):
        """队列变化事件 → 立即刷新(第 58 节: 事件驱动, 不轮询)。"""
        self._refresh_snapshot()

    def _on_add_account(self, serial, username, password, to_front):
        if not username:
            QMessageBox.warning(self, "提示", "账号不能为空")
            return
        if not password:
            QMessageBox.warning(self, "提示", "密码不能为空")
            return
        dup = self.controller.check_duplicate(serial, username)
        if dup.get("same_device"):
            # 同设备重复(第 36 节): 默认 不添加
            box = QMessageBox(self)
            box.setWindowTitle("账号重复")
            box.setText(f"账号 {username} 已在此设备队列中, 是否仍然添加?")
            add_btn = box.addButton("仍然添加", QMessageBox.AcceptRole)
            no_btn = box.addButton("不添加", QMessageBox.RejectRole)
            box.setDefaultButton(no_btn)
            box.exec()
            if box.clickedButton() is not add_btn:
                return
        if dup.get("other_device"):
            # 跨设备已分配(第 37 节): 默认 取消
            other = self._device_display_name(dup["other_device"])
            box = QMessageBox(self)
            box.setWindowTitle("跨设备提示")
            box.setText(f"该账号当前已经分配给: {other}, "
                        "是否仍然继续添加?")
            add_btn = box.addButton("仍然添加", QMessageBox.AcceptRole)
            cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
            box.setDefaultButton(cancel_btn)
            box.exec()
            if box.clickedButton() is not add_btn:
                return
        result = self.controller.add_account(
            serial, username, password, to_front)
        if not result.get("ok"):
            QMessageBox.warning(self, "无法添加",
                                result.get("error", "未知错误"))
            return
        card = self._cards.get(serial)
        if card is not None:
            card.clear_add_inputs()

    def _device_display_name(self, serial: str) -> str:
        card = self._cards.get(serial)
        if card is not None:
            return f"设备{card.index:02d} {card.model.text()}"
        return f"设备 {serial}"

    def _on_batch_add(self, serial: str):
        dlg = BatchAddDialog(self.controller, serial, self)
        dlg.exec()

    def _find_task(self, serial: str, task_id: int) -> dict | None:
        snap = self.controller.queue_snapshot(serial)
        for t in snap.get("tasks") or []:
            if t["id"] == task_id:
                return t
        return None

    def _on_edit_account(self, serial: str, task_id: int):
        task = self._find_task(serial, task_id)
        if task is None:
            return
        if task["status"] != "WAITING":
            QMessageBox.warning(self, "无法编辑",
                                "仅「等待」状态的账号可以编辑(第 35 节)")
            return
        dlg = EditAccountDialog(task["username"], self)
        if dlg.exec() != QDialog.Accepted:
            return
        username, password = dlg.values()
        result = self.controller.update_account(
            serial, task_id, username, password)
        if not result.get("ok"):
            QMessageBox.warning(self, "无法保存",
                                result.get("error", "未知错误"))

    def _on_delete_account(self, serial: str, task_id: int):
        task = self._find_task(serial, task_id)
        if task is None:
            return
        if task["status"] not in ("WAITING", "RETRY"):
            QMessageBox.warning(self, "无法删除",
                                "仅「等待/待重试」的账号可删除(第 32 节)")
            return
        box = QMessageBox(self)
        box.setWindowTitle("删除账号")
        box.setText(f"确定删除账号 {task['username']} 吗?\n"
                    "(仅「等待/待重试」的账号可删除)")
        del_btn = box.addButton("删除", QMessageBox.AcceptRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        if box.clickedButton() is not del_btn:
            return
        result = self.controller.remove_account(serial, task_id)
        if not result.get("ok"):
            QMessageBox.warning(self, "无法删除",
                                result.get("error", "未知错误"))

    def _on_move_account(self, serial: str, task_id: int, direction: str):
        task = self._find_task(serial, task_id)
        if task is not None and task["status"] not in ("WAITING", "RETRY"):
            QMessageBox.warning(self, "无法移动",
                                "仅「等待/待重试」的账号可移动")
            return
        result = self.controller.move_account(serial, task_id, direction)
        if not result.get("ok"):
            QMessageBox.warning(self, "无法移动",
                                result.get("error", "未知错误"))

    def _on_to_front_account(self, serial: str, task_id: int):
        task = self._find_task(serial, task_id)
        if task is not None and task["status"] not in ("WAITING", "RETRY"):
            QMessageBox.warning(self, "无法插到队首",
                                "仅「等待/待重试」的账号可插到队首")
            return
        result = self.controller.move_to_front(serial, task_id)
        if not result.get("ok"):
            QMessageBox.warning(self, "无法插到队首",
                                result.get("error", "未知错误"))

    def _on_clear_queue(self, serial: str):
        qs = self.controller.queue_snapshot(serial)
        n = qs.get("waiting", 0) + qs.get("retry", 0)
        box = QMessageBox(self)
        box.setWindowTitle("清空待执行")
        box.setText(f"确定清空该设备的 {n} 个待执行账号吗?\n"
                    "(当前执行中的账号与「已中断」账号保留)")
        clear_btn = box.addButton("清空", QMessageBox.AcceptRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        if box.clickedButton() is not clear_btn:
            return
        result = self.controller.clear_waiting(serial)
        if not result.get("ok"):
            QMessageBox.warning(self, "无法清空",
                                result.get("error", "未知错误"))

    def _on_start_device(self, serial: str):
        """单设备「开始」(第 25 节: 开始=启用消费)。"""
        result = self.controller.start_device(serial)
        if not result.get("ok"):
            QMessageBox.warning(self, "无法开始",
                                result.get("error", "未知错误"))

    # ── 按钮 ──

    def _on_start(self):
        if not self._vpn_preflight_guard():
            return
        result = self.controller.start()
        if not result.get("ok"):
            QMessageBox.warning(self, "无法启动", result.get("error", "未知错误"))

    def _vpn_preflight_guard(self) -> bool:
        """运行前 VPN 预检: 缺失时弹窗确认, 返回 False 表示取消运行。"""
        result = self.controller.preflight_vpn()
        if result.get("ok"):
            return True
        serial = (result.get("serials") or ["?"])[0]
        box = QMessageBox(self)
        box.setWindowTitle("VPN 未检测到")
        box.setIcon(QMessageBox.Warning)
        box.setText(
            f"手机 {serial[:12]} 未检测到 VPN 连接。\n\n"
            "PTC 登录需要科学上网: 未开 VPN 时登录页能加载, "
            "但提交账号后系统跳转会超时, 自动化将卡在游戏登录界面反复重试。\n\n"
            "注意: 机场/Clash 纯代理模式不算系统 VPN, 需开启 "
            "TUN/虚拟网卡(VPN 模式)让隧道接管流量; 开启后几秒内生效。\n\n"
            "请在手机上开启 VPN 后再运行, 或选择「仍然继续」。\n\n"
            f"检测详情: {result.get('detail', '')}")
        cont = box.addButton("仍然继续", QMessageBox.AcceptRole)
        box.addButton("取消运行", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is cont

    def _on_vpn_warning(self, serial: str, detail: str):
        """DeviceMonitor 检测到 VPN 缺失 → 弹窗提醒。

        仅首次缺失/由开转关时触发(device_monitor 已去重);
        此处再防叠加: 已有弹窗时直接忽略新事件。
        """
        if getattr(self, "_vpn_warning_open", False):
            return
        self._vpn_warning_open = True
        try:
            box = QMessageBox(self)
            box.setWindowTitle("VPN 未检测到")
            box.setIcon(QMessageBox.Warning)
            box.setText(
                f"手机 {serial[:12]} 未检测到 VPN 连接。\n\n"
                "PTC 登录需要科学上网: 未开 VPN 时登录页能加载, "
                "但提交账号后系统跳转会超时, 自动化将卡在游戏登录界面反复重试。\n\n"
                "注意: 机场/Clash 纯代理模式不算系统 VPN, 需开启 "
                "TUN/虚拟网卡(VPN 模式)让隧道接管流量; 开启后几秒内生效。\n\n"
                "请在手机上开启 VPN 后再运行。\n\n"
                f"检测详情: {detail}")
            box.addButton("已开启VPN", QMessageBox.AcceptRole)
            box.exec()
        finally:
            self._vpn_warning_open = False

    def _on_stop_all(self):
        self.controller.stop_all()

    def _on_stop_device(self, serial: str):
        result = self.controller.stop_device(serial)
        if not result.get("ok"):
            QMessageBox.warning(self, "停止失败",
                                result.get("error", "未知错误"))

    def _on_reidentify_device(self, serial: str):
        """单设备「重新识别」— 检测手机真实页面并提示建议步骤。"""
        card = self._cards.get(serial)
        if card is None:
            return
        result = self.controller.reidentify(serial)
        if result.get("ok"):
            card.show_notice(f"识别: {result.get('page')}"
                             f" → 建议 {result.get('suggested')}")
        else:
            card.show_notice(f"⚠ {result.get('error', '未知错误')}")

    def _on_reset_device(self, serial: str):
        """设备环境重置 — 二次确认后仅重置当前选中设备(规格 §1/§2)。"""
        snap = self.controller.snapshot()
        dev = next((d for d in snap["devices"]
                    if d["serial"] == serial), None) or {}
        dlg = ResetConfirmDialog(dev.get("model") or "-",
                                 dev.get("account") or "—",
                                 dev.get("worker_state") or "—",
                                 self)
        if dlg.exec() != QDialog.Accepted:
            return
        result = self.controller.reset_device_environment(
            serial, include_browser=dlg.include_browser)
        if not result.get("ok"):
            QMessageBox.warning(self, "无法重置",
                                result.get("error", "未知错误"))

    def _on_rescan(self):
        self.controller.rescan_now()   # 立即重新检测(热插拔)
        env = self.controller.check_environment()
        lines = [f"{'✓' if ok else '✗'} {name}: {detail}"
                 for name, ok, detail in env["items"]]
        QMessageBox.information(self, "环境检测", "\n".join(lines))
        self._refresh_snapshot()

    def _on_copy_diagnostics(self):
        """复制诊断信息到剪贴板(客户报障时粘贴给客服)。"""
        from PySide6.QtWidgets import QApplication
        info = self.controller.diagnostics()
        QApplication.clipboard().setText(info)
        QMessageBox.information(
            self, "诊断信息",
            "诊断信息已复制到剪贴板, 请粘贴给技术支持。\n\n"
            + info[:800])

    def _selected_serial(self) -> str:
        return self.device_combo.currentData() or ""

    def _on_reidentify(self):
        serial = self._selected_serial()
        if not serial:
            QMessageBox.warning(self, "提示", "请先连接手机并选择设备")
            return
        result = self.controller.reidentify(serial)
        if result.get("ok"):
            self.reidentify_result.setText(
                f"当前识别: {result['page']}\n"
                f"建议继续: {result['suggested']}")
        else:
            self.reidentify_result.setText(
                f"识别失败: {result.get('error', '')}")

    def _on_resume_from_step(self):
        serial = self._selected_serial()
        if not serial:
            QMessageBox.warning(self, "提示", "请先连接手机并选择设备")
            return
        step_key = self.step_combo.currentData()
        result = self.controller.resume_from_step(serial, step_key)
        if result.get("ok"):
            QMessageBox.information(
                self, "已执行",
                "已通过前置校验, 从该步骤继续运行。")
        else:
            QMessageBox.warning(self, "无法从该步骤开始",
                                result.get("error", "未知错误"))

    def _on_history(self):
        from desktop.history_dialog import HistoryDialog
        dlg = HistoryDialog(self.controller, self)
        dlg.exec()

    def _on_open_logdir(self):
        log_dir = str(self.controller.cfg.logs_dir)
        try:
            os.startfile(log_dir)  # Windows
        except OSError:
            QMessageBox.information(self, "日志目录", log_dir)

    def _on_check_update(self):
        info = self.controller.update_service.check()
        if not info.configured:
            QMessageBox.information(self, "检查更新", info.error)
        else:
            QMessageBox.information(
                self, "检查更新",
                f"当前版本: {APP_VERSION_TAG}\n{info.error}")

    # ── 关闭 ──

    def closeEvent(self, event):
        # 待执行账号确认(第 28/29 节): 关闭清空内存队列, 已完成记录保留
        pending = self.controller.pending_total()
        if pending > 0:
            box = QMessageBox(self)
            box.setWindowTitle("确定关闭程序吗?")
            box.setText(
                f"当前还有 {pending} 个待执行账号, 关闭后将清空本次队列。\n\n"
                "已完成的账号记录、历史日志和错误记录不会删除。")
            close_btn = box.addButton("确定关闭", QMessageBox.AcceptRole)
            cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
            box.setDefaultButton(cancel_btn)
            box.exec()
            if box.clickedButton() is not close_btn:
                event.ignore()
                return
        if self.controller.state in (ApplicationRunState.RUNNING,
                                     ApplicationRunState.STARTING,
                                     ApplicationRunState.STOPPING):
            answer = QMessageBox.question(
                self, "确定关闭程序吗?",
                "关闭后将停止当前自动化任务并重置本次运行状态。\n\n"
                "历史日志、账号记录和错误记录不会删除。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.controller.shutdown()
        event.accept()
