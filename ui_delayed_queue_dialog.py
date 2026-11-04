# -*- coding: utf-8 -*-
"""
ArtCrossPost - Delayed Public Wave Queue Dialog (延迟公开波队列管理抽屉/弹窗)
- 独立 UI 入口查看全部队列任务
- 显示标题、目标平台、创建时间、到期时间、状态
- 失败项标红并展示具体平台错误原因
- 提供「立即触发」、「修改时间」、「取消」、「重试失败平台」操作
"""

import logging
from datetime import datetime
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from delayed_wave_executor import DelayedWaveExecutor
from delayed_wave_manager import DelayedQueueItem, DelayedQueueManager

logger = logging.getLogger("ArtCrossPost")


class DelayedQueueDialog(QDialog):
    """延迟公开波队列管理窗口"""

    queue_updated_signal = pyqtSignal()

    def __init__(
        self,
        manager: DelayedQueueManager,
        executor: Optional[DelayedWaveExecutor] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.manager = manager
        self.executor = executor
        self.setWindowTitle("⏳ 延迟公开波队列管理 (Delayed Public Wave)")
        self.resize(880, 520)
        self.setMinimumSize(720, 400)
        self._init_ui()
        self.refresh_table()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # 头部说明与操作栏
        header = QHBoxLayout()
        title_lbl = QLabel("📋 延迟公开波待发队列")
        title_lbl.setStyleSheet("font-weight: bold; color: #f1f5f9;")
        desc_lbl = QLabel("Patreon 先发后到期自动将同款新作分发至免费平台 (DA / X / Bluesky / Civitai)")
        desc_lbl.setStyleSheet("color: #9ca3af;")

        h_text_v = QVBoxLayout()
        h_text_v.addWidget(title_lbl)
        h_text_v.addWidget(desc_lbl)
        header.addLayout(h_text_v)
        header.addStretch()

        self.refresh_btn = QPushButton("🔄 刷新")
        self.refresh_btn.clicked.connect(self.refresh_table)
        header.addWidget(self.refresh_btn)

        layout.addLayout(header)

        # 核心任务表格
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "作品标题",
            "目标平台",
            "到期时间",
            "状态",
            "尝试次数 / 错误详情",
            "操作",
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #121218;
                border: 1px solid #2e2e3e;
                border-radius: 6px;
                gridline-color: #222230;
                color: #e2e8f0;
            }
            QHeaderView::section {
                background-color: #181824;
                color: #9ca3af;
                font-weight: bold;
                padding: 6px;
                border: 1px solid #2e2e3e;
            }
        """)
        layout.addWidget(self.table, 1)

        # 底部关闭按钮
        footer = QHBoxLayout()
        self.summary_lbl = QLabel("")
        self.summary_lbl.setStyleSheet("color: #9ca3af;")
        footer.addWidget(self.summary_lbl)
        footer.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.setMinimumWidth(80)
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

    def refresh_table(self):
        """刷新队列表格数据"""
        items = self.manager.get_items()
        # 按创建时间倒序展示
        items.sort(key=lambda x: x.created_at, reverse=True)

        self.table.setRowCount(len(items))

        pending_cnt = 0
        failed_cnt = 0

        for row, it in enumerate(items):
            # 1. 标题
            title_item = QTableWidgetItem(it.title)
            title_item.setToolTip(f"ID: {it.id}\n创建于: {it.created_at}")
            self.table.setItem(row, 0, title_item)

            # 2. 目标平台
            plat_names = [p.replace("x_twitter", "X").replace("deviantart", "DA").replace("bluesky", "Bluesky").replace("civitai", "Civitai") for p in it.platforms]
            plat_item = QTableWidgetItem(", ".join(plat_names))
            self.table.setItem(row, 1, plat_item)

            # 3. 到期时间
            due_str = it.due_at.replace("T", " ")[:19]
            due_item = QTableWidgetItem(due_str)
            self.table.setItem(row, 2, due_item)

            # 4. 状态 (失败标红)
            status_map = {
                "pending": "⏳ 待执行",
                "running": "🚀 执行中",
                "done": "✅ 已完成",
                "failed": "❌ 失败",
                "cancelled": "🚫 已取消",
            }
            status_text = status_map.get(it.status, it.status)
            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignCenter)

            if it.status == "failed":
                status_item.setForeground(QColor("#f87171"))
                status_item.setBackground(QColor("#451a1a"))
                failed_cnt += 1
            elif it.status == "done":
                status_item.setForeground(QColor("#4ade80"))
            elif it.status == "pending":
                status_item.setForeground(QColor("#60a5fa"))
                pending_cnt += 1
            elif it.status == "running":
                status_item.setForeground(QColor("#fbbf24"))

            self.table.setItem(row, 3, status_item)

            # 5. 错误详情
            err_desc = ""
            if it.last_error:
                err_parts = [f"[{k}] {v}" for k, v in it.last_error.items()]
                err_desc = " | ".join(err_parts)
            elif it.attempts > 0:
                err_desc = f"已尝试 {it.attempts} 次"

            err_item = QTableWidgetItem(err_desc)
            if it.status == "failed":
                err_item.setForeground(QColor("#fca5a5"))
            err_item.setToolTip(err_desc)
            self.table.setItem(row, 4, err_item)

            # 6. 操作按钮区
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)

            if it.status in ("pending", "failed"):
                # 立即触发
                trig_btn = QPushButton("⚡ 立即发")
                trig_btn.setProperty("fontRole", "small")
                trig_btn.setStyleSheet("padding: 2px 6px;")
                trig_btn.clicked.connect(lambda _, item_id=it.id: self._on_trigger_now(item_id))
                action_layout.addWidget(trig_btn)

                # 修改时间
                time_btn = QPushButton("⏰ 改时间")
                time_btn.setProperty("fontRole", "small")
                time_btn.setStyleSheet("padding: 2px 6px;")
                time_btn.clicked.connect(lambda _, item_id=it.id, cur_due=it.due_at: self._on_change_time(item_id, cur_due))
                action_layout.addWidget(time_btn)

                # 取消
                cancel_btn = QPushButton("✕ 取消")
                cancel_btn.setProperty("fontRole", "small")
                cancel_btn.setStyleSheet("padding: 2px 6px; color: #f87171;")
                cancel_btn.clicked.connect(lambda _, item_id=it.id: self._on_cancel(item_id))
                action_layout.addWidget(cancel_btn)

            if it.status == "failed":
                # 重试失败平台
                retry_btn = QPushButton("🔄 重试失败平台")
                retry_btn.setProperty("fontRole", "small")
                retry_btn.setStyleSheet("padding: 2px 6px; background-color: #991b1b; color: #fef2f2;")
                retry_btn.clicked.connect(lambda _, item_id=it.id: self._on_retry_failed(item_id))
                action_layout.addWidget(retry_btn)

            self.table.setCellWidget(row, 5, action_widget)

        self.summary_lbl.setText(f"总计: {len(items)} 项 | 待发: {pending_cnt} 项 | 异常: {failed_cnt} 项")

    def _on_trigger_now(self, item_id: str):
        """立即手动触发该队列项"""
        item = next((it for it in self.manager.get_items() if it.id == item_id), None)
        if not item:
            return

        ret = QMessageBox.question(
            self,
            "立即发布确认",
            f"确定要立即将作品「{item.title}」发布到目标平台吗？\n目标平台: {item.platforms}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ret != QMessageBox.Yes:
            return

        if self.executor:
            ok, errors = self.executor.execute_item(item)
            if ok:
                QMessageBox.information(self, "发布成功", f"作品「{item.title}」已全部发布成功！")
            else:
                QMessageBox.warning(self, "部分平台失败", f"发布完成但存在失败平台:\n{errors}")
        else:
            QMessageBox.warning(self, "执行器未就绪", "后台执行器未初始化")

        self.refresh_table()
        self.queue_updated_signal.emit()

    def _on_retry_failed(self, item_id: str):
        """仅重试失败的平台"""
        item = next((it for it in self.manager.get_items() if it.id == item_id), None)
        if not item:
            return

        if self.executor:
            ok, errors = self.executor.execute_item(item, only_failed=True)
            if ok:
                QMessageBox.information(self, "重试成功", f"失败平台已全部补发成功！")
            else:
                QMessageBox.warning(self, "重试完成", f"部分平台重试后依然失败:\n{errors}")
        self.refresh_table()
        self.queue_updated_signal.emit()

    def _on_change_time(self, item_id: str, current_due: str):
        """修改到期时间"""
        text, ok = QInputDialog.getText(
            self,
            "修改到期时间",
            "请输入新的到期时间 (ISO 格式，如 2026-11-12T12:00:00):",
            text=current_due,
        )
        if ok and text.strip():
            try:
                datetime.fromisoformat(text.strip())
                self.manager.update_due_time(item_id, text.strip())
                self.refresh_table()
                self.queue_updated_signal.emit()
            except ValueError:
                QMessageBox.warning(self, "格式错误", "请输入有效的 ISO 时间格式 (YYYY-MM-DDTHH:MM:SS)")

    def _on_cancel(self, item_id: str):
        """取消任务"""
        ret = QMessageBox.question(
            self,
            "取消确认",
            "确定要取消此公开波任务吗？取消后素材副本将被清理，不再自动发布。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            self.manager.cancel_item(item_id)
            self.refresh_table()
            self.queue_updated_signal.emit()
