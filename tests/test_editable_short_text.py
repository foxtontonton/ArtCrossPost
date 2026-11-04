# -*- coding: utf-8 -*-
"""
Tests for Editable Short Text Feature (feature/editable-short-text)
Covers:
1. 置脏语义 (Dirty flag semantics: user edit sets dirty, auto-refresh does not overwrite dirty, programmatic write does not set dirty, restore button re-assembles seeds & clears dirty)
2. 双计数 (Dual counters: X weighted chars & Bluesky graphemes, real-time update, red over limit with exceeded count)
3. 发送原样性 (Dispatcher verbatim sending: skips teaser assembly, no extra AI tags, facets extracted)
4. 两阶段替换不叠加 (Two-stage DA link idempotent replacement: skips if post URL exists, replaces homepage, dispatchers do not process links)
5. 空框兜底 (Empty box fallback: payload omits short_text, gatekeeper & dispatchers fall back to auto-assembled caliber)
6. 草稿往返与定时快照 (Draft & scheduled snapshot persistence of short_text and short_text_dirty)
7. 门禁口径 (Gatekeeper & counter caliber: measures box text, falls back to auto-assembled when empty, confirmation bar)
"""
import sys
import json
from unittest.mock import MagicMock, patch
if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()

import pytest
from pathlib import Path
from PIL import Image
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread

from x_text_utils import calculate_x_char_count, format_x_teaser_post, format_x_full_text
from dispatchers.bluesky import BlueskyDispatcher, count_graphemes, MAX_GRAPHEMES, extract_facets
from dispatchers.x_twitter import XTwitterDispatcher
from dispatchers.base import BaseDispatcher
from app import MainWindow, PublishThread


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def mock_main_window(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
        win = MainWindow()
        win.data_dir = tmp_path
        win.notify = MagicMock()

        if hasattr(win, "censor_engine"):
            win.censor_engine.apply_censor.return_value = (MagicMock(), MagicMock())
        if hasattr(win, "auto_censor_cb"):
            win.auto_censor_cb.setChecked(False)
        if hasattr(win, "watermark_enabled_cb"):
            win.watermark_enabled_cb.setChecked(False)

        yield win

        for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
            if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                getattr(win, t_attr).stop()
        if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
            win.thread.quit()
            win.thread.wait()


class DummyDispatcher(BaseDispatcher):
    def __init__(self, name, display_name, cookies_dir):
        super().__init__(name=name, display_name=display_name, login_url="http://test", cookies_dir=cookies_dir)
        self.published_payloads = []
        self.publish_result = (True, "Publish success", [])

    def check_login(self):
        return True

    def login_interactive(self):
        return True, "Login ok"

    def probe_login(self):
        return {"status": "ok", "detail": "Dummy logged in"}

    def publish(self, payload):
        import copy
        self.published_payloads.append(copy.deepcopy(payload))
        return self.publish_result


class TestDirtyFlagSemantics:
    """① 置脏语义测试"""

    def test_programmatic_write_does_not_set_dirty(self, mock_main_window):
        """程序化写入不置脏"""
        win = mock_main_window
        assert win.short_text_dirty is False

        # 修改标题和描述（程序化刷新）
        win.title_input.setText("Auto Title 1")
        win.desc_input.setPlainText("Auto Description 1")
        win.tags_input.setPlainText("tag1, tag2")

        # 验证文本框被自动写入，但 short_text_dirty 依然为 False
        assert win.short_text_dirty is False
        assert "Auto Title 1" in win.x_preview_text_edit.toPlainText()

    def test_user_edit_sets_dirty_and_prevents_auto_refresh_overwrite(self, mock_main_window):
        """用户编辑后置脏，且后续自动刷新不得覆盖用户修改"""
        win = mock_main_window
        win.title_input.setText("Seed Title")
        win.desc_input.setPlainText("Seed Desc")

        # 用户编辑预览框
        custom_text = "My Custom Short Text (Do Not Overwrite!) #custom"
        win.x_preview_text_edit.setPlainText(custom_text)

        # 验证脏标记已置为 True
        assert win.short_text_dirty is True
        assert win.x_preview_text_edit.toPlainText() == custom_text

        # 外部种子输入项变化，触发自动刷新
        win.title_input.setText("New Seed Title That Would Normally Change Preview")
        win.desc_input.setPlainText("New Seed Desc That Would Normally Change Preview")
        win.tags_input.setPlainText("new_tag_1, new_tag_2")

        # 核心红线：用户编辑后自动刷新不得覆盖！
        assert win.x_preview_text_edit.toPlainText() == custom_text
        assert win.short_text_dirty is True

    def test_restore_auto_text_reassembles_and_clears_dirty(self, mock_main_window):
        """「恢复自动文案」按钮重组种子并清脏"""
        win = mock_main_window
        win.title_input.setText("Final Title")
        win.desc_input.setPlainText("Final Description")
        win.tags_input.setPlainText("final_tag")

        # 用户手动篡改内容
        win.x_preview_text_edit.setPlainText("Manual Tamper")
        assert win.short_text_dirty is True

        # 点击恢复自动文案
        win._restore_auto_short_text()

        # 验证脏标记清空，且重新按当前种子重组
        assert win.short_text_dirty is False
        expected_assembled = format_x_teaser_post(
            title="Final Title",
            desc="Final Description",
            tags=["final_tag"],
            da_link=win.config["defaults"].get("x_da_link"),
            nsfw_notice=win.config["defaults"].get("x_nsfw_notice"),
            limit=280
        )
        assert win.x_preview_text_edit.toPlainText() == expected_assembled
        win.notify.assert_called_with("🔄 已恢复自动组装短文案并清除修改标记", level="info")


class TestDualCounters:
    """② 框底双计数测试"""

    def test_dual_counters_real_time_update(self, mock_main_window):
        """实时更新双计数"""
        win = mock_main_window
        test_text = "Hello world from ArtCrossPost"
        win.x_preview_text_edit.setPlainText(test_text)

        x_chars = calculate_x_char_count(test_text)
        bsky_graphemes = count_graphemes(test_text)

        x_counter_str = win.x_preview_counter_lbl.text()
        bsky_counter_str = win.bluesky_preview_counter_lbl.text()

        assert f"{x_chars}/280" in x_counter_str
        assert f"{bsky_graphemes}/{MAX_GRAPHEMES}" in bsky_counter_str
        assert "#ef4444" not in x_counter_str
        assert "#ef4444" not in bsky_counter_str

    def test_x_over_limit_turns_red_with_excess_amount(self, mock_main_window):
        """X 超限变红并注明超出量"""
        win = mock_main_window
        # 构造超过 280 加权字符的文本 (例如 285 个 ascii 字符)
        over_text = "A" * 285
        win.x_preview_text_edit.setPlainText(over_text)

        x_counter_str = win.x_preview_counter_lbl.text()
        assert "#ef4444" in x_counter_str
        assert "285/280" in x_counter_str
        assert "超 5 字" in x_counter_str

    def test_bluesky_over_limit_turns_red_with_excess_amount(self, mock_main_window):
        """Bluesky 超限变红并注明超出量 (MAX_GRAPHEMES = 300)"""
        win = mock_main_window
        # 构造 308 个字素
        over_text = "B" * 308
        win.x_preview_text_edit.setPlainText(over_text)

        bsky_counter_str = win.bluesky_preview_counter_lbl.text()
        assert "#ef4444" in bsky_counter_str
        assert f"308/{MAX_GRAPHEMES}" in bsky_counter_str
        assert "超 8 字素" in bsky_counter_str


class TestDispatcherVerbatimSending:
    """③ 分发器原样发送测试"""

    def test_x_twitter_sends_short_text_verbatim(self, tmp_path, monkeypatch):
        """x_twitter 见 short_text 非空即原样发送、跳过 teaser 组装"""
        disp = XTwitterDispatcher(tmp_path)
        img_p = tmp_path / "img.png"
        img_p.write_bytes(b"dummy")

        custom_short = "Verbatim short text for X. Link: https://example.com #custom"
        payload = {
            "title": "Ignored Title",
            "description": "Ignored Description",
            "tags": ["ignored_tag"],
            "short_text": custom_short,
            "images": [str(img_p)]
        }

        disp.get_cookies = MagicMock(return_value=[{"name": "auth_token", "value": "123"}])

        mock_page = MagicMock()
        mock_page.url = "https://x.com/home"
        mock_editor = MagicMock()

        def fake_qs(sel):
            if "tweetTextarea_0" in sel:
                return mock_editor
            if "fileInput" in sel:
                mock_input = MagicMock()
                return mock_input
            return MagicMock()

        mock_page.query_selector = MagicMock(side_effect=fake_qs)
        mock_page.wait_for_selector = MagicMock(return_value=mock_editor)

        mock_context = MagicMock()
        mock_context.pages = [mock_page]

        monkeypatch.setattr(disp, "create_persistent_stealth_context", lambda p, d, headless=True: mock_context)
        monkeypatch.setattr(disp, "create_stealth_browser", lambda p, headless=True: MagicMock(new_context=lambda **kw: mock_context))
        monkeypatch.setattr(disp, "apply_stealth", lambda page: None)
        monkeypatch.setattr(disp, "safe_goto", lambda page, url, timeout=30000: None)
        monkeypatch.setattr(disp, "assert_field_value", lambda page, sel, val, is_contenteditable=False: (True, ""))
        monkeypatch.setattr(disp, "assert_attached", lambda *args, **kwargs: (False, "mock exit"))
        monkeypatch.setattr(disp, "close_login_session", lambda b, c: None)

        with patch("playwright.sync_api.sync_playwright") as mock_pw:
            mock_play = MagicMock()
            mock_pw.return_value.__enter__.return_value = mock_play

            disp.publish(payload)

            mock_editor.fill.assert_called_with(custom_short)

    def test_bluesky_sends_short_text_verbatim_without_ai_tags(self, tmp_path):
        """bluesky 见 short_text 非空原样发送，不追加 AI 标签，facets 照常提取"""
        disp = BlueskyDispatcher(tmp_path)
        disp._get_active_session = MagicMock(
            return_value=(True, "ok", {"accessJwt": "jwt", "did": "did:plc:123", "handle": "foxton.bsky.social"})
        )

        custom_short = "Check out https://artstation.com/artist by @foxton #digitalart"
        img = tmp_path / "img.png"
        img.write_bytes(b"dummy")

        payload = {
            "title": "Ignored Title",
            "description": "Ignored Description",
            "tags": ["ignored_tag"],
            "short_text": custom_short,
            "is_ai": True,  # 即使是 AI 作品
            "platform_options": {"bluesky_ai_hashtags": True},  # 且勾选追加 AI 标签
            "images": [str(img)]
        }

        captured_records = []
        disp._assert_post_published = MagicMock(return_value=(True, ""))

        def fake_post(url, *args, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "createSession" in url:
                mock_resp.json.return_value = {"accessJwt": "jwt", "did": "did:plc:123"}
            elif "createRecord" in url:
                body = kwargs.get("json", {})
                captured_records.append(body.get("record", {}))
                mock_resp.json.return_value = {"uri": "at://did:plc:123/app.bsky.feed.post/456", "cid": "bafy456"}
            elif "uploadBlob" in url:
                mock_resp.json.return_value = {"blob": {"$type": "blob", "ref": {"$link": "cid"}}}
            return mock_resp

        with patch("requests.post", side_effect=fake_post), patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"posts": [{"uri": "at://did:plc:123/app.bsky.feed.post/456"}]}

            ok, msg = disp.publish(payload)
            assert ok is True

            assert len(captured_records) == 1
            rec = captured_records[0]

            # 1. 验证正文逐字不变，未被追加 #AIArt / #GenerativeArt
            assert rec["text"] == custom_short
            assert "#AIArt" not in rec["text"]
            assert "#GenerativeArt" not in rec["text"]

            # 2. 验证 facets 照常提取
            assert "facets" in rec
            features = [f["features"][0]["$type"] for f in rec["facets"]]
            assert "app.bsky.richtext.facet#link" in features
            assert "app.bsky.richtext.facet#tag" in features


class TestTwoStageDALinkIdempotency:
    """④ 两阶段 DA 链接幂等替换测试"""

    def test_two_stage_replaces_homepage_in_short_text(self, tmp_path):
        """两阶段 DA 发布成功后，若 short_text 含主页链接则替换为当帖 URL"""
        homepage_url = "https://www.deviantart.com/foxtontonton"
        post_url = "https://www.deviantart.com/foxtontonton/art/New-Artwork-999999"

        da_disp = DummyDispatcher("deviantart", "DeviantArt", tmp_path)
        da_disp.last_published_url = post_url
        da_disp.publish_result = (True, f"DeviantArt 发布成功: {post_url}", [
            {"item": "post_url", "verified": True, "detail": post_url, "expected": True}
        ])

        x_disp = DummyDispatcher("x_twitter", "X (Twitter)", tmp_path)

        dummy_img = tmp_path / "img1.png"
        dummy_img.write_bytes(b"dummy")

        custom_short = f"Brand new drawing! Full set on DA: {homepage_url} enjoy!"
        payload = {
            "title": "Artwork 999",
            "description": "Desc",
            "short_text": custom_short,
            "images": [str(dummy_img)],
            "use_da_post_link_for_traffic": True,
            "x_da_link": homepage_url,
            "platform_options": {"x_da_link": homepage_url}
        }

        thread = PublishThread([da_disp, x_disp], payload)
        thread.run()

        x_received = x_disp.published_payloads[0]
        st_after = x_received.get("short_text")
        assert post_url in st_after
        # 确认主页链接已精确被替换为当帖 URL，未遗留独立主页
        assert homepage_url not in st_after.replace(post_url, "")
        assert st_after == f"Brand new drawing! Full set on DA: {post_url} enjoy!"

    def test_two_stage_skips_if_short_text_already_has_post_url(self, tmp_path):
        """两阶段 DA 发布成功后，若 short_text 已含当帖 URL 则跳过，绝不重复叠加路径"""
        homepage_url = "https://www.deviantart.com/foxtontonton"
        post_url = "https://www.deviantart.com/foxtontonton/art/New-Artwork-888888"

        da_disp = DummyDispatcher("deviantart", "DeviantArt", tmp_path)
        da_disp.last_published_url = post_url
        da_disp.publish_result = (True, f"DeviantArt 发布成功: {post_url}", [
            {"item": "post_url", "verified": True, "detail": post_url, "expected": True}
        ])

        x_disp = DummyDispatcher("x_twitter", "X (Twitter)", tmp_path)

        dummy_img = tmp_path / "img1.png"
        dummy_img.write_bytes(b"dummy")

        # short_text 中已存在当帖 URL
        custom_short = f"Already has post link: {post_url} and nothing else"
        payload = {
            "title": "Artwork 888",
            "description": "Desc",
            "short_text": custom_short,
            "images": [str(dummy_img)],
            "use_da_post_link_for_traffic": True,
            "x_da_link": homepage_url,
            "platform_options": {"x_da_link": homepage_url}
        }

        thread = PublishThread([da_disp, x_disp], payload)
        thread.run()

        x_received = x_disp.published_payloads[0]
        st_after = x_received.get("short_text")
        # 逐字保持不变，不产生 /art/888888/art/888888 叠加
        assert st_after == custom_short
        assert st_after.count(post_url) == 1

    def test_two_stage_without_da_link_untouched(self, tmp_path):
        """short_text 不含 DA 链接时保持逐字不变"""
        homepage_url = "https://www.deviantart.com/foxtontonton"
        post_url = "https://www.deviantart.com/foxtontonton/art/New-Artwork-777777"

        da_disp = DummyDispatcher("deviantart", "DeviantArt", tmp_path)
        da_disp.last_published_url = post_url
        da_disp.publish_result = (True, f"DeviantArt 发布成功: {post_url}", [])

        x_disp = DummyDispatcher("x_twitter", "X (Twitter)", tmp_path)

        dummy_img = tmp_path / "img1.png"
        dummy_img.write_bytes(b"dummy")

        custom_short = "Just my artwork without any deviantart link #art"
        payload = {
            "title": "Artwork 777",
            "description": "Desc",
            "short_text": custom_short,
            "images": [str(dummy_img)],
            "use_da_post_link_for_traffic": True,
            "x_da_link": homepage_url,
            "platform_options": {"x_da_link": homepage_url}
        }

        thread = PublishThread([da_disp, x_disp], payload)
        thread.run()

        x_received = x_disp.published_payloads[0]
        assert x_received.get("short_text") == custom_short


class TestEmptyBoxFallback:
    """⑤ 空框兜底测试"""

    def test_empty_box_omits_short_text_in_payload(self, mock_main_window, tmp_path):
        """框空时 payload 不写入 short_text，回退现行组装"""
        win = mock_main_window
        win.title_input.setText("Fallback Title")
        win.desc_input.setPlainText("Fallback Description")
        win.tags_input.setPlainText("fb_tag")

        dummy_img = tmp_path / "img.png"
        Image.new("RGB", (64, 64), color="red").save(dummy_img)
        win.image_paths = [str(dummy_img)]

        # 确保勾选一个平台
        if "x_twitter" in win.platform_controls:
            win.platform_controls["x_twitter"]["checkbox"].setChecked(True)

        # 清空编辑框
        win._updating_short_text = True
        win.x_preview_text_edit.setPlainText("")
        win._updating_short_text = False

        # 捕获组装的 payload
        with patch.object(PublishThread, "start"):
            win._start_publishing()

        payload = win._last_publish_payload
        assert payload is not None
        assert "short_text" not in payload

    def test_dispatchers_fallback_when_short_text_empty(self, tmp_path):
        """分发器在 short_text 为空或空白字符串时正常回退现行 teaser 组装"""
        img_p = tmp_path / "img.png"
        img_p.write_bytes(b"data")

        payload = {
            "title": "Original Title",
            "description": "Original Description",
            "tags": ["fallback_tag"],
            "short_text": "   ",  # 空白字符串
            "x_da_link": "https://www.deviantart.com/foxtontonton",
            "images": [str(img_p)]
        }

        # 1. x_twitter 回退
        disp_x = XTwitterDispatcher(tmp_path)
        disp_x.get_cookies = MagicMock(return_value=None)
        ok, msg, comp = disp_x.publish(payload)
        assert "未登录" in msg

        # 2. bluesky 回退
        disp_bsky = BlueskyDispatcher(tmp_path)
        disp_bsky.get_credentials = MagicMock(return_value=("foxton.bsky.social", "pass123"))
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 401
            mock_post.return_value.json.return_value = {"error": "AuthenticationRequired"}
            ok, msg = disp_bsky.publish(payload)
            assert ok is False


class TestDraftAndScheduledSnapshotPersistence:
    """⑥ 草稿往返与定时快照持久化测试"""

    def test_draft_roundtrip_short_text_and_dirty(self, tmp_path, qapp, monkeypatch):
        """草稿自动保存与加载持久化 short_text 与 short_text_dirty"""
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

        with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
            win1 = MainWindow()
            win1.data_dir = tmp_path
            win1.notify = MagicMock()

            win1.title_input.setText("Draft Title")
            win1.desc_input.setPlainText("Draft Desc")
            win1.x_preview_text_edit.setPlainText("Persisted Custom Short Text")
            win1.short_text_dirty = True

            # 保存草稿
            monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
            win1._save_draft()
            monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

            draft_path = tmp_path / "draft.json"
            assert draft_path.exists()
            with open(draft_path, "r", encoding="utf-8") as f:
                saved_json = json.load(f)

            assert saved_json.get("short_text") == "Persisted Custom Short Text"
            assert saved_json.get("short_text_dirty") is True

            # 创建新窗口加载草稿
            win2 = MainWindow()
            win2.data_dir = tmp_path
            win2.notify = MagicMock()

            monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
            win2._load_draft()
            monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

            assert win2.x_preview_text_edit.toPlainText() == "Persisted Custom Short Text"
            assert win2.short_text_dirty is True

            for w in (win1, win2):
                for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
                    if hasattr(w, t_attr) and getattr(w, t_attr).isActive():
                        getattr(w, t_attr).stop()

    def test_scheduled_snapshot_persists_short_text_and_dirty(self, mock_main_window, tmp_path):
        """定时发布快照持久化 short_text 与 short_text_dirty"""
        win = mock_main_window
        dummy_img = tmp_path / "img.png"
        Image.new("RGB", (64, 64), color="blue").save(dummy_img)

        win.image_paths = [dummy_img]
        win.title_input.setText("Scheduled Title")
        win.desc_input.setPlainText("Scheduled Desc")
        win.x_preview_text_edit.setPlainText("Scheduled Custom Short Text")
        win.short_text_dirty = True

        snapshot = win._capture_current_post_snapshot()
        assert snapshot is not None
        assert snapshot.get("short_text") == "Scheduled Custom Short Text"
        assert snapshot.get("short_text_dirty") is True


class TestGatekeeperCaliber:
    """⑦ 门禁口径测试"""

    def test_gatekeeper_counts_box_text_instead_of_full_text(self, mock_main_window, tmp_path):
        """框内文本在限制内时，即使 full_text 超限，门禁也通过（不弹确认条）"""
        win = mock_main_window
        dummy_img = tmp_path / "img.png"
        Image.new("RGB", (64, 64), color="green").save(dummy_img)
        win.image_paths = [str(dummy_img)]

        if "x_twitter" in win.platform_controls:
            win.platform_controls["x_twitter"]["checkbox"].setChecked(True)

        # 故意让 full_text 大幅超限 (> 280)
        win.title_input.setText("Very Long Title " * 10)
        win.desc_input.setPlainText("Very Long Description " * 30)

        # 但用户手动把短文案框改成了精简合规的文本 (50 字)
        win.x_preview_text_edit.setPlainText("Short concise post under 280 chars!")
        win.short_text_dirty = True

        # 触发门禁检查
        with patch.object(PublishThread, "start"):
            win._start_publishing()

        # 确认未被确认条拦截
        assert hasattr(win, "x_limit_confirm_widget")
        assert not win.x_limit_confirm_widget.isVisible()
        # payload 带有框内短文案
        assert win._last_publish_payload["short_text"] == "Short concise post under 280 chars!"

    def test_gatekeeper_triggers_confirm_when_box_text_over_limit(self, mock_main_window, tmp_path):
        """框内文本超限时触发 X 超限确认条"""
        win = mock_main_window
        dummy_img = tmp_path / "img.png"
        Image.new("RGB", (64, 64), color="yellow").save(dummy_img)
        win.image_paths = [str(dummy_img)]

        if "x_twitter" in win.platform_controls:
            win.platform_controls["x_twitter"]["checkbox"].setChecked(True)

        win.title_input.setText("Normal Title")
        win.desc_input.setPlainText("Normal Desc")

        # 用户在框内写了超过 280 字的内容
        win.x_preview_text_edit.setPlainText("X" * 310)
        win.short_text_dirty = True

        win._start_publishing()

        # 确认触发了超限确认条，且未进入发布
        assert win.x_limit_confirm_widget.isVisible()
        assert "310" in win.x_limit_msg_lbl.text()
        assert "30" in win.x_limit_msg_lbl.text()
        assert win._last_publish_payload is None
