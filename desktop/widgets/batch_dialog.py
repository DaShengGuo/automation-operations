"""
desktop/widgets/batch_dialog.py
批量添加账号弹窗(第 9-13 节):
  粘贴多行账号密码 → 实时解析(---- 优先 > Tab > 英文逗号)
  → 逐行错误提示(缺少密码/账号为空/重复) → 预览(仅账号名, 密码绝不显示)
  → 确认添加。
"""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                               QLabel, QListWidget, QMessageBox,
                               QPlainTextEdit, QVBoxLayout)

from core.bulk_parser import parse_account_lines


class BatchAddDialog(QDialog):
    """批量添加 — 实时解析 + 错误提示 + 预览。"""

    def __init__(self, controller, serial: str, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.serial = serial
        self.setWindowTitle(f"批量添加账号 — {serial}")
        self.resize(520, 500)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "每行一个账号, 账号与密码之间用 ---- 、Tab 或英文逗号分隔:\n"
            "  例: 账号1----密码1    账号2\t密码2    账号3,密码3"))
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("粘贴账号密码, 每行一条...")
        self.input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.input, 3)
        self.front_check = QCheckBox("插到队首(不打断当前账号)")
        layout.addWidget(self.front_check)
        self.summary = QLabel("有效: 0 行    错误: 0 行")
        self.summary.setStyleSheet("color: #1565c0; font-weight: bold;")
        layout.addWidget(self.summary)
        layout.addWidget(QLabel("格式错误(修正后才能添加):"))
        self.error_list = QListWidget()
        self.error_list.setStyleSheet("color: #c62828;")
        layout.addWidget(self.error_list, 2)
        layout.addWidget(QLabel("预览(仅显示账号名, 密码隐藏):"))
        self.preview = QListWidget()
        layout.addWidget(self.preview, 2)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("添加")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._on_text_changed()

    def _on_text_changed(self):
        """实时解析(第 12 节): 每敲一行立刻给出逐行错误。"""
        parsed = parse_account_lines(self.input.toPlainText())
        self.summary.setText(
            f"有效: {parsed.ok_count} 行    错误: {parsed.error_count} 行")
        self.error_list.clear()
        for e in parsed.error_lines:
            self.error_list.addItem(f"第 {e.line_no} 行: {e.error}")
        self.preview.clear()
        for line in parsed.ok_lines:
            self.preview.addItem(line.username)

    def _on_accept(self):
        result = self.controller.add_accounts_batch(
            self.serial, self.input.toPlainText(),
            to_front=self.front_check.isChecked())
        skipped = result.get("skipped", 0)
        errors = result.get("errors") or []
        parts = [f"已添加 {result.get('added', 0)} 个账号"]
        if skipped:
            parts.append(f"跳过重复 {skipped} 个")
        if errors:
            parts.append(f"格式错误 {len(errors)} 行")
        QMessageBox.information(self, "批量添加结果", "，".join(parts) + "。")
        self.accept()
