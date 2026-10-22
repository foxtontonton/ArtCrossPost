# -*- coding: utf-8 -*-
"""
Tests for P1-1: X 文案实时预览 (Real-time X Text Preview)
"""
import sys
from unittest.mock import MagicMock
import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread
from x_text_utils import format_x_teaser_post, calculate_x_char_count


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
    win.data_dir = tmp_path
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


class TestXTextPreview:
    def test_x_preview_tab_exists_in_center_area(self, mock_main_window):
        """验证中央区域存在 X (Twitter) 推文预览 Tab"""
        win = mock_main_window
        assert hasattr(win, "center_tabs")
        assert win.center_tabs.count() >= 2
        tab_names = [win.center_tabs.tabText(i) for i in range(win.center_tabs.count())]
        assert any("X (Twitter) 推文预览" in name for name in tab_names)

        assert hasattr(win, "x_preview_text_edit")
        assert hasattr(win, "x_preview_counter_lbl")
        assert hasattr(win, "x_preview_status_lbl")

    def test_real_time_refresh_on_title_desc_tags_change(self, mock_main_window):
        """
        验证标题/描述/标签任一变化即实时刷新，
        且内容与实际发送使用的 format_x_teaser_post 严格一致
        """
        win = mock_main_window

        win.title_input.setText("Latex Warrior")
        win.desc_input.setPlainText("An epic fantasy battle in latex armor. Full HD rendering.")
        win.tags_input.setPlainText("latex, oc, fantasy")

        expected_preview = format_x_teaser_post(
            title="Latex Warrior",
            desc="An epic fantasy battle in latex armor. Full HD rendering.",
            tags=["latex", "oc", "fantasy"],
            da_link=win.config["defaults"].get("x_da_link"),
            nsfw_notice=win.config["defaults"].get("x_nsfw_notice"),
            limit=280
        )

        assert win.x_preview_text_edit.toPlainText() == expected_preview

        # 修改标题，断言立即同步刷新
        win.title_input.setText("Latex Queen")
        new_expected = format_x_teaser_post(
            title="Latex Queen",
            desc="An epic fantasy battle in latex armor. Full HD rendering.",
            tags=["latex", "oc", "fantasy"],
            da_link=win.config["defaults"].get("x_da_link"),
            nsfw_notice=win.config["defaults"].get("x_nsfw_notice"),
            limit=280
        )
        assert win.x_preview_text_edit.toPlainText() == new_expected

        # 修改标签，断言立即同步刷新
        win.tags_input.setPlainText("latex, sci_fi, cyberpunk")
        tag_expected = format_x_teaser_post(
            title="Latex Queen",
            desc="An epic fantasy battle in latex armor. Full HD rendering.",
            tags=["latex", "sci_fi", "cyberpunk"],
            da_link=win.config["defaults"].get("x_da_link"),
            nsfw_notice=win.config["defaults"].get("x_nsfw_notice"),
            limit=280
        )
        assert win.x_preview_text_edit.toPlainText() == tag_expected

    def test_weighted_char_counter_bottom_display(self, mock_main_window):
        """验证底部展示加权字数，格式形如 '243/280'"""
        win = mock_main_window

        win.title_input.setText("Sample Artwork")
        win.desc_input.setPlainText("Short description for test.")
        win.tags_input.setPlainText("anime, art")

        text = win.x_preview_text_edit.toPlainText()
        expected_count = calculate_x_char_count(text)
        limit = win._get_x_char_limit()

        counter_lbl_text = win.x_preview_counter_lbl.text()
        assert f"{expected_count}/{limit}" in counter_lbl_text
        assert "加权字数" in counter_lbl_text

    def test_copy_x_preview_button(self, mock_main_window):
        """验证复制文案按钮正常工作"""
        from unittest.mock import patch
        win = mock_main_window
        win.title_input.setText("Copy Test")
        win.desc_input.setPlainText("Testing clipboard copy.")
        win.tags_input.setPlainText("test")

        mock_cb = MagicMock()
        with patch.object(QApplication, "clipboard", return_value=mock_cb):
            win._copy_x_preview_text()
            mock_cb.setText.assert_called_once_with(win.x_preview_text_edit.toPlainText())
        win.notify.assert_called_with("📋 已成功复制 X 推文预览文案到剪贴板", level="success")
