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
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from desktop.controller import CHAT_SOURCES, DesktopAppController
from desktop.runtime_state import ApplicationRunState
from desktop.state_registry import PokemonStateRegistry
from desktop.widgets.device_card import DeviceCard
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
        # 启动即读设备 + 回填配置(但 ApplicationRunState = STOPPED, 不自动运行)
        self._refresh_snapshot()
        self._restore_chat_config()

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
        source_box = QGroupBox("账号来源")
        source_layout = QGridLayout(source_box)
        source_layout.addWidget(QLabel("来源:"), 0, 0)
        self.source_combo = QComboBox()
        for key, label in CHAT_SOURCES:
            self.source_combo.addItem(label, key)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        source_layout.addWidget(self.source_combo, 0, 1)
        self.chat_label = QLabel("QQ群/聊天框名称:")
        source_layout.addWidget(self.chat_label, 0, 2)
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText(
            "请输入接收账号的QQ群聊或聊天框名称")
        self.chat_input.setMinimumWidth(280)
        source_layout.addWidget(self.chat_input, 0, 3)
        self.confirm_btn = QPushButton("确认并运行")
        self.confirm_btn.setStyleSheet(
            "background: #1565c0; color: white; padding: 6px 22px; "
            "border-radius: 4px; font-weight: bold;")
        self.confirm_btn.clicked.connect(self._on_confirm_run)
        source_layout.addWidget(self.confirm_btn, 0, 4)
        root.addWidget(source_box)

        # ── 系统状态行(设备数拆三个指标: 检测到 / READY / 运行中) ──
        status_row = QHBoxLayout()
        self.adb_label = QLabel("ADB: 检测中")
        self.detected_label = QLabel("检测到设备: 0")
        self.ready_label = QLabel("READY设备: 0")
        self.running_workers_label = QLabel("运行中Worker: 0")
        self.source_state_label = QLabel("账号来源: —")
        self.status_font = "color: #424242;"
        for lb in (self.adb_label, self.detected_label, self.ready_label,
                   self.running_workers_label, self.source_state_label):
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
        self.start_btn = QPushButton("开始运行")
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

        # ── 运行统计 ──
        stats_box = QGroupBox("运行统计")
        stats_layout = QHBoxLayout(stats_box)
        self.stats_labels: dict[str, QLabel] = {}
        for key, name in (("PENDING", "队列"), ("RUNNING", "运行中"),
                          ("SUCCESS", "完成"), ("FAILED", "失败")):
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
        self.confirm_btn.setEnabled(stopped)
        self.start_btn.setEnabled(stopped)
        self.stop_all_btn.setEnabled(running and not stopping)

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
        seen = set()
        for i, dev in enumerate(devices):
            serial = dev["serial"]
            seen.add(serial)
            card = self._cards.get(serial)
            if card is None:
                card = DeviceCard(serial, i + 1)
                card.stop_requested.connect(self._on_stop_device)
                self._cards[serial] = card
                self.devices_grid.addWidget(card, i // 3, i % 3)
            card.update_snapshot(dev, i + 1)
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
        self.source_state_label.setText(
            f"账号来源: {'监听中' if snap['system']['running'] else '未运行'}")

    def _paint_counts(self, counts: dict):
        """三指标设备计数(检测到 / READY / 运行中Worker)。"""
        self.detected_label.setText(f"检测到设备: {counts.get('detected', 0)}")
        self.ready_label.setText(f"READY设备: {counts.get('ready', 0)}")
        self.running_workers_label.setText(
            f"运行中Worker: {counts.get('running', 0)}")

    def _update_stats(self, snap: dict):
        stats = snap.get("accounts", {})
        for key, lb in self.stats_labels.items():
            lb.setText(f"{lb.text().split(':')[0]}: {stats.get(key, 0)}")
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

    # ── 配置回填 ──

    def _restore_chat_config(self):
        source, name = self.controller.chat_config()
        idx = self.source_combo.findData(source)
        if idx >= 0:
            self.source_combo.setCurrentIndex(idx)
        self.chat_input.setText(name)

    def _on_source_changed(self):
        source = self.source_combo.currentData()
        if source == "qq_ui":
            self.chat_label.setText("QQ群/聊天框名称:")
            self.chat_input.setPlaceholderText(
                "请输入接收账号的QQ群聊或聊天框名称")
        else:
            self.chat_label.setText("账号文件:")
            self.chat_input.setPlaceholderText(
                "请输入账号文件完整路径(.xlsx/.csv)")

    # ── 按钮 ──

    def _on_confirm_run(self):
        if not self._vpn_preflight_guard():
            return
        source = self.source_combo.currentData()
        name = self.chat_input.text()
        result = self.controller.confirm_and_run(source, name)
        if not result.get("ok"):
            QMessageBox.warning(self, "无法启动", result.get("error", "未知错误"))
        else:
            self.log_view.appendPlainText(
                f"[{time.strftime('%H:%M:%S')}] 正在启动生产运行...")

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
