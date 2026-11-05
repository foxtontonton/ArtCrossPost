# -*- coding: utf-8 -*-
"""
Tests for Delayed Public Wave Subsystem (延迟公开波队列)
覆盖 Section 6 全部 10 项要求:
1. 入队判定 (Patreon 成功才入队 / 失败不入队)
2. 快照与源草稿脱钩 (源文件修改/删除不影响队列项自包含副本)
3. 到期筛选与逾期补发判定 (逾期项正确识别与补发日志)
4. 单平台失败不阻塞其余平台且状态正确 (隔离失败平台至 last_error，其余平台成功)
5. 总开关关闭时零副作用 (不建文件、不启定时器、主窗口界面一致)
6. 延迟设置解析 (分钟/小时/天三种单位换算正确)
7. 免费平台降档换算 (×0.5 取整，1792×2304 → 896×1152；Patreon 墙下展示图与附件不缩放)
8. B 页附件门禁 (附件挂载失败不入队、标记「缺附件」；严禁使用 *_raw_wm.* 渲染件)
9. Patreon 预告图变体来源 (「公开安全」独立预设，与免费平台/轻码件像素不同)
10. 标签页 A 行为回归
"""

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from censor_engine import CensorEngine
from delayed_wave_executor import DelayedWaveExecutor
from delayed_wave_manager import (
    DelayedQueueItem,
    DelayedQueueManager,
    downscale_image_half,
    parse_delay_to_seconds,
)
from dispatchers.base import BaseDispatcher
from dispatchers.monetization import PatreonDispatcher


class DummyDispatcher(BaseDispatcher):
    """用于测试的虚拟分发器"""

    def __init__(self, name, display_name, succeed=True, fail_msg="Error"):
        super().__init__(name, display_name, "https://example.com", Path("."))
        self.succeed = succeed
        self.fail_msg = fail_msg
        self.publish_called = False
        self.captured_payload = None
        self.last_published_url = None

    def check_login(self):
        return True

    def probe_login(self):
        return {"status": "ok", "detail": "Logged in"}

    def login_interactive(self, parent_window=None):
        return True

    def publish(self, payload):
        self.publish_called = True
        self.captured_payload = payload
        if self.name == "deviantart" and self.succeed:
            self.last_published_url = "https://www.deviantart.com/foxtontonton/art/12345678"
            return True, f"DeviantArt 发布成功: {self.last_published_url}", []
        if self.succeed:
            return True, f"{self.display_name} 发布成功", []
        return False, f"{self.display_name} 发布失败: {self.fail_msg}", []


# =============================================================================
# 1. 延迟设置解析 (分钟/小时/天三种单位换算正确)
# =============================================================================

def test_parse_delay_to_seconds_units():
    """测试延迟设置解析：分钟、小时、天换算"""
    # 天
    assert parse_delay_to_seconds(7, "天") == 7 * 86400
    assert parse_delay_to_seconds(1, "days") == 86400
    assert parse_delay_to_seconds(0.5, "d") == 0.5 * 86400

    # 小时
    assert parse_delay_to_seconds(2, "小时") == 2 * 3600
    assert parse_delay_to_seconds(24, "hours") == 24 * 3600
    assert parse_delay_to_seconds(1.5, "h") == 1.5 * 3600

    # 分钟
    assert parse_delay_to_seconds(3, "分钟") == 180
    assert parse_delay_to_seconds(10, "minutes") == 600
    assert parse_delay_to_seconds(0.5, "m") == 30

    # 非法或默认回退
    assert parse_delay_to_seconds("invalid", "天") == 7 * 86400


# =============================================================================
# 2. 免费平台降档换算 (1792×2304 → 896×1152)
# =============================================================================

def test_downscale_image_half_anchor_and_dimensions(tmp_path):
    """测试免费平台降档换算：1792×2304 严格缩放至 896×1152，像素量为原图 25%"""
    src_img = tmp_path / "original_1792x2304.jpg"
    img = Image.new("RGB", (1792, 2304), color=(60, 100, 180))
    img.save(src_img, "JPEG")

    dest_img = tmp_path / "scaled_half.jpg"
    out_p = downscale_image_half(src_img, dest_img)

    assert out_p.exists()
    with Image.open(out_p) as im:
        assert im.size == (896, 1152)
        assert im.size[0] == 1792 // 2
        assert im.size[1] == 2304 // 2


def test_downscale_image_half_odd_dimensions(tmp_path):
    """测试奇数边长四舍五入取整"""
    src_img = tmp_path / "odd_1001x1501.png"
    img = Image.new("RGB", (1001, 1501), color=(10, 20, 30))
    img.save(src_img, "PNG")

    dest_img = tmp_path / "scaled_odd.png"
    downscale_image_half(src_img, dest_img)

    with Image.open(dest_img) as im:
        assert im.size == (501, 751)


# =============================================================================
# 3. 入队判定 (Patreon 成功才入队 / 失败不入队)
# =============================================================================

def test_delayed_queue_enroll_success(tmp_path):
    """测试登记入队：自包含保存素材副本、生成 ID 与到期时间"""
    q_file = tmp_path / "delayed_queue.json"
    a_dir = tmp_path / "delayed_assets"

    mgr = DelayedQueueManager(queue_file=q_file, assets_dir=a_dir, enabled=True)

    dummy_img = tmp_path / "master_art.jpg"
    dummy_img.write_bytes(b"master_raw_content")

    material = {
        "title": "Masterpiece Wave 1",
        "description": "Exclusive artwork",
        "raw_images": [str(dummy_img)],
        "tags": ["art", "oc"],
    }

    # 延迟 3 分钟
    item = mgr.enroll(material, delay_seconds=180, target_platforms=["deviantart", "x_twitter"])

    assert item.title == "Masterpiece Wave 1"
    assert item.status == "pending"
    assert q_file.exists()

    # 验证自包含素材副本创建
    item_asset_dir = a_dir / item.id
    assert item_asset_dir.exists()
    copied_files = list(item_asset_dir.glob("*"))
    assert len(copied_files) == 1
    assert copied_files[0].read_bytes() == b"master_raw_content"


# =============================================================================
# 4. 快照与源草稿脱钩 (源文件修改/删除不影响队列项自包含副本)
# =============================================================================

def test_snapshot_decoupled_from_source_file(tmp_path):
    """测试脱钩：登记后删除源文件，队列项持有的自包含副本仍然完好"""
    q_file = tmp_path / "delayed_queue.json"
    a_dir = tmp_path / "delayed_assets"
    mgr = DelayedQueueManager(queue_file=q_file, assets_dir=a_dir, enabled=True)

    src_img = tmp_path / "transient_source.jpg"
    src_img.write_bytes(b"initial_bytes")

    item = mgr.enroll({"title": "Decouple Test", "raw_images": [str(src_img)]}, 600, ["deviantart"])

    # 删除源文件
    src_img.unlink()
    assert not src_img.exists()

    # 重新加载队列并验证素材副本依然可读
    reloaded_items = mgr.load_queue()
    assert len(reloaded_items) == 1
    saved_asset = reloaded_items[0].material_snapshot["asset_files"][0]
    assert Path(saved_asset).exists()
    assert Path(saved_asset).read_bytes() == b"initial_bytes"


# =============================================================================
# 5. 到期筛选与逾期补发判定
# =============================================================================

def test_get_due_items_and_overdue_detection(tmp_path):
    """测试到期项筛选：未到期不触发，已到期筛选出，逾期项打日志并补发"""
    q_file = tmp_path / "delayed_queue.json"
    a_dir = tmp_path / "delayed_assets"
    mgr = DelayedQueueManager(queue_file=q_file, assets_dir=a_dir, enabled=True)

    now = datetime.now()

    # 项 1: 过去已到期 (1 小时前，逾期项)
    item_past = DelayedQueueItem(
        id="item_past",
        title="Past Item",
        created_at=(now - timedelta(hours=2)).isoformat(),
        due_at=(now - timedelta(hours=1)).isoformat(),
        platforms=["deviantart"],
        status="pending",
    )
    # 项 2: 未来到期 (2 小时后，未到期)
    item_future = DelayedQueueItem(
        id="item_future",
        title="Future Item",
        created_at=now.isoformat(),
        due_at=(now + timedelta(hours=2)).isoformat(),
        platforms=["deviantart"],
        status="pending",
    )
    # 项 3: 已完成 (done, 不应作为到期执行)
    item_done = DelayedQueueItem(
        id="item_done",
        title="Done Item",
        created_at=(now - timedelta(hours=3)).isoformat(),
        due_at=(now - timedelta(hours=2)).isoformat(),
        platforms=["deviantart"],
        status="done",
    )

    mgr.save_queue([item_past, item_future, item_done])

    due_items = mgr.get_due_items(current_time=now)
    assert len(due_items) == 1
    assert due_items[0].id == "item_past"


# =============================================================================
# 6. 单平台失败不阻塞其余平台且状态正确
# =============================================================================

def test_executor_single_platform_failure_isolation(tmp_path):
    """测试单平台失败隔离：X 失败，DA 与 Bluesky 照常发布，队列项标记为 failed 且记入 last_error"""
    q_file = tmp_path / "delayed_queue.json"
    a_dir = tmp_path / "delayed_assets"
    mgr = DelayedQueueManager(queue_file=q_file, assets_dir=a_dir, enabled=True)

    # 准备测试素材
    dummy_art = tmp_path / "test_art.jpg"
    im = Image.new("RGB", (200, 200), color=(50, 50, 50))
    im.save(dummy_art, "JPEG")

    item = mgr.enroll(
        {"title": "Isolation Test", "raw_images": [str(dummy_art)]},
        delay_seconds=0,
        target_platforms=["deviantart", "x_twitter", "bluesky"],
    )

    # 构建分发器：DA 成功，X 失败，Bluesky 成功
    disp_da = DummyDispatcher("deviantart", "DeviantArt", succeed=True)
    disp_x = DummyDispatcher("x_twitter", "X", succeed=False, fail_msg="API Rate Limit")
    disp_bsky = DummyDispatcher("bluesky", "Bluesky", succeed=True)

    executor = DelayedWaveExecutor(
        manager=mgr,
        dispatchers_factory=lambda: [disp_da, disp_x, disp_bsky],
    )

    all_ok, errors = executor.execute_item(item)

    assert all_ok is False
    assert "x_twitter" in errors
    assert "API Rate Limit" in errors["x_twitter"]
    assert "deviantart" not in errors
    assert "bluesky" not in errors

    # 验证 DA 和 Bluesky 的 publish 被调用并成功
    assert disp_da.publish_called is True
    assert disp_bsky.publish_called is True
    assert disp_x.publish_called is True

    # 队列状态持久化为 failed
    reloaded = mgr.load_queue()[0]
    assert reloaded.status == "failed"
    assert "x_twitter" in reloaded.last_error

    # 重试失败平台：只重试 X
    disp_x.succeed = True
    disp_da.publish_called = False
    disp_bsky.publish_called = False

    retry_ok, retry_errors = executor.execute_item(reloaded, only_failed=True)
    assert retry_ok is True
    assert not retry_errors
    assert disp_x.publish_called is True
    assert disp_da.publish_called is False  # 之前成功的不再重发

    # 最终状态变为 done，且素材目录被清理
    final_reloaded = mgr.load_queue()[0]
    assert final_reloaded.status == "done"
    assert not (a_dir / item.id).exists()


# =============================================================================
# 7. 总开关关闭时零副作用
# =============================================================================

def test_zero_side_effect_when_disabled(tmp_path):
    """测试总开关关闭时：不建队列文件、不建素材目录"""
    q_file = tmp_path / "delayed_queue.json"
    a_dir = tmp_path / "delayed_assets"

    mgr = DelayedQueueManager(queue_file=q_file, assets_dir=a_dir, enabled=False)

    # 此时不应自动创建任何文件
    assert not q_file.exists()
    assert not a_dir.exists()

    # 尝试加载返回空列表
    assert mgr.load_queue() == []
    assert not q_file.exists()


# =============================================================================
# 8. B 页附件门禁 (附件失败不入队、状态「缺附件」、严禁渲染件)
# =============================================================================

def test_patreon_attachments_rejects_raw_wm_derived_files(tmp_path, monkeypatch):
    """
    §2 & §6 附件门禁反转回归测试 (按新 spec 改写):
    - 附件为打码变体或 ×0.5 降档变体 -> 判「缺附件」失败；
    - 附件为本帖全尺寸无码带水印 hd 渲染源文件 (*_raw_wm.* / *_pat_display_wm.*) -> 门禁放行通过。
    """
    disp = PatreonDispatcher(tmp_path)

    src_file = tmp_path / "src_master.jpg"
    Image.new("RGB", (200, 200), color=(100, 150, 200)).save(src_file, "JPEG")

    hd_wm_file = tmp_path / "test_artwork_0_raw_wm.jpg"
    Image.new("RGB", (200, 200), color=(102, 152, 202)).save(hd_wm_file, "JPEG")

    teaser_file = tmp_path / "test_artwork_0_pat_teaser.jpg"
    Image.new("RGB", (100, 100), color=(0, 0, 0)).save(teaser_file, "JPEG")

    censored_file = tmp_path / "test_artwork_0_censored_wm.jpg"
    Image.new("RGB", (200, 200), color=(0, 0, 0)).save(censored_file, "JPEG")

    downscaled_unmarked = tmp_path / "shrunk_variant.jpg"
    Image.new("RGB", (100, 100), color=(100, 150, 200)).save(downscaled_unmarked, "JPEG")

    # 1. 传入打码变体 -> 判「缺附件」失败
    payload_censor = {
        "title": "Censored Attachment Test",
        "raw_images": [str(src_file)],
        "patreon_images": [str(teaser_file), str(hd_wm_file)],
        "patreon_hd_images": [str(hd_wm_file)],
        "attachments": [str(censored_file)],
    }
    ok1, msg1, comp1 = disp.publish(payload_censor)
    assert ok1 is False
    assert "附件门禁拦截" in msg1
    assert "缺附件" in msg1
    comp_att1 = next((c for c in comp1 if c.get("item") == "attachments"), None)
    assert comp_att1 is not None
    assert "缺附件" in comp_att1.get("detail", "")

    # 2. 传入降档变体 (尺寸减半) -> 判「缺附件」失败
    payload_downscaled = {
        "title": "Downscaled Attachment Test",
        "raw_images": [str(src_file)],
        "patreon_images": [str(teaser_file), str(downscaled_unmarked)],
        "patreon_hd_images": [str(downscaled_unmarked)],
        "attachments": [str(downscaled_unmarked)],
    }
    ok2, msg2, comp2 = disp.publish(payload_downscaled)
    assert ok2 is False
    assert "缺附件" in msg2
    comp_att2 = next((c for c in comp2 if c.get("item") == "attachments"), None)
    assert comp_att2 is not None
    assert "缺附件" in comp_att2.get("detail", "")

    # 3. 传入本帖全尺寸无码带水印 hd 渲染源文件 (*_raw_wm.jpg) -> 通过附件门禁 (进入后续浏览器流程)
    disp.get_cookies = MagicMock(return_value=[])
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path / "no_profile"))
    payload_valid_hd = {
        "title": "Valid HD Attachment Test",
        "raw_images": [str(src_file)],
        "patreon_images": [str(teaser_file), str(hd_wm_file)],
        "patreon_hd_images": [str(hd_wm_file)],
        "attachments": [str(hd_wm_file)],
    }
    ok3, msg3, _ = disp.publish(payload_valid_hd)
    # 未被附件门禁拦截 (因无 cookie 在登录检查返回未登录)
    assert "附件门禁拦截" not in msg3
    assert "未登录" in msg3


def test_patreon_attachments_mount_failure_aborts_and_reports_missing_attachment(tmp_path, monkeypatch):
    """测试附件挂载失败拦截：若编辑器回读未检测到全部附件，报错并标记「缺附件」，严禁发布"""
    disp = PatreonDispatcher(tmp_path)
    disp.get_cookies = MagicMock(return_value=[{"name": "session", "value": "123"}])
    disp.get_app_browser_profile_dir = MagicMock(return_value=str(tmp_path / "non_existent"))

    # 模拟真实母版文件
    master_file = tmp_path / "master_original.png"
    master_file.write_bytes(b"clean_master_art")

    class MockPatreonPage:
        def __init__(self):
            self.url = "https://www.patreon.com/posts/new"
            self.goto = MagicMock()
        def query_selector(self, sel):
            if any(k in sel.lower() for k in ["progressbar", "uploading", "spinner"]):
                return None
            m = MagicMock()
            m.is_visible.return_value = True
            m.is_disabled.return_value = False
            m.get_attribute.return_value = "false"
            return m
        def query_selector_all(self, sel):
            return []
        def evaluate(self, script, *args):
            # 模拟编辑器回读未包含该母版文件名
            return "No matching attachments"
        def wait_for_selector(self, *a, **kw):
            return MagicMock()

    class MockContext:
        def __init__(self):
            self.pages = [MockPatreonPage()]
        def new_page(self):
            return self.pages[0]
        def add_cookies(self, c):
            pass
        def close(self):
            pass

    class MockPlaywright:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    disp.create_stealth_browser = MagicMock(return_value=MagicMock(new_context=MagicMock(return_value=MockContext())))
    disp.assert_field_value = MagicMock(return_value=(True, "OK"))
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: MockPlaywright())
    monkeypatch.setattr("time.sleep", lambda s: None)

    payload = {
        "title": "Master Attachment Test",
        "images": [str(master_file)],
        "patreon_images": [str(master_file)],
        "attachments": [str(master_file)],
    }

    ok, msg, comp = disp.publish(payload)

    assert ok is False
    assert "附件挂载失败" in msg
    comp_att = next((c for c in comp if c.get("item") == "attachments"), None)
    assert comp_att is not None
    assert "缺附件" in comp_att.get("detail", "")


# =============================================================================
# 9. Patreon 预告图变体来源 (「公开安全」独立预设，与免费平台轻码件逐像素不同)
# =============================================================================

def test_patreon_public_safe_preset_distinct_pixels(tmp_path):
    """测试 Patreon 预告图「公开安全」独立预设与普通打码件像素不同"""
    ce = CensorEngine()

    test_img = tmp_path / "censor_test.jpg"
    img = Image.new("RGB", (600, 600), color=(150, 100, 80))
    img.save(test_img, "JPEG")

    box = [150, 150, 300, 300]

    # 1. 公开安全独立预设
    censored_safe, b_safe = ce.apply_censor(str(test_img), mode="public_safe", custom_boxes=[box])
    # 2. 免费平台/轻码预设
    censored_mod, b_mod = ce.apply_censor(str(test_img), mode="moderate", custom_boxes=[box])

    # 验证遮挡框覆盖范围显著更大
    assert b_safe[0][0] < b_mod[0][0]  # 左边界外扩
    assert b_safe[0][1] < b_mod[0][1]  # 上边界外扩
    assert b_safe[0][2] > b_mod[0][2]  # 右边界外扩
    assert b_safe[0][3] > b_mod[0][3]  # 下边界外扩

    # 验证逐像素比对不同
    diff = np.sum(np.abs(censored_safe.astype(int) - censored_mod.astype(int)))
    assert diff > 0, "Patreon 预告图「公开安全」预设必须与标准轻码件产生像素级差异"


# =============================================================================
# 10. 标签页 A 行为回归与两阶段 DA 链接透传测试
# =============================================================================

def test_delayed_wave_two_phase_da_link_propagation(tmp_path):
    """测试延迟公开波执行时的两阶段 DA 当帖链接透传引流"""
    q_file = tmp_path / "delayed_queue.json"
    a_dir = tmp_path / "delayed_assets"
    mgr = DelayedQueueManager(queue_file=q_file, assets_dir=a_dir, enabled=True)

    dummy_art = tmp_path / "two_phase_art.jpg"
    Image.new("RGB", (100, 100), color=(10, 10, 10)).save(dummy_art, "JPEG")

    default_homepage = "https://www.deviantart.com/foxtontonton"
    item = mgr.enroll(
        {
            "title": "Two Phase Wave Art",
            "description": f"See full set on DA: {default_homepage}",
            "short_text": f"New art out! Full set: {default_homepage} #art",
            "raw_images": [str(dummy_art)],
        },
        delay_seconds=0,
        target_platforms=["deviantart", "x_twitter"],
    )

    disp_da = DummyDispatcher("deviantart", "DeviantArt", succeed=True)
    disp_x = DummyDispatcher("x_twitter", "X", succeed=True)

    executor = DelayedWaveExecutor(
        manager=mgr,
        dispatchers_factory=lambda: [disp_da, disp_x],
    )

    all_ok, errors = executor.execute_item(item)

    assert all_ok is True
    # 验证 X 收到的短文案中，原主页链接已被幂等替换为当帖真实 URL
    assert disp_x.captured_payload is not None
    x_short = disp_x.captured_payload.get("short_text", "")
    expected_da_url = "https://www.deviantart.com/foxtontonton/art/12345678"
    assert expected_da_url in x_short
    assert x_short == f"New art out! Full set: {expected_da_url} #art"


# =============================================================================
# 11. Civitai GIF 降档跳过与 MP4 配对回归测试
# =============================================================================

def test_publish_thread_civitai_gif_skips_downscale_and_preserves_pairing(tmp_path):
    """
    PublishThread 门禁循环针对 Civitai 跳过 .gif 降档改名：
    - Civitai 不发 GIF 本体，上传的是配对 MP4，GIF 仅作配对钥匙；
    - 改名即断链，故 civitai_images 中 .gif 保持原路径原名；
    - prepare_civitai_media 顺利配对并产出 accepted 含配对 MP4；
    - 同批的 DA 则按规则正常降档。
    """
    from app import PublishThread
    from dispatchers.civitai import prepare_civitai_media

    gif_file = tmp_path / "dance_loop.gif"
    im = Image.new("RGB", (100, 100), color=(255, 0, 0))
    im.save(gif_file, "GIF")

    mp4_file = tmp_path / "dance_loop.mp4"
    mp4_file.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isom")

    disp_civ = DummyDispatcher("civitai", "Civitai", succeed=True)
    disp_da = DummyDispatcher("deviantart", "DeviantArt", succeed=True)

    payload = {
        "title": "Civitai GIF Pairing Test",
        "images": [str(gif_file)],
        "raw_images": [str(gif_file)],
        "civitai_images": [str(gif_file)],
        "da_images": [str(gif_file)],
        "x_video_paths": {str(gif_file): str(mp4_file)},
        "platform_options": {
            "x_video_paths": {str(gif_file): str(mp4_file)},
        },
    }

    with patch("dispatchers.civitai.validate_civitai_video_constraints", return_value=(True, "OK")):
        thread = PublishThread([disp_civ, disp_da], payload)
        thread.run()

    # 1. 验证 Civitai 名单未被 _freehalf 污染改名，保持原路径原名
    assert disp_civ.captured_payload is not None
    civ_imgs = disp_civ.captured_payload.get("civitai_images", [])
    assert len(civ_imgs) == 1
    assert civ_imgs[0] == str(gif_file)
    assert "_freehalf" not in civ_imgs[0]

    # 2. 验证 DA 依然按免费平台规则正常降档
    assert disp_da.captured_payload is not None
    da_imgs = disp_da.captured_payload.get("da_images", [])
    assert len(da_imgs) == 1
    assert "_freehalf.gif" in da_imgs[0]

    # 3. 验证 prepare_civitai_media 顺利完成钥匙匹配，accepted 含配对 MP4
    with patch("dispatchers.civitai.validate_civitai_video_constraints", return_value=(True, "OK")):
        accepted, skipped = prepare_civitai_media(
            disp_civ.captured_payload.get("civitai_images"),
            x_video_paths=disp_civ.captured_payload.get("x_video_paths"),
        )
    assert str(mp4_file) in accepted
    assert len(accepted) == 1


def test_delayed_wave_executor_civitai_gif_pairing_and_media_resolution(tmp_path, monkeypatch):
    """
    DelayedWaveExecutor 构建 payload 时：
    - civitai_images 保留 GIF 原路径原名；
    - 自动为快照中的 GIF 查同 stem MP4 (素材目录/ComfyUI输出树) 并补齐 x_video_paths；
    - prepare_civitai_media 顺利配对并产出 accepted 含配对 MP4。
    """
    from dispatchers.civitai import prepare_civitai_media

    q_file = tmp_path / "delayed_queue.json"
    a_dir = tmp_path / "delayed_assets"
    mgr = DelayedQueueManager(queue_file=q_file, assets_dir=a_dir, enabled=True)
    # 模拟保留资产副本，以便后续断言 prepare_civitai_media 的文件存在检查
    monkeypatch.setattr(mgr, "cleanup_assets", lambda item_id: None)

    # 在临时素材目录创建源 GIF 与同 stem 的配对 MP4
    src_dir = tmp_path / "src_materials"
    src_dir.mkdir()
    gif_file = src_dir / "hero_anim.gif"
    im = Image.new("RGB", (120, 120), color=(0, 255, 0))
    im.save(gif_file, "GIF")

    mp4_file = src_dir / "hero_anim.mp4"
    mp4_file.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isom")

    item = mgr.enroll(
        {
            "title": "Wave GIF Civitai Test",
            "description": "Awesome animated set",
            "short_text": "Awesome animated set #civitai",
            "raw_images": [str(gif_file)],
        },
        delay_seconds=0,
        target_platforms=["civitai"],
    )

    disp_civ = DummyDispatcher("civitai", "Civitai", succeed=True)
    executor = DelayedWaveExecutor(
        manager=mgr,
        dispatchers_factory=lambda: [disp_civ],
    )

    all_ok, errors = executor.execute_item(item)
    assert all_ok is True
    assert disp_civ.captured_payload is not None

    # 1. 验证 civitai_images 保留 GIF 原扩展名与路径，未被降档为 _half.gif
    civ_imgs = disp_civ.captured_payload.get("civitai_images", [])
    assert len(civ_imgs) == 1
    assert civ_imgs[0].lower().endswith(".gif")
    assert "_half.gif" not in civ_imgs[0]

    # 2. 验证 x_video_paths 自动解析并补齐了配对 MP4
    xvids = disp_civ.captured_payload.get("x_video_paths", {})
    assert len(xvids) > 0
    assert any(str(mp4_file) == str(v) for v in xvids.values())

    # 3. 验证 prepare_civitai_media 顺利接收并产出配对 MP4
    with patch("dispatchers.civitai.validate_civitai_video_constraints", return_value=(True, "OK")):
        accepted, skipped = prepare_civitai_media(
            civ_imgs,
            x_video_paths=xvids,
        )
    assert str(mp4_file) in accepted
    assert len(accepted) == 1


# =============================================================================
# 12. 公开波执行期补渲染管线 (打码引擎与水印引擎按各平台设置生效，母版不直进图单)
# =============================================================================

def test_executor_rendering_pipeline_censor_and_watermark_per_platform(tmp_path):
    """
    验证公开波执行期补渲染管线：
    - X / Bluesky 变体经过打码引擎 (CensorEngine.apply_censor)
    - DA 变体带水印 (WatermarkEngine.apply_watermark，当设置开启时)
    - 母版原件只供 Civitai 配对钥匙与附件语义，不直接进任何平台的静态图单
    """
    from watermark_engine import WatermarkEngine

    q_file = tmp_path / "delayed_queue.json"
    a_dir = tmp_path / "delayed_assets"
    cache_dir = tmp_path / "cache"
    mgr = DelayedQueueManager(queue_file=q_file, assets_dir=a_dir, enabled=True)

    # 创建母版静态图 400x400
    master_img = tmp_path / "master_source.png"
    Image.new("RGB", (400, 400), color=(180, 120, 90)).save(master_img, "PNG")

    item = mgr.enroll(
        {
            "title": "Render Pipeline Test",
            "description": "Testing per-platform censor and watermark",
            "raw_images": [str(master_img)],
            "custom_censor_boxes": {master_img.name: [[100, 100, 200, 200]]},
        },
        delay_seconds=0,
        target_platforms=["deviantart", "x_twitter", "bluesky", "civitai"],
    )
    saved_asset_path = item.material_snapshot["asset_files"][0]

    # 配置: DA 不打码但带水印，X 与 Bluesky 开启打码且带水印，Civitai 不打码带水印
    test_cfg = {
        "censor": {
            "enabled": True,
            "style": "black_bar",
            "coverage_mode": "moderate",
            "coverage_scale": 0.55,
            "strength": 50,
            "opacity": 1.0,
            "padding_ratio": 0.15,
        },
        "watermark": {
            "enabled": True,
            "apply_to_publish": True,
            "text": "@TEST_WATERMARK",
            "font_family": "微软雅黑 (Microsoft YaHei)",
            "size_pct": 4.0,
            "opacity": 0.8,
            "position": "bottom_right",
            "color": "#ffffff",
            "stroke": True,
        },
        "defaults": {
            "deviantart_censor": False,
            "x_censor": True,
            "bluesky_censor": True,
            "civitai_censor": False,
        },
    }

    real_ce = CensorEngine()
    real_wm = WatermarkEngine()

    with patch.object(real_ce, "apply_censor", wraps=real_ce.apply_censor) as spy_censor, \
         patch.object(real_wm, "apply_watermark", wraps=real_wm.apply_watermark) as spy_wm:

        disp_da = DummyDispatcher("deviantart", "DeviantArt", succeed=True)
        disp_x = DummyDispatcher("x_twitter", "X", succeed=True)
        disp_bsky = DummyDispatcher("bluesky", "Bluesky", succeed=True)
        disp_civ = DummyDispatcher("civitai", "Civitai", succeed=True)

        executor = DelayedWaveExecutor(
            manager=mgr,
            dispatchers_factory=lambda: [disp_da, disp_x, disp_bsky, disp_civ],
            config_provider=lambda: test_cfg,
            censor_engine=real_ce,
            watermark_engine=real_wm,
            cache_dir=cache_dir,
        )

        all_ok, errors = executor.execute_item(item)

        assert all_ok is True
        assert not errors

        # 1. CensorEngine.apply_censor 与 WatermarkEngine.apply_watermark 均被真实调用
        assert spy_censor.call_count >= 1
        assert spy_wm.call_count >= 2  # 打码分支 + 无码分支各压制一次水印

        payload = disp_x.captured_payload
        assert payload is not None

        # 2. 母版原件路径绝不直接进入任何平台的静态图单
        for key in ("da_images", "x_images", "bluesky_images", "civitai_images"):
            plat_list = payload.get(key, [])
            assert len(plat_list) == 1
            assert str(master_img) not in plat_list
            assert str(saved_asset_path) not in plat_list
            # 均为 ×0.5 降档后的 200x200 变体
            with Image.open(plat_list[0]) as out_im:
                assert out_im.size == (200, 200)

        # 3. X 与 Bluesky 使用的是经过打码引擎的变体，DA 使用的是无码带水印变体
        x_img_path = payload["x_images"][0]
        bsky_img_path = payload["bluesky_images"][0]
        da_img_path = payload["da_images"][0]
        assert "censored_half" in Path(x_img_path).name
        assert "censored_half" in Path(bsky_img_path).name

        # 校验 X/Bluesky 变体在打码中心点 (150//2=75, 150//2=75) 为纯黑条 (0,0,0)，而 DA 变体保留原色
        with Image.open(x_img_path) as x_im, Image.open(da_img_path) as da_im:
            x_px = x_im.convert("RGB").getpixel((75, 75))
            da_px = da_im.convert("RGB").getpixel((75, 75))
            assert x_px == (0, 0, 0)
            assert da_px != (0, 0, 0)


# =============================================================================
# 13. Executor 平台规则对齐与发布历史写入
# =============================================================================

def test_executor_platform_rules_alignment_and_history_write(tmp_path):
    """
    验证 executor 平台规则对齐:
    - Bluesky 静态图单 ≤ 4；X 静态图单 ≤ 4
    - Civitai 注入 platform_options 与顶层预设 (resources/tool/channel/max_images)
    - 执行完成时写入 publish_history.json (复用 PublishHistoryManager.append_record 口径)
    """
    from history_manager import PublishHistoryManager

    q_file = tmp_path / "delayed_queue.json"
    a_dir = tmp_path / "delayed_assets"
    cache_dir = tmp_path / "cache"
    mgr = DelayedQueueManager(queue_file=q_file, assets_dir=a_dir, enabled=True)
    hist_mgr = PublishHistoryManager(tmp_path)

    # 创建 6 张静态图
    imgs = []
    for i in range(6):
        p = tmp_path / f"multi_{i}.jpg"
        Image.new("RGB", (100, 100), color=(20 * i, 50, 100)).save(p, "JPEG")
        imgs.append(str(p))

    item = mgr.enroll(
        {
            "title": "Six Images Rule Test",
            "description": "Testing <=4 cap and civitai presets",
            "tags": ["tag1", "tag2"],
            "raw_images": imgs,
        },
        delay_seconds=0,
        target_platforms=["deviantart", "x_twitter", "bluesky", "civitai"],
    )

    custom_cfg = {
        "censor": {"enabled": False},
        "watermark": {"enabled": False},
        "defaults": {
            "civitai_channel": "api",
            "civitai_max_images_per_post": 15,
            "civitai_resources": [{"modelVersionId": 999, "modelName": "TestLoRA"}],
            "civitai_resources_scope": "first",
            "civitai_tool": "ForgeUI",
        },
    }

    disp_da = DummyDispatcher("deviantart", "DeviantArt", succeed=True)
    disp_x = DummyDispatcher("x_twitter", "X", succeed=True)
    disp_bsky = DummyDispatcher("bluesky", "Bluesky", succeed=True)
    disp_civ = DummyDispatcher("civitai", "Civitai", succeed=True)

    executor = DelayedWaveExecutor(
        manager=mgr,
        dispatchers_factory=lambda: [disp_da, disp_x, disp_bsky, disp_civ],
        config_provider=lambda: custom_cfg,
        history_manager=hist_mgr,
        cache_dir=cache_dir,
    )

    all_ok, errors = executor.execute_item(item)
    assert all_ok is True

    payload = disp_civ.captured_payload
    # 1. X 与 Bluesky 图单均被截断至 ≤ 4 张，DA 与 Civitai 保留全部 6 张
    assert len(payload["x_images"]) == 4
    assert len(payload["bluesky_images"]) == 4
    assert payload["bluesky_discarded_images_count"] == 2
    assert len(payload["da_images"]) == 6
    assert len(payload["civitai_images"]) == 6

    # 2. Civitai 预设注入到 payload 顶层与 platform_options
    assert payload["civitai_channel"] == "api"
    assert payload["civitai_max_images_per_post"] == 15
    assert payload["civitai_resources"] == [{"modelVersionId": 999, "modelName": "TestLoRA"}]
    assert payload["civitai_resources_scope"] == "first"
    assert payload["civitai_tool"] == "ForgeUI"
    po = payload["platform_options"]
    assert po["civitai_channel"] == "api"
    assert po["civitai_max_images_per_post"] == 15
    assert po["civitai_resources"] == [{"modelVersionId": 999, "modelName": "TestLoRA"}]
    assert po["civitai_tool"] == "ForgeUI"

    # 3. 验证 publish_history.json 已写入记录
    records = hist_mgr.load_history()
    assert len(records) == 1
    assert records[0]["title"] == "Six Images Rule Test"
    assert "deviantart" in records[0]["platforms"]
    assert records[0]["platforms"]["deviantart"]["success"] is True


def test_executor_gif_sets_x_video_and_bluesky_video(tmp_path, monkeypatch):
    """
    验证单 GIF 帖在 executor 中:
    - X 设置 x_video 指向配对 MP4
    - Bluesky 设置 bluesky_video 且将 bluesky_images 置空 [] (单帖单 embed 规范)
    """
    q_file = tmp_path / "delayed_queue.json"
    a_dir = tmp_path / "delayed_assets"
    cache_dir = tmp_path / "cache"
    mgr = DelayedQueueManager(queue_file=q_file, assets_dir=a_dir, enabled=True)

    src_dir = tmp_path / "gif_src"
    src_dir.mkdir()
    gif_file = src_dir / "loop_art.gif"
    Image.new("RGB", (100, 100), color=(10, 200, 50)).save(gif_file, "GIF")
    mp4_file = src_dir / "loop_art.mp4"
    mp4_file.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isom")

    item = mgr.enroll(
        {"title": "GIF Video Rule Test", "raw_images": [str(gif_file)]},
        delay_seconds=0,
        target_platforms=["x_twitter", "bluesky"],
    )

    disp_x = DummyDispatcher("x_twitter", "X", succeed=True)
    disp_bsky = DummyDispatcher("bluesky", "Bluesky", succeed=True)

    executor = DelayedWaveExecutor(
        manager=mgr,
        dispatchers_factory=lambda: [disp_x, disp_bsky],
        config_provider=lambda: {"censor": {"enabled": False}, "watermark": {"enabled": False}},
        cache_dir=cache_dir,
    )

    all_ok, _ = executor.execute_item(item)
    assert all_ok is True

    payload = disp_x.captured_payload
    assert payload["x_video"] == str(mp4_file)
    assert payload["bluesky_video"] == str(mp4_file)
    assert payload["bluesky_images"] == []


# =============================================================================
# 14. Tab B 预告图 fail-closed 与 GIF 逐帧打码 & 描述双换行 & 防重入
# =============================================================================

def test_tab_b_teaser_fail_closed_aborts_on_censor_error(tmp_path, monkeypatch):
    """
    验证 Tab B 预告图 fail-closed:
    当 public_safe 打码抛异常时，立即中止发布并响亮报错，绝不回退未打码原图，绝不启动 PublishThread。
    """
    from PyQt5.QtWidgets import QApplication, QMessageBox
    import app as app_mod

    qapp = QApplication.instance() or QApplication([])
    assert qapp is not None
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    win = app_mod.MainWindow()
    win.config["delayed_wave_enabled"] = True
    win._update_delayed_wave_ui()

    master_img = tmp_path / "tab_b_master.png"
    Image.new("RGB", (200, 200), color=(200, 50, 50)).save(master_img, "PNG")

    win.tab_b_title_input.setText("Fail Closed Test")
    from PyQt5.QtWidgets import QListWidgetItem
    from PyQt5.QtCore import Qt
    it = QListWidgetItem(master_img.name)
    it.setData(Qt.UserRole, str(master_img))
    win.tab_b_img_list.addItem(it)
    win.tab_b_censor_cb.setChecked(True)

    # 模拟打码引擎失败
    monkeypatch.setattr(win.censor_engine, "apply_censor", MagicMock(side_effect=RuntimeError("Mock NudeNet Crash")))
    crit_mock = MagicMock()
    monkeypatch.setattr(QMessageBox, "critical", crit_mock)
    pub_thread_mock = MagicMock()
    monkeypatch.setattr(app_mod, "PublishThread", pub_thread_mock)

    win._start_tab_b_publishing()

    # 必须弹窗报错、未启动 PublishThread、按钮已恢复可用
    assert crit_mock.call_count == 1
    assert pub_thread_mock.call_count == 0
    assert win.tab_b_publish_btn.isEnabled() is True


def test_tab_b_gif_teaser_uses_gif_engine_and_paragraph_description(tmp_path, monkeypatch):
    """
    验证 Tab B:
    - 首图为 GIF 时走 GIF 逐帧打码路径 (render_composite_gif_frames)，不走 cv2.imwrite
    - 描述框多段文本按块间双换行 (\\n\\n) 读取
    - 发布期间按钮禁用，防重入守卫生效
    """
    from PyQt5.QtWidgets import QApplication, QMessageBox
    import app as app_mod

    qapp = QApplication.instance() or QApplication([])
    assert qapp is not None
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    win = app_mod.MainWindow()
    win.config["delayed_wave_enabled"] = True
    win._update_delayed_wave_ui()

    gif_master = tmp_path / "anim_master.gif"
    f1 = Image.new("RGB", (200, 200), color=(255, 100, 100))
    f2 = Image.new("RGB", (200, 200), color=(100, 255, 100))
    f1.save(gif_master, save_all=True, append_images=[f2], duration=100, loop=0)

    win.tab_b_title_input.setText("GIF Teaser Test")
    win.tab_b_desc_input.setPlainText("Line 1\nLine 2\n\n\nLine 3")

    from PyQt5.QtWidgets import QListWidgetItem
    from PyQt5.QtCore import Qt
    it = QListWidgetItem(gif_master.name)
    it.setData(Qt.UserRole, str(gif_master))
    win.tab_b_img_list.addItem(it)
    win.tab_b_censor_cb.setChecked(True)
    win.custom_censor_boxes[str(gif_master)] = [[40, 40, 120, 120]]

    captured_threads = []

    class FakePublishThread:
        def __init__(self, disps, payload):
            self.disps = disps
            self.payload = payload
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()
            captured_threads.append(self)

        def start(self):
            pass

        def isRunning(self):
            return True

    monkeypatch.setattr(app_mod, "PublishThread", FakePublishThread)
    monkeypatch.setattr(QMessageBox, "critical", MagicMock())

    win._start_tab_b_publishing()

    assert len(captured_threads) == 1
    sent_payload = captured_threads[0].payload
    # 1. 描述保留段落间双换行语义
    assert sent_payload["description"] == "Line 1\n\nLine 2\n\nLine 3"
    # 2. GIF 预告图成功生成且尺寸减半 (100x100)
    teaser_gif = Path(sent_payload["patreon_images"][0])
    assert teaser_gif.exists()
    assert teaser_gif.suffix.lower() == ".gif"
    with Image.open(teaser_gif) as out_gif:
        assert out_gif.size == (100, 100)
    # 3. 发布中按钮处于禁用状态
    assert win.tab_b_publish_btn.isEnabled() is False


# =============================================================================
# 15. 繁忙互斥与 queue_btn 告警样式恢复 & _start_publishing(scheduled_snapshot)
# =============================================================================

def test_delayed_wave_busy_mutex_defers_when_main_publish_running(tmp_path, monkeypatch):
    """
    验证繁忙互斥：当主发布或定时发布正在运行时，_poll_delayed_wave_queue 顺延并打日志，不启动后台公开波线程。
    """
    from PyQt5.QtWidgets import QApplication
    import app as app_mod

    qapp = QApplication.instance() or QApplication([])
    assert qapp is not None
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    win = app_mod.MainWindow()
    win.data_dir = tmp_path
    win.config["delayed_wave_enabled"] = True
    win._init_delayed_wave_subsystem()

    # 登记一个已到期项
    dummy_img = tmp_path / "mutex_art.jpg"
    Image.new("RGB", (100, 100), color=(10, 20, 30)).save(dummy_img, "JPEG")
    win.delayed_wave_manager.enroll({"title": "Mutex Art", "raw_images": [str(dummy_img)]}, -10, ["deviantart"])

    # 模拟定时发布正在运行
    win._is_scheduled_task_running = True
    worker_spy = MagicMock()
    monkeypatch.setattr(app_mod, "DelayedWaveWorkerThread", worker_spy)

    win._poll_delayed_wave_queue()
    # 繁忙互斥拦截，不启动 worker
    assert worker_spy.call_count == 0

    # 解除繁忙后再次轮询，启动后台线程
    win._is_scheduled_task_running = False
    win._poll_delayed_wave_queue()
    assert worker_spy.call_count == 1


def test_queue_btn_alert_style_restores_after_clear_or_retry_success(tmp_path, monkeypatch):
    """
    验证 queue_btn 告警样式在队列存在失败项时变红，在重试成功或取消后恢复默认空样式。
    """
    from PyQt5.QtWidgets import QApplication
    import app as app_mod

    qapp = QApplication.instance() or QApplication([])
    assert qapp is not None
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    win = app_mod.MainWindow()
    win.data_dir = tmp_path
    win.config["delayed_wave_enabled"] = True
    win._init_delayed_wave_subsystem()

    dummy_img = tmp_path / "alert_art.jpg"
    Image.new("RGB", (100, 100), color=(10, 20, 30)).save(dummy_img, "JPEG")
    item = win.delayed_wave_manager.enroll({"title": "Alert Art", "raw_images": [str(dummy_img)]}, 0, ["deviantart"])

    # 标记为失败 -> 按钮样式变红
    win.delayed_wave_manager.update_item_status(item.id, status="failed", last_error={"deviantart": "Net Err"})
    win._on_delayed_wave_item_finished(item, False)
    assert "#991b1b" in win.queue_btn.styleSheet()

    # 标记为成功完成 -> 按钮样式恢复默认 ("")
    win.delayed_wave_manager.update_item_status(item.id, status="done", last_error={})
    win._on_delayed_wave_item_finished(item, True)
    assert win.queue_btn.styleSheet() == ""


def test_start_publishing_scheduled_snapshot_strictly_overrides_ui_widgets(tmp_path, monkeypatch):
    """
    验证 _start_publishing(scheduled_snapshot) 全程使用快照值：
    当快照与当前 UI 内容（title/description/tags/is_nsfw/is_ai/platform_options）完全不同时，
    生成的 payload 必须严格等于快照值，绝不被当前 UI 控件覆盖。
    """
    from PyQt5.QtWidgets import QApplication
    import app as app_mod

    qapp = QApplication.instance() or QApplication([])
    assert qapp is not None
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    win = app_mod.MainWindow()
    # 关闭打码和水印以简化素材路径直通验证
    win.auto_censor_cb.setChecked(False)
    win.watermark_enabled_cb.setChecked(False)

    # 在当前 UI 中填入一套完全不同的干扰值
    win.title_input.setText("UI Dirty Title")
    win.desc_input.setPlainText("UI Dirty Description")
    win.tags_input.setPlainText("ui_tag_1, ui_tag_2")
    win.nsfw_checkbox.setChecked(False)
    win.ai_checkbox.setChecked(False)
    if "deviantart" in win.platform_controls:
        win.platform_controls["deviantart"]["checkbox"].setChecked(False)

    # 准备快照专属图片与快照字典
    snap_img = tmp_path / "snapshot_only.jpg"
    Image.new("RGB", (120, 120), color=(30, 60, 90)).save(snap_img, "JPEG")

    snapshot = {
        "title": "Snapshot Clean Title",
        "description": "Snapshot Para 1\n\nSnapshot Para 2",
        "tags": ["snap_tag_a", "snap_tag_b"],
        "image_paths": [str(snap_img)],
        "selected_platforms": ["deviantart", "civitai"],
        "censor_enabled": False,
        "is_nsfw": True,
        "is_ai": True,
        "short_text": "Snapshot custom short text",
        "platform_options": {
            "deviantart_mature": "strict",
            "deviantart_gallery": ["CustomSnapFolder"],
            "deviantart_ai": True,
            "deviantart_keep_preset_description": False,
            "civitai_channel": "web",
            "civitai_tool": "SnapTool",
            "custom_snap_marker": "preserved_123",
        },
    }

    captured = {}

    class DummyPubThread:
        def __init__(self, selected, payload):
            captured["selected"] = [d.name for d in selected]
            captured["payload"] = payload
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()

        def start(self):
            pass

        def isRunning(self):
            return False

    monkeypatch.setattr(app_mod, "PublishThread", DummyPubThread)

    win._start_publishing(scheduled_snapshot=snapshot)

    assert "payload" in captured
    p = captured["payload"]
    assert captured["selected"] == ["deviantart", "civitai"]
    assert p["title"] == "Snapshot Clean Title"
    assert p["description"] == "Snapshot Para 1\n\nSnapshot Para 2"
    assert p["tags"] == ["snap_tag_a", "snap_tag_b"]
    assert p["is_nsfw"] is True
    assert p["is_ai"] is True
    assert p["short_text"] == "Snapshot custom short text"

    po = p["platform_options"]
    assert po["custom_snap_marker"] == "preserved_123"
    assert po["deviantart_mature"] == "strict"
    assert po["deviantart_gallery"] == ["CustomSnapFolder"]
    assert po["deviantart_keep_preset_description"] is False
    assert po["civitai_channel"] == "web"
    assert po["civitai_tool"] == "SnapTool"
    assert p["civitai_channel"] == "web"
    assert p["civitai_tool"] == "SnapTool"
def test_delayed_wave_tab_b_toggle_lifecycle_no_runtime_error(tmp_path, monkeypatch):
    """
    1. 开关生命周期回归测试:
    - 开启 -> 关闭 (tab_b_workspace 置为 None) -> 再次开启
    - 标签页 B 正常重建，不抛 RuntimeError: wrapped C/C++ object has been deleted
    - 测试 sip.isdeleted 兜底分支
    """
    import app as app_mod
    from PyQt5.QtWidgets import QApplication, QWidget
    qapp = QApplication.instance() or QApplication([])
    assert qapp is not None
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    win = app_mod.MainWindow()
    # 1. 开启
    win.config["delayed_wave_enabled"] = True
    win._update_delayed_wave_ui()
    assert win.main_tab_widget is not None
    assert win.tab_b_workspace is not None
    assert win.main_tab_widget.count() == 2

    # 2. 关闭 (workspace 设为 None)
    win.config["delayed_wave_enabled"] = False
    win._update_delayed_wave_ui()
    assert win.main_tab_widget is None
    assert win.tab_b_workspace is None

    # 3. 再次开启 (同会话重建)
    win.config["delayed_wave_enabled"] = True
    win._update_delayed_wave_ui()
    assert win.main_tab_widget is not None
    assert win.tab_b_workspace is not None
    assert win.main_tab_widget.count() == 2

    # 4. 模拟已删除的 C++ QWidget 存留在 tab_b_workspace (sip.isdeleted 双保险)
    from PyQt5 import sip
    deleted_widget = QWidget()
    deleted_widget.deleteLater()
    QApplication.processEvents()
    if hasattr(sip, "isdeleted") and sip.isdeleted(deleted_widget):
        win.main_tab_widget = None
        win.tab_b_workspace = deleted_widget
        win.config["delayed_wave_enabled"] = True
        win._update_delayed_wave_ui()
        assert win.tab_b_workspace is not None
        assert win.tab_b_workspace != deleted_widget


def test_tab_b_snapshot_short_text_empty_and_executor_payload_empty(tmp_path, monkeypatch):
    """
    2. Tab B 快照 short_text 留空回归测试:
    - B 页入队快照 short_text 必须为 "" (不截断正文)
    - executor 产出的 payload short_text 必须为 "" (留给分发器自动组装预告)
    """
    import app as app_mod
    from PyQt5.QtWidgets import QApplication, QMessageBox
    qapp = QApplication.instance() or QApplication([])
    assert qapp is not None
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    win = app_mod.MainWindow()
    win.config["delayed_wave_enabled"] = True
    win._update_delayed_wave_ui()

    master_img = tmp_path / "tab_b_master_short.png"
    Image.new("RGB", (200, 200), color=(100, 150, 200)).save(master_img, "PNG")

    win.tab_b_title_input.setText("Test Short Text Empty Title")
    long_desc = "Long description " * 30
    win.tab_b_desc_input.setPlainText(long_desc)

    from PyQt5.QtWidgets import QListWidgetItem
    from PyQt5.QtCore import Qt
    it = QListWidgetItem(master_img.name)
    it.setData(Qt.UserRole, str(master_img))
    win.tab_b_img_list.addItem(it)

    captured_snapshot = {}

    class DummyManager:
        def enroll(self, material_snapshot, delay_seconds, target_platforms):
            captured_snapshot.update(material_snapshot)
            item = MagicMock()
            item.id = "mock_item_001"
            item.due_at = "2026-11-12 10:00:00"
            return item

    win.delayed_wave_manager = DummyManager()

    class SignalMock:
        def __init__(self):
            self._cbs = []
        def connect(self, cb):
            self._cbs.append(cb)
        def emit(self, *a, **kw):
            for cb in self._cbs:
                cb(*a, **kw)

    class ImmediateThread:
        def __init__(self, selected, payload):
            self.selected = selected
            self.payload = payload
            self.progress_signal = SignalMock()
            self.completed_signal = SignalMock()

        def start(self):
            self.completed_signal.emit({"patreon": (True, "发布成功", [])})

        def isRunning(self):
            return False

    monkeypatch.setattr(app_mod, "PublishThread", ImmediateThread)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: None)

    win._start_tab_b_publishing()

    # 断言 1: 入队快照 short_text 为 ""
    assert captured_snapshot.get("short_text") == ""

    # 断言 2: executor 组装 payload 时，short_text 亦为 ""
    queue_file = tmp_path / "delayed_queue.json"
    mgr = DelayedQueueManager(queue_file=queue_file, assets_dir=tmp_path / "assets")
    enrolled_item = mgr.enroll(captured_snapshot, delay_seconds=60, target_platforms=["x_twitter", "bluesky"])

    captured_payloads = {}
    def fake_publish(disp_name, payload):
        captured_payloads[disp_name] = payload
        return True, "ok", []

    class DummyDisp:
        def __init__(self, name):
            self.name = name
            self.display_name = name
        def check_login(self):
            return True
        def publish(self, payload):
            return fake_publish(self.name, payload)

    executor = DelayedWaveExecutor(
        manager=mgr,
        config_provider=lambda: {},
        dispatchers_factory=lambda: [DummyDisp("x_twitter"), DummyDisp("bluesky")],
    )
    ok, errs = executor.execute_item(enrolled_item)
    assert ok is True
    assert "x_twitter" in captured_payloads
    assert captured_payloads["x_twitter"]["short_text"] == ""
    assert captured_payloads["bluesky"]["short_text"] == ""


def test_delayed_wave_retry_recovers_da_link_from_history(tmp_path):
    """
    3. executor 引流兜底回归测试:
    - 首轮: DA 成功 (URL: https://www.deviantart.com/artist/art/123456789)、X 失败
    - 写入发布历史记录 (publish_history.json)
    - 次轮: only_failed 重试 X (目标只有 x_twitter，DA 不在本轮执行)
    - 验证 payload 的 x_da_link 为历史中的 DA 当帖 URL，而非主页
    """
    from history_manager import PublishHistoryManager

    queue_file = tmp_path / "delayed_queue.json"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    mgr = DelayedQueueManager(queue_file=queue_file, assets_dir=tmp_path / "assets")

    img_file = tmp_path / "source.png"
    Image.new("RGB", (100, 100), color=(10, 20, 30)).save(img_file, "PNG")

    snapshot = {
        "title": "History Fallback Artwork",
        "description": "Test Desc",
        "short_text": "",
        "tags": ["art"],
        "is_nsfw": False,
        "is_ai": False,
        "images": [str(img_file)],
        "raw_images": [str(img_file)],
    }
    item = mgr.enroll(snapshot, delay_seconds=0, target_platforms=["deviantart", "x_twitter"])

    da_post_url = "https://www.deviantart.com/foxtontonton/art/123456789"
    default_homepage = "https://www.deviantart.com/foxtontonton"

    call_count = {"round": 1}
    captured_payloads = []

    class DummyDADisp:
        name = "deviantart"
        display_name = "DeviantArt"
        def check_login(self): return True
        def publish(self, payload):
            captured_payloads.append(("deviantart", payload))
            return True, f"发布成功 {da_post_url}", [da_post_url]

    class DummyXDisp:
        name = "x_twitter"
        display_name = "X"
        def check_login(self): return True
        def publish(self, payload):
            captured_payloads.append(("x_twitter", payload))
            if call_count["round"] == 1:
                return False, "Network error", []
            else:
                return True, "Published", ["https://x.com/status/999"]

    hist_mgr = PublishHistoryManager(data_dir=data_dir)
    executor = DelayedWaveExecutor(
        manager=mgr,
        config_provider=lambda: {"defaults": {"x_da_link": default_homepage}},
        dispatchers_factory=lambda: [DummyDADisp(), DummyXDisp()],
        history_manager=hist_mgr,
    )

    # 第一轮: DA 成功, X 失败 -> 写入 history
    ok, errs = executor.execute_item(item)
    assert ok is False
    assert item.status == "failed"
    assert "x_twitter" in errs
    assert "deviantart" not in errs

    # 验证历史记录已写入
    history_records = hist_mgr.load_history()
    assert len(history_records) >= 1

    # 第二轮: 重试仅失败平台 (only_failed=True, target_platforms 仅剩 x_twitter)
    call_count["round"] = 2
    captured_payloads.clear()

    ok2, errs2 = executor.execute_item(item, only_failed=True)
    assert ok2 is True
    assert len(captured_payloads) == 1
    plat, payload = captured_payloads[0]
    assert plat == "x_twitter"
    # 核心断言: payload 的 x_da_link 必须来自历史中的 DA 当帖 URL，而非主页！
    assert payload["x_da_link"] == da_post_url
    assert payload["da_post_url"] == da_post_url
    assert payload["actual_traffic_link"] == da_post_url


def test_tab_b_deviantart_image_count_warning_dialog(tmp_path, monkeypatch):
    """
    4. Tab B 发前张数警告回归测试:
    - 目标平台含 DA
    - 素材数 (11 张) > deviantart_max_images_per_deviation (默认 10)
    - 弹窗警告 "Patreon 不受影响，但到期公开波的 DA 将超限失败"
    - 用户选 Cancel -> 中止发布，按钮保持可用，不启动 PublishThread
    - 用户选 Yes -> 继续发布流程
    """
    import app as app_mod
    from PyQt5.QtWidgets import QApplication, QMessageBox
    qapp = QApplication.instance() or QApplication([])
    assert qapp is not None
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    win = app_mod.MainWindow()
    win.config["delayed_wave_enabled"] = True
    win.config["defaults"]["deviantart_max_images_per_deviation"] = 10
    win._update_delayed_wave_ui()

    from PyQt5.QtWidgets import QListWidgetItem
    from PyQt5.QtCore import Qt

    # 创建 11 张素材
    for i in range(11):
        img_p = tmp_path / f"batch_img_{i}.png"
        Image.new("RGB", (50, 50), color=(10 * i, 20, 30)).save(img_p, "PNG")
        it = QListWidgetItem(img_p.name)
        it.setData(Qt.UserRole, str(img_p))
        win.tab_b_img_list.addItem(it)

    win.tab_b_title_input.setText("Batch DA Limit Test")
    win.tab_b_target_da_cb.setChecked(True)

    warn_calls = []
    def mock_warning(parent, title, text, buttons, default_btn):
        warn_calls.append({"title": title, "text": text, "buttons": buttons})
        return current_user_choice[0]

    current_user_choice = [QMessageBox.Cancel]
    monkeypatch.setattr(QMessageBox, "warning", mock_warning)

    pub_threads = []
    class DummyPubThread:
        def __init__(self, selected, payload):
            pub_threads.append(self)
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()
        def start(self): pass
        def isRunning(self): return False

    monkeypatch.setattr(app_mod, "PublishThread", DummyPubThread)

    # Case A: 用户点击 Cancel
    win._start_tab_b_publishing()
    assert len(warn_calls) == 1
    assert "Patreon 不受影响，但到期公开波的 DA 将超限失败" in warn_calls[0]["text"]
    assert len(pub_threads) == 0
    assert win.tab_b_publish_btn.isEnabled() is True

    # Case B: 用户点击 Yes (继续)
    current_user_choice[0] = QMessageBox.Yes
    win._start_tab_b_publishing()
    assert len(warn_calls) == 2
    assert len(pub_threads) == 1


# =============================================================================
# 16. Patreon 发帖结构统一 (预告 / 墙下高清 / 附件反转 / 水印全局门禁 / A 页开关三态)
# =============================================================================

def test_patreon_unified_structure_tab_a_and_tab_b_and_public_visibility(tmp_path, monkeypatch):
    """
    §1 & §6 结构构建测试:
    - 单图帖 (visibility=patrons): patreon_images == [teaser_1, hd_1]，
      teaser_1 含公开安全打码 + 水印 + ×0.5 降档，hd_1 为全尺寸无码带水印，attachments == [hd_1]；
    - 多图帖 (3 张): patreon_images == [teaser_1, hd_1, hd_2, hd_3]，
      attachments == [hd_1, hd_2, hd_3]，与墙下展示图一一对应、同序；
    - visibility=public 时: 无 teaser_1，patreon_images == [hd_1, ..., hd_N]，attachments 同上。
    """
    import app as app_mod
    from PyQt5.QtWidgets import QApplication, QMessageBox, QListWidgetItem
    from PyQt5.QtCore import Qt

    qapp = QApplication.instance() or QApplication([])
    assert qapp is not None
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    win = app_mod.MainWindow()
    win.config["delayed_wave_enabled"] = True
    win._update_delayed_wave_ui()

    # 开启水印与打码 (固定黑条 100% 不透明度以验证像素遮挡)
    win.auto_censor_cb.setChecked(True)
    win.censor_style_combo.setCurrentIndex(0)  # black_bar
    win.censor_opacity_slider.setValue(100)
    win.watermark_enabled_cb.setChecked(True)
    win.watermark_apply_publish_cb.setChecked(True)
    win.watermark_text_input.setText("@PATREON_WM")

    # 创建 2 张 200x200 原图
    img1 = tmp_path / "art_p1.png"
    img2 = tmp_path / "art_p2.png"
    Image.new("RGB", (200, 200), color=(180, 90, 90)).save(img1, "PNG")
    Image.new("RGB", (200, 200), color=(90, 180, 90)).save(img2, "PNG")
    win.custom_censor_boxes[str(img1)] = [[60, 60, 140, 140]]

    captured = []

    class DummyPubThread:
        def __init__(self, selected, payload):
            captured.append((selected, payload))
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()
        def start(self):
            pass
        def isRunning(self):
            return False

    monkeypatch.setattr(app_mod, "PublishThread", DummyPubThread)
    monkeypatch.setattr(QMessageBox, "critical", MagicMock())

    # --- Part 1: Tab B 单图帖 (visibility=patrons) ---
    win.tab_b_title_input.setText("TabB Single Post")
    win.tab_b_img_list.clear()
    it1 = QListWidgetItem(img1.name)
    it1.setData(Qt.UserRole, str(img1))
    win.tab_b_img_list.addItem(it1)
    win.tab_b_visibility_combo.setCurrentIndex(0)  # patrons

    with patch.object(win.watermark_engine, "apply_watermark", wraps=win.watermark_engine.apply_watermark) as spy_wm:
        win._start_tab_b_publishing()
        # teaser_1 与 hd_1 均经过水印引擎 (调用 2 次)
        assert spy_wm.call_count == 2

    assert len(captured) == 1
    _, p_tab_b_single = captured[-1]
    pat_imgs_b1 = p_tab_b_single["patreon_images"]
    assert len(pat_imgs_b1) == 2  # [teaser_1, hd_1]
    teaser_b1, hd_b1 = Path(pat_imgs_b1[0]), Path(pat_imgs_b1[1])
    assert teaser_b1.exists() and hd_b1.exists()
    with Image.open(teaser_b1) as im_t, Image.open(hd_b1) as im_hd:
        assert im_t.size == (100, 100)  # ×0.5 降档
        assert im_hd.size == (200, 200)  # 全尺寸
        # teaser 中心点 (50, 50) 被公开安全黑条遮挡为 (0, 0, 0)，hd_1 中心点保持无码原色
        assert im_t.convert("RGB").getpixel((50, 50)) == (0, 0, 0)
        assert im_hd.convert("RGB").getpixel((100, 100)) != (0, 0, 0)
    assert p_tab_b_single["attachments"] == [str(hd_b1)]

    # --- Part 2: Tab B 多图帖 (2 张, visibility=patrons) ---
    it2 = QListWidgetItem(img2.name)
    it2.setData(Qt.UserRole, str(img2))
    win.tab_b_img_list.addItem(it2)
    win.tab_b_title_input.setText("TabB Multi Post")
    win._start_tab_b_publishing()

    _, p_tab_b_multi = captured[-1]
    assert len(p_tab_b_multi["patreon_images"]) == 3  # [teaser_1, hd_1, hd_2]
    assert p_tab_b_multi["attachments"] == p_tab_b_multi["patreon_images"][1:]
    assert len(p_tab_b_multi["attachments"]) == 2

    # --- Part 3: Tab B visibility=public (无预告图) ---
    win.tab_b_visibility_combo.setCurrentIndex(1)  # public
    win.tab_b_title_input.setText("TabB Public Post")
    win._start_tab_b_publishing()

    _, p_tab_b_pub = captured[-1]
    assert len(p_tab_b_pub["patreon_images"]) == 2  # [hd_1, hd_2] 无 teaser
    assert p_tab_b_pub["attachments"] == p_tab_b_pub["patreon_images"]

    # --- Part 4: Tab A 多图帖 (visibility=patrons vs visibility=public) ---
    win.title_input.setText("TabA Unified Post")
    win.image_paths = [str(img1), str(img2)]
    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(name == "patreon")
    pat_opts = win.platform_controls["patreon"]["options"]
    pat_opts["visibility"].setCurrentIndex(0)  # patrons
    pat_opts["attach_originals"].setChecked(True)

    win._start_publishing()
    _, p_tab_a_patrons = captured[-1]
    assert len(p_tab_a_patrons["patreon_images"]) == 3  # [teaser_1, hd_1, hd_2]
    assert p_tab_a_patrons["attachments"] == p_tab_a_patrons["patreon_images"][1:]
    for hd_p in p_tab_a_patrons["attachments"]:
        assert "_raw_wm" in Path(hd_p).name
        with Image.open(hd_p) as im_hd_a:
            assert im_hd_a.size == (200, 200)

    # 切换 Tab A 为 public -> 无 teaser
    pat_opts["visibility"].setCurrentIndex(1)  # public
    win._start_publishing()
    _, p_tab_a_public = captured[-1]
    assert len(p_tab_a_public["patreon_images"]) == 2  # [hd_1, hd_2]
    assert p_tab_a_public["attachments"] == p_tab_a_public["patreon_images"]


def test_tab_a_patreon_attach_originals_three_states_and_snapshot(tmp_path, monkeypatch):
    """
    §3 & §6 A 页「附带原图附件」开关三态与定时快照路径测试:
    1. 开关开 + 勾选 Patreon -> payload["attachments"] == [hd_1, ...]
    2. 开关关 + 勾选 Patreon -> "attachments" not in payload (其余结构不变)
    3. 开关开 + 未勾选 Patreon -> "attachments" not in payload
    4. 定时快照路径 -> 按快照素材与快照 platform_options["patreon_attach_originals"] 构建结构与 hd 附件
    """
    import app as app_mod
    from PyQt5.QtWidgets import QApplication

    qapp = QApplication.instance() or QApplication([])
    assert qapp is not None
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    win = app_mod.MainWindow()
    win.auto_censor_cb.setChecked(True)
    win.watermark_enabled_cb.setChecked(True)
    win.watermark_apply_publish_cb.setChecked(True)
    win.watermark_text_input.setText("@SNAP_WM")

    img1 = tmp_path / "state_test.jpg"
    Image.new("RGB", (160, 160), color=(60, 120, 180)).save(img1, "JPEG")
    win.image_paths = [str(img1)]
    win.title_input.setText("Attach Switch Three States")

    captured = []

    class DummyPubThread:
        def __init__(self, selected, payload):
            captured.append(([d.name for d in selected], payload))
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()
        def start(self):
            pass
        def isRunning(self):
            return False

    monkeypatch.setattr(app_mod, "PublishThread", DummyPubThread)

    pat_opts = win.platform_controls["patreon"]["options"]
    pat_opts["visibility"].setCurrentIndex(0)  # patrons

    # 态 1: 开关开 + 勾选 Patreon
    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(name == "patreon")
    pat_opts["attach_originals"].setChecked(True)
    win._start_publishing()
    _, p1 = captured[-1]
    assert len(p1["patreon_images"]) == 2
    assert "attachments" in p1
    assert p1["attachments"] == [p1["patreon_images"][1]]
    assert "_raw_wm" in Path(p1["attachments"][0]).name

    # 态 2: 开关关 + 勾选 Patreon -> 不写 attachments，patreon_images 结构不变
    pat_opts["attach_originals"].setChecked(False)
    win._start_publishing()
    _, p2 = captured[-1]
    assert len(p2["patreon_images"]) == 2
    assert "attachments" not in p2

    # 态 3: 开关开 + 未勾选 Patreon (仅勾选 DA) -> 不写 attachments
    pat_opts["attach_originals"].setChecked(True)
    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(name == "deviantart")
    win._start_publishing()
    _, p3 = captured[-1]
    assert "attachments" not in p3

    # 态 4: 定时快照路径 (UI 当前把开关关掉，但快照中 patreon_attach_originals=True)
    pat_opts["attach_originals"].setChecked(False)
    snap_img = tmp_path / "snap_patreon.jpg"
    Image.new("RGB", (180, 180), color=(140, 70, 110)).save(snap_img, "JPEG")
    snapshot = {
        "title": "Scheduled Patreon Post",
        "description": "Scheduled body",
        "tags": ["sched"],
        "image_paths": [str(snap_img)],
        "selected_platforms": ["patreon"],
        "censor_enabled": True,
        "is_nsfw": True,
        "is_ai": True,
        "platform_options": {
            "patreon_visibility": "patrons",
            "patreon_attach_originals": True,
        },
    }
    win._start_publishing(scheduled_snapshot=snapshot)
    _, p_snap = captured[-1]
    assert len(p_snap["patreon_images"]) == 2  # [teaser_1, hd_1]
    assert "attachments" in p_snap
    assert p_snap["attachments"] == [p_snap["patreon_images"][1]]
    assert "_raw_wm" in Path(p_snap["attachments"][0]).name


def test_watermark_global_gate_tab_a_and_tab_b_and_executor_note(tmp_path, monkeypatch):
    """
    §4 & §6 水印全局门禁测试:
    - 手动发布入口 (Tab A _start_publishing、Tab B _start_tab_b_publishing) 在无水印时弹确认框:
      「本次发布的图片将不带水印，仍要继续发布吗？」
      选取消 -> 中止发布，不启动 PublishThread；
      选继续 -> 正常启动发布。
    - 延迟公开波 executor 在无水印执行时 -> 打 error 日志 + 触发 error 通知 + 队列项结果注记含「无水印发布」。
    """
    import app as app_mod
    from PyQt5.QtWidgets import QApplication, QMessageBox, QListWidgetItem
    from PyQt5.QtCore import Qt

    qapp = QApplication.instance() or QApplication([])
    assert qapp is not None
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    win = app_mod.MainWindow()
    win.config["delayed_wave_enabled"] = True
    win._update_delayed_wave_ui()

    # 设置水印为关闭状态
    win.watermark_enabled_cb.setChecked(False)

    img = tmp_path / "wm_gate.jpg"
    Image.new("RGB", (120, 120), color=(80, 80, 80)).save(img, "JPEG")

    # 准备 Tab A 与 Tab B 输入
    win.title_input.setText("WM Gate Tab A")
    win.image_paths = [str(img)]
    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(name == "deviantart")

    win.tab_b_title_input.setText("WM Gate Tab B")
    it = QListWidgetItem(img.name)
    it.setData(Qt.UserRole, str(img))
    win.tab_b_img_list.addItem(it)

    dialog_calls = []
    user_decision = [QMessageBox.Cancel]

    def mock_question(parent, title, text, buttons, default_btn):
        dialog_calls.append((title, text))
        return user_decision[0]

    monkeypatch.setattr(QMessageBox, "question", mock_question)

    pub_threads = []

    class DummyPubThread:
        def __init__(self, selected, payload):
            pub_threads.append(payload)
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()
        def start(self):
            pass
        def isRunning(self):
            return False

    monkeypatch.setattr(app_mod, "PublishThread", DummyPubThread)

    # 1. Tab A 无水印 + 选取消 -> 不发布
    win._start_publishing()
    assert len(dialog_calls) == 1
    assert "本次发布的图片将不带水印，仍要继续发布吗？" in dialog_calls[-1][1]
    assert len(pub_threads) == 0

    # 2. Tab B 无水印 + 选取消 -> 不发布，按钮保持可用
    win._start_tab_b_publishing()
    assert len(dialog_calls) == 2
    assert "本次发布的图片将不带水印，仍要继续发布吗？" in dialog_calls[-1][1]
    assert len(pub_threads) == 0
    assert win.tab_b_publish_btn.isEnabled() is True

    # 3. 选继续 -> Tab A 与 Tab B 均正常发布
    user_decision[0] = QMessageBox.Yes
    win._start_publishing()
    assert len(pub_threads) == 1
    win._start_tab_b_publishing()
    assert len(pub_threads) == 2

    # 4. Executor 无水印执行 -> error 通知 + 队列项结果含「无水印发布」注记
    q_file = tmp_path / "wm_queue.json"
    a_dir = tmp_path / "wm_assets"
    mgr = DelayedQueueManager(queue_file=q_file, assets_dir=a_dir, enabled=True)
    item = mgr.enroll(
        {"title": "Unwatermarked Wave", "raw_images": [str(img)]},
        delay_seconds=0,
        target_platforms=["deviantart"],
    )

    notified_errors = []
    disp_da = DummyDispatcher("deviantart", "DeviantArt", succeed=True)
    executor = DelayedWaveExecutor(
        manager=mgr,
        dispatchers_factory=lambda: [disp_da],
        config_provider=lambda: {"censor": {"enabled": False}, "watermark": {"enabled": False}},
        notify_callback=lambda msg, lvl: notified_errors.append((msg, lvl)),
        cache_dir=tmp_path / "cache",
    )

    ok, errs = executor.execute_item(item)
    assert ok is True
    assert item.result_note == "无水印发布"
    assert any(lvl == "error" and "无水印发布" in msg for msg, lvl in notified_errors)
    reloaded = mgr.load_queue()[0]
    assert reloaded.result_note == "无水印发布"

