# -*- coding: utf-8 -*-
"""
ArtCrossPost - Publish History Viewer Dialog (P1-2)
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView
)
from PyQt5.QtCore import Qt
from history_manager import PublishHistoryManager, PLATFORM_DISPLAY_SHORT


class PublishHistoryDialog(QDialog):
    """简单只读弹窗，按倒序查看全部发布记录"""

    def __init__(self, history_manager: PublishHistoryManager, parent=None):
        super().__init__(parent)
        self.history_manager = history_manager

        self.setWindowTitle("📜 发布历史记录")
        self.resize(820, 520)
        self.setMinimumSize(680, 420)
        self.setModal(True)

        self.setStyleSheet("""
            QDialog {
                background-color: #121218;
                color: #e2e8f0;
            }
            QTableWidget {
                background-color: #181824;
                color: #e2e8f0;
                gridline-color: #27273a;
                border: 1px solid #27273a;
                border-radius: 6px;
            }
            QHeaderView::section {
                background-color: #1e1e2d;
                color: #94a3b8;
                padding: 6px;
                font-weight: bold;
                border: none;
                border-bottom: 1px solid #27273a;
            }
        """)

        self._build_ui()
        self._load_records()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 头部说明
        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        h_title = QLabel("📜 <b>发布历史记录</b>")
        h_title.setStyleSheet("color: #60a5fa; font-size: 15px;")
        h_sub = QLabel("按倒序记录各次发布的时间、素材指纹、平台分发状态与帖子直链 (只读)")
        h_sub.setStyleSheet("color: #94a3b8; font-size: 12px;")
        title_box.addWidget(h_title)
        title_box.addWidget(h_sub)
        header_row.addLayout(title_box, 1)

        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: #ffffff;
                font-weight: bold;
                padding: 6px 12px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """)
        refresh_btn.clicked.connect(self._load_records)
        header_row.addWidget(refresh_btn)
        layout.addLayout(header_row)

        # 历史记录表格
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["发布时间", "作品标题", "图片数", "各平台发布状态", "帖子直链"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        # 底部操作栏
        footer = QHBoxLayout()
        self.count_lbl = QLabel("共 0 条发布记录")
        self.count_lbl.setStyleSheet("color: #9ca3af; font-size: 12px;")
        footer.addWidget(self.count_lbl)
        footer.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #374151;
                color: #e2e8f0;
                padding: 6px 16px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4b5563;
            }
        """)
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

    def _load_records(self):
        records = self.history_manager.load_history()
        self.table.setRowCount(len(records))
        self.count_lbl.setText(f"共 {len(records)} 条发布记录")

        for row, r in enumerate(records):
            # 0. 时间
            ts_item = QTableWidgetItem(r.get("timestamp", ""))
            ts_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, ts_item)

            # 1. 标题
            title_item = QTableWidgetItem(r.get("title", ""))
            self.table.setItem(row, 1, title_item)

            # 2. 图片数
            fps = r.get("image_fingerprints", [])
            cnt_item = QTableWidgetItem(f"{len(fps)} 张")
            cnt_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, cnt_item)

            # 3. 平台状态汇总
            plats = r.get("platforms", {})
            status_strs = []
            links = []
            for p_name, p_val in plats.items():
                short = PLATFORM_DISPLAY_SHORT.get(p_name, p_name)
                succ = p_val.get("success", False)
                symbol = "✅" if succ else "❌"
                status_strs.append(f"{short}:{symbol}")
                if p_val.get("url"):
                    links.append(f"{short}: {p_val['url']}")

            plat_item = QTableWidgetItem("  ".join(status_strs) if status_strs else "-")
            plat_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, plat_item)

            # 4. 直链
            links_item = QTableWidgetItem("; ".join(links) if links else "-")
            self.table.setItem(row, 4, links_item)
