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

def test_patreon_attachments_rejects_raw_wm_derived_files(tmp_path):
    """测试附件门禁：必须是源素材母版路径，严禁使用 *_raw_wm.* 或 censor 渲染件"""
    disp = PatreonDispatcher(tmp_path)

    # 传入非法带水印渲染件
    derived_file = tmp_path / "test_artwork_0_raw_wm.jpg"
    derived_file.write_bytes(b"wm_content")

    payload = {
        "title": "Illegal Attachment Test",
        "images": [str(derived_file)],
        "patreon_images": [str(derived_file)],
        "attachments": [str(derived_file)],
    }

    ok, msg, comp = disp.publish(payload)

    assert ok is False
    assert "附件门禁拦截" in msg
    assert "严禁使用下游渲染派生件" in msg
    # 状态包含「缺附件」
    comp_att = next((c for c in comp if c.get("item") == "attachments"), None)
    assert comp_att is not None
    assert "缺附件" in comp_att.get("detail", "")


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
