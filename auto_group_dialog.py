# -*- coding: utf-8 -*-
"""
ArtCrossPost - Folder Batch Import Auto-Group Review Dialog (P2-2)
"""
from pathlib import Path
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTreeWidget, QTreeWidgetItem,
    QInputDialog, QMessageBox, QAbstractItemView
)
from PyQt5.QtCore import Qt
from auto_grouper import save_groups_to_drafts_box


class AutoGroupReviewDialog(QDialog):
    """
    文件夹批量导入自动分组审查对话框:
    - 树形展示自动识别的作品套组
    - 支持用户微调（重命名组、合并选中组、拆分独立作品、移除素材）
    - 一次性载入草稿箱
    """

    def __init__(self, initial_groups: dict[str, list[str]], data_dir: Path, parent=None):
        super().__init__(parent)
        self.groups = {k: list(v) for k, v in initial_groups.items()}
        self.data_dir = Path(data_dir)
        self.confirmed_groups = {}
        self.saved_draft_paths = []

        self.setWindowTitle("📂 文件夹批量导入自动分组")
        self.resize(720, 500)
        self.setMinimumSize(600, 380)
        self.setModal(True)

        self.setStyleSheet("""
            QDialog {
                background-color: #121218;
                color: #e2e8f0;
            }
            QTreeWidget {
                background-color: #181824;
                color: #e2e8f0;
                border: 1px solid #27273a;
                border-radius: 6px;
                padding: 4px;
            }
            QHeaderView::section {
                background-color: #1e1e2d;
                color: #94a3b8;
                padding: 4px;
                font-weight: bold;
                border: none;
            }
        """)

        self._build_ui()
        self._refresh_tree()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 头部说明
        header = QVBoxLayout()
        header.setSpacing(4)
        title_lbl = QLabel("📂 <b>文件夹导入素材自动分组预览</b>")
        title_lbl.setStyleSheet("color: #60a5fa; font-size: 15px;")
        desc_lbl = QLabel("系统已按同前缀、序号与章节规则识别出以下多套作品。您可以合并、拆分或重命名微调后再载入草稿箱：")
        desc_lbl.setStyleSheet("color: #9ca3af; font-size: 12px;")
        desc_lbl.setWordWrap(True)
        header.addWidget(title_lbl)
        header.addWidget(desc_lbl)
        layout.addLayout(header)

        # 分组树
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["作品套组 / 素材文件名", "素材张数 / 文件路径"])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.header().setStretchLastSection(True)
        layout.addWidget(self.tree, 1)

        # 微调工具栏
        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)

        self.rename_btn = QPushButton("✏️ 重命名作品组")
        self.rename_btn.setStyleSheet("background-color: #374151; color: #e2e8f0; padding: 6px 12px; border-radius: 4px;")
        self.rename_btn.clicked.connect(self._rename_group)
        tool_row.addWidget(self.rename_btn)

        self.merge_btn = QPushButton("🔗 合并选中作品组")
        self.merge_btn.setStyleSheet("background-color: #374151; color: #e2e8f0; padding: 6px 12px; border-radius: 4px;")
        self.merge_btn.clicked.connect(self._merge_groups)
        tool_row.addWidget(self.merge_btn)

        self.split_btn = QPushButton("✂️ 拆为新独立作品")
        self.split_btn.setStyleSheet("background-color: #374151; color: #e2e8f0; padding: 6px 12px; border-radius: 4px;")
        self.split_btn.clicked.connect(self._split_item)
        tool_row.addWidget(self.split_btn)

        self.remove_btn = QPushButton("🗑️ 移除")
        self.remove_btn.setStyleSheet("background-color: #7f1d1d; color: #fca5a5; padding: 6px 12px; border-radius: 4px;")
        self.remove_btn.clicked.connect(self._remove_selected)
        tool_row.addWidget(self.remove_btn)

        tool_row.addStretch()
        layout.addLayout(tool_row)

        # 底部确定与统计
        footer = QHBoxLayout()
        self.summary_lbl = QLabel()
        self.summary_lbl.setStyleSheet("color: #9ca3af; font-size: 12px;")
        footer.addWidget(self.summary_lbl)
        footer.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("background-color: #374151; color: #e2e8f0; padding: 6px 16px; border-radius: 6px;")
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)

        self.confirm_btn = QPushButton("📦 一次性载入草稿箱")
        self.confirm_btn.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: #ffffff;
                font-weight: bold;
                padding: 6px 18px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """)
        self.confirm_btn.clicked.connect(self._confirm_and_save)
        footer.addWidget(self.confirm_btn)

        layout.addLayout(footer)

    def _refresh_tree(self):
        self.tree.clear()
        total_images = sum(len(imgs) for imgs in self.groups.values())
        self.summary_lbl.setText(f"共识别出 <b>{len(self.groups)}</b> 套作品，包含 <b>{total_images}</b> 张素材图片")

        for grp_name, img_paths in self.groups.items():
            grp_item = QTreeWidgetItem(self.tree)
            grp_item.setText(0, f"📁 {grp_name}")
            grp_item.setText(1, f"共 {len(img_paths)} 张")
            grp_item.setData(0, Qt.UserRole, ("group", grp_name))
            grp_item.setExpanded(True)

            for p in img_paths:
                file_item = QTreeWidgetItem(grp_item)
                file_item.setText(0, f"🖼️ {Path(p).name}")
                file_item.setText(1, str(p))
                file_item.setData(0, Qt.UserRole, ("image", grp_name, p))

    def _rename_group(self):
        selected = self.tree.selectedItems()
        if not selected:
            return
        item = selected[0]
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "group":
            return
        old_name = data[1]
        new_name, ok = QInputDialog.getText(self, "重命名作品组", "请输入新的作品组名称:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            self.groups[new_name] = self.groups.pop(old_name)
            self._refresh_tree()

    def _merge_groups(self):
        selected = self.tree.selectedItems()
        group_names = []
        for item in selected:
            data = item.data(0, Qt.UserRole)
            if data and data[0] == "group" and data[1] not in group_names:
                group_names.append(data[1])

        if len(group_names) < 2:
            return

        target_name = group_names[0]
        merged_imgs = list(self.groups[target_name])
        for other in group_names[1:]:
            merged_imgs.extend(self.groups.pop(other))

        self.groups[target_name] = merged_imgs
        self._refresh_tree()

    def _split_item(self):
        selected = self.tree.selectedItems()
        split_paths = []
        for item in selected:
            data = item.data(0, Qt.UserRole)
            if data and data[0] == "image":
                split_paths.append((data[1], data[2]))

        if not split_paths:
            return

        for src_grp, p in split_paths:
            if src_grp in self.groups and p in self.groups[src_grp]:
                self.groups[src_grp].remove(p)
                if not self.groups[src_grp]:
                    del self.groups[src_grp]

                new_grp_name = Path(p).stem
                idx = 1
                unique_name = new_grp_name
                while unique_name in self.groups:
                    unique_name = f"{new_grp_name}_{idx}"
                    idx += 1
                self.groups[unique_name] = [p]

        self._refresh_tree()

    def _remove_selected(self):
        selected = self.tree.selectedItems()
        for item in selected:
            data = item.data(0, Qt.UserRole)
            if not data:
                continue
            if data[0] == "group":
                grp_name = data[1]
                if grp_name in self.groups:
                    del self.groups[grp_name]
            elif data[0] == "image":
                grp_name, p = data[1], data[2]
                if grp_name in self.groups and p in self.groups[grp_name]:
                    self.groups[grp_name].remove(p)
                    if not self.groups[grp_name]:
                        del self.groups[grp_name]

        self._refresh_tree()

    def _confirm_and_save(self):
        if not self.groups:
            self.reject()
            return

        self.confirmed_groups = dict(self.groups)
        self.saved_draft_paths = save_groups_to_drafts_box(self.confirmed_groups, self.data_dir)
        self.accept()
