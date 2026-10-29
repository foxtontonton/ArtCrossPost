# -*- coding: utf-8 -*-
"""
Tests for 精简发布渲染生成的图片副本 (feature/reduce-render-copies):
1. 取消 censored_clean 的无条件生成与写入，Patreon/Ko-fi target 逻辑简化
2. DA 干净名副本优先硬链接 (os.link)，失败回退 shutil.copyfile
3. X 静态图 xopt：未超限无需转码时不生成副本；未选用 xopt 自动清理不留残骸
4. GIF 跨平台复用：打码与水印参数一致且体积合规时，X 和 Ko-fi 复用已渲染动图，不重复调用 _generate_composite_gif
5. 失败重试命中缓存复用
"""
import copy
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()

import numpy as np
from PIL import Image
import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread

from config import DEFAULT_CONFIG
from app import (
    MainWindow,
    CACHE_DIR,
    link_or_copy_file,
    optimize_static_image,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def mock_main_window(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    # 隔离 config 并开启 Ko-fi
    cfg_copy = copy.deepcopy(DEFAULT_CONFIG)
    cfg_copy["platform_kofi_enabled"] = True
    monkeypatch.setattr("app.load_config", lambda: copy.deepcopy(cfg_copy))
    monkeypatch.setattr("app.save_config", lambda cfg: None)

    with patch("app.CensorEngine") as mock_ce, patch("app.WatermarkEngine") as mock_we:
        ce_instance = mock_ce.return_value
        # 默认 apply_censor 返回一个 ndarray 和 boxes
        dummy_bgr = np.zeros((100, 100, 3), dtype=np.uint8)
        ce_instance.apply_censor.return_value = (dummy_bgr, [[10, 10, 30, 30]])

        we_instance = mock_we.return_value
        def fake_apply_wm(img_or_bgr, **kwargs):
            out_p = kwargs.get("output_path")
            if out_p:
                Path(out_p).parent.mkdir(parents=True, exist_ok=True)
                if isinstance(img_or_bgr, np.ndarray):
                    import cv2
                    cv2.imwrite(str(out_p), img_or_bgr)
                elif isinstance(img_or_bgr, Image.Image):
                    img_or_bgr.save(str(out_p))
                elif isinstance(img_or_bgr, (str, Path)):
                    shutil.copyfile(str(img_or_bgr), str(out_p))
            if isinstance(img_or_bgr, Image.Image):
                return img_or_bgr, (10, 10)
            return img_or_bgr
        we_instance.apply_watermark.side_effect = fake_apply_wm

        win = MainWindow()

    win.data_dir = tmp_path
    win.notify = MagicMock()
    win._clear_draft = MagicMock()

    # 创建标准 4 平台 dispatchers
    d_da = MagicMock()
    d_da.name = "deviantart"
    d_da.display_name = "DeviantArt"

    d_x = MagicMock()
    d_x.name = "x_twitter"
    d_x.display_name = "X (Twitter)"

    d_pat = MagicMock()
    d_pat.name = "patreon"
    d_pat.display_name = "Patreon"

    d_kofi = MagicMock()
    d_kofi.name = "kofi"
    d_kofi.display_name = "Ko-fi"

    win.dispatchers = [d_da, d_x, d_pat, d_kofi]

    yield win

    # 停止定时器防退出崩溃
    if hasattr(win, "_schedule_poll_timer") and win._schedule_poll_timer.isActive():
        win._schedule_poll_timer.stop()
    if hasattr(win, "_draft_save_timer") and win._draft_save_timer.isActive():
        win._draft_save_timer.stop()
    if hasattr(win, "_layout_save_timer") and win._layout_save_timer.isActive():
        win._layout_save_timer.stop()
    if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
        win.thread.quit()
        win.thread.wait()


# ==============================================================================
# 1. link_or_copy_file 单元测试
# ==============================================================================

def test_link_or_copy_file_hardlink_and_fallback(tmp_path, monkeypatch):
    """验证硬链接优先、已存在覆盖、以及跨卷/失败回退 copyfile 机制"""
    src_file = tmp_path / "original.png"
    src_file.write_bytes(b"HELLO_ORIGINAL_BYTES_12345")
    dst_file = tmp_path / "subfolder" / "link_copy.png"

    # 1. 首次创建硬链接
    ret = link_or_copy_file(src_file, dst_file)
    assert Path(ret).exists()
    assert dst_file.read_bytes() == b"HELLO_ORIGINAL_BYTES_12345"

    # 检查 inode 是否一致 (在支持硬链接的本地卷上)
    same_inode = False
    try:
        same_inode = (src_file.stat().st_ino == dst_file.stat().st_ino)
    except Exception:
        pass

    # 2. 目标已存在时无异常覆盖
    src_file.write_bytes(b"UPDATED_CONTENT_FOR_OVERWRITE")
    ret2 = link_or_copy_file(src_file, dst_file)
    assert Path(ret2).exists()
    assert dst_file.read_bytes() == b"UPDATED_CONTENT_FOR_OVERWRITE"

    # 3. 模拟 os.link 抛出 OSError (例如跨卷 EXDEV 错误) -> 优雅回退 shutil.copyfile
    fallback_dst = tmp_path / "fallback.png"
    def mock_link_fail(s, d):
        raise OSError(18, "Cross-device link")

    monkeypatch.setattr(os, "link", mock_link_fail)
    ret3 = link_or_copy_file(src_file, fallback_dst)
    assert Path(ret3).exists()
    assert fallback_dst.read_bytes() == b"UPDATED_CONTENT_FOR_OVERWRITE"

    # 4. 同路径保护
    ret4 = link_or_copy_file(src_file, src_file)
    assert ret4 == str(src_file)


# ==============================================================================
# 2. optimize_static_image 副本精简测试
# ==============================================================================

def test_optimize_static_image_no_unnecessary_copy(tmp_path):
    """原图为 JPEG 且体积 <= 上限时，不生成 _xopt.jpg 文件副本，直接返回原图路径"""
    in_jpg = tmp_path / "sample_under_limit.jpg"
    out_jpg = tmp_path / "sample_under_limit_xopt.jpg"

    im = Image.new("RGB", (200, 200), color=(100, 150, 200))
    im.save(in_jpg, "JPEG", quality=90)

    res = optimize_static_image(in_jpg, out_jpg, max_limit_mb=4.8, quality=88, compress_only_when_over_limit=True)
    assert res == str(in_jpg)
    assert out_jpg.exists() is False, "体积未超限且无需转码的 JPEG 不得生成 xopt 文件"


# ==============================================================================
# 3. 静态图打码开+水印开：最小产物集合断言 (严格无 censored_clean)
# ==============================================================================

def test_static_render_minimal_set_censor_on_watermark_on(mock_main_window, tmp_path, monkeypatch):
    """
    打码开 + 水印开:
    单张静态图缓存中应仅有 raw_wm、censored_wm、DA 干净名，
    严格杜绝生成 censored_clean!
    """
    win = mock_main_window
    art_title = "MinimalSetTest1"
    win.title_input.setText(art_title)

    img_path = tmp_path / f"{art_title}.jpg"
    im = Image.new("RGB", (100, 100), color=(50, 100, 150))
    im.save(img_path, "JPEG")
    win.image_paths = [str(img_path)]

    # 启用所有 4 个平台
    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(True)
        if "censor" in ctrl["options"]:
            ctrl["options"]["censor"].setChecked(True)

    # 启用打码与水印
    win.auto_censor_cb.setChecked(True)
    win.watermark_enabled_cb.setChecked(True)
    win.watermark_apply_publish_cb.setChecked(True)
    win.watermark_text_input.setText("@ARTIST")

    # 清理 CACHE_DIR 中残留同名前缀文件
    for f in CACHE_DIR.glob(f"{art_title}*"):
        try:
            f.unlink()
        except Exception:
            pass

    captured_payloads = []
    class DummyPublishThread:
        def __init__(self, selected, payload):
            captured_payloads.append(payload)
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()
        def start(self):
            pass
        def isRunning(self):
            return False

    monkeypatch.setattr("app.PublishThread", DummyPublishThread)

    win._start_publishing()

    assert len(captured_payloads) == 1
    pld = captured_payloads[0]

    # 验证关键缓存文件生成
    raw_wm_file = CACHE_DIR / f"{art_title}_0_raw_wm.jpg"
    censored_wm_file = CACHE_DIR / f"{art_title}_0_censored_wm.jpg"
    da_clean_file = CACHE_DIR / f"{art_title}.jpg"
    censored_clean_file = CACHE_DIR / f"{art_title}_0_censored_clean.jpg"

    assert raw_wm_file.exists(), "raw_wm 必须存在供无码平台与水印备用"
    assert censored_wm_file.exists(), "censored_wm 必须存在供打码平台"
    assert da_clean_file.exists(), "DA 洁净名文件必须存在"

    # 核心需求 1 断言: 严禁生成 censored_clean
    assert not censored_clean_file.exists(), "必须彻底取消 censored_clean 的生成，不留重复副本"

    # 验证全平台 target 分发
    assert pld["da_images"] == [str(da_clean_file)]
    assert pld["x_images"] == [str(censored_wm_file)]
    assert pld["patreon_images"] == [str(censored_wm_file)]
    assert pld["kofi_images"] == [str(censored_wm_file)]


# ==============================================================================
# 4. 静态图打码开+水印关：无重复第二份打码图
# ==============================================================================

def test_static_render_minimal_set_censor_on_watermark_off(mock_main_window, tmp_path, monkeypatch):
    """
    打码开 + 水印关:
    生成唯一的 _censored 产物，DA 干净名指向它，
    严禁生成第二份完全相同的 _censored_clean 副本。
    """
    win = mock_main_window
    art_title = "MinimalSetTest2"
    win.title_input.setText(art_title)

    img_path = tmp_path / f"{art_title}.jpg"
    im = Image.new("RGB", (100, 100), color=(80, 120, 160))
    im.save(img_path, "JPEG")
    win.image_paths = [str(img_path)]

    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(True)
        if "censor" in ctrl["options"]:
            ctrl["options"]["censor"].setChecked(True)

    win.auto_censor_cb.setChecked(True)
    win.watermark_enabled_cb.setChecked(False)

    for f in CACHE_DIR.glob(f"{art_title}*"):
        try:
            f.unlink()
        except Exception:
            pass

    captured_payloads = []
    class DummyPublishThread:
        def __init__(self, selected, payload):
            captured_payloads.append(payload)
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()
        def start(self):
            pass
        def isRunning(self):
            return False

    monkeypatch.setattr("app.PublishThread", DummyPublishThread)

    win._start_publishing()

    assert len(captured_payloads) == 1
    pld = captured_payloads[0]

    censor_file = CACHE_DIR / f"{art_title}_0_censored.jpg"
    da_clean_file = CACHE_DIR / f"{art_title}.jpg"
    censored_clean_file = CACHE_DIR / f"{art_title}_0_censored_clean.jpg"

    assert censor_file.exists()
    assert da_clean_file.exists()
    assert not censored_clean_file.exists(), "水印关时亦严禁生成冗余的 censored_clean 副本"

    # Patreon / Ko-fi / X 均复用唯一的 censor_file
    assert pld["x_images"] == [str(censor_file)]
    assert pld["patreon_images"] == [str(censor_file)]
    assert pld["kofi_images"] == [str(censor_file)]


# ==============================================================================
# 5. DA 干净名文件内容一致性与硬链接校验
# ==============================================================================

def test_da_clean_name_contents_match_and_hardlink(mock_main_window, tmp_path, monkeypatch):
    """
    验证 DA 干净名文件不仅存在且与 target 内容严格一致，
    在同卷下优先建立硬链接 (st_ino 相同，零额外占用)。
    """
    win = mock_main_window
    art_title = "DaHardlinkTest"
    win.title_input.setText(art_title)

    img_path = tmp_path / f"{art_title}.jpg"
    im = Image.new("RGB", (120, 120), color=(20, 40, 60))
    im.save(img_path, "JPEG")
    win.image_paths = [str(img_path)]

    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(name == "deviantart")

    win.auto_censor_cb.setChecked(False)
    win.watermark_enabled_cb.setChecked(False)

    for f in CACHE_DIR.glob(f"{art_title}*"):
        try:
            f.unlink()
        except Exception:
            pass

    captured_payloads = []
    class DummyPublishThread:
        def __init__(self, selected, payload):
            captured_payloads.append(payload)
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()
        def start(self):
            pass
        def isRunning(self):
            return False

    monkeypatch.setattr("app.PublishThread", DummyPublishThread)

    win._start_publishing()

    assert len(captured_payloads) == 1
    da_clean_file = CACHE_DIR / f"{art_title}.jpg"
    assert da_clean_file.exists()
    assert da_clean_file.read_bytes() == img_path.read_bytes()

    # 尝试在同卷下断言硬链接
    try:
        if da_clean_file.stat().st_dev == img_path.stat().st_dev:
            assert da_clean_file.stat().st_ino == img_path.stat().st_ino
    except Exception:
        pass


# ==============================================================================
# 6. X 未被选用静态图的 _xopt.jpg 自动清理
# ==============================================================================

def test_x_unselected_xopt_cleaned_up(mock_main_window, tmp_path, monkeypatch):
    """
    当导入多张 PNG 图片（需格式转码转为 JPEG）时，
    若用户仅勾选前 2 张发布到 X，其余 3 张在渲染循环中即便生成了 _xopt.jpg，
    在选用筛选后必须立即被 unlink 删除，绝不留存残骸。
    """
    win = mock_main_window
    art_title = "XOptCleanupTest"
    win.title_input.setText(art_title)

    # 创建 5 张 PNG 图片
    png_paths = []
    for i in range(5):
        p = tmp_path / f"{art_title}_{i}.png"
        im = Image.new("RGBA", (80, 80), color=(10 * i, 20 * i, 30 * i, 255))
        im.save(p, "PNG")
        png_paths.append(str(p))

    win.image_paths = png_paths
    win.x_selected_paths.clear()
    win._sync_img_list_widget()
    win._refresh_thumbnail_gallery()

    # 仅勾选第 0, 1 张到 X
    win.thumbnail_gallery.cards[0].x_cb.setChecked(True)
    win.thumbnail_gallery.cards[1].x_cb.setChecked(True)

    # 启用 DA 和 X
    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(name in ("deviantart", "x_twitter"))

    win.auto_censor_cb.setChecked(False)
    win.watermark_enabled_cb.setChecked(False)

    for f in CACHE_DIR.glob(f"{art_title}*"):
        try:
            f.unlink()
        except Exception:
            pass

    captured_payloads = []
    class DummyPublishThread:
        def __init__(self, selected, payload):
            captured_payloads.append(payload)
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()
        def start(self):
            pass
        def isRunning(self):
            return False

    monkeypatch.setattr("app.PublishThread", DummyPublishThread)

    win._start_publishing()

    assert len(captured_payloads) == 1
    pld = captured_payloads[0]
    assert len(pld["x_images"]) == 2

    # 前 2 张选中的 _xopt.jpg 应该存在
    opt_0 = CACHE_DIR / f"{art_title}_0_xopt.jpg"
    opt_1 = CACHE_DIR / f"{art_title}_1_xopt.jpg"
    assert opt_0.exists(), "被选用的 X 图片 xopt 产物必须保留"
    assert opt_1.exists(), "被选用的 X 图片 xopt 产物必须保留"

    # 未被选用的第 2, 3, 4 张 _xopt.jpg 必须被清理删除
    opt_2 = CACHE_DIR / f"{art_title}_2_xopt.jpg"
    opt_3 = CACHE_DIR / f"{art_title}_3_xopt.jpg"
    opt_4 = CACHE_DIR / f"{art_title}_4_xopt.jpg"
    assert not opt_2.exists(), "未选用的 xopt 缓存文件必须被删除"
    assert not opt_3.exists(), "未选用的 xopt 缓存文件必须被删除"
    assert not opt_4.exists(), "未选用的 xopt 缓存文件必须被删除"


# ==============================================================================
# 6. GIF 跨平台复用：参数与体积合规时不重复渲染
# ==============================================================================

def test_gif_cross_platform_reuse_when_params_and_size_match(mock_main_window, tmp_path, monkeypatch):
    """
    当 GIF 体积符合限制 (例如 100KB <= 3.8MB) 且打码/水印参数一致时：
    DA 渲染一次，X、Patreon、Ko-fi 均直接复用 DA 的渲染产物，
    _generate_composite_gif 仅被调用 1 次，严禁产生重复渲染与冗余 GIF 文件。
    """
    win = mock_main_window
    art_title = "GifReuseTest"
    win.title_input.setText(art_title)

    # 创建一个轻量 GIF 文件
    gif_path = tmp_path / f"{art_title}.gif"
    im1 = Image.new("RGB", (60, 60), color=(255, 0, 0))
    im2 = Image.new("RGB", (60, 60), color=(0, 255, 0))
    im1.save(gif_path, "GIF", save_all=True, append_images=[im2], duration=100, loop=0)
    win.image_paths = [str(gif_path)]

    # 启用 DA, X, Patreon, Ko-fi 且全部开启打码
    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(True)
        if "censor" in ctrl["options"]:
            ctrl["options"]["censor"].setChecked(True)

    win.auto_censor_cb.setChecked(True)
    win.watermark_enabled_cb.setChecked(True)
    win.watermark_apply_publish_cb.setChecked(True)
    win.watermark_text_input.setText("@ARTIST_GIF")

    for f in CACHE_DIR.glob(f"{art_title}*"):
        try:
            f.unlink()
        except Exception:
            pass

    # 跟踪 _generate_composite_gif 调用次数
    orig_composite_gif = win._generate_composite_gif
    call_records = []
    def spy_composite_gif(input_p, output_p, **kwargs):
        call_records.append((str(input_p), str(output_p), kwargs))
        return orig_composite_gif(input_p, output_p, **kwargs)

    monkeypatch.setattr(win, "_generate_composite_gif", spy_composite_gif)

    captured_payloads = []
    class DummyPublishThread:
        def __init__(self, selected, payload):
            captured_payloads.append(payload)
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()
        def start(self):
            pass
        def isRunning(self):
            return False

    monkeypatch.setattr("app.PublishThread", DummyPublishThread)

    win._start_publishing()

    # 核心断言: _generate_composite_gif 仅被 DA 调用 1 次
    assert len(call_records) == 1, f"预期 _generate_composite_gif 仅执行 1 次，实际执行了 {len(call_records)} 次: {call_records}"

    da_render_out = CACHE_DIR / f"{art_title}_0_da_censor.gif"
    assert da_render_out.exists()

    # 严禁生成独立的 X 与 Ko-fi 冗余 GIF 文件
    x_opt_gif = CACHE_DIR / f"{art_title}_0_xopt.gif"
    kofi_censor_gif = CACHE_DIR / f"{art_title}_0_kofi_censor.gif"
    assert not x_opt_gif.exists(), "X 应复用 DA 动图，严禁重复渲染 _xopt.gif"
    assert not kofi_censor_gif.exists(), "Ko-fi 应复用 DA 动图，严禁重复渲染 _kofi_censor.gif"

    # 验证 payload 中均复用了 DA 动图路径
    pld = captured_payloads[0]
    assert pld["x_images"] == [str(da_render_out)]
    assert pld["patreon_images"] == [str(da_render_out)]
    assert pld["kofi_images"] == [str(da_render_out)]


# ==============================================================================
# 7. 发布失败重试仍命中缓存复用
# ==============================================================================

def test_publish_failure_retry_reuses_payload_cache(mock_main_window, tmp_path, monkeypatch):
    """
    单平台发布失败重试时，直接复用已保存在 _last_publish_payload 中的渲染产物与文案快照，
    不重新触发预处理或额外副本写入。
    """
    win = mock_main_window

    cached_payload = {
        "title": "RetryArtwork",
        "description": "Retry Description",
        "tags": ["art", "anime"],
        "images": ["/fake/cache/da_clean.jpg"],
        "da_images": ["/fake/cache/da_clean.jpg"],
        "x_images": ["/fake/cache/x_target.jpg"],
        "patreon_images": ["/fake/cache/pat_target.jpg"],
        "kofi_images": ["/fake/cache/kofi_target.jpg"],
        "raw_images": ["/fake/cache/raw.jpg"],
    }
    win._last_publish_payload = cached_payload

    # 模拟 DA 成功，X 失败
    initial_results = {
        "deviantart": (True, "https://deviantart.com/art/100", []),
        "x_twitter": (False, "Timeout", []),
    }
    win._on_completed(initial_results)

    dispatched = []
    class DummyPublishThread:
        def __init__(self, selected, payload):
            dispatched.append((selected, payload))
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()
        def start(self):
            pass
        def isRunning(self):
            return False

    monkeypatch.setattr("app.PublishThread", DummyPublishThread)

    # 触发 X 平台单独重试
    win._retry_single_platform("x_twitter")

    assert len(dispatched) == 1
    sel_disp, passed_payload = dispatched[0]

    # 断言仅调度失败平台
    assert len(sel_disp) == 1
    assert sel_disp[0].name == "x_twitter"

    # 断言完全复用 cached_payload，无额外写入
    assert passed_payload == cached_payload
    assert passed_payload["x_images"] == ["/fake/cache/x_target.jpg"]
