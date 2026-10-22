# -*- coding: utf-8 -*-
"""
Tests for P1-2: 发布历史与重复图片指纹预警 (Publish History & Image Fingerprint Warning)
"""
import sys
import json
from unittest.mock import MagicMock
from pathlib import Path
from PIL import Image
import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread
from history_manager import PublishHistoryManager, compute_image_fingerprints
from history_dialog import PublishHistoryDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def dummy_image(tmp_path):
    img_path = tmp_path / "sample_art.png"
    im = Image.new("RGB", (64, 64), color="red")
    im.save(img_path)
    return img_path


@pytest.fixture
def mock_main_window(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    monkeypatch.setitem(sys.modules, "onnxruntime", MagicMock())

    from app import MainWindow

    win = MainWindow()
    win.data_dir = tmp_path
    win.history_manager = PublishHistoryManager(win.data_dir)
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


class TestPublishHistory:
    def test_fingerprint_generation(self, dummy_image):
        """验证能够正确计算 SHA-256 和 dHash 指纹"""
        fp = compute_image_fingerprints(dummy_image)
        assert "sha256" in fp
        assert len(fp["sha256"]) == 64
        assert "dhash" in fp
        assert len(fp["dhash"]) > 0

    def test_append_record_and_reverse_loading(self, tmp_path, dummy_image):
        """
        验证每次发布结束后追加一条记录到 data/publish_history.json:
        时间戳、图片指纹哈希、各平台发布成败状态、帖子直链，按倒序排列
        """
        mgr = PublishHistoryManager(tmp_path)

        rec1 = mgr.append_record(
            title="Artwork 1",
            image_paths=[str(dummy_image)],
            publish_results={
                "deviantart": (True, "https://www.deviantart.com/art/111", []),
                "x_twitter": (False, "Timed out", [])
            }
        )

        rec2 = mgr.append_record(
            title="Artwork 2",
            image_paths=[str(dummy_image)],
            publish_results={
                "deviantart": (True, "https://www.deviantart.com/art/222", []),
                "x_twitter": (True, "https://x.com/status/333", [])
            }
        )

        history = mgr.load_history()
        assert len(history) == 2
        # 倒序检查 (rec2 在前，rec1 在后)
        assert history[0]["title"] == "Artwork 2"
        assert history[1]["title"] == "Artwork 1"

        # 检查记录字段
        assert "timestamp" in history[0]
        assert len(history[0]["image_fingerprints"]) == 1
        assert history[0]["image_fingerprints"][0]["sha256"] != ""
        assert history[0]["platforms"]["deviantart"]["success"] is True
        assert history[0]["platforms"]["deviantart"]["url"] == "https://www.deviantart.com/art/222"
        assert history[0]["platforms"]["x_twitter"]["success"] is True

    def test_duplicate_image_warning_banner_on_import(self, mock_main_window, dummy_image):
        """
        验证导入图片时查历史：
        若命中已发布过的图片指纹，界面顶部黄色横幅提示：
        '该图片曾于 YYYY-MM-DD 发布至 [DA, X]，请确认是否重复发布'
        """
        win = mock_main_window

        # 预设一条历史记录
        custom_history = [{
            "id": "test_id",
            "timestamp": "2026-10-21 15:30:00",
            "title": "Old Masterpiece",
            "image_fingerprints": [compute_image_fingerprints(dummy_image)],
            "platforms": {
                "deviantart": {"success": True, "url": "https://deviantart.com/1", "message": "OK"},
                "x_twitter": {"success": True, "url": "https://x.com/1", "message": "OK"}
            }
        }]
        (win.data_dir / "publish_history.json").write_text(json.dumps(custom_history), encoding="utf-8")

        # 模拟导入该图片
        win.image_paths = [str(dummy_image)]
        win._check_image_duplicate_history()

        # 断言顶部黄色横幅显示并包含确切文本
        assert win.history_warning_banner.isVisible() is True
        banner_text = win.history_warning_banner_lbl.text()
        assert "该图片曾于 2026-10-21 发布至 [DA, X]，请确认是否重复发布" in banner_text

        # 清空图片后横幅自动隐藏
        win._clear_images()
        assert win.history_warning_banner.isVisible() is False

    def test_publish_completion_appends_to_history(self, mock_main_window, dummy_image):
        """验证发布完成回调时自动追加历史"""
        win = mock_main_window
        win.image_paths = [str(dummy_image)]
        win.title_input.setText("New Painting")

        results = {
            "deviantart": (True, "https://www.deviantart.com/new_art", []),
            "x_twitter": (True, "https://x.com/status/999", [])
        }

        win._on_completed(results)

        history = win.history_manager.load_history()
        assert len(history) >= 1
        assert history[0]["title"] == "New Painting"
        assert history[0]["platforms"]["deviantart"]["url"] == "https://www.deviantart.com/new_art"

    def test_history_dialog_table_ui(self, mock_main_window, dummy_image):
        """验证历史记录查看器弹窗能够正确展示倒序记录"""
        win = mock_main_window
        mgr = win.history_manager
        mgr.append_record("Art 1", [str(dummy_image)], {"deviantart": (True, "https://da.com/1", [])})
        mgr.append_record("Art 2", [str(dummy_image)], {"x_twitter": (True, "https://x.com/2", [])})

        dlg = PublishHistoryDialog(mgr, parent=win)
        assert dlg.table.rowCount() == 2
        # 第一行 (最新) 是 Art 2
        assert dlg.table.item(0, 1).text() == "Art 2"
        # 第二行是 Art 1
        assert dlg.table.item(1, 1).text() == "Art 1"
        dlg.close()
