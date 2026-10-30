# -*- coding: utf-8 -*-
"""
Tests for fix/censor-slider-custom-boxes:
1. 定制框 + 拉杆变化 -> 框按 current_ratio / captured_ratio 比值绕自身中心缩放，再钳制越界
2. 同一 ratio 反复刷新 -> 框零漂移（幂等），且不回写修改 custom_censor_boxes
3. ratio 拉回 captured 值 -> 框与存储值逐像素一致（含奇数宽高框）
4. 无定制框时行为与现状一致（回归测试）
5. MainWindow 预览（静态图 + GIF）与发布路径共用 _get_effective_censor_boxes
"""
import sys
from unittest.mock import MagicMock, patch
if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()
import pytest
from PIL import Image
from PyQt5.QtWidgets import QApplication
from censor_engine import CensorEngine


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


class TestCensorSliderCustomBoxes:
    def test_custom_box_scales_around_center_with_ratio_change(self, censor_engine):
        """
        单测 1：定制框 + 拉杆变化 → 框按 (current_ratio / captured_ratio) 比值绕中心缩放
        """
        # 初始定制框: 中心 (200, 300), 宽 100 (150..250), 高 80 (260..340)
        stored_boxes = [[150, 260, 250, 340]]
        captured_ratio = 0.50

        # 拉杆调大至 1.00 (比值 = 2.0) -> 宽 200 (100..300), 高 160 (220..380)
        scaled_up = censor_engine.resolve_final_boxes(
            stored_boxes,
            img_w=800,
            img_h=800,
            mode="moderate",
            custom_ratio=1.00,
            user_customized=True,
            captured_ratio=captured_ratio,
        )
        assert scaled_up == [[100, 220, 300, 380]]
        # 源存储框严禁被修改
        assert stored_boxes == [[150, 260, 250, 340]]

        # 拉杆调小至 0.25 (比值 = 0.5) -> 宽 50 (175..225), 高 40 (280..320)
        scaled_down = censor_engine.resolve_final_boxes(
            stored_boxes,
            img_w=800,
            img_h=800,
            mode="moderate",
            custom_ratio=0.25,
            user_customized=True,
            captured_ratio=captured_ratio,
        )
        assert scaled_down == [[175, 280, 225, 320]]

    def test_idempotent_repeated_refresh_zero_drift(self, censor_engine):
        """
        单测 2：同一 ratio 反复刷新 → 框零漂移（幂等），且禁止回写 custom_censor_boxes
        """
        stored_boxes = [[120, 180, 280, 320]]
        captured_ratio = 0.60
        target_ratio = 0.42

        first_pass = censor_engine.resolve_final_boxes(
            stored_boxes,
            img_w=1000,
            img_h=1000,
            mode="moderate",
            custom_ratio=target_ratio,
            user_customized=True,
            captured_ratio=captured_ratio,
        )
        for _ in range(20):
            again = censor_engine.resolve_final_boxes(
                stored_boxes,
                img_w=1000,
                img_h=1000,
                mode="moderate",
                custom_ratio=target_ratio,
                user_customized=True,
                captured_ratio=captured_ratio,
            )
            assert again == first_pass
        # 确认原始存储框未被污染
        assert stored_boxes == [[120, 180, 280, 320]]

    def test_returning_to_captured_ratio_matches_stored_pixel_for_pixel(self, censor_engine):
        """
        单测 3：ratio 拉回 captured 值 → 框与存储值逐像素一致（含奇数尺寸框）
        """
        # 包含奇数宽高框 (宽 103, 高 77)
        stored_boxes = [[101, 203, 204, 280], [310, 415, 499, 550]]
        captured_ratio = 0.55

        # 先拉到其他比例
        _ = censor_engine.resolve_final_boxes(
            stored_boxes,
            img_w=800,
            img_h=800,
            mode="moderate",
            custom_ratio=0.95,
            user_customized=True,
            captured_ratio=captured_ratio,
        )
        _ = censor_engine.resolve_final_boxes(
            stored_boxes,
            img_w=800,
            img_h=800,
            mode="moderate",
            custom_ratio=0.25,
            user_customized=True,
            captured_ratio=captured_ratio,
        )

        # 拉回 captured_ratio (0.55)
        restored = censor_engine.resolve_final_boxes(
            stored_boxes,
            img_w=800,
            img_h=800,
            mode="moderate",
            custom_ratio=captured_ratio,
            user_customized=True,
            captured_ratio=captured_ratio,
        )
        assert restored == stored_boxes

    def test_non_customized_boxes_regression(self, censor_engine):
        """
        单测 4：无定制框 (user_customized=False) 时行为与现状一致（回归）
        """
        ai_raw_boxes = [[100, 100, 300, 300]]
        for r in (0.30, 0.55, 1.00):
            expected = [censor_engine.compute_censor_box(ai_raw_boxes[0], 800, 800, mode="moderate", custom_ratio=r)]
            actual = censor_engine.resolve_final_boxes(
                ai_raw_boxes,
                img_w=800,
                img_h=800,
                mode="moderate",
                custom_ratio=r,
                user_customized=False,
                captured_ratio=None,
            )
            assert actual == expected

    def test_mainwindow_shared_effective_boxes_for_static_and_gif(self, tmp_path, qapp, monkeypatch):
        """
        验证 MainWindow._on_canvas_boxes_changed 记录 captured_ratio，
        且静态图预览、GIF 预览与发布路径共用 _get_effective_censor_boxes，不回写 custom_censor_boxes
        """
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
        from app import MainWindow
        from history_manager import PublishHistoryManager

        static_p = tmp_path / "static_art.png"
        Image.new("RGB", (600, 600), color=(120, 140, 160)).save(static_p)

        gif_p = tmp_path / "anim_art.gif"
        f1 = Image.new("RGB", (600, 600), color=(100, 100, 100))
        f2 = Image.new("RGB", (600, 600), color=(150, 150, 150))
        f1.save(gif_p, save_all=True, append_images=[f2], duration=100, loop=0)

        with patch.object(CensorEngine, "_init_detector", lambda self: None), patch("app.WatermarkEngine"):
            win = MainWindow()
            win.data_dir = tmp_path
            win.history_manager = PublishHistoryManager(tmp_path)
            win.notify = MagicMock()

            win.image_paths = [str(static_p), str(gif_p)]
            win.current_preview_index = 0
            win.auto_censor_cb.setChecked(True)
            win.watermark_enabled_cb.setChecked(False)
            win.censor_slider.setValue(50)  # captured_ratio = 0.50

            # 模拟用户在画布上手调静态图打码框
            win._on_canvas_boxes_changed([[200, 200, 300, 300]])
            assert win.custom_censor_boxes[str(static_p)] == [[200, 200, 300, 300]]
            assert win.custom_censor_captured_ratios[str(static_p)] == pytest.approx(0.55)

            # 调整强度拉杆到 100%，手动框保持画布拖拽尺寸不变（不再受拉杆缩放）
            win.censor_slider.setValue(100)
            win._do_refresh_live_preview()
            eff_static = win._get_effective_censor_boxes(str(static_p), orig_w=600, orig_h=600)
            assert eff_static == [[200, 200, 300, 300]]
            # custom_censor_boxes 存储值保持原样未回写
            assert win.custom_censor_boxes[str(static_p)] == [[200, 200, 300, 300]]

            # 拉至 0%，同样保持原样
            win.censor_slider.setValue(0)
            assert win._get_effective_censor_boxes(str(static_p), orig_w=600, orig_h=600) == [[200, 200, 300, 300]]

            # 切换到 GIF 动图，验证走同一套计算且手动框不受强度拉杆缩放
            win.current_preview_index = 1
            win.censor_slider.setValue(50)
            win._on_canvas_boxes_changed([[200, 200, 300, 300]])
            win.censor_slider.setValue(100)
            win._do_refresh_live_preview()
            assert win.preview_canvas._censor_boxes == [[200, 200, 300, 300]]
            assert win.custom_censor_boxes[str(gif_p)] == [[200, 200, 300, 300]]

            for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
                if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                    getattr(win, t_attr).stop()
