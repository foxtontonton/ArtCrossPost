# -*- coding: utf-8 -*-
"""
ArtCrossPost - Folder Batch Import Auto-Group Review Dialog (P2-2)
"""
from pathlib import Path
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTreeWidget, QTreeWidgetItem,
    QInputDialog, QMessageBox, QAbstractItemView,
    QFileDialog, QMenu
)
from PyQt5.QtCore import Qt
from auto_grouper import save_groups_to_drafts_box
from video_engine import pair_gifs_with_videos, is_ffmpeg_available


class AutoGroupReviewDialog(QDialog):
    """
    文件夹批量导入自动分组审查对话框:
    - 树形展示自动识别的作品套组
    - 每组 GIF 同步显示配对到的视频文件名（自动配对 / 手动指定），支持当场改绑或解绑
    - 支持用户微调（重命名组、合并选中组、拆分独立作品、移除素材）
    - 一次性载入草稿箱
    """

    def __init__(
        self,
        initial_groups: dict[str, list[str]],
        data_dir: Path,
        parent=None,
        output_dir: str | Path | None = None,
        initial_video_pairs: dict[str, dict] | None = None,
        comfyui_output_dir: str | Path | None = None,
    ):
        super().__init__(parent)
        self.groups = {k: list(v) for k, v in initial_groups.items()}
        self.data_dir = Path(data_dir)
        effective_out_dir = output_dir or comfyui_output_dir
        if effective_out_dir:
            self.output_dir = str(effective_out_dir)
        elif parent is not None and hasattr(parent, "config") and isinstance(parent.config, dict):
            self.output_dir = str(parent.config.get("comfyui_output_dir", "") or "")
        else:
            self.output_dir = ""
        self.confirmed_groups = {}
        self.confirmed_video_pairs = {}
        self.saved_draft_paths = []

        # 初始化每张 GIF 的配对视频映射: {gif_path: {"video_path": str, "source": "auto"|"manual"}}
        self.video_pairs: dict[str, dict] = {}
        if initial_video_pairs is not None:
            for k, v in initial_video_pairs.items():
                if isinstance(v, dict) and v.get("video_path"):
                    self.video_pairs[str(k)] = {
                        "video_path": str(v["video_path"]),
                        "source": str(v.get("source", "auto")),
                    }
                elif isinstance(v, str) and v:
                    self.video_pairs[str(k)] = {"video_path": v, "source": "auto"}
        else:
            self._auto_pair_all_gifs()

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
        self.tree.setHeaderLabels(["作品套组 / 素材文件名", "素材张数 / 配对视频与路径"])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.header().setStretchLastSection(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
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

        self.rebind_video_btn = QPushButton("🎬 指定/改绑视频")
        self.rebind_video_btn.setStyleSheet("background-color: #0e7490; color: #e0f2fe; padding: 6px 12px; border-radius: 4px;")
        self.rebind_video_btn.setToolTip("为选中的 GIF 素材或单 GIF 作品组手动指定/改绑 X 视频版本 (.mp4/.mov/.webm)")
        self.rebind_video_btn.clicked.connect(self._rebind_selected_video)
        tool_row.addWidget(self.rebind_video_btn)

        self.unbind_video_btn = QPushButton("🔗 解绑视频")
        self.unbind_video_btn.setStyleSheet("background-color: #374151; color: #cbd5e1; padding: 6px 12px; border-radius: 4px;")
        self.unbind_video_btn.setToolTip("解除选中 GIF 素材当前的 X 视频配对")
        self.unbind_video_btn.clicked.connect(self._unbind_selected_video)
        tool_row.addWidget(self.unbind_video_btn)

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

    def _auto_pair_all_gifs(self):
        """按组内顺序对所有导入的 GIF 执行自动视频配对 (先到先得)"""
        if not is_ffmpeg_available():
            return
        all_gifs = []
        extra_dirs = []
        for imgs in self.groups.values():
            for p in imgs:
                if str(p).lower().endswith(".gif"):
                    all_gifs.append(str(p))
                    parent_dir = Path(p).parent
                    if parent_dir not in extra_dirs:
                        extra_dirs.append(parent_dir)

        if not all_gifs:
            return

        paired = pair_gifs_with_videos(
            all_gifs,
            output_dir=self.output_dir or None,
            extra_search_dirs=extra_dirs,
            recursive_extra=True,
        )
        for gif_p, vid_p in paired.items():
            if vid_p:
                self.video_pairs[str(gif_p)] = {
                    "video_path": str(vid_p),
                    "source": "auto",
                }

    def get_paired_video_info(self, gif_path: str) -> dict | None:
        return self.video_pairs.get(str(gif_path))

    def rebind_gif_video(self, gif_path: str, new_video_path: str, source: str = "manual"):
        """当场指定或改绑某张 GIF 的配对视频"""
        if not str(gif_path).lower().endswith(".gif"):
            return
        if new_video_path:
            self.video_pairs[str(gif_path)] = {
                "video_path": str(Path(new_video_path).resolve()),
                "source": source,
            }
        else:
            self.video_pairs.pop(str(gif_path), None)
        self._refresh_tree()

    def unbind_gif_video(self, gif_path: str):
        """当场解绑某张 GIF 的配对视频"""
        self.video_pairs.pop(str(gif_path), None)
        self._refresh_tree()

    def _get_selected_gif_paths(self) -> list[str]:
        selected = self.tree.selectedItems()
        gif_paths = []
        for item in selected:
            data = item.data(0, Qt.UserRole)
            if not data:
                continue
            if data[0] == "image":
                p = str(data[2])
                if p.lower().endswith(".gif") and p not in gif_paths:
                    gif_paths.append(p)
            elif data[0] == "group":
                grp_name = data[1]
                for p in self.groups.get(grp_name, []):
                    if str(p).lower().endswith(".gif") and str(p) not in gif_paths:
                        gif_paths.append(str(p))
        return gif_paths

    def _rebind_selected_video(self):
        if not is_ffmpeg_available():
            QMessageBox.warning(self, "功能不可用", "未检测到 imageio-ffmpeg 组件，X 视频配对功能已禁用。")
            return
        gif_paths = self._get_selected_gif_paths()
        if not gif_paths:
            QMessageBox.information(self, "提示", "请先在列表中选中一个 GIF 素材或包含 GIF 的作品组。")
            return
        target_gif = gif_paths[0]
        start_dir = self.output_dir or str(Path(target_gif).parent)
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            f"为 {Path(target_gif).name} 选择配对视频",
            start_dir,
            "视频文件 (*.mp4 *.mov *.webm)",
        )
        if chosen:
            self.rebind_gif_video(target_gif, chosen, source="manual")

    def _unbind_selected_video(self):
        gif_paths = self._get_selected_gif_paths()
        if not gif_paths:
            return
        for gp in gif_paths:
            self.unbind_gif_video(gp)

    def _on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        self.tree.setCurrentItem(item)
        gif_paths = self._get_selected_gif_paths()
        if not gif_paths:
            return
        target_gif = gif_paths[0]
        info = self.get_paired_video_info(target_gif)
        menu = QMenu(self)
        if info and info.get("video_path"):
            act_rebind = menu.addAction(f"🎬 改绑 X 视频 (当前: {Path(info['video_path']).name})...")
            act_unbind = menu.addAction("🔗 解绑 X 视频")
            chosen_act = menu.exec_(self.tree.viewport().mapToGlobal(pos))
            if chosen_act == act_rebind:
                self._rebind_selected_video()
            elif chosen_act == act_unbind:
                self.unbind_gif_video(target_gif)
        else:
            act_bind = menu.addAction("🎬 手动指定 X 视频...")
            chosen_act = menu.exec_(self.tree.viewport().mapToGlobal(pos))
            if chosen_act == act_bind:
                self._rebind_selected_video()

    def _refresh_tree(self):
        self.tree.clear()
        total_images = sum(len(imgs) for imgs in self.groups.values())
        paired_count = sum(
            1 for imgs in self.groups.values() for p in imgs
            if str(p).lower().endswith(".gif") and self.get_paired_video_info(p)
        )
        pair_note = f"，已配对 X 视频 <b>{paired_count}</b> 个" if paired_count > 0 else ""
        self.summary_lbl.setText(f"共识别出 <b>{len(self.groups)}</b> 套作品，包含 <b>{total_images}</b> 张素材图片{pair_note}")

        for grp_name, img_paths in self.groups.items():
            grp_item = QTreeWidgetItem(self.tree)
            grp_item.setText(0, f"📁 {grp_name}")

            # 统计该组内 GIF 的配对视频显示信息
            grp_gifs = [p for p in img_paths if str(p).lower().endswith(".gif")]
            grp_vid_labels = []
            for gp in grp_gifs:
                v_info = self.get_paired_video_info(gp)
                if v_info and v_info.get("video_path"):
                    v_name = Path(v_info["video_path"]).name
                    src_tag = "自动配对" if v_info.get("source") == "auto" else "手动指定"
                    grp_vid_labels.append(f"🎬 {v_name} ({src_tag})")
                else:
                    grp_vid_labels.append("🎬 未配对视频")

            if grp_vid_labels:
                multi_warn = " [多图帖发X忽略视频]" if len(img_paths) > 1 else ""
                grp_item.setText(1, f"共 {len(img_paths)} 张  |  {' ; '.join(grp_vid_labels)}{multi_warn}")
            else:
                grp_item.setText(1, f"共 {len(img_paths)} 张")

            grp_item.setData(0, Qt.UserRole, ("group", grp_name))
            grp_item.setExpanded(True)

            for p in img_paths:
                file_item = QTreeWidgetItem(grp_item)
                p_str = str(p)
                if p_str.lower().endswith(".gif"):
                    v_info = self.get_paired_video_info(p_str)
                    if v_info and v_info.get("video_path"):
                        v_path = v_info["video_path"]
                        v_name = Path(v_path).name
                        src_tag = "自动配对" if v_info.get("source") == "auto" else "手动指定"
                        file_item.setText(0, f"🖼️ {Path(p).name}  [🎬 {v_name} ({src_tag})]")
                        file_item.setText(1, f"{p_str}  ➔  🎬 {v_path}")
                    else:
                        file_item.setText(0, f"🖼️ {Path(p).name}  [🎬 未配对视频]")
                        file_item.setText(1, p_str)
                else:
                    file_item.setText(0, f"🖼️ {Path(p).name}")
                    file_item.setText(1, p_str)
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
        self.confirmed_video_pairs = dict(self.video_pairs)
        self.saved_draft_paths = save_groups_to_drafts_box(
            self.confirmed_groups,
            self.data_dir,
            video_pairs=self.confirmed_video_pairs,
        )
        self.accept()
