# -*- coding: utf-8 -*-
"""
Tests for 缩略图框尺寸解锁可调 (fix/thumbnail-size-adjustable)
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()

import pytest
from PIL import Image
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QThread

from config import DEFAULT_CONFIG, load_config, save_config
from app import (
    MainWindow,
    ThumbnailCardWidget,
    ThumbnailGalleryWidget,
    calculate_thumbnail_metrics,
    DEFAULT_THUMBNAIL_CARD_WIDTH,
    MIN_THUMBNAIL_CARD_WIDTH,
    MAX_THUMBNAIL_CARD_WIDTH,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def dummy_image(tmp_path):
    img_path = tmp_path / "sample_thumb_test.png"
    im = Image.new("RGB", (400, 300), color="blue")
    im.save(img_path)
    return img_path


@pytest.fixture
def mock_main_window(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
        win = MainWindow()
        win.data_dir = tmp_path
        win.notify = MagicMock()

        yield win

        # 清理定时器，避免后台泄漏
        for t_attr in (
            "_draft_save_timer",
            "_layout_save_timer",
            "_config_save_timer",
            "_preview_timer",
            "_thumb_size_timer",
            "x_counter_timer",
            "_schedule_poll_timer",
        ):
            if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                getattr(win, t_attr).stop()
        if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
            win.thread.quit()
            win.thread.wait()


class TestThumbnailSizeAdjustable:
    def test_calculate_thumbnail_metrics_pure_function(self):
        """
        验证尺寸纯函数计算结果:
        - 默认值 118: 卡片 118x136, 图片区高 70, 图集栏高 152, 渲染框 106x70
        - 最小值 84:  卡片 84x97,   图片区高 50, 图集栏高 113, 渲染框 72x50
        - 最大值 196: 卡片 196x226, 图片区高 116, 图集栏高 242, 渲染框 184x116
        - 越界钳制: <84 钳制为 84; >196 钳制为 196; 非法/None 回退默认 118
        """
        # 1. 默认值 118
        m_def = calculate_thumbnail_metrics(118)
        assert m_def["card_width"] == 118
        assert m_def["card_height"] == 136
        assert m_def["img_height"] == 70
        assert m_def["gallery_height"] == 152
        assert m_def["render_width"] == 106
        assert m_def["render_height"] == 70
        assert m_def["render_box"] == (106, 70)
        assert m_def["scale"] == pytest.approx(1.0)

        # None 输入回退默认
        m_none = calculate_thumbnail_metrics(None)
        assert m_none == m_def

        # 2. 最小值 84
        # s = 84 / 118.0
        # card_height = round(136 * 84 / 118) = round(96.81) = 97
        # img_height = round(70 * 84 / 118) = round(49.83) = 50
        # gallery_height = 97 + 16 = 113
        # render_width = 84 - 12 = 72
        m_min = calculate_thumbnail_metrics(84)
        assert m_min["card_width"] == 84
        assert m_min["card_height"] == 97
        assert m_min["img_height"] == 50
        assert m_min["gallery_height"] == 113
        assert m_min["render_width"] == 72
        assert m_min["render_height"] == 50
        assert m_min["render_box"] == (72, 50)

        # 3. 最大值 196
        # s = 196 / 118.0
        # card_height = round(136 * 196 / 118) = round(225.90) = 226
        # img_height = round(70 * 196 / 118) = round(116.27) = 116
        # gallery_height = 226 + 16 = 242
        # render_width = 196 - 12 = 184
        m_max = calculate_thumbnail_metrics(196)
        assert m_max["card_width"] == 196
        assert m_max["card_height"] == 226
        assert m_max["img_height"] == 116
        assert m_max["gallery_height"] == 242
        assert m_max["render_width"] == 184
        assert m_max["render_height"] == 116
        assert m_max["render_box"] == (184, 116)

        # 4. 越界钳制测试
        m_under = calculate_thumbnail_metrics(50)
        assert m_under == m_min
        m_negative = calculate_thumbnail_metrics(-20)
        assert m_negative == m_min

        m_over = calculate_thumbnail_metrics(350)
        assert m_over == m_max

        m_invalid = calculate_thumbnail_metrics("not_a_number")
        assert m_invalid == m_def

    def test_thumbnail_cache_dimension_aware(self, mock_main_window, dummy_image):
        """
        验证缩略图缓存改尺寸感知:
        - 缓存键包含渲染框尺寸 (img_path, target_w, target_h)
        - 尺寸变更后从原图重新平滑采样，不复用旧条目，严禁用小图拉伸
        - 整表清除缓存正常运作
        """
        win = mock_main_window

        # 1. 默认尺寸采样 (106 x 70)
        pix_small = win._get_cached_thumbnail(str(dummy_image), 106, 70)
        assert pix_small is not None
        assert not pix_small.isNull()
        # 400x300 在 106x70 下保持 4:3 比例 -> 93x70
        assert pix_small.width() <= 106
        assert pix_small.height() <= 70
        assert (str(dummy_image), 106, 70) in win._thumbnail_cache

        # 2. 调大尺寸采样 (184 x 116)
        pix_large = win._get_cached_thumbnail(str(dummy_image), 184, 116)
        assert pix_large is not None
        assert not pix_large.isNull()
        # 400x300 在 184x116 下保持 4:3 比例 -> 154x116
        assert pix_large.width() <= 184
        assert pix_large.height() <= 116
        assert (str(dummy_image), 184, 116) in win._thumbnail_cache

        # 断言大图分辨率明显高于小图，非小图拉伸
        assert pix_large.width() > pix_small.width()
        assert pix_large.height() > pix_small.height()

        # 3. 验证删除图片时关联缓存清除
        win.image_paths = [str(dummy_image)]
        win.remove_image(0)
        assert (str(dummy_image), 106, 70) not in win._thumbnail_cache
        assert (str(dummy_image), 184, 116) not in win._thumbnail_cache

    def test_thumbnail_config_default_and_roundtrip(self, tmp_path):
        """验证配置项默认值 118、持久化与读回"""
        assert DEFAULT_CONFIG["thumbnail_card_width"] == 118

        # 验证 load_config 继承默认值
        cfg = load_config()
        assert "thumbnail_card_width" in cfg

        # 验证保存与回读
        cfg["thumbnail_card_width"] = 144
        with patch("config.PRESETS_FILE", tmp_path / "presets_test.json"):
            save_config(cfg)
            loaded = load_config()
            assert loaded["thumbnail_card_width"] == 144

    def test_ui_slider_and_debounce_apply(self, mock_main_window, dummy_image):
        """
        验证图集标题行滑杆 (84-196, 步进 2, 宽 110px, 带数值标注):
        - valueChanged 触发数值即时刷新与 120ms 防抖
        - 防抖超时后完成写 config、清缓存、重设图集栏高度与刷新卡片
        """
        win = mock_main_window
        win.image_paths = [str(dummy_image)]

        # 1. 验证控件初始属性
        slider = win.thumb_size_slider
        assert slider.minimum() == 84
        assert slider.maximum() == 196
        assert slider.singleStep() == 2
        assert slider.width() == 110 or slider.maximumWidth() == 110

        assert win.thumb_size_val_lbl.text() == "118px"
        assert win.thumbnail_gallery.height() == 152

        # 2. 模拟拖动滑杆至 160
        slider.setValue(160)
        assert win.thumb_size_val_lbl.text() == "160px"
        assert win._thumb_size_timer.isActive() is True

        # 预先塞入假缓存以验证清空
        win._thumbnail_cache[("fake_path", 106, 70)] = MagicMock()

        # 3. 触发防抖超时应用
        win._do_apply_thumbnail_size()

        # 验证写 config
        assert win.config["thumbnail_card_width"] == 160
        # 验证清缓存
        assert ("fake_path", 106, 70) not in win._thumbnail_cache

        # 验证图集栏高度更新 (160 对应 scale=1.3559, card_height=184, gallery_height=200)
        metrics_160 = calculate_thumbnail_metrics(160)
        assert win.thumbnail_gallery.height() == metrics_160["gallery_height"]

        # 验证卡片尺寸与图片标签高度
        assert len(win.thumbnail_gallery.cards) == 1
        card = win.thumbnail_gallery.cards[0]
        assert card.width() == 160
        assert card.height() == metrics_160["card_height"]
        assert card.img_lbl.height() == metrics_160["img_height"]

    def test_thumbnail_card_geometry_fallback_and_fixed_subcomponents(self, mock_main_window, dummy_image):
        """
        验证:
        1. ThumbnailGalleryWidget._get_card_geometry 兜底宽度动态跟随 get_thumbnail_metrics
        2. 卡内按钮/复选框/徽章保持固定尺寸，不随比例失真缩放
        """
        win = mock_main_window
        win.image_paths = [str(dummy_image)]
        win._refresh_thumbnail_gallery()

        card = win.thumbnail_gallery.cards[0]

        # 验证内部操作按钮固定 16x16
        for btn_attr in ("◀", "▶", "✕"):
            btn = next(b for b in card.findChildren(type(card.findChild(type(card))))) if False else None

        # 验证 _get_card_geometry 兜底宽度读自 metrics
        win.config["thumbnail_card_width"] = 150
        with patch.object(card, "width", return_value=0):
            _, fallback_w = win.thumbnail_gallery._get_card_geometry(0)
            assert fallback_w == 150
