# -*- coding: utf-8 -*-
"""
Unit & integration tests for REQUIREMENTS_media_foundation.md:
- §1 & §3: 6 archive categories by pipeline role, SHA-256 deduplication, unsent intermediates excluded
- §2: Monthly directory helpers & cross-month boundary
- §3.2 & §3.3: Patreon bundle pool index & one-time migration with verification pass/fail
- §4.1 & §4.2: Retention cleanup (30d low_*, 60d other cache, delayed_assets protection, protected paths, legacy cache scan & confirm)
- §5: ASCII filename sanitization, fallback chain, collision _2/_3, OpenCV cv2.imread with emoji title
- §6: Text assets presets.json copy-only migration & read fallback
"""
import datetime
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

import config as config_mod
from delayed_wave_manager import downscale_image_half
from media_foundation import (
    ARCHIVE_CATEGORIES,
    archive_file_by_role,
    archive_publish_run_outputs,
    compute_file_sha256,
    execute_legacy_cache_cleanup,
    get_monthly_archive_dir,
    get_monthly_cache_dir,
    get_monthly_dir,
    migrate_legacy_bundle_pool,
    migrate_presets_file,
    resolve_non_colliding_path,
    run_scheduled_cleanup,
    sanitize_ascii_filename,
    sanitize_ascii_stem,
    scan_legacy_cache,
)


# ==============================================================================
# §5 文件名 ASCII 化、冲突顺延与 OpenCV 可读性测试
# ==============================================================================

def test_sanitize_ascii_stem_and_filename_variants():
    # 中文 + emoji + 空格 + 连续符号
    s1 = sanitize_ascii_stem("  📸 魅魔 Succubus — [Part 1]!!  ", fallback_stem="src_01")
    assert s1 == "Succubus_Part_1"
    assert all(c.isalnum() or c in "._-" for c in s1)

    # 全符号/纯中文/纯 emoji -> 空主干回退源文件主干
    s2 = sanitize_ascii_stem("📸🔥✨魅魔特集！？", fallback_stem="ComfyUI_00128_")
    assert s2 == "ComfyUI_00128"

    # 源文件主干也为空或纯中文 -> 回退 "file"
    s3 = sanitize_ascii_stem("📸🔥✨", fallback_stem="纯中文名")
    assert s3 == "file"

    # sanitize_ascii_filename 保留扩展名
    fn = sanitize_ascii_filename("📸 作品 A (测试).png", fallback_stem="fallback")
    assert fn == "A.png"


def test_resolve_non_colliding_path_appends_2_3_and_deduplicates_identical(tmp_path):
    target = tmp_path / "Succubus_0_da.png"
    target.write_bytes(b"CONTENT_V1")
    h1 = compute_file_sha256(target)

    # 相同内容（同 SHA-256）直接复用原路径，不顺延
    same_path = resolve_non_colliding_path(target, source_sha256=h1)
    assert same_path == target

    # 不同内容冲突时追加 _2，绝不覆盖原文件
    p2 = resolve_non_colliding_path(target, source_sha256="different_hash_v2")
    assert p2 == tmp_path / "Succubus_0_da_2.png"
    p2.write_bytes(b"CONTENT_V2")

    # 再次不同内容冲突追加 _3
    p3 = resolve_non_colliding_path(target, source_sha256="different_hash_v3")
    assert p3 == tmp_path / "Succubus_0_da_3.png"
    assert target.read_bytes() == b"CONTENT_V1"
    assert p2.read_bytes() == b"CONTENT_V2"


def test_cv2_imread_readable_on_variant_generated_with_emoji_title(tmp_path):
    """验证标题含 📸 等 emoji 时，生成的变体路径纯 ASCII 且 cv2.imread 可正常读取"""
    src_img = tmp_path / "master_001.png"
    arr = np.full((80, 100, 3), 180, dtype=np.uint8)
    Image.fromarray(arr).save(src_img)

    title_with_emoji = "📸 绝密作品 — 测试！"
    safe_stem = sanitize_ascii_stem(title_with_emoji, fallback_stem=src_img.stem)
    assert safe_stem == "master_001"

    cache_dir = get_monthly_cache_dir(tmp_path / "cache")
    variant_raw_path = cache_dir / f"{safe_stem}_0_da.png"
    downscaled_str = downscale_image_half(str(src_img), str(variant_raw_path))

    # OpenCV 必须能直接读取该文件且尺寸减半
    loaded = cv2.imread(downscaled_str)
    assert loaded is not None
    assert loaded.shape[:2] == (40, 50)


# ==============================================================================
# §2 按月目录助手与跨月边界测试
# ==============================================================================

def test_monthly_dir_helper_and_cross_month_boundary(tmp_path):
    t_apr = datetime.datetime(2026, 4, 30, 23, 59, 59)
    t_may = datetime.datetime(2026, 5, 1, 0, 1, 0)

    cache_root = tmp_path / "cache"
    archive_root = tmp_path / "archive"

    c_apr = get_monthly_cache_dir(cache_root, now=t_apr)
    c_may = get_monthly_cache_dir(cache_root, now=t_may)
    assert c_apr == cache_root / "2026-04"
    assert c_may == cache_root / "2026-05"
    assert c_apr.is_dir()
    assert c_may.is_dir()

    # 6 个归档分类目录在两个月分别落入对应月份子文件夹
    for cat in ARCHIVE_CATEGORIES:
        a_apr = get_monthly_archive_dir(archive_root, cat, now=t_apr)
        a_may = get_monthly_archive_dir(archive_root, cat, now=t_may)
        assert a_apr == archive_root / "2026-04" / cat
        assert a_may == archive_root / "2026-05" / cat
        assert a_apr.is_dir()
        assert a_may.is_dir()

    with pytest.raises(ValueError):
        get_monthly_archive_dir(archive_root, "invalid_category", now=t_apr)


# ==============================================================================
# §1 & §3 角色分流归档、未发出中间产物排除与 SHA-256 去重测试
# ==============================================================================

def test_role_based_archiving_all_6_categories_and_sha256_dedup(tmp_path):
    now = datetime.datetime(2026, 6, 15, 12, 0, 0)
    cache_dir = get_monthly_cache_dir(tmp_path / "cache", now=now)
    archive_dir = tmp_path / "archive"
    data_dir = tmp_path / "data"

    # 准备 6 类文件 + 1 个未发出的中间产物 (*_censored_wm.png)
    hd_img = cache_dir / "work_0_raw_wm.png"
    low_img = cache_dir / "work_0_da.png"
    hd_gif = cache_dir / "work_1_pat_wm.gif"
    low_gif = cache_dir / "work_1_pat_teaser.gif"
    mp4_orig = tmp_path / "source" / "work_1.mp4"
    mp4_render = cache_dir / "work_1_x_render.mp4"
    unsent_intermediate = cache_dir / "work_0_censored_wm.png"

    mp4_orig.parent.mkdir(parents=True, exist_ok=True)
    hd_img.write_bytes(b"HD_IMAGE_BYTES_001")
    low_img.write_bytes(b"LOW_IMAGE_BYTES_001")
    hd_gif.write_bytes(b"HD_GIF_BYTES_001")
    low_gif.write_bytes(b"LOW_GIF_BYTES_001")
    mp4_orig.write_bytes(b"MP4_ORIGINAL_BYTES_001")
    mp4_render.write_bytes(b"MP4_RENDER_BYTES_001")
    unsent_intermediate.write_bytes(b"UNSENT_INTERMEDIATE_BYTES")

    payload = {
        "title": "Archive6CatTest",
        "da_images": [str(low_img)],
        "x_images": [str(low_img)],
        "x_video": str(mp4_render),
        "patreon_images": [str(low_gif), str(hd_img), str(hd_gif)],
        "patreon_hd_images": [str(hd_img), str(hd_gif)],
        "x_video_paths": {str(hd_gif): str(mp4_orig)},
        "_media_roles": {
            str(hd_img): "hd_images",
            str(low_img): "low_images",
            str(hd_gif): "hd_gif",
            str(low_gif): "low_gif",
            str(mp4_orig): "mp4_originals",
            str(mp4_render): "mp4_renders",
        },
    }
    results = {
        "deviantart": (True, "ok", []),
        "x_twitter": (True, "ok", []),
        "patreon": (True, "ok", []),
    }

    archived_map = archive_publish_run_outputs(
        payload=payload,
        results=results,
        archive_root=archive_dir,
        data_dir=data_dir,
        now=now,
    )

    # 验证 6 类全部归档到各自的 data/archive/2026-06/<category>/
    for cat in ARCHIVE_CATEGORIES:
        assert len(archived_map[cat]) == 1, f"Category {cat} should have 1 archived file"
        arch_file = Path(archived_map[cat][0])
        assert arch_file.exists()
        assert arch_file.parent == archive_dir / "2026-06" / cat

    # 验证未发出的中间产物 (*_censored_wm.png) 绝不进入 archive
    all_archived_names = [Path(p).name for paths in archived_map.values() for p in paths]
    assert unsent_intermediate.name not in all_archived_names

    # 再次归档相同内容（同 SHA-256）不产生第二份文件
    dup_source = cache_dir / "another_name_same_bytes.png"
    dup_source.write_bytes(b"HD_IMAGE_BYTES_001")
    reused_path = archive_file_by_role(dup_source, "hd_images", archive_root=archive_dir, now=now)
    assert reused_path == Path(archived_map["hd_images"][0])
    hd_files_in_dir = list((archive_dir / "2026-06" / "hd_images").iterdir())
    assert len(hd_files_in_dir) == 1

    # 验证 §3.2 patreon_bundle_pool.json 索引全部指向 archive/2026-06/...
    pool_index = json.loads((data_dir / "patreon_bundle_pool.json").read_text(encoding="utf-8"))
    assert isinstance(pool_index, list) and len(pool_index) == 1
    entry = pool_index[0]
    assert entry["month"] == "2026-06"
    assert len(entry["images"]) == 2
    assert len(entry["videos"]) == 1
    for p in entry["images"] + entry["videos"]:
        assert Path(p).exists()
        assert str(archive_dir / "2026-06") in str(Path(p))


# ==============================================================================
# §3.3 附件池存量一次性迁移（校验通过删除 vs 校验失败保留）
# ==============================================================================

def test_migrate_legacy_bundle_pool_success_and_failure_preservation(tmp_path):
    # Case 1: 正常迁移并在 SHA-256 校验通过后清除旧目录
    data_dir = tmp_path / "data_ok"
    legacy_dir = data_dir / "patreon_bundle_assets" / "2026-03"
    legacy_dir.mkdir(parents=True, exist_ok=True)

    f_img = legacy_dir / "art1.png"
    f_gif = legacy_dir / "anim1.gif"
    f_mp4 = legacy_dir / "anim1.mp4"
    f_img.write_bytes(b"LEGACY_PNG_1")
    f_gif.write_bytes(b"LEGACY_GIF_1")
    f_mp4.write_bytes(b"LEGACY_MP4_1")

    pool_file = data_dir / "patreon_bundle_pool.json"
    pool_file.write_text(
        json.dumps({
            "2026-03": {
                "images": [str(f_img), str(f_gif)],
                "videos": [str(f_mp4)],
            }
        }),
        encoding="utf-8",
    )

    ok, _ = migrate_legacy_bundle_pool(data_dir=data_dir)
    assert ok is True
    assert not (data_dir / "patreon_bundle_assets").exists(), "校验通过后应删除旧 patreon_bundle_assets 目录"

    new_index = json.loads(pool_file.read_text(encoding="utf-8"))
    assert len(new_index["2026-03"]["images"]) == 2
    assert len(new_index["2026-03"]["videos"]) == 1
    for p in new_index["2026-03"]["images"] + new_index["2026-03"]["videos"]:
        assert Path(p).exists()
        assert "archive" in Path(p).parts

    # Case 2: 模拟索引中存在缺失文件 -> 校验失败，旧目录必须保留不删
    data_fail = tmp_path / "data_fail"
    legacy_fail_dir = data_fail / "patreon_bundle_assets" / "2026-03"
    legacy_fail_dir.mkdir(parents=True, exist_ok=True)
    existing_f = legacy_fail_dir / "keep_me.png"
    existing_f.write_bytes(b"KEEP_ME_SAFE")

    pool_fail_file = data_fail / "patreon_bundle_pool.json"
    pool_fail_file.write_text(
        json.dumps({
            "2026-03": {
                "images": [str(existing_f), str(legacy_fail_dir / "missing_file.png")],
                "videos": [],
            }
        }),
        encoding="utf-8",
    )

    ok_fail, _ = migrate_legacy_bundle_pool(data_dir=data_fail)
    assert ok_fail is False
    assert (data_fail / "patreon_bundle_assets").exists(), "任一条校验失败时严禁删除旧目录"
    assert existing_f.exists()


# ==============================================================================
# §4.1 & §4.2 自动留存清理、受保护路径零改动与一次性存量扫描确认
# ==============================================================================

def test_scheduled_cleanup_retention_boundaries_and_protected_paths(tmp_path):
    now = datetime.datetime(2026, 7, 1, 12, 0, 0)
    now_ts = now.timestamp()
    day_sec = 86400

    data_dir = tmp_path / "data"
    cache_dir = data_dir / "cache" / "2026-04"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 构造 15 天、40 天、70 天文件
    low_15d = cache_dir / "work_0_da.jpg"
    low_40d = cache_dir / "work_1_da.jpg"
    other_40d = cache_dir / "work_1_raw_wm.png"
    other_70d = cache_dir / "work_2_raw_wm.png"

    for f in (low_15d, low_40d, other_40d, other_70d):
        f.write_bytes(b"X" * 128)

    os.utime(low_15d, (now_ts - 15 * day_sec, now_ts - 15 * day_sec))
    os.utime(low_40d, (now_ts - 40 * day_sec, now_ts - 40 * day_sec))
    os.utime(other_40d, (now_ts - 40 * day_sec, now_ts - 40 * day_sec))
    os.utime(other_70d, (now_ts - 70 * day_sec, now_ts - 70 * day_sec))

    # 构造受保护路径：archive/、text_assets/、draft.json、publish_history.json（全部设为 90 天前）
    arch_f = data_dir / "archive" / "2026-01" / "hd_images" / "old_arch.png"
    arch_f.parent.mkdir(parents=True, exist_ok=True)
    arch_f.write_bytes(b"ARCHIVE_SAFE")
    os.utime(arch_f, (now_ts - 90 * day_sec, now_ts - 90 * day_sec))

    preset_f = data_dir / "text_assets" / "presets.json"
    preset_f.parent.mkdir(parents=True, exist_ok=True)
    preset_f.write_text("{}", encoding="utf-8")
    os.utime(preset_f, (now_ts - 90 * day_sec, now_ts - 90 * day_sec))

    # 构造 delayed_assets：1 个未完成 pending（即使 50 天前也绝不删），1 个 done 已满 35 天（应删）
    delayed_pending_dir = data_dir / "delayed_assets" / "item_pending"
    delayed_pending_dir.mkdir(parents=True, exist_ok=True)
    (delayed_pending_dir / "snap.png").write_bytes(b"PENDING_SAFE")
    os.utime(delayed_pending_dir / "snap.png", (now_ts - 50 * day_sec, now_ts - 50 * day_sec))

    delayed_done_dir = data_dir / "delayed_assets" / "item_done_old"
    delayed_done_dir.mkdir(parents=True, exist_ok=True)
    (delayed_done_dir / "snap_done.png").write_bytes(b"DONE_EXPIRED")

    done_35d_iso = (now - datetime.timedelta(days=35)).isoformat()
    pending_50d_iso = (now - datetime.timedelta(days=50)).isoformat()
    (data_dir / "delayed_queue.json").write_text(
        json.dumps([
            {"id": "item_pending", "status": "pending", "created_at": pending_50d_iso},
            {"id": "item_done_old", "status": "done", "completed_at": done_35d_iso},
        ]),
        encoding="utf-8",
    )

    stats = run_scheduled_cleanup(data_dir=data_dir, force=True, now=now)
    assert stats["skipped"] is False
    assert stats["deleted_counts"]["low_images"] == 1
    assert stats["deleted_counts"]["hd_images"] == 1
    assert stats["deleted_counts"]["delayed_assets"] == 1

    # 验证留存边界
    assert low_15d.exists(), "15 天 low_* 未满 30 天必须保留"
    assert not low_40d.exists(), "40 天 low_* 超过 30 天必须删除"
    assert other_40d.exists(), "40 天非 low_* 缓存未满 60 天必须保留"
    assert not other_70d.exists(), "70 天非 low_* 缓存超过 60 天必须删除"

    # 验证受保护路径与未完成 delayed_assets 零改动
    assert arch_f.exists(), "archive/ 永不自动清理"
    assert preset_f.exists(), "text_assets/ 永不自动清理"
    assert (delayed_pending_dir / "snap.png").exists(), "未完成 delayed_assets 严禁删除"
    assert not delayed_done_dir.exists(), "已完成且超 30 天的 delayed_assets 应被清理"

    # 验证每日一次节流（非 force 时同日跳过）
    stats_second = run_scheduled_cleanup(data_dir=data_dir, force=False, now=now)
    assert stats_second["skipped"] is True


def test_legacy_cache_scan_and_confirm_required(tmp_path):
    cache_root = tmp_path / "cache"
    archive_root = tmp_path / "archive"
    cache_root.mkdir(parents=True, exist_ok=True)

    # 1. 高清带水印成品 (1600x1200 -> 归入 hd_images 迁入 archive)
    hd_wm = cache_root / "art_0_raw_wm.png"
    Image.new("RGB", (1600, 1200), color=(10, 20, 30)).save(hd_wm)

    # 2. 低清发布图 (800x600 _da -> 归入 low_images 清理)
    low_da = cache_root / "art_0_da.jpg"
    Image.new("RGB", (800, 600), color=(40, 50, 60)).save(low_da)

    # 3. 无法识别文件 (不动)
    unknown_f = cache_root / "notes.txt"
    unknown_f.write_text("keep me", encoding="utf-8")

    report = scan_legacy_cache(cache_root=cache_root)
    assert report["total_files"] == 3
    assert len(report["to_archive"]) == 1
    assert len(report["to_delete"]) == 1
    assert len(report["unrecognized"]) == 1

    # 未确认时（confirmed=False）严禁删除任何文件
    res_unconfirmed = execute_legacy_cache_cleanup(
        cache_root=cache_root,
        archive_root=archive_root,
        confirmed=False,
    )
    assert res_unconfirmed["executed"] is False
    assert hd_wm.exists()
    assert low_da.exists()
    assert unknown_f.exists()

    # 用户确认后（confirmed=True）执行分拣：hd 迁入 archive，low 删除，unknown 保留
    res_confirmed = execute_legacy_cache_cleanup(
        cache_root=cache_root,
        archive_root=archive_root,
        confirmed=True,
    )
    assert res_confirmed["executed"] is True
    assert res_confirmed["archived_count"] == 1
    assert res_confirmed["deleted_count"] == 1
    assert not hd_wm.exists()
    assert not low_da.exists()
    assert unknown_f.exists()


# ==============================================================================
# §6 文案素材专夹 data/text_assets/presets.json 迁移测试
# ==============================================================================

def test_presets_migration_copy_only_and_read_priority(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    old_presets = data_dir / "presets.json"
    new_presets = data_dir / "text_assets" / "presets.json"

    sample_data = {
        "defaults": {"artist_name": "FoxArtist"},
        "watermark_presets": {"p1": {"text": "@Fox"}},
    }
    old_presets.write_text(json.dumps(sample_data, ensure_ascii=False), encoding="utf-8")

    active_path, ok = migrate_presets_file(data_dir=data_dir)
    assert ok is True
    assert active_path == new_presets
    assert new_presets.exists(), "应自动复制到 data/text_assets/presets.json"
    assert old_presets.exists(), "旧 data/presets.json 严禁自动删除"

    migrated_data = json.loads(new_presets.read_text(encoding="utf-8"))
    assert len(migrated_data) == len(sample_data)
    assert migrated_data["defaults"]["artist_name"] == "FoxArtist"

    # 验证 config.load_config 优先读新位置，且 save_config 写入新位置
    monkeypatch.setattr(config_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(config_mod, "TEXT_ASSETS_DIR", data_dir / "text_assets")
    monkeypatch.setattr(config_mod, "LEGACY_PRESETS_FILE", old_presets)
    monkeypatch.setattr(config_mod, "PRESETS_FILE", new_presets)

    loaded = config_mod.load_config()
    assert loaded["defaults"]["artist_name"] == "FoxArtist"
    assert old_presets.exists()
