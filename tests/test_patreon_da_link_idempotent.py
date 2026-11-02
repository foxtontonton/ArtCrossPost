import time
from unittest.mock import MagicMock
import pytest
from config import CANONICAL_SIGNATURE_TEMPLATE
from dispatchers.monetization import (
    PatreonDispatcher,
    extract_html_hrefs,
    render_description_to_html,
)


def _setup_mock_patreon(tmp_path, monkeypatch):
    """构建快速无头的 PatreonDispatcher 模拟环境，截获编辑器收到的正文与 HTML"""
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

    captured_html = []
    mock_desc_box = MagicMock()

    def fake_evaluate(script, *args):
        if len(args) > 0 and isinstance(args[0], str):
            captured_html.append(args[0])
        if "innerHTML" in str(script):
            return captured_html[-1] if captured_html else ""
        return "ok"

    mock_desc_box.evaluate.side_effect = fake_evaluate

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
        btn = MagicMock()
        btn.is_disabled.return_value = False
        btn.get_attribute.return_value = "false"
        return btn

    mock_page.query_selector = MagicMock(side_effect=fake_qs)
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    img = tmp_path / "img.png"
    img.write_bytes(b"data")

    captured_desc = []
    orig_render = render_description_to_html

    def spy_render(d):
        captured_desc.append(d)
        return orig_render(d)

    monkeypatch.setattr("dispatchers.monetization.render_description_to_html", spy_render)

    return disp, captured_desc, captured_html, str(img)


def test_patreon_desc_already_has_da_post_url_verbatim_unchanged(tmp_path, monkeypatch):
    """
    测试 1：已含当帖 URL 时逐字不变
    当 patreon_description 在阶段间已由 app.py 替换为当帖 URL 时，
    monetization.py 中的三序第 ① 步命中，跳过全部替换，正文保持逐字不变，绝不叠加路径。
    """
    disp, captured_desc, captured_html, img_path = _setup_mock_patreon(tmp_path, monkeypatch)

    da_post_url = "https://www.deviantart.com/foxtontonton/art/Alpha-Artwork-123456"
    # 模拟 app.py 阶段间已把主页链接替换为当帖 URL
    input_desc = ("Exclusive creation for supporters.\n\n" + CANONICAL_SIGNATURE_TEMPLATE).replace(
        "https://www.deviantart.com/foxtontonton", da_post_url
    )

    payload = {
        "title": "Idempotent Test Post 1",
        "patreon_description": input_desc,
        "da_post_url": da_post_url,
        "patreon_images": [img_path],
        "tags": []
    }

    ok, msg, comp = disp.publish(payload)
    assert ok is True

    # 1. 验证正文逐字不变
    assert len(captured_desc) == 1
    assert captured_desc[0] == input_desc

    # 2. 验证提取出的 href 中 DA 链接精确匹配当帖 URL，无任何路径叠加 (/art/xxx/art/xxx)
    assert len(captured_html) == 1
    hrefs = extract_html_hrefs(captured_html[0])
    assert da_post_url in hrefs
    assert not any("/art/Alpha-Artwork-123456/art" in h for h in hrefs)
    # 统计 /art/ 出现次数，断言仅出现 1 次
    assert captured_html[0].count("/art/Alpha-Artwork-123456") == 1


def test_patreon_desc_with_homepage_replaces_once_in_place(tmp_path, monkeypatch):
    """
    测试 2：含主页时替换一次到位
    当 patreon_description 仍为主页链接时，第 ① 步未命中，走第 ②/③ 步替换一次到位。
    """
    disp, captured_desc, captured_html, img_path = _setup_mock_patreon(tmp_path, monkeypatch)

    da_home_url = "https://www.deviantart.com/foxtontonton"
    da_post_url = "https://www.deviantart.com/foxtontonton/art/Beta-Artwork-234567"
    input_desc = "Exclusive creation for supporters.\n\n" + CANONICAL_SIGNATURE_TEMPLATE

    payload = {
        "title": "Idempotent Test Post 2",
        "patreon_description": input_desc,
        "da_post_url": da_post_url,
        "x_da_link_original": da_home_url,
        "patreon_images": [img_path],
        "tags": []
    }

    ok, msg, comp = disp.publish(payload)
    assert ok is True

    # 1. 验证主页链接被替换为当帖 URL
    assert len(captured_desc) == 1
    assert da_post_url in captured_desc[0]
    assert da_home_url not in captured_desc[0].replace(da_post_url, "")

    # 2. 验证 HTML 中的 href 替换一次到位，无二次叠加
    assert len(captured_html) == 1
    hrefs = extract_html_hrefs(captured_html[0])
    assert da_post_url in hrefs
    assert not any("/art/Beta-Artwork-234567/art" in h for h in hrefs)


def test_patreon_desc_with_query_param_does_not_overlay(tmp_path, monkeypatch):
    """
    测试 3：含带 ?action=published 的当帖 URL 时不叠加
    实机 DA 发布后回读的当帖 URL 往往带有 ?action=published 等查询串。
    当 patreon_description 已包含此类带参数 URL 时，规范化后的 da_post_url 依然判定命中，
    跳过替换，杜绝将主页前缀误当替换目标而产生 /art/<id>/art/<id> 叠加。
    """
    disp, captured_desc, captured_html, img_path = _setup_mock_patreon(tmp_path, monkeypatch)

    clean_post_url = "https://www.deviantart.com/foxtontonton/art/Gamma-Artwork-345678"
    query_post_url = f"{clean_post_url}?action=published"
    input_desc = ("Exclusive creation for supporters.\n\n" + CANONICAL_SIGNATURE_TEMPLATE).replace(
        "https://www.deviantart.com/foxtontonton", query_post_url
    )

    # payload 中传入可能带或不带参数的 da_post_url
    payload = {
        "title": "Idempotent Test Post 3",
        "patreon_description": input_desc,
        "da_post_url": query_post_url,
        "x_da_link_original": "https://www.deviantart.com/foxtontonton",
        "patreon_images": [img_path],
        "tags": []
    }

    ok, msg, comp = disp.publish(payload)
    assert ok is True

    # 1. 验证正文未被破坏叠加
    assert len(captured_desc) == 1
    assert captured_desc[0] == input_desc

    # 2. 验证 HTML 中的 href 正确且未产生叠加
    assert len(captured_html) == 1
    hrefs = extract_html_hrefs(captured_html[0])
    assert query_post_url in hrefs
    assert not any("/art/Gamma-Artwork-345678/art" in h for h in hrefs)
    assert captured_html[0].count("/art/Gamma-Artwork-345678") == 1
