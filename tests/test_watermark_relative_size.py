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


def test_small_image_min_10px_floor():
    """
    单测 2：小图保底 10px 生效
    当图宽为 200px 时：
    - 1.0% 理论值为 2px -> 保底应为 10px
    - 3.0% 理论值为 6px -> 保底应为 10px
    两者在 200px 小图上渲染出的文字包围盒宽度应完全一致（均按 10px 渲染）。
    """
    engine = WatermarkEngine()
    assert engine.compute_font_px(200, 1.0) == 10
    assert engine.compute_font_px(200, 3.0) == 10
    assert engine.compute_font_px(100, 1.0) == 10
    assert engine.compute_font_px(1000, 3.0) == 30

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
    # 5.0% 在 200px 宽图上正好也是 10px
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

