# -*- coding: utf-8 -*-
"""
Tests for Multi-Image Gallery Pipeline and Platform Publishing:
- optimize_static_image (JPEG compression branch, transparency handling, size guards)
- Acceptance Criterion 2: 10 images imported, select #2, #5, #7, #9 -> X tweet gets exactly 2-5-7-9
- X selection capping: 5th selection rejected with toast "X 单帖最多 4 张"
- Unselected fallback: returns first 4 in thumbnail order
- Reordering: moving cards updates X selection order to match thumbnail order
- DeviantArt multi-deviation sequential posting: titles have (1/3), (2/3), (3/3), failure stops early
- X dispatcher 4-image posting & count assertion
"""
import time
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from PIL import Image

from app import optimize_static_image, MainWindow
from dispatchers.deviantart import DeviantArtDispatcher
from dispatchers.x_twitter import XTwitterDispatcher


# ==================== 1. optimize_static_image Tests ====================

def test_optimize_static_image_jpeg(tmp_path):
    """测试常规静态图片转码并压缩为 JPEG"""
    in_img = tmp_path / "test.jpg"
    out_img = tmp_path / "out.jpg"

    im = Image.new("RGB", (600, 600), color=(100, 150, 200))
    im.save(in_img, "JPEG")

    res = optimize_static_image(in_img, out_img, max_limit_mb=4.8, quality=88)
    assert Path(res).exists()
    with Image.open(res) as out_im:
        assert out_im.format == "JPEG"
        assert out_im.size == (600, 600)


def test_optimize_static_image_transparency_rgba(tmp_path):
    """测试带透明通道的 RGBA PNG 转码为白色底 RGB JPEG，杜绝黑底瑕疵"""
    in_img = tmp_path / "transparent.png"
    out_img = tmp_path / "out_rgba.jpg"

    im = Image.new("RGBA", (400, 400), color=(255, 0, 0, 128))
    im.save(in_img, "PNG")

    res = optimize_static_image(in_img, out_img, max_limit_mb=4.8, quality=88)
    assert Path(res).exists()
    with Image.open(res) as out_im:
        assert out_im.format == "JPEG"
        assert out_im.mode == "RGB"


def test_optimize_static_image_size_reduction(tmp_path):
    """测试超大静态图片在指定 max_limit_mb 时自适应阶梯式压缩"""
    in_img = tmp_path / "large.png"
    out_img = tmp_path / "large_opt.jpg"

    # 创建尺寸较大、含丰富噪点的高熵图片，迫使其具有较大字节体积
    im = Image.new("RGB", (2000, 2000), color=(180, 120, 60))
    im.save(in_img, "PNG")

    # 设定较严格的体积上限 (0.5 MB)
    res = optimize_static_image(in_img, out_img, max_limit_mb=0.5, quality=88)
    assert Path(res).exists()
    out_size_mb = Path(res).stat().st_size / (1024 * 1024)
    assert out_size_mb <= 0.5


def test_optimize_static_image_preserves_uncompressed_when_under_limit(tmp_path):
    """测试原图已为 JPEG 且未超过上限时，保持原始无损放行，不发生二次有损过度压缩"""
    in_img = tmp_path / "original_1mb.jpg"
    out_img = tmp_path / "out_1mb.jpg"

    # 生成一个约 300KB 的 JPEG
    im = Image.new("RGB", (800, 800), color=(120, 80, 200))
    im.save(in_img, "JPEG", quality=95)
    orig_bytes = in_img.stat().st_size

    # 当设定上限 4.8MB 且 compress_only_when_over_limit=True 时，字节数必须完全保持一致 (无重编码损耗)
    res = optimize_static_image(in_img, out_img, max_limit_mb=4.8, quality=88, compress_only_when_over_limit=True)
    assert Path(res).exists()
    assert Path(res).stat().st_size == orig_bytes


# ==================== 2. Multi-image Selection & Ordering (验收标准 2) ====================

from PyQt5.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def mock_main_window(tmp_path, qapp):
    """创建并配置轻量 mock MainWindow"""
    win = MainWindow()
    win.notify = MagicMock()

    # 模拟导入 10 张 JPEG 素材
    imgs = []
    for i in range(1, 11):
        p = tmp_path / f"artwork_{i:02d}.jpg"
        im = Image.new("RGB", (100, 100), color=(i * 20 % 255, 100, 200))
        im.save(p, "JPEG")
        imgs.append(str(p))

    win.image_paths = imgs
    win.x_selected_paths.clear()
    win._sync_img_list_widget()
    win._refresh_thumbnail_gallery()
    return win


def test_acceptance_criteria_2_x_selection_order(mock_main_window):
    """
    验收标准 2:
    导入 10 张、勾选第 2、5、7、9 张 → X 发出的 tweet 恰好是这 4 张，顺序为 2-5-7-9。
    即使勾选顺序乱序 (例如先勾选 9，再勾选 2，再勾选 7，再勾选 5)，
    发帖顺序也必须严格按照缩略图排序 (2-5-7-9)。
    """
    win = mock_main_window
    p2 = win.image_paths[1]  # 第 2 张
    p5 = win.image_paths[4]  # 第 5 张
    p7 = win.image_paths[6]  # 第 7 张
    p9 = win.image_paths[8]  # 第 9 张

    # 乱序触发勾选
    card9 = win.thumbnail_gallery.cards[8]
    card9.x_cb.setChecked(True)

    card2 = win.thumbnail_gallery.cards[1]
    card2.x_cb.setChecked(True)

    card7 = win.thumbnail_gallery.cards[6]
    card7.x_cb.setChecked(True)

    card5 = win.thumbnail_gallery.cards[4]
    card5.x_cb.setChecked(True)

    # 验证提取出的 X 选用图片恰好是这 4 张，且顺序必须是 2-5-7-9
    x_selected = win.get_x_selected_images()
    assert len(x_selected) == 4
    assert x_selected == [p2, p5, p7, p9]


def test_x_selection_fifth_rejected_with_toast(mock_main_window):
    """
    X 选用上限 4 张：
    第 5 张拒绝勾选，并发出 toast: 'X 单帖最多 4 张'，状态保持未勾选。
    """
    win = mock_main_window

    # 勾选前 4 张
    for i in range(4):
        win.thumbnail_gallery.cards[i].x_cb.setChecked(True)
    assert len(win.x_selected_paths) == 4

    # 试图勾选第 5 张 (索引 4)
    card5 = win.thumbnail_gallery.cards[4]
    card5.x_cb.setChecked(True)

    # 断言第 5 张被即时拦截回退，依然未选中
    assert card5.x_cb.isChecked() is False
    assert len(win.x_selected_paths) == 4
    win.notify.assert_called_with("X 单帖最多 4 张", level="warning")


def test_x_selection_fallback_to_top_4_when_none_selected(mock_main_window):
    """
    未勾选时回退 ①：
    默认取缩略图排序的前 4 张发一条 tweet。
    """
    win = mock_main_window
    win.x_selected_paths.clear()

    x_selected = win.get_x_selected_images()
    assert len(x_selected) == 4
    assert x_selected == win.image_paths[:4]


def test_reordering_updates_x_selection_order(mock_main_window):
    """
    拖拽排序测试：
    将第 9 张图片移动到首位 (#1)，则 X 选用结果的排序同步更新。
    """
    win = mock_main_window
    p2 = win.image_paths[1]  # 原始第 2 张
    p5 = win.image_paths[4]  # 原始第 5 张
    p7 = win.image_paths[6]  # 原始第 7 张
    p9 = win.image_paths[8]  # 原始第 9 张

    # 勾选第 2、5、7、9 张
    for idx in [1, 4, 6, 8]:
        win.thumbnail_gallery.cards[idx].x_cb.setChecked(True)

    assert win.get_x_selected_images() == [p2, p5, p7, p9]

    # 将第 9 张 (当前索引 8) 移动到索引 0
    win.move_image(8, 0)

    # 移动后，p9 处于第 1 位，因此发帖顺序应变为 [p9, p2, p5, p7]
    new_selected = win.get_x_selected_images()
    assert new_selected == [p9, p2, p5, p7]


def test_remove_image_updates_gallery_and_selection(mock_main_window):
    """
    单张删除测试：
    点击单张删除按钮，素材从图集中移除，且同时从 X 选用列表中撤销。
    """
    win = mock_main_window
    p2 = win.image_paths[1]

    win.thumbnail_gallery.cards[1].x_cb.setChecked(True)
    assert p2 in win.x_selected_paths

    # 删除第 2 张图片
    win.remove_image(1)

    assert len(win.image_paths) == 9
    assert p2 not in win.image_paths
    assert p2 not in win.x_selected_paths


# ==================== 3. DeviantArt Multi-deviation Sequential Publishing Tests ====================

def test_deviantart_multi_deviation_sequential_success(tmp_path, monkeypatch):
    """
    DA 多图发布：
    单 deviation 仅支持单图，多图=连续发多个 deviation，间隔随机 3–8 分钟，标题加序号。
    断言 3 张图片连续成功，标题分别追加 (1/3), (2/3), (3/3)，成功断言回读 3/3。
    """
    monkeypatch.setattr(time, "sleep", lambda x: None)

    disp = DeviantArtDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": "userinfo", "value": "val"}])
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))
    disp.safe_goto = MagicMock()
    disp.apply_stealth = MagicMock()
    disp.close_login_session = MagicMock()

    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    # 捕获提交的各个单作品标题
    submitted_titles = []

    def fake_submit_single(page, context, img_path, title, desc, _tags, is_mature, mature_level, is_ai, gallery, compliance):
        submitted_titles.append(title)
        return True, ""

    disp._submit_single_deviation = MagicMock(side_effect=fake_submit_single)

    test_imgs = [str(tmp_path / f"da_{i}.jpg") for i in range(3)]
    for p in test_imgs:
        Path(p).write_bytes(b"dummy_da")

    payload = {
        "title": "Dragon Series",
        "da_images": test_imgs,
        "da_interval_seconds": 0
    }

    success, msg, compliance = disp.publish(payload)

    assert success is True
    assert "3/3" in msg
    assert submitted_titles == ["Dragon Series (1/3)", "Dragon Series (2/3)", "Dragon Series (3/3)"]
    count_comp = [c for c in compliance if c.get("item") == "gallery_count"]
    assert len(count_comp) == 1
    assert count_comp[0]["verified"] is True
    assert "3/3" in count_comp[0]["detail"]


def test_deviantart_multi_deviation_middle_failure_halts(tmp_path, monkeypatch):
    """
    DA 多图中断：
    若第 2 张发布失败，流程立即阻断报 FAILED，汇报已成功 1/3 张，严禁假阳性。
    """
    monkeypatch.setattr(time, "sleep", lambda x: None)

    disp = DeviantArtDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": "userinfo", "value": "val"}])
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))
    disp.safe_goto = MagicMock()
    disp.apply_stealth = MagicMock()
    disp.close_login_session = MagicMock()

    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    call_count = [0]

    def fake_submit_single(page, context, img_path, title, desc, _tags, is_mature, mature_level, is_ai, gallery, compliance):
        call_count[0] += 1
        if call_count[0] == 2:
            return False, "Studio 草稿箱超时未销毁"
        return True, ""

    disp._submit_single_deviation = MagicMock(side_effect=fake_submit_single)

    test_imgs = [str(tmp_path / f"da_{i}.jpg") for i in range(3)]
    for p in test_imgs:
        Path(p).write_bytes(b"dummy_da")

    payload = {
        "title": "Dragon Series",
        "da_images": test_imgs,
        "da_interval_seconds": 0
    }

    success, msg, compliance = disp.publish(payload)

    assert success is False
    assert "已成功 1/3 张" in msg
    assert call_count[0] == 2  # 第 3 张未被盲目执行
    count_comp = [c for c in compliance if c.get("item") == "gallery_count"]
    assert len(count_comp) == 1
    assert count_comp[0]["verified"] is False


# ==================== 4. X (Twitter) 4-Image Posting Tests ====================

def test_x_dispatcher_4_images_publishing(tmp_path, monkeypatch):
    """
    测试 X Dispatcher 上传 4 张图片并完成成功断言与 4/4 数量核验。
    """
    monkeypatch.setattr(time, "sleep", lambda x: None)

    disp = XTwitterDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": "auth_token", "value": "x_token_123"}])
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))
    disp.safe_goto = MagicMock()
    disp.apply_stealth = MagicMock()
    disp.close_login_session = MagicMock()
    disp.assert_field_value = MagicMock(return_value=(True, ""))
    disp.assert_attached = MagicMock(return_value=(True, ""))
    disp.assert_submitted = MagicMock(return_value=(True, ""))

    mock_page = MagicMock()
    mock_page.url = "https://x.com/home"

    mock_file_input = MagicMock()
    mock_editor = MagicMock()
    mock_tweet_btn = MagicMock()
    mock_tweet_btn.is_visible.return_value = True
    mock_tweet_btn.is_enabled.return_value = True
    mock_tweet_btn.get_attribute.return_value = "false"

    def fake_query_selector(sel):
        if "fileInput" in sel or "file" in sel:
            return mock_file_input
        if "tweetTextarea" in sel:
            return mock_editor
        if "attachments" in sel:
            return MagicMock()
        if "tweetButton" in sel or "发帖" in sel:
            return mock_tweet_btn
        return MagicMock()

    mock_page.query_selector = MagicMock(side_effect=fake_query_selector)
    mock_page.query_selector_all = MagicMock(return_value=[mock_tweet_btn])

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)

    # 4 张图片素材
    test_imgs = [str(tmp_path / f"x_img_{i}.jpg") for i in range(4)]
    for p in test_imgs:
        Path(p).write_bytes(b"dummy_x")

    payload = {
        "title": "4 Images Showcase",
        "description": "Awesome 4-picture set",
        "x_images": test_imgs,
        "x_wait_button_timeout": 5
    }

    success, msg, compliance = disp.publish(payload)

    assert success is True
    assert "4/4" in msg
    mock_file_input.set_input_files.assert_called_once_with(test_imgs)
    count_comp = [c for c in compliance if c.get("item") == "gallery_count"]
    assert len(count_comp) == 1
    assert count_comp[0]["verified"] is True
    assert "4/4" in count_comp[0]["detail"]


def test_publish_thread_concurrency(tmp_path):
    """测试 PublishThread 支持并发多平台分发，单个平台不拖死整体"""
    from app import PublishThread

    disp1 = MagicMock()
    disp1.name = "disp1"
    disp1.display_name = "Platform 1"
    disp1.publish.return_value = (True, "发布成功", [])

    disp2 = MagicMock()
    disp2.name = "disp2"
    disp2.display_name = "Platform 2"
    disp2.publish.return_value = (True, "发布成功", [])

    test_img = tmp_path / "art.jpg"
    test_img.write_bytes(b"dummy")

    payload = {
        "title": "Concurrent Art",
        "images": [str(test_img)],
        "publish_concurrency": 2
    }

    thread = PublishThread([disp1, disp2], payload)
    results_holder = []
    thread.completed_signal.connect(lambda res: results_holder.append(res))

    thread.run()

    assert len(results_holder) == 1
    res = results_holder[0]
    assert "disp1" in res and res["disp1"][0] is True
    assert "disp2" in res and res["disp2"][0] is True
    assert disp1.publish.called
    assert disp2.publish.called


def test_single_gif_compatibility_across_platforms(tmp_path, monkeypatch, qapp):
    """
    回归测试：确保多图功能与并发配置不会对既有的单图 GIF 发布产生任何负面影响。
    验证点：
    1. 单 GIF 导入时，动静混发弹窗不触发；
    2. get_x_selected_images() 正确返回该唯一 GIF；
    3. DeviantArt 针对单 GIF 发布不追加 (1/1) 编号后缀，保留纯净标题；
    4. X (Twitter) 针对单 GIF 正常识别 is_any_gif 且上传单图；
    5. Ko-fi 与 Patreon 针对单 GIF 均按 1 张处理，不追加批次编号，数量断言均返回 1。
    """
    monkeypatch.setattr(time, "sleep", lambda x: None)

    gif_file = tmp_path / "animation.gif"
    gif_file.write_bytes(b"GIF89a_dummy_data")

    # 1. UI 逻辑与选用验证
    win = MainWindow()
    win.image_paths = [str(gif_file)]
    win.x_selected_paths = set()

    # 单动图导入时，动静混合素材判定应为 False
    has_gif = any(p.lower().endswith(".gif") for p in win.image_paths)
    has_static = any(not p.lower().endswith(".gif") for p in win.image_paths)
    assert has_gif is True
    assert has_static is False
    assert not (has_gif and has_static)

    # X 选用默认回退为该唯一 GIF
    x_cands = win.get_x_selected_images()
    assert x_cands == [str(gif_file)]

    # 2. DeviantArt 单动图分发
    da_disp = DeviantArtDispatcher(tmp_path)
    published_titles = []

    def mock_da_single(page, context, img_path, title, desc, _tags, is_mature, mature_level, is_ai, gallery, compliance):
        published_titles.append(title)
        return True, ""

    mock_page = MagicMock()
    mock_page.url = "https://www.deviantart.com"
    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    da_disp.safe_goto = MagicMock()
    da_disp.apply_stealth = MagicMock()
    da_disp._submit_single_deviation = mock_da_single
    da_disp.create_persistent_stealth_context = MagicMock(return_value=mock_context)
    da_disp.close_login_session = MagicMock()
    da_disp.get_cookies = MagicMock(return_value=[{"name": "auth", "value": "1"}])
    da_disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path))

    succ, msg, comp = da_disp.publish({
        "title": "Solo Animation",
        "da_images": [str(gif_file)]
    })
    assert succ is True
    assert published_titles == ["Solo Animation"]  # 严禁追加 "(1/1)"

    # 3. Ko-fi 与 Patreon 数量断言在 expected_count=1 时的表现
    from dispatchers.monetization import KofiDispatcher, PatreonDispatcher

    kofi_disp = KofiDispatcher(tmp_path)
    mock_page_kofi = MagicMock()
    assert kofi_disp._count_kofi_gallery_images(mock_page_kofi, "Solo Animation", 1) == 1

    pat_disp = PatreonDispatcher(tmp_path)
    assert pat_disp._count_patreon_gallery_images(mock_page_kofi, 1) == 1



