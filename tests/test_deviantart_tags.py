# -*- coding: utf-8 -*-
"""
ArtCrossPost - DeviantArt 标签注入与校验测试 (fix/da-tags-apply)
覆盖四大核心路径:
1. 正常路径 (Normal):
   - 多词标签整体输入不拆分
   - 按 payload 顺序逐个输入并回车成 chip
   - 候选等待与精确匹配优先
   - 回读 chip 与期望比对 (大小写不敏感)
   - 结果消息与合规记录注明已设置标签数
2. 残留清理 (Residue Clearing):
   - 表单已有旧 chip 时清空至 0
   - payload 标签为空时仍先清残留，结果消息注明本帖未设置标签
3. 断言拦截 (Assertion Interception):
   - 提交前回读比对，缺失/多余即中止提交，不点击发布按钮并清理孤儿草稿
   - 返回失败并列明缺失/多余差异
4. 上限截断 (Upper Limit Truncation):
   - DA 上限 30，超出只填前 30
   - 结果消息注明截断数，不得静默丢失
"""
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from dispatchers.deviantart import DeviantArtDispatcher


class MockDAPage:
    """模拟 DeviantArt 提交页面中标签与表单交互的 Playwright Page 对象"""

    def __init__(self, initial_chips=None, autocomplete_options=None):
        self.chips = list(initial_chips or [])
        self.autocomplete_options = list(autocomplete_options or [])
        self.typed_inputs = []
        self.current_title = "Test Title"
        self.url = "https://www.deviantart.com/artist/art/Deviation-12345"

        self.goto = MagicMock()
        self.wait_for_selector = MagicMock()
        self.wait_for_timeout = MagicMock()

        self._tag_input = MagicMock()
        self._tag_input.is_visible.return_value = True

        def fake_fill(val, *args, **kwargs):
            self.typed_inputs.append(val)

        def fake_press(key, *args, **kwargs):
            if key == "Enter" and self.typed_inputs:
                curr = self.typed_inputs[-1]
                if curr not in self.chips:
                    self.chips.append(curr)

        self._tag_input.fill.side_effect = fake_fill
        self._tag_input.press.side_effect = fake_press

        self._title_input = MagicMock()
        self._title_input.is_visible.return_value = True
        self._title_input.input_value.side_effect = lambda: self.current_title
        def fake_title_fill(val, *args, **kwargs):
            self.current_title = val
        self._title_input.fill.side_effect = fake_title_fill

        self.submit_btn = MagicMock()
        self.submit_btn.is_enabled.return_value = True
        self.submit_btn.get_attribute.return_value = "false"
        self.submit_btn.inner_text.return_value = "Submit"

        self.file_input = MagicMock()
        self.file_input.get_attribute.return_value = "image/png,image/jpeg"
        self.desc_area = MagicMock()
        self.desc_area.inner_text.return_value = ""
        self.desc_area.get_attribute.return_value = ""
        self.desc_area.evaluate.return_value = ""

    def query_selector(self, sel):
        if "option" in sel and ":has-text(" in sel:
            for opt in self.autocomplete_options:
                if f":has-text('{opt}')" in sel:
                    opt_el = MagicMock()
                    def make_opt_click(target_opt):
                        def do_click(*a, **kw):
                            if target_opt not in self.chips:
                                self.chips.append(target_opt)
                        return do_click
                    opt_el.click.side_effect = make_opt_click(opt)
                    opt_el.is_visible.return_value = True
                    return opt_el
            return None

        if "Submit" in sel or "submit" in sel:
            return self.submit_btn
        if "input[type='file']" in sel:
            return self.file_input
        if "description" in sel or "tiptap" in sel or "role='textbox'" in sel:
            return self.desc_area
        if "input[name='title']" in sel or "title" in sel:
            return self._title_input
        if "tag" in sel.lower() or "input" in sel.lower():
            return self._tag_input

        return MagicMock()

    def query_selector_all(self, sel):
        if "Submit" in sel:
            return [self.submit_btn]
        if "input[type='file']" in sel:
            return [self.file_input]
        if "chip" in sel.lower() or "tag" in sel.lower():
            els = []
            for c in list(self.chips):
                chip_mock = MagicMock()
                chip_mock.inner_text.return_value = f"{c} ×"
                remove_btn = MagicMock()
                def make_click(tag_val):
                    def do_remove(*a, **kw):
                        if tag_val in self.chips:
                            self.chips.remove(tag_val)
                    return do_remove
                remove_btn.click.side_effect = make_click(c)
                chip_mock.query_selector.return_value = remove_btn
                els.append(chip_mock)
            return els
        return []

    def evaluate(self, script, *args):
        if "querySelectorAll" in script and "cloneNode" in script:
            return list(self.chips)
        if "Remove tag" in script or "remove_tag" in script or "TagChip button" in script:
            self.chips.clear()
            return None
        if "title" in str(script).lower() and args:
            self.current_title = str(args[0])
            return None
        return {"count": 1, "llama": False}

    def wait_for_timeout(self, ms):
        pass


@pytest.fixture
def da_dispatcher(tmp_path):
    cookies_dir = tmp_path / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    return DeviantArtDispatcher(cookies_dir)


class TestDeviantArtTags:
    """DeviantArt 标签注入、回读比对与截断全流程测试"""

    # -------------------------------------------------------------------------
    # 1. 正常路径 (Normal Path)
    # -------------------------------------------------------------------------

    def test_da_tags_normal_flow_inject_tags_method(self, da_dispatcher):
        """测试 inject_tags 纯逻辑：按序输入、多词标签不拆分、精确匹配优先、回读匹配"""
        page = MockDAPage(autocomplete_options=["digital art"])
        tags = ["anime", "digital art", "concept illustration"]

        ok, note = da_dispatcher.inject_tags(page, tags)

        assert ok is True
        assert "已设置 3 个标签" in note
        assert getattr(da_dispatcher, "_last_tag_note", None) == "已设置 3 个标签"
        # 验证输入按顺序
        assert page.typed_inputs == ["anime", "digital art", "concept illustration"]
        # 多词标签整词输入未被拆分
        assert "digital art" in page.typed_inputs
        assert "concept illustration" in page.typed_inputs
        # 验证回读 chip 完全一致
        assert [c.lower() for c in page.chips] == [t.lower() for t in tags]

    def test_da_tags_normal_case_insensitive_readback(self, da_dispatcher):
        """测试大小写不敏感比对：payload 小写，页面 DOM 返回大写仍校验通过"""
        page = MockDAPage()
        def fake_press_uppercase(key, *args, **kwargs):
            if key == "Enter" and page.typed_inputs:
                curr = page.typed_inputs[-1].upper()
                if curr not in page.chips:
                    page.chips.append(curr)
        page._tag_input.press.side_effect = fake_press_uppercase

        tags = ["anime", "digital art"]
        ok, note = da_dispatcher.inject_tags(page, tags)

        assert ok is True
        assert "已设置 2 个标签" in note

    def test_da_tags_normal_publish_pipeline_message_and_compliance(self, da_dispatcher, tmp_path):
        """测试完整 publish 流程下结果消息及 compliance 正确注入标签信息"""
        dummy_img = tmp_path / "art.jpg"
        dummy_img.write_bytes(b"image_content")

        page = MockDAPage()
        mock_context = MagicMock()
        mock_studio = MagicMock()
        mock_studio.inner_text.return_value = "No drafts here"
        mock_studio.url = "https://www.deviantart.com/studio"
        mock_context.new_page.return_value = mock_studio
        mock_context.pages = [page]

        payload = {
            "title": "Normal Tags Artwork",
            "description": "My artwork description",
            "tags": ["anime", "digital art", "fantasy"],
            "images": [str(dummy_img)],
        }

        with patch.object(da_dispatcher, "get_cookies", return_value=[{"name": "auth", "value": "1"}]), \
             patch.object(da_dispatcher, "create_persistent_stealth_context", return_value=mock_context), \
             patch.object(da_dispatcher, "safe_goto"), \
             patch.object(da_dispatcher, "assert_field_value", return_value=(True, "OK")), \
             patch.object(da_dispatcher, "check_selector_state", return_value=(True, "OK")), \
             patch.object(da_dispatcher, "close_login_session"), \
             patch.object(da_dispatcher, "_count_deviantart_gallery_images", return_value=1), \
             patch("time.sleep"):
            ok, msg, comp = da_dispatcher.publish(payload)

        assert ok is True
        assert "已设置 3 个标签" in msg
        # 验证 compliance 项中包含 tags 记录
        tag_comp = next((c for c in comp if c.get("item") == "tags"), None)
        assert tag_comp is not None
        assert tag_comp["verified"] is True
        assert "已设置 3 个标签" in tag_comp["detail"]

    # -------------------------------------------------------------------------
    # 2. 残留清理 (Residue Clearing)
    # -------------------------------------------------------------------------

    def test_da_tags_residue_clearing_with_new_tags(self, da_dispatcher):
        """测试表单存在历史残留 chip 时，先清空至 0 再填入本次新标签"""
        page = MockDAPage(initial_chips=["old_stale_1", "old_stale_2"])
        assert len(page.chips) == 2

        new_tags = ["brand_new_tag"]
        ok, note = da_dispatcher.inject_tags(page, new_tags)

        assert ok is True
        assert "已设置 1 个标签" in note
        # 旧残留必须被彻底清除，仅保留本次新标签
        assert "old_stale_1" not in page.chips
        assert "old_stale_2" not in page.chips
        assert page.chips == ["brand_new_tag"]

    def test_da_tags_residue_clearing_when_payload_tags_empty(self, da_dispatcher, tmp_path):
        """测试 payload 标签为空时仍先清空残留，结果消息明确注明'本帖未设置标签'"""
        page = MockDAPage(initial_chips=["residue_tag_to_remove"])
        assert len(page.chips) == 1

        ok, note = da_dispatcher.inject_tags(page, [])

        assert ok is True
        assert note == "本帖未设置标签"
        assert len(page.chips) == 0

        # 通过 publish 检验结果消息
        dummy_img = tmp_path / "art.jpg"
        dummy_img.write_bytes(b"image_content")

        mock_context = MagicMock()
        mock_studio = MagicMock()
        mock_studio.inner_text.return_value = "No drafts here"
        mock_context.new_page.return_value = mock_studio
        mock_context.pages = [page]

        payload = {
            "title": "Empty Tags Artwork",
            "description": "Artwork description without tags",
            "tags": [],
            "images": [str(dummy_img)],
        }

        with patch.object(da_dispatcher, "get_cookies", return_value=[{"name": "auth", "value": "1"}]), \
             patch.object(da_dispatcher, "create_persistent_stealth_context", return_value=mock_context), \
             patch.object(da_dispatcher, "safe_goto"), \
             patch.object(da_dispatcher, "assert_field_value", return_value=(True, "OK")), \
             patch.object(da_dispatcher, "check_selector_state", return_value=(True, "OK")), \
             patch.object(da_dispatcher, "close_login_session"), \
             patch.object(da_dispatcher, "_count_deviantart_gallery_images", return_value=1), \
             patch("time.sleep"):
            ok, msg, comp = da_dispatcher.publish(payload)

        assert ok is True
        assert "本帖未设置标签" in msg
        tag_comp = next((c for c in comp if c.get("item") == "tags"), None)
        assert tag_comp is not None
        assert tag_comp["detail"] == "本帖未设置标签"

    # -------------------------------------------------------------------------
    # 3. 断言拦截 (Assertion Interception)
    # -------------------------------------------------------------------------

    def test_da_tags_assertion_interception_missing_tags(self, da_dispatcher):
        """测试回读缺失标签：预期 3 个但页面仅回读出 2 个，中止提交并报错缺失差异"""
        page = MockDAPage()
        tags = ["tag1", "tag2", "tag3_missing"]

        # 模拟最后一个标签输入失败/未形成 chip
        def fake_press_drop_last(key, *args, **kwargs):
            if key == "Enter" and page.typed_inputs:
                curr = page.typed_inputs[-1]
                if curr != "tag3_missing" and curr not in page.chips:
                    page.chips.append(curr)
        page._tag_input.press.side_effect = fake_press_drop_last

        ok, err_msg = da_dispatcher.inject_tags(page, tags)

        assert ok is False
        assert "DeviantArt 标签回读校验不一致，已中止提交" in err_msg
        assert "缺失: ['tag3_missing']" in err_msg

    def test_da_tags_assertion_interception_extra_tags(self, da_dispatcher):
        """测试回读多余标签：页面回读出预期外的残留或额外标签，中止提交并报错多余差异"""
        page = MockDAPage(initial_chips=["unremovable_stale_chip"])
        page.evaluate = MagicMock(return_value=["unremovable_stale_chip", "wanted_tag"])

        tags = ["wanted_tag"]
        ok, err_msg = da_dispatcher.inject_tags(page, tags)

        assert ok is False
        assert "DeviantArt 标签回读校验不一致，已中止提交" in err_msg
        assert "多余: ['unremovable_stale_chip']" in err_msg

    def test_da_tags_assertion_interception_aborts_submit_and_cleans_draft(self, da_dispatcher, tmp_path):
        """测试比对失败时 _submit_deviation 中止流程：不点击发布按钮，且触发清理孤儿草稿"""
        page = MockDAPage()
        # 模拟回读总是为空
        page.evaluate = MagicMock(return_value=[])

        dummy_img = tmp_path / "art.jpg"
        dummy_img.write_bytes(b"image_content")

        with patch.object(da_dispatcher, "assert_field_value", return_value=(True, "OK")), \
             patch.object(da_dispatcher, "check_selector_state", return_value=(True, "OK")), \
             patch.object(da_dispatcher, "_cleanup_orphan_draft") as mock_cleanup, \
             patch("time.sleep"):
            sub_ok, sub_msg = da_dispatcher._submit_deviation(
                page, MagicMock(), [str(dummy_img)], "Mismatch Artwork", "Desc",
                tags=["expected_tag"], is_mature=False, mature_level="moderate",
                is_ai=False, gallery="", compliance=[]
            )

        assert sub_ok is False
        assert "DeviantArt 标签回读校验不一致，已中止提交" in sub_msg
        assert "缺失: ['expected_tag']" in sub_msg
        # 核心防漏保护断言：发布按钮绝对未被点击，且草稿清理被调用
        assert not page.submit_btn.click.called
        assert not page.submit_btn.evaluate.called
        mock_cleanup.assert_called_once()

    # -------------------------------------------------------------------------
    # 4. 上限截断 (Upper Limit Truncation)
    # -------------------------------------------------------------------------

    def test_da_tags_upper_limit_truncation_35_to_30(self, da_dispatcher):
        """测试 DA 标签 30 个上限约束：传入 35 个，仅填入前 30 个，注明截断 5 个"""
        page = MockDAPage()
        tags_35 = [f"tag_{i:02d}" for i in range(1, 36)]

        ok, note = da_dispatcher.inject_tags(page, tags_35)

        assert ok is True
        assert "已设置 30 个标签" in note
        assert "截断 5 个" in note
        # 验证只输入并生成了前 30 个 chip
        assert len(page.typed_inputs) == 30
        assert page.typed_inputs == tags_35[:30]
        assert page.chips == tags_35[:30]

    def test_da_tags_upper_limit_truncation_publish_result_message(self, da_dispatcher, tmp_path):
        """测试 publish 整体流程下截断数被注入结果消息，绝不静默丢失"""
        dummy_img = tmp_path / "art.jpg"
        dummy_img.write_bytes(b"image_content")

        page = MockDAPage()
        mock_context = MagicMock()
        mock_studio = MagicMock()
        mock_studio.inner_text.return_value = "No drafts here"
        mock_context.new_page.return_value = mock_studio
        mock_context.pages = [page]

        tags_33 = [f"custom_tag_{i}" for i in range(1, 34)]

        payload = {
            "title": "Truncated Tags Artwork",
            "description": "Artwork description with 33 tags",
            "tags": tags_33,
            "images": [str(dummy_img)],
        }

        with patch.object(da_dispatcher, "get_cookies", return_value=[{"name": "auth", "value": "1"}]), \
             patch.object(da_dispatcher, "create_persistent_stealth_context", return_value=mock_context), \
             patch.object(da_dispatcher, "safe_goto"), \
             patch.object(da_dispatcher, "assert_field_value", return_value=(True, "OK")), \
             patch.object(da_dispatcher, "check_selector_state", return_value=(True, "OK")), \
             patch.object(da_dispatcher, "close_login_session"), \
             patch.object(da_dispatcher, "_count_deviantart_gallery_images", return_value=1), \
             patch("time.sleep"):
            ok, msg, comp = da_dispatcher.publish(payload)

        assert ok is True
        # 结果消息必须明确体现已设置 30 个并注明截断 3 个
        assert "已设置 30 个标签" in msg
        assert "截断 3 个" in msg
        tag_comp = next((c for c in comp if c.get("item") == "tags"), None)
        assert tag_comp is not None
        assert "截断 3 个" in tag_comp["detail"]
