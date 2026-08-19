"""
desktop/widgets/edit_dialog.py
编辑待执行账号(第 35 节): 仅「等待」状态的账号可编辑。
密码必须重新输入 — 内存队列不提供密码读取, 界面层不保存密码明文
(第 14-17 节)。
"""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton,
                               QVBoxLayout)


class EditAccountDialog(QDialog):
    """编辑账号/密码 — 密码默认隐藏 + 👁 切换。"""

    def __init__(self, username: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑账号")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("账号:"))
        self.user_edit = QLineEdit(username)
        self.user_edit.setMinimumWidth(260)
        layout.addWidget(self.user_edit)
        layout.addWidget(QLabel("密码(必填, 重新输入):"))
        pwd_row = QHBoxLayout()
        self.pwd_edit = QLineEdit()
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        eye = QPushButton("👁")
        eye.setFixedWidth(34)
        eye.setCheckable(True)
        eye.toggled.connect(lambda on: self.pwd_edit.setEchoMode(
            QLineEdit.Normal if on else QLineEdit.Password))
        pwd_row.addWidget(self.pwd_edit, 1)
        pwd_row.addWidget(eye)
        layout.addLayout(pwd_row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_ok(self):
        if not self.user_edit.text().strip():
            QMessageBox.warning(self, "提示", "账号不能为空")
            return
        if not self.pwd_edit.text():
            QMessageBox.warning(self, "提示", "请重新输入密码")
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return self.user_edit.text().strip(), self.pwd_edit.text()
