# -*- coding: utf-8 -*-
"""
ArtCrossPost - UI 跟进四项单元测试 (fix/ui-followup-batch)

1. 左栏纵向自适应:
   - left_layout 对 desc_group 与 media_group 设置 stretch=1，meta_group stretch=0
   - desc_input 与 img_list_widget 设置 stretch=1，窗口长高时两者纵向自适应生长
   - 保持最小高度 (85 与 75)
2. Bluesky 打码保护持久化:
   - _sync_config_from_ui 平台汇总写入 bluesky_censor
   - 关闭打码保护 -> 同步配置 -> 重启程序 -> 状态保持关闭
3. 相册下拉外框:
   - wrap_flow_field 与 wrap_flow_slider 采用 QWidget#<id> 限定样式，不级联子控件
   - 全局 get_theme_stylesheet 补齐 QComboBox:editable 与 QComboBox QLineEdit 规则
   - DA 相册下拉内部 lineEdit 设置主题兜底透明样式
4. Bluesky 签名转换:
   - 组装文本时签名中的 [显示文字](URL) 转换为纯文本，URL 以裸文本呈现
   - 无 [ ]( ) 语法残留；正文普通文本与方括号严格不受影响
   - extract_facets 成功命中签名中的全部 5 个引流链接并生成 link facet
"""
import pytest
from unittest.mock import MagicMock, patch
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit, QListWidget, QTextEdit, QWidget

from app import MainWindow, SettingsDialog, wrap_flow_field, wrap_flow_slider, get_theme_stylesheet
from signature_utils import (
    CANONICAL_SIGNATURE_TEMPLATE,
    CANONICAL_SIGNATURE_LINKS,
    convert_signature_links_for_bluesky,
    render_description_for_bluesky,
    split_body_and_signature,
)
from dispatchers.bluesky import BlueskyDispatcher, extract_facets


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestUIFollowupBatch:
    """UI 跟进四项全面回归测试集"""

    @pytest.fixture(autouse=True)
    def setup_app(self, qapp):
        self.app = qapp

    def test_left_column_vertical_stretch_and_bounds(self):
        """条目 1: 验证左栏纵向自适应拉伸权重与最小高度"""
        win = MainWindow()
        win.show()
        self.app.processEvents()

        # 1. 验证 left_layout 中各组的拉伸权重
        left_layout = win.left_layout
        assert left_layout is not None, "MainWindow 缺少 left_layout 属性"

        # meta_group stretch 应为 0，desc_group 与 media_group stretch 应为 1
        assert left_layout.stretch(0) == 0, "1 号区 meta_group 纵向拉伸权重应为 0 (自然高度)"
        assert left_layout.stretch(1) == 1, "2 号区 desc_group 纵向拉伸权重应为 1 (随窗口拉伸)"
        assert left_layout.stretch(2) == 1, "3 号区 media_group 纵向拉伸权重应为 1 (随窗口拉伸)"

        # 2. 验证 desc_group 内部 desc_input 拉伸权重与最小高度
        desc_layout = win.desc_group.layout()
        assert desc_layout.stretch(0) == 1, "desc_input 在 desc_layout 中的拉伸权重应为 1"
        assert win.desc_input.minimumHeight() >= 85, "desc_input 最小高度不得低于 85px"

        # 3. 验证 media_group 内部 img_list_widget 拉伸权重与最小高度
        media_layout = win.media_group.layout()
        assert media_layout.stretch(1) == 1, "img_list_widget 在 media_layout 中的拉伸权重应为 1"
        assert win.img_list_widget.minimumHeight() >= 75, "img_list_widget 最小高度不得低于 75px"

        # 4. 模拟窗口纵向放大，验证 desc_input 与 img_list_widget 随之长高
        win.resize(1100, 680)
        self.app.processEvents()
        h_desc_small = win.desc_input.height()
        h_img_small = win.img_list_widget.height()

        win.resize(1100, 960)
        self.app.processEvents()
        h_desc_large = win.desc_input.height()
        h_img_large = win.img_list_widget.height()

        assert h_desc_large >= h_desc_small, f"窗口变高时描述框应随之长高: {h_desc_large} vs {h_desc_small}"
        assert h_img_large >= h_img_small, f"窗口变高时图片列表应随之长高: {h_img_large} vs {h_img_small}"
        assert h_desc_large >= 85
        assert h_img_large >= 75

    def test_bluesky_censor_persistence_in_sync_config(self, monkeypatch):
        """条目 2: 验证 Bluesky 打码保护状态在 _sync_config_from_ui 中的持久化与重启复原"""
        win = MainWindow()
        bsky_ctrl = win.platform_controls.get("bluesky")
        assert bsky_ctrl is not None, "未找到 bluesky 平台控制组件"
        bsky_censor = bsky_ctrl["options"]["censor"]
        assert isinstance(bsky_censor, QCheckBox)

        # 1. 关闭 Bluesky 打码保护
        bsky_censor.setChecked(False)
        win._sync_config_from_ui()

        # 断言 defaults 中成功保存 False
        assert win.config.get("defaults", {}).get("bluesky_censor") is False, "bluesky_censor 未正确持久化为 False"

        # 2. 模拟重启：用保存的配置新建窗口，打码保护仍为关闭
        saved_config = dict(win.config)
        with patch("app.load_config", return_value=saved_config):
            restarted_win = MainWindow()
            restarted_censor = restarted_win.platform_controls["bluesky"]["options"]["censor"]
            assert restarted_censor.isChecked() is False, "重启后 Bluesky 打码保护应保持关闭状态"

        # 3. 再次开启打码保护并验证持久化
        bsky_censor.setChecked(True)
        win._sync_config_from_ui()
        assert win.config.get("defaults", {}).get("bluesky_censor") is True, "bluesky_censor 未正确持久化为 True"

    def test_flow_container_id_styling_and_editable_combo_frame(self):
        """条目 3: 验证包装盒 ID 限定样式与 DA 相册下拉内部 lineEdit 兜底样式"""
        # 1. wrap_flow_field ID 限定
        test_widget = QWidget()
        field_container = wrap_flow_field("测试标签:", test_widget)
        assert field_container.objectName() == "flowFieldContainer"
        assert field_container.styleSheet() == "QWidget#flowFieldContainer { background: transparent; border: none; }"

        # 2. wrap_flow_slider ID 限定
        slider_container = wrap_flow_slider("滑杆:", test_widget, test_widget)
        assert slider_container.objectName() == "flowSliderContainer"
        assert slider_container.styleSheet() == "QWidget#flowSliderContainer { background: transparent; border: none; }"

        # 3. get_theme_stylesheet 中关于 QComboBox:editable 的样式规则
        theme_qss = get_theme_stylesheet("pure_dark", 13)
        assert "QComboBox:editable" in theme_qss, "主题 QSS 中缺少 QComboBox:editable 选择器"
        assert "QComboBox:editable QLineEdit, QComboBox QLineEdit" in theme_qss, "缺少 QComboBox QLineEdit 子控件选择器"

        # 4. 主界面 DA 相册下拉内部 lineEdit 兜底样式
        win = MainWindow()
        da_ctrl = win.platform_controls.get("deviantart")
        assert da_ctrl is not None
        da_gallery = da_ctrl["options"]["gallery"]
        assert da_gallery.isEditable() is True
        line_edit = da_gallery.lineEdit()
        assert line_edit is not None
        le_style = line_edit.styleSheet()
        assert "background: transparent" in le_style
        assert "border: none" in le_style

        # 5. 设置对话框中 DA 相册下拉内部 lineEdit 兜底样式
        dlg = SettingsDialog(win)
        dlg_da_gallery = dlg.def_da_gallery_combo
        assert dlg_da_gallery.isEditable() is True
        dlg_line_edit = dlg_da_gallery.lineEdit()
        assert dlg_line_edit is not None
        dlg_le_style = dlg_line_edit.styleSheet()
        assert "background: transparent" in dlg_le_style
        assert "border: none" in dlg_le_style

    def test_bluesky_signature_link_conversion_and_facet_extraction(self):
        """条目 4: 验证 Bluesky 签名转换为裸 URL、无语法残留，且 facet 命中全部链接"""
        # 1. 签名段独立转换测试
        converted_sig = convert_signature_links_for_bluesky(CANONICAL_SIGNATURE_TEMPLATE)
        assert "[" not in converted_sig and "]" not in converted_sig, "转换后签名不得含有方括号残留"
        for _, url in CANONICAL_SIGNATURE_LINKS:
            assert url in converted_sig, f"裸 URL 必须完整保留在签名纯文本中: {url}"

        # 2. 混合正文描述测试 (正文带 [OC], [1/3], [Artist])
        body_with_brackets = "My special artwork [OC] [Chapter 1/3] by [Artist]. Hope you enjoy!"
        full_desc = f"{body_with_brackets}\n\n{CANONICAL_SIGNATURE_TEMPLATE}"

        rendered = render_description_for_bluesky(full_desc)

        # 验证正文普通方括号严格不受影响
        assert "[OC]" in rendered, "正文中的 [OC] 方括号不得被误消除"
        assert "[Chapter 1/3]" in rendered, "正文中的 [Chapter 1/3] 方括号不得被误消除"
        assert "[Artist]" in rendered, "正文中的 [Artist] 方括号不得被误消除"

        # 验证签名段中无 [ ]( ) 标记残留
        body_part, sig_part = split_body_and_signature(rendered)
        assert "[" not in sig_part and "]" not in sig_part, "签名部分不得有任何方括号残留"

        # 3. 验证 extract_facets 准确提取全部 6 个链接 facet (含第 6 个 Bluesky 链接)
        facets = extract_facets(rendered)
        link_facets = [f for f in facets if f["features"][0].get("$type") == "app.bsky.richtext.facet#link"]
        assert len(link_facets) == 6, f"应准确提取全部 6 个链接 facet，实际提取: {len(link_facets)}"

        facet_uris = [f["features"][0]["uri"] for f in link_facets]
        assert "https://bsky.app/profile/foxtontonton.bsky.social" in facet_uris
        for _, expected_url in CANONICAL_SIGNATURE_LINKS:
            assert expected_url in facet_uris, f"链接 facet 必须命中签名中的引流 URL: {expected_url}"

        # 验证 UTF-8 字节偏移准确性
        rendered_bytes = rendered.encode("utf-8")
        for f in link_facets:
            start_b = f["index"]["byteStart"]
            end_b = f["index"]["byteEnd"]
            extracted_from_bytes = rendered_bytes[start_b:end_b].decode("utf-8")
            assert extracted_from_bytes == f["features"][0]["uri"], "Facet 字节偏移量必须与 URI 严格精确对齐"

    def test_bluesky_dispatcher_publish_with_signature_conversion(self, tmp_path, monkeypatch):
        """条目 4 补充: 验证 BlueskyDispatcher.publish 执行时真实转换签名文本与附带 facets"""
        dispatcher = BlueskyDispatcher(cookies_dir=tmp_path)

        monkeypatch.setattr(
            dispatcher,
            "_get_active_session",
            lambda: (True, "OK", {
                "accessJwt": "fake_token_123",
                "did": "did:plc:testartist123",
                "handle": "testartist.bsky.social"
            })
        )

        captured_requests = []

        def mock_post(url, headers=None, json=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.ok = True
            captured_requests.append({"url": url, "json": json, "headers": headers})
            idx = len(captured_requests)
            resp.json.return_value = {
                "uri": f"at://did:plc:testartist123/app.bsky.feed.post/3kfakerecord{idx}",
                "cid": f"bafyreitestcid{idx}"
            }
            return resp

        monkeypatch.setattr("requests.post", mock_post)
        monkeypatch.setattr(dispatcher, "_assert_post_published", lambda *args, **kwargs: (True, ""))

        # 1. 单帖未超限场景：正文带方括号 + 签名带两个引流链接
        short_sig = (
            "Where to find me:\n\n"
            "🎬X: [@FOXTONTONTON](https://x.com/FOXTONTONTON)\n\n"
            "☕Ko-fi: [FOXTONTONTON](https://ko-fi.com/foxtontonton)"
        )
        payload_single = {
            "title": "Summer Illustration [OC]",
            "description": f"Fresh breeze by coast [OC] [2026]\n\n{short_sig}",
            "tags": [],
            "images": [],
            "is_ai_generated": False,
            "platform_options": {"bluesky_ai_hashtags": False}
        }

        ok, msg = dispatcher.publish(payload_single)
        assert ok is True, f"发布应成功: {msg}"

        create_calls = [c for c in captured_requests if "com.atproto.repo.createRecord" in c["url"]]
        assert len(create_calls) == 1, "未超限时应调用一次 createRecord"

        record = create_calls[0]["json"]["record"]
        record_text = record["text"]

        # 验证正文保留 [OC]，旧签名被预告引流格式取代 (teaser 格式替代签名)
        assert "[OC]" in record_text
        assert "[2026]" in record_text
        assert "Where to find me" not in record_text

        # 验证 facets 包含了预告 DA 引流链接
        post_facets = record.get("facets", [])
        post_links = [f["features"][0]["uri"] for f in post_facets if f["features"][0].get("$type") == "app.bsky.richtext.facet#link"]
        assert "https://www.deviantart.com/foxtontonton" in post_links

        # 2. 超限串推安全网场景
        captured_requests.clear()
        payload_thread = {
            "title": "Full Links Post [OC]",
            "description": f"Full profile [OC] [2026]\n\n{CANONICAL_SIGNATURE_TEMPLATE}",
            "tags": [],
            "images": [],
            "is_ai_generated": False,
            "bluesky_nsfw_notice": "超限提示句。" * 60,
            "platform_options": {
                "bluesky_ai_hashtags": False,
                "bluesky_over_limit_action": "thread"
            }
        }
        ok_thread, msg_thread = dispatcher.publish(payload_thread)
        assert ok_thread is True, f"串推发布应成功: {msg_thread}"

        thread_calls = [c for c in captured_requests if "com.atproto.repo.createRecord" in c["url"]]
        assert len(thread_calls) >= 2, "超限时应切分为多个串推记录"

        all_thread_links = []
        for call in thread_calls:
            rec = call["json"]["record"]
            for f in rec.get("facets", []):
                if f["features"][0].get("$type") == "app.bsky.richtext.facet#link":
                    all_thread_links.append(f["features"][0]["uri"])

        # 验证预告 DA 引流链接在串推中被提取为 link facet
        assert "https://www.deviantart.com/foxtontonton" in all_thread_links
