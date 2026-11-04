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
        self._tag_input.get_attribute.side_effect = lambda attr: "text" if attr == "type" else ""

        def fake_fill(val, *args, **kwargs):
            self.typed_inputs.append(val)

        def fake_press(key, *args, **kwargs):
            if key == "Enter" and self.typed_inputs:
                curr = self.typed_inputs[-1]
                if curr not in self.chips:
                    self.chips.append(curr)
            elif key == "Backspace" and self.chips:
                self.chips.pop()

        self._tag_input.fill.side_effect = fake_fill
        self._tag_input.press.side_effect = fake_press

        self.tag_container = MagicMock()
        self.tag_container.is_visible.return_value = True

        self._title_input = MagicMock()
        self._title_input.is_visible.return_value = True
        self._title_input.input_value.side_effect = lambda: self.current_title
        self._title_input.get_attribute.side_effect = lambda attr: "title" if attr == "name" else ""
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

        # 标签清理中的删除按钮
        if any(k in sel for k in ["rjL0oj", "remove", "delete", "Remove tag", "TagChip button", "[data-tag] button", "[data-tag] svg", "[data-tag] path"]):
            if self.chips:
                remove_btn = MagicMock()
                def do_remove(*a, **kw):
                    if self.chips:
                        self.chips.pop(0)
                remove_btn.click.side_effect = do_remove
                remove_btn.evaluate.side_effect = do_remove
                return remove_btn
            return None

        if "Submit" in sel or "submit" in sel:
            return self.submit_btn
        if "input[type='file']" in sel:
            return self.file_input
        if "description" in sel or "tiptap" in sel or "role='textbox'" in sel:
            return self.desc_area

        # 标题输入框 (严格排除含有 :not([name='title']) 的标签输入框选择器)
        if ":not([name='title'])" not in sel and ("input[name='title']" in sel or sel == "input[name='title']"):
            return self._title_input

        # 标签容器
        if ("combobox" in sel.lower() or "an5hlk" in sel.lower() or "data-tag" in sel.lower()) and "input" not in sel:
            return self.tag_container

        # 标签输入框
        if "tag" in sel.lower() or "input" in sel.lower() or "combobox" in sel.lower():
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

    # -------------------------------------------------------------------------
    # 5. 正向定位与失明防护 (Positive Localization & Anti-Blindness) - fix/da-tags-live
    # -------------------------------------------------------------------------

    def test_inject_tags_fails_when_no_editor_found(self, da_dispatcher):
        """inject 前正向定位标签编辑器，定位不到立即以「未定位到 DA 标签编辑器」失败"""
        blind_page = MagicMock()
        blind_page.query_selector.return_value = None
        blind_page.query_selector_all.return_value = []

        ok, msg = da_dispatcher.inject_tags(blind_page, ["anime", "digital art"])

        assert ok is False
        assert msg == "未定位到 DA 标签编辑器"

    def test_clear_existing_tags_blind_readback_does_not_report_cleared(self, da_dispatcher, caplog):
        """clear 不得对失明回读报已清空，返回 -1 并记录警告日志"""
        blind_page = MagicMock()
        blind_page.query_selector.return_value = None
        blind_page.query_selector_all.return_value = []

        import logging
        with caplog.at_level(logging.INFO):
            res = da_dispatcher.clear_existing_tags(blind_page)

        assert res == -1
        # 绝不得报已清空至 0
        assert "已清空至 0" not in caplog.text
        assert "失明态" in caplog.text or "跳过清理" in caplog.text

    def test_diagnostic_logs_and_failed_attempts_output(self, da_dispatcher, caplog):
        """输入框定位状态、逐标签成败汇总、实际 chip 列表与 failed_attempts 必须明确输出"""
        page = MockDAPage()
        # 模拟第二个标签输入抛出异常
        orig_side_effect = page._tag_input.fill.side_effect
        def faulty_fill(val, *a, **kw):
            if val == "broken_tag":
                raise RuntimeError("Input element detached")
            return orig_side_effect(val, *a, **kw)
        page._tag_input.fill.side_effect = faulty_fill

        import logging
        with caplog.at_level(logging.INFO):
            ok, msg = da_dispatcher.inject_tags(page, ["valid_tag", "broken_tag"])

        assert ok is False
        # 校验诊断日志输出
        assert "[标签诊断] 标签编辑器定位状态:" in caplog.text
        assert "[标签诊断] 逐标签成败汇总:" in caplog.text
        assert "[标签诊断] 实际 chip 列表" in caplog.text
        assert "failed_attempts 详细列表: ['broken_tag']" in caplog.text
        # 校验返回失败消息包含 failed_attempts
        assert "注入失败: ['broken_tag']" in msg

    def test_real_dom_fixture_hit_assertions(self, da_dispatcher):
        """
        基于真实 DOM fixture (tests/fixtures/deviantart_tag_section.html) 的选择器命中断言：
        1. 验证真实 DOM 中能够准确定位容器、输入框、30 个 chip 与删除按钮；
        2. 验证错误或猜测的选择器必定断言失败变红，杜绝再次凭空猜测。
        """
        fixture_path = Path(__file__).parent / "fixtures" / "deviantart_tag_section.html"
        assert fixture_path.exists(), f"真实 DOM fixture 不存在: {fixture_path}"
        html_content = fixture_path.read_text(encoding="utf-8")

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, "html.parser")

        # 1. 验证正向输入框选择器命中 (真实 input 没有 placeholder，但有 id="_r_f_" 和 role='combobox' 父级)
        real_input = soup.select_one("div[role='combobox'] input:not([name='title'])")
        assert real_input is not None, "选择器未能命中真实 DOM 中的标签输入框"
        assert real_input.get("id") == "_r_f_"

        # 验证历史猜测的选择器会 miss (若使用猜的 placeholder 则必须变红)
        old_guessed_input = soup.select_one("input[placeholder*='tag' i]")
        assert old_guessed_input is None, "真实 DA Studio 输入框不应含有 placeholder，旧猜测选择器应失效"

        # 2. 验证正向容器选择器命中
        real_container = soup.select_one("div[role='combobox'] div.an5HlK")
        assert real_container is not None, "选择器未能命中真实 DOM 中的标签 Combobox 容器"

        old_guessed_container = soup.select_one("[data-hook='tag_input']")
        assert old_guessed_container is None, "真实 DOM 中不含 data-hook='tag_input'，旧猜测应失效"

        # 3. 验证 chip 列表解析：30 个 chip 全部命中
        chips = soup.select("span[data-tag]")
        assert len(chips) == 30, f"真实 fixture 包含 30 个 chip，实际命中 {len(chips)} 个"

        # 4. 验证删除按钮定位与纯净文本提取
        first_chip = chips[0]
        remove_icon = first_chip.select_one("span.rjL0oj")
        assert remove_icon is not None, "未能在 chip 中定位到删除/关闭图标 (span.rjL0oj)"

        # 提取全部 30 个标签文本并确认不含关闭图标
        extracted_tags = []
        for c in chips:
            c_copy = BeautifulSoup(str(c), "html.parser")
            for icon in c_copy.select("span.rjL0oj, svg"):
                icon.decompose()
            extracted_tags.append(c_copy.get_text().strip())

        assert len(extracted_tags) == 30
        assert extracted_tags[0] == "Bara"
        assert extracted_tags[-1] == "musclelatex"
        assert "Muscle Growth" in extracted_tags
