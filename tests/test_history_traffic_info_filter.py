import json
import os
import sys
from unittest.mock import MagicMock, patch
from pathlib import Path
from PIL import Image
import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread

if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()

from history_manager import PublishHistoryManager, PLATFORM_DISPLAY_SHORT


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def dummy_image(tmp_path):
    img_path = tmp_path / "sample_art.png"
    im = Image.new("RGB", (64, 64), color="blue")
    im.save(img_path)
    return img_path


@pytest.fixture
def mock_main_window(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    from app import MainWindow

    with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
        win = MainWindow()
        win.data_dir = tmp_path
        win.history_manager = PublishHistoryManager(win.data_dir)
        win.notify = MagicMock()
        yield win

        for t_attr in (
            "_draft_save_timer",
            "_layout_save_timer",
            "_config_save_timer",
            "_preview_timer",
            "x_counter_timer",
            "_schedule_poll_timer",
            "_font_apply_timer",
            "_thumb_size_timer",
        ):
            if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                getattr(win, t_attr).stop()
        if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
            win.thread.quit()
            win.thread.wait()


def test_append_record_skips_underscore_keys(tmp_path, dummy_image):
    """测试 append_record 组装 platforms 时严格跳过以 _ 开头的键 (如 _traffic_info)"""
    mgr = PublishHistoryManager(tmp_path)
    results = {
        "deviantart": (True, "https://www.deviantart.com/artist/art/123"),
        "_traffic_info": {
            "source": "history",
            "url": "https://www.deviantart.com/artist/art/123"
        },
        "_debug_flag": True,
        "bluesky": (True, "Published successfully https://bsky.app/profile/test.bsky.social/post/123")
    }

    record = mgr.append_record(
        title="测试作品",
        image_paths=[dummy_image],
        publish_results=results
    )

    # 1. 返回的 record["platforms"] 中不包含下划线开头的私有字段
    platforms = record["platforms"]
    assert "deviantart" in platforms
    assert "bluesky" in platforms
    assert "_traffic_info" not in platforms
    assert "_debug_flag" not in platforms

    # 2. 检查落盘 JSON 文件中同样没有 _traffic_info
    with open(mgr.history_file, "r", encoding="utf-8") as f:
        disk_records = json.load(f)
    assert len(disk_records) == 1
    disk_platforms = disk_records[0]["platforms"]
    assert "deviantart" in disk_platforms
    assert "bluesky" in disk_platforms
    assert "_traffic_info" not in disk_platforms
    assert "_debug_flag" not in disk_platforms


def test_check_duplicate_image_filters_dirty_traffic_info(tmp_path, dummy_image):
    """测试读取包含历史脏数据 (_traffic_info) 的记录时，check_duplicate_image 自动清洗，只返回白名单平台"""
    from history_manager import compute_image_fingerprints
    fp = compute_image_fingerprints(dummy_image)

    # 构造一条带脏数据的旧记录直接写入文件
    dirty_record = {
        "id": "20261101_120000_000000",
        "timestamp": "2026-11-01 12:00:00",
        "title": "脏数据测试",
        "image_fingerprints": [fp],
        "platforms": {
            "deviantart": {"success": True, "url": "https://www.deviantart.com/art/1", "message": "ok"},
            "_traffic_info": {"success": True, "url": "https://www.deviantart.com/art/1", "message": "{...}"},
            "unknown_platform": {"success": True, "url": "", "message": "ok"},
            "bluesky": {"success": True, "url": "https://bsky.app/post/1", "message": "ok"}
        }
    }
    history_file = tmp_path / "publish_history.json"
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump([dirty_record], f)

    mgr = PublishHistoryManager(tmp_path)
    hit = mgr.check_duplicate_image(dummy_image)

    assert hit is not None
    # 平台名单只应收录 PLATFORM_DISPLAY_SHORT 中的平台，过滤 _traffic_info 与未知平台
    assert "_traffic_info" not in hit["platforms"]
    assert "unknown_platform" not in hit["platforms"]
    assert "DA" in hit["platforms"]
    assert "Bluesky" in hit["platforms"]
    assert hit["platforms"] == ["DA", "Bluesky"]


def test_platform_display_short_contains_bluesky():
    """断言 PLATFORM_DISPLAY_SHORT 正确收录 bluesky -> Bluesky"""
    assert "bluesky" in PLATFORM_DISPLAY_SHORT
    assert PLATFORM_DISPLAY_SHORT["bluesky"] == "Bluesky"


def test_on_completed_cleans_results_before_history_append(mock_main_window, dummy_image):
    """测试 app.py _on_completed 流程中，_traffic_info 在结果面板更新后、append_record 之前被清洗"""
    win = mock_main_window
    win.image_paths = [str(dummy_image)]

    # 模拟包含 _traffic_info 的发布结果
    results = {
        "deviantart": (True, "https://www.deviantart.com/art/999"),
        "_traffic_info": {"source": "history", "url": "https://www.deviantart.com/art/999"}
    }

    # 监控 append_record 调用时接收到的 publish_results
    captured_results = {}
    original_append = win.history_manager.append_record

    def spy_append(*args, **kwargs):
        nonlocal captured_results
        captured_results = dict(kwargs.get("publish_results", {}))
        return original_append(*args, **kwargs)

    win.history_manager.append_record = MagicMock(side_effect=spy_append)

    # 触发 _on_completed
    win._on_completed(results)

    # 1. 验证传给 append_record 的 publish_results 已经 pop 掉 _traffic_info
    assert "_traffic_info" not in captured_results
    assert "deviantart" in captured_results

    # 2. 验证原始传入的 results 字典里的 _traffic_info 已被 pop
    assert "_traffic_info" not in results

    # 3. 验证落盘的发布历史记录中无 _traffic_info
    history = win.history_manager.load_history()
    assert len(history) >= 1
    assert "_traffic_info" not in history[0]["platforms"]
