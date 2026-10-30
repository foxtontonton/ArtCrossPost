# -*- coding: utf-8 -*-
"""
Tests for censor params redesign (fix/censor-params-redesign):
1. 引擎单测:
   - mosaic_size 8 vs 40 输出颗粒度可测不同
   - opacity=0.5 等权混合数值断言 (对纯色底图打纯黑条，像素值精确折半)
   - blur_ksize 参数生效 (小核 vs 大核高斯滤波扩散范围不同)
   - GIF 路径透传与透明度逐帧混合
2. 接线与 UI 单测:
   - 黑条禁用强度拉杆并设置对应 tooltip，马赛克与高斯模糊启用强度拉杆
   - 强度 (strength) 与透明度 (opacity) 在 config 中回读与同步持久化
   - GIF 合成链与发布链透传 mosaic_size, blur_ksize, opacity
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()

import cv2
import numpy as np
import pytest
from PIL import Image
from PyQt5.QtWidgets import QApplication

from censor_engine import (
    CensorEngine,
    map_strength_to_mosaic_size,
    map_strength_to_blur_ksize,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def censor_engine():
    with patch.object(CensorEngine, "_init_detector", lambda self: None):
        ce = CensorEngine()
        ce.detector = None
        return ce


class TestCensorParamsEngine:
    def test_mapping_formulas(self):
        """测试强度映射公式边界与默认值"""
        # 默认值 50
        assert map_strength_to_mosaic_size(50) == 20
        assert map_strength_to_blur_ksize(50) == 45

        # 最小值 0
        assert map_strength_to_mosaic_size(0) == 4
        assert map_strength_to_blur_ksize(0) == 5

        # 最大值 100
        assert map_strength_to_mosaic_size(100) == 64
        assert map_strength_to_blur_ksize(100) == 99

        # blur_ksize 必须恒为奇数
        for s in range(0, 101):
            k = map_strength_to_blur_ksize(s)
            assert k % 2 == 1, f"blur_ksize at strength {s} is not odd: {k}"
            assert 5 <= k <= 99

    def test_mosaic_size_8_vs_40_pixel_difference(self, censor_engine, tmp_path):
        """测试 1: mosaic_size 8 vs 40 输出颗粒度可测不同"""
        # 创建带有渐变纹理的测试图
        arr = np.zeros((200, 200, 3), dtype=np.uint8)
        for i in range(200):
            arr[i, :, :] = (i, (i * 2) % 256, (255 - i))
        img_p = tmp_path / "gradient.png"
        cv2.imwrite(str(img_p), arr)

        box = [[20, 20, 180, 180]]
        res_8, _ = censor_engine.apply_censor(
            str(img_p),
            style="mosaic",
            resolved_boxes=box,
            mosaic_size=8,
            opacity=1.0,
        )
        res_40, _ = censor_engine.apply_censor(
            str(img_p),
            style="mosaic",
            resolved_boxes=box,
            mosaic_size=40,
            opacity=1.0,
        )

        roi_8 = res_8[20:180, 20:180]
        roi_40 = res_40[20:180, 20:180]

        # 颗粒度不同，ROI 像素值绝对不可相同
        diff = cv2.absdiff(roi_8, roi_40)
        assert np.max(diff) > 20, "mosaic_size 8 与 40 颗粒度输出无显著差异"
        # 40px 的颗粒块数量远少于 8px (颜色阶梯更少)
        unique_colors_8 = len(np.unique(roi_8.reshape(-1, 3), axis=0))
        unique_colors_40 = len(np.unique(roi_40.reshape(-1, 3), axis=0))
        assert unique_colors_8 > unique_colors_40 * 2

    def test_opacity_numerical_assertion(self, censor_engine, tmp_path):
        """测试 2: opacity=0.5 等权混合数值断言 (alpha * ROI + (1-alpha) * 原图)"""
        # 创建纯色测试图：RGB(200, 200, 200)
        arr = np.full((100, 100, 3), 200, dtype=np.uint8)
        img_p = tmp_path / "solid_200.png"
        cv2.imwrite(str(img_p), arr)

        box = [[20, 20, 80, 80]]

        # 黑条 style="black_bar"，打码 ROI 为纯黑 (0, 0, 0)
        # 1. opacity = 1.0 -> 纯黑 0
        res_100, _ = censor_engine.apply_censor(str(img_p), style="black_bar", resolved_boxes=box, opacity=1.0)
        assert np.all(res_100[20:80, 20:80] == 0)

        # 2. opacity = 0.5 -> 0.5 * 0 + 0.5 * 200 = 100
        res_50, _ = censor_engine.apply_censor(str(img_p), style="black_bar", resolved_boxes=box, opacity=0.5)
        roi_50 = res_50[20:80, 20:80]
        # 允许 ±1 的 OpenCV 浮点混合舍入误差
        assert np.all(np.abs(roi_50.astype(int) - 100) <= 1), f"opacity=0.5 混合数值异常: {roi_50[0, 0]}"

        # 3. opacity = 0.2 -> 0.2 * 0 + 0.8 * 200 = 160
        res_20, _ = censor_engine.apply_censor(str(img_p), style="black_bar", resolved_boxes=box, opacity=0.2)
        roi_20 = res_20[20:80, 20:80]
        assert np.all(np.abs(roi_20.astype(int) - 160) <= 1), f"opacity=0.2 混合数值异常: {roi_20[0, 0]}"

    def test_blur_ksize_parameter_effect(self, censor_engine, tmp_path):
        """测试 3: blur_ksize 参数生效 (小核 vs 大核)"""
        # 创建中心为白色单像素线的黑色背景图
        arr = np.zeros((100, 100, 3), dtype=np.uint8)
        arr[50, :, :] = 255
        img_p = tmp_path / "line.png"
        cv2.imwrite(str(img_p), arr)

        box = [[10, 10, 90, 90]]
        res_small, _ = censor_engine.apply_censor(str(img_p), style="blur", resolved_boxes=box, blur_ksize=5)
        res_large, _ = censor_engine.apply_censor(str(img_p), style="blur", resolved_boxes=box, blur_ksize=85)

        roi_small = res_small[10:90, 10:90]
        roi_large = res_large[10:90, 10:90]

        # 大核高斯模糊能量散布更广，中心行 50 的峰值亮度应低于小核
        center_small = np.mean(roi_small[40, :, :])
        center_large = np.mean(roi_large[40, :, :])
        assert center_small > center_large + 20, "高斯核大小变化未产生预期的扩散差异"

    def test_gif_censor_pipeline_opacity_and_mosaic(self, censor_engine, tmp_path):
        """测试 4: GIF 路径支持 mosaic_size, blur_ksize 与 opacity 逐帧混合"""
        # 创建 2 帧的测试动图
        gif_p = tmp_path / "test.gif"
        f1 = Image.new("RGB", (100, 100), color=(180, 180, 180))
        f2 = Image.new("RGB", (100, 100), color=(220, 220, 220))
        f1.save(str(gif_p), save_all=True, append_images=[f2], duration=100, loop=0)

        out_gif = tmp_path / "out_censored.gif"
        box = [[20, 20, 80, 80]]
        censor_engine.apply_censor_gif(
            str(gif_p),
            output_path=str(out_gif),
            style="black_bar",
            resolved_boxes=box,
            opacity=0.5,
        )

        assert out_gif.exists()
        with Image.open(str(out_gif)) as im:
            assert im.n_frames == 2
            im.seek(0)
            fr1 = np.array(im.convert("RGB"))
            # 第一帧 180 的 50% 混合 = 90 (±2)
            assert np.all(np.abs(fr1[30:70, 30:70, :].astype(int) - 90) <= 2)


class TestCensorParamsUIWiring:
    def test_censor_style_slider_enable_disable(self, qapp, tmp_path, monkeypatch):
        """测试 5: 黑条模式禁用强度滑杆加 tooltip，马赛克/模糊启用"""
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
        monkeypatch.setattr("app.save_config", lambda cfg: None)
        from app import MainWindow
        from history_manager import PublishHistoryManager

        with patch.object(CensorEngine, "_init_detector", lambda self: None), patch("app.WatermarkEngine"):
            win = MainWindow()
            win.data_dir = tmp_path
            win.history_manager = PublishHistoryManager(tmp_path)
            win.notify = MagicMock()

            # 初始默认或选黑条
            win.censor_style_combo.setCurrentIndex(0)  # 黑条
            assert win.censor_slider.isEnabled() is False
            assert "黑条模式无颗粒/强度参数" in win.censor_slider.toolTip()

            # 切换为马赛克
            win.censor_style_combo.setCurrentIndex(1)
            assert win.censor_slider.isEnabled() is True
            assert "马赛克方块大小" in win.censor_slider.toolTip()

            # 切换为高斯模糊
            win.censor_style_combo.setCurrentIndex(2)
            assert win.censor_slider.isEnabled() is True
            assert "高斯模糊核大小" in win.censor_slider.toolTip()

            # 切换回黑条
            win.censor_style_combo.setCurrentIndex(0)
            assert win.censor_slider.isEnabled() is False

            for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
                if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                    getattr(win, t_attr).stop()

    def test_config_sync_and_persistence(self, qapp, tmp_path, monkeypatch):
        """测试 6: 强度与透明度 config 回读与保存"""
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
        monkeypatch.setattr("app.save_config", lambda cfg: None)
        from app import MainWindow
        from history_manager import PublishHistoryManager

        with patch.object(CensorEngine, "_init_detector", lambda self: None), patch("app.WatermarkEngine"):
            win = MainWindow()
            win.data_dir = tmp_path
            win.history_manager = PublishHistoryManager(tmp_path)
            win.notify = MagicMock()

            # 调节透明度拉杆至 70%，强度滑块至 85
            win.censor_opacity_slider.setValue(70)
            win.censor_slider.setValue(85)

            win._sync_config_from_ui()
            assert win.config["censor"]["opacity"] == 0.70
            assert win.config["censor"]["strength"] == 85

            assert win._get_censor_opacity() == pytest.approx(0.70)
            assert win._get_censor_strength() == 85
            assert win._get_censor_mosaic_size() == map_strength_to_mosaic_size(85)
            assert win._get_censor_blur_ksize() == map_strength_to_blur_ksize(85)

            for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
                if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                    getattr(win, t_attr).stop()

    def test_gif_censor_params_passthrough_in_composite(self, qapp, tmp_path, monkeypatch):
        """测试 7: GIF 合成链 censor_params 包含 mosaic_size, blur_ksize 与 opacity"""
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
        monkeypatch.setattr("app.save_config", lambda cfg: None)
        from app import MainWindow
        from history_manager import PublishHistoryManager

        gif_p = tmp_path / "art.gif"
        f1 = Image.new("RGB", (100, 100), color=(100, 100, 100))
        f1.save(str(gif_p), save_all=True, append_images=[f1], duration=100)

        with patch.object(CensorEngine, "_init_detector", lambda self: None), patch("app.WatermarkEngine"):
            win = MainWindow()
            win.data_dir = tmp_path
            win.history_manager = PublishHistoryManager(tmp_path)
            win.notify = MagicMock()

            win.image_paths = [str(gif_p)]
            win.current_preview_index = 0
            win.censor_opacity_slider.setValue(65)
            win.censor_slider.setValue(30)
            win.censor_style_combo.setCurrentIndex(1)  # 马赛克

            # 触发 _generate_composite_gif
            out_target = tmp_path / "out.gif"
            c_params = {
                "enabled": True,
                "style": "mosaic",
                "boxes": [[10, 10, 50, 50]],
                "mosaic_size": win._get_censor_mosaic_size(),
                "blur_ksize": win._get_censor_blur_ksize(),
                "opacity": win._get_censor_opacity(),
            }
            win._generate_composite_gif(str(gif_p), str(out_target), censor_params=c_params, wm_params=None)
            assert out_target.exists()

            for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
                if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                    getattr(win, t_attr).stop()
