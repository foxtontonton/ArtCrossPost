# -*- coding: utf-8 -*-
"""
Tests for P0-1: 失败平台单独重试 (Individual Platform Retry)
"""
import sys
from unittest.mock import MagicMock, patch
import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread


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

    win.notify = MagicMock()
    win._clear_draft = MagicMock()

    # Create 4 dummy dispatchers
    d1 = MagicMock()
    d1.name = "deviantart"
    d1.display_name = "DeviantArt"

    d2 = MagicMock()
    d2.name = "x_twitter"
    d2.display_name = "X (Twitter)"

    d3 = MagicMock()
    d3.name = "patreon"
    d3.display_name = "Patreon"

    d4 = MagicMock()
    d4.name = "reddit"
    d4.display_name = "Reddit"

    win.dispatchers = [d1, d2, d3, d4]

    yield win

    # Cleanup timers / threads to avoid teardown crash
    if hasattr(win, "_schedule_poll_timer") and win._schedule_poll_timer.isActive():
        win._schedule_poll_timer.stop()
    if hasattr(win, "_draft_save_timer") and win._draft_save_timer.isActive():
        win._draft_save_timer.stop()
    if hasattr(win, "_layout_save_timer") and win._layout_save_timer.isActive():
        win._layout_save_timer.stop()
    if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
        win.thread.quit()
        win.thread.wait()


class TestRetryFailedPlatforms:
    def test_results_panel_populates_with_retry_button_only_on_failed_row(self, mock_main_window):
        """
        验证发布完成后：
        - 结果面板展示每个平台一行
        - 成功平台重试按钮隐藏
        - 失败平台重试按钮显示
        """
        win = mock_main_window

        # 模拟 3 成功 1 失败
        initial_results = {
            "deviantart": (True, "https://deviantart.com/art/123", []),
            "x_twitter": (False, "API rate limit exceeded", []),
            "patreon": (True, "https://patreon.com/posts/456", []),
            "reddit": (True, "https://reddit.com/r/art/789", []),
        }

        win._on_completed(initial_results)

        assert win.publish_results_panel.isVisible() is True
        assert len(win._platform_retry_buttons) == 4

        # 3 个成功平台的重试按钮必须隐藏
        assert win._platform_retry_buttons["deviantart"].isHidden() is True
        assert win._platform_retry_buttons["patreon"].isHidden() is True
        assert win._platform_retry_buttons["reddit"].isHidden() is True

        # 1 个失败平台的重试按钮必须显示
        assert win._platform_retry_buttons["x_twitter"].isHidden() is False
        assert win._platform_retry_buttons["x_twitter"].text() == "🔄 重试"

        # 此时全平台未全部成功，草稿未清理
        win._clear_draft.assert_not_called()

    def test_single_platform_retry_calls_only_failed_platform(self, mock_main_window):
        """
        单测核心要求：
        模拟 3 成功 1 失败，点击重试后断言【只有失败平台被调用】，
        已成功平台绝不重发（防重复帖），且复用本次发布的渲染产物与文案快照。
        """
        win = mock_main_window

        cached_payload = {
            "title": "My Masterpiece",
            "description": "Exclusive artwork",
            "tags": "art, illustration",
            "images": ["/fake/path/censor.png"],
            "da_images": ["/fake/path/censor.png"],
            "x_images": ["/fake/path/x_opt.png"],
            "raw_images": ["/fake/path/raw.png"],
        }
        win._last_publish_payload = cached_payload

        initial_results = {
            "deviantart": (True, "OK", []),
            "x_twitter": (False, "Connection reset", []),
            "patreon": (True, "OK", []),
            "reddit": (True, "OK", []),
        }
        win._on_completed(initial_results)

        # 监控 PublishThread 实例化
        from app import PublishThread

        dispatched_instances = []

        class MockPublishThread(QThread):
            def __init__(self, selected_dispatchers, payload):
                super().__init__()
                self.selected_dispatchers = selected_dispatchers
                self.payload = payload
                dispatched_instances.append(self)

            def start(self):
                # 模拟直接在当前测试同步执行或不真正启线程
                pass

        with patch("app.PublishThread", side_effect=MockPublishThread):
            # 点击 X (Twitter) 的单独重试按钮
            win._platform_retry_buttons["x_twitter"].click()

        assert len(dispatched_instances) == 1
        thread = dispatched_instances[0]

        # 断言：只有失败平台 (x_twitter) 被调用
        assert len(thread.selected_dispatchers) == 1
        assert thread.selected_dispatchers[0].name == "x_twitter"

        # 断言：严格复用上次的 payload，没有重新生成
        assert thread.payload is cached_payload

    def test_retry_success_hides_all_retry_buttons_and_clears_draft(self, mock_main_window):
        """
        验证失败平台重试成功后：
        - 该平台状态行变为成功 (绿色)
        - 重试按钮消失
        - 全平台全部成功后清除草稿
        """
        win = mock_main_window
        win._last_publish_payload = {"title": "Test Artwork"}

        initial_results = {
            "deviantart": (True, "OK", []),
            "x_twitter": (False, "Timeout", []),
            "patreon": (True, "OK", []),
            "reddit": (True, "OK", []),
        }
        win._on_completed(initial_results)

        assert win._platform_retry_buttons["x_twitter"].isHidden() is False

        # 模拟重试成功回调
        retry_result = {
            "x_twitter": (True, "https://x.com/user/status/999", [])
        }
        win._on_single_platform_retry_completed("x_twitter", retry_result)

        # X 平台的重试按钮现在隐藏
        assert win._platform_retry_buttons["x_twitter"].isHidden() is True

        # 全部 4 个平台的重试按钮均已消失/隐藏
        for btn in win._platform_retry_buttons.values():
            assert btn.isHidden() is True

        # 文本标签显示为成功
        assert "成功" in win._platform_result_labels["x_twitter"].text()

        # 草稿已被清理
        win._clear_draft.assert_called_once()

    def test_retry_failure_keeps_retry_button_enabled(self, mock_main_window):
        """
        验证重试若依然失败：
        - 该平台状态行仍为失败 (红色)
        - 重试按钮保持显示并可再次点击
        - 草稿不清理
        """
        win = mock_main_window
        win._last_publish_payload = {"title": "Test Artwork"}

        initial_results = {
            "deviantart": (True, "OK", []),
            "x_twitter": (False, "Timeout", []),
        }
        win._on_completed(initial_results)

        # 模拟重试依然失败
        retry_result = {
            "x_twitter": (False, "Still timed out", [])
        }
        win._on_single_platform_retry_completed("x_twitter", retry_result)

        # 重试按钮依然显示并恢复可用
        btn = win._platform_retry_buttons["x_twitter"]
        assert btn.isHidden() is False
        assert btn.isEnabled() is True
        assert "重试" in btn.text()

        # 状态依然是失败
        assert "失败" in win._platform_result_labels["x_twitter"].text()

        # 草稿未清理
        win._clear_draft.assert_not_called()
