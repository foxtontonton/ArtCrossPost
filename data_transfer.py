# -*- coding: utf-8 -*-
"""
ArtCrossPost - Data Export & Import Engine (P2-1)
"""
import zipfile
import shutil
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger("ArtCrossPost.DataTransfer")

# 导出中应包含的关键文件与目录名称
EXPORT_TARGET_ITEMS = [
    "presets.json",
    "draft.json",
    "scheduled_posts.json",
    "publish_history.json",
    "window_state.json",
    "cookies"
]


def export_user_data(data_dir: Path | str, export_zip_path: Path | str) -> Path:
    """
    导出全部关键数据并打包为 zip:
    包含 config(presets.json)、草稿(draft.json)、各平台 profile/登录态(cookies 目录)、
    定时任务(scheduled_posts.json)、历史记录(publish_history.json)
    """
    data_dir = Path(data_dir).resolve()
    export_zip_path = Path(export_zip_path).resolve()
    export_zip_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(export_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 遍历目标文件与目录
        for item_name in EXPORT_TARGET_ITEMS:
            src_path = data_dir / item_name
            if not src_path.exists():
                continue

            if src_path.is_file():
                zf.write(src_path, arcname=item_name)
            elif src_path.is_dir():
                for sub in src_path.rglob("*"):
                    if sub.is_file():
                        rel = sub.relative_to(data_dir)
                        zf.write(sub, arcname=str(rel).replace("\\", "/"))

        # 写入备份元数据描述
        meta_info = {
            "app": "ArtCrossPost",
            "version": "2.0",
            "exported_at": datetime.now().isoformat(),
            "warning": "包含登录凭证，请妥善保管"
        }
        import json
        zf.writestr("backup_metadata.json", json.dumps(meta_info, ensure_ascii=False, indent=2))

    logger.info(f"数据成功导出至: {export_zip_path}")
    return export_zip_path


def import_user_data(import_zip_path: Path | str, data_dir: Path | str) -> tuple[bool, str, Path | None]:
    """
    导入 zip 恢复数据:
    覆盖恢复前，先把现有 data/ 目录完整备份为 data_backup_时间戳/
    返回: (success, message, backup_dir_path)
    """
    import_zip_path = Path(import_zip_path).resolve()
    data_dir = Path(data_dir).resolve()

    if not import_zip_path.exists() or not import_zip_path.is_file():
        return False, f"备份文件不存在: {import_zip_path}", None

    if not zipfile.is_zipfile(import_zip_path):
        return False, "选中的文件不是有效的 ZIP 压缩包", None

    # 1. 创建现有 data/ 的时间戳备份目录
    backup_name = f"data_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_dir = data_dir.parent / backup_name

    try:
        if data_dir.exists():
            # 复制除 cache 外的文件，若已有数据则整体安全复制备份
            shutil.copytree(
                data_dir,
                backup_dir,
                ignore=shutil.ignore_patterns("cache", "*.tmp", "__pycache__")
            )
            logger.info(f"覆盖前已创建安全备份: {backup_dir}")
        else:
            data_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"创建现有数据安全备份失败: {e}")
        return False, f"创建本地安全备份失败，恢复已终止: {e}", None

    # 2. 解压并恢复数据到 data_dir
    try:
        with zipfile.ZipFile(import_zip_path, "r") as zf:
            namelist = zf.namelist()
            # 基础结构校验
            has_valid_item = any(
                item.startswith("cookies") or item in EXPORT_TARGET_ITEMS or item == "backup_metadata.json"
                for item in namelist
            )
            if not has_valid_item:
                return False, "压缩包中未检测到 ArtCrossPost 关联数据", backup_dir

            for member in zf.infolist():
                # 安全解压防 zip-slip
                norm_name = Path(member.filename).as_posix()
                if ".." in norm_name or norm_name.startswith("/"):
                    continue
                if member.filename == "backup_metadata.json":
                    continue
                zf.extract(member, data_dir)

        logger.info(f"数据已成功从 {import_zip_path.name} 恢复至 {data_dir}")
        return True, "数据恢复成功", backup_dir
    except Exception as e:
        logger.error(f"解压恢复数据异常: {e}")
        return False, f"解压恢复数据异常: {e}", backup_dir
