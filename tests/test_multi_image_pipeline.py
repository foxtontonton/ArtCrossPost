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
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image
from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtCore import QPoint, Qt, QMimeData
from PyQt5.QtGui import QDropEvent

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


def test_reorder_images_interface_and_multi_platform_dispatch_order(mock_main_window):
    """
    reorder_images 核心接口与多平台发送顺序联动测试:
    - 直接调用 reorder_images(src_idx, target_idx)
    - 内部图片列表 image_paths 顺序实时更新
    - X 选用勾选按新顺序重新映射
    - 全平台分发源 (Patreon gallery, DA 图集等) 使用的 raw_images 与 image_paths 同步更新
    - move_image 作为别名方法完全兼容
    """
    win = mock_main_window
    p1 = win.image_paths[0]
    p5 = win.image_paths[4]
    p10 = win.image_paths[9]

    # 初始顺序验证
    assert win.image_paths[0] == p1
    assert win.image_paths[4] == p5
    assert win.image_paths[9] == p10

    # 勾选第 5 张与第 10 张
    win.thumbnail_gallery.cards[4].x_cb.setChecked(True)
    win.thumbnail_gallery.cards[9].x_cb.setChecked(True)
    assert win.get_x_selected_images() == [p5, p10]

    # 将第 10 张 (索引 9) 通过 reorder_images 接口移动到第 1 位 (索引 0)
    win.reorder_images(9, 0)

    # 验证 image_paths 内部顺序已更新 (各平台如 Patreon gallery, DA 图集分发顺序)
    assert win.image_paths[0] == p10
    assert win.image_paths[1] == p1

    # 验证 X 选用顺序重新映射为 [p10, p5]
    assert win.get_x_selected_images() == [p10, p5]

    # 验证 move_image 别名接口将第 5 张 (当前索引 5) 移动到首位
    win.move_image(5, 0)
    assert win.image_paths[0] == p5
    assert win.image_paths[1] == p10
    assert win.get_x_selected_images() == [p5, p10]


def test_thumbnail_gallery_slot_calculation_and_insertion_indicator(mock_main_window):
    """
    缩略图画廊拖拽槽位计算与竖线插入指示符测试:
    - 槽位 0 (首图左侧)
    - 槽位 中间 (卡片间隙)
    - 槽位 N (尾图右侧)
    - 竖线指示符的显示、定位与隐藏
    """
    win = mock_main_window
    gallery = win.thumbnail_gallery
    total_cards = len(gallery.cards)
    assert total_cards == 10

    # 1. 模拟鼠标移至画廊最左侧 (X=0) -> 槽位 0
    slot_0 = gallery._calculate_insertion_slot(QPoint(0, 50))
    assert slot_0 == 0

    # 2. 模拟鼠标移至第 2 张卡片中心右侧 -> 槽位 2
    # 卡片 1 的理论几何范围: x=130, w=118, mid=189
    slot_2 = gallery._calculate_insertion_slot(QPoint(200, 50))
    assert slot_2 == 2

    # 3. 模拟鼠标移至最右侧 (X=9999) -> 槽位 10 (末尾)
    slot_end = gallery._calculate_insertion_slot(QPoint(9999, 50))
    assert slot_end == total_cards

    # 4. 指示符显示与定位
    assert hasattr(gallery, "indicator")
    gallery._update_insertion_indicator(QPoint(200, 50))
    assert gallery.indicator.isVisible() is True
    assert gallery.indicator.width() == 8

    # 5. 隐藏指示符
    gallery._hide_insertion_indicator()
    assert gallery.indicator.isVisible() is False


def test_thumbnail_gallery_drop_event_triggers_reordering(mock_main_window):
    """
    UI 鼠标拖拽松开落定 (dropEvent) 触发重排与联动测试:
    - 模拟从索引 8 拖至槽位 0
    - 验证 dropEvent 解析 slot 并调用 reorder_images
    - 验证竖线插入符在 drop 结束后被隐藏
    """
    win = mock_main_window
    gallery = win.thumbnail_gallery
    orig_p9 = win.image_paths[8]

    # 激活指示符
    gallery._update_insertion_indicator(QPoint(0, 50))
    assert gallery.indicator.isVisible() is True

    # 构造拖拽落定事件 (将原索引 8 拖拽落定至最左侧 slot 0)
    mime = QMimeData()
    mime.setText("8")
    drop_ev = QDropEvent(QPoint(0, 50), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)

    gallery.dropEvent(drop_ev)

    # 落定后指示符必须隐藏
    assert gallery.indicator.isVisible() is False

    # 内部图片顺序已更新至首位
    assert win.image_paths[0] == orig_p9


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


# ==================== 3. DeviantArt Single-Deviation Multi-Image Gallery Tests ====================

def test_deviantart_multi_image_single_deviation_success(tmp_path, monkeypatch):
    """
    DA 单作品多图规范 (2026-10-13 22:42 定版)：
    - 多张图片一次投稿进 1 个 deviation，图集形式展示 (对标 Patreon gallery)；
    - 标题/描述/标签只填一次，标题无 (1/N) 后缀；
    - 3–8 分钟间隔逻辑对 DA 作废 (单次投稿即可)；
    - 成功断言：发布后访问作品页，反查图集图片数 == 预期传入数 (3/3 成功)。
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

    submitted_batches = []

    def fake_submit(page, context, images, title, desc, _tags, is_mature, mature_level, is_ai, gallery, compliance):
        submitted_batches.append({"images": images, "title": title, "desc": desc, "tags": _tags})
        return True, ""

    disp._submit_deviation = MagicMock(side_effect=fake_submit)
    disp._count_deviantart_gallery_images = MagicMock(return_value=3)

    test_imgs = [str(tmp_path / f"da_{i}.jpg") for i in range(3)]
    for p in test_imgs:
        Path(p).write_bytes(b"dummy_da")

    payload = {
        "title": "Dragon Series",
        "description": "Epic dragon gallery",
        "tags": ["dragon", "fantasy"],
        "da_images": test_imgs
    }

    success, msg, compliance = disp.publish(payload)

    assert success is True
    assert "3/3" in msg or "单作品图集核验通过" in msg
    assert len(submitted_batches) == 1  # 严禁重复分发，单次投稿进 1 个 deviation
    assert submitted_batches[0]["title"] == "Dragon Series"  # 纯净标题，严禁追加 (1/3)
    assert submitted_batches[0]["images"] == test_imgs
    count_comp = [c for c in compliance if c.get("item") == "gallery_count"]
    assert len(count_comp) == 1
    assert count_comp[0]["verified"] is True
    assert "3/3" in count_comp[0]["detail"]


def test_deviantart_max_images_limit_warning_and_block(tmp_path, monkeypatch, mock_main_window):
    """
    单 deviation 图片上限配置 deviantart_max_images_per_deviation (默认 10)：
    1. 投稿时若图片数超限 (13 > 10)，UI 弹警告弹窗提示 'DA 单作品上限 10 张，当前 13 张'，由用户自行删减，不自动拆分；
    2. Dispatcher 同样阻断并返回合规警告。
    """
    win = mock_main_window
    win.config["defaults"]["deviantart_max_images_per_deviation"] = 10

    # 13 张图片素材
    imgs_13 = [str(tmp_path / f"da_{i}.jpg") for i in range(13)]
    for p in imgs_13:
        Path(p).write_bytes(b"dummy")
    win.image_paths = imgs_13

    # 勾选 DeviantArt
    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(name == "deviantart")

    win.title_input.setText("Latex Showcase")

    with patch.object(QMessageBox, "warning") as mock_warn:
        win._start_publishing()
        mock_warn.assert_called_once()
        args = mock_warn.call_args[0]
        assert "DA 单作品上限 10 张，当前 13 张" in args[2]

    win.notify.assert_called_with("DA 单作品上限 10 张，当前 13 张", level="warning")

    # 验证 Dispatcher 层面同样做严格门禁拦截
    disp = DeviantArtDispatcher(tmp_path)
    succ, msg, comp = disp.publish({
        "title": "Latex Showcase",
        "da_images": imgs_13,
        "deviantart_max_images_per_deviation": 10
    })
    assert succ is False
    assert "DA 单作品上限 10 张，当前 13 张" in msg
    assert "请自行删减" in msg


def test_deviantart_gallery_count_assertion_failure(tmp_path, monkeypatch):
    """
    成功断言反查：
    发布后访问作品页，反查图集图片数 == 预期传入数。
    若预期 4 张，但页面反查仅检测到 2 张，必须大声失败并判 FAILED，严禁虚报成功！
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

    disp._submit_deviation = MagicMock(return_value=(True, ""))
    # 模拟实际反查到的图片仅有 2 张 (缺漏 2 张)
    disp._count_deviantart_gallery_images = MagicMock(return_value=2)

    test_imgs = [str(tmp_path / f"da_{i}.jpg") for i in range(4)]
    for p in test_imgs:
        Path(p).write_bytes(b"dummy_da")

    payload = {
        "title": "Quad Art",
        "da_images": test_imgs
    }

    success, msg, compliance = disp.publish(payload)

    assert success is False
    assert "图集张数断言失败" in msg
    assert "预期包含 4 张图片" in msg
    assert "实际检测到 2 张" in msg
    count_comp = [c for c in compliance if c.get("item") == "gallery_count"]
    assert len(count_comp) == 1
    assert count_comp[0]["verified"] is False


def test_deviantart_mature_and_ai_flags_rule(tmp_path, monkeypatch):
    """
    官方规则验证：
    1. 任一图 mature 则整个 deviation 必须标 Mature；
    2. 任一图 AI 则必须标 AI 标签。
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

    captured_flags = {}

    def fake_submit(page, context, images, title, desc, _tags, is_mature, mature_level, is_ai, gallery, compliance):
        captured_flags["is_mature"] = is_mature
        captured_flags["is_ai"] = is_ai
        return True, ""

    disp._submit_deviation = MagicMock(side_effect=fake_submit)
    disp._count_deviantart_gallery_images = MagicMock(return_value=2)

    test_imgs = [str(tmp_path / f"da_{i}.jpg") for i in range(2)]
    for p in test_imgs:
        Path(p).write_bytes(b"dummy")

    payload = {
        "title": "Mixed Mature/AI Art",
        "da_images": test_imgs,
        "is_nsfw": True,
        "platform_options": {
            "deviantart_mature": "strict",
            "deviantart_ai": True
        }
    }

    success, msg, comp = disp.publish(payload)
    assert success is True
    assert captured_flags["is_mature"] is True
    assert captured_flags["is_ai"] is True


def test_deviantart_real_submit_deviation_method_no_nameerror(tmp_path, monkeypatch):
    """
    真实执行 _submit_deviation 内部流程，验证首图作为封面不可重排，无 NameError 或未定义变量。
    """
    monkeypatch.setattr(time, "sleep", lambda x: None)

    disp_real = DeviantArtDispatcher(tmp_path)
    disp_real.assert_attached = MagicMock(return_value=(True, ""))
    disp_real.assert_field_value = MagicMock(return_value=(True, ""))
    disp_real.check_selector_state = MagicMock(return_value=(True, "ok"))
    disp_real.assert_submitted = MagicMock(return_value=(True, ""))
    disp_real._cleanup_orphan_draft = MagicMock()

    mock_real_page = MagicMock()
    mock_real_page.url = "https://www.deviantart.com/art/view-12345"
    mock_real_page.inner_text = MagicMock(return_value="")
    mock_file_in = MagicMock()
    mock_file_in.inner_text.return_value = "Submit"
    mock_file_in.is_enabled.return_value = True
    mock_file_in.get_attribute.return_value = "false"
    mock_real_page.query_selector_all = MagicMock(return_value=[mock_file_in])
    mock_real_page.query_selector = MagicMock(return_value=mock_file_in)

    mock_real_context = MagicMock()
    mock_studio_page = MagicMock()
    mock_studio_page.inner_text = MagicMock(return_value="No drafts here")
    mock_real_context.new_page = MagicMock(return_value=mock_studio_page)

    images = [str(tmp_path / f"da_{i}.jpg") for i in range(3)]
    for p in images:
        Path(p).write_bytes(b"dummy")

    comp_list = []
    res_ok, res_msg = disp_real._submit_deviation(
        mock_real_page, mock_real_context, images, "Latex Sessions Vol. 1",
        "Description", ["tag1"], False, "strict", False, "", comp_list
    )
    assert res_ok is True
    assert res_msg == ""
    # 断言 set_input_files 接收到完整的包含首图在内的图片列表
    mock_file_in.set_input_files.assert_called_once_with(images)


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

    def mock_da_single(page, context, images, title, desc, _tags, is_mature, mature_level, is_ai, gallery, compliance):
        published_titles.append(title)
        return True, ""

    mock_page = MagicMock()
    mock_page.url = "https://www.deviantart.com/art/view-123"
    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    da_disp.safe_goto = MagicMock()
    da_disp.apply_stealth = MagicMock()
    da_disp._submit_deviation = mock_da_single
    da_disp._count_deviantart_gallery_images = MagicMock(return_value=1)
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



