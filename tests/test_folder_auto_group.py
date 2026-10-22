# -*- coding: utf-8 -*-
"""
Tests for P2-2: 文件夹批量导入自动分组 (Folder Auto-Grouping Engine)
"""
import sys
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread
from auto_grouper import (
    extract_group_key,
    group_images_by_naming,
    scan_and_group_folder,
    save_groups_to_drafts_box
)
from auto_group_dialog import AutoGroupReviewDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def mock_main_window(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    monkeypatch.setitem(sys.modules, "onnxruntime", MagicMock())

    from app import MainWindow

    win = MainWindow()
    win.data_dir = tmp_path / "data"
    win.data_dir.mkdir(parents=True, exist_ok=True)
    win.notify = MagicMock()

    yield win

    # Cleanup timers
    if hasattr(win, "_draft_save_timer") and win._draft_save_timer.isActive():
        win._draft_save_timer.stop()
    if hasattr(win, "_layout_save_timer") and win._layout_save_timer.isActive():
        win._layout_save_timer.stop()
    if hasattr(win, "x_counter_timer") and win.x_counter_timer.isActive():
        win.x_counter_timer.stop()
    if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
        win.thread.quit()
        win.thread.wait()


class TestFolderAutoGroup:
    def test_extract_group_key_samples(self):
        """
        单测要求: 给出多种真实命名样本，断言提取作品组前缀正确
        """
        assert extract_group_key("art_01.png") == "art"
        assert extract_group_key("art_02.png") == "art"
        assert extract_group_key("sketch.png") == "sketch"
        assert extract_group_key("chapter1_p1.jpg") == "chapter1"
        assert extract_group_key("chapter1_p2.jpg") == "chapter1"
        assert extract_group_key("Vol.2_01.png") == "Vol.2"
        assert extract_group_key("Vol.2_cover.png") == "Vol.2"
        assert extract_group_key("Latex_Session_001.png") == "Latex_Session"
        assert extract_group_key("Latex_Session_002.png") == "Latex_Session"
        assert extract_group_key("img001.jpg") == "img"
        assert extract_group_key("pic-1.png") == "pic"
        assert extract_group_key("standalone_artwork.webp") == "standalone_artwork"

    def test_group_images_by_naming_and_order(self, tmp_path):
        """
        验证根据真实命名样本自动分组，且组内严格保持自然序号排版
        """
        sample_filenames = [
            "art_01.png",
            "sketch.png",
            "art_03.png",
            "chapter1_p2.jpg",
            "art_02.png",
            "chapter1_p1.jpg",
            "Vol.1_02.png",
            "Vol.1_01.png"
        ]

        file_paths = []
        for fn in sample_filenames:
            p = tmp_path / fn
            p.write_bytes(b"dummy")
            file_paths.append(str(p))

        groups = group_images_by_naming(file_paths)

        # 检查分组 Key
        assert set(groups.keys()) == {"art", "sketch", "chapter1", "Vol.1"}

        # 检查 art 组: 必须自然升序排列 01 -> 02 -> 03
        art_names = [Path(p).name for p in groups["art"]]
        assert art_names == ["art_01.png", "art_02.png", "art_03.png"]

        # 检查 chapter1 组: p1 -> p2
        ch1_names = [Path(p).name for p in groups["chapter1"]]
        assert ch1_names == ["chapter1_p1.jpg", "chapter1_p2.jpg"]

        # 检查 Vol.1 组: 01 -> 02
        vol1_names = [Path(p).name for p in groups["Vol.1"]]
        assert vol1_names == ["Vol.1_01.png", "Vol.1_02.png"]

        # 检查 sketch 组
        assert [Path(p).name for p in groups["sketch"]] == ["sketch.png"]

    def test_scan_and_group_folder(self, tmp_path):
        """验证扫描真实文件夹"""
        folder = tmp_path / "incoming_assets"
        folder.mkdir()
        (folder / "hero_01.png").write_bytes(b"")
        (folder / "hero_02.png").write_bytes(b"")
        (folder / "monster.jpg").write_bytes(b"")
        (folder / "ignore.txt").write_bytes(b"")  # 非图片文件忽略

        groups = scan_and_group_folder(folder)
        assert set(groups.keys()) == {"hero", "monster"}
        assert len(groups["hero"]) == 2
        assert len(groups["monster"]) == 1

    def test_save_groups_to_drafts_box(self, tmp_path):
        """验证将分组结果一键载入草稿箱持久化"""
        data_dir = tmp_path / "data"
        groups = {
            "Series_Alpha": ["/fake/a1.png", "/fake/a2.png"],
            "Series_Beta": ["/fake/b1.png"]
        }
        saved_drafts = save_groups_to_drafts_box(groups, data_dir)
        assert len(saved_drafts) == 2

        draft1_content = json.loads(saved_drafts[0].read_text(encoding="utf-8"))
        assert draft1_content["title"] in ["Series_Alpha", "Series_Beta"]
        assert len(draft1_content["images"]) >= 1

    def test_auto_group_review_dialog_merge_and_split(self, tmp_path, qapp):
        """验证审核弹窗的合并与拆分交互"""
        groups = {
            "group_a": ["/fake/a1.png", "/fake/a2.png"],
            "group_b": ["/fake/b1.png"]
        }
        dlg = AutoGroupReviewDialog(groups, tmp_path)

        # 验证初始展示
        assert dlg.tree.topLevelItemCount() == 2

        # 模拟合并 group_a 与 group_b
        dlg.groups["group_a"].extend(dlg.groups.pop("group_b"))
        dlg._refresh_tree()
        assert dlg.tree.topLevelItemCount() == 1
        assert len(dlg.groups["group_a"]) == 3

        # 模拟确认载入草稿箱
        dlg._confirm_and_save()
        assert len(dlg.saved_draft_paths) == 1
        dlg.close()

    def test_mainwindow_import_folder_integration(self, mock_main_window, tmp_path):
        """验证 MainWindow 文件夹导入动作"""
        win = mock_main_window
        win.title_input.clear()

        folder = tmp_path / "sample_import"
        folder.mkdir()
        (folder / "pose_01.png").write_bytes(b"")
        (folder / "pose_02.png").write_bytes(b"")

        # 模拟 AutoGroupReviewDialog 自动确认
        from unittest.mock import patch
        from PyQt5.QtWidgets import QDialog

        def fake_exec(self):
            self._confirm_and_save()
            return QDialog.Accepted

        with patch("auto_group_dialog.AutoGroupReviewDialog.exec_", fake_exec):
            win._import_folder_with_auto_group(str(folder))

        # 断言首套作品已载入当前编辑区
        assert win.title_input.text() == "pose"
        assert len(win.image_paths) == 2
        assert any("成功识别" in str(c) for c in win.notify.call_args_list)
