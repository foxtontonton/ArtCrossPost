import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from PyQt5.QtCore import QRect, QSize, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel, QMessageBox, QPushButton, QWidget

from app import MainWindow, SettingsDialog
from flow_layout import FlowLayout
from ui_icons import ICONS_DIR, create_ui_icon_button, get_icon_path, get_ui_svg_icon


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestUIIconsAndLayout:
    """UI 图标与 DA 选项区布局全量测试集"""

    @pytest.fixture(autouse=True)
    def setup_app(self, qapp):
        self.app = qapp

    def test_svg_asset_files_exist_and_valid(self):
        """测试 1: 确保 assets/icons/ 下的 3 个 SVG 图标文件均存在且内容有效"""
        svg_names = ["refresh.svg", "text_increase.svg", "text_decrease.svg"]
        for name in svg_names:
            file_path = ICONS_DIR / name
            assert file_path.is_file(), f"图标文件不存在: {file_path}"
            content = file_path.read_text(encoding="utf-8")
            assert "<svg" in content and "</svg>" in content, f"图标内容不是有效 SVG: {name}"

    def test_get_ui_svg_icon_known_and_unknown(self):
        """测试 2: 验证已知 SVG 返回非空 QIcon，未知 SVG 返回空 QIcon 且不抛异常"""
        for name in ("refresh", "refresh.svg", "text_increase", "text_decrease"):
            icon = get_ui_svg_icon(name, size=18)
            assert isinstance(icon, QIcon)
            assert not icon.isNull(), f"图标 {name} 加载失败"
            # 验证多尺寸 High-DPI 缩放位图生成
            sizes = icon.availableSizes()
            assert len(sizes) >= 2, f"图标 {name} 未生成多分辨率位图"

        # 未知图标容错
        unknown_icon = get_ui_svg_icon("unknown_icon_xyz", size=18)
        assert isinstance(unknown_icon, QIcon)
        assert unknown_icon.isNull(), "未知图标应返回空 QIcon"

    def test_create_ui_icon_button_properties(self):
        """测试 3: 验证纯图标按钮尺寸约 28×28、图标 18–20px、提示和光标"""
        btn = create_ui_icon_button(
            icon_name="refresh.svg",
            tooltip="刷新相册列表",
            btn_size=28,
            icon_size=18,
            object_name="iconBtn"
        )
        assert btn.width() == 28 and btn.height() == 28
        assert btn.cursor().shape() == Qt.PointingHandCursor
        assert btn.toolTip() == "刷新相册列表"
        assert not btn.icon().isNull()
        assert btn.iconSize() == QSize(18, 18)

    def test_flow_layout_wrapping_and_centering(self):
        """测试 4: 验证 FlowLayout 流式换行与高度随宽度自适应计算"""
        parent = QWidget()
        flow = FlowLayout(parent, margin=0, spacing=8)

        w1 = QPushButton("Button 1")
        w1.setFixedSize(120, 30)
        w2 = QPushButton("Button 2")
        w2.setFixedSize(120, 30)
        w3 = QPushButton("Button 3")
        w3.setFixedSize(120, 30)

        flow.addWidget(w1)
        flow.addWidget(w2)
        flow.addWidget(w3)

        # 宽屏宽度下 (500px): 3 个 120px 按钮可以在同一行排布
        h_wide = flow.heightForWidth(500)
        assert h_wide <= 35, f"宽屏应为单行，实际高度: {h_wide}"

        # 窄屏宽度下 (200px): 每个按钮 120px，无法并排，必须换行为 3 行
        h_narrow = flow.heightForWidth(200)
        assert h_narrow > h_wide, f"窄屏高度应大于宽屏，实际: {h_narrow} vs {h_wide}"
        assert h_narrow >= 90, f"3 行至少应达到 90px，实际: {h_narrow}"

    def test_deviantart_options_controls_and_flow_layout(self):
        """测试 5: 验证 DA 选项面板使用 FlowLayout，各项控件完好且可正常访问"""
        win = MainWindow()
        win.show()
        self.app.processEvents()

        da_ctrl = win.platform_controls.get("deviantart")
        assert da_ctrl is not None, "未找到 deviantart 平台控制组件"
        opts = da_ctrl["options"]

        # 验证控件集合完整性
        assert "censor" in opts and isinstance(opts["censor"], QCheckBox)
        assert "mature" in opts and isinstance(opts["mature"], QComboBox)
        assert "gallery" in opts and isinstance(opts["gallery"], QComboBox)
        assert "refresh_btn" in opts and isinstance(opts["refresh_btn"], QPushButton)
        assert "ai" in opts and isinstance(opts["ai"], QCheckBox)
        assert "keep_preset" in opts and isinstance(opts["keep_preset"], QCheckBox)

        # 验证刷新按钮规格与 Tooltip
        ref_btn = opts["refresh_btn"]
        assert ref_btn.toolTip() == "刷新相册列表"
        assert ref_btn.width() == 28 and ref_btn.height() == 28
        assert not ref_btn.icon().isNull()

    def test_settings_dialog_refresh_button(self):
        """测试 6: 验证设置弹窗 DA 标签页中的刷新相册按钮图标化规格"""
        win = MainWindow()
        dlg = SettingsDialog(win)

        btn = dlg.btn_refresh_da_galleries
        assert btn.toolTip() == "刷新相册列表"
        assert btn.width() == 28 and btn.height() == 28
        assert not btn.icon().isNull()
        assert btn.cursor().shape() == Qt.PointingHandCursor

    def test_font_zoom_buttons_icons_and_step(self):
        """测试 7: 验证界面字号放大/缩小按钮改用 SVG 图标且步进与功能正常"""
        win = MainWindow()
        win.show()
        self.app.processEvents()

        # 缩放按钮尺寸与图标
        assert win.zoom_out_btn.width() == 28 and win.zoom_out_btn.height() == 28
        assert not win.zoom_out_btn.icon().isNull()
        assert win.zoom_out_btn.toolTip() == "缩小界面字号 (-1px)"

        assert win.zoom_in_btn.width() == 28 and win.zoom_in_btn.height() == 28
        assert not win.zoom_in_btn.icon().isNull()
        assert win.zoom_in_btn.toolTip() == "放大界面字号 (+1px)"

        # 步进测试
        initial_font = win.header_font_spin.value()
        win.zoom_in_btn.click()
        assert win.header_font_spin.value() == initial_font + 1

        win.zoom_out_btn.click()
        assert win.header_font_spin.value() == initial_font

    def test_window_default_and_minimum_width_da_visibility(self):
        """测试 8: 需求第 5 项验证：主窗口默认宽度与最小宽度下，DA 面板逐个控件可见可点、无截断"""
        win = MainWindow()
        win.show()
        self.app.processEvents()

        da_ctrl = win.platform_controls["deviantart"]
        opts = da_ctrl["options"]
        keys = ("censor", "mature", "gallery", "refresh_btn", "ai", "keep_preset")

        # 1. 默认宽度 (1260px) 下验证所有控件可见、具有几何尺寸且处于可用状态
        for key in keys:
            widget = opts[key]
            assert widget.isVisible(), f"默认宽度下控件不可见: {key}"
            assert widget.isEnabled(), f"默认宽度下控件不可用: {key}"
            assert widget.width() > 0 and widget.height() > 0, f"默认宽度下控件尺寸为 0: {key}"

        # 2. 最小宽度下 (win.minimumWidth()) 验证所有控件仍然可见、可用、无零尺寸
        min_w = win.minimumWidth()
        min_h = win.minimumHeight()
        win.resize(min_w, min_h)
        self.app.processEvents()

        for key in keys:
            widget = opts[key]
            assert widget.isVisible(), f"最小宽度下控件不可见: {key}"
            assert widget.isEnabled(), f"最小宽度下控件不可用: {key}"
            assert widget.width() > 0 and widget.height() > 0, f"最小宽度下控件尺寸为 0: {key}"

    def test_flow_layout_effective_minimum_width(self):
        """测试 9: 验证 FlowLayout 当 item 的 minimumWidth 大于 sizeHint 时按 minimumWidth 排布并据此换行"""
        parent = QWidget()
        flow = FlowLayout(parent, margin=0, spacing=8)

        class FixedMinWidget(QWidget):
            def __init__(self, min_w, hint_w, h):
                super().__init__()
                self.setMinimumWidth(min_w)
                self._hint = QSize(hint_w, h)

            def sizeHint(self):
                return self._hint

        w1 = FixedMinWidget(min_w=200, hint_w=50, h=30)
        w2 = FixedMinWidget(min_w=200, hint_w=50, h=30)
        flow.addWidget(w1)
        flow.addWidget(w2)

        # 350px 容器下: 200 + 8 + 200 = 408 > 350，必须依据 effective minimumWidth 换行为两行
        h_350 = flow.heightForWidth(350)
        # 500px 容器下: 408 <= 500，可容纳为单行
        h_500 = flow.heightForWidth(500)
        assert h_350 > h_500, f"350px 应换行为多行 (h_350={h_350} vs h_500={h_500})"
        assert h_350 >= 60, f"两行高度至少为 60px，实际: {h_350}"

    def test_deviantart_panel_responsive_wrapping_at_380px(self):
        """测试 10: 验证 DA 面板组装：380px 下相册组换行至独立行、两下拉实际宽度不小于各自最小宽度"""
        win = MainWindow()
        da_ctrl = win.platform_controls["deviantart"]
        opts = da_ctrl["options"]
        mature_combo = opts["mature"]
        gallery_combo = opts["gallery"]

        mature_box = mature_combo.parentWidget()
        gal_box = gallery_combo.parentWidget()
        opts_layout = mature_box.parentWidget().layout().itemAt(1).layout()

        # 验证在 380px 宽度约束下布局执行
        opts_layout.setGeometry(QRect(0, 0, 380, 260))

        # 相册组必须换行至独立行 (Y 坐标大于 Mature 组)
        assert gal_box.y() > mature_box.y(), f"380px 下相册组应换行至独立行，实际: gal_y={gal_box.y()}, mature_y={mature_box.y()}"

        # 两下拉实际宽度不小于各自设定的最小宽度 (Mature >= 180, Gallery >= 170)
        assert mature_combo.width() >= 180, f"Mature 下拉宽度 {mature_combo.width()} < 180"
        assert gallery_combo.width() >= 170, f"相册下拉宽度 {gallery_combo.width()} < 170"

    def test_patreon_and_bluesky_responsive_wrapping_and_flow(self):
        """测试 11: 验证 Patreon、Bluesky 组装：380px 换行、900px 回流一行；超宽容器下拉宽度不超上限"""
        win = MainWindow()

        # 1. Patreon 卡片测试
        pat_ctrl = win.platform_controls["patreon"]
        pat_combo = pat_ctrl["options"]["visibility"]
        pat_box = pat_combo.parentWidget()
        pat_opts_layout = pat_box.parentWidget().layout().itemAt(1).layout()

        assert pat_opts_layout.heightForWidth(380) > pat_opts_layout.heightForWidth(900), "Patreon 在 380px 换行高度应大于 900px"
        pat_opts_layout.setGeometry(QRect(0, 0, 1500, 200))
        assert pat_combo.width() <= 260, f"超宽容器下 Patreon 下拉不应超过上限 260px，实际: {pat_combo.width()}"

        # 2. Bluesky 卡片测试
        bsky_ctrl = win.platform_controls["bluesky"]
        bsky_label_combo = bsky_ctrl["options"]["label"]
        bsky_policy_combo = bsky_ctrl["options"]["policy"]
        bsky_label_box = bsky_label_combo.parentWidget()
        bsky_opts_layout = bsky_label_box.parentWidget().layout().itemAt(1).layout()

        assert bsky_opts_layout.heightForWidth(380) > bsky_opts_layout.heightForWidth(900), "Bluesky 在 380px 换行高度应大于 900px"
        bsky_opts_layout.setGeometry(QRect(0, 0, 1500, 200))
        assert bsky_label_combo.width() <= 220, f"超宽容器下 Bluesky 分级下拉不应超过上限 220px，实际: {bsky_label_combo.width()}"
        assert bsky_policy_combo.width() <= 200, f"超宽容器下 Bluesky 策略下拉不应超过上限 200px，实际: {bsky_policy_combo.width()}"

    def test_left_panel_zone3_responsive_wrapping_and_bounds(self):
        """测试 12: 验证左栏 3 号区组装：340px 下 c_row2 两下拉上下分行且文字完整、w_row4 三组分行；800px 下回流同行且滑杆/下拉不超上限"""
        win = MainWindow()

        # 1. c_row2 (打码样式与模式)
        style_box = win.censor_style_combo.parentWidget()
        mode_box = win.censor_mode_combo.parentWidget()
        c_row2 = style_box.parentWidget().layout().itemAt(2).layout()

        # 340px 下分行
        c_row2.setGeometry(QRect(0, 0, 340, 120))
        assert mode_box.y() > style_box.y(), f"340px 下 c_row2 样式与模式应上下分行，实际: style_y={style_box.y()}, mode_y={mode_box.y()}"
        assert win.censor_style_combo.width() >= 150, "样式下拉宽度不低于 150"
        assert win.censor_mode_combo.width() >= 160, "模式下拉宽度不低于 160"

        # 800px 下回流同行
        c_row2.setGeometry(QRect(0, 0, 800, 120))
        assert mode_box.y() == style_box.y(), f"800px 下 c_row2 应回流同行，实际: style_y={style_box.y()}, mode_y={mode_box.y()}"
        assert win.censor_style_combo.width() <= 220, f"样式下拉不超上限 220，实际: {win.censor_style_combo.width()}"
        assert win.censor_mode_combo.width() <= 220, f"模式下拉不超上限 220，实际: {win.censor_mode_combo.width()}"

        # 2. w_row4 (水印字号、透明、颜色)
        size_box = win.watermark_size_slider.parentWidget()
        op_box = win.watermark_opacity_slider.parentWidget()
        col_box = win.watermark_color_combo.parentWidget()
        w_row4 = size_box.parentWidget().layout().itemAt(3).layout()

        # 340px 下三组分行
        w_row4.setGeometry(QRect(0, 0, 340, 160))
        assert size_box.y() < op_box.y() < col_box.y(), (
            f"340px 下 w_row4 应三组分行，实际: size_y={size_box.y()}, op_y={op_box.y()}, col_y={col_box.y()}"
        )

        # 800px 下回流同行
        w_row4.setGeometry(QRect(0, 0, 800, 160))
        assert abs(size_box.y() - op_box.y()) <= 4 and abs(op_box.y() - col_box.y()) <= 4, (
            f"800px 下 w_row4 应回流同行，实际: size_y={size_box.y()}, op_y={op_box.y()}, col_y={col_box.y()}"
        )
        assert win.watermark_size_slider.width() <= 260, f"字号滑杆不超上限 260，实际: {win.watermark_size_slider.width()}"
        assert win.watermark_opacity_slider.width() <= 260, f"透明滑杆不超上限 260，实际: {win.watermark_opacity_slider.width()}"
        assert win.watermark_color_combo.width() <= 200, f"颜色下拉不超上限 200，实际: {win.watermark_color_combo.width()}"
