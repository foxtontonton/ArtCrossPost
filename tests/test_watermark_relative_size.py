# -*- coding: utf-8 -*-
"""
针对「水印字号改为相对画幅宽度自适应 (feature/watermark-relative-size)」的单元测试：
1. 同一文字 + 同一百分比分别压到 800px 与 3200px 宽图上，文字渲染包围盒宽度比值 ≈ 4（允许 ±10% 误差）
2. 小图保底 10px 生效
3. UI 拉杆范围 1.0%–8.0%（步进 0.1，标签 "3.0%"）、配置键 watermark.size_pct 默认 3.0，且全调用点统一传 font_size_pct
"""
import ast
from pathlib import Path
import numpy as np
from PIL import Image

from config import DEFAULT_CONFIG, load_config
from watermark_engine import WatermarkEngine


def _measure_rendered_watermark_bbox_width(img_rgb: Image.Image) -> int:
    """
    在纯黑底图 (0,0,0) 上测量渲染出的非零水印像素包围盒宽度 (x_max - x_min + 1)
    """
    arr = np.array(img_rgb)
    mask = np.any(arr > 0, axis=2)
    xs = np.where(np.any(mask, axis=0))[0]
    assert len(xs) > 0, "未检测到渲染出的水印像素"
    return int(xs[-1] - xs[0] + 1)


def test_watermark_bbox_width_ratio_800_vs_3200():
    """
    单测 1：同一文字 + 同一百分比 (3.0%) 分别压到 800px 与 3200px 宽图上，
    文字渲染包围盒宽度比值 ≈ 4（允许 ±10% 误差，即 3.6 ~ 4.4）
    """
    engine = WatermarkEngine()
    img_800 = Image.new("RGB", (800, 600), (0, 0, 0))
    img_3200 = Image.new("RGB", (3200, 2400), (0, 0, 0))

    out_800, _ = engine.apply_watermark(
        img_800,
        text="@FOXTONTONTON",
        font_size_pct=3.0,
        opacity=1.0,
        position="center",
        color_hex="#ffffff",
        enable_stroke=True,
    )
    out_3200, _ = engine.apply_watermark(
        img_3200,
        text="@FOXTONTONTON",
        font_size_pct=3.0,
        opacity=1.0,
        position="center",
        color_hex="#ffffff",
        enable_stroke=True,
    )

    w_800 = _measure_rendered_watermark_bbox_width(out_800)
    w_3200 = _measure_rendered_watermark_bbox_width(out_3200)
    ratio = w_3200 / float(w_800)

    assert 3.6 <= ratio <= 4.4, (
        f"3200px 与 800px 宽图的水印包围盒宽度比值应约为 4 (±10%)，实际 w_800={w_800}, w_3200={w_3200}, ratio={ratio:.3f}"
    )


def test_small_image_clamp_14_to_120px():
    """
    单测 2：方案 A 长边制下，水印字号严格钳制在 [14, 120] 像素区间内：
    - 下限保底 14px：小图 200x150 时，1.0% (2px)、3.0% (6px)、5.0% (10px) 均钳制为 14px
      因此在小图上渲染出的文字包围盒宽度完全一致。
    - 上限封顶 120px：大图 5000x4000 时，3.0% (150px) 封顶为 120px；8000x6000 时 (240px) 封顶为 120px。
    """
    engine = WatermarkEngine()
    # 下限 14px 钳制
    assert engine.compute_font_px(200, 1.0) == 14
    assert engine.compute_font_px(200, 3.0) == 14
    assert engine.compute_font_px(100, 1.0) == 14
    assert engine.compute_font_px(200, 150, 1.0) == 14
    assert engine.compute_font_px(200, 150, 3.0) == 14
    assert engine.compute_font_px(200, 150, 5.0) == 14

    # 中间正常计算 (不触发钳制)
    assert engine.compute_font_px(1000, 3.0) == 30
    assert engine.compute_font_px(1000, 1000, 3.0) == 30

    # 上限 120px 封顶
    assert engine.compute_font_px(5000, 4000, 3.0) == 120
    assert engine.compute_font_px(8000, 6000, 3.0) == 120
    assert engine.compute_font_px(4000, 5000, 3.0) == 120

    small_img = Image.new("RGB", (200, 150), (0, 0, 0))
    out_1pct, _ = engine.apply_watermark(
        small_img,
        text="@FOX",
        font_size_pct=1.0,
        opacity=1.0,
        position="center",
        enable_stroke=False,
    )
    out_3pct, _ = engine.apply_watermark(
        small_img,
        text="@FOX",
        font_size_pct=3.0,
        opacity=1.0,
        position="center",
        enable_stroke=False,
    )
    out_5pct, _ = engine.apply_watermark(
        small_img,
        text="@FOX",
        font_size_pct=5.0,
        opacity=1.0,
        position="center",
        enable_stroke=False,
    )

    w_1 = _measure_rendered_watermark_bbox_width(out_1pct)
    w_3 = _measure_rendered_watermark_bbox_width(out_3pct)
    w_5 = _measure_rendered_watermark_bbox_width(out_5pct)
    assert w_1 == w_3 == w_5


def test_watermark_long_edge_horizontal_vertical_symmetry(tmp_path):
    """
    单测 5：方案 A 长边制断言：
    - 横图 (1600x800) 与 竖图 (800x1600) 在相同字号比例 (3.0%) 下，
      计算得到的字号绝对一致：max(1600, 800) * 3% = 48px。
    - 渲染出的水印包围盒宽度与高度在横图和竖图上绝对一致（杜绝竖图因窄宽导致字号缩水问题）。
    - VideoEngine 生成的 overlay 水印图层在长边相同时字号亦完全对称。
    """
    engine = WatermarkEngine()
    px_h = engine.compute_font_px(1600, 800, 3.0)
    px_v = engine.compute_font_px(800, 1600, 3.0)
    assert px_h == 48
    assert px_v == 48
    assert px_h == px_v

    img_h = Image.new("RGB", (1600, 800), (0, 0, 0))
    img_v = Image.new("RGB", (800, 1600), (0, 0, 0))

    out_h, _ = engine.apply_watermark(
        img_h,
        text="@FOXTONTONTON",
        font_size_pct=3.0,
        opacity=1.0,
        position="center",
        enable_stroke=True,
    )
    out_v, _ = engine.apply_watermark(
        img_v,
        text="@FOXTONTONTON",
        font_size_pct=3.0,
        opacity=1.0,
        position="center",
        enable_stroke=True,
    )

    bw_h = _measure_rendered_watermark_bbox_width(out_h)
    bw_v = _measure_rendered_watermark_bbox_width(out_v)
    assert bw_h == bw_v, f"横图与竖图长边相同时文字渲染宽度应完全相等，实际横图={bw_h}, 竖图={bw_v}"

    # 验证 VideoEngine 同样遵循长边规则
    from video_engine import VideoEngine
    ve = VideoEngine()
    wm_params = {
        "text": "@FOXTONTONTON",
        "font_size_pct": 3.0,
        "opacity": 1.0,
        "position": "center",
        "stroke": True,
    }
    overlay_h_path = ve.render_watermark_overlay_png(1600, 800, wm_params, tmp_path / "wm_h.png")
    overlay_v_path = ve.render_watermark_overlay_png(800, 1600, wm_params, tmp_path / "wm_v.png")

    img_ov_h = Image.open(overlay_h_path).convert("RGB")
    img_ov_v = Image.open(overlay_v_path).convert("RGB")
    assert _measure_rendered_watermark_bbox_width(img_ov_h) == _measure_rendered_watermark_bbox_width(img_ov_v)


def test_config_and_all_call_sites_use_font_size_pct():
    """
    单测 3：验证配置 watermark.size_pct 默认 3.0（废弃旧 font_size 键），
    且 app.py 中所有 apply_watermark 调用点均传 font_size_pct，不留任何传绝对 font_size 的调用点。
    """
    wm_cfg = DEFAULT_CONFIG["watermark"]
    assert wm_cfg.get("size_pct") == 3.0
    assert "font_size" not in wm_cfg

    loaded = load_config()
    assert "font_size" not in loaded.get("watermark", {})
    assert isinstance(loaded["watermark"].get("size_pct", 3.0), (int, float))

    app_src = Path(__file__).resolve().parent.parent.joinpath("app.py").read_text(encoding="utf-8")
    tree = ast.parse(app_src)

    apply_wm_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("apply_watermark", "apply_watermark_gif"):
                kw_names = [kw.arg for kw in node.keywords]
                apply_wm_calls.append((node.lineno, node.func.attr, kw_names))

    assert len(apply_wm_calls) >= 4
    for lineno, attr_name, kw_names in apply_wm_calls:
        assert "font_size_pct" in kw_names, f"app.py:{lineno} 调用 {attr_name} 未传入 font_size_pct"
        assert "font_size" not in kw_names, f"app.py:{lineno} 调用 {attr_name} 仍残留绝对像素参数 font_size"


def test_watermark_ui_slider_range_and_label(monkeypatch):
    """
    单测 4：验证 UI 水印字号拉杆范围为 1.0%–8.0%（步进 0.1%，即 10~80），默认标签显示 "3.0%"
    """
    import sys
    from PyQt5.QtWidgets import QApplication
    import app as app_mod

    _ = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(app_mod, "get_all_dispatchers", lambda *a, **kw: [])
    monkeypatch.setattr(app_mod.MainWindow, "_restore_window_layout", lambda self: None)

    win = app_mod.MainWindow()
    try:
        assert win.watermark_size_slider.minimum() == 10
        assert win.watermark_size_slider.maximum() == 80
        assert win.watermark_size_slider.singleStep() == 1
        win.watermark_size_slider.setValue(30)
        assert win._get_watermark_size_pct() == 3.0
        assert win.watermark_size_lbl.text() == "3.0%"
        win.watermark_size_slider.setValue(45)
        assert win._get_watermark_size_pct() == 4.5
        assert win.watermark_size_lbl.text() == "4.5%"
    finally:
        win.close()

