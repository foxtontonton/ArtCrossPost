# -*- coding: utf-8 -*-
"""
ArtCrossPost - Signature Link Utilities & Hyperlink Readback Unit Tests
严格覆盖需求: 全平台统一程序签名，带真超链接 (feature/signature-with-links)

1. 签名模板链接语法解析:
   - 纯文本与多链接混排
   - URL 含特殊字符 (&, ?, =, #)
   - 未闭合中括号容错不崩溃，保持原文本
   - 旧版无 markdown 签名向后兼容
2. 正文方括号防误解析:
   - 正文中的 [OC], [1/3], [Draft] 等方括号严格保持纯文本，不得转为 <a>
3. 纯文本回退:
   - [Display](URL) -> Display
4. 提交前回读断言:
   - 5 链接全齐通过 (True, [])
   - 故意少 1 链接或全为纯文本时，断言拦截、返回明确错误信息，阻断发布
   - DA 当帖链接透传时，允许匹配当帖 URL
"""
import pytest
from signature_utils import (
    CANONICAL_SIGNATURE_TEMPLATE,
    CANONICAL_SIGNATURE_LINKS,
    extract_markdown_links,
    split_body_and_signature,
    render_markdown_signature_to_html,
    render_description_to_html,
    render_description_to_plain_text,
    verify_signature_links_in_html,
    has_signature,
    normalize_url,
    convert_signature_links_for_bluesky,
    render_description_for_bluesky,
)


def test_canonical_signature_contains_exact_six_links():
    """验证规范签名模板中准确包含全部 6 个目标链接与定版文案"""
    links = extract_markdown_links(CANONICAL_SIGNATURE_TEMPLATE)
    assert len(links) == 6

    extracted_urls = [url for _, url in links]
    assert "https://x.com/FOXTONTONTON" in extracted_urls
    assert "https://www.deviantart.com/foxtontonton" in extracted_urls
    assert "https://patreon.com/foxtontonton" in extracted_urls
    assert "https://ko-fi.com/foxtontonton" in extracted_urls
    assert "https://civitai.red/user/FOXTONTONTON" in extracted_urls
    assert "https://bsky.app/profile/foxtontonton.bsky.social" in extracted_urls

    # 验证末句定版: "...support my work, you can buy me a coffee on Ko-fi..."
    assert "support my work, you can buy me a coffee on Ko-fi" in CANONICAL_SIGNATURE_TEMPLATE


def test_markdown_link_parsing_mixed_text_and_links():
    """测试纯文本与多链接混排的准确解析与 HTML 实体转义"""
    text = "Follow on [X Profile](https://x.com/FOXTONTONTON) & [Patreon Page](https://patreon.com/foxtontonton) for 100% bonus <exclusive>!"
    rendered = render_markdown_signature_to_html(text)

    # 实体必须转义
    assert "&amp;" in rendered
    assert "&lt;exclusive&gt;" in rendered
    # 链接必须生成 <a>
    assert '<a href="https://x.com/FOXTONTONTON" target="_blank" rel="noopener noreferrer nofollow">X Profile</a>' in rendered
    assert '<a href="https://patreon.com/foxtontonton" target="_blank" rel="noopener noreferrer nofollow">Patreon Page</a>' in rendered


def test_markdown_link_special_characters_in_url():
    """测试 URL 中包含特殊查询参数 (&, ?, =, #) 的安全转义与解析"""
    text = "Check out [My Gallery](https://example.com/art?tag=oc&sort=latest#top)"
    rendered = render_markdown_signature_to_html(text)

    assert '<a href="https://example.com/art?tag=oc&amp;sort=latest#top" target="_blank" rel="noopener noreferrer nofollow">My Gallery</a>' in rendered


def test_unclosed_brackets_fallback_without_crashing():
    """测试未闭合中括号或孤立中括号不崩溃且原样保持文本"""
    bad_text = "Here is [unfinished bracket and [real link](https://ko-fi.com/foxtontonton) and [another unclosed"
    rendered = render_markdown_signature_to_html(bad_text)

    assert "[unfinished bracket and" in rendered
    assert "[another unclosed" in rendered
    assert '<a href="https://ko-fi.com/foxtontonton" target="_blank" rel="noopener noreferrer nofollow">real link</a>' in rendered


def test_legacy_template_backward_compatibility():
    """测试旧版无 markdown 的纯文本签名向后兼容"""
    legacy = "---\nSupport my work:\nPatreon: https://patreon.com/yourname\nKo-fi: https://ko-fi.com/yourname"
    rendered = render_markdown_signature_to_html(legacy)

    assert '<a href=' not in rendered
    assert "Support my work:" in rendered
    assert "Patreon: https://patreon.com/yourname" in rendered


def test_body_brackets_strictly_kept_as_plain_text():
    """测试正文中的方括号 ([OC], [1/3], [Artist]) 绝对不得被作为链接解析"""
    full_desc = (
        "Artwork title: [OC] Demon King [Part 1/3]\n"
        "Artist: [Self-taught & proud]\n"
        "Check <brackets> & symbols.\n\n"
        + CANONICAL_SIGNATURE_TEMPLATE
    )

    body, sig = split_body_and_signature(full_desc)
    assert "[OC] Demon King [Part 1/3]" in body
    assert "[Self-taught & proud]" in body
    assert "Where to find me" in sig

    html_out = render_description_to_html(full_desc)
    # 正文中的方括号绝不能有 <a> 标签
    assert "<p>Artwork title: [OC] Demon King [Part 1/3]<br>Artist: [Self-taught &amp; proud]<br>Check &lt;brackets&gt; &amp; symbols.</p>" in html_out
    # 签名中的 6 个链接必须有 <a> 标签
    assert '<a href="https://x.com/FOXTONTONTON"' in html_out
    assert '<a href="https://www.deviantart.com/foxtontonton"' in html_out
    assert '<a href="https://civitai.red/user/FOXTONTONTON"' in html_out
    assert '<a href="https://bsky.app/profile/foxtontonton.bsky.social"' in html_out


def test_render_description_to_plain_text():
    """测试纯文本转换：[Display](URL) -> Display，完全去除 markdown 括号"""
    plain = render_description_to_plain_text(CANONICAL_SIGNATURE_TEMPLATE)
    assert "[@FOXTONTONTON]" not in plain
    assert "@FOXTONTONTON" in plain
    assert "(https://x.com/FOXTONTONTON)" not in plain
    assert "(https://civitai.red/user/FOXTONTONTON)" not in plain
    assert "💎Civitai: FOXTONTONTON" in plain
    assert "🦋Bluesky: FOXTONTONTON" in plain
    assert "(https://bsky.app/profile/foxtontonton.bsky.social)" not in plain


def test_verify_signature_links_all_six_present():
    """测试 6 链接全齐通过断言"""
    html_sample = (
        '<p>Body text</p>'
        '<p>🎬X: <a href="https://x.com/FOXTONTONTON">@FOXTONTONTON</a></p>'
        '<p>🖼️DeviantArt: <a href="https://www.deviantart.com/foxtontonton/">FOXTONTONTON</a></p>'
        '<p>💜Patreon：<a href="https://patreon.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>☕Ko-fi: <a href="https://ko-fi.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💎Civitai: <a href="https://civitai.red/user/FOXTONTONTON">FOXTONTONTON</a></p>'
        '<p>🦋Bluesky: <a href="https://bsky.app/profile/foxtontonton.bsky.social">FOXTONTONTON</a></p>'
    )
    ok, missing = verify_signature_links_in_html(html_sample)
    assert ok is True
    assert len(missing) == 0


def test_verify_signature_links_missing_one_fails():
    """测试故意少 1 个链接 (如 Civitai 缺失) 时断言拦截并准确报告"""
    html_sample = (
        '<p>Body text</p>'
        '<p>🎬X: <a href="https://x.com/FOXTONTONTON">@FOXTONTONTON</a></p>'
        '<p>🖼️DeviantArt: <a href="https://www.deviantart.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💜Patreon：<a href="https://patreon.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>☕Ko-fi: <a href="https://ko-fi.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>🦋Bluesky: <a href="https://bsky.app/profile/foxtontonton.bsky.social">FOXTONTONTON</a></p>'
        # Civitai omitted
    )
    ok, missing = verify_signature_links_in_html(html_sample)
    assert ok is False
    assert len(missing) == 1
    assert "Civitai" in missing[0]
    assert "https://civitai.red/user/FOXTONTONTON" in missing[0]


def test_verify_signature_links_plain_text_rejected():
    """测试纯文本填入 (禁止纯文本填了就算成功)，所有 5 个链接全部判定缺失"""
    plain_text_html = (
        "<p>Where to find me — same name everywhere:</p>"
        "<p>🎬X: [@FOXTONTONTON](https://x.com/FOXTONTONTON)</p>"
        "<p>🖼️DeviantArt: [FOXTONTONTON](https://www.deviantart.com/foxtontonton)</p>"
        "<p>💜Patreon：[FOXTONTONTON](https://patreon.com/foxtontonton)</p>"
        "<p>☕Ko-fi: [FOXTONTONTON](https://ko-fi.com/foxtontonton)</p>"
        "<p>💎Civitai: [FOXTONTONTON](https://civitai.red/user/FOXTONTONTON)</p>"
        "<p>🦋Bluesky: [FOXTONTONTON](https://bsky.app/profile/foxtontonton.bsky.social)</p>"
    )
    ok, missing = verify_signature_links_in_html(plain_text_html)
    assert ok is False
    assert len(missing) == 6


def test_verify_signature_links_da_post_url_propagation():
    """测试 DA 当帖链接透传到 Patreon 时，DeviantArt 链接允许匹配当帖 URL"""
    da_post_url = "https://www.deviantart.com/foxtontonton/art/New-Alpha-Post-999999"
    html_sample = (
        '<p>Body text</p>'
        '<p>🎬X: <a href="https://x.com/FOXTONTONTON">@FOXTONTONTON</a></p>'
        f'<p>🖼️DeviantArt: <a href="{da_post_url}">FOXTONTONTON</a></p>'
        '<p>💜Patreon：<a href="https://patreon.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>☕Ko-fi: <a href="https://ko-fi.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💎Civitai: <a href="https://civitai.red/user/FOXTONTONTON">FOXTONTONTON</a></p>'
        '<p>🦋Bluesky: <a href="https://bsky.app/profile/foxtontonton.bsky.social">FOXTONTONTON</a></p>'
    )
    # 不传 da_post_url 时 DA 会因为不匹配主页而报错
    ok_no_prop, missing_no_prop = verify_signature_links_in_html(html_sample)
    assert ok_no_prop is False
    assert any("DeviantArt" in m for m in missing_no_prop)

    # 传入 da_post_url 后透传匹配成功
    ok_with_prop, missing_with_prop = verify_signature_links_in_html(html_sample, da_post_url=da_post_url)
    assert ok_with_prop is True
    assert len(missing_with_prop) == 0


def test_verify_signature_links_da_query_params_and_path_matching():
    """测试 DA 链接剥查询串与路径比对: 无论输入或 DOM 中是否带有参数，均按路径匹配通过"""
    da_clean_url = "https://www.deviantart.com/foxtontonton/art/New-Alpha-Post-888888"
    da_url_with_query = da_clean_url + "?ga_submit_new=10%3A123456789&ga_type=edit#comments"

    # DOM 中的 href 带有其他参数或末尾斜杠
    html_sample_with_query = (
        '<p>Body text</p>'
        '<p>🎬X: <a href="https://x.com/FOXTONTONTON">@FOXTONTONTON</a></p>'
        f'<p>🖼️DeviantArt: <a href="{da_clean_url}/?attributes=test">FOXTONTONTON</a></p>'
        '<p>💜Patreon：<a href="https://patreon.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>☕Ko-fi: <a href="https://ko-fi.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💎Civitai: <a href="https://civitai.red/user/FOXTONTONTON">FOXTONTONTON</a></p>'
        '<p>🦋Bluesky: <a href="https://bsky.app/profile/foxtontonton.bsky.social">FOXTONTONTON</a></p>'
    )

    # 传入带查询串的 da_post_url，应自动剥除参数并按路径与 DOM 中的 href 匹配成功
    ok, missing = verify_signature_links_in_html(html_sample_with_query, da_post_url=da_url_with_query)
    assert ok is True
    assert len(missing) == 0


# ==================== Dispatcher Integration Tests ====================
import time
from unittest.mock import MagicMock, patch
from pathlib import Path
from dispatchers.deviantart import DeviantArtDispatcher
from dispatchers.civitai import CivitaiDispatcher
from dispatchers.monetization import PatreonDispatcher


def _create_mock_da_desc_area(html_content, preset_text=""):
    mock_el = MagicMock()
    def fake_eval(script, *args):
        s = str(script)
        if "innerHTML" in s:
            return html_content
        if "innerText" in s or "textContent" in s:
            return preset_text
        return "execCommand"
    mock_el.evaluate.side_effect = fake_eval
    mock_el.inner_html.return_value = html_content
    mock_el.inner_text.return_value = preset_text
    return mock_el


def test_deviantart_dispatcher_signature_readback_success(tmp_path, monkeypatch):
    """测试 DA 分发器包含签名时，5 个超链接回读断言成功通过"""
    monkeypatch.setattr(time, "sleep", lambda x: None)

    disp = DeviantArtDispatcher(tmp_path)
    disp.safe_goto = MagicMock()
    disp.assert_attached = MagicMock(return_value=(True, ""))
    disp.assert_field_value = MagicMock(return_value=(True, ""))
    disp.check_selector_state = MagicMock(return_value=(True, "ok"))
    disp.assert_submitted = MagicMock(return_value=(True, ""))
    disp._cleanup_orphan_draft = MagicMock()

    valid_html = (
        '<p>Work description</p>'
        '<p>🎬X: <a href="https://x.com/FOXTONTONTON">@FOXTONTONTON</a></p>'
        '<p>🖼️DeviantArt: <a href="https://www.deviantart.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💜Patreon：<a href="https://patreon.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>☕Ko-fi: <a href="https://ko-fi.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💎Civitai: <a href="https://civitai.red/user/FOXTONTONTON">FOXTONTONTON</a></p>'
        '<p>🦋Bluesky: <a href="https://bsky.app/profile/foxtontonton.bsky.social">FOXTONTONTON</a></p>'
    )
    mock_desc_area = _create_mock_da_desc_area(valid_html)

    mock_page = MagicMock()
    mock_page.url = "https://www.deviantart.com/art/view-12345"
    mock_cover_fi = MagicMock()
    mock_submit_btn = MagicMock()
    mock_submit_btn.inner_text.return_value = "Submit"
    mock_submit_btn.is_enabled.return_value = True
    mock_submit_btn.get_attribute.return_value = "false"

    def fake_qs(sel):
        if "input[type='file']" in sel:
            return mock_cover_fi
        if "Submit" in sel or "submit" in sel:
            return mock_submit_btn
        if "description" in sel or "tiptap" in sel or "role='textbox'" in sel:
            return mock_desc_area
        return MagicMock()

    def fake_qs_all(sel):
        if "Submit" in sel or "submit" in sel or "button" in sel:
            return [mock_submit_btn]
        if "input[type='file']" in sel:
            return [mock_cover_fi]
        return []

    mock_page.query_selector = MagicMock(side_effect=fake_qs)
    mock_page.query_selector_all = MagicMock(side_effect=fake_qs_all)
    mock_page.evaluate = MagicMock(return_value={"count": 1, "llama": False})

    mock_context = MagicMock()
    mock_studio_page = MagicMock()
    mock_studio_page.inner_text.return_value = "No drafts"
    mock_studio_page.url = "https://www.deviantart.com/studio"
    mock_context.new_page.return_value = mock_studio_page

    img_file = tmp_path / "art.jpg"
    img_file.write_bytes(b"dummy")

    comp_list = []
    desc_with_sig = "My new art\n\n" + CANONICAL_SIGNATURE_TEMPLATE
    ok, msg = disp._submit_deviation(
        mock_page, mock_context, [str(img_file)], "Title", desc_with_sig,
        ["tag1"], False, "strict", False, "", comp_list
    )

    assert ok is True
    assert msg == ""
    assert any(c.get("item") == "signature_links" and "6/6 真超链接已生效" in c.get("detail", "") for c in comp_list)


def test_deviantart_dispatcher_signature_readback_missing_link_aborts(tmp_path, monkeypatch):
    """测试 DA 分发器当编辑器回读缺失超链接时，立即报错阻断发布"""
    monkeypatch.setattr(time, "sleep", lambda x: None)

    disp = DeviantArtDispatcher(tmp_path)
    disp.safe_goto = MagicMock()
    disp.assert_attached = MagicMock(return_value=(True, ""))
    disp.assert_field_value = MagicMock(return_value=(True, ""))
    disp.check_selector_state = MagicMock(return_value=(True, "ok"))
    disp.assert_submitted = MagicMock(return_value=(True, ""))
    disp._cleanup_orphan_draft = MagicMock()

    # 故意缺失 Civitai 链接
    incomplete_html = (
        '<p>Work description</p>'
        '<p>🎬X: <a href="https://x.com/FOXTONTONTON">@FOXTONTONTON</a></p>'
        '<p>🖼️DeviantArt: <a href="https://www.deviantart.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💜Patreon：<a href="https://patreon.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>☕Ko-fi: <a href="https://ko-fi.com/foxtontonton">FOXTONTONTON</a></p>'
    )
    mock_desc_area = _create_mock_da_desc_area(incomplete_html)

    mock_page = MagicMock()
    mock_page.url = "https://www.deviantart.com/art/view-12345"
    mock_cover_fi = MagicMock()
    mock_submit_btn = MagicMock()

    def fake_qs(sel):
        if "input[type='file']" in sel:
            return mock_cover_fi
        if "Submit" in sel:
            return mock_submit_btn
        if "description" in sel or "tiptap" in sel or "role='textbox'" in sel:
            return mock_desc_area
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_qs)
    mock_page.query_selector_all = MagicMock(return_value=[mock_cover_fi])
    mock_page.evaluate = MagicMock(return_value={"count": 1, "llama": False})

    mock_context = MagicMock()
    img_file = tmp_path / "art.jpg"
    img_file.write_bytes(b"dummy")

    comp_list = []
    desc_with_sig = "My new art\n\n" + CANONICAL_SIGNATURE_TEMPLATE
    ok, msg = disp._submit_deviation(
        mock_page, mock_context, [str(img_file)], "Title", desc_with_sig,
        ["tag1"], False, "strict", False, "", comp_list
    )

    assert ok is False
    assert "签名超链接断言失败" in msg
    assert "Civitai" in msg
    mock_submit_btn.click.assert_not_called()
    mock_submit_btn.evaluate.assert_not_called()


def test_deviantart_keep_preset_with_signature_links(tmp_path, monkeypatch):
    """测试 DA 预设拼接兼容：有预设时保留拼接逻辑，程序签名渲染为真链接"""
    monkeypatch.setattr(time, "sleep", lambda x: None)

    disp = DeviantArtDispatcher(tmp_path)
    disp.safe_goto = MagicMock()
    disp.assert_attached = MagicMock(return_value=(True, ""))
    disp.assert_field_value = MagicMock(return_value=(True, ""))
    disp.check_selector_state = MagicMock(return_value=(True, "ok"))
    disp.assert_submitted = MagicMock(return_value=(True, ""))
    disp._cleanup_orphan_draft = MagicMock()

    valid_html = (
        '<p>New Program Artwork</p>'
        '<p>🎬X: <a href="https://x.com/FOXTONTONTON">@FOXTONTONTON</a></p>'
        '<p>🖼️DeviantArt: <a href="https://www.deviantart.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💜Patreon：<a href="https://patreon.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>☕Ko-fi: <a href="https://ko-fi.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💎Civitai: <a href="https://civitai.red/user/FOXTONTONTON">FOXTONTONTON</a></p>'
        '<p>🦋Bluesky: <a href="https://bsky.app/profile/foxtontonton.bsky.social">FOXTONTONTON</a></p>'
        '<p>Existing Preset Artist Bio</p>'
    )
    mock_desc_area = _create_mock_da_desc_area(valid_html, preset_text="Existing Preset Artist Bio")

    mock_page = MagicMock()
    mock_page.url = "https://www.deviantart.com/art/view-12345"
    mock_cover_fi = MagicMock()
    mock_submit_btn = MagicMock()
    mock_submit_btn.inner_text.return_value = "Submit"
    mock_submit_btn.is_enabled.return_value = True
    mock_submit_btn.get_attribute.return_value = "false"

    def fake_qs(sel):
        if "input[type='file']" in sel:
            return mock_cover_fi
        if "Submit" in sel or "submit" in sel:
            return mock_submit_btn
        if "description" in sel or "tiptap" in sel or "role='textbox'" in sel:
            return mock_desc_area
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_qs)
    mock_page.query_selector_all = MagicMock(side_effect=lambda sel: [mock_submit_btn] if "submit" in sel or "button" in sel else [mock_cover_fi])
    mock_page.evaluate = MagicMock(return_value={"count": 1, "llama": False})

    mock_context = MagicMock()
    mock_studio_page = MagicMock()
    mock_studio_page.inner_text.return_value = "No drafts"
    mock_studio_page.url = "https://www.deviantart.com/studio"
    mock_context.new_page.return_value = mock_studio_page

    img_file = tmp_path / "art.jpg"
    img_file.write_bytes(b"dummy")

    comp_list = []
    desc_with_sig = "New Program Artwork\n\n" + CANONICAL_SIGNATURE_TEMPLATE
    ok, msg = disp._submit_deviation(
        mock_page, mock_context, [str(img_file)], "Title", desc_with_sig,
        ["tag1"], False, "strict", False, "", comp_list,
        keep_preset_description=True
    )

    assert ok is True
    assert msg == ""
    assert getattr(disp, "_last_desc_source", None) == "拼接"
    assert any(c.get("item") == "description_source" and c.get("detail") == "拼接" for c in comp_list)
    assert any(c.get("item") == "signature_links" and "6/6 真超链接已生效" in c.get("detail", "") for c in comp_list)


def test_civitai_dispatcher_signature_readback_success(tmp_path, monkeypatch):
    """测试 Civitai 分发器富文本描述 5 个链接全部渲染成功放行"""
    monkeypatch.setattr(time, "sleep", lambda x: None)

    cookies_dir = tmp_path / "cookies"
    cookies_dir.mkdir()
    disp = CivitaiDispatcher(cookies_dir)

    dummy_img = tmp_path / "test.png"
    dummy_img.write_bytes(b"data")

    valid_html = (
        '<p>Civitai post body</p>'
        '<p>🎬X: <a href="https://x.com/FOXTONTONTON">@FOXTONTONTON</a></p>'
        '<p>🖼️DeviantArt: <a href="https://www.deviantart.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💜Patreon：<a href="https://patreon.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>☕Ko-fi: <a href="https://ko-fi.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💎Civitai: <a href="https://civitai.red/user/FOXTONTONTON">FOXTONTONTON</a></p>'
        '<p>🦋Bluesky: <a href="https://bsky.app/profile/foxtontonton.bsky.social">FOXTONTONTON</a></p>'
    )
    mock_desc_box = MagicMock()
    mock_desc_box.evaluate.side_effect = lambda script, *args: valid_html if "innerHTML" in str(script) else "execCommand"

    mock_page = MagicMock()
    mock_page.url = "https://civitai.com/posts/31951550/edit"
    mock_pub_btn = MagicMock()
    mock_pub_btn.is_disabled.return_value = False
    mock_saved_el = MagicMock()
    mock_view_post_btn = MagicMock()
    mock_view_post_btn.is_visible.return_value = True

    def fake_qs(sel):
        if "input[type='file']" in sel:
            return MagicMock()
        if "ProseMirror" in sel:
            return mock_desc_box
        if "Publish" in sel:
            return mock_pub_btn
        if "SAVED" in sel:
            return mock_saved_el
        if "View Post" in sel:
            return mock_view_post_btn
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_qs)
    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    payload = {
        "title": "Civitai Sig Test",
        "description": "My Civitai art\n\n" + CANONICAL_SIGNATURE_TEMPLATE,
        "tags": [],
        "civitai_images": [str(dummy_img)],
        "civitai_max_images_per_post": 20
    }

    with patch.object(disp, "create_persistent_stealth_context", return_value=mock_context), \
         patch.object(disp, "safe_goto"), \
         patch.object(disp, "delete_orphan_draft") as mock_delete:
        ok, msg, comp = disp.publish(payload)

    assert ok is True
    assert "https://civitai.com/posts/31951550" in msg
    assert any(c.get("item") == "signature_links" and "6/6 真超链接已生效" in c.get("detail", "") for c in comp)
    mock_delete.assert_not_called()


def test_civitai_dispatcher_signature_readback_missing_aborts_and_cleans_draft(tmp_path, monkeypatch):
    """测试 Civitai 分发器签名链接缺失时，拦截发布并清理孤儿草稿"""
    monkeypatch.setattr(time, "sleep", lambda x: None)

    cookies_dir = tmp_path / "cookies"
    cookies_dir.mkdir()
    disp = CivitaiDispatcher(cookies_dir)

    dummy_img = tmp_path / "test.png"
    dummy_img.write_bytes(b"data")

    # 纯文本 (无 <a>)
    plain_html = "<p>🎬X: [@FOXTONTONTON](https://x.com/FOXTONTONTON)</p>"
    mock_desc_box = MagicMock()
    mock_desc_box.evaluate.side_effect = lambda script, *args: plain_html if "innerHTML" in str(script) else "execCommand"

    mock_page = MagicMock()
    mock_page.url = "https://civitai.com/posts/31951550/edit"
    mock_pub_btn = MagicMock()
    mock_pub_btn.is_disabled.return_value = False
    mock_saved_el = MagicMock()

    def fake_qs(sel):
        if "input[type='file']" in sel:
            return MagicMock()
        if "ProseMirror" in sel:
            return mock_desc_box
        if "Publish" in sel:
            return mock_pub_btn
        if "SAVED" in sel:
            return mock_saved_el
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_qs)
    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    payload = {
        "title": "Civitai Fail Test",
        "description": "My Civitai art\n\n" + CANONICAL_SIGNATURE_TEMPLATE,
        "tags": [],
        "civitai_images": [str(dummy_img)],
        "civitai_max_images_per_post": 20
    }

    with patch.object(disp, "create_persistent_stealth_context", return_value=mock_context), \
         patch.object(disp, "safe_goto"), \
         patch.object(disp, "delete_orphan_draft") as mock_delete:
        ok, msg, comp = disp.publish(payload)

    assert ok is False
    assert "签名超链接断言失败" in msg
    mock_pub_btn.click.assert_not_called()
    mock_delete.assert_called_once_with(mock_page, "31951550")


def test_patreon_dispatcher_signature_readback_success(tmp_path, monkeypatch):
    """测试 Patreon 分发器签名 5 链接全齐通过发布"""
    monkeypatch.setattr(time, "sleep", lambda x: None)

    disp = PatreonDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": "session_id", "value": "xyz"}])
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))
    disp.safe_goto = MagicMock()
    disp.clean_context_tabs = MagicMock()
    disp.apply_stealth = MagicMock()
    disp.close_login_session = MagicMock()
    disp.assert_field_value = MagicMock(return_value=(True, ""))
    disp.assert_attached = MagicMock(return_value=(True, ""))
    disp.assert_submitted = MagicMock(return_value=(True, ""))
    disp._count_patreon_gallery_images = MagicMock(return_value=1)

    valid_html = (
        '<p>Patreon body</p>'
        '<p>🎬X: <a href="https://x.com/FOXTONTONTON">@FOXTONTONTON</a></p>'
        '<p>🖼️DeviantArt: <a href="https://www.deviantart.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💜Patreon：<a href="https://patreon.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>☕Ko-fi: <a href="https://ko-fi.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💎Civitai: <a href="https://civitai.red/user/FOXTONTONTON">FOXTONTONTON</a></p>'
        '<p>🦋Bluesky: <a href="https://bsky.app/profile/foxtontonton.bsky.social">FOXTONTONTON</a></p>'
    )
    mock_desc_box = MagicMock()
    mock_desc_box.evaluate.side_effect = lambda script, *args: valid_html if "innerHTML" in str(script) else "execCommand"

    mock_page = MagicMock()
    mock_page.url = "https://www.patreon.com/FOXTONTONTON/posts/12345"
    mock_file_input = MagicMock()
    mock_inline = MagicMock()
    mock_inline.is_visible.return_value = True

    def fake_qs(sel):
        if "input#mainMedia" in sel or "photosInput" in sel:
            return mock_file_input
        if "patreon-media" in sel or "media-item" in sel:
            return mock_inline
        if "contenteditable" in sel or "ProseMirror" in sel:
            return mock_desc_box
        if "Title" in sel or "title" in sel:
            return MagicMock()
        if "Publish" in sel:
            btn = MagicMock()
            btn.is_disabled.return_value = False
            btn.get_attribute.return_value = "false"
            return btn
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_qs)
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    img = tmp_path / "img.png"
    img.write_bytes(b"data")

    payload = {
        "title": "Patreon Sig Post",
        "description": "Exclusive artwork\n\n" + CANONICAL_SIGNATURE_TEMPLATE,
        "patreon_images": [str(img)],
        "tags": []
    }

    ok, msg, comp = disp.publish(payload)
    assert ok is True
    assert any(c.get("item") == "signature_links" and "6/6 真超链接已生效" in c.get("detail", "") for c in comp)


def test_patreon_dispatcher_signature_missing_link_aborts(tmp_path, monkeypatch):
    """测试 Patreon 分发器签名链接缺失时，拦截发布并返回错误"""
    monkeypatch.setattr(time, "sleep", lambda x: None)

    disp = PatreonDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": "session_id", "value": "xyz"}])
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))
    disp.safe_goto = MagicMock()
    disp.clean_context_tabs = MagicMock()
    disp.apply_stealth = MagicMock()
    disp.close_login_session = MagicMock()
    disp.assert_field_value = MagicMock(return_value=(True, ""))

    # 缺失 Ko-fi 链接
    incomplete_html = (
        '<p>🎬X: <a href="https://x.com/FOXTONTONTON">@FOXTONTONTON</a></p>'
        '<p>🖼️DeviantArt: <a href="https://www.deviantart.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💜Patreon：<a href="https://patreon.com/foxtontonton">FOXTONTONTON</a></p>'
        '<p>💎Civitai: <a href="https://civitai.red/user/FOXTONTONTON">FOXTONTONTON</a></p>'
        '<p>🦋Bluesky: <a href="https://bsky.app/profile/foxtontonton.bsky.social">FOXTONTONTON</a></p>'
    )
    mock_desc_box = MagicMock()
    mock_desc_box.evaluate.side_effect = lambda script, *args: incomplete_html if "innerHTML" in str(script) else "execCommand"

    mock_page = MagicMock()
    mock_page.url = "https://www.patreon.com/posts/new"
    mock_pub_btn = MagicMock()
    mock_file_input = MagicMock()
    mock_inline = MagicMock()
    mock_inline.is_visible.return_value = True

    def fake_qs(sel):
        if "input#mainMedia" in sel or "photosInput" in sel:
            return mock_file_input
        if "patreon-media" in sel or "media-item" in sel:
            return mock_inline
        if "contenteditable" in sel or "ProseMirror" in sel:
            return mock_desc_box
        if "Title" in sel or "title" in sel:
            return MagicMock()
        if "Publish" in sel:
            return mock_pub_btn
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_qs)
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    img = tmp_path / "img.png"
    img.write_bytes(b"data")

    payload = {
        "title": "Patreon Fail Post",
        "description": "Exclusive artwork\n\n" + CANONICAL_SIGNATURE_TEMPLATE,
        "patreon_images": [str(img)],
        "tags": []
    }

    ok, msg, comp = disp.publish(payload)
    assert ok is False
    assert "签名超链接断言失败" in msg
    assert "Ko-fi" in msg
    assert "实际回读 href 清单" in msg
    assert "https://x.com/FOXTONTONTON" in msg
    mock_pub_btn.click.assert_not_called()


def test_bluesky_signature_markdown_link_conversion():
    """验证 Bluesky 签名转换为裸 URL 纯文本且无 []() 语法标记残留"""
    from dispatchers.bluesky import extract_facets

    converted = convert_signature_links_for_bluesky(CANONICAL_SIGNATURE_TEMPLATE)
    assert "[" not in converted and "]" not in converted
    for _, u in CANONICAL_SIGNATURE_LINKS:
        assert u in converted

    facets = extract_facets(converted)
    link_facets = [f for f in facets if f["features"][0].get("$type") == "app.bsky.richtext.facet#link"]
    assert len(link_facets) == 6
    uris = [f["features"][0]["uri"] for f in link_facets]
    for _, u in CANONICAL_SIGNATURE_LINKS:
        assert u in uris


def test_bluesky_description_rendering_preserves_body_brackets():
    """验证 Bluesky 描述组装时正文普通文本/方括号严格不受影响"""
    from dispatchers.bluesky import extract_facets

    body = "Brand new art piece [OC] [Chapter 2/3] created by [Artist]"
    full_text = f"{body}\n\n{CANONICAL_SIGNATURE_TEMPLATE}"

    rendered = render_description_for_bluesky(full_text)
    assert "[OC]" in rendered
    assert "[Chapter 2/3]" in rendered
    assert "[Artist]" in rendered

    body_res, sig_res = split_body_and_signature(rendered)
    assert "[" in body_res and "]" in body_res
    assert "[" not in sig_res and "]" not in sig_res

    facets = extract_facets(rendered)
    link_uris = [f["features"][0]["uri"] for f in facets if f["features"][0].get("$type") == "app.bsky.richtext.facet#link"]
    assert len(link_uris) == 6
    for _, u in CANONICAL_SIGNATURE_LINKS:
        assert u in link_uris


