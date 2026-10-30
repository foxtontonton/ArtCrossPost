# -*- coding: utf-8 -*-
"""
探针测试：检测左栏与右栏内容在字号 13 与 18 下的横向溢出情况，
定位最小宽度膨胀与导致 horizontalScrollBar().maximum() > 0 的元凶控件链。
"""

import sys
import pytest
from unittest.mock import patch, MagicMock
from PyQt5.QtWidgets import QApplication, QWidget, QLayout
from PyQt5.QtCore import Qt


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def get_widest_chain(widget, depth=0):
    """
    逐层下钻查找产生最大 minimumSizeHint 宽度的子控件链。
    返回: (max_width, [(depth, widget_or_layout_name, min_w, hint_w, actual_w), ...])
    """
    min_w = widget.minimumSizeHint().width()
    actual_w = widget.width()
    name = widget.objectName() or widget.__class__.__name__
    chain = [(depth, f"{name} ({widget.__class__.__name__})", min_w, widget.sizeHint().width(), actual_w)]

    layout = widget.layout()
    if not layout:
        return min_w, chain

    max_sub_w = 0
    best_sub_chain = []

    for i in range(layout.count()):
        item = layout.itemAt(i)
        sub_w = item.widget()
        sub_l = item.layout()
        if sub_w:
            w_val, sub_chain = get_widest_chain(sub_w, depth + 1)
            if w_val > max_sub_w:
                max_sub_w = w_val
                best_sub_chain = sub_chain
        elif sub_l:
            l_name = sub_l.__class__.__name__
            l_min_w = sub_l.minimumSize().width()
            l_chain_item = (depth + 1, f"[Layout] {l_name}", l_min_w, sub_l.sizeHint().width(), 0)
            sub_max_w = 0
            sub_sub_chain = []
            for j in range(sub_l.count()):
                sub_sub_item = sub_l.itemAt(j)
                if sub_sub_item.widget():
                    w_val, s_chain = get_widest_chain(sub_sub_item.widget(), depth + 2)
                    if w_val > sub_max_w:
                        sub_max_w = w_val
                        sub_sub_chain = s_chain
            if sub_max_w > max_sub_w or l_min_w > max_sub_w:
                max_sub_w = max(sub_max_w, l_min_w)
                best_sub_chain = [l_chain_item] + sub_sub_chain

    return max(min_w, max_sub_w), chain + best_sub_chain


def format_chain_report(chain):
    lines = []
    for depth, name, min_w, hint_w, actual_w in chain:
        indent = "  " * depth
        lines.append(f"{indent}-> {name}: minHint_w={min_w}, hint_w={hint_w}, actual_w={actual_w}")
    return "\n".join(lines)


def dump_left_column_diagnostics(left_scroll, left_content):
    report_lines = []
    report_lines.append(f"Left Scroll Area width={left_scroll.width()}, viewport={left_scroll.viewport().width()}")
    report_lines.append(f"Left Content width={left_content.width()}, minHint_w={left_content.minimumSizeHint().width()}")
    report_lines.append(f"Left ScrollBar: max={left_scroll.horizontalScrollBar().maximum()}, val={left_scroll.horizontalScrollBar().value()}")

    layout = left_content.layout()
    if layout:
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget()
            if w:
                w_name = w.objectName() or getattr(w, "title", lambda: w.__class__.__name__)()
                max_w, chain = get_widest_chain(w, depth=1)
                report_lines.append(f"\n[顶层板块 {i+1}] {w_name}: minHint_w={w.minimumSizeHint().width()}, sub_max_w={max_w}")
                report_lines.append(format_chain_report(chain))
    return "\n".join(report_lines)


def dump_right_column_diagnostics(right_scroll, right_content):
    report_lines = []
    report_lines.append(f"Right Scroll Area width={right_scroll.width()}, viewport={right_scroll.viewport().width()}")
    report_lines.append(f"Right Content width={right_content.width()}, minHint_w={right_content.minimumSizeHint().width()}")
    report_lines.append(f"Right ScrollBar: max={right_scroll.horizontalScrollBar().maximum()}, val={right_scroll.horizontalScrollBar().value()}")

    layout = right_content.layout()
    if layout:
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget()
            if w:
                w_name = w.objectName() or getattr(w, "title", lambda: w.__class__.__name__)()
                max_w, chain = get_widest_chain(w, depth=1)
                report_lines.append(f"\n[右栏板块 {i+1}] {w_name}: minHint_w={w.minimumSizeHint().width()}, sub_max_w={max_w}")
                report_lines.append(format_chain_report(chain))
    return "\n".join(report_lines)


@pytest.mark.parametrize("font_size", [13, 18])
def test_probe_column_overflow(qapp, tmp_path, monkeypatch, font_size):
    """
    第一步探针测试：
    离屏构建主窗口，字号分别设 13 与 18，断言：
    left_content.width() <= left_scroll.viewport().width() 且
    left_scroll.horizontalScrollBar().maximum() == 0（右栏同断言）
    断言失败时输出左栏与右栏每个顶层板块的 minimumSizeHint 宽度与最宽子控件链。
    """
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    monkeypatch.setattr("config.save_config", lambda cfg: None)
    monkeypatch.setattr("app.save_config", lambda cfg: None)

    import app
    from censor_engine import CensorEngine

    app.apply_app_appearance(qapp, font_size=font_size)

    try:
        with patch.object(CensorEngine, "_init_detector", lambda self: None), patch("app.WatermarkEngine"):
            win = app.MainWindow()
            win.data_dir = tmp_path
            win.notify = MagicMock()
            win.resize(1280, 800)
            win.show()
            qapp.processEvents()

            left_scroll = win.main_splitter.widget(0)
            left_content = left_scroll.widget()
            right_scroll = win.main_splitter.widget(2)
            right_content = right_scroll.widget()

            left_diag = dump_left_column_diagnostics(left_scroll, left_content)
            right_diag = dump_right_column_diagnostics(right_scroll, right_content)

            # 检查左栏
            left_viewport_w = left_scroll.viewport().width()
            left_actual_w = left_content.width()
            left_h_max = left_scroll.horizontalScrollBar().maximum()

            # 检查右栏
            right_viewport_w = right_scroll.viewport().width()
            right_actual_w = right_content.width()
            right_h_max = right_scroll.horizontalScrollBar().maximum()

            failure_msgs = []

            if left_actual_w > left_viewport_w or left_h_max != 0:
                failure_msgs.append(
                    f"[左栏溢出 (font_size={font_size})]\n"
                    f"left_content.width({left_actual_w}) > viewport({left_viewport_w}) 或 hbar.maximum({left_h_max}) != 0\n"
                    f"诊断链:\n{left_diag}\n"
                )

            if right_actual_w > right_viewport_w or right_h_max != 0:
                failure_msgs.append(
                    f"[右栏溢出 (font_size={font_size})]\n"
                    f"right_content.width({right_actual_w}) > viewport({right_viewport_w}) 或 hbar.maximum({right_h_max}) != 0\n"
                    f"诊断链:\n{right_diag}\n"
                )

            win.close()

            if failure_msgs:
                pytest.fail("\n" + "=" * 80 + "\n" + "\n".join(failure_msgs) + "\n" + "=" * 80)
    finally:
        app.apply_app_appearance(qapp, font_size=13)


def test_user_scenario_font18_text_wrapping(qapp, tmp_path, monkeypatch):
    """
    验收测试：字号 18、窗口宽 1280 复现用户真实场景。
    验证：
    1. 描述框中赞助引流签名（含 DeviantArt 行长网址）在框内自动换行完整可见
    2. 通用标签长行在框内自动换行完整可见
    3. 程序化断言：
       desc_input.document().textWidth() <= desc_input.viewport().width()
       tags_input.document().textWidth() <= tags_input.viewport().width()
    4. 左右滚动区横向最大滚动量恒为 0（无溢出被遮挡）
    """
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    monkeypatch.setattr("config.save_config", lambda cfg: None)
    monkeypatch.setattr("app.save_config", lambda cfg: None)

    import app
    from censor_engine import CensorEngine
    from signature_utils import CANONICAL_SIGNATURE_TEMPLATE

    app.apply_app_appearance(qapp, font_size=18)

    try:
        with patch.object(CensorEngine, "_init_detector", lambda self: None), patch("app.WatermarkEngine"):
            win = app.MainWindow()
            win.data_dir = tmp_path
            win.notify = MagicMock()
            win.resize(1280, 800)

            # 填入包含 DeviantArt 等 6 个超链接的规范签名以及长标签
            win.desc_input.setPlainText(CANONICAL_SIGNATURE_TEMPLATE)
            long_tags = "original, cute fox girl, highly detailed anime illustration, masterpiece, extremely long tag that wraps automatically within viewport"
            win.tags_input.setPlainText(long_tags)

            win.show()
            qapp.processEvents()

            left_scroll = win.main_splitter.widget(0)
            left_content = left_scroll.widget()
            right_scroll = win.main_splitter.widget(2)
            right_content = right_scroll.widget()

            # 1. 验证滚动区无横向溢出
            assert left_content.width() <= left_scroll.viewport().width(), (
                f"左栏内容宽度({left_content.width()})不得超过滚动区视口宽度({left_scroll.viewport().width()})"
            )
            assert left_scroll.horizontalScrollBar().maximum() == 0, (
                f"左栏横向滚动条 maximum 应为 0，实际为: {left_scroll.horizontalScrollBar().maximum()}"
            )
            assert right_content.width() <= right_scroll.viewport().width(), (
                f"右栏内容宽度({right_content.width()})不得超过滚动区视口宽度({right_scroll.viewport().width()})"
            )
            assert right_scroll.horizontalScrollBar().maximum() == 0, (
                f"右栏横向滚动条 maximum 应为 0，实际为: {right_scroll.horizontalScrollBar().maximum()}"
            )

            # 2. 验证正文描述输入框换行与视口宽度断言
            desc_vp_w = win.desc_input.viewport().width()
            desc_doc_w = win.desc_input.document().textWidth()
            assert desc_doc_w <= desc_vp_w, (
                f"描述框文本宽度 textWidth({desc_doc_w}) 不得大于视口宽度 viewport({desc_vp_w})"
            )

            # 验证 DeviantArt 链接行文本在框内正常换行并完整可见
            doc = win.desc_input.document()
            assert "deviantart" in doc.toPlainText().lower()
            # 逐 block 检查 layout 行宽均不得超出视口宽度
            b = doc.begin()
            while b.isValid():
                layout = b.layout()
                if layout is not None:
                    for line_idx in range(layout.lineCount()):
                        line = layout.lineAt(line_idx)
                        assert line.naturalTextWidth() <= desc_vp_w + 1.0, (
                            f"描述框第 {line_idx} 行文字宽度({line.naturalTextWidth()})超出了视口宽度({desc_vp_w})"
                        )
                b = b.next()

            # 3. 验证通用标签输入框换行与视口宽度断言
            tags_vp_w = win.tags_input.viewport().width()
            tags_doc_w = win.tags_input.document().textWidth()
            assert tags_doc_w <= tags_vp_w, (
                f"标签框文本宽度 textWidth({tags_doc_w}) 不得大于视口宽度 viewport({tags_vp_w})"
            )

            win.close()
    finally:
        app.apply_app_appearance(qapp, font_size=13)


