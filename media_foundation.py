import hashlib
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image
from logger import get_logger

logger = get_logger()

# §1. 全局唯一归档六分类
ARCHIVE_CATEGORIES: Tuple[str, ...] = (
    "hd_images",      # 高清图：无码 + 水印 + 全尺寸渲染件（如 *_raw_wm.png）
    "low_images",     # 低画质图：免费平台发送件（*_freehalf.*, *_xopt.jpg, *_pat_teaser.*, Bluesky 超限再压 JPEG）
    "hd_gif",         # 高画质 GIF：无码 + 全尺寸 GIF 渲染件
    "low_gif",        # 低画质 GIF：预告 GIF（打码+×0.5）与降级发送件
    "mp4_originals",  # GIF 配对 MP4 原件（不加工）
    "mp4_renders",    # MP4 发送版：带水印渲染件（X / Bluesky 用）
)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
GIF_EXTS = {".gif"}
VIDEO_EXTS = {".mp4"}

_MONTH_DIR_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_ASCII_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


# ==============================================================================
# §5. 文件名 ASCII 化与撞名处理
# ==============================================================================

def _clean_single_stem(raw_stem: str) -> str:
    """Internal helper to sanitize a single stem string to pure ASCII."""
    if not raw_stem:
        return ""
    cleaned = _ASCII_SAFE_RE.sub("_", str(raw_stem))
    cleaned = _MULTI_UNDERSCORE_RE.sub("_", cleaned)
    cleaned = cleaned.strip("_.")
    cleaned = _MULTI_UNDERSCORE_RE.sub("_", cleaned).strip("_")
    return cleaned


def sanitize_ascii_stem(text: str, fallback_stem: str = "") -> str:
    """
    Sanitize a filename stem to pure ASCII (`[A-Za-z0-9._-]`).
    - Non-ASCII characters (Chinese, emoji, spaces, symbols) -> `_`
    - Consecutive `_` collapsed to single `_`
    - Leading/trailing `_` stripped
    - If empty after sanitization -> falls back to sanitized `fallback_stem`
    - If still empty -> `"file"`
    """
    cleaned = _clean_single_stem(text)
    if cleaned:
        return cleaned
    if fallback_stem:
        fb_cleaned = _clean_single_stem(Path(str(fallback_stem)).stem or str(fallback_stem))
        if fb_cleaned:
            return fb_cleaned
    return "file"


def sanitize_ascii_filename(filename: str, fallback_stem: str = "") -> str:
    """
    Sanitize a full filename (stem + extension) to pure ASCII.
    Preserves extension; falls back to `fallback_stem` or `"file"` if stem becomes empty.
    """
    p = Path(str(filename))
    raw_ext = p.suffix
    raw_stem = p.name[:-len(raw_ext)] if raw_ext else p.name

    clean_stem = sanitize_ascii_stem(raw_stem, fallback_stem=fallback_stem)
    if raw_ext:
        ext_body = re.sub(r"[^A-Za-z0-9]+", "", raw_ext.lstrip("."))
        clean_ext = f".{ext_body}" if ext_body else ""
    else:
        clean_ext = ""
    return f"{clean_stem}{clean_ext}"


def compute_file_sha256(file_path: Union[str, Path]) -> str:
    """Compute SHA-256 hex digest of a file on disk."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_bytes_sha256(data: bytes) -> str:
    """Compute SHA-256 hex digest of in-memory bytes."""
    return hashlib.sha256(data).hexdigest()


def resolve_non_colliding_path(
    target_dir: Union[str, Path],
    filename: Optional[str] = None,
    source_file: Optional[Union[str, Path]] = None,
    content_bytes: Optional[bytes] = None,
    source_sha256: Optional[str] = None,
    fallback_stem: str = "",
) -> Path:
    """
    Resolve a pure-ASCII file path inside `target_dir` that never overwrites
    an existing file with different content.
    Can also be called with a single full file path `resolve_non_colliding_path(full_path, ...)`.
    - If `target_dir / clean_name` does not exist -> return it.
    - If it exists and its SHA-256 matches `source_file`, `content_bytes`, or `source_sha256` -> return it.
    - Otherwise -> append `_2`, `_3`, ... to stem until a free or matching-hash path is found.
    """
    if filename is None:
        full_p = Path(target_dir)
        target_dir_path = full_p.parent
        filename = full_p.name
    else:
        target_dir_path = Path(target_dir)
    target_dir_path.mkdir(parents=True, exist_ok=True)

    clean_name = sanitize_ascii_filename(filename, fallback_stem=fallback_stem)
    candidate = target_dir_path / clean_name
    if not candidate.exists():
        return candidate

    ref_hash: Optional[str] = source_sha256
    if ref_hash is None and source_file is not None and Path(source_file).exists():
        try:
            ref_hash = compute_file_sha256(source_file)
        except Exception:
            ref_hash = None
    elif ref_hash is None and content_bytes is not None:
        ref_hash = compute_bytes_sha256(content_bytes)

    if ref_hash is not None:
        try:
            if candidate.is_file() and compute_file_sha256(candidate) == ref_hash:
                return candidate
        except Exception:
            pass

    p = Path(clean_name)
    ext = p.suffix
    stem = p.name[:-len(ext)] if ext else p.name

    idx = 2
    while True:
        next_candidate = target_dir_path / f"{stem}_{idx}{ext}"
        if not next_candidate.exists():
            return next_candidate
        if ref_hash is not None:
            try:
                if next_candidate.is_file() and compute_file_sha256(next_candidate) == ref_hash:
                    return next_candidate
            except Exception:
                pass
        idx += 1


# ==============================================================================
# §2. 按月子文件夹单一路径助手
# ==============================================================================

def get_year_month_str(now: Optional[Union[datetime, float]] = None) -> str:
    """Return `<YYYY-MM>` in local timezone for the given timestamp or current time."""
    if now is None:
        dt = datetime.now()
    elif isinstance(now, (int, float)):
        dt = datetime.fromtimestamp(now)
    elif isinstance(now, datetime):
        dt = now
    else:
        dt = datetime.now()
    return dt.strftime("%Y-%m")


_PYTEST_SANDBOX_DIR: Optional[Path] = None


def get_pytest_sandbox_dir() -> Path:
    """Return an isolated sandbox directory for test media cache & archive."""
    global _PYTEST_SANDBOX_DIR
    if _PYTEST_SANDBOX_DIR is None:
        import tempfile
        _PYTEST_SANDBOX_DIR = Path(tempfile.gettempdir()) / "artcrosspost_pytest_sandbox"
        _PYTEST_SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    return _PYTEST_SANDBOX_DIR


def get_monthly_dir(
    root_dir: Union[str, Path],
    category: Optional[str] = None,
    now: Optional[Union[datetime, float]] = None,
) -> Path:
    """
    Single converged helper for constructing monthly media directories.
    - For cache: `get_monthly_dir(cache_dir, now=now)` -> `<cache_dir>/<YYYY-MM>`
    - For archive: `get_monthly_dir(archive_dir, category, now=now)` -> `<archive_dir>/<YYYY-MM>/<category>`
    Ensures the directory exists before returning.
    """
    ym = get_year_month_str(now)
    base = Path(root_dir)
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        try:
            from config import DATA_DIR
            prod_data = DATA_DIR.resolve()
            resolved_base = base.resolve()
            if resolved_base == prod_data or prod_data in resolved_base.parents:
                rel_to_prod = resolved_base.relative_to(prod_data)
                base = get_pytest_sandbox_dir() / rel_to_prod
        except Exception:
            pass

    if base.name == ym and _MONTH_DIR_RE.match(base.name):
        month_dir = base
    else:
        month_dir = base / ym

    if category is not None:
        if category not in ARCHIVE_CATEGORIES:
            raise ValueError(f"Unknown archive category '{category}'. Must be one of {ARCHIVE_CATEGORIES}")
        target = month_dir / category
    else:
        target = month_dir

    target.mkdir(parents=True, exist_ok=True)
    return target


def get_monthly_cache_dir(
    base_cache_dir: Optional[Union[str, Path]] = None,
    now: Optional[Union[datetime, float]] = None,
) -> Path:
    """Return `data/cache/<YYYY-MM>` and ensure it exists."""
    if base_cache_dir is None:
        from config import DATA_DIR
        base_cache_dir = DATA_DIR / "cache"
    return get_monthly_dir(base_cache_dir, category=None, now=now)


def get_monthly_archive_dir(
    category_or_dir: Union[str, Path],
    base_archive_dir: Optional[Union[str, Path]] = None,
    now: Optional[Union[datetime, float]] = None,
) -> Path:
    """Return `data/archive/<YYYY-MM>/<category>` and ensure it exists."""
    if isinstance(category_or_dir, str) and category_or_dir in ARCHIVE_CATEGORIES:
        category = category_or_dir
        arch_dir = base_archive_dir
    elif isinstance(base_archive_dir, str) and base_archive_dir in ARCHIVE_CATEGORIES:
        arch_dir = category_or_dir
        category = base_archive_dir
    else:
        category = str(category_or_dir)
        arch_dir = base_archive_dir
    if arch_dir is None:
        from config import DATA_DIR
        arch_dir = DATA_DIR / "archive"
    return get_monthly_dir(arch_dir, category=category, now=now)


# ==============================================================================
# §1 & §3. 按角色归档与附件池索引合一
# ==============================================================================

def archive_file_by_role(
    src_path: Union[str, Path],
    category: str,
    base_archive_dir: Optional[Union[str, Path]] = None,
    archive_root: Optional[Union[str, Path]] = None,
    now: Optional[Union[datetime, float]] = None,
    fallback_stem: str = "",
) -> Optional[Path]:
    """
    Archive a single file into `data/archive/<YYYY-MM>/<category>/` by its pipeline role.
    - Deduplicates by SHA-256: if a file with identical SHA-256 already exists in the
      category directory (current month or any existing month in `base_archive_dir`),
      skips copying and returns the existing archived path.
    - Sanitizes target filename to pure ASCII and avoids overwriting different content.
    """
    if category not in ARCHIVE_CATEGORIES:
        raise ValueError(f"Invalid archive category: {category}")

    src = Path(src_path)
    if not src.exists() or not src.is_file():
        logger.warning(f"归档跳过：源文件不存在 {src}")
        return None

    if base_archive_dir is None and archive_root is not None:
        base_archive_dir = archive_root
    if base_archive_dir is None:
        from config import DATA_DIR
        base_archive_dir = DATA_DIR / "archive"
    archive_root_path = Path(base_archive_dir)
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        try:
            from config import DATA_DIR
            prod_data = DATA_DIR.resolve()
            resolved_arch = archive_root_path.resolve()
            if resolved_arch == prod_data or prod_data in resolved_arch.parents:
                rel_to_prod = resolved_arch.relative_to(prod_data)
                archive_root_path = get_pytest_sandbox_dir() / rel_to_prod
        except Exception:
            pass

    src_hash = compute_file_sha256(src)
    target_cat_dir = get_monthly_archive_dir(category, base_archive_dir=archive_root_path, now=now)

    # 1. Check current month's category dir first, then other months in archive_root
    search_dirs: List[Path] = [target_cat_dir]
    if archive_root_path.exists():
        for m_dir in sorted(archive_root_path.iterdir()):
            if m_dir.is_dir() and _MONTH_DIR_RE.match(m_dir.name):
                cand_cat = m_dir / category
                if cand_cat.exists() and cand_cat != target_cat_dir:
                    search_dirs.append(cand_cat)

    for s_dir in search_dirs:
        for existing in sorted(s_dir.iterdir()):
            if existing.is_file():
                try:
                    if existing.stat().st_size == src.stat().st_size and compute_file_sha256(existing) == src_hash:
                        logger.info(f"归档复用既有同哈希文件 ({category}): {existing}")
                        return existing
                except Exception:
                    continue

    # 2. Not yet archived -> copy to non-colliding ASCII path in target_cat_dir
    dest_path = resolve_non_colliding_path(
        target_cat_dir,
        src.name,
        source_file=src,
        fallback_stem=fallback_stem or src.stem,
    )
    if not dest_path.exists():
        shutil.copy2(src, dest_path)
        logger.info(f"已归档 ({category}): {src.name} -> {dest_path}")
    return dest_path


def classify_role_for_sent_file(
    file_path: Union[str, Path],
    role_map: Optional[Dict[str, str]] = None,
    default_quality: str = "low",
) -> Optional[str]:
    """
    Determine the archive category for a file sent in the pipeline.
    Prioritizes explicit pipeline role registered in `role_map`;
    falls back to role context (`default_quality` + media type).
    """
    p = Path(file_path)
    str_p = str(p)
    if role_map:
        if str_p in role_map and role_map[str_p] in ARCHIVE_CATEGORIES:
            return role_map[str_p]
        try:
            res_p = str(p.resolve())
            for k, v in role_map.items():
                if v in ARCHIVE_CATEGORIES and (k == res_p or str(Path(k).resolve()) == res_p):
                    return v
        except Exception:
            pass

    ext = p.suffix.lower()
    name_lower = p.name.lower()

    if ext in VIDEO_EXTS:
        if default_quality == "original" or "raw" in name_lower or "orig" in name_lower:
            return "mp4_originals"
        return "mp4_renders"

    if ext in GIF_EXTS:
        if default_quality == "hd" or "_raw_wm" in name_lower or "_hd" in name_lower:
            if "_pat_teaser" not in name_lower and "_freehalf" not in name_lower and "_teaser" not in name_lower:
                return "hd_gif"
        return "low_gif"

    if ext in IMAGE_EXTS:
        if default_quality == "hd" or "_raw_wm" in name_lower or "_pat_display_wm" in name_lower:
            if (
                "_freehalf" not in name_lower
                and "_xopt" not in name_lower
                and "_pat_teaser" not in name_lower
                and "_bsky_comp" not in name_lower
            ):
                return "hd_images"
        return "low_images"

    return None


def update_patreon_bundle_pool_index(
    pool_file: Union[str, Path],
    title: str,
    archived_hd_files: List[Path],
    archived_mp4_originals: List[Path],
    post_url: str = "",
    now: Optional[Union[datetime, float]] = None,
) -> Dict[str, Any]:
    """
    §3.2 Register archived Patreon HD files and MP4 originals in `data/patreon_bundle_pool.json`.
    Does not copy files; stores only archive paths.
    """
    pool_path = Path(pool_file)
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        try:
            from config import DATA_DIR
            prod_data = DATA_DIR.resolve()
            if pool_path.resolve() == prod_data or prod_data in pool_path.resolve().parents:
                pool_path = get_pytest_sandbox_dir() / pool_path.resolve().relative_to(prod_data)
        except Exception:
            pass
    pool_path.parent.mkdir(parents=True, exist_ok=True)

    entries: List[Dict[str, Any]] = []
    if pool_path.exists():
        try:
            with open(pool_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    entries = loaded
                elif isinstance(loaded, dict) and isinstance(loaded.get("items"), list):
                    entries = loaded["items"]
        except Exception as e:
            logger.warning(f"读取 patreon_bundle_pool.json 失败，将重建索引: {e}")

    ym = get_year_month_str(now)
    ts_iso = (
        datetime.fromtimestamp(now).isoformat()
        if isinstance(now, (int, float))
        else (now.isoformat() if isinstance(now, datetime) else datetime.now().isoformat())
    )

    img_paths = [str(p) for p in archived_hd_files if p is not None]
    vid_paths = [str(p) for p in archived_mp4_originals if p is not None]

    record = {
        "id": f"pat_{int(time.time() * 1000)}_{len(entries) + 1}",
        "title": title or "Untitled",
        "month": ym,
        "created_at": ts_iso,
        "post_url": post_url or "",
        "images": img_paths,
        "videos": vid_paths,
    }
    entries.append(record)

    with open(pool_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    return record


def archive_publish_run_outputs(
    payload: Dict[str, Any],
    publish_results: Optional[Dict[str, Any]] = None,
    results: Optional[Dict[str, Any]] = None,
    base_archive_dir: Optional[Union[str, Path]] = None,
    archive_root: Optional[Union[str, Path]] = None,
    pool_file: Optional[Union[str, Path]] = None,
    data_dir: Optional[Union[str, Path]] = None,
    now: Optional[Union[datetime, float]] = None,
) -> Dict[str, List[str]]:
    """
    §3.1 Archive files actually received by each succeeded platform at the end of a publish run.
    - When Patreon succeeds, also archives its HD rendered files (`hd_images` / `hd_gif`)
      and paired MP4 originals (`mp4_originals`) and indexes them into `patreon_bundle_pool.json`.
    - Never raises or alters `publish_results`; logs loud warning/error on any failure.
    Returns a summary dict mapping `category -> [archived_path_str, ...]`.
    """
    if publish_results is None and results is not None:
        publish_results = results
    if base_archive_dir is None and archive_root is not None:
        base_archive_dir = archive_root
    archived_by_category: Dict[str, List[str]] = {cat: [] for cat in ARCHIVE_CATEGORIES}
    if not isinstance(payload, dict) or not isinstance(publish_results, dict):
        return archived_by_category

    try:
        if base_archive_dir is None:
            if data_dir is not None:
                base_archive_dir = Path(data_dir) / "archive"
            else:
                from config import DATA_DIR
                base_archive_dir = DATA_DIR / "archive"
        if pool_file is None:
            if data_dir is not None:
                pool_file = Path(data_dir) / "patreon_bundle_pool.json"
            else:
                from config import DATA_DIR
                pool_file = DATA_DIR / "patreon_bundle_pool.json"

        if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
            try:
                from config import DATA_DIR
                prod_data = DATA_DIR.resolve()
                if base_archive_dir and (Path(base_archive_dir).resolve() == prod_data or prod_data in Path(base_archive_dir).resolve().parents):
                    base_archive_dir = get_pytest_sandbox_dir() / Path(base_archive_dir).resolve().relative_to(prod_data)
                if pool_file and (Path(pool_file).resolve() == prod_data or prod_data in Path(pool_file).resolve().parents):
                    pool_file = get_pytest_sandbox_dir() / Path(pool_file).resolve().relative_to(prod_data)
            except Exception:
                pass

        role_map: Dict[str, str] = dict(payload.get("_media_roles") or {})
        title = str(payload.get("title") or "artwork")
        fallback_stem = sanitize_ascii_stem(title)

        def _is_platform_success(val: Any) -> bool:
            if isinstance(val, (tuple, list)) and len(val) >= 1:
                return bool(val[0])
            if isinstance(val, dict):
                return bool(val.get("success"))
            return bool(val)

        def _extract_platform_url(val: Any) -> str:
            if isinstance(val, (tuple, list)) and len(val) >= 2 and isinstance(val[1], str):
                if val[1].startswith("http"):
                    return val[1]
            if isinstance(val, dict) and isinstance(val.get("url"), str):
                return val["url"]
            return ""

        def _archive_one(fpath: Union[str, Path], category: str) -> Optional[Path]:
            try:
                res = archive_file_by_role(
                    fpath,
                    category,
                    base_archive_dir=base_archive_dir,
                    now=now,
                    fallback_stem=fallback_stem,
                )
                if res is not None:
                    res_str = str(res)
                    if res_str not in archived_by_category[category]:
                        archived_by_category[category].append(res_str)
                return res
            except Exception as ex:
                logger.error(f"归档文件失败 ({category}: {fpath}): {ex}")
                return None

        # 1. DeviantArt
        if _is_platform_success(publish_results.get("deviantart")):
            da_files = payload.get("da_images") or payload.get("images") or []
            for f in da_files:
                cat = classify_role_for_sent_file(f, role_map, default_quality="low")
                if cat:
                    _archive_one(f, cat)

        # 2. X (Twitter)
        if _is_platform_success(publish_results.get("x_twitter")):
            x_vid = payload.get("x_video")
            if x_vid:
                cat = classify_role_for_sent_file(x_vid, role_map, default_quality="render")
                _archive_one(x_vid, cat or "mp4_renders")
            else:
                x_files = payload.get("x_images") or payload.get("images") or []
                for f in x_files:
                    cat = classify_role_for_sent_file(f, role_map, default_quality="low")
                    if cat:
                        _archive_one(f, cat)

        # 3. Bluesky
        if _is_platform_success(publish_results.get("bluesky")):
            bsky_vid = payload.get("bluesky_video")
            if bsky_vid:
                cat = classify_role_for_sent_file(bsky_vid, role_map, default_quality="render")
                _archive_one(bsky_vid, cat or "mp4_renders")
            else:
                bsky_files = payload.get("bluesky_sent_images") or payload.get("bluesky_images") or payload.get("images") or []
                for f in bsky_files:
                    cat = classify_role_for_sent_file(f, role_map, default_quality="low")
                    if cat:
                        _archive_one(f, cat)

        # 4. Civitai
        if _is_platform_success(publish_results.get("civitai")):
            civ_files = payload.get("civitai_images") or payload.get("images") or []
            x_vid_map = payload.get("x_video_paths") or {}
            for f in civ_files:
                p = Path(f)
                if p.suffix.lower() == ".gif":
                    paired_mp4 = x_vid_map.get(str(p)) or x_vid_map.get(str(p.resolve()))
                    if paired_mp4 and Path(paired_mp4).exists():
                        _archive_one(paired_mp4, "mp4_originals")
                    continue
                cat = classify_role_for_sent_file(f, role_map, default_quality="low")
                if cat:
                    _archive_one(f, cat)

        # 5. Ko-fi
        if _is_platform_success(publish_results.get("kofi")):
            kofi_files = payload.get("kofi_images") or payload.get("images") or []
            for f in kofi_files:
                cat = classify_role_for_sent_file(f, role_map, default_quality="low")
                if cat:
                    _archive_one(f, cat)

        # 6. Patreon (sent teaser + HD display/attachments + paired MP4 originals -> bundle pool index)
        if _is_platform_success(publish_results.get("patreon")):
            pat_opts = (payload.get("platform_options") or {}).get("patreon") or {}
            vis_mode = pat_opts.get("visibility", "patrons")
            pat_imgs = list(payload.get("patreon_images") or payload.get("images") or [])
            pat_hd_list = list(payload.get("patreon_hd_images") or payload.get("attachments") or [])

            archived_hd_for_pool: List[Path] = []
            archived_mp4_for_pool: List[Path] = []

            if pat_imgs:
                if vis_mode != "public" and len(pat_imgs) >= 1 and (not pat_hd_list or pat_imgs[0] not in pat_hd_list):
                    teaser_file = pat_imgs[0]
                    t_cat = classify_role_for_sent_file(teaser_file, role_map, default_quality="low")
                    if t_cat:
                        _archive_one(teaser_file, t_cat)
                    display_Slice = pat_imgs[1:]
                else:
                    display_Slice = pat_imgs

                for hd_f in display_Slice:
                    h_cat = classify_role_for_sent_file(hd_f, role_map, default_quality="hd")
                    if h_cat:
                        arch_p = _archive_one(hd_f, h_cat)
                        if arch_p and arch_p not in archived_hd_for_pool:
                            archived_hd_for_pool.append(arch_p)

            for hd_f in pat_hd_list:
                h_cat = classify_role_for_sent_file(hd_f, role_map, default_quality="hd")
                if h_cat:
                    arch_p = _archive_one(hd_f, h_cat)
                    if arch_p and arch_p not in archived_hd_for_pool:
                        archived_hd_for_pool.append(arch_p)

            # Paired MP4 originals for Patreon bundle pool
            x_vid_map = payload.get("x_video_paths") or {}
            extra_mp4s = list(payload.get("patreon_mp4_originals") or [])
            for mp4_cand in list(x_vid_map.values()) + extra_mp4s:
                if mp4_cand and Path(mp4_cand).exists():
                    arch_mp4 = _archive_one(mp4_cand, "mp4_originals")
                    if arch_mp4 and arch_mp4 not in archived_mp4_for_pool:
                        archived_mp4_for_pool.append(arch_mp4)

            if archived_hd_for_pool or archived_mp4_for_pool:
                pat_url = _extract_platform_url(publish_results.get("patreon"))
                update_patreon_bundle_pool_index(
                    pool_file=pool_file,
                    title=title,
                    archived_hd_files=archived_hd_for_pool,
                    archived_mp4_originals=archived_mp4_for_pool,
                    post_url=pat_url,
                    now=now,
                )

    except Exception as e:
        logger.error(f"发布后归档流程异常（不影响发布结果）: {e}")

    return archived_by_category


# ==============================================================================
# §3.3 与结构支的对账迁移（一次性）
# ==============================================================================

def migrate_legacy_bundle_pool(
    data_dir: Optional[Union[str, Path]] = None,
    now: Optional[Union[datetime, float]] = None,
) -> Tuple[bool, str]:
    """
    §3.3 One-time migration from `data/patreon_bundle_assets/<YYYY-MM>/` to
    `data/archive/<YYYY-MM>/<category>/` and rewriting `data/patreon_bundle_pool.json`.
    - Verifies every indexed file exists and its SHA-256 matches before deleting
      `data/patreon_bundle_assets/`.
    - If any check fails -> aborts migration, logs loud error, and preserves old directory intact.
    """
    if data_dir is None:
        from config import DATA_DIR
        data_dir = DATA_DIR
    data_root = Path(data_dir)
    legacy_dir = data_root / "patreon_bundle_assets"
    pool_file = data_root / "patreon_bundle_pool.json"
    archive_root = data_root / "archive"

    if not legacy_dir.exists():
        return True, "无需迁移：旧版 patreon_bundle_assets 目录不存在"

    try:
        pool_raw: Any = []
        pool_entries: List[Dict[str, Any]] = []
        is_dict_by_month = False
        if pool_file.exists():
            with open(pool_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                pool_raw = loaded
                if isinstance(loaded, list):
                    pool_entries = loaded
                elif isinstance(loaded, dict):
                    is_dict_by_month = True
                    for k, v in loaded.items():
                        if isinstance(v, dict):
                            pool_entries.append(v)
                else:
                    raise ValueError("patreon_bundle_pool.json 格式异常")

        # Pre-check all indexed files and their expected hashes (if provided)
        source_hashes: Dict[str, str] = {}
        for entry in pool_entries:
            expected_hashes = entry.get("hashes") or entry.get("sha256") or {}
            for key in ("images", "videos"):
                for fpath_str in entry.get(key, []):
                    fp = Path(fpath_str)
                    if not fp.exists() or not fp.is_file():
                        msg = f"附件池迁移校验失败：索引文件不存在 {fp}"
                        logger.error(msg)
                        return False, msg
                    actual_hash = compute_file_sha256(fp)
                    exp_h = expected_hashes.get(fpath_str) or expected_hashes.get(fp.name)
                    if exp_h and exp_h != actual_hash:
                        msg = f"附件池迁移校验失败：文件哈希不匹配 {fp} (期望 {exp_h}, 实际 {actual_hash})"
                        logger.error(msg)
                        return False, msg
                    source_hashes[str(fp.resolve())] = actual_hash

        # Also collect any unindexed files inside legacy_dir
        all_legacy_files: List[Path] = [p for p in legacy_dir.rglob("*") if p.is_file()]
        for fp in all_legacy_files:
            res_key = str(fp.resolve())
            if res_key not in source_hashes:
                source_hashes[res_key] = compute_file_sha256(fp)

        # Copy all legacy files into archive by category & month
        migrated_path_map: Dict[str, Path] = {}
        for fp in all_legacy_files:
            # Infer month from parent directory if YYYY-MM
            parent_name = fp.parent.name
            if _MONTH_DIR_RE.match(parent_name):
                month_dt = datetime.strptime(parent_name + "-01", "%Y-%m-%d")
            else:
                month_dt = datetime.fromtimestamp(fp.stat().st_mtime)

            ext = fp.suffix.lower()
            if ext in VIDEO_EXTS:
                cat = "mp4_originals"
            elif ext in GIF_EXTS:
                cat = "hd_gif"
            else:
                cat = "hd_images"

            archived_p = archive_file_by_role(
                fp,
                cat,
                base_archive_dir=archive_root,
                now=month_dt,
                fallback_stem=fp.stem,
            )
            if archived_p is None or not archived_p.exists():
                msg = f"附件池迁移失败：无法归档文件 {fp}"
                logger.error(msg)
                return False, msg
            migrated_path_map[str(fp.resolve())] = archived_p
            migrated_path_map[str(fp)] = archived_p

        # Build updated pool entries and verify every target file exists & hash matches
        updated_raw = json.loads(json.dumps(pool_raw))
        target_entries = (
            [v for v in updated_raw.values() if isinstance(v, dict)]
            if is_dict_by_month and isinstance(updated_raw, dict)
            else (updated_raw if isinstance(updated_raw, list) else [])
        )
        for entry in target_entries:
            for key in ("images", "videos"):
                new_list: List[str] = []
                for old_p_str in entry.get(key, []):
                    old_p = Path(old_p_str)
                    old_res = str(old_p.resolve())
                    new_p = migrated_path_map.get(old_res) or migrated_path_map.get(old_p_str)
                    if new_p is None or not new_p.exists():
                        msg = f"附件池迁移后置校验失败：目标归档文件缺失 {old_p_str}"
                        logger.error(msg)
                        return False, msg
                    new_hash = compute_file_sha256(new_p)
                    orig_hash = source_hashes.get(old_res)
                    if orig_hash and new_hash != orig_hash:
                        msg = f"附件池迁移后置校验失败：归档文件哈希不一致 {new_p}"
                        logger.error(msg)
                        return False, msg
                    new_list.append(str(new_p))
                entry[key] = new_list

        # All checks passed: write updated pool.json and remove legacy_dir
        with open(pool_file, "w", encoding="utf-8") as f:
            json.dump(updated_raw, f, ensure_ascii=False, indent=2)

        shutil.rmtree(legacy_dir)
        logger.info("附件池一次性迁移完成：已迁入 data/archive 并清理旧版 patreon_bundle_assets 目录")
        return True, "迁移成功"

    except Exception as e:
        msg = f"附件池迁移中止：{e}"
        logger.error(msg)
        return False, msg


# ==============================================================================
# §4. 清理规则（§4.1 自动留存期清理 & §4.2 存量扫描与确认执行）
# ==============================================================================

LOW_RETENTION_DAYS = 30
DEFAULT_CACHE_RETENTION_DAYS = 60
DELAYED_ASSETS_RETENTION_DAYS = 30


def infer_cache_file_category(
    file_path: Union[str, Path],
    sibling_max_dims: Optional[Dict[str, Tuple[int, int]]] = None,
) -> str:
    """
    Classify a file in `data/cache/` by suffix + filename markers + pixel dimensions.
    Returns one of:
      - `"low_images"`, `"low_gif"`
      - `"intermediate"` (e.g. `*_censored_wm.*` full-size intermediates)
      - `"hd_images"`, `"hd_gif"`
      - `"mp4_renders"`, `"mp4_originals"`
      - `"unrecognized"`
    """
    p = Path(file_path)
    ext = p.suffix.lower()
    name_lower = p.name.lower()

    if ext in VIDEO_EXTS:
        if any(tag in name_lower for tag in ("_x_video", "_bsky_video", "_wm", "_render", "video_render")):
            return "mp4_renders"
        return "mp4_originals"

    if ext in GIF_EXTS:
        if any(tag in name_lower for tag in ("_pat_teaser", "_freehalf", "_teaser", "_half", "_low")):
            return "low_gif"
        try:
            with Image.open(p) as im:
                w, h = im.size
            if sibling_max_dims:
                base_key = re.sub(r"(_\d+)?(_raw_wm|_censored_wm|_pat_teaser|_freehalf|_teaser)?$", "", p.stem.lower())
                ref_dim = sibling_max_dims.get(base_key)
                if ref_dim and (w < ref_dim[0] * 0.75 or h < ref_dim[1] * 0.75):
                    return "low_gif"
            return "hd_gif"
        except Exception:
            return "unrecognized"

    if ext in IMAGE_EXTS:
        if "_censored_wm" in name_lower and not any(t in name_lower for t in ("_freehalf", "_xopt", "_pat_teaser")):
            return "intermediate"
        if any(tag in name_lower for tag in ("_freehalf", "_xopt", "_pat_teaser", "_bsky_comp", "_teaser", "_low", "_da.", "_x.", "_bsky.")):
            return "low_images"
        if "_raw_wm" in name_lower or "_pat_display_wm" in name_lower:
            return "hd_images"
        try:
            with Image.open(p) as im:
                w, h = im.size
        except Exception:
            return "unrecognized"

        if sibling_max_dims:
            base_key = re.sub(
                r"(_\d+)?(_raw_wm|_censored_wm|_pat_teaser|_freehalf|_xopt|_bsky_comp|_pat_display_wm)?$",
                "",
                p.stem.lower(),
            )
            ref_dim = sibling_max_dims.get(base_key)
            if ref_dim and (w < ref_dim[0] * 0.75 or h < ref_dim[1] * 0.75):
                return "low_images"
            if ref_dim and w == ref_dim[0] and h == ref_dim[1]:
                return "hd_images"

        # Default heuristic by pixel resolution if no marker/sibling matched
        if max(w, h) >= 1600:
            return "hd_images"
        return "low_images"

    return "unrecognized"


def _build_sibling_max_dims(files: List[Path]) -> Dict[str, Tuple[int, int]]:
    dims: Dict[str, Tuple[int, int]] = {}
    for p in files:
        if p.suffix.lower() not in (IMAGE_EXTS | GIF_EXTS):
            continue
        try:
            with Image.open(p) as im:
                w, h = im.size
            base_key = re.sub(
                r"(_\d+)?(_raw_wm|_censored_wm|_pat_teaser|_freehalf|_xopt|_bsky_comp|_pat_display_wm|_teaser)?$",
                "",
                p.stem.lower(),
            )
            prev = dims.get(base_key)
            if prev is None or (w * h > prev[0] * prev[1]):
                dims[base_key] = (w, h)
        except Exception:
            continue
    return dims


def run_scheduled_cleanup(
    data_dir: Optional[Union[str, Path]] = None,
    now: Optional[Union[datetime, float]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    §4.1 Automatic retention cleanup:
    - `low_*` (`low_images`, `low_gif`) in `data/cache/`: retained 30 days.
    - Other files in `data/cache/`: retained 60 days.
    - Empty expired `<YYYY-MM>` month subdirectories in `data/cache/` are removed.
    - `data/delayed_assets/<item_id>`: deleted only if queue item is `done` or `cancelled`
      and completed > 30 days ago. Unexpired / pending / running / failed items are NEVER touched.
    - Protected paths (`archive`, `text_assets`, `browser_profiles`, `cookies`, configs,
      drafts, publish_history, user source dirs) are NEVER touched.
    - Throttled to once per calendar day unless `force=True`.
    """
    if data_dir is None:
        from config import DATA_DIR
        data_dir = DATA_DIR
    data_root = Path(data_dir)
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        try:
            from config import DATA_DIR
            prod_data = DATA_DIR.resolve()
            if data_root.resolve() == prod_data or prod_data in data_root.resolve().parents:
                data_root = get_pytest_sandbox_dir()
        except Exception:
            pass
    cache_dir = data_root / "cache"
    delayed_assets_dir = data_root / "delayed_assets"
    queue_file = data_root / "delayed_queue.json"
    state_file = data_root / "cleanup_state.json"

    if now is None:
        now_ts = time.time()
        now_dt = datetime.now()
    elif isinstance(now, (int, float)):
        now_ts = float(now)
        now_dt = datetime.fromtimestamp(now_ts)
    else:
        now_dt = now
        now_ts = now_dt.timestamp()

    today_str = now_dt.strftime("%Y-%m-%d")

    if not force and state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                st = json.load(f)
            if st.get("last_cleanup_date") == today_str:
                return {"skipped": True, "reason": "throttled_daily", "date": today_str}
        except Exception:
            pass

    categories_summary: Dict[str, Dict[str, int]] = {
        "low_images": {"count": 0, "bytes": 0},
        "low_gif": {"count": 0, "bytes": 0},
        "intermediate": {"count": 0, "bytes": 0},
        "hd_images": {"count": 0, "bytes": 0},
        "hd_gif": {"count": 0, "bytes": 0},
        "mp4_renders": {"count": 0, "bytes": 0},
        "mp4_originals": {"count": 0, "bytes": 0},
        "other_cache": {"count": 0, "bytes": 0},
        "delayed_assets": {"count": 0, "bytes": 0},
    }
    removed_month_dirs: List[str] = []

    # 1. Clean expired cache files
    if cache_dir.exists():
        all_cache_files = [p for p in cache_dir.rglob("*") if p.is_file()]
        sibling_dims = _build_sibling_max_dims(all_cache_files)

        for fp in all_cache_files:
            try:
                st_stat = fp.stat()
                age_days = (now_ts - st_stat.st_mtime) / 86400.0
                cat = infer_cache_file_category(fp, sibling_dims)
                retention_days = (
                    LOW_RETENTION_DAYS if cat in ("low_images", "low_gif") else DEFAULT_CACHE_RETENTION_DAYS
                )
                if age_days > retention_days:
                    f_size = st_stat.st_size
                    fp.unlink()
                    bucket = cat if cat in categories_summary else "other_cache"
                    categories_summary[bucket]["count"] += 1
                    categories_summary[bucket]["bytes"] += f_size
            except Exception as e:
                logger.warning(f"清理缓存文件失败 {fp}: {e}")

        # Remove empty <YYYY-MM> month directories in cache_dir
        current_ym = get_year_month_str(now_dt)
        for sub in sorted(cache_dir.iterdir()):
            if sub.is_dir() and _MONTH_DIR_RE.match(sub.name):
                try:
                    remaining = list(sub.iterdir())
                    if not remaining and sub.name != current_ym:
                        sub.rmdir()
                        removed_month_dirs.append(sub.name)
                except Exception:
                    pass

    # 2. Clean expired terminal delayed_assets subdirectories
    if delayed_assets_dir.exists() and queue_file.exists():
        try:
            with open(queue_file, "r", encoding="utf-8") as f:
                queue_items = json.load(f)
            if isinstance(queue_items, list):
                for item in queue_items:
                    if not isinstance(item, dict):
                        continue
                    status = item.get("status", "")
                    if status not in ("done", "cancelled"):
                        continue
                    item_id = item.get("id")
                    if not item_id:
                        continue
                    item_dir = delayed_assets_dir / str(item_id)
                    if not item_dir.exists() or not item_dir.is_dir():
                        continue

                    # Determine completion timestamp
                    ref_ts: Optional[float] = None
                    for ts_key in ("completed_at", "executed_at", "updated_at", "scheduled_at", "created_at"):
                        val = item.get(ts_key)
                        if val:
                            try:
                                ref_ts = datetime.fromisoformat(str(val)).timestamp()
                                break
                            except Exception:
                                continue
                    if ref_ts is None:
                        ref_ts = item_dir.stat().st_mtime

                    age_days = (now_ts - ref_ts) / 86400.0
                    if age_days > DELAYED_ASSETS_RETENTION_DAYS:
                        dir_bytes = sum(p.stat().st_size for p in item_dir.rglob("*") if p.is_file())
                        dir_files = sum(1 for p in item_dir.rglob("*") if p.is_file())
                        shutil.rmtree(item_dir)
                        categories_summary["delayed_assets"]["count"] += max(1, dir_files)
                        categories_summary["delayed_assets"]["bytes"] += dir_bytes
        except Exception as e:
            logger.warning(f"清理过期公开波资产失败: {e}")

    total_deleted = sum(v["count"] for v in categories_summary.values())
    total_bytes = sum(v["bytes"] for v in categories_summary.values())

    # Update state file
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "last_cleanup_date": today_str,
                    "last_cleanup_ts": now_ts,
                    "total_deleted": total_deleted,
                    "total_bytes": total_bytes,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass

    cat_log_parts = [
        f"{k}: {v['count']} 个 ({v['bytes']} B)"
        for k, v in categories_summary.items()
        if v["count"] > 0
    ]
    summary_msg = (
        f"🧹 缓存定期清理完成：共删除 {total_deleted} 个过期文件，释放 {total_bytes} B"
        + (f" [{', '.join(cat_log_parts)}]" if cat_log_parts else " [无过期文件]")
    )
    logger.info(summary_msg)

    return {
        "skipped": False,
        "date": today_str,
        "categories": categories_summary,
        "deleted_counts": {k: v["count"] for k, v in categories_summary.items()},
        "deleted_bytes": {k: v["bytes"] for k, v in categories_summary.items()},
        "removed_month_dirs": removed_month_dirs,
        "total_deleted": total_deleted,
        "total_bytes": total_bytes,
        "summary_message": summary_msg,
    }


def scan_legacy_cache(
    cache_dir: Optional[Union[str, Path]] = None,
    cache_root: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """
    §4.2 Scan `data/cache/` and produce a report grouped by `category × month`
    using file extension + pixel dimensions + naming heuristics.
    Does NOT modify or delete any files.
    """
    if cache_dir is None and cache_root is not None:
        cache_dir = cache_root
    if cache_dir is None:
        from config import DATA_DIR
        cache_dir = DATA_DIR / "cache"
    cache_root_path = Path(cache_dir)

    all_categories = (
        "low_images",
        "low_gif",
        "intermediate",
        "hd_images",
        "hd_gif",
        "mp4_renders",
        "mp4_originals",
        "unrecognized",
    )
    by_category: Dict[str, Dict[str, Any]] = {
        c: {"count": 0, "bytes": 0, "files": [], "action": (
            "delete" if c in ("low_images", "low_gif", "intermediate")
            else ("archive" if c in ARCHIVE_CATEGORIES else "keep")
        )}
        for c in all_categories
    }
    by_month_and_category: Dict[str, Dict[str, Dict[str, Any]]] = {}

    if not cache_root_path.exists():
        return {
            "cache_dir": str(cache_root_path),
            "by_category": by_category,
            "by_month_and_category": by_month_and_category,
            "total_count": 0,
            "total_files": 0,
            "total_bytes": 0,
            "to_delete": [],
            "to_archive": [],
            "unrecognized": [],
            "summary_text": "缓存目录为空，无需分拣。",
        }

    all_files = sorted([p for p in cache_root_path.rglob("*") if p.is_file()])
    sibling_dims = _build_sibling_max_dims(all_files)

    for fp in all_files:
        try:
            st = fp.stat()
            f_bytes = st.st_size
            parent_name = fp.parent.name
            if _MONTH_DIR_RE.match(parent_name):
                ym = parent_name
            else:
                ym = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m")
        except Exception:
            f_bytes = 0
            ym = get_year_month_str()

        cat = infer_cache_file_category(fp, sibling_dims)
        if cat not in by_category:
            cat = "unrecognized"

        by_category[cat]["count"] += 1
        by_category[cat]["bytes"] += f_bytes
        by_category[cat]["files"].append({"path": str(fp), "month": ym, "bytes": f_bytes})

        if ym not in by_month_and_category:
            by_month_and_category[ym] = {
                c: {"count": 0, "bytes": 0, "files": []} for c in all_categories
            }
        by_month_and_category[ym][cat]["count"] += 1
        by_month_and_category[ym][cat]["bytes"] += f_bytes
        by_month_and_category[ym][cat]["files"].append(str(fp))

    total_count = sum(v["count"] for v in by_category.values())
    total_bytes = sum(v["bytes"] for v in by_category.values())

    lines = [f"存量缓存扫描报告 (共 {total_count} 个文件, {total_bytes / 1024:.1f} KB):"]
    for ym in sorted(by_month_and_category.keys()):
        lines.append(f"\n[{ym}]")
        for c in all_categories:
            info = by_month_and_category[ym][c]
            if info["count"] > 0:
                act_label = (
                    "删除" if by_category[c]["action"] == "delete"
                    else ("迁入归档区" if by_category[c]["action"] == "archive" else "保留不动")
                )
                lines.append(f"  - {c}: {info['count']} 个 ({info['bytes'] / 1024:.1f} KB) -> {act_label}")

    to_delete_list = [
        item["path"]
        for c in ("low_images", "low_gif", "intermediate")
        for item in by_category[c]["files"]
    ]
    to_archive_list = [
        item["path"]
        for c in ARCHIVE_CATEGORIES
        if by_category.get(c, {}).get("action") == "archive"
        for item in by_category[c]["files"]
    ]
    unrec_list = [item["path"] for item in by_category["unrecognized"]["files"]]

    return {
        "cache_dir": str(cache_root_path),
        "by_category": by_category,
        "by_month_and_category": by_month_and_category,
        "total_count": total_count,
        "total_files": total_count,
        "total_bytes": total_bytes,
        "to_delete": to_delete_list,
        "to_archive": to_archive_list,
        "unrecognized": unrec_list,
        "summary_text": "\n".join(lines),
    }


def execute_legacy_cache_cleanup(
    scan_report: Optional[Dict[str, Any]] = None,
    base_archive_dir: Optional[Union[str, Path]] = None,
    archive_root: Optional[Union[str, Path]] = None,
    cache_root: Optional[Union[str, Path]] = None,
    confirmed: bool = False,
) -> Dict[str, Any]:
    """
    §4.2 Execute the one-time legacy cache cleanup according to `scan_report`.
    - Requires `confirmed=True`; if `confirmed=False`, performs zero modifications.
    - `low_*` and `intermediate` -> deleted.
    - `hd_*` and `mp4_*` -> moved into `data/archive/<YYYY-MM>/<category>/`.
    - `unrecognized` -> untouched.
    """
    if scan_report is None and cache_root is not None:
        scan_report = scan_legacy_cache(cache_dir=cache_root)
    if scan_report is None:
        scan_report = {}
    if base_archive_dir is None and archive_root is not None:
        base_archive_dir = archive_root
    if not confirmed:
        return {
            "executed": False,
            "deleted_count": 0,
            "deleted_bytes": 0,
            "archived_count": 0,
            "archived_bytes": 0,
            "untouched_count": scan_report.get("by_category", {}).get("unrecognized", {}).get("count", 0),
        }

    if base_archive_dir is None:
        from config import DATA_DIR
        base_archive_dir = DATA_DIR / "archive"
    archive_root = Path(base_archive_dir)
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        try:
            from config import DATA_DIR
            prod_data = DATA_DIR.resolve()
            if archive_root.resolve() == prod_data or prod_data in archive_root.resolve().parents:
                archive_root = get_pytest_sandbox_dir() / "archive"
        except Exception:
            pass

    deleted_count = 0
    deleted_bytes = 0
    archived_count = 0
    archived_bytes = 0
    untouched_count = 0
    archived_paths: List[str] = []

    by_category = scan_report.get("by_category", {})
    for cat, info in by_category.items():
        action = info.get("action", "keep")
        for item in info.get("files", []):
            fp = Path(item["path"]) if isinstance(item, dict) else Path(item)
            ym = item.get("month") if isinstance(item, dict) else None
            f_bytes = item.get("bytes", 0) if isinstance(item, dict) else (fp.stat().st_size if fp.exists() else 0)

            if not fp.exists():
                continue

            if action == "delete":
                try:
                    fp.unlink()
                    deleted_count += 1
                    deleted_bytes += f_bytes
                except Exception as e:
                    logger.warning(f"存量清理删除失败 {fp}: {e}")
            elif action == "archive" and cat in ARCHIVE_CATEGORIES:
                try:
                    month_dt = datetime.strptime(f"{ym}-01", "%Y-%m-%d") if (ym and _MONTH_DIR_RE.match(ym)) else None
                    dest = archive_file_by_role(
                        fp,
                        cat,
                        base_archive_dir=archive_root,
                        now=month_dt,
                        fallback_stem=fp.stem,
                    )
                    if dest is not None and dest.exists():
                        if dest.resolve() != fp.resolve():
                            fp.unlink()
                        archived_count += 1
                        archived_bytes += f_bytes
                        archived_paths.append(str(dest))
                except Exception as e:
                    logger.warning(f"存量清理归档失败 {fp}: {e}")
            else:
                untouched_count += 1

    logger.info(
        f"📊 存量缓存分拣执行完成：已删除低画质/中间件 {deleted_count} 个 ({deleted_bytes} B)，"
        f"已归档高清/视频 {archived_count} 个 ({archived_bytes} B)，未识别保留 {untouched_count} 个。"
    )
    return {
        "executed": True,
        "deleted_count": deleted_count,
        "deleted_bytes": deleted_bytes,
        "archived_count": archived_count,
        "archived_bytes": archived_bytes,
        "untouched_count": untouched_count,
        "archived_paths": archived_paths,
    }


def list_unreferenced_source_files(
    source_dir: Union[str, Path],
    data_dir: Optional[Union[str, Path]] = None,
) -> List[Path]:
    """
    §4.2 List files in `source_dir` that have never been referenced by
    `publish_history.json`, `draft.json`, or `delayed_queue.json`.
    Read-only: never deletes user source files.
    """
    src_root = Path(source_dir)
    if not src_root.exists() or not src_root.is_dir():
        return []

    if data_dir is None:
        from config import DATA_DIR
        data_dir = DATA_DIR
    data_root = Path(data_dir)

    referenced_names = set()
    referenced_paths = set()

    for state_name in ("publish_history.json", "draft.json", "delayed_queue.json"):
        sf = data_root / state_name
        if not sf.exists():
            continue
        try:
            raw_text = sf.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            text_blob = json.dumps(data, ensure_ascii=False)
            for p in src_root.iterdir():
                if p.is_file():
                    if p.name in text_blob or str(p) in text_blob or str(p.resolve()) in text_blob:
                        referenced_names.add(p.name)
                        referenced_paths.add(str(p.resolve()))
        except Exception:
            continue

    unreferenced: List[Path] = []
    for p in sorted(src_root.iterdir()):
        if p.is_file() and p.suffix.lower() in (IMAGE_EXTS | GIF_EXTS | VIDEO_EXTS):
            if p.name not in referenced_names and str(p.resolve()) not in referenced_paths:
                unreferenced.append(p)
    return unreferenced


# ==============================================================================
# §6. 文案素材专夹 (`data/text_assets/presets.json`) 安全迁移
# ==============================================================================

def migrate_presets_file(data_dir: Optional[Union[str, Path]] = None) -> Tuple[Path, bool]:
    """
    §6 Safe copy-only migration for `presets.json`:
    - Ensures `data/text_assets/` exists.
    - When `data/text_assets/presets.json` does not exist and `data/presets.json` exists:
      copies `data/presets.json` -> `data/text_assets/presets.json` and verifies top-level
      entry count matches.
    - Read order: `data/text_assets/presets.json` first, `data/presets.json` fallback.
    - NEVER deletes `data/presets.json`.
    Returns `(active_presets_path, migrated_ok)`.
    """
    if data_dir is None:
        from config import DATA_DIR
        data_dir = DATA_DIR
    data_root = Path(data_dir)
    text_assets_dir = data_root / "text_assets"
    text_assets_dir.mkdir(parents=True, exist_ok=True)

    new_presets = text_assets_dir / "presets.json"
    old_presets = data_root / "presets.json"

    if new_presets.exists():
        return new_presets, True

    if old_presets.exists():
        try:
            with open(old_presets, "r", encoding="utf-8") as f:
                old_data = json.load(f)
            tmp_target = text_assets_dir / "presets.json.tmp"
            shutil.copy2(old_presets, tmp_target)
            with open(tmp_target, "r", encoding="utf-8") as f:
                new_data = json.load(f)

            if isinstance(old_data, dict) and isinstance(new_data, dict):
                if len(old_data) != len(new_data):
                    tmp_target.unlink(missing_ok=True)
                    logger.error(
                        f"预设迁移校验失败：新旧条目数不一致 ({len(new_data)} != {len(old_data)})，回退读取旧文件"
                    )
                    return old_presets, False
                # Also verify preset lists if present
                for sub_k in ("description_presets", "tag_presets", "title_presets"):
                    if sub_k in old_data and len(old_data.get(sub_k) or {}) != len(new_data.get(sub_k) or {}):
                        tmp_target.unlink(missing_ok=True)
                        logger.error(f"预设迁移校验失败：{sub_k} 条目数不一致，回退读取旧文件")
                        return old_presets, False

            shutil.move(str(tmp_target), str(new_presets))
            logger.info(f"预设文件已安全复制到 {new_presets}（旧文件 {old_presets} 原样保留）")
            return new_presets, True
        except Exception as e:
            logger.error(f"预设文件迁移异常，回退使用旧位置 {old_presets}: {e}")
            return old_presets, False

    return new_presets, True


# ==============================================================================
# §7. 测试残留文件清扫报告与执行 (Test Artifacts Cleanup)
# ==============================================================================

DEFINITE_TEST_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?:^|[_ \-\.\d])test(?:[_ \-\.\d]|$)", re.IGNORECASE),
    re.compile(r"[a-z0-9]Test(?:\d*|_|\.|$|[A-Z])"),
    re.compile(r"^wave_\d{8}_\d{6}"),
    re.compile(r"mutex[_\s]?art", re.IGNORECASE),
    re.compile(r"alert[_\s]?art", re.IGNORECASE),
    re.compile(r"Attach_Switch_Three_States", re.IGNORECASE),
    re.compile(r"Two_Phase_Wave_Art", re.IGNORECASE),
    re.compile(r"TabA_Unified_Post", re.IGNORECASE),
    re.compile(r"Scheduled_Patreon_Post", re.IGNORECASE),
    re.compile(r"Snapshot_Clean_Title", re.IGNORECASE),
    re.compile(r"History_Fallback_Artwork", re.IGNORECASE),
    re.compile(r"WM_Gate_Tab_A", re.IGNORECASE),
    re.compile(r"hero_anim", re.IGNORECASE),
    re.compile(r"MinimalSetTest", re.IGNORECASE),
    re.compile(r"Normal_Title", re.IGNORECASE),
    re.compile(r"Very_Long_Title", re.IGNORECASE),
    re.compile(r"Condition_Test", re.IGNORECASE),
    re.compile(r"Censor_Test", re.IGNORECASE),
    re.compile(r"XOptCleanupTest", re.IGNORECASE),
]

SUSPICIOUS_TEST_PATTERNS: List[re.Pattern] = [
    re.compile(r"dummy", re.IGNORECASE),
    re.compile(r"sample", re.IGNORECASE),
    re.compile(r"placeholder", re.IGNORECASE),
    re.compile(r"fake", re.IGNORECASE),
    re.compile(r"temp", re.IGNORECASE),
    re.compile(r"tmp", re.IGNORECASE),
]


def is_solid_color_placeholder(path: Path) -> bool:
    """Detect if an image is a 1-color placeholder synthetic fixture."""
    if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        return False
    try:
        with Image.open(path) as im:
            ext = im.getextrema()
            if im.mode == "RGB":
                return ext[0][0] == ext[0][1] and ext[1][0] == ext[1][1] and ext[2][0] == ext[2][1]
            elif im.mode == "RGBA":
                return ext[0][0] == ext[0][1] and ext[1][0] == ext[1][1] and ext[2][0] == ext[2][1] and ext[3][0] == ext[3][1]
            elif im.mode == "L":
                return ext[0] == ext[1]
    except Exception:
        pass
    return False


def scan_test_artifacts(
    data_dir: Optional[Union[str, Path]] = None,
    cache_dir: Optional[Union[str, Path]] = None,
    archive_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """
    Scan cache and archive trees for test fixtures across two tiers:
    1. 'definite_files': Definite test artifacts (contains test, wave_* prefix, mutex/alert art, known fixtures, solid color placeholders).
    2. 'suspicious_files': Needs manual review (dummy/sample/temp keywords). Default NOT deleted.
    """
    if data_dir is None:
        from config import DATA_DIR
        data_dir = DATA_DIR
    data_root = Path(data_dir)
    c_root = Path(cache_dir) if cache_dir is not None else (data_root / "cache")
    a_root = Path(archive_dir) if archive_dir is not None else (data_root / "archive")

    definite_files: List[Dict[str, Any]] = []
    suspicious_files: List[Dict[str, Any]] = []

    for root_dir in [c_root, a_root]:
        if not root_dir.exists():
            continue
        for p in root_dir.rglob("*"):
            if not p.is_file():
                continue
            try:
                rel = str(p.relative_to(data_root))
            except Exception:
                rel = p.name
            f_size = p.stat().st_size

            # 1. Definite pattern match
            matched_reason = None
            for pat in DEFINITE_TEST_PATTERNS:
                if pat.search(p.name):
                    matched_reason = f"匹配测试特征: {pat.pattern}"
                    break

            if matched_reason is None and is_solid_color_placeholder(p):
                matched_reason = "纯色占位测试小文件"

            if matched_reason:
                definite_files.append({
                    "path": str(p),
                    "rel_path": rel,
                    "filename": p.name,
                    "reason": matched_reason,
                    "bytes": f_size,
                })
                continue

            # 2. Suspicious pattern match
            susp_reason = None
            for pat in SUSPICIOUS_TEST_PATTERNS:
                if pat.search(p.name):
                    susp_reason = f"疑似关键字: {pat.pattern}"
                    break

            if susp_reason:
                suspicious_files.append({
                    "path": str(p),
                    "rel_path": rel,
                    "filename": p.name,
                    "reason": susp_reason,
                    "bytes": f_size,
                })

    def_bytes = sum(f["bytes"] for f in definite_files)
    susp_bytes = sum(f["bytes"] for f in suspicious_files)

    def_mb = def_bytes / (1024 * 1024)
    susp_mb = susp_bytes / (1024 * 1024)

    summary_lines = [
        "🧪 测试文件清扫报告",
        "------------------------------------",
        f"• 确定测试件: {len(definite_files)} 个 ({def_mb:.2f} MB)",
        f"• 疑似测试件 (需人工确认): {len(suspicious_files)} 个 ({susp_mb:.2f} MB)",
    ]

    detailed_lines = [
        f"【确定测试件完整名单（共 {len(definite_files)} 个，{def_mb:.2f} MB）】",
    ]
    if definite_files:
        for idx, item in enumerate(definite_files, 1):
            detailed_lines.append(f"{idx}. {item['rel_path']}  [{item['reason']}] ({item['bytes']} B)")
    else:
        detailed_lines.append("  (无)")

    if suspicious_files:
        detailed_lines.append("")
        detailed_lines.append(f"【疑似测试件完整名单（需人工确认，共 {len(suspicious_files)} 个，{susp_mb:.2f} MB）】")
        for idx, item in enumerate(suspicious_files, 1):
            detailed_lines.append(f"{idx}. {item['rel_path']}  [{item['reason']}] ({item['bytes']} B)")

    return {
        "definite_files": definite_files,
        "suspicious_files": suspicious_files,
        "definite_count": len(definite_files),
        "definite_bytes": def_bytes,
        "suspicious_count": len(suspicious_files),
        "suspicious_bytes": susp_bytes,
        "summary_text": "\n".join(summary_lines),
        "detailed_text": "\n".join(detailed_lines),
    }


def execute_test_artifacts_cleanup(
    scan_report: Dict[str, Any],
    confirmed: bool = False,
    pool_file: Optional[Union[str, Path]] = None,
    data_dir: Optional[Union[str, Path]] = None,
    delete_suspicious: bool = False,
) -> Dict[str, Any]:
    """
    Execute cleanup of test artifacts identified in scan_report:
    - Only deletes if confirmed is True.
    - Always deletes 'definite_files'.
    - 'suspicious_files' are only deleted if delete_suspicious is True (default False).
    - If `patreon_bundle_pool.json` references deleted files, removes paths from entries
      and prunes entries that become empty or reference deleted files.
    """
    if not confirmed:
        return {
            "executed": False,
            "deleted_count": 0,
            "deleted_bytes": 0,
            "pool_entries_removed": 0,
            "pool_refs_pruned": 0,
            "summary_message": "清扫已取消：未获用户确认",
        }

    if data_dir is None:
        from config import DATA_DIR
        data_dir = DATA_DIR
    data_root = Path(data_dir)

    if pool_file is None:
        pool_file = data_root / "patreon_bundle_pool.json"
    pool_path = Path(pool_file)

    to_delete = list(scan_report.get("definite_files", []))
    if delete_suspicious:
        to_delete.extend(scan_report.get("suspicious_files", []))

    deleted_paths: List[str] = []
    deleted_count = 0
    deleted_bytes = 0

    deleted_path_set = set()
    for item in to_delete:
        fp_str = item.get("path") if isinstance(item, dict) else str(item)
        fp = Path(fp_str)
        if fp.exists() and fp.is_file():
            try:
                sz = fp.stat().st_size
                fp.unlink()
                deleted_count += 1
                deleted_bytes += sz
                deleted_paths.append(str(fp))
                deleted_path_set.add(str(fp.resolve()))
                deleted_path_set.add(fp.name)
            except Exception as e:
                logger.warning(f"删除测试文件失败 {fp}: {e}")

    # Prune patreon_bundle_pool.json if present
    pool_entries_removed = 0
    pool_refs_pruned = 0
    if pool_path.exists():
        try:
            with open(pool_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            entries = loaded if isinstance(loaded, list) else (loaded.get("items", []) if isinstance(loaded, dict) else [])
            updated_entries = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                orig_imgs = entry.get("images", [])
                orig_vids = entry.get("videos", [])
                new_imgs = [p for p in orig_imgs if str(Path(p).resolve()) not in deleted_path_set and Path(p).name not in deleted_path_set]
                new_vids = [p for p in orig_vids if str(Path(p).resolve()) not in deleted_path_set and Path(p).name not in deleted_path_set]
                pruned_in_entry = (len(orig_imgs) - len(new_imgs)) + (len(orig_vids) - len(new_vids))
                pool_refs_pruned += pruned_in_entry
                if not new_imgs and not new_vids:
                    pool_entries_removed += 1
                else:
                    entry["images"] = new_imgs
                    entry["videos"] = new_vids
                    updated_entries.append(entry)
            with open(pool_path, "w", encoding="utf-8") as f:
                json.dump(updated_entries, f, ensure_ascii=False, indent=2)
            logger.info(f"Patreon 附件池索引更新：剔除条目 {pool_entries_removed} 个，清理引用 {pool_refs_pruned} 个")
        except Exception as e:
            logger.warning(f"更新 patreon_bundle_pool.json 失败: {e}")

    pool_note = ""
    if pool_entries_removed > 0 or pool_refs_pruned > 0:
        pool_note = f"；Patreon 附件池已同步剔除 {pool_entries_removed} 条测试条目 ({pool_refs_pruned} 个引用)"

    msg = f"清扫完成：已安全删除 {deleted_count} 个测试文件 ({deleted_bytes / (1024*1024):.2f} MB){pool_note}"
    logger.info(f"🧪 {msg}")
    return {
        "executed": True,
        "deleted_count": deleted_count,
        "deleted_bytes": deleted_bytes,
        "pool_entries_removed": pool_entries_removed,
        "pool_refs_pruned": pool_refs_pruned,
        "deleted_paths": deleted_paths,
        "summary_message": msg,
    }
