import pytest
from PyQt5.QtWidgets import QApplication, QWidget, QComboBox, QSlider, QTextEdit
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QWheelEvent
from app import NoWheelEventFilter, SmartTagEdit, get_theme_stylesheet, THEMES


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_scrollbar_handle_theme_accent():
    """
    P2 验证：
    1. QSS 中 QScrollBar::handle:vertical/horizontal 的背景色为当前主题的 {accent}
    2. hover 态为 {accent_hover}
    3. 主题切换时跟随变化，charcoal（默认）下手柄为醒目绿 (#059669)
    """
    # 默认炭素暗灰主题 (charcoal)
    qss_charcoal = get_theme_stylesheet("charcoal", font_size=13)
    accent_charcoal = THEMES["charcoal"]["accent"] # "#059669"
    hover_charcoal = THEMES["charcoal"]["accent_hover"] # "#10b981"

    assert f"background: {accent_charcoal};" in qss_charcoal
    assert f"background: {hover_charcoal};" in qss_charcoal
    assert "background: #2a2a3a;" not in qss_charcoal

    # 切换至纯净极黑 (pure_dark)
    qss_pure = get_theme_stylesheet("pure_dark", font_size=13)
    accent_pure = THEMES["pure_dark"]["accent"] # "#4f46e5"
    assert f"background: {accent_pure};" in qss_pure

    # 切换至深蓝夜色 (deep_navy)
    qss_navy = get_theme_stylesheet("deep_navy", font_size=13)
    accent_navy = THEMES["deep_navy"]["accent"] # "#2563eb"
    assert f"background: {accent_navy};" in qss_navy


def test_no_wheel_event_filter_explicit_allow(qapp):
    """
    P1 验证：
    1. SmartTagEdit、desc_input、log_drawer 三处（及其中 viewport）显式放行（eventFilter 返回 False）
    2. QComboBox、QSlider 保持拦截防误触（eventFilter 返回 True，且调用 event.ignore()）
    """
    parent_win = QWidget()
    wheel_filter = NoWheelEventFilter(parent_win)

    # 构造三处控件
    smart_tag = SmartTagEdit(parent_win)
    smart_tag.setObjectName("tags_input")
    smart_tag.viewport().setObjectName("tags_input_viewport")

    desc_input = QTextEdit(parent_win)
    desc_input.setObjectName("desc_input")
    desc_input.viewport().setObjectName("desc_input_viewport")

    log_drawer = QTextEdit(parent_win)
    log_drawer.setObjectName("log_drawer")
    log_drawer.viewport().setObjectName("log_drawer_viewport")

    parent_win.tags_input = smart_tag
    parent_win.desc_input = desc_input
    parent_win.log_drawer = log_drawer

    # 构造测试防误触控件
    combo = QComboBox(parent_win)
    slider = QSlider(Qt.Horizontal, parent_win)

    def make_wheel_event():
        return QWheelEvent(
            QPoint(10, 10),
            QPoint(10, 10),
            QPoint(0, 0),
            QPoint(0, 120),
            120,
            Qt.Vertical,
            Qt.NoButton,
            Qt.NoModifier
        )

    # 验证 SmartTagEdit 及其 viewport 显式放行
    ev_tag = make_wheel_event()
    assert wheel_filter.eventFilter(smart_tag, ev_tag) is False
    ev_tag_vp = make_wheel_event()
    assert wheel_filter.eventFilter(smart_tag.viewport(), ev_tag_vp) is False

    # 验证 desc_input 及其 viewport 显式放行
    ev_desc = make_wheel_event()
    assert wheel_filter.eventFilter(desc_input, ev_desc) is False
    ev_desc_vp = make_wheel_event()
    assert wheel_filter.eventFilter(desc_input.viewport(), ev_desc_vp) is False

    # 验证 log_drawer 及其 viewport 显式放行
    ev_log = make_wheel_event()
    assert wheel_filter.eventFilter(log_drawer, ev_log) is False
    ev_log_vp = make_wheel_event()
    assert wheel_filter.eventFilter(log_drawer.viewport(), ev_log_vp) is False

    # 验证 QComboBox / QSlider 拦截防误触
    ev_combo = make_wheel_event()
    assert wheel_filter.eventFilter(combo, ev_combo) is True
    assert not ev_combo.isAccepted() # ignore() 被调用

    ev_slider = make_wheel_event()
    assert wheel_filter.eventFilter(slider, ev_slider) is True
    assert not ev_slider.isAccepted() # ignore() 被调用
