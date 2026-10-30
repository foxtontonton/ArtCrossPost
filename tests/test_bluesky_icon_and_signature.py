# -*- coding: utf-8 -*-
"""
Tests for feature/bluesky-icon-and-signature:
1. Bluesky 图标加载、控件属性、浏览器唤起与防误触
2. 规范签名 6 个超链接结构与富文本回读断言
3. Bluesky 发帖签名纯文本转换与 6 链接 Facets 提取
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()

import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QThread

from platform_icons import (
    platform_icon,
    get_platform_clean_key,
    get_platform_display_name,
    get_platform_profile_url,
    open_platform_profile,
    ClickablePlatformIcon,
    DEFAULT_PLATFORM_PROFILE_URLS,
)
from signature_utils import (
    CANONICAL_SIGNATURE_TEMPLATE,
    CANONICAL_SIGNATURE_LINKS,
    extract_markdown_links,
    render_description_to_html,
    verify_signature_links_in_html,
    render_description_for_bluesky,
    convert_signature_links_for_bluesky,
)
from dispatchers.bluesky import extract_facets


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


class TestBlueskyIcon:
    def test_bluesky_icon_loaded_and_clickable(self, qapp):
        """测试 1: 验证 Bluesky 图标加载、控件样式、Tooltip 与浏览器唤起"""
        # 1. Helper 验证
        assert get_platform_clean_key("bluesky") == "bluesky"
        assert get_platform_clean_key("bsky") == "bluesky"
        assert get_platform_display_name("bluesky") == "Bluesky"
        assert get_platform_profile_url("bluesky") == "https://bsky.app/profile/foxtontonton.bsky.social"
        assert DEFAULT_PLATFORM_PROFILE_URLS["bluesky"] == "https://bsky.app/profile/foxtontonton.bsky.social"

        # 2. 图标文件与 QIcon
        icon = platform_icon("bluesky")
        assert isinstance(icon, QIcon)
        assert not icon.isNull()

        # 3. ClickablePlatformIcon 控件
        btn = ClickablePlatformIcon("bluesky", size=18)
        assert not btn.icon().isNull()
        assert btn.toolTip() == "打开我的 Bluesky 主页"
        assert btn.cursor().shape() == 13  # PointingHandCursor
        assert btn.cursor().shape() == 13  # PointingHandCursor

        # 4. 点击打开主页
        with patch("webbrowser.open") as mock_open:
            mock_open.return_value = True
            btn.click()
            mock_open.assert_called_once_with("https://bsky.app/profile/foxtontonton.bsky.social")

    def test_bluesky_card_icon_independent_of_checkbox(self, qapp, tmp_path, monkeypatch):
        """测试 2: 验证主窗口 Bluesky 平台卡片中蝴蝶图标点击不切换复选框"""
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
        monkeypatch.setattr("config.save_config", lambda cfg: None)
        monkeypatch.setattr("app.save_config", lambda cfg: None)
        from app import MainWindow
        from censor_engine import CensorEngine

        with patch.object(CensorEngine, "_init_detector", lambda self: None), patch("app.WatermarkEngine"):
            win = MainWindow()
            win.data_dir = tmp_path
            win.notify = MagicMock()

            ctrl = win.platform_controls.get("bluesky")
            assert ctrl is not None, "platform_controls 中必须包含 bluesky"

            cb = ctrl["checkbox"]
            icon_btn = ctrl["icon_btn"]
            assert isinstance(icon_btn, ClickablePlatformIcon)
            assert icon_btn.toolTip() == "打开我的 Bluesky 主页"

            init_checked = cb.isChecked()
            with patch("webbrowser.open") as mock_open:
                mock_open.return_value = True
                icon_btn.click()
                mock_open.assert_called_once_with("https://bsky.app/profile/foxtontonton.bsky.social")

            # 状态毫发无伤
            assert cb.isChecked() == init_checked

            # 切换状态再试
            cb.setChecked(not init_checked)
            new_checked = cb.isChecked()
            with patch("webbrowser.open") as mock_open:
                mock_open.return_value = True
                icon_btn.click()
            assert cb.isChecked() == new_checked

            for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
                if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                    getattr(win, t_attr).stop()


class TestBlueskySignatureLink:
    def test_six_signature_links_canonical_structure(self):
        """测试 3: 规范签名 6 个超链接结构与 Civitai 紧随 Bluesky 顺序"""
        links = extract_markdown_links(CANONICAL_SIGNATURE_TEMPLATE)
        assert len(links) == 6, f"规范签名必须包含恰好 6 个链接，当前: {len(links)}"

        expected_order = [
            ("X", "https://x.com/FOXTONTONTON"),
            ("DeviantArt", "https://www.deviantart.com/foxtontonton"),
            ("Patreon", "https://patreon.com/foxtontonton"),
            ("Ko-fi", "https://ko-fi.com/foxtontonton"),
            ("Civitai", "https://civitai.red/user/FOXTONTONTON"),
            ("Bluesky", "https://bsky.app/profile/foxtontonton.bsky.social"),
        ]
        assert CANONICAL_SIGNATURE_LINKS == expected_order

        # 验证 Civitai 行后紧随 Bluesky 行
        assert "💎Civitai: [FOXTONTONTON](https://civitai.red/user/FOXTONTONTON)\n\n🦋Bluesky: [FOXTONTONTON](https://bsky.app/profile/foxtontonton.bsky.social)" in CANONICAL_SIGNATURE_TEMPLATE

    def test_six_links_html_render_and_verification(self):
        """测试 4: 富文本 HTML 渲染 6/6 超链接并通过回读断言；缺失任一均报错拦截"""
        html_content = render_description_to_html(CANONICAL_SIGNATURE_TEMPLATE)

        # 6 个链接全齐通过断言
        ok, missing = verify_signature_links_in_html(html_content)
        assert ok is True
        assert len(missing) == 0

        # 人为剔除 Bluesky 链接 -> 断言失败
        broken_html = html_content.replace('href="https://bsky.app/profile/foxtontonton.bsky.social"', "")
        ok_broken, missing_broken = verify_signature_links_in_html(broken_html)
        assert ok_broken is False
        assert any("Bluesky" in m for m in missing_broken)

    def test_bluesky_post_rendering_with_six_links_and_facets(self):
        """测试 5: Bluesky 描述转换包含第 6 链接，且链接 facet 命中率 6/6"""
        body = "Excited to share my newest artwork [OC] [Chapter 3/3] by [Artist]"
        full_text = f"{body}\n\n{CANONICAL_SIGNATURE_TEMPLATE}"

        rendered = render_description_for_bluesky(full_text)

        # 验证正文普通方括号完整保留
        assert "[OC]" in rendered
        assert "[Chapter 3/3]" in rendered
        assert "[Artist]" in rendered

        # 验证签名段中无 markdown []() 残留
        from signature_utils import split_body_and_signature
        _, sig_part = split_body_and_signature(rendered)
        assert "[" not in sig_part and "]" not in sig_part
        assert "https://bsky.app/profile/foxtontonton.bsky.social" in sig_part

        # 验证 extract_facets 准确提取全部 6 个链接 facet
        facets = extract_facets(rendered)
        link_facets = [f for f in facets if f["features"][0].get("$type") == "app.bsky.richtext.facet#link"]
        assert len(link_facets) == 6, f"应提取 6 个链接 facet，实际: {len(link_facets)}"

        extracted_uris = [f["features"][0]["uri"] for f in link_facets]
        assert "https://bsky.app/profile/foxtontonton.bsky.social" in extracted_uris
        for _, url in CANONICAL_SIGNATURE_LINKS:
            assert url in extracted_uris
