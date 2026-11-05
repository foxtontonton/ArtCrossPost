# -*- coding: utf-8 -*-
"""
ArtCrossPost - Delayed Public Wave Manager (延迟公开波队列管理器)
- 管理延期公开发布队列 (data/delayed_queue.json)
- 自包含保存素材副本 (data/delayed_assets/<id>/)，源草稿删除/修改不影响队列
- 延迟单位换算与到期筛选
- 免费平台降档纯函数 (宽、高 ×0.5 取整)
"""

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Union

import cv2
from PIL import Image, ImageSequence

from media_foundation import (
    get_monthly_cache_dir,
    resolve_non_colliding_path,
    sanitize_ascii_filename,
)

logger = logging.getLogger("ArtCrossPost")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DELAYED_QUEUE_FILE = DATA_DIR / "delayed_queue.json"
DELAYED_ASSETS_DIR = DATA_DIR / "delayed_assets"
CACHE_DIR = DATA_DIR / "cache"


def parse_delay_to_seconds(value: Union[int, float, str], unit: str) -> float:
    """
    将数值和单位解析为总秒数:
    - 分钟: minutes / minute / 分钟 / 分 / m -> value * 60
    - 小时: hours / hour / 小时 / 时 / h -> value * 3600
    - 天: days / day / 天 / d -> value * 86400
    """
    try:
        val = float(value)
    except (ValueError, TypeError):
        val = 7.0

    u = str(unit).strip().lower()
    if u in ("分钟", "分", "m", "min", "mins", "minute", "minutes"):
        return max(1.0, val * 60.0)
    elif u in ("小时", "时", "h", "hr", "hrs", "hour", "hours"):
        return max(1.0, val * 3600.0)
    else:
        # 默认单位为天
        return max(1.0, val * 86400.0)


def downscale_image_half(src_path: Union[str, Path], dest_path: Optional[Union[str, Path]] = None) -> Path:
    """
    免费平台降档纯函数:
    所有发往免费平台 (DA / X / Bluesky / Civitai) 的图片统一降档:
    宽、高边长 ×0.5 (取整)。
    锚点换算：1792×2304 → 896×1152 (像素量为原图 25%)。
    支持静态图与多帧 GIF。
    """
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"源图片不存在: {src}")

    if dest_path is None:
        monthly_cache = get_monthly_cache_dir(CACHE_DIR)
        dest = resolve_non_colliding_path(
            monthly_cache,
            f"{src.stem}_half{src.suffix}",
            fallback_stem=src.stem,
        )
    else:
        raw_dest = Path(dest_path)
        raw_dest.parent.mkdir(parents=True, exist_ok=True)
        dest = raw_dest.parent / sanitize_ascii_filename(raw_dest.name, fallback_stem=src.stem)

    is_gif = src.suffix.lower() == ".gif"

    if is_gif:
        # 多帧 GIF 逐帧等比缩放
        with Image.open(src) as im:
            frames = []
            durations = []
            disposals = []
            orig_w, orig_h = im.size
            new_w = max(1, int(orig_w * 0.5 + 0.5))
            new_h = max(1, int(orig_h * 0.5 + 0.5))

            loop = im.info.get("loop", 0)

            for frame in ImageSequence.Iterator(im):
                f = frame.copy().convert("RGBA")
                f_resized = f.resize((new_w, new_h), resample=Image.Resampling.LANCZOS)
                frames.append(f_resized)
                durations.append(frame.info.get("duration", 100))
                disposals.append(frame.info.get("disposal", 2))

            if frames:
                save_kwargs = {
                    "save_all": True,
                    "optimize": False,
                    "loop": loop,
                }
                if len(frames) > 1:
                    save_kwargs["append_images"] = frames[1:]
                    save_kwargs["duration"] = durations
                    save_kwargs["disposal"] = disposals
                else:
                    if durations:
                        save_kwargs["duration"] = durations[0]
                    if disposals:
                        save_kwargs["disposal"] = disposals[0]
                frames[0].save(str(dest), **save_kwargs)
    else:
        img_bgr = cv2.imread(str(src))
        if img_bgr is None:
            # 回退使用 PIL
            with Image.open(src) as pim:
                orig_w, orig_h = pim.size
                new_w = max(1, int(orig_w * 0.5 + 0.5))
                new_h = max(1, int(orig_h * 0.5 + 0.5))
                resized = pim.resize((new_w, new_h), resample=Image.Resampling.LANCZOS)
                resized.save(str(dest), quality=95)
        else:
            orig_h, orig_w = img_bgr.shape[:2]
            new_w = max(1, int(orig_w * 0.5 + 0.5))
            new_h = max(1, int(orig_h * 0.5 + 0.5))
            resized = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
            # 保存高质量图片
            ext = src.suffix.lower()
            if ext in (".jpg", ".jpeg"):
                cv2.imwrite(str(dest), resized, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            elif ext == ".png":
                cv2.imwrite(str(dest), resized, [int(cv2.IMWRITE_PNG_COMPRESSION), 3])
            elif ext == ".webp":
                cv2.imwrite(str(dest), resized, [int(cv2.IMWRITE_WEBP_QUALITY), 95])
            else:
                cv2.imwrite(str(dest), resized)

    logger.debug(f"[DelayedWave] 免费平台降档完成: {src.name} -> {dest.name}")
    return dest


@dataclass
class DelayedQueueItem:
    id: str
    title: str
    created_at: str
    due_at: str
    platforms: list[str]
    status: str = "pending"  # pending / running / done / failed / cancelled
    last_error: dict[str, str] = field(default_factory=dict)
    attempts: int = 0
    material_snapshot: dict = field(default_factory=dict)
    result_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DelayedQueueItem":
        # 宽容处理缺失字段
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            title=str(data.get("title") or "Untitled"),
            created_at=str(data.get("created_at") or datetime.now().isoformat()),
            due_at=str(data.get("due_at") or datetime.now().isoformat()),
            platforms=list(data.get("platforms") or []),
            status=str(data.get("status") or "pending"),
            last_error=dict(data.get("last_error") or {}),
            attempts=int(data.get("attempts") or 0),
            material_snapshot=dict(data.get("material_snapshot") or {}),
            result_note=str(data.get("result_note") or ""),
        )


class DelayedQueueManager:
    """延迟公开波队列管理器"""

    def __init__(
        self,
        queue_file: Optional[Union[str, Path]] = None,
        assets_dir: Optional[Union[str, Path]] = None,
        enabled: bool = False,
    ):
        self.queue_file = Path(queue_file) if queue_file else DELAYED_QUEUE_FILE
        self.assets_dir = Path(assets_dir) if assets_dir else DELAYED_ASSETS_DIR
        self.enabled = enabled

    def load_queue(self) -> list[DelayedQueueItem]:
        """安全加载队列文件，损坏或缺失时打日志并视为空队列，杜绝崩溃"""
        if not self.queue_file.exists():
            return []

        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            if isinstance(raw_data, list):
                items = [DelayedQueueItem.from_dict(d) for d in raw_data if isinstance(d, dict)]
                return items
            else:
                logger.warning(f"[DelayedWave] 队列文件格式异常 (非列表)，重置为空: {self.queue_file}")
                return []
        except Exception as e:
            logger.error(f"[DelayedWave] 队列文件损坏或读取异常 ({e})，视为空队列: {self.queue_file}")
            return []

    def save_queue(self, items: list[DelayedQueueItem]) -> bool:
        """安全持久化队列列表"""
        try:
            self.queue_file.parent.mkdir(parents=True, exist_ok=True)
            data = [item.to_dict() for item in items]
            tmp_file = self.queue_file.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            if tmp_file.exists():
                tmp_file.replace(self.queue_file)
            return True
        except Exception as e:
            logger.error(f"[DelayedWave] 保存队列异常: {e}")
            return False

    def enroll(
        self,
        material: dict,
        delay_seconds: float,
        target_platforms: list[str],
    ) -> DelayedQueueItem:
        """
        Patreon 发布成功后登记一发延迟公开波:
        - 自包含保存母版素材文件到 data/delayed_assets/<id>/
        - 脱钩源草稿与源文件
        - 写入 data/delayed_queue.json
        """
        now = datetime.now()
        due_time = now + timedelta(seconds=float(delay_seconds))
        item_id = f"wave_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        title = str(material.get("title") or "New Artwork").strip() or "New Artwork"

        # 自包含素材副本存储
        item_asset_dir = self.assets_dir / item_id
        item_asset_dir.mkdir(parents=True, exist_ok=True)

        source_images = material.get("raw_images") or material.get("images") or []
        copied_assets = []
        original_filenames = []

        for idx, src_p in enumerate(source_images):
            sp = Path(src_p)
            if sp.exists():
                dest_p = resolve_non_colliding_path(
                    item_asset_dir,
                    f"{idx:02d}_{sp.name}",
                    source_file=sp,
                    fallback_stem=f"asset_{idx:02d}",
                )
                try:
                    shutil.copy2(sp, dest_p)
                    copied_assets.append(str(dest_p))
                    original_filenames.append(sp.name)
                except Exception as ce:
                    logger.warning(f"[DelayedWave] 复制素材副本异常 ({ce})，保留原路径引用: {sp}")
                    copied_assets.append(str(sp))
                    original_filenames.append(sp.name)
            else:
                logger.warning(f"[DelayedWave] 登记入队时检测到源素材不存在: {sp}")

        # 构建自包含物料快照
        snapshot = dict(material)
        snapshot["asset_files"] = copied_assets
        snapshot["original_filenames"] = original_filenames
        snapshot["original_source_images"] = [str(s) for s in source_images]
        snapshot["images"] = copied_assets
        snapshot["raw_images"] = copied_assets

        # 确保公开波目标平台至少默认包含四大免费平台
        final_platforms = [p for p in target_platforms if p in ("deviantart", "x_twitter", "bluesky", "civitai")]
        if not final_platforms:
            final_platforms = ["deviantart", "x_twitter", "bluesky", "civitai"]

        new_item = DelayedQueueItem(
            id=item_id,
            title=title,
            created_at=now.isoformat(),
            due_at=due_time.isoformat(),
            platforms=final_platforms,
            status="pending",
            last_error={},
            attempts=0,
            material_snapshot=snapshot,
        )

        items = self.load_queue()
        items.append(new_item)
        self.save_queue(items)

        logger.info(
            f"[DelayedWave] 成功登记延迟公开波: id={item_id}, 标题='{title}', "
            f"目标平台={final_platforms}, 到期时间={new_item.due_at} (+{delay_seconds:.0f}s)"
        )
        return new_item

    def get_items(self) -> list[DelayedQueueItem]:
        return self.load_queue()

    def get_due_items(self, current_time: Optional[datetime] = None) -> list[DelayedQueueItem]:
        """获取所有到期且未完成的队列项 (按到期时间升序排序)"""
        now = current_time or datetime.now()
        items = self.load_queue()
        due_list = []
        for it in items:
            if it.status in ("pending", "failed"):
                try:
                    due_dt = datetime.fromisoformat(it.due_at)
                    if due_dt <= now:
                        due_list.append(it)
                except Exception:
                    pass
        due_list.sort(key=lambda x: x.due_at)
        return due_list

    def update_item_status(
        self,
        item_id: str,
        status: str,
        last_error: Optional[dict] = None,
        increment_attempts: bool = False,
        result_note: Optional[str] = None,
    ) -> bool:
        """更新队列项状态并持久化；完成或取消时清理素材副本"""
        items = self.load_queue()
        found = False
        for it in items:
            if it.id == item_id:
                it.status = status
                if last_error is not None:
                    it.last_error = last_error
                if increment_attempts:
                    it.attempts += 1
                if result_note is not None:
                    it.result_note = result_note
                found = True
                break

        if found:
            self.save_queue(items)
            if status in ("done", "cancelled"):
                self.cleanup_assets(item_id)
        return found

    def update_due_time(self, item_id: str, new_due_at: str) -> bool:
        """修改队列项到期时间"""
        items = self.load_queue()
        found = False
        for it in items:
            if it.id == item_id:
                it.due_at = new_due_at
                found = True
                break
        if found:
            self.save_queue(items)
            logger.info(f"[DelayedWave] 队列项 {item_id} 到期时间已更新为: {new_due_at}")
        return found

    def cancel_item(self, item_id: str) -> bool:
        """取消队列项并清理素材副本"""
        return self.update_item_status(item_id, status="cancelled")

    def cleanup_assets(self, item_id: str):
        """清理已完成或取消的队列项素材副本"""
        item_dir = self.assets_dir / item_id
        if item_dir.exists() and item_dir.is_dir():
            try:
                shutil.rmtree(item_dir)
                logger.info(f"[DelayedWave] 已清理队列项素材副本目录: {item_dir}")
            except Exception as e:
                logger.warning(f"[DelayedWave] 清理素材副本目录异常: {e}")
