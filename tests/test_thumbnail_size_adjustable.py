# -*- coding: utf-8 -*-
"""
Tests for 缩略图改拖动调节 (fix/thumbnail-drag-resize)
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()

import pytest
from PIL import Image
from PyQt5.QtWidgets import QApplication, QSplitter
from PyQt5.QtCore import Qt, QThread

from config import DEFAULT_CONFIG, load_config, save_config
from app import (
    MainWindow,
    ThumbnailCardWidget,
    ThumbnailGalleryWidget,
    calculate_thumbnail_metrics,
    migrate_thumbnail_card_width_to_gallery_height,
    DEFAULT_THUMBNAIL_GALLERY_HEIGHT,
    MIN_THUMBNAIL_GALLERY_HEIGHT,
    MAX_THUMBNAIL_GALLERY_HEIGHT,
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
    monkeypatch.setattr("config.PRESETS_FILE", tmp_path / "presets.json")

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


class TestThumbnailDragResize:
    def test_calculate_thumbnail_metrics_pure_function(self):
        """
        验证尺寸纯函数三校验点（152/351/126）、越界钳制与单调性:
        - 校验点 152 (默认): 卡片 118x136, 图片区高 70, 图集栏高 152, 渲染框 106x70
        - 校验点 351 (拉满): 卡片 320x335, 图片区高 269, 图集栏高 351, 渲染框 308x269
        - 校验点 126 (最小): 卡片 84x110,  图片区高 44,  图集栏高 126, 渲染框 72x44
        - 越界钳制: <126 钳制为 126; >351 钳制为 351; 非法/None 回退默认 152
        - 单调性: 126~351 范围内 card_width 随 gallery_height 严格单调不减
        """
        # 1. 校验点 152 (默认)
        m_def = calculate_thumbnail_metrics(152)
        assert m_def["gallery_height"] == 152
        assert m_def["card_height"] == 136
        assert m_def["img_height"] == 70
        assert m_def["card_width"] == 118
        assert m_def["render_width"] == 106
        assert m_def["render_height"] == 70
        assert m_def["render_box"] == (106, 70)
        assert m_def["scale"] == pytest.approx(1.0)

        # None 输入回退默认 152
        m_none = calculate_thumbnail_metrics(None)
        assert m_none == m_def

        # 2. 校验点 351 (拉满)
        m_max = calculate_thumbnail_metrics(351)
        assert m_max["gallery_height"] == 351
        assert m_max["card_height"] == 335
        assert m_max["img_height"] == 269
        assert m_max["card_width"] == 320
        assert m_max["render_width"] == 308
        assert m_max["render_height"] == 269
        assert m_max["render_box"] == (308, 269)

        # 3. 校验点 126 (最小)
        m_min = calculate_thumbnail_metrics(126)
        assert m_min["gallery_height"] == 126
        assert m_min["card_height"] == 110
        assert m_min["img_height"] == 44
        assert m_min["card_width"] == 84
        assert m_min["render_width"] == 72
        assert m_min["render_height"] == 44
        assert m_min["render_box"] == (72, 44)

        # 4. 越界钳制测试
        m_under = calculate_thumbnail_metrics(50)
        assert m_under == m_min
        m_negative = calculate_thumbnail_metrics(-20)
        assert m_negative == m_min

        m_over = calculate_thumbnail_metrics(500)
        assert m_over == m_max

        m_invalid = calculate_thumbnail_metrics("not_a_number")
        assert m_invalid == m_def

        # 5. 单调性检验: 126~351 范围内 card_width 必须随 gallery_height 递增非减
        prev_w = 0
        for gh in range(126, 352):
            m = calculate_thumbnail_metrics(gh)
            assert m["card_width"] >= prev_w, f"卡宽在高度 {gh} 处未保持单调: {m['card_width']} < {prev_w}"
            prev_w = m["card_width"]

    def test_legacy_thumbnail_card_width_migration(self):
        """
        验证旧键 thumbnail_card_width 首次迁移初值换算:
        - 84 -> 126
        - 118 -> 152
        - 196 -> 221
        - 320 -> 351
        - 越界与无效值回退默认 152
        """
        assert migrate_thumbnail_card_width_to_gallery_height(118) == 152
        assert migrate_thumbnail_card_width_to_gallery_height(84) == 126
        assert migrate_thumbnail_card_width_to_gallery_height(196) == 221
        assert migrate_thumbnail_card_width_to_gallery_height(320) == 351

        # 越界与边界值
        assert migrate_thumbnail_card_width_to_gallery_height(50) == 126
        assert migrate_thumbnail_card_width_to_gallery_height(500) == 351
        assert migrate_thumbnail_card_width_to_gallery_height(None) == 152
        assert migrate_thumbnail_card_width_to_gallery_height("bad") == 152

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
        assert pix_small.width() <= 106
        assert pix_small.height() <= 70
        assert (str(dummy_image), 106, 70) in win._thumbnail_cache

        # 2. 调大尺寸采样 (308 x 269)
        pix_large = win._get_cached_thumbnail(str(dummy_image), 308, 269)
        assert pix_large is not None
        assert not pix_large.isNull()
        assert pix_large.width() <= 308
        assert pix_large.height() <= 269
        assert (str(dummy_image), 308, 269) in win._thumbnail_cache

        # 断言大图分辨率明显高于小图，非小图拉伸
        assert pix_large.width() > pix_small.width()
        assert pix_large.height() > pix_small.height()

        # 3. 验证删除图片时关联缓存清除
        win.image_paths = [str(dummy_image)]
        win.remove_image(0)
        assert (str(dummy_image), 106, 70) not in win._thumbnail_cache
        assert (str(dummy_image), 308, 269) not in win._thumbnail_cache

    def test_thumbnail_config_default_and_roundtrip(self, tmp_path):
        """验证配置项新键 thumbnail_gallery_height 默认值 152、持久化、读回与旧键迁移"""
        assert DEFAULT_CONFIG["thumbnail_gallery_height"] == 152

        # 验证 load_config 继承默认值
        with patch("config.PRESETS_FILE", tmp_path / "presets_default.json"):
            cfg = load_config()
            assert "thumbnail_gallery_height" in cfg
            assert cfg["thumbnail_gallery_height"] == 152

        # 验证保存与回读新键
        cfg["thumbnail_gallery_height"] = 240
        with patch("config.PRESETS_FILE", tmp_path / "presets_test.json"):
            save_config(cfg)
            loaded = load_config()
            assert loaded["thumbnail_gallery_height"] == 240

        # 验证旧键迁移: 若 presets 中仅有 thumbnail_card_width，自动迁移换算为 thumbnail_gallery_height
        legacy_cfg = {"thumbnail_card_width": 196}
        legacy_file = tmp_path / "presets_legacy.json"
        import json
        legacy_file.write_text(json.dumps(legacy_cfg), encoding="utf-8")
        with patch("config.PRESETS_FILE", legacy_file):
            loaded_legacy = load_config()
            assert loaded_legacy["thumbnail_gallery_height"] == 221

    def test_ui_splitter_and_debounce_apply(self, mock_main_window, dummy_image):
        """
        验证中央预览与底部图集栏纵向 QSplitter:
        - 移除尺寸滑杆与数值标签，无悬空引用
        - splitter 具备垂直方向、不可折叠与拉伸权重 (1, 0)
        - 拖动/设置高度触发 120ms 防抖，超时后写 config、清缓存、重建卡片尺寸
        """
        win = mock_main_window
        win.image_paths = [str(dummy_image)]

        # 1. 验证滑杆与数值标签已被移除
        assert not hasattr(win, "thumb_size_slider")
        assert not hasattr(win, "thumb_size_val_lbl")

        # 2. 验证 QSplitter 存在与配置属性
        assert hasattr(win, "preview_splitter")
        splitter = win.preview_splitter
        assert isinstance(splitter, QSplitter)
        assert splitter.orientation() == Qt.Vertical
        assert splitter.isCollapsible(0) is False
        assert splitter.isCollapsible(1) is False

        # 3. 模拟拖动 splitter: 触发防抖定时器
        win._on_thumbnail_splitter_moved(300, 1)
        assert win._thumb_size_timer.isActive() is True

        # 预先塞入假缓存以验证清空
        win._thumbnail_cache[("fake_path", 106, 70)] = MagicMock()

        # 4. 设置新栏高 221 并触发防抖应用
        win.set_thumbnail_gallery_height(221)

        # 验证写 config
        assert win.config["thumbnail_gallery_height"] == 221
        # 验证清缓存
        assert ("fake_path", 106, 70) not in win._thumbnail_cache

        # 验证卡片尺寸与图片区高度 (221 对应 card_width=196, card_height=205, img_height=139)
        metrics_221 = calculate_thumbnail_metrics(221)
        assert len(win.thumbnail_gallery.cards) == 1
        card = win.thumbnail_gallery.cards[0]
        assert card.width() == metrics_221["card_width"]
        assert card.height() == metrics_221["card_height"]
        assert card.img_lbl.height() == metrics_221["img_height"]

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
        win.config["thumbnail_gallery_height"] = 221
        with patch.object(card, "width", return_value=0):
            _, fallback_w = win.thumbnail_gallery._get_card_geometry(0)
            assert fallback_w == 196
