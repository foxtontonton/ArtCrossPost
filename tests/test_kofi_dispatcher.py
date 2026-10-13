# -*- coding: utf-8 -*-
import time
from unittest.mock import MagicMock
from dispatchers.monetization import KofiDispatcher


def _setup_kofi_dispatcher(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda x: None)
    disp = KofiDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": ".aspnetcore.cookies", "value": "xyz"}])
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))
    disp.safe_goto = MagicMock()
    disp.apply_stealth = MagicMock()
    disp.close_login_session = MagicMock()
    disp.assert_field_value = MagicMock(return_value=(True, ""))
    disp.assert_attached = MagicMock(return_value=(True, ""))
    return disp


def test_kofi_session_expired(tmp_path, monkeypatch):
    """测试：当导航到管理主页被重定向到登录页时，必须阻断并提示会话失效"""
    disp = _setup_kofi_dispatcher(tmp_path, monkeypatch)
    mock_page = MagicMock()
    mock_page.url = "https://ko-fi.com/account/login"
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    payload = {"title": "Test Art", "kofi_images": ["test.png"]}
    success, msg, compliance = disp.publish(payload)
    assert success is False
    assert "会话已失效" in msg or "登录" in msg


def test_kofi_modal_not_opened(tmp_path, monkeypatch):
    """测试：当上传弹窗未能打开渲染时，必须大声报错中止，绝不静默放行"""
    disp = _setup_kofi_dispatcher(tmp_path, monkeypatch)
    mock_page = MagicMock()
    mock_page.url = "https://ko-fi.com/manage/mypage"
    mock_page.query_selector_all = MagicMock(return_value=[])
    mock_page.evaluate = MagicMock(return_value=False)
    mock_page.query_selector = MagicMock(return_value=None)
    mock_page.wait_for_selector = MagicMock(side_effect=Exception("Timeout 15000ms"))

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    payload = {"title": "Test Art", "kofi_images": ["test.png"]}
    success, msg, compliance = disp.publish(payload)
    assert success is False
    assert "未能打开图片上传弹窗" in msg


def test_kofi_file_input_missing(tmp_path, monkeypatch):
    """测试：弹窗打开但未能定位到 Dropzone 文件上传控件时，必须中止以防文字单独泄露"""
    disp = _setup_kofi_dispatcher(tmp_path, monkeypatch)
    mock_page = MagicMock()
    mock_page.url = "https://ko-fi.com/manage/mypage"
    mock_page.wait_for_selector = MagicMock(return_value=MagicMock())

    def fake_query_selector(sel):
        if "input" in sel:
            return None  # 上传控件缺失
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_query_selector)
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    payload = {"title": "Test Art", "kofi_images": ["test.png"]}
    success, msg, compliance = disp.publish(payload)
    assert success is False
    assert "未能定位到文件上传控件" in msg


def test_kofi_real_assertion_failure(tmp_path, monkeypatch):
    """
    测试【P0 核心修复】：提交后跳转主页反查作品标题，
    若在主页和管理动态中均未检索到刚刚发布的标题，必须判定 FAILED，严禁误报成功。
    """
    disp = _setup_kofi_dispatcher(tmp_path, monkeypatch)
    mock_page = MagicMock()
    mock_page.url = "https://ko-fi.com/manage/mypage"
    mock_page.wait_for_selector = MagicMock(return_value=MagicMock())

    mock_btn = MagicMock()
    mock_btn.is_disabled = MagicMock(return_value=False)
    mock_btn.get_attribute = MagicMock(return_value="")

    mock_modal = MagicMock()
    mock_modal.is_visible = MagicMock(return_value=False)  # 弹窗已关闭

    def fake_query_selector(sel):
        if "#uploadModal" in sel:
            return mock_modal
        if "#imageUploadSubmit" in sel:
            return mock_btn
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_query_selector)
    # 主页内容中没有任何关于 "Unique Secret Title 123" 的文字
    mock_page.inner_text = MagicMock(return_value="Welcome to my Ko-fi page! Here are older works: Post A, Post B")

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    payload = {"title": "Unique Secret Title 123", "kofi_images": ["test.png"]}
    success, msg, compliance = disp.publish(payload)
    assert success is False
    assert "Ko-fi 真实成功断言失败" in msg
    assert "Unique Secret Title 123" in msg


def test_kofi_time_guardrail_triggered(tmp_path, monkeypatch):
    """
    测试【P0 耗时护栏】：从点击发布到断言通过少于 min_publish_duration (20s) 时，
    直接判定为异常拦截，防止因同名旧作品残留或虚假提交而假阳性放行。
    """
    disp = _setup_kofi_dispatcher(tmp_path, monkeypatch)
    mock_page = MagicMock()
    mock_page.url = "https://ko-fi.com/manage/mypage"
    mock_page.wait_for_selector = MagicMock(return_value=MagicMock())

    mock_btn = MagicMock()
    mock_btn.is_disabled = MagicMock(return_value=False)
    mock_btn.get_attribute = MagicMock(return_value="")

    mock_modal = MagicMock()
    mock_modal.is_visible = MagicMock(return_value=False)

    def fake_query_selector(sel):
        if "#uploadModal" in sel:
            return mock_modal
        if "#imageUploadSubmit" in sel:
            return mock_btn
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_query_selector)
    mock_page.inner_text = MagicMock(return_value="New Artwork displayed here")

    # 模拟时钟：从点击发布到断言完成仅过去 5 秒 (远低于 20 秒阈值)
    clock = [100.0]

    def fake_time():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(time, "time", fake_time)

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    payload = {"title": "New Artwork", "kofi_images": ["test.png"], "kofi_min_duration": 20}
    success, msg, compliance = disp.publish(payload)
    assert success is False
    assert "Ko-fi 发布耗时异常护栏拦截" in msg


def test_kofi_publish_success_verified(tmp_path, monkeypatch):
    """
    测试：真实作品在管理主页中确认存在且发布耗时满足护栏 (>=20s)，
    判定为真正成功。
    """
    disp = _setup_kofi_dispatcher(tmp_path, monkeypatch)
    mock_page = MagicMock()
    mock_page.url = "https://ko-fi.com/manage/mypage"
    mock_page.wait_for_selector = MagicMock(return_value=MagicMock())

    mock_btn = MagicMock()
    mock_btn.is_disabled = MagicMock(return_value=False)
    mock_btn.get_attribute = MagicMock(return_value="")

    mock_modal = MagicMock()
    mock_modal.is_visible = MagicMock(return_value=False)

    def fake_query_selector(sel):
        if "#uploadModal" in sel:
            return mock_modal
        if "#imageUploadSubmit" in sel:
            return mock_btn
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_query_selector)
    mock_page.inner_text = MagicMock(return_value="Artwork: Legendary Dragon Fox")

    # 模拟时钟：每次调用推进 5 秒，确保点击发布到断言通过超过 20 秒
    curr_time = [100.0]

    def fake_time():
        curr_time[0] += 5.0
        return curr_time[0]

    monkeypatch.setattr(time, "time", fake_time)

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    payload = {"title": "Legendary Dragon Fox", "kofi_images": ["test.png"], "kofi_min_duration": 20}
    success, msg, compliance = disp.publish(payload)
    assert success is True
    assert "Ko-fi 发布成功" in msg
    assert "真实作品" in msg
