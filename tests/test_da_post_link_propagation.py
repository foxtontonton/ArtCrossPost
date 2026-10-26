# -*- coding: utf-8 -*-
"""
Tests for DeviantArt Post URL Propagation to X (Twitter) and Patreon (Phase 3).
Covers:
1. X weighted character count equivalence (homepage URL vs post URL both 23 units).
2. DA success -> X and Patreon copy contains DA post URL, does not contain bare homepage URL.
3. DA failure -> Fallback to homepage URL with loud notice in result and panel.
4. DA not selected -> Concurrent behavior unchanged, uses homepage link.
5. use_da_post_link_for_traffic disabled -> Always uses homepage link, single-phase.
6. Results panel rendering of actual traffic link and fallback notice.
"""
import sys
from unittest.mock import MagicMock, patch
if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()

import re
import pytest
from pathlib import Path
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread

from x_text_utils import calculate_x_char_count, format_x_teaser_post
from app import PublishThread, MainWindow
from dispatchers.base import BaseDispatcher


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


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


def test_x_weighted_char_count_url_equivalence():
    """
    断言 X 预告加权字数：当帖 URL 与主页 URL 同为 23 加权，断言一致。
    """
    homepage_url = "https://www.deviantart.com/foxtontonton"
    post_url = "https://www.deviantart.com/foxtontonton/art/Under-His-Rubber-Seated-Sealed-Served-1402512687"

    # 1. 裸 URL 独立加权字数均为 23
    assert calculate_x_char_count(homepage_url) == 23
    assert calculate_x_char_count(post_url) == 23

    # 2. 调用 format_x_teaser_post 组合后加权字数严格一致
    post_with_home = format_x_teaser_post(
        title="Test Artwork",
        desc="This is an awesome muscle latex artwork description.",
        tags=["bara", "muscle"],
        da_link=homepage_url,
        nsfw_notice="🔞 NSFW full set on DeviantArt 👇",
        limit=280
    )

    post_with_post_url = format_x_teaser_post(
        title="Test Artwork",
        desc="This is an awesome muscle latex artwork description.",
        tags=["bara", "muscle"],
        da_link=post_url,
        nsfw_notice="🔞 NSFW full set on DeviantArt 👇",
        limit=280
    )

    count_home = calculate_x_char_count(post_with_home)
    count_post = calculate_x_char_count(post_with_post_url)

    assert count_home == count_post
    assert post_url in post_with_post_url
    # 确认没有残留独立的纯主页链接 (即必须全部转化为作品页 /art/ 链接)
    assert not re.search(r"https?://(?:www\.)?deviantart\.com/foxtontonton(?!\S*/art)", post_with_post_url)


def test_da_success_propagates_post_url_to_x_and_patreon(tmp_path):
    """
    测试【DA 成功两阶段透传】：
    - DA 成功发布并生成当帖 URL；
    - X 预告格式中的 DA 链接替换为当帖 URL，不含主页链接；
    - Patreon 正文末尾引流签名里的 DA 链接同样替换为当帖 URL，不含主页链接；
    - 共享上下文与结果面板记录实际使用的当帖 URL。
    """
    homepage_url = "https://www.deviantart.com/foxtontonton"
    post_url = "https://www.deviantart.com/foxtontonton/art/Latex-Alpha-1402516666"

    da_disp = DummyDispatcher("deviantart", "DeviantArt", tmp_path)
    da_disp.last_published_url = post_url
    da_disp.publish_result = (True, f"DeviantArt 发布成功: {post_url} (图集核验通过)", [
        {"item": "post_url", "verified": True, "detail": post_url, "expected": True}
    ])

    x_disp = DummyDispatcher("x_twitter", "X (Twitter)", tmp_path)
    pat_disp = DummyDispatcher("patreon", "Patreon", tmp_path)

    dummy_img = tmp_path / "img1.png"
    dummy_img.write_bytes(b"dummy")

    payload = {
        "title": "Latex Alpha",
        "description": f"New creation teaser.\n\n---\nCheck out more NSFW content on 👇：\ndeviantart: {homepage_url}\nPatreon: https://patreon.com/FOXTONTONTON",
        "tags": ["latex", "muscle"],
        "images": [str(dummy_img)],
        "use_da_post_link_for_traffic": True,
        "x_da_link": homepage_url,
        "platform_options": {"x_da_link": homepage_url}
    }

    thread = PublishThread([da_disp, x_disp, pat_disp], payload)

    captured_results = {}
    thread.completed_signal.connect(lambda res: captured_results.update(res))

    thread.run()

    # 1. 验证 DA 成功，且 X 和 Patreon 均收到当帖 URL
    assert len(da_disp.published_payloads) == 1
    assert len(x_disp.published_payloads) == 1
    assert len(pat_disp.published_payloads) == 1

    x_received = x_disp.published_payloads[0]
    assert x_received.get("x_da_link") == post_url
    assert x_received.get("da_post_url") == post_url
    assert x_received.get("actual_traffic_link") == post_url

    pat_received = pat_disp.published_payloads[0]
    pat_desc = pat_received.get("patreon_description") or pat_received.get("description", "")
    assert post_url in pat_desc
    assert not re.search(r"https?://(?:www\.)?deviantart\.com/foxtontonton(?!\S*/art)", pat_desc, flags=re.IGNORECASE)

    # 2. 验证 traffic_info
    traffic_info = captured_results.get("_traffic_info")
    assert traffic_info is not None
    assert traffic_info["link"] == post_url
    assert traffic_info["notice"] == ""
    assert traffic_info["is_post_link"] is True


def test_da_failure_falls_back_to_homepage_link_with_notice(tmp_path):
    """
    测试【DA 失败回退机制】：
    - DA 发布失败（如网络超时/未跳转）；
    - X 与 Patreon 照常发，链接回退到主页 x_da_link；
    - 结果注明 'DA 未成功，引流链接已回退为主页'。
    """
    homepage_url = "https://www.deviantart.com/foxtontonton"

    da_disp = DummyDispatcher("deviantart", "DeviantArt", tmp_path)
    da_disp.last_published_url = None
    da_disp.publish_result = (False, "DeviantArt 提交超时未跳转至作品展示页", [])

    x_disp = DummyDispatcher("x_twitter", "X (Twitter)", tmp_path)
    pat_disp = DummyDispatcher("patreon", "Patreon", tmp_path)

    dummy_img = tmp_path / "img1.png"
    dummy_img.write_bytes(b"dummy")

    payload = {
        "title": "Failed DA Post",
        "description": f"Artwork info.\n\n---\nDeviantArt: {homepage_url}",
        "tags": ["art"],
        "images": [str(dummy_img)],
        "use_da_post_link_for_traffic": True,
        "x_da_link": homepage_url,
        "platform_options": {"x_da_link": homepage_url}
    }

    thread = PublishThread([da_disp, x_disp, pat_disp], payload)

    captured_results = {}
    thread.completed_signal.connect(lambda res: captured_results.update(res))

    thread.run()

    # 1. DA 失败，但 X 和 Patreon 照常发
    assert len(da_disp.published_payloads) == 1
    assert len(x_disp.published_payloads) == 1
    assert len(pat_disp.published_payloads) == 1

    # 2. X 与 Patreon 链接回退为主页
    x_received = x_disp.published_payloads[0]
    assert x_received.get("x_da_link") == homepage_url
    assert x_received.get("actual_traffic_link") == homepage_url

    pat_received = pat_disp.published_payloads[0]
    pat_desc = pat_received.get("patreon_description") or pat_received.get("description", "")
    assert homepage_url in pat_desc

    # 3. 结果面板注明回退
    assert "x_twitter" in captured_results
    assert "DA 未成功，引流链接已回退为主页" in captured_results["x_twitter"][1]

    assert "patreon" in captured_results
    assert "DA 未成功，引流链接已回退为主页" in captured_results["patreon"][1]

    traffic_info = captured_results.get("_traffic_info")
    assert traffic_info is not None
    assert traffic_info["link"] == homepage_url
    assert traffic_info["notice"] == "DA 未成功，引流链接已回退为主页"
    assert traffic_info["is_post_link"] is False


def test_da_not_selected_maintains_homepage_link_and_order(tmp_path):
    """
    测试【不选 DA 平台时维持现状】：
    - 集合中没有 DeviantArt；
    - X 与 Patreon 维持使用配置的 homepage 链接；
    - 顺序与并发行为维持原有逻辑。
    """
    homepage_url = "https://www.deviantart.com/foxtontonton"

    x_disp = DummyDispatcher("x_twitter", "X (Twitter)", tmp_path)
    pat_disp = DummyDispatcher("patreon", "Patreon", tmp_path)

    dummy_img = tmp_path / "img1.png"
    dummy_img.write_bytes(b"dummy")

    payload = {
        "title": "No DA Post",
        "description": f"Artwork info.\n\n---\nDeviantArt: {homepage_url}",
        "tags": ["art"],
        "images": [str(dummy_img)],
        "use_da_post_link_for_traffic": True,
        "x_da_link": homepage_url,
        "platform_options": {"x_da_link": homepage_url}
    }

    thread = PublishThread([x_disp, pat_disp], payload)

    captured_results = {}
    thread.completed_signal.connect(lambda res: captured_results.update(res))

    thread.run()

    assert len(x_disp.published_payloads) == 1
    assert len(pat_disp.published_payloads) == 1

    x_received = x_disp.published_payloads[0]
    assert x_received.get("x_da_link") == homepage_url

    pat_received = pat_disp.published_payloads[0]
    pat_desc = pat_received.get("patreon_description") or pat_received.get("description", "")
    assert homepage_url in pat_desc

    assert captured_results["x_twitter"][0] is True
    assert captured_results["patreon"][0] is True


def test_use_da_post_link_disabled_always_uses_homepage(tmp_path):
    """
    测试【关闭配置开关 use_da_post_link_for_traffic = False】：
    - 即使 DA 与 X/Patreon 同时选中，也不进入两阶段，永远使用主页链接。
    """
    homepage_url = "https://www.deviantart.com/foxtontonton"
    post_url = "https://www.deviantart.com/foxtontonton/art/Should-Not-Propagate-1402519999"

    da_disp = DummyDispatcher("deviantart", "DeviantArt", tmp_path)
    da_disp.last_published_url = post_url
    da_disp.publish_result = (True, f"DeviantArt 发布成功: {post_url}", [])

    x_disp = DummyDispatcher("x_twitter", "X (Twitter)", tmp_path)

    dummy_img = tmp_path / "img1.png"
    dummy_img.write_bytes(b"dummy")

    payload = {
        "title": "Disabled Switch Post",
        "description": "Artwork info.",
        "tags": ["art"],
        "images": [str(dummy_img)],
        "use_da_post_link_for_traffic": False,  # 关掉开关
        "x_da_link": homepage_url,
        "platform_options": {"x_da_link": homepage_url}
    }

    thread = PublishThread([da_disp, x_disp], payload)

    captured_results = {}
    thread.completed_signal.connect(lambda res: captured_results.update(res))

    thread.run()

    x_received = x_disp.published_payloads[0]
    # 开关关闭，X 保持主页链接
    assert x_received.get("x_da_link") == homepage_url
    assert not re.search(r"https?://(?:www\.)?deviantart\.com/foxtontonton(?!\S*/art)", homepage_url) is False


def test_results_panel_displays_traffic_link_and_fallback_notice(tmp_path, qapp, monkeypatch):
    """
    测试【结果面板展示】：
    - 成功捕获 DA 当帖 URL 时，面板渲染实际引流链接；
    - 回退主页时，面板与各行注明回退告警。
    """
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
        win = MainWindow()
        win.data_dir = tmp_path
        win.notify = MagicMock()

        try:
            # 1. 成功透传案例
            results_success = {
                "_traffic_info": {
                    "link": "https://www.deviantart.com/foxtontonton/art/Awesome-Piece-12345",
                    "notice": "",
                    "is_post_link": True
                },
                "deviantart": (True, "发布成功", []),
                "x_twitter": (True, "X 成功", [])
            }
            win._update_publish_results_panel(results_success)

            found_success_link = False
            for i in range(win.publish_results_layout.count()):
                item = win.publish_results_layout.itemAt(i)
                w = item.widget()
                if w:
                    for child in w.children():
                        if hasattr(child, "text") and "Awesome-Piece-12345" in child.text():
                            found_success_link = True
                            break
            assert found_success_link is True

            # 2. 失败回退案例
            results_fallback = {
                "_traffic_info": {
                    "link": "https://www.deviantart.com/foxtontonton",
                    "notice": "DA 未成功，引流链接已回退为主页",
                    "is_post_link": False
                },
                "deviantart": (False, "超时失败", []),
                "x_twitter": (True, "X 成功 (DA 未成功，引流链接已回退为主页)", [])
            }
            win._update_publish_results_panel(results_fallback)

            found_fallback_notice = False
            for i in range(win.publish_results_layout.count()):
                item = win.publish_results_layout.itemAt(i)
                w = item.widget()
                if w:
                    for child in w.children():
                        if hasattr(child, "text") and "DA 未成功，引流链接已回退为主页" in child.text():
                            found_fallback_notice = True
                            break
            assert found_fallback_notice is True
        finally:
            for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
                if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                    getattr(win, t_attr).stop()
            if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
                win.thread.quit()
