import time
from pathlib import Path
from unittest.mock import MagicMock
from dispatchers.monetization import PatreonDispatcher


def test_patreon_upload_no_generic_fallback(tmp_path):
    """
    测试：当页面仅存在通用 input[type='file'] 时，严格不予采纳，
    直接报错 'Patreon 图片上传通道未找到' 并中止，绝不静默回退。
    """
    disp = PatreonDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": "session_id", "value": "123"}])

    mock_page = MagicMock()
    def fake_query_selector(sel):
        if "mainMedia" in sel or "photosInput" in sel or "accept*='image'" in sel:
            return None
        if sel == "input[type='file']":
            return MagicMock() # 普通通用 input
        return None

    mock_page.query_selector = MagicMock(side_effect=fake_query_selector)

    payload = {"images": ["test.png"], "patreon_images": ["test.png"]}

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))

    disp.safe_goto = MagicMock()
    disp.clean_context_tabs = MagicMock()
    disp.apply_stealth = MagicMock()

    success, msg, compliance = disp.publish(payload)
    assert success is False
    assert "Patreon 图片上传通道未找到" in msg


def test_patreon_upload_inline_media_verification(tmp_path, monkeypatch):
    """
    测试：当上传后未找到内嵌图片 (Hero media)，必须判定失败，
    防止退化为下载附件行。
    """
    monkeypatch.setattr(time, "sleep", lambda x: None)

    disp = PatreonDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": "session_id", "value": "123"}])

    mock_page = MagicMock()
    mock_file_input = MagicMock()

    def fake_query_selector(sel):
        if "input#mainMedia" in sel:
            return mock_file_input
        if "patreon-media" in sel or "media-item" in sel:
            return None
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_query_selector)

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))
    disp.assert_attached = MagicMock(return_value=(True, ""))

    disp.safe_goto = MagicMock()
    disp.clean_context_tabs = MagicMock()
    disp.apply_stealth = MagicMock()

    payload = {"images": ["test.png"], "patreon_images": ["test.png"]}
    success, msg, compliance = disp.publish(payload)
    assert success is False
    assert "未检测到内嵌主媒体 (Hero media) 渲染" in msg


def test_patreon_submission_signals(tmp_path):
    """
    测试：Patreon 提交信号判定
    1. URL 离开 /edit 且含 /posts/
    2. 原地 React 状态跃迁：按钮变为 Update 且无 Publish 按钮
    """
    # 信号 1: URL 重定向
    page1 = MagicMock()
    page1.url = "https://www.patreon.com/FOXTONTONTON/posts/my-artwork-172082751"
    u1 = page1.url.lower()
    assert "/posts/" in u1 and "/edit" not in u1 and "/posts/new" not in u1

    # 信号 2: React 状态切换，按钮变为 Update 且无 Publish
    page2 = MagicMock()
    page2.url = "https://www.patreon.com/FOXTONTONTON/posts/172082751/edit"
    mock_update_btn = MagicMock()
    mock_publish_btn = None
    assert mock_update_btn is not None and mock_publish_btn is None


def test_patreon_gallery_10_images_success(tmp_path, monkeypatch):
    """
    验收标准 1: Patreon 一次发 10 张 JPEG，gallery 里 10 张全在，报告显示成功 10/10。
    """
    monkeypatch.setattr(time, "sleep", lambda x: None)

    disp = PatreonDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": "session_id", "value": "123"}])

    mock_page = MagicMock()
    mock_file_input = MagicMock()
    mock_inline = MagicMock()
    mock_inline.is_visible.return_value = True

    # 模拟 10 张图片素材
    test_imgs = [str(tmp_path / f"img_{i}.jpg") for i in range(10)]
    for p in test_imgs:
        Path(p).write_bytes(b"dummy_jpg")

    def fake_query_selector(sel):
        if "input#mainMedia" in sel or "photosInput" in sel:
            return mock_file_input
        if "patreon-media" in sel or "media-item" in sel:
            return mock_inline
        if "Title" in sel:
            title_input = MagicMock()
            return title_input
        if "Publish" in sel:
            btn = MagicMock()
            btn.is_disabled.return_value = False
            btn.get_attribute.return_value = "false"
            return btn
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_query_selector)
    mock_page.url = "https://www.patreon.com/FOXTONTONTON/posts/12345678"

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))
    disp.assert_attached = MagicMock(return_value=(True, ""))
    disp.assert_field_value = MagicMock(return_value=(True, ""))
    disp.safe_goto = MagicMock()
    disp.clean_context_tabs = MagicMock()
    disp.apply_stealth = MagicMock()

    # mock _count_patreon_gallery_images 返回全部 10 张
    disp._count_patreon_gallery_images = MagicMock(return_value=10)

    payload = {
        "title": "My 10 Image Gallery",
        "images": test_imgs,
        "patreon_images": test_imgs
    }
    success, msg, compliance = disp.publish(payload)

    assert success is True
    assert "10/10" in msg
    mock_file_input.set_input_files.assert_called_once_with(test_imgs)
    count_comp = [c for c in compliance if c.get("item") == "gallery_count"]
    assert len(count_comp) == 1
    assert count_comp[0]["verified"] is True
    assert "10/10" in count_comp[0]["detail"]


def test_patreon_gallery_count_mismatch_fails_loudly(tmp_path, monkeypatch):
    """
    成功断言: 预期 10 张但实际核验为 8 张时，断言失败一律报 FAILED，不许报成功。
    """
    monkeypatch.setattr(time, "sleep", lambda x: None)

    disp = PatreonDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": "session_id", "value": "123"}])

    mock_page = MagicMock()
    mock_file_input = MagicMock()
    mock_inline = MagicMock()
    mock_inline.is_visible.return_value = True

    test_imgs = [str(tmp_path / f"img_{i}.jpg") for i in range(10)]
    for p in test_imgs:
        Path(p).write_bytes(b"dummy_jpg")

    def fake_query_selector(sel):
        if "input#mainMedia" in sel or "photosInput" in sel:
            return mock_file_input
        if "patreon-media" in sel or "media-item" in sel:
            return mock_inline
        if "Title" in sel:
            return MagicMock()
        if "Publish" in sel:
            btn = MagicMock()
            btn.is_disabled.return_value = False
            btn.get_attribute.return_value = "false"
            return btn
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_query_selector)
    mock_page.url = "https://www.patreon.com/FOXTONTONTON/posts/12345678"

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))
    disp.assert_attached = MagicMock(return_value=(True, ""))
    disp.assert_field_value = MagicMock(return_value=(True, ""))
    disp.safe_goto = MagicMock()
    disp.clean_context_tabs = MagicMock()
    disp.apply_stealth = MagicMock()

    # 实际只核验到 8 张
    disp._count_patreon_gallery_images = MagicMock(return_value=8)

    payload = {
        "title": "My 10 Image Gallery",
        "images": test_imgs,
        "patreon_images": test_imgs
    }
    success, msg, compliance = disp.publish(payload)

    assert success is False
    assert "断言失败" in msg or "FAILED" in msg or "不匹配" in msg
    assert "8/10" in msg
    count_comp = [c for c in compliance if c.get("item") == "gallery_count"]
    assert len(count_comp) == 1
    assert count_comp[0]["verified"] is False


def test_patreon_attach_multi_selector_and_status_summary(tmp_path):
    """
    测试 Patreon 附件回读多路定位与状态摘要输出：
    1. 附件分别通过 evaluate 容器和 a[download] 多路定位器命中；
    2. 返回值包含已就绪数/总数、耗时、附件清单的状态摘要；
    3. 全部落盘后快速通过，不拖延轮询。
    """
    disp = PatreonDispatcher(tmp_path)
    file_a = tmp_path / "work_01_hd_raw_wm.png"
    file_b = tmp_path / "work_02_hd_raw_wm.png"
    file_a.write_bytes(b"dummy1")
    file_b.write_bytes(b"dummy2")

    mock_page = MagicMock()
    mock_page.query_selector.return_value = None

    # 路径 1: evaluate 返回包含 file_a
    def fake_evaluate(script):
        if "attachment" in str(script):
            return "work_01_hd_raw_wm.png"
        return ""
    mock_page.evaluate.side_effect = fake_evaluate

    # 路径 2: query_selector_all a[download] 返回包含 file_b 的元素
    def fake_query_selector_all(sel):
        if "a[download]" in sel:
            el = MagicMock()
            el.inner_text.return_value = "Download work_02_hd_raw_wm.png"
            el.get_attribute.return_value = ""
            return [el]
        if "input[type='file']" in sel:
            fi = MagicMock()
            return [fi]
        return []
    mock_page.query_selector_all.side_effect = fake_query_selector_all

    ok, summary = disp._attach_files_in_editor(
        mock_page,
        [file_a, file_b],
        timeout=5.0,
        poll_interval=0.01,
    )

    assert ok is True
    assert "成功挂载 2 个附件" in summary
    assert "已就绪: 2/2" in summary
    assert "work_01_hd_raw_wm.png" in summary
    assert "work_02_hd_raw_wm.png" in summary


def test_patreon_attach_timeout_with_status_summary(tmp_path):
    """
    测试 Patreon 附件回读 90 秒超时轮询断言与缺失状态摘要：
    1. 期望 2 个附件，DOM 中仅出现 1 个；
    2. 超时退出后报错明确，输出耗时、缺失项、已找到项与状态摘要。
    """
    disp = PatreonDispatcher(tmp_path)
    file_a = tmp_path / "alpha_master_wm.png"
    file_b = tmp_path / "beta_master_wm.png"
    file_a.write_bytes(b"dummy_a")
    file_b.write_bytes(b"dummy_b")

    mock_page = MagicMock()
    mock_page.query_selector.return_value = None

    # DOM 中仅有 alpha，beta 始终未就绪
    mock_page.evaluate.return_value = "alpha_master_wm.png uploaded"
    mock_page.query_selector_all.return_value = []
    mock_page.inner_text.return_value = "alpha_master_wm.png uploaded"

    ok, err_msg = disp._attach_files_in_editor(
        mock_page,
        [file_a, file_b],
        timeout=0.05,
        poll_interval=0.01,
    )

    assert ok is False
    assert "Patreon 附件挂载失败" in err_msg and "超时" in err_msg
    assert "仍有 1/2 个附件未回读到" in err_msg
    assert "缺失: ['beta_master_wm.png']" in err_msg
    assert "已找到: ['alpha_master_wm.png']" in err_msg
    assert "状态摘要: 已就绪: 1/2" in err_msg


def test_patreon_attach_title_and_aria_label_multi_selector(tmp_path):
    """
    测试通过 span[title] 或 [aria-label] 属性定位附件
    """
    disp = PatreonDispatcher(tmp_path)
    file_c = tmp_path / "gamma_render_file.png"
    file_c.write_bytes(b"dummy_c")

    mock_page = MagicMock()
    mock_page.query_selector.return_value = None
    mock_page.evaluate.return_value = ""

    def fake_qsa(sel):
        if "span[title]" in sel:
            el = MagicMock()
            el.inner_text.return_value = ""
            el.get_attribute.side_effect = lambda attr: "gamma_render_file.png" if attr == "title" else ""
            return [el]
        return []
    mock_page.query_selector_all.side_effect = fake_qsa

    ok, summary = disp._attach_files_in_editor(
        mock_page,
        [file_c],
        timeout=2.0,
        poll_interval=0.01,
    )

    assert ok is True
    assert "成功挂载 1 个附件" in summary
    assert "gamma_render_file.png" in summary

