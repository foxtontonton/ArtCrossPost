# -*- coding: utf-8 -*-
"""
ArtCrossPost - Folder Batch Import & Auto-Grouping Engine (P2-2)
"""
import re
import json
from pathlib import Path
from datetime import datetime

SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def natural_sort_key(s: str):
    """自然排序键 (如 art_2 排在 art_10 前面)"""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', str(s))]


def extract_group_key(filename: str) -> str:
    """
    根据常见作品命名规则提取分组前缀 Key:
    - art_01.png, art_02.png -> 'art'
    - chapter1_p1.jpg, chapter1_p2.jpg -> 'chapter1'
    - Vol.2_01.png, Vol.2_cover.png -> 'Vol.2'
    - Latex_Session_001.png -> 'Latex_Session'
    - sketch.png -> 'sketch'
    """
    stem = Path(filename).stem.strip()

    # 1. 优先匹配 Vol.N / Chapter N 章节前缀
    vol_match = re.match(
        r'^(.*?(?:vol\.?\s*\d+|chapter\s*\d+|ch\s*\d+|part\s*\d+))([_\s\-\.].*)?$',
        stem,
        re.IGNORECASE
    )
    if vol_match:
        prefix = vol_match.group(1).strip(" _-.")
        if prefix:
            return prefix

    # 2. 匹配末尾带有序号 / 页码的情形: e.g. art_01, page-02, img_p1, pic_001
    num_match = re.match(r'^(.*?)[_\s\-]*(?:p|page|part)?0*(\d+)[_\s\-]*$', stem, re.IGNORECASE)
    if num_match and num_match.group(1).strip(" _-."):
        return num_match.group(1).strip(" _-.")

    # 3. 匹配无下划线直接接数字的: e.g. art01, img002
    trailing_num = re.match(r'^(.*?[a-zA-Z_\u4e00-\u9fa5]+)0*(\d+)$', stem)
    if trailing_num and trailing_num.group(1).strip(" _-."):
        return trailing_num.group(1).strip(" _-.")

    return stem


def group_images_by_naming(file_paths: list[str]) -> dict[str, list[str]]:
    """
    将文件路径列表按命名规则自动分组为多套作品:
    返回有序字典: {group_title: [path1, path2, ...]}
    每套作品内部按自然排序 (页码顺序) 排列
    """
    valid_files = [
        str(Path(p).resolve()) for p in file_paths
        if Path(p).suffix.lower() in SUPPORTED_IMAGE_EXTS and Path(p).is_file()
    ]

    groups = {}
    for p in valid_files:
        name = Path(p).name
        key = extract_group_key(name)
        if key not in groups:
            groups[key] = []
        groups[key].append(p)

    # 组内自然排序
    for k in groups:
        groups[k].sort(key=natural_sort_key)

    # 按组名自然排序
    sorted_groups = {k: groups[k] for k in sorted(groups.keys(), key=natural_sort_key)}
    return sorted_groups


def scan_and_group_folder(folder_path: Path | str) -> dict[str, list[str]]:
    """扫描指定文件夹中的全部图片素材并自动分组"""
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        return {}

    all_files = [str(f) for f in folder.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_IMAGE_EXTS]
    return group_images_by_naming(all_files)


def save_groups_to_drafts_box(groups: dict[str, list[str]], data_dir: Path | str) -> list[Path]:
    """
    将分组结果持久化存储到草稿箱目录 (data/drafts/):
    每个作品组生成一份独立完整的 draft_{id}.json
    """
    drafts_dir = Path(data_dir) / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    for idx, (group_title, img_paths) in enumerate(groups.items()):
        if not img_paths:
            continue
        safe_title = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5]', '_', group_title)
        draft_id = f"{now_str}_{idx:02d}_{safe_title}"
        draft_file = drafts_dir / f"draft_{draft_id}.json"

        data = {
            "id": draft_id,
            "title": group_title,
            "tags": "",
            "description": "",
            "images": img_paths,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }

        try:
            with open(draft_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            saved_files.append(draft_file)
        except Exception:
            pass

    return saved_files
