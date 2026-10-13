# -*- coding: utf-8 -*-
import time
from unittest.mock import MagicMock
from dispatchers.deviantart import DeviantArtDispatcher
from dispatchers.x_twitter import XTwitterDispatcher


def _setup_da(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    disp = DeviantArtDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": "userinfo", "value": "da_val"}])
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))
    disp.safe_goto = MagicMock()
    disp.apply_stealth = MagicMock()
    disp.close_login_session = MagicMock()
    disp.assert_field_value = MagicMock(return_value=(True, ""))
    disp.assert_attached = MagicMock(return_value=(True, ""))
    return disp


def test_da_gallery_not_found_blocks_publish(tmp_path, monkeypatch):
    """
    测试【DA 相册保护】：指定目标相册时，若列表未找到该相册，
    必须升级为阻断 (返回 False)，严禁静默降级到默认相册发布。
    """
    disp = _setup_da(tmp_path, monkeypatch)
    dummy_img = tmp_path / "test.png"
    dummy_img.write_bytes(b"dummy")

    mock_page = MagicMock()
    mock_page.url = "https://www.deviantart.com/submit"
    mock_page.wait_for_selector = MagicMock(return_value=MagicMock())
    mock_page.wait_for_timeout = MagicMock()

    mock_file_input = MagicMock()
    mock_file_input.get_attribute = MagicMock(return_value="image/png")
    mock_title_input = MagicMock()
    mock_gallery_trigger = MagicMock()
    mock_gallery_trigger.is_visible = MagicMock(return_value=True)

    def fake_query_selector(sel):
        if "file" in sel:
            return mock_file_input
        if "title" in sel:
            return mock_title_input
        if "gallery" in sel or "Gallery" in sel:
            return mock_gallery_trigger
        return MagicMock()

    def fake_query_selector_all(sel):
        if "file" in sel:
            return [mock_file_input]
        return []  # 相册列表项未匹配

    mock_page.query_selector = MagicMock(side_effect=fake_query_selector)
    mock_page.query_selector_all = MagicMock(side_effect=fake_query_selector_all)

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    payload = {
        "title": "NSFW Muscle Art",
        "images": [str(dummy_img)],
        "platform_options": {"deviantart_gallery": "NonExistentSpecialGallery"}
    }

    success, msg, compliance = disp.publish(payload)
    assert success is False
    assert "已阻断发布以防止作品发布到错误相册" in msg or "未能匹配或定位" in msg
    assert "NonExistentSpecialGallery" in msg


def test_da_gallery_readback_failure_blocks_publish(tmp_path, monkeypatch):
    """
    测试【DA 相册保护】：目标相册点击后，回读校验未能确认相册文本时，
    必须阻断发布，严禁降级到默认相册。
    """
    disp = _setup_da(tmp_path, monkeypatch)
    dummy_img = tmp_path / "test.png"
    dummy_img.write_bytes(b"dummy")

    mock_page = MagicMock()
    mock_page.url = "https://www.deviantart.com/submit"
    mock_page.wait_for_selector = MagicMock(return_value=MagicMock())
    mock_page.wait_for_timeout = MagicMock()

    mock_file_input = MagicMock()
    mock_file_input.get_attribute = MagicMock(return_value="image/png")
    mock_title_input = MagicMock()
    mock_gallery_trigger = MagicMock()
    mock_gallery_trigger.is_visible = MagicMock(return_value=True)

    # 列表项中匹配到该相册
    mock_item = MagicMock()
    mock_item.inner_text = MagicMock(return_value="Secret Themed Gallery")

    def fake_query_selector(sel):
        if "file" in sel:
            return mock_file_input
        if "title" in sel:
            return mock_title_input
        if "gallery" in sel or "Gallery" in sel:
            return mock_gallery_trigger
        return MagicMock()

    def fake_query_selector_all(sel):
        if "file" in sel:
            return [mock_file_input]
        if "option" in sel or "menuitem" in sel or "checkbox" in sel or "Secret Themed Gallery" in sel:
            return [mock_item]
        return []

    mock_page.query_selector = MagicMock(side_effect=fake_query_selector)
    mock_page.query_selector_all = MagicMock(side_effect=fake_query_selector_all)
    # evaluate 回读校验返回 False (未在 React DOM 中确认)
    mock_page.evaluate = MagicMock(return_value=False)

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    payload = {
        "title": "NSFW Muscle Art",
        "images": [str(dummy_img)],
        "platform_options": {"deviantart_gallery": "Secret Themed Gallery"}
    }

    success, msg, compliance = disp.publish(payload)
    assert success is False
    assert "相册回读校验失败" in msg
    assert "Secret Themed Gallery" in msg


def test_x_button_wait_timeout_and_retry(tmp_path, monkeypatch):
    """
    测试【X 发帖按钮超时与重试】：发帖按钮持续 aria-disabled=true 时，
    超时后刷新 composer 页面重试，最终超限后报告详细诊断与 aria-disabled 状态。
    """
    monkeypatch.setattr(time, "sleep", lambda x: None)
    dummy_img = tmp_path / "test.png"
    dummy_img.write_bytes(b"dummy")

    disp = XTwitterDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": "auth_token", "value": "x_token"}])
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))
    disp.safe_goto = MagicMock()
    disp.apply_stealth = MagicMock()
    disp.close_login_session = MagicMock()
    disp.assert_field_value = MagicMock(return_value=(True, ""))
    disp.assert_attached = MagicMock(return_value=(True, ""))

    mock_page = MagicMock()
    mock_page.url = "https://x.com/home"
    mock_page.wait_for_selector = MagicMock(return_value=MagicMock())
    mock_page.wait_for_timeout = MagicMock()
    mock_page.reload = MagicMock()

    # 发帖按钮始终处于 aria-disabled=true
    mock_disabled_btn = MagicMock()
    mock_disabled_btn.is_visible = MagicMock(return_value=True)
    mock_disabled_btn.is_enabled = MagicMock(return_value=False)
    mock_disabled_btn.get_attribute = MagicMock(return_value="true")

    mock_page.query_selector = MagicMock(return_value=mock_disabled_btn)
    mock_page.query_selector_all = MagicMock(return_value=[mock_disabled_btn])

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    # 模拟时钟：让每次等待快速推进并超时
    clock = [100.0]

    def fake_time():
        clock[0] += 15.0
        return clock[0]

    monkeypatch.setattr(time, "time", fake_time)

    payload = {
        "title": "GIF Artwork",
        "images": [str(dummy_img)],
        "x_wait_button_timeout": 5  # 5秒快速测试超时
    }

    success, msg, compliance = disp.publish(payload)
    assert success is False
    assert "发帖按钮未能在预期时间内激活" in msg
    assert "已尝试 2 次" in msg or "已重试" in msg
    # 验证确实调用了 reload 刷新 composer
    assert mock_page.reload.call_count >= 1
