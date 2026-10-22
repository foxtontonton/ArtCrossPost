# -*- coding: utf-8 -*-
"""
ArtCrossPost - Schedule Publish Dialogs (定时发布 UI 弹窗)
1. ScheduledPublishDialog: 定时发布创建与任务队列管理窗口
2. ExpiredTasksDialog: 启动时过期任务处理确认弹窗 (立即发布 / 跳过 / 重新定时)
"""
from datetime import datetime, timedelta
from pathlib import Path
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDateTimeEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QFrame, QWidget
)
from PyQt5.QtCore import QDateTime, Qt
from PyQt5.QtGui import QColor

try:
    from schedule_manager import ScheduleManager, ScheduledTask, ISO_FORMAT
except ImportError:
    from .schedule_manager import ScheduleManager, ScheduledTask, ISO_FORMAT


class ScheduledPublishDialog(QDialog):
    """定时发布管理与新建窗口"""

    def __init__(self, main_window, schedule_manager: ScheduleManager):
        super().__init__(main_window)
        self.main_window = main_window
        self.schedule_manager = schedule_manager
        self.setWindowTitle("⏱️ 定时发布管理")
        self.resize(780, 520)
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e2e;
                color: #cdd6f4;
            }
            QLabel {
                color: #cdd6f4;
            }
            QDateTimeEdit {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }
            QTableWidget {
                background-color: #181825;
                color: #cdd6f4;
                gridline-color: #313244;
                border: 1px solid #313244;
                border-radius: 6px;
            }
            QHeaderView::section {
                background-color: #313244;
                color: #cdd6f4;
                padding: 6px;
                border: none;
                font-weight: bold;
            }
            QPushButton {
                background-color: #45475a;
                color: #cdd6f4;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #585b70;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # 1. 顶部新建定时任务区域
        top_frame = QFrame()
        top_frame.setStyleSheet("background-color: #252538; border-radius: 8px; padding: 8px;")
        top_layout = QVBoxLayout(top_frame)

        title_lbl = QLabel("<b>📅 新建定时任务</b> (将当前素材、标题及平台选项打包存入快照)")
        title_lbl.setStyleSheet("font-size: 14px; color: #89b4fa;")
        top_layout.addWidget(title_lbl)

        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("计划执行时间:"))

        self.datetime_picker = QDateTimeEdit(self)
        self.datetime_picker.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.datetime_picker.setCalendarPopup(True)
        # 默认当前时间 + 1 小时
        now_dt = QDateTime.currentDateTime()
        self.datetime_picker.setMinimumDateTime(now_dt)
        self.datetime_picker.setDateTime(now_dt.addSecs(3600))
        input_row.addWidget(self.datetime_picker, stretch=1)

        self.add_btn = QPushButton("➕ 确认加入定时队列")
        self.add_btn.setStyleSheet("""
            QPushButton {
                background-color: #0f766e;
                color: #ffffff;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #0d9488;
            }
        """)
        self.add_btn.clicked.connect(self._on_add_task)
        input_row.addWidget(self.add_btn)

        top_layout.addLayout(input_row)
        layout.addWidget(top_frame)

        # 2. 中部任务列表区域
        list_header_row = QHBoxLayout()
        queue_lbl = QLabel("<b>📋 任务队列清单</b>")
        queue_lbl.setStyleSheet("font-size: 14px; color: #a6adc8;")
        list_header_row.addWidget(queue_lbl)
        list_header_row.addStretch()

        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.clicked.connect(self.refresh_table)
        list_header_row.addWidget(refresh_btn)

        layout.addLayout(list_header_row)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["任务 ID", "计划执行时间", "作品标题", "目标平台", "当前状态", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table, stretch=1)

        # 3. 底部关闭与提示
        tip_lbl = QLabel("ℹ️ 提示: 定时发布功能在软件保持开启时生效 (后台 QTimer 每 30 秒自动轮询到期任务)")
        tip_lbl.setStyleSheet("color: #6c7086; font-size: 12px;")
        layout.addWidget(tip_lbl)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

        self.refresh_table()

    def _on_add_task(self):
        # 门禁检查：委托 main_window 提取当前快照
        snapshot = self.main_window._capture_current_post_snapshot()
        if not snapshot:
            return

        target_time_str = self.datetime_picker.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        task = self.schedule_manager.add_task(target_time=target_time_str, snapshot=snapshot)
        self.main_window.notify(f"已成功添加定时发布任务: {task.snapshot.get('title', '')} (于 {target_time_str})", level="success")
        self.refresh_table()

    def refresh_table(self):
        self.schedule_manager.load_tasks()
        tasks = list(self.schedule_manager.tasks)
        # 按创建时间倒序展示
        tasks.sort(key=lambda t: t.parse_created_datetime() or datetime.min, reverse=True)

        self.table.setRowCount(len(tasks))
        for row, t in enumerate(tasks):
            # 0. ID
            id_item = QTableWidgetItem(t.id)
            self.table.setItem(row, 0, id_item)

            # 1. 计划时间
            time_item = QTableWidgetItem(t.target_time)
            self.table.setItem(row, 1, time_item)

            # 2. 标题
            title_text = t.snapshot.get("title", "未命名")
            title_item = QTableWidgetItem(title_text)
            self.table.setItem(row, 2, title_item)

            # 3. 目标平台
            platforms = t.snapshot.get("selected_platforms", [])
            plat_str = ", ".join(platforms) if platforms else "全选"
            plat_item = QTableWidgetItem(plat_str)
            self.table.setItem(row, 3, plat_item)

            # 4. 状态
            status_map = {
                "pending": ("⏳ 等待中", "#f9e2af"),
                "running": ("🚀 执行中", "#89dceb"),
                "completed": ("✅ 已完成", "#a6e3a1"),
                "failed": ("❌ 失败", "#f38ba8"),
                "skipped": ("⏭️ 已跳过", "#6c7086"),
                "cancelled": ("🚫 已取消", "#6c7086"),
            }
            st_text, st_color = status_map.get(t.status, (t.status, "#cdd6f4"))
            st_item = QTableWidgetItem(st_text)
            st_item.setForeground(QColor(st_color))
            self.table.setItem(row, 4, st_item)

            # 5. 操作栏
            if t.status == "pending":
                cancel_btn = QPushButton("取消")
                cancel_btn.setStyleSheet("background-color: #e11d48; color: #ffffff; padding: 4px 8px;")
                cancel_btn.clicked.connect(lambda _, tid=t.id: self._on_cancel_task(tid))
                self.table.setCellWidget(row, 5, cancel_btn)
            else:
                self.table.setCellWidget(row, 5, QWidget())

    def _on_cancel_task(self, task_id: str):
        if QMessageBox.question(
            self,
            "取消确认",
            "确定要取消该待发布的定时任务吗？",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self.schedule_manager.cancel_task(task_id)
            self.refresh_table()


class ExpiredTasksDialog(QDialog):
    """启动时过期任务处理对话框 (不许静默丢弃)"""

    def __init__(self, parent, expired_tasks: list, schedule_manager: ScheduleManager):
        super().__init__(parent)
        self.expired_tasks = expired_tasks
        self.schedule_manager = schedule_manager
        self.setWindowTitle("⚠️ 检测到已过期的定时发布任务")
        self.resize(650, 420)
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e2e;
                color: #cdd6f4;
            }
            QLabel {
                color: #cdd6f4;
            }
            QTableWidget {
                background-color: #181825;
                color: #cdd6f4;
                gridline-color: #313244;
                border: 1px solid #313244;
            }
            QPushButton {
                background-color: #45475a;
                color: #cdd6f4;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
            }
        """)

        layout = QVBoxLayout(self)

        alert_lbl = QLabel(
            "<b>⚠️ 软件启动时发现以下定时任务已超过原定计划时间:</b><br>"
            "根据规范，系统不会静默丢弃任务。请为过期任务选择处理方式："
        )
        alert_lbl.setStyleSheet("color: #f9e2af; font-size: 13px; line-height: 1.4;")
        layout.addWidget(alert_lbl)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["作品标题", "原定时间", "已超期", "操作选择"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setRowCount(len(self.expired_tasks))

        now = datetime.now()
        for row, t in enumerate(self.expired_tasks):
            title = t.snapshot.get("title", "未命名")
            self.table.setItem(row, 0, QTableWidgetItem(title))
            self.table.setItem(row, 1, QTableWidgetItem(t.target_time))

            dt = t.parse_target_datetime()
            diff_str = str(now - dt).split(".")[0] if dt else "未知"
            self.table.setItem(row, 2, QTableWidgetItem(f"超时 {diff_str}"))

            # 操作按钮区
            action_widget = QWidget()
            btn_h = QHBoxLayout(action_widget)
            btn_h.setContentsMargins(4, 2, 4, 2)
            btn_h.setSpacing(6)

            run_btn = QPushButton("🚀 立即发布")
            run_btn.setStyleSheet("background-color: #059669; color: white;")
            run_btn.clicked.connect(lambda _, tid=t.id, r=row: self._action_run_now(tid, r))
            btn_h.addWidget(run_btn)

            reschedule_btn = QPushButton("⏰ 重新定时")
            reschedule_btn.setStyleSheet("background-color: #0284c7; color: white;")
            reschedule_btn.clicked.connect(lambda _, tid=t.id, r=row: self._action_reschedule(tid, r))
            btn_h.addWidget(reschedule_btn)

            skip_btn = QPushButton("⏭️ 跳过")
            skip_btn.setStyleSheet("background-color: #4b5563; color: white;")
            skip_btn.clicked.connect(lambda _, tid=t.id, r=row: self._action_skip(tid, r))
            btn_h.addWidget(skip_btn)

            self.table.setCellWidget(row, 3, action_widget)

        layout.addWidget(self.table, stretch=1)

        bottom_layout = QHBoxLayout()
        all_skip_btn = QPushButton("全部跳过")
        all_skip_btn.clicked.connect(self._on_skip_all)
        bottom_layout.addWidget(all_skip_btn)

        bottom_layout.addStretch()

        done_btn = QPushButton("完成")
        done_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(done_btn)

        layout.addLayout(bottom_layout)

    def _action_run_now(self, task_id: str, row: int):
        # 立即发布：将 target_time 重设为当前时间，在下一次轮询立即被执行
        self.schedule_manager.reschedule_task(task_id, datetime.now().strftime(ISO_FORMAT))
        self.table.setCellWidget(row, 3, QLabel("<font color='#34d399'>✓ 已排入立即发布队列</font>"))

    def _action_skip(self, task_id: str, row: int):
        self.schedule_manager.mark_skipped(task_id)
        self.table.setCellWidget(row, 3, QLabel("<font color='#6b7280'>已跳过</font>"))

    def _action_reschedule(self, task_id: str, row: int):
        dlg = QDialog(self)
        dlg.setWindowTitle("⏰ 重新选择执行时间")
        dlg_layout = QVBoxLayout(dlg)
        picker = QDateTimeEdit(dlg)
        picker.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        picker.setCalendarPopup(True)
        now_dt = QDateTime.currentDateTime()
        picker.setMinimumDateTime(now_dt)
        picker.setDateTime(now_dt.addSecs(3600))
        dlg_layout.addWidget(picker)

        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(dlg.accept)
        dlg_layout.addWidget(ok_btn)

        if dlg.exec_() == QDialog.Accepted:
            new_t = picker.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            self.schedule_manager.reschedule_task(task_id, new_t)
            self.table.setCellWidget(row, 3, QLabel(f"<font color='#38bdf8'>✓ 已重置于 {new_t}</font>"))

    def _on_skip_all(self):
        for t in self.expired_tasks:
            if t.status == "pending":
                self.schedule_manager.mark_skipped(t.id)
        self.accept()
