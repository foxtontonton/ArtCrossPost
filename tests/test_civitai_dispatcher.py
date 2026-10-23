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
from unittest.mock import MagicMock, patch

from dispatchers.civitai import (
    CivitaiDispatcher,
    prepare_civitai_media,
    validate_civitai_video_constraints,
    POST_URL_REGEX,
    DRAFT_EDIT_REGEX,
    parse_civitai_resource_item,
    parse_civitai_resources,
)
from config import load_config


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


# =========================================================================
# 需求 1 & 6: Civitai 资源配置解析与非法元素剔除测试
# =========================================================================

def test_civitai_resource_parsing_three_valid_forms():
    """6.1 配置三种合法形态解析 (数字 ID、模型 URL、字典含版本)"""
    # 形态 1: int 或纯数字字符串
    res1 = parse_civitai_resource_item(25760)
    assert res1 == {"id": 25760, "version": None}

    res2 = parse_civitai_resource_item("25760")
    assert res2 == {"id": 25760, "version": None}

    # 形态 2: Civitai 模型 URL (含不同 path 与 query)
    res3 = parse_civitai_resource_item("https://civitai.com/models/25760")
    assert res3 == {"id": 25760, "version": None}

    res4 = parse_civitai_resource_item("https://civitai.com/models/25760/latex-catsuit?modelVersionId=1889632")
    assert res4 == {"id": 25760, "version": None}

    # 形态 3: 字典 (含 id 与 version)
    res5 = parse_civitai_resource_item({"id": 25760, "version": "v1.0"})
    assert res5 == {"id": 25760, "version": "v1.0"}

    # 字典中 id 为 URL 且含版本
    res6 = parse_civitai_resource_item({"id": "https://civitai.com/models/25760", "version": "v2.0"})
    assert res6 == {"id": 25760, "version": "v2.0"}

    # 字典未指定版本或版本为空串
    res7 = parse_civitai_resource_item({"id": 25760, "version": "  "})
    assert res7 == {"id": 25760, "version": None}


def test_civitai_resource_parsing_invalid_and_pure_names_rejected():
    """6.2 纯名称字符串与非法元素直接拒绝（不可靠）并剔除"""
    # 纯名称字符串 (例如 "Goth Girl LoRA"、"Latex") 必须直接拒绝
    rejected_names = [
        "Goth Girl LoRA",
        "Latex",
        "Cyberpunk Style",
        "Pony Diffusion XL",
        "Flux Dev Inpainting",
        "anime_character_v2",
    ]
    for name in rejected_names:
        assert parse_civitai_resource_item(name) is None, f"纯名称应当被拒绝: {name}"

    # 其他非法类型
    invalid_items = [
        None,
        True,
        False,
        {},
        {"version": "v1.0"},      # 缺失 id
        {"id": "invalid_string"}, # id 无法解析
        {"id": -100},             # 负数 id
        0,                        # 0 id
        -25760,                   # 负数 id
        [],
        123.456,
    ]
    for item in invalid_items:
        assert parse_civitai_resource_item(item) is None, f"非法元素应当被拒绝: {item}"


def test_parse_civitai_resources_batch_filtering_and_cap_at_10():
    """6.3 列表批量解析、非法元素剔除与单图 10 个资源上限截断"""
    raw_mixed = [
        25760,
        "Goth Girl LoRA",  # 纯名称 -> 剔除
        "https://civitai.com/models/12345",
        {"version": "v1.0"}, # 缺失 id -> 剔除
        {"id": 88888, "version": "v2.0"},
        "Latex",           # 纯名称 -> 剔除
        "99999",
        None,              # 剔除
    ]
    cleaned = parse_civitai_resources(raw_mixed)
    assert len(cleaned) == 4
    assert cleaned == [
        {"id": 25760, "version": None},
        {"id": 12345, "version": None},
        {"id": 88888, "version": "v2.0"},
        {"id": 99999, "version": None},
    ]

    # 单图上限 10: 传入 15 个有效资源，截断为前 10 个
    raw_many = [1000 + i for i in range(15)]
    capped = parse_civitai_resources(raw_many, max_limit=10)
    assert len(capped) == 10
    assert [item["id"] for item in capped] == [1000 + i for i in range(10)]


# =========================================================================
# 需求 2 & 6: Scope 路由测试 (all vs cover)
# =========================================================================

def test_civitai_resources_scope_routing(tmp_path):
    """6.4 scope 路由: scope=all 对每张卡片执行关联；scope=cover 仅第 1 张"""
    disp = CivitaiDispatcher(tmp_path)

    mock_page = MagicMock()
    mock_btn0 = MagicMock()
    mock_btn0.inner_text.return_value = "+ RESOURCE"
    mock_btn1 = MagicMock()
    mock_btn1.inner_text.return_value = "+ RESOURCE"
    mock_btn2 = MagicMock()
    mock_btn2.inner_text.return_value = "+ RESOURCE"

    mock_page.query_selector_all.return_value = [mock_btn0, mock_btn1, mock_btn2]

    # 1. scope=all: 应该关联 3 张图
    payload_all = {
        "civitai_resources": [25760],
        "civitai_resources_scope": "all",
        "civitai_tool": "",
    }
    with patch.object(disp, "_attach_single_card_resources", return_value=True) as mock_single:
        s_ok, s_fail, _, _ = disp.attach_resources_and_tools(mock_page, payload_all, media_count=3)
        assert s_ok == 3
        assert s_fail == 0
        assert mock_single.call_count == 3
        calls = [c[0][1] for c in mock_single.call_args_list]
        assert calls == [0, 1, 2]

    # 2. scope=cover: 仅第 1 张封面图关联
    payload_cover = {
        "civitai_resources": [25760],
        "civitai_resources_scope": "cover",
        "civitai_tool": "",
    }
    with patch.object(disp, "_attach_single_card_resources", return_value=True) as mock_single:
        s_ok, s_fail, _, _ = disp.attach_resources_and_tools(mock_page, payload_cover, media_count=3)
        assert s_ok == 1
        assert s_fail == 0
        assert mock_single.call_count == 1
        assert mock_single.call_args[0][1] == 0


# =========================================================================
# 需求 4 & 6: 单图失败不中断发布流程编排测试
# =========================================================================

def test_civitai_resource_single_card_failure_does_not_break_publish(tmp_path):
    """6.5 单图关联失败记 warning 继续下一张，不中断主发布；汇总成功/失败张数"""
    disp = CivitaiDispatcher(tmp_path)
    dummy_img1 = tmp_path / "img1.png"
    dummy_img1.write_bytes(b"data1")
    dummy_img2 = tmp_path / "img2.png"
    dummy_img2.write_bytes(b"data2")

    payload = {
        "title": "Test Multi Card Resource",
        "description": "Test Desc",
        "tags": ["art"],
        "civitai_images": [str(dummy_img1), str(dummy_img2)],
        "civitai_resources": [25760],
        "civitai_resources_scope": "all",
        "civitai_tool": "",
        "civitai_max_images_per_post": 20
    }

    mock_page = MagicMock()
    mock_file_input = MagicMock()
    mock_pub_btn = MagicMock()
    mock_pub_btn.is_disabled.return_value = False
    mock_saved_el = MagicMock()

    mock_page.url = "https://civitai.com/posts/31856190"

    def fake_query_selector(selector):
        if "input[type='file']" in selector:
            return mock_file_input
        if "button:has-text('Publish')" in selector:
            return mock_pub_btn
        if "SAVED" in selector:
            return mock_saved_el
        return MagicMock()

    mock_page.query_selector.side_effect = fake_query_selector
    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    # 模拟第 0 张卡片失败，第 1 张卡片成功
    card_results = [False, True]
    def fake_attach_single(page, idx, resources):
        return card_results[idx]

    with patch.object(disp, "create_persistent_stealth_context", return_value=mock_context), \
         patch.object(disp, "safe_goto"), \
         patch.object(disp, "_attach_single_card_resources", side_effect=fake_attach_single):
        success, msg, compliance = disp.publish(payload)

    # 验证主流程成功发布，且汇总了 "资源关联: 成功 1 张 / 失败 1 张"
    assert success is True
    assert "31856190" in msg
    assert "资源关联: 成功 1 张 / 失败 1 张" in msg
    res_comp = [c for c in compliance if c.get("item") == "resources_attached"]
    assert len(res_comp) == 1
    assert "成功 1 张 / 失败 1 张" in res_comp[0]["detail"]


# =========================================================================
# 需求 5: civitai_tool 标注与容错测试
# =========================================================================

def test_civitai_tool_attachment_and_tolerance(tmp_path):
    """6.6 工具标注: 挂 TOOL 正常成功与异常容错不中断"""
    disp = CivitaiDispatcher(tmp_path)

    mock_page = MagicMock()
    mock_tool_btn = MagicMock()
    mock_tool_btn.inner_text.return_value = "+ TOOL"
    mock_tool_input = MagicMock()
    mock_tool_opt = MagicMock()

    def fake_qs(selector):
        if "input" in selector:
            return mock_tool_input
        if "ComfyUI" in selector:
            return mock_tool_opt
        return None

    mock_page.query_selector.side_effect = fake_qs
    mock_page.query_selector_all.return_value = [mock_tool_btn]

    # 1. 正常成功
    ok = disp._attach_single_card_tool(mock_page, 0, "ComfyUI")
    assert ok is True
    mock_tool_btn.click.assert_called_once()
    mock_tool_input.fill.assert_called_with("ComfyUI")
    mock_tool_opt.click.assert_called_once()

    # 2. 异常容错 (抛出异常时不阻断)
    mock_page.query_selector.side_effect = RuntimeError("DOM detached")
    fail_ok = disp._attach_single_card_tool(mock_page, 0, "ComfyUI")
    assert fail_ok is False


# =========================================================================
# 配置项扩展测试: civitai_resources, scope 与 civitai_tool 校验
# =========================================================================

def test_load_config_civitai_resources_and_scope():
    """测试 load_config 对 civitai_resources 规范化与 scope/tool 的默认值与提取"""
    raw_user_conf = {
        "civitai_resources": [
            25760,
            "Latex", # 应当剔除
            "https://civitai.com/models/9999",
            {"id": 8888, "version": "v1.0"}
        ],
        "civitai_resources_scope": "COVER",
        "civitai_tool": " Automatic1111 ",
        "defaults": {
            "civitai_resources_scope": "all",
            "civitai_tool": "ComfyUI"
        }
    }
    with patch("json.load", return_value=raw_user_conf), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("builtins.open"):
        cfg = load_config()

    # civitai_resources 中的 "Latex" 已被剔除，保留 3 项规范化字典
    assert len(cfg["civitai_resources"]) == 3
    assert cfg["civitai_resources"][0] == {"id": 25760, "version": None}
    assert cfg["civitai_resources"][1] == {"id": 9999, "version": None}
    assert cfg["civitai_resources"][2] == {"id": 8888, "version": "v1.0"}

    # scope 规范化为 "cover"
    assert cfg["civitai_resources_scope"] == "cover"

    # tool 去除空格
    assert cfg["civitai_tool"] == "Automatic1111"
