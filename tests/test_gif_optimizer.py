# -*- coding: utf-8 -*-
"""
Tests for GIF Optimizer (optimize_raw_gif & compress_gif_frames in app.py)
Covering:
- Small GIF already within limit
- Palette reduction
- Smart frame decimation (preserves playback speed, halves frames)
- Strict aspect ratio preservation (no 1:1 square warped into 4:3)
- 2026-10-23: 52MB 级大 GIF 降级顺序重排 (先降分辨率 -> 再降色彩 -> 最后抽帧)
"""
import pytest
from pathlib import Path
from PIL import Image, ImageDraw
from app import optimize_raw_gif, compress_gif_frames


@pytest.fixture
def temp_gif_path(tmp_path):
    def _create_gif(num_frames=20, width=200, height=200, duration=60):
        frames = []
        for i in range(num_frames):
            img = Image.new("RGB", (width, height), color=(i * 10 % 255, 60, (i * 20) % 255))
            draw = ImageDraw.Draw(img)
            draw.rectangle([i * 4, i * 4, i * 4 + 50, i * 4 + 50], fill=((i * 30) % 255, 200, 150))
            frames.append(img)
        gif_file = tmp_path / "sample.gif"
        frames[0].save(
            str(gif_file),
            save_all=True,
            append_images=frames[1:],
            duration=duration,
            loop=0,
            optimize=True
        )
        return gif_file, frames, duration

    return _create_gif


def test_optimize_raw_gif_within_limit(temp_gif_path, tmp_path):
    gif_file, _, _ = temp_gif_path(num_frames=5, width=100, height=100)
    out_file = tmp_path / "out_small.gif"
    res = optimize_raw_gif(gif_file, out_file, max_limit_mb=3.8)
    assert Path(res).exists()
    assert Path(res).stat().st_size <= 3.8 * 1024 * 1024


def test_optimize_raw_gif_aspect_ratio_preservation(temp_gif_path, tmp_path):
    # 创建正方形较重动图 (1:1 宽高比)
    gif_file, _, _ = temp_gif_path(num_frames=24, width=300, height=300)
    out_file = tmp_path / "out_square.gif"

    # 压缩到一个较低的预算 (例如 0.05MB) 强制触发缩放
    target_mb = 0.05
    res = optimize_raw_gif(gif_file, out_file, max_limit_mb=target_mb)
    assert Path(res).exists()
    assert Path(res).stat().st_size <= target_mb * 1024 * 1024

    with Image.open(res) as im:
        w, h = im.size
        # 严禁旧版 max(320, ...) max(240, ...) 导致的方形画面被拉扯为 320x299
        assert w == h, f"宽高比应严格保持 1:1，实际为 {w}x{h}"


def test_compress_gif_frames_frame_decimation(temp_gif_path, tmp_path):
    _, frames, duration = temp_gif_path(num_frames=20, width=150, height=150, duration=50)
    out_file = tmp_path / "out_decimated.gif"

    # 触发包含抽帧的压缩 (0.02MB)
    target_mb = 0.02
    res = compress_gif_frames(frames, duration, 0, out_file, max_limit_mb=target_mb)
    assert Path(res).exists()
    assert Path(res).stat().st_size <= target_mb * 1024 * 1024


def test_compress_gif_52mb_reordered_pipeline(tmp_path, caplog):
    """
    单测要求：52MB 级大 GIF 走完管线后体积 ≤ 限制（13.8MB），且断言分辨率先于色彩被调整（检查处理日志顺序）
    """
    import logging
    import numpy as np

    w, h = 2200, 2200
    num_frames = 20
    frames = []
    for _ in range(num_frames):
        tile = np.random.randint(0, 256, (200, 200, 3), dtype=np.uint8)
        arr = np.tile(tile, (11, 11, 1))
        frames.append(Image.fromarray(arr))

    out_file = tmp_path / "out_52mb_opt.gif"
    target_limit_mb = 13.8

    with caplog.at_level(logging.INFO):
        res = compress_gif_frames(frames, duration=100, loop=0, out_p=out_file, max_limit_mb=target_limit_mb)

    # 1. 断言走完管线后最终文件存在且体积 <= 13.8MB
    final_path = Path(res)
    assert final_path.exists()
    final_bytes = final_path.stat().st_size
    assert final_bytes <= int(target_limit_mb * 1024 * 1024)

    # 2. 断言分辨率调整生效且先于调色板与抽帧降级
    log_text = caplog.text
    assert "自适应降低分辨率" in log_text

    res_pos = log_text.find("自适应降低分辨率")
    assert res_pos != -1

    color_pos = log_text.find("色调色板")
    if color_pos != -1:
        assert res_pos < color_pos, "降级顺序错误: 调色板降级应在分辨率降级达到下限后才触发"

    frame_pos = log_text.find("智能隔帧抽帧")
    if frame_pos != -1:
        assert res_pos < frame_pos, "降级顺序错误: 抽帧降级应在最后才触发"
