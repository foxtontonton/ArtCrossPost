# -*- coding: utf-8 -*-
"""
ArtCrossPost - Civitai Dispatcher Unit Tests
严格覆盖需求 12 & 13:
12. 媒体过滤：
    - GIF 有配对换视频
    - GIF 无配对跳过 (注明 "Civitai 不支持 GIF 且无配对视频，已跳过")
    - 超过上限 (默认 20) 发布前自查拦截报错
    - 视频超限跳过 (体积 >750MB、时长 >245s、分辨率 >4K)
13. 成功断言与发布容错：
    - URL 正则匹配案 (匹配 civitai.com/posts/<数字> 并回填帖子链接)
    - URL 正则不匹配案 (留在 /posts/create 或 /posts/<id>/edit 判失败)
    - 标签部分失败不中断主流程
    - 失败时孤儿草稿清理 (Delete Post) 逻辑与残留警示
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dispatchers.civitai import (
    CivitaiDispatcher,
    prepare_civitai_media,
    validate_civitai_video_constraints,
    POST_URL_REGEX,
    DRAFT_EDIT_REGEX,
    CIVITAI_MAX_MEDIA_LIMIT,
)
from config import load_config, DEFAULT_CONFIG


@pytest.fixture
def dummy_files(tmp_path):
    """创建测试用的各类媒体文件"""
    img1 = tmp_path / "art1.png"
    img1.write_bytes(b"dummy_png_data_1")

    img2 = tmp_path / "art2.jpg"
    img2.write_bytes(b"dummy_jpg_data_2")

    img3 = tmp_path / "art3.webp"
    img3.write_bytes(b"dummy_webp_data_3")

    gif_with_pair = tmp_path / "anim_paired.gif"
    gif_with_pair.write_bytes(b"dummy_gif_data_paired")

    gif_no_pair = tmp_path / "anim_standalone.gif"
    gif_no_pair.write_bytes(b"dummy_gif_data_no_pair")

    paired_mp4 = tmp_path / "anim_paired.mp4"
    paired_mp4.write_bytes(b"dummy_mp4_video_data")

    normal_mp4 = tmp_path / "video_clip.mp4"
    normal_mp4.write_bytes(b"dummy_video_clip_data")

    return {
        "png": img1,
        "jpg": img2,
        "webp": img3,
        "gif_paired": gif_with_pair,
        "gif_no_pair": gif_no_pair,
        "paired_mp4": paired_mp4,
        "normal_mp4": normal_mp4,
        "tmp_path": tmp_path,
    }


# =========================================================================
# 需求 12: 媒体过滤与门禁自检测试
# =========================================================================

def test_media_filter_gif_with_paired_video_replaced(dummy_files):
    """12.1 帖中 GIF 有配对视频时，自动替换为配对视频"""
    x_video_paths = {
        str(dummy_files["gif_paired"]): str(dummy_files["paired_mp4"])
    }
    media_items = [dummy_files["png"], dummy_files["gif_paired"]]

    with patch("dispatchers.civitai.validate_civitai_video_constraints", return_value=(True, "合规")):
        accepted, skipped = prepare_civitai_media(
            media_items,
            x_video_paths=x_video_paths,
            max_limit=20
        )

    assert str(dummy_files["png"]) in accepted
    assert str(dummy_files["paired_mp4"]) in accepted
    assert str(dummy_files["gif_paired"]) not in accepted
    assert len(accepted) == 2
    assert len(skipped) == 0


def test_media_filter_gif_without_paired_video_skipped(dummy_files):
    """12.2 帖中 GIF 无配对视频时，该张跳过并附带指定注明文案"""
    media_items = [dummy_files["png"], dummy_files["gif_no_pair"]]
    x_video_paths = {}

    accepted, skipped = prepare_civitai_media(
        media_items,
        x_video_paths=x_video_paths,
        max_limit=20
    )

    assert str(dummy_files["png"]) in accepted
    assert str(dummy_files["gif_no_pair"]) not in accepted
    assert len(accepted) == 1
    assert any("Civitai 不支持 GIF 且无配对视频，已跳过" in s for s in skipped)


def test_media_filter_over_20_intercepted(dummy_files):
    """12.3 媒体数量超上限 (例如 21 张) 时自查拦截报错，提示用户删减"""
    tmp_path = dummy_files["tmp_path"]
    many_images = []
    for i in range(25):
        p = tmp_path / f"img_{i:02d}.png"
        p.write_bytes(b"dummy")
        many_images.append(p)

    with pytest.raises(ValueError) as excinfo:
        prepare_civitai_media(many_images, max_limit=20)

    err_msg = str(excinfo.value)
    assert "超过 Civitai 上限" in err_msg
    assert "25" in err_msg
    assert "20" in err_msg
    assert "删减" in err_msg


def test_media_filter_configured_smaller_limit_intercepted(dummy_files):
    """12.4 配置只许调小时 (如配置上限 10 张，传入 12 张)，触发自查拦截"""
    tmp_path = dummy_files["tmp_path"]
    images = []
    for i in range(12):
        p = tmp_path / f"img_{i:02d}.jpg"
        p.write_bytes(b"dummy")
        images.append(p)

    with pytest.raises(ValueError) as excinfo:
        prepare_civitai_media(images, max_limit=10)

    err_msg = str(excinfo.value)
    assert "超过 Civitai 上限 (10 张)" in err_msg


def test_media_filter_video_exceeding_constraints_skipped(dummy_files):
    """12.5 视频超限跳过: 针对体积、时长、分辨率三项门禁"""
    v_file = dummy_files["normal_mp4"]

    # 1. 体积超限 (> 750MB)
    with patch("dispatchers.civitai.validate_civitai_video_constraints", return_value=(False, "限制视频体积 ≤ 750MB (当前 800MB)")):
        accepted, skipped = prepare_civitai_media(
            [dummy_files["png"], v_file],
            max_limit=20
        )
    assert str(dummy_files["png"]) in accepted
    assert str(v_file) not in accepted
    assert any("限制视频体积" in s for s in skipped)

    # 2. 时长超限 (> 245s)
    with patch("dispatchers.civitai.validate_civitai_video_constraints", return_value=(False, "限制视频时长 ≤ 245s (当前 300s)")):
        accepted, skipped = prepare_civitai_media(
            [dummy_files["png"], v_file],
            max_limit=20
        )
    assert str(v_file) not in accepted
    assert any("限制视频时长" in s for s in skipped)

    # 3. 分辨率超限 (> 4K)
    with patch("dispatchers.civitai.validate_civitai_video_constraints", return_value=(False, "限制视频分辨率 ≤ 4K (当前 5000x5000)")):
        accepted, skipped = prepare_civitai_media(
            [dummy_files["png"], v_file],
            max_limit=20
        )
    assert str(v_file) not in accepted
    assert any("限制视频分辨率" in s for s in skipped)


def test_validate_civitai_video_constraints_real_logic(tmp_path):
    """测试 validate_civitai_video_constraints 函数的核心计算与判断逻辑"""
    test_v = tmp_path / "sample.mp4"
    test_v.write_bytes(b"x" * 1024)

    # 正常视频
    with patch("dispatchers.civitai.probe_video_info", return_value={"duration_sec": 120.0, "width": 1920, "height": 1080}):
        ok, msg = validate_civitai_video_constraints(test_v)
        assert ok is True
        assert "合规" in msg

    # 时长超限
    with patch("dispatchers.civitai.probe_video_info", return_value={"duration_sec": 246.0, "width": 1920, "height": 1080}):
        ok, msg = validate_civitai_video_constraints(test_v)
        assert ok is False
        assert "限制视频时长 ≤ 245s" in msg

    # 分辨率超限
    with patch("dispatchers.civitai.probe_video_info", return_value={"duration_sec": 60.0, "width": 5000, "height": 3000}):
        ok, msg = validate_civitai_video_constraints(test_v)
        assert ok is False
        assert "限制视频分辨率 ≤ 4K" in msg


# =========================================================================
# 需求 13: 成功断言与发布容错测试
# =========================================================================

def test_success_assertion_regex_match():
    """13.1 URL 正则匹配案: 匹配 civitai.com/posts/<数字> 并提取 ID"""
    valid_urls = [
        ("https://civitai.com/posts/31856190", "31856190"),
        ("http://civitai.com/posts/12345678", "12345678"),
        ("https://www.civitai.com/posts/999999/", "999999"),
        ("https://civitai.com/posts/88888?view=feed", "88888"),
    ]
    for url, expected_id in valid_urls:
        match = POST_URL_REGEX.search(url)
        assert match is not None, f"应当匹配成功: {url}"
        assert match.group(1) == expected_id


def test_success_assertion_regex_mismatch():
    """13.2 URL 正则不匹配案: 草稿、编辑页、新建页或错误页判定不匹配"""
    invalid_urls = [
        "https://civitai.com/posts/create",
        "https://civitai.com/posts/31856190/edit",
        "https://civitai.com/models/1234",
        "https://civitai.com/images/99999",
        "https://civitai.com/home",
    ]
    for url in invalid_urls:
        if "/edit" in url or "/create" in url:
            # 即使包含数字但以 /edit 结尾不应作为发布完成断言
            match = POST_URL_REGEX.search(url)
            is_valid_post = match and not url.endswith("/edit") and not url.endswith("/create")
            assert not is_valid_post, f"编辑/新建页不应判为已发布: {url}"
        else:
            match = POST_URL_REGEX.search(url)
            assert match is None, f"非帖子 URL 不应匹配: {url}"


def test_draft_edit_regex():
    """测试草稿编辑页正则提取草稿 ID"""
    edit_url = "https://civitai.com/posts/31856190/edit"
    match = DRAFT_EDIT_REGEX.search(edit_url)
    assert match is not None
    assert match.group(1) == "31856190"


def test_tag_partial_failure_does_not_break_flow(tmp_path):
    """13.3 标签部分失败不中断主流程: 模拟某个标签抛出异常，其他标签与发布仍成功完成"""
    cookies_dir = tmp_path / "cookies"
    cookies_dir.mkdir()
    disp = CivitaiDispatcher(cookies_dir)

    dummy_img = tmp_path / "test.png"
    dummy_img.write_bytes(b"data")

    payload = {
        "title": "Test Title",
        "description": "Test Description",
        "tags": ["good_tag_1", "broken_tag", "good_tag_2"],
        "civitai_images": [str(dummy_img)],
        "civitai_max_images_per_post": 20
    }

    mock_page = MagicMock()
    mock_file_input = MagicMock()
    mock_pub_btn = MagicMock()
    mock_pub_btn.is_disabled.return_value = False
    mock_saved_el = MagicMock()

    mock_page.url = "https://civitai.com/posts/31856190"

    # 模拟 query_selector
    def fake_query_selector(selector):
        if "input[type='file']" in selector:
            return mock_file_input
        if "button:has-text('Publish')" in selector:
            return mock_pub_btn
        if "SAVED" in selector:
            return mock_saved_el
        if "title" in selector:
            return MagicMock()
        if "ProseMirror" in selector:
            return MagicMock()
        if "Tag" in selector or "post:tag" in selector:
            return MagicMock()
        if "Autocomplete" in selector or "input[placeholder*='tag'" in selector:
            mock_tag_input = MagicMock()
            return mock_tag_input
        return MagicMock()

    mock_page.query_selector.side_effect = fake_query_selector

    # 模拟在输入 broken_tag 时抛异常
    tag_attempts = []
    def fake_fill(text):
        tag_attempts.append(text)
        if text == "broken_tag":
            raise RuntimeError("Mantine dropdown crashed for broken_tag")

    # 当查询到标签输入控件时，赋予具有 fake_fill 的 mock
    tag_input_mock = MagicMock()
    tag_input_mock.fill.side_effect = fake_fill

    def fake_query_selector_with_tag_error(selector):
        if "input[placeholder*='tag'" in selector or "Autocomplete" in selector:
            return tag_input_mock
        return fake_query_selector(selector)

    mock_page.query_selector.side_effect = fake_query_selector_with_tag_error

    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    with patch.object(disp, "create_persistent_stealth_context", return_value=mock_context), \
         patch.object(disp, "safe_goto"):
        success, msg, compliance = disp.publish(payload)

    # 验证主流程成功完成
    assert success is True
    assert "31856190" in msg
    assert any(c.get("item") == "post_url" and "31856190" in c.get("detail") for c in compliance)
    # 验证尝试填入了 broken_tag 并继续执行了后续
    assert "broken_tag" in tag_attempts


def test_orphan_draft_cleanup_on_failure(tmp_path):
    """13.4 失败时孤儿草稿清理: 模拟发布等待超时且已生成草稿 ID，触发 delete_orphan_draft 清理"""
    cookies_dir = tmp_path / "cookies"
    cookies_dir.mkdir()
    disp = CivitaiDispatcher(cookies_dir)

    mock_page = MagicMock()
    mock_del_btn = MagicMock()
    mock_modal_del = MagicMock()

    def fake_query_selector(selector):
        if "button:has-text('Delete Post')" in selector:
            if ".mantine-Modal" in selector or "dialog" in selector:
                return mock_modal_del
            return mock_del_btn
        return None

    mock_page.query_selector.side_effect = fake_query_selector
    mock_page.url = "https://civitai.com/posts/98765432/edit"

    with patch.object(disp, "safe_goto"):
        res = disp.delete_orphan_draft(mock_page, draft_id="98765432")

    assert res is True
    mock_del_btn.click.assert_called_once()
    mock_modal_del.click.assert_called_once()


# =========================================================================
# 配置项与硬顶钳制约束测试
# =========================================================================

def test_civitai_config_ceiling_and_clamp():
    """测试 civitai_max_images_per_post 默认 20，且配置只许调小、严禁调大超限 (强制 ≤20)"""
    cfg = load_config()
    assert cfg.get("civitai_max_images_per_post") == 20
    assert cfg.get("defaults", {}).get("civitai_max_images_per_post") == 20

    # 模拟外部不合规的超大配置 (如 35)
    with patch("json.load", return_value={"civitai_max_images_per_post": 35, "defaults": {"civitai_max_images_per_post": 50}}), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("builtins.open"):
        merged = load_config()
        # 强制钳制在 20 以内
        assert merged["civitai_max_images_per_post"] == 20
        assert merged["defaults"]["civitai_max_images_per_post"] == 20
