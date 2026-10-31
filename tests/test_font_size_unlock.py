# -*- coding: utf-8 -*-
"""
ArtCrossPost - 全应用写死字号解锁与角色分级规则测试 (fix/font-size-unlock)

覆盖验证:
1. get_theme_stylesheet 动态三档角色规则生成:
   - *[fontRole="title"]  -> fs + 2
   - *[fontRole="small"]  -> max(11, fs - 1)
   - *[fontRole="xsmall"] -> max(9, fs - 3)
   - 验证 fs=13 (默认), fs=18 (大字号), fs=10 (下限截断) 下的规则数值
2. 全局硬编码字号清除复核:
   - 自动扫描仓库所有 .py 文件，断言除两处 B 类保留（HTML 导出报告 body 与 QDateTimeEdit 日历格）外，
     不存在任何写死像素的 font-size
3. 图集总数标签 (self.gallery_status_lbl) 特性:
   - wordWrap 为 True
   - 布局 stretch 为 1
   - 标记 fontRole="small"
   - 自带 styleSheet 不含写死 font-size
4. 缩略图项目 (ThumbnailCardWidget / ThumbnailItem) 徽章与按钮:
   - order_lbl, left_btn, right_btn, del_btn, img_lbl, x_cb, video_badge_lbl 标注 fontRole="xsmall"
   - 样式表不含写死 font-size，大字号下不会撑爆
5. 对话框控件角色标注:
   - AutoGroupReviewDialog, PublishHistoryDialog, ScheduledPublishDialog 标题与正文样式
6. 动态热切换 18px 模拟验收:
   - apply_app_appearance 在 13px 与 18px 之间切换时，各角色的像素尺寸实时按公式跟随
"""
import os
import re
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch
from PyQt5.QtWidgets import (
    QApplication, QLabel, QPushButton, QCheckBox, QHBoxLayout,
    QDateTimeEdit, QDialog
)
from PyQt5.QtCore import Qt

from app import (
    get_theme_stylesheet,
    apply_app_appearance,
    MainWindow,
    ThumbnailCardWidget,
    UnifiedPreviewCanvas,
    SettingsDialog,
)
from auto_group_dialog import AutoGroupReviewDialog
from history_dialog import PublishHistoryDialog
from schedule_dialogs import ScheduledPublishDialog, ExpiredTasksDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestFontSizeUnlock:
    """全应用字号角色规则与写死解锁单测集"""

    @pytest.fixture(autouse=True)
    def setup_app(self, qapp):
        self.app = qapp

    def test_get_theme_stylesheet_font_roles(self):
        """测试 get_theme_stylesheet 中三档角色的动态计算规则"""
        # 1. 默认字号 13px
        qss_13 = get_theme_stylesheet("pure_dark", 13)
        assert '*[fontRole="title"]' in qss_13
        assert '*[fontRole="small"]' in qss_13
        assert '*[fontRole="xsmall"]' in qss_13

        # fs=13: title=15, small=max(11, 12)=12, xsmall=max(9, 10)=10
        assert re.search(r'\*\[fontRole="title"\]\s*\{\s*font-size:\s*15px;', qss_13)
        assert re.search(r'\*\[fontRole="small"\]\s*\{\s*font-size:\s*12px;', qss_13)
        assert re.search(r'\*\[fontRole="xsmall"\]\s*\{\s*font-size:\s*10px;', qss_13)

        # 2. 界面放大至 18px (验收要求场景)
        qss_18 = get_theme_stylesheet("pure_dark", 18)
        # fs=18: title=20, small=max(11, 17)=17, xsmall=max(9, 15)=15
        assert re.search(r'\*\[fontRole="title"\]\s*\{\s*font-size:\s*20px;', qss_18)
        assert re.search(r'\*\[fontRole="small"\]\s*\{\s*font-size:\s*17px;', qss_18)
        assert re.search(r'\*\[fontRole="xsmall"\]\s*\{\s*font-size:\s*15px;', qss_18)

        # 3. 极小字号 10px (下限截断保护)
        qss_10 = get_theme_stylesheet("pure_dark", 10)
        # fs=10: title=12, small=max(11, 9)=11, xsmall=max(9, 7)=9
        assert re.search(r'\*\[fontRole="title"\]\s*\{\s*font-size:\s*12px;', qss_10)
        assert re.search(r'\*\[fontRole="small"\]\s*\{\s*font-size:\s*11px;', qss_10)
        assert re.search(r'\*\[fontRole="xsmall"\]\s*\{\s*font-size:\s*9px;', qss_10)

    def test_repo_wide_font_size_grep_audit(self):
        """复核全仓所有 .py 文件，断言仅允许保留的两处 B 类纯控件/文档样式"""
        repo_root = Path(__file__).resolve().parent.parent
        py_files = list(repo_root.glob("*.py")) + list((repo_root / "dispatchers").glob("*.py"))
        
        matches = []
        for pf in py_files:
            rel = pf.relative_to(repo_root).as_posix()
            with open(pf, "r", encoding="utf-8", errors="ignore") as f:
                for line_no, line in enumerate(f, 1):
                    # 匹配硬编码数字字号（排除 {fs} 等动态插值）
                    if re.search(r'font-size:\s*[0-9]+', line):
                        matches.append((rel, line_no, line.strip()))

        # 严格断言：只许存在 B 类保留清单中的两处
        # 1. app.py 诊断报告 HTML 导出页面 body 样式
        # 2. schedule_dialogs.py QDateTimeEdit 日历控件样式
        allowed_b_class = [
            ("app.py", "html_lines = [\"<body style='font-family:sans-serif; color:#e2e8f0; font-size:13px;'>\"]"),
            ("schedule_dialogs.py", "font-size: 13px;"),
        ]

        for rel, line_no, text in matches:
            is_allowed = any(
                allowed_rel == rel and allowed_frag in text
                for allowed_rel, allowed_frag in allowed_b_class
            )
            assert is_allowed, f"Found unhandled hardcoded font-size in {rel}:{line_no}: {text}"

        assert len(matches) == 2, f"Expected exactly 2 B-class matches, found {len(matches)}: {matches}"

    def test_gallery_status_label_properties_and_layout(self):
        """验证图集总数标签的 wordWrap、stretch=1 及 fontRole 属性"""
        mw = MainWindow()
        lbl = mw.gallery_status_lbl

        # 1. 验证 fontRole="small"
        assert lbl.property("fontRole") == "small"

        # 2. 验证 wordWrap 为 True
        assert lbl.wordWrap() is True

        # 3. 验证自带 stylesheet 不含硬编码字号
        assert "font-size" not in lbl.styleSheet()

        # 4. 验证在横排布局中的 stretch 设置为 1
        found_stretch = None
        for l in mw.findChildren(QHBoxLayout):
            idx = l.indexOf(lbl)
            if idx >= 0:
                found_stretch = l.stretch(idx)
                break

        assert found_stretch == 1, f"Expected gallery_status_lbl stretch=1 in layout, got {found_stretch}"

    def test_thumbnail_card_widget_font_roles(self, tmp_path):
        """验证缩略图组件 (ThumbnailCardWidget) 各子控件 fontRole="xsmall" 且无硬编码字号"""
        dummy_img = tmp_path / "sample.jpg"
        dummy_img.write_bytes(b"dummy")

        mw = MainWindow()
        card = ThumbnailCardWidget(mw, 0, str(dummy_img))

        # 序号标签
        order_lbls = [w for w in card.findChildren(QLabel) if w.text().startswith("#")]
        assert len(order_lbls) == 1
        assert order_lbls[0].property("fontRole") == "xsmall"
        assert "font-size" not in order_lbls[0].styleSheet()

        # 左右及删除按钮
        btns = [w for w in card.findChildren(QPushButton) if w.text() in ("◀", "▶", "✕")]
        assert len(btns) == 3
        for b in btns:
            assert b.property("fontRole") == "xsmall"
            assert "font-size" not in b.styleSheet()

        # X 选用复选框
        assert card.x_cb.property("fontRole") == "xsmall"
        assert "font-size" not in card.x_cb.styleSheet()

        # 视频徽标
        assert "font-size" not in card.video_badge_lbl.styleSheet()

    def test_dialogs_font_roles(self, tmp_path):
        """验证对话框组件中角色属性及无硬编码字号"""
        mw = MainWindow()

        # AutoGroupReviewDialog
        ag_dlg = AutoGroupReviewDialog({}, tmp_path, mw)
        title_lbl = [w for w in ag_dlg.findChildren(QLabel) if "文件夹导入素材" in w.text()][0]
        assert title_lbl.property("fontRole") == "title"
        assert "font-size" not in title_lbl.styleSheet()
        assert "font-size" not in ag_dlg.summary_lbl.styleSheet()

        # PublishHistoryDialog
        mock_hm = MagicMock()
        mock_hm.get_all_records.return_value = []
        h_dlg = PublishHistoryDialog(mock_hm, mw)
        h_title = [w for w in h_dlg.findChildren(QLabel) if "发布历史记录" in w.text()][0]
        assert h_title.property("fontRole") == "title"
        assert "font-size" not in h_title.styleSheet()
        assert "font-size" not in h_dlg.count_lbl.styleSheet()

        # ScheduledPublishDialog
        mock_sm = MagicMock()
        mock_sm.get_all_tasks.return_value = []
        s_dlg = ScheduledPublishDialog(mw, mock_sm)
        s_title = [w for w in s_dlg.findChildren(QLabel) if "新建定时任务" in w.text()][0]
        assert s_title.property("fontRole") == "title"
        assert "font-size" not in s_title.styleSheet()

    def test_live_font_size_18_appearance_switch(self):
        """验收模拟: 界面字号 18 下，字号加减时文字实时跟随且徽章保持相对较小"""
        apply_app_appearance(self.app, "pure_dark", 18)
        
        lbl_normal = QLabel("正文文本")
        lbl_title = QLabel("大标题")
        lbl_title.setProperty("fontRole", "title")
        lbl_small = QLabel("次级说明")
        lbl_small.setProperty("fontRole", "small")
        lbl_xsmall = QLabel("徽章")
        lbl_xsmall.setProperty("fontRole", "xsmall")

        for w in (lbl_normal, lbl_title, lbl_small, lbl_xsmall):
            w.show()

        assert lbl_normal.font().pixelSize() == 18
        assert lbl_title.font().pixelSize() == 20
        assert lbl_small.font().pixelSize() == 17
        assert lbl_xsmall.font().pixelSize() == 15

        # 调回默认 13
        apply_app_appearance(self.app, "pure_dark", 13)
        assert lbl_normal.font().pixelSize() == 13
        assert lbl_title.font().pixelSize() == 15
        assert lbl_small.font().pixelSize() == 12
        assert lbl_xsmall.font().pixelSize() == 10
