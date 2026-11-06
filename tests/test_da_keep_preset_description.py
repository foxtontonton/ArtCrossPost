# -*- coding: utf-8 -*-
"""
ArtCrossPost - DeviantArt 保留预设描述单元测试
覆盖需求:
1. 有预设 + 有程序文案 → 拼接顺序断言（程序在前、预设在后，中间空一行 \n\n）
2. 有预设 + 无程序文案 → 框内容不变，不碰描述框
3. 无预设 → 直接填入程序描述
4. 读预设失败（定位不到/读出为空）→ 回退现行为 + 日志注明 "未读到预设描述"
5. 开关关闭（deviantart_keep_preset_description=False）→ 直接替换
6. 结果面板注明本次描述来源：预设保留 / 拼接 / 仅程序描述
"""
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from dispatchers.deviantart import DeviantArtDispatcher
from config import load_config, DEFAULT_CONFIG


@pytest.fixture
def dummy_da_env(tmp_path):
    """创建基础测试环境与 Dispatcher 实例"""
    cookies_dir = tmp_path / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    disp = DeviantArtDispatcher(cookies_dir)

    dummy_img = tmp_path / "test_art.png"
    dummy_img.write_bytes(b"dummy_png_data")

    return {
        "disp": disp,
        "img": dummy_img,
        "tmp_path": tmp_path,
    }


def _create_mock_page_for_da_submit(preset_text=""):
    """构建模拟 DA 提交表单页面对象"""
    mock_page = MagicMock()
    mock_context = MagicMock()

    current_val = [preset_text]

    # 描述框 mock
    mock_desc_area = MagicMock()

    def _eval(script, *args):
        s = str(script)
        if "insertHTML" in s or "setContent" in s:
            if args:
                current_val[0] = args[0]
            return True
        if "el.innerHTML = html" in s or "el.innerHTML =" in s:
            if args:
                current_val[0] = args[0]
            return True
        return current_val[0]

    mock_desc_area.evaluate.side_effect = _eval
    mock_desc_area.inner_text.side_effect = lambda: current_val[0]
    mock_desc_area.inner_html.side_effect = lambda: current_val[0]
    mock_desc_area.get_attribute.side_effect = lambda attr: current_val[0] if attr == "value" else None

    # 文件上传控件
    mock_file_input = MagicMock()

    # 提交按钮
    mock_submit_btn = MagicMock()
    mock_submit_btn.inner_text.return_value = "Submit"
    mock_submit_btn.is_enabled.return_value = True
    mock_submit_btn.get_attribute.return_value = "false"

    def fake_query_selector(sel):
        if "description" in sel or "tiptap" in sel or "role='textbox'" in sel:
            return mock_desc_area
        if "input[name='title']" in sel or "title" in sel:
            mock_t = MagicMock()
            mock_t.is_visible.return_value = True
            mock_t.get_attribute.return_value = "Test Title"
            mock_t.input_value.return_value = "Test Title"
            return mock_t
        if "Submit" in sel or "submit" in sel:
            return mock_submit_btn
        if "isAiGenerated" in sel:
            return MagicMock()
        if "matureContent" in sel:
            return MagicMock()
        return MagicMock()

    def fake_query_selector_all(sel):
        if "Submit" in sel or "submit" in sel or "button" in sel:
            return [mock_submit_btn]
        if "input[type='file']" in sel:
            return [mock_file_input]
        if "chip" in sel.lower() or "tag" in sel.lower():
            mock_chip = MagicMock()
            mock_chip.inner_text.return_value = "art"
            return [mock_chip]
        return []

    mock_page.query_selector.side_effect = fake_query_selector
    mock_page.query_selector_all.side_effect = fake_query_selector_all
    mock_page.url = "https://www.deviantart.com/artist/art/Test-123456"

    # Studio drafts 页面反查
    mock_studio_page = MagicMock()
    mock_studio_page.inner_text.return_value = "All Published, No Drafts"
    mock_studio_page.url = "https://www.deviantart.com/studio"
    mock_context.new_page.return_value = mock_studio_page

    return mock_page, mock_context, mock_desc_area, mock_submit_btn


# =========================================================================
# 需求 1: 有预设 + 有程序文案 → 拼接顺序断言
# =========================================================================

def test_preset_with_program_desc_concatenation_order(dummy_da_env):
    """1. 框里有预设文本 + 程序描述非空: 拼接后填入，顺序为 程序描述 + '\\n\\n' + 预设描述"""
    disp = dummy_da_env["disp"]
    preset_text = "Support my work on Patreon:\nhttps://www.patreon.com/myart"
    program_desc = "If You Were the Researcher in This Facility..."

    mock_page, mock_context, mock_desc_area, _ = _create_mock_page_for_da_submit(preset_text=preset_text)

    comp_list = []
    with patch.object(disp, "safe_goto"), \
         patch.object(disp, "assert_field_value", return_value=(True, "OK")), \
         patch.object(disp, "check_selector_state", return_value=(True, "OK")), \
         patch.object(disp, "assert_submitted", return_value=(True, "")), \
         patch("time.sleep"):
        ok, msg = disp._submit_deviation(
            mock_page, mock_context, [dummy_da_env["img"]],
            title="Test Title", desc=program_desc, _tags=["art"],
            is_mature=False, mature_level="strict", is_ai=False,
            gallery="", compliance=comp_list, keep_preset_description=True
        )

    assert ok is True
    # 验证 fill 被调用且内容为：程序描述 + "\n\n" + 预设描述
    expected_combined = f"{program_desc}\n\n{preset_text}"
    mock_desc_area.fill.assert_called_once_with(expected_combined, timeout=5000)

    # 验证描述来源标记为 "拼接"
    assert getattr(disp, "_last_desc_source", None) == "拼接"
    desc_comp = [c for c in comp_list if c.get("item") == "description_source"]
    assert len(desc_comp) == 1
    assert desc_comp[0]["detail"] == "拼接"


# =========================================================================
# 需求 2: 有预设 + 无程序文案 → 框内容不变，不碰描述框
# =========================================================================

def test_preset_with_empty_program_desc_leaves_box_untouched(dummy_da_env):
    """2. 框里有预设文本 + 程序描述为空: 不碰描述框 (保留预设)"""
    disp = dummy_da_env["disp"]
    preset_text = "Default artist commission guidelines and links"

    mock_page, mock_context, mock_desc_area, _ = _create_mock_page_for_da_submit(preset_text=preset_text)

    comp_list = []
    with patch.object(disp, "safe_goto"), \
         patch.object(disp, "assert_field_value", return_value=(True, "OK")), \
         patch.object(disp, "check_selector_state", return_value=(True, "OK")), \
         patch.object(disp, "assert_submitted", return_value=(True, "")), \
         patch("time.sleep"):
        ok, msg = disp._submit_deviation(
            mock_page, mock_context, [dummy_da_env["img"]],
            title="Test Title", desc="", _tags=["art"],
            is_mature=False, mature_level="strict", is_ai=False,
            gallery="", compliance=comp_list, keep_preset_description=True
        )

    assert ok is True
    # 验证未调用 fill 且未触发点击注入，描述框未被触碰
    mock_desc_area.fill.assert_not_called()
    assert getattr(disp, "_last_desc_source", None) == "预设保留"
    desc_comp = [c for c in comp_list if c.get("item") == "description_source"]
    assert len(desc_comp) == 1
    assert desc_comp[0]["detail"] == "预设保留"


# =========================================================================
# 需求 3: 无预设 → 直接填入程序描述
# =========================================================================

def test_no_preset_fills_program_desc_directly(dummy_da_env):
    """3. 框里无预设文本 (空串或纯空白): 直接填入程序描述"""
    disp = dummy_da_env["disp"]
    program_desc = "Brand new character illustration 2026"

    # 纯空白视为无预设
    mock_page, mock_context, mock_desc_area, _ = _create_mock_page_for_da_submit(preset_text="   \n\t  ")

    comp_list = []
    with patch.object(disp, "safe_goto"), \
         patch.object(disp, "assert_field_value", return_value=(True, "OK")), \
         patch.object(disp, "check_selector_state", return_value=(True, "OK")), \
         patch.object(disp, "assert_submitted", return_value=(True, "")), \
         patch("time.sleep"):
        ok, msg = disp._submit_deviation(
            mock_page, mock_context, [dummy_da_env["img"]],
            title="Test Title", desc=program_desc, _tags=["art"],
            is_mature=False, mature_level="strict", is_ai=False,
            gallery="", compliance=comp_list, keep_preset_description=True
        )

    assert ok is True
    # 验证直接填入程序描述
    mock_desc_area.fill.assert_called_once_with(program_desc, timeout=5000)
    assert getattr(disp, "_last_desc_source", None) == "仅程序描述"
    desc_comp = [c for c in comp_list if c.get("item") == "description_source"]
    assert len(desc_comp) == 1
    assert desc_comp[0]["detail"] == "仅程序描述"


# =========================================================================
# 需求 4: 读预设失败 → 回退现行为 + 日志注明
# =========================================================================

def test_read_preset_failure_fallback_and_logs(dummy_da_env, caplog):
    """4. 读预设失败 (DOM 读取异常或定位不到): 回退现行为填入程序描述，不阻断发布，日志大声注明"""
    disp = dummy_da_env["disp"]
    program_desc = "Program description under fallback"

    mock_page, mock_context, mock_desc_area, _ = _create_mock_page_for_da_submit(preset_text="")

    # 模拟 evaluate 与 inner_text 抛出异常
    def boom(*args, **kwargs):
        raise RuntimeError("Tiptap editor not ready")
    mock_desc_area.evaluate.side_effect = boom
    mock_desc_area.inner_text.side_effect = boom
    mock_desc_area.get_attribute.side_effect = boom

    comp_list = []
    with caplog.at_level(logging.WARNING), \
         patch.object(disp, "safe_goto"), \
         patch.object(disp, "assert_field_value", return_value=(True, "OK")), \
         patch.object(disp, "check_selector_state", return_value=(True, "OK")), \
         patch.object(disp, "assert_submitted", return_value=(True, "")), \
         patch("time.sleep"):
        ok, msg = disp._submit_deviation(
            mock_page, mock_context, [dummy_da_env["img"]],
            title="Test Title", desc=program_desc, _tags=["art"],
            is_mature=False, mature_level="strict", is_ai=False,
            gallery="", compliance=comp_list, keep_preset_description=True
        )

    assert ok is True
    # 验证未阻断发布且正常填入了程序描述
    mock_desc_area.fill.assert_called_once_with(program_desc, timeout=5000)
    assert getattr(disp, "_last_desc_source", None) == "仅程序描述"

    # 验证日志包含 "未读到预设描述"
    assert any("未读到预设描述" in record.message for record in caplog.records)


# =========================================================================
# 需求 5: 开关关闭 → 直接替换 (现行为)
# =========================================================================

def test_keep_preset_disabled_replaces_directly(dummy_da_env):
    """5. 配置开关关闭 (deviantart_keep_preset_description=False): 无论有无预设均直接以程序描述替换"""
    disp = dummy_da_env["disp"]
    preset_text = "Existing Preset Description that should be replaced"
    program_desc = "New Program Description"

    mock_page, mock_context, mock_desc_area, _ = _create_mock_page_for_da_submit(preset_text=preset_text)

    comp_list = []
    with patch.object(disp, "safe_goto"), \
         patch.object(disp, "assert_field_value", return_value=(True, "OK")), \
         patch.object(disp, "check_selector_state", return_value=(True, "OK")), \
         patch.object(disp, "assert_submitted", return_value=(True, "")), \
         patch("time.sleep"):
        ok, msg = disp._submit_deviation(
            mock_page, mock_context, [dummy_da_env["img"]],
            title="Test Title", desc=program_desc, _tags=["art"],
            is_mature=False, mature_level="strict", is_ai=False,
            gallery="", compliance=comp_list, keep_preset_description=False
        )

    assert ok is True
    # 验证未拼接预设，而是直接用程序描述覆盖
    mock_desc_area.fill.assert_called_once_with(program_desc, timeout=5000)
    assert getattr(disp, "_last_desc_source", None) == "仅程序描述"


# =========================================================================
# 需求 6: publish 结果消息与合规列表回填描述来源
# =========================================================================

def test_publish_result_message_includes_description_source(dummy_da_env):
    """6. 验证 publish 成功返回消息与合规列表中明确包含描述来源 (拼接 / 预设保留 / 仅程序描述)"""
    disp = dummy_da_env["disp"]

    payload = {
        "title": "Publish Test",
        "description": "Program Art Desc",
        "images": [str(dummy_da_env["img"])],
        "deviantart_keep_preset_description": True,
        "platform_options": {
            "deviantart_keep_preset_description": True
        }
    }

    mock_page, mock_context, mock_desc_area, _ = _create_mock_page_for_da_submit(preset_text="Preset Bio Text")

    with patch.object(disp, "get_cookies", return_value=[{"name": "auth", "value": "1"}]), \
         patch.object(disp, "safe_goto"), \
         patch.object(disp, "create_persistent_stealth_context", return_value=mock_context), \
         patch.object(disp, "apply_stealth"), \
         patch.object(disp, "_submit_deviation") as mock_sub, \
         patch.object(disp, "_count_deviantart_gallery_images", return_value=1):

        def fake_submit(*args, **kwargs):
            disp._last_desc_source = "拼接"
            comp = args[10]
            comp.append({"item": "description_source", "verified": True, "detail": "拼接", "expected": True})
            return True, ""

        mock_sub.side_effect = fake_submit

        success, msg, compliance = disp.publish(payload)

    assert success is True
    # 验证返回消息中明确包含 "描述来源: 拼接"
    assert "描述来源: 拼接" in msg
    assert any(c.get("item") == "description_source" and c.get("detail") == "拼接" for c in compliance)


# =========================================================================
# 配置项测试: deviantart_keep_preset_description 默认值与加载
# =========================================================================

def test_config_deviantart_keep_preset_description():
    """测试配置项默认开启，并支持在配置层关闭"""
    cfg = load_config()
    assert cfg.get("deviantart_keep_preset_description") is True
    assert cfg.get("defaults", {}).get("deviantart_keep_preset_description") is True


# =========================================================================
# 需求 7: DA 预设改 HTML 读回与 HTML 层合并、预设链接保全断言、残留只警告不改预设
# =========================================================================

def test_da_preset_empty_detection_variants():
    """测试 _is_empty_preset 对各类纯文本与富文本空段落的精确识别"""
    disp = DeviantArtDispatcher(Path("dummy"))
    assert disp._is_empty_preset(None) is True
    assert disp._is_empty_preset("") is True
    assert disp._is_empty_preset("   \n\t  ") is True
    assert disp._is_empty_preset("<p></p>") is True
    assert disp._is_empty_preset("<p><br></p>") is True
    assert disp._is_empty_preset("<p class='is-empty'><br class='ProseMirror-trailingBreak'></p>") is True
    assert disp._is_empty_preset("<p>&nbsp;</p>") is True
    assert disp._is_empty_preset("<p>   <br>  </p>") is True

    # 非空情形
    assert disp._is_empty_preset("Hello world") is False
    assert disp._is_empty_preset("<p>Commission info</p>") is False
    assert disp._is_empty_preset('<p><a href="https://boosty.to/art">Boosty</a></p>') is False
    assert disp._is_empty_preset('<p><img src="test.jpg" /></p>') is False


def test_da_preset_html_readback_and_merging_preserves_html_links(dummy_da_env):
    """
    测试预设从 HTML 读回并在 HTML 层合并:
    1. 预设原有 <a href="..."> 超链接与排版标签在合并后不被 html.escape 转义为 &lt;a ...&gt;
    2. 合并后注入的 HTML 中，程序描述在前，预设在后，段间含有 <p><br></p> 间距空段
    """
    disp = dummy_da_env["disp"]
    preset_html = '<p>Support me on <a href="https://boosty.to/myart">Boosty</a> and <a href="https://fanbox.cc/@myart">Fanbox</a>!</p>'
    program_desc = "Chapter 1: The New Horizon.\n\nEnjoy the artwork!"

    mock_page, mock_context, mock_desc_area, _ = _create_mock_page_for_da_submit(preset_text=preset_html)

    comp_list = []
    with patch.object(disp, "safe_goto"), \
         patch.object(disp, "assert_field_value", return_value=(True, "OK")), \
         patch.object(disp, "check_selector_state", return_value=(True, "OK")), \
         patch.object(disp, "assert_submitted", return_value=(True, "")), \
         patch("time.sleep"):
        ok, msg = disp._submit_deviation(
            mock_page, mock_context, [dummy_da_env["img"]],
            title="Artwork Title", desc=program_desc, _tags=["art"],
            is_mature=False, mature_level="strict", is_ai=False,
            gallery="", compliance=comp_list, keep_preset_description=True
        )

    assert ok is True
    assert getattr(disp, "_last_desc_source", None) == "拼接"

    # 验证最终注入的 HTML 保留了预设的真实超链接标签，而不是被转义成实体
    injected_html = mock_desc_area.evaluate("el => el.innerHTML || ''")
    assert '<a href="https://boosty.to/myart">Boosty</a>' in injected_html
    assert '<a href="https://fanbox.cc/@myart">Fanbox</a>' in injected_html
    assert "&lt;a href=" not in injected_html
    # 验证段间包含空段落间距
    assert "<p><br></p>" in injected_html
    # 验证合规项记录了预设超链接保全
    assert any(c.get("item") == "preset_links_preserved" and "2/2 预设超链接已保全" in c.get("detail", "") for c in comp_list)


def test_da_preset_link_preservation_assertion_failure_aborts(dummy_da_env, caplog):
    """
    测试预设超链接保全断言失败场景:
    若注入后编辑器 DOM 丢弃了预设中的某个超链接，断言失败阻断发布，草稿保留在 Studio
    """
    disp = dummy_da_env["disp"]
    preset_html = '<p>Follow on <a href="https://boosty.to/important_link">Boosty</a></p>'
    program_desc = "Standard illustration description"

    mock_page, mock_context, mock_desc_area, _ = _create_mock_page_for_da_submit(preset_text=preset_html)

    # 模拟编辑器在注入后回读时，丢失了 boosty 超链接 (例如变成了纯文本)
    broken_readback_html = "<p>Standard illustration description</p><p><br></p><p>Follow on Boosty</p>"

    def broken_eval(script, *args):
        s = str(script)
        if "insertHTML" in s or "setContent" in s:
            return True
        if "innerHTML" in s:
            return broken_readback_html
        return preset_html

    mock_desc_area.evaluate.side_effect = broken_eval
    with patch.object(disp, "read_existing_description", return_value=preset_html):
        comp_list = []
        with caplog.at_level(logging.ERROR), \
             patch.object(disp, "safe_goto"), \
             patch.object(disp, "assert_field_value", return_value=(True, "OK")), \
             patch.object(disp, "check_selector_state", return_value=(True, "OK")), \
             patch.object(disp, "assert_submitted", return_value=(True, "")), \
             patch("time.sleep"):
            ok, msg = disp._submit_deviation(
                mock_page, mock_context, [dummy_da_env["img"]],
                title="Artwork Title", desc=program_desc, _tags=["art"],
                is_mature=False, mature_level="strict", is_ai=False,
                gallery="", compliance=comp_list, keep_preset_description=True
            )

    assert ok is False
    assert "DeviantArt 预设描述超链接保全断言失败" in msg
    assert "https://boosty.to/important_link" in msg
    assert "草稿保留在 Studio" in msg
    assert any("预设描述超链接保全断言失败" in record.message for record in caplog.records)


def test_da_preset_residue_warning_only_without_mutating_preset(dummy_da_env, caplog):
    """
    测试预设残留只警告不改预设:
    预设中包含历史签名标记或引流链接时，仅记录 WARNING 日志，绝不篡改或截断预设内容
    """
    disp = dummy_da_env["disp"]
    residue_preset_html = (
        '<p>Old description text</p>'
        '<p>Where to find me — same name everywhere:</p>'
        '<p>💜Patreon：<a href="https://patreon.com/foxtontonton">FOXTONTONTON</a></p>'
    )
    program_desc = "Brand new exclusive story"

    mock_page, mock_context, mock_desc_area, _ = _create_mock_page_for_da_submit(preset_text=residue_preset_html)

    comp_list = []
    with caplog.at_level(logging.WARNING), \
         patch.object(disp, "safe_goto"), \
         patch.object(disp, "assert_field_value", return_value=(True, "OK")), \
         patch.object(disp, "check_selector_state", return_value=(True, "OK")), \
         patch.object(disp, "assert_submitted", return_value=(True, "")), \
         patch("time.sleep"):
        ok, msg = disp._submit_deviation(
            mock_page, mock_context, [dummy_da_env["img"]],
            title="Artwork Title", desc=program_desc, _tags=["art"],
            is_mature=False, mature_level="strict", is_ai=False,
            gallery="", compliance=comp_list, keep_preset_description=True
        )

    assert ok is True
    warning_records = [r for r in caplog.records if "疑似历史残留内容" in r.message]
    assert len(warning_records) >= 1
    assert "保留原样不作篡改" in warning_records[0].message

    # 验证最终注入的 HTML 中，残留的预设内容完整保留，绝无被擅自删除或截断
    injected_html = mock_desc_area.evaluate("el => el.innerHTML || ''")
    assert "Old description text" in injected_html
    assert "Where to find me — same name everywhere:" in injected_html
    assert 'href="https://patreon.com/foxtontonton"' in injected_html
