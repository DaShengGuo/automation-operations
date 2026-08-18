"""
desktop/history_dialog.py
历史记录对话框 — 账号执行历史(今天/全部筛选) + Excel 导出。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from core.logger import mask_account


class HistoryDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("历史记录")
        self.resize(980, 560)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("范围:"))
        self.scope = QComboBox()
        self.scope.addItem("今天", "today")
        self.scope.addItem("全部", "all")
        self.scope.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.scope)
        top.addStretch(1)
        export_btn = QPushButton("导出 Excel")
        export_btn.clicked.connect(self._export)
        top.addWidget(export_btn)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["时间", "账号", "手机", "执行结果", "总耗时",
             "失败步骤", "错误信息", "程序版本"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.refresh()

    def refresh(self):
        scope = self.scope.currentData()
        rows = self.controller.get_history(scope=scope)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            t = time.strftime("%m-%d %H:%M:%S",
                              time.localtime(r["started_at"]))
            state = r["state"]
            for col, text in enumerate((
                    t,
                    mask_account(r["account"]),
                    r["device_serial"],
                    state,
                    f"{r['duration_sec']:.1f}s",
                    r["failed_step"] or "—",
                    (r["error"] or "—")[:80],
                    "—")):
                item = QTableWidgetItem(text)
                if col == 3:
                    item.setForeground(
                        Qt.darkGreen if state == "SUCCESS"
                        else Qt.darkRed)
                self.table.setItem(i, col, item)
        self.table.resizeColumnsToContents()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出历史记录", "执行历史.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            self.controller.export_history(path)
            QMessageBox.information(self, "导出成功", f"已导出到:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
