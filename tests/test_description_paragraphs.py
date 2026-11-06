import os
import json
import pytest
from unittest.mock import patch, MagicMock
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread
from signature_utils import render_description_to_html, CANONICAL_SIGNATURE_TEMPLATE


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def mock_main_window(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    from app import MainWindow

    with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
        win = MainWindow()
        win.data_dir = tmp_path
        win.notify = MagicMock()
        win.config["signature_template"] = CANONICAL_SIGNATURE_TEMPLATE

        yield win

        # Cleanup timers
        for t_attr in (
            "_draft_save_timer",
            "_layout_save_timer",
            "_config_save_timer",
            "_preview_timer",
            "x_counter_timer",
            "_schedule_poll_timer",
            "_font_apply_timer",
            "_thumb_size_apply_timer",
        ):
            if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                getattr(win, t_attr).stop()
        if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
            win.thread.quit()
            win.thread.wait()


class TestDescriptionParagraphs:
    """测试描述段落提取、往返不动点及富文本渲染分段"""

    def test_multi_block_document_reads_double_newlines(self, mock_main_window):
        """测试多 block 文档（用户回车分段）经 _get_description_text 提取后段间为 \\n\\n"""
        win = mock_main_window
        typed_input = "Paragraph 1\nParagraph 2\nParagraph 3"
        win.desc_input.setPlainText(typed_input)

        assert win.desc_input.document().blockCount() == 3

        extracted = win._get_description_text()
        assert extracted == "Paragraph 1\n\nParagraph 2\n\nParagraph 3"

    def test_roundtrip_fixed_point_verbatim_equality(self, mock_main_window):
        """测试存读往返不动点：多次读写保持逐字相等 (f(f(x)) == f(x))"""
        win = mock_main_window
        test_cases = [
            "Single line description.",
            "Paragraph A\n\nParagraph B\n\nParagraph C",
            "Line 1\n\n\n\nLine 2",
            "   Indented para 1\n\nIndented para 2   ",
            f"Intro body.\n\n{CANONICAL_SIGNATURE_TEMPLATE}",
            "",
        ]

        for tc in test_cases:
            win.desc_input.setPlainText(tc)
            read1 = win._get_description_text()
            win.desc_input.setPlainText(read1)
            read2 = win._get_description_text()
            assert read1 == read2, f"Fixed point failure on input: {tc!r}"

    def test_draft_save_load_roundtrip_preserves_paragraphs(self, mock_main_window, tmp_path, monkeypatch):
        """测试草稿保存与加载往返：段落语义完全保留且逐字一致"""
        win = mock_main_window
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        draft_file = tmp_path / "draft.json"

        original_desc = "Hero introduction.\n\nWorld background.\n\nExtra note."
        win.desc_input.setPlainText(original_desc)
        win.title_input.setText("Epic Adventure")

        win._save_draft()
        assert draft_file.exists()

        with open(draft_file, "r", encoding="utf-8") as f:
            saved_json = json.load(f)
        assert saved_json["description"] == original_desc

        win.desc_input.setPlainText("")
        win._load_draft()

        loaded_desc = win._get_description_text()
        assert loaded_desc == original_desc

    def test_render_to_html_paragraph_count_equals_block_count(self, mock_main_window):
        """测试经 _get_description_text 取值后接 render_description_to_html，<p> 数严格等于段落数"""
        win = mock_main_window
        win.desc_input.setPlainText("Scene one description.\nScene two description.\nScene three description.")
        desc = win._get_description_text()

        html = render_description_to_html(desc)
        assert html.count("<p>") == 3
        assert "<p>Scene one description.</p>" in html
        assert "<p>Scene two description.</p>" in html
        assert "<p>Scene three description.</p>" in html

    def test_render_to_html_with_signature_preserves_all_paragraphs(self, mock_main_window):
        """测试正文段落加引流签名渲染：正文各段与签名各段均独立成 <p>"""
        win = mock_main_window
        win.desc_input.setPlainText("Chapter 1: The Awakening.\nChapter 2: The Journey.")
        win._append_signature()

        desc_with_sig = win._get_description_text()
        html = render_description_to_html(desc_with_sig)

        assert "<p>Chapter 1: The Awakening.</p>" in html
        assert "<p>Chapter 2: The Journey.</p>" in html
        assert 'href="https://patreon.com/foxtontonton"' in html
        assert 'href="https://www.deviantart.com/foxtontonton"' in html

    def test_scheduled_snapshot_captures_paragraph_text(self, mock_main_window):
        """测试定时发布快照捕获完整多段落文本"""
        win = mock_main_window
        win.title_input.setText("Scheduled Artwork")
        win.image_paths = ["fake_path.png"]
        win.desc_input.setPlainText("First section.\nSecond section.")
        if "x_twitter" in win.platform_controls:
            win.platform_controls["x_twitter"]["checkbox"].setChecked(True)

        snapshot = win._capture_current_post_snapshot()
        assert snapshot is not None
        assert snapshot["description"] == "First section.\n\nSecond section."

    def test_append_signature_idempotent_and_preserves_paragraphs(self, mock_main_window):
        """测试追加签名不仅不冲掉已有段落回车，而且重复点击不重复追加"""
        win = mock_main_window
        win.desc_input.setPlainText("First line.\nSecond line.")
        win._append_signature()

        after_first = win._get_description_text()
        assert after_first.startswith("First line.\n\nSecond line.\n\n")

        win._append_signature()
        after_second = win._get_description_text()
        assert after_second == after_first
