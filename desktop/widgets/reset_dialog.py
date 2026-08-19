"""
desktop/widgets/reset_dialog.py
ResetConfirmDialog — 设备环境重置二次确认弹窗(规格 §2/§5/§19)。

展示: 设备型号 / 当前账号(脱敏) / 当前步骤 / 影响范围清单 /
警告文案, [取消] + [确认重置]。
浏览器数据为独立高级选项, 默认不勾选。
"""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout)


class ResetConfirmDialog(QDialog):
    """重置前的人工确认。include_browser 由用户勾选决定(默认 OFF)。"""

    def __init__(self, model: str, account: str, step: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("重置设备环境")
        self.setModal(True)
        self.setFixedWidth(480)
        self.include_browser = False
        self._build(model, account, step)

    def _build(self, model: str, account: str, step: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 设备信息(规格 §2: 设备/当前账号脱敏/当前步骤)
        info = QGridLayout()
        info.setHorizontalSpacing(12)
        info.setColumnStretch(1, 1)
        info.addWidget(QLabel("设备:"), 0, 0)
        info.addWidget(QLabel(model or "-"), 0, 1)
        info.addWidget(QLabel("当前账号:"), 1, 0)
        info.addWidget(QLabel(account or "—"), 1, 1)
        info.addWidget(QLabel("当前步骤:"), 2, 0)
        info.addWidget(QLabel(step or "—"), 2, 1)
        layout.addLayout(info)

        # 影响范围(规格 §19: 应用环境/运行状态勾选, 浏览器默认不勾选)
        scope = QFrame()
        scope.setStyleSheet(
            "QFrame { background: #fff8e1; border: 1px solid #fdd835; "
            "border-radius: 4px; }")
        scope_layout = QVBoxLayout(scope)
        scope_layout.setSpacing(6)
        scope_title = QLabel("本次重置将执行:")
        scope_title.setStyleSheet("font-weight: bold; border: none;")
        scope_items = QLabel(
            "✓ Pokémon GO 应用环境\n"
            "✓ 自动化运行状态\n"
            "□ 外部浏览器数据（默认关闭，可能影响浏览器已有登录信息）")
        scope_items.setStyleSheet("border: none;")
        self.browser_cb = QCheckBox("同时重置当前外部网页登录环境")
        self.browser_cb.setChecked(False)
        browser_hint = QLabel(
            "该操作可能影响浏览器中的登录状态和网站数据。")
        browser_hint.setWordWrap(True)
        browser_hint.setStyleSheet("color: #c62828; border: none;")
        scope_layout.addWidget(scope_title)
        scope_layout.addWidget(scope_items)
        scope_layout.addWidget(self.browser_cb)
        scope_layout.addWidget(browser_hint)
        layout.addWidget(scope)

        # 警告(规格 §2)
        warning = QLabel(
            "⚠ 警告: 本操作可能清除 Pokémon GO 本地数据，"
            "并终止当前自动化任务。是否继续？")
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #c62828; font-weight: bold;")
        layout.addWidget(warning)

        # 按钮: [取消] [确认重置]
        btns = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        confirm_btn = QPushButton("确认重置")
        confirm_btn.setStyleSheet(
            "color: #c62828; border: 1px solid #ef9a9a; "
            "border-radius: 3px; font-weight: bold;")
        cancel_btn.clicked.connect(self.reject)
        confirm_btn.clicked.connect(self._on_confirm)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        btns.addWidget(confirm_btn)
        layout.addLayout(btns)

    def _on_confirm(self):
        self.include_browser = self.browser_cb.isChecked()
        self.accept()
