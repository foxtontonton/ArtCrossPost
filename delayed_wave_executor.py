# -*- coding: utf-8 -*-
"""
ArtCrossPost - Delayed Public Wave Executor (延迟公开波自动执行器)
- 串行执行到期公开波队列项
- 执行前按各目标平台在 A 页的现行设置（打码开关与参数、水印开关与参数）逐平台渲染变体并 ×0.5 降档
- 母版原件仅作 Civitai 配对钥匙与附件语义，绝不直接进入各平台图单
- 平台规则对齐: Bluesky 图单 ≤4 且 GIF 转 bluesky_video、X 图单 ≤4 且单 GIF 帖设 x_video、Civitai 注入预设参数
- 执行前门禁自检 (登录态与限制探测)
- 单平台失败不阻塞其余平台
- 两阶段 DA 链接透传引流
- 执行完成写入 publish_history (复用 PublishHistoryManager.append_record 口径)
- 提供后台线程 DelayedWaveWorkerThread 支持非阻塞执行
"""

import logging
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageSequence
from PyQt5.QtCore import QThread, pyqtSignal

from censor_engine import CensorEngine, map_strength_to_blur_ksize, map_strength_to_mosaic_size
from config import load_config
from signature_utils import CANONICAL_SIGNATURE_TEMPLATE
from delayed_wave_manager import DelayedQueueItem, DelayedQueueManager, downscale_image_half
from history_manager import PublishHistoryManager
from media_foundation import (
    archive_publish_run_outputs,
    get_monthly_cache_dir,
    sanitize_ascii_stem,
)
from watermark_engine import WatermarkEngine

logger = logging.getLogger("ArtCrossPost")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"


def render_composite_gif_frames(
    input_gif_path: str | Path,
    output_gif_path: str | Path,
    censor_params: Optional[dict] = None,
    wm_params: Optional[dict] = None,
    watermark_engine: Optional[WatermarkEngine] = None,
) -> Path:
    """
    与标签页 A `_generate_composite_gif` 同口径的多帧 GIF 打码 + 水印合成函数:
    - 支持 mode="public_safe" 及常规打码框
    - 单帧与多帧 GIF 均安全保存
    """
    im = Image.open(str(input_gif_path))
    loop = im.info.get("loop", 0)

    censor_boxes = []
    censor_style = "black_bar"
    censor_mode = "moderate"
    censor_mosaic_size = 20
    censor_blur_ksize = 45
    censor_opacity = 1.0
    if censor_params and censor_params.get("enabled"):
        censor_boxes = censor_params.get("boxes", [])
        censor_style = censor_params.get("style", "black_bar")
        censor_mode = censor_params.get("mode", "moderate")
        censor_mosaic_size = censor_params.get("mosaic_size", 20)
        censor_blur_ksize = censor_params.get("blur_ksize", 45)
        censor_opacity = censor_params.get("opacity", 1.0)

    wm_enabled = bool(wm_params and wm_params.get("enabled") and str(wm_params.get("text", "")).strip())
    wm_eng = watermark_engine or (WatermarkEngine() if wm_enabled else None)

    out_frames = []
    durations = []
    for frame in ImageSequence.Iterator(im):
        durations.append(frame.info.get("duration", im.info.get("duration", 100)))
        fr_rgb = frame.convert("RGB")
        fr_bgr = cv2.cvtColor(np.array(fr_rgb), cv2.COLOR_RGB2BGR)
        h, w = fr_bgr.shape[:2]

        if censor_boxes:
            for b in censor_boxes:
                x1, y1, x2, y2 = (int(round(v)) for v in b[:4])
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                if x2 <= x1 or y2 <= y1:
                    continue
                orig_roi = fr_bgr[y1:y2, x1:x2].copy()
                if censor_mode == "public_safe":
                    if censor_style == "mosaic":
                        rw, rh = max(1, x2 - x1), max(1, y2 - y1)
                        cell = max(36, int(censor_mosaic_size * 2))
                        small_w = max(1, rw // cell)
                        small_h = max(1, rh // cell)
                        small = cv2.resize(orig_roi, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
                        censor_roi = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
                    elif censor_style == "blur":
                        k = max(55, int(censor_blur_ksize) if int(censor_blur_ksize) % 2 == 1 else int(censor_blur_ksize) + 1)
                        censor_roi = cv2.GaussianBlur(orig_roi, (k, k), 50)
                    else:
                        censor_roi = np.zeros_like(orig_roi)
                elif censor_style == "black_bar":
                    censor_roi = np.zeros_like(orig_roi)
                elif censor_style == "mosaic":
                    rw, rh = max(1, x2 - x1), max(1, y2 - y1)
                    cell = max(4, int(censor_mosaic_size))
                    small_w = max(1, rw // cell)
                    small_h = max(1, rh // cell)
                    small = cv2.resize(orig_roi, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
                    censor_roi = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
                elif censor_style == "blur":
                    k = int(censor_blur_ksize)
                    if k % 2 == 0:
                        k += 1
                    k = max(3, min(99, k))
                    censor_roi = cv2.GaussianBlur(orig_roi, (k, k), 30)
                else:
                    censor_roi = np.zeros_like(orig_roi)

                if censor_opacity >= 0.999:
                    fr_bgr[y1:y2, x1:x2] = censor_roi
                else:
                    alpha = max(0.0, min(1.0, float(censor_opacity)))
                    fr_bgr[y1:y2, x1:x2] = cv2.addWeighted(censor_roi, alpha, orig_roi, 1.0 - alpha, 0)

        fr_pil = Image.fromarray(cv2.cvtColor(fr_bgr, cv2.COLOR_BGR2RGB))
        if wm_enabled and wm_eng is not None:
            fr_pil, _ = wm_eng.apply_watermark(
                fr_pil,
                text=wm_params.get("text", ""),
                font_name=wm_params.get("font_name", "微软雅黑 (Microsoft YaHei)"),
                font_size_pct=wm_params.get("font_size_pct", 3.0),
                opacity=wm_params.get("opacity", 0.65),
                position=wm_params.get("position", "bottom_right"),
                color_hex=wm_params.get("color_hex", "#ffffff"),
                enable_stroke=wm_params.get("stroke", True),
            )
        out_frames.append(fr_pil.convert("RGB"))

    out_p = Path(output_gif_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    if not out_frames:
        raise RuntimeError(f"GIF 帧为空: {input_gif_path}")

    p_frames = [f.convert("P", palette=Image.ADAPTIVE, colors=256) for f in out_frames]
    save_kwargs = {
        "format": "GIF",
        "loop": loop,
        "optimize": True,
    }
    if len(p_frames) > 1:
        save_kwargs["save_all"] = True
        save_kwargs["append_images"] = p_frames[1:]
        save_kwargs["duration"] = durations
    else:
        save_kwargs["duration"] = durations[0] if durations else 100
    p_frames[0].save(str(out_p), **save_kwargs)
    return out_p


class DelayedWaveWorkerThread(QThread):
    """
    后台执行延迟公开波的线程 (严禁在 UI 线程同步调用 dispatcher.publish):
    mode:
      - "poll": 执行 executor.process_due_items()
      - "single": 执行 executor.execute_item(item, only_failed=only_failed)
    """

    poll_finished_signal = pyqtSignal(int)
    item_finished_signal = pyqtSignal(object, bool, dict)

    def __init__(
        self,
        executor: "DelayedWaveExecutor",
        mode: str = "poll",
        item: Optional[DelayedQueueItem] = None,
        only_failed: bool = False,
        exclude_ids: Optional[set[str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.executor = executor
        self.mode = mode
        self.item = item
        self.only_failed = only_failed
        self.exclude_ids = set(exclude_ids) if exclude_ids else set()

    def run(self):
        try:
            if self.mode == "single" and self.item is not None:
                ok, errors = self.executor.execute_item(self.item, only_failed=self.only_failed)
                self.item_finished_signal.emit(self.item, ok, errors)
            else:
                cnt = self.executor.process_due_items(exclude_ids=self.exclude_ids)
                self.poll_finished_signal.emit(cnt)
        except Exception as e:
            logger.error(f"[DelayedWaveWorkerThread] 后台线程执行异常: {e}")
            if self.mode == "single" and self.item is not None:
                self.item_finished_signal.emit(self.item, False, {"_thread": str(e)})
            else:
                self.poll_finished_signal.emit(0)


class DelayedWaveExecutor:
    """公开波串行执行器"""

    def __init__(
        self,
        manager: DelayedQueueManager,
        dispatchers_factory: Optional[Callable[[], list]] = None,
        on_item_finished: Optional[Callable[[DelayedQueueItem, bool], None]] = None,
        config_provider: Optional[Callable[[], dict]] = None,
        censor_engine: Optional[CensorEngine] = None,
        watermark_engine: Optional[WatermarkEngine] = None,
        history_manager: Optional[PublishHistoryManager] = None,
        cache_dir: Optional[Path] = None,
        notify_callback: Optional[Callable[[str, str], None]] = None,
    ):
        self.manager = manager
        self.dispatchers_factory = dispatchers_factory
        self.on_item_finished = on_item_finished
        self.config_provider = config_provider
        self._censor_engine = censor_engine
        self._watermark_engine = watermark_engine
        self._history_manager = history_manager
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.notify_callback = notify_callback

    def _get_config(self) -> dict:
        if self.config_provider:
            try:
                cfg = self.config_provider()
                if isinstance(cfg, dict):
                    return cfg
            except Exception as e:
                logger.debug(f"[DelayedWave] config_provider 读取异常: {e}")
        return load_config()

    def _get_censor_engine(self, cfg: dict) -> CensorEngine:
        if self._censor_engine is not None:
            return self._censor_engine
        c_cfg = cfg.get("censor", {})
        self._censor_engine = CensorEngine(
            default_style=c_cfg.get("style", "black_bar"),
            default_mode=c_cfg.get("coverage_mode", "moderate"),
            padding_ratio=c_cfg.get("padding_ratio", 0.15),
        )
        return self._censor_engine

    def _get_watermark_engine(self) -> WatermarkEngine:
        if self._watermark_engine is None:
            self._watermark_engine = WatermarkEngine()
        return self._watermark_engine

    def _get_history_manager(self) -> PublishHistoryManager:
        if self._history_manager is not None:
            return self._history_manager
        data_dir = self.manager.queue_file.parent if (self.manager and self.manager.queue_file) else DATA_DIR
        self._history_manager = PublishHistoryManager(data_dir)
        return self._history_manager

    def _get_dispatchers_map(self) -> dict:
        """获取分发器实例映射 {name: dispatcher}"""
        if self.dispatchers_factory:
            disps = self.dispatchers_factory()
            return {d.name: d for d in disps}
        else:
            from dispatchers import get_dispatchers
            disps = get_dispatchers(DATA_DIR / "cookies")
            return {d.name: d for d in disps}

    def execute_item(
        self,
        item: DelayedQueueItem,
        only_failed: bool = False,
    ) -> tuple[bool, dict[str, str]]:
        """
        执行单个队列项:
        - only_failed=True: 仅重试之前失败的平台
        - 按照两阶段顺序: 先发 DeviantArt，再发引流平台 (X / Bluesky / Civitai)
        - 返回: (是否全部成功, 失败原因字典 {platform: err_msg})
        """
        logger.info(f"[DelayedWave] 开始执行公开波队列项: {item.id} ('{item.title}')")
        self.manager.update_item_status(item.id, status="running", increment_attempts=True)

        target_platforms = list(item.platforms)
        if only_failed and item.last_error:
            target_platforms = [p for p in target_platforms if p in item.last_error]

        if not target_platforms:
            self.manager.update_item_status(item.id, status="done")
            return True, {}

        disps_map = self._get_dispatchers_map()
        snapshot = dict(item.material_snapshot)
        errors = dict(item.last_error) if only_failed else {}
        cfg = self._get_config()
        defaults = cfg.get("defaults", {})
        censor_cfg = cfg.get("censor", {})
        wm_cfg = cfg.get("watermark", {})

        # 1. 读取母版素材列表 (母版仅作渲染输入与 Civitai GIF 配对钥匙，不直接进任何平台图单)
        asset_files = snapshot.get("asset_files", [])
        if not asset_files:
            asset_files = snapshot.get("images", []) or snapshot.get("raw_images", [])

        for af in asset_files:
            if not Path(af).exists():
                logger.error(f"[DelayedWave] 素材文件丢失: {af}")
                errors["_assets"] = f"素材文件丢失: {af}"
                self.manager.update_item_status(item.id, status="failed", last_error=errors)
                return False, errors

        # 2. 解析各目标平台在 A 页的现行设置 (打码开关与参数、水印开关与参数)
        platform_opts = dict(snapshot.get("platform_options", {}))
        censor_global_on = bool(
            snapshot.get("censor_enabled", censor_cfg.get("enabled", True))
        )

        def _plat_censor_on(plat_name: str) -> bool:
            if not censor_global_on:
                return False
            opt_key = "x_censor" if plat_name == "x_twitter" else f"{plat_name}_censor"
            if opt_key in platform_opts:
                return bool(platform_opts[opt_key])
            default_val = False if plat_name == "civitai" else True
            return bool(defaults.get(opt_key, default_val))

        is_da_censor = _plat_censor_on("deviantart")
        is_x_censor = _plat_censor_on("x_twitter")
        is_bsky_censor = _plat_censor_on("bluesky")
        is_civitai_censor = _plat_censor_on("civitai")

        style = censor_cfg.get("style", "black_bar")
        mode = censor_cfg.get("coverage_mode", "moderate")
        ratio = float(censor_cfg.get("coverage_scale", 0.55))
        strength = int(censor_cfg.get("strength", 50))
        censor_mosaic = int(censor_cfg.get("mosaic_size", map_strength_to_mosaic_size(strength)))
        censor_blur = int(censor_cfg.get("blur_radius", map_strength_to_blur_ksize(strength)))
        if censor_blur % 2 == 0:
            censor_blur += 1
        censor_opacity = float(censor_cfg.get("opacity", 1.0))

        wm_enabled = bool(wm_cfg.get("enabled", False))
        wm_apply = bool(wm_cfg.get("apply_to_publish", True))
        wm_text = str(wm_cfg.get("text", "")).strip()
        wm_info = {
            "enabled": True,
            "text": wm_text,
            "font_name": wm_cfg.get("font_family", "微软雅黑 (Microsoft YaHei)"),
            "font_size_pct": float(wm_cfg.get("size_pct", 3.0)),
            "opacity": float(wm_cfg.get("opacity", 0.65)),
            "position": wm_cfg.get("position", "bottom_right"),
            "color_hex": wm_cfg.get("color", "#ffffff"),
            "stroke": bool(wm_cfg.get("stroke", True)),
        } if (wm_enabled and wm_apply and wm_text) else None

        # §4 水印全局门禁: 后台执行期若水印缺失，不弹模态框，但必须以 error 级通知 + 队列项结果注记「无水印发布」响亮留痕
        watermark_note = ""
        if wm_info is None:
            watermark_note = "无水印发布"
            warn_log = f"[DelayedWave] ⚠️ 水印门禁告警: 队列项 {item.id} ('{item.title}') 当前水印设置未开启或文本为空，按【{watermark_note}】执行！"
            logger.error(warn_log)
            if self.notify_callback:
                try:
                    self.notify_callback(
                        f"⚠️ 延迟公开波无水印发布告警: {item.title} (结果注记: {watermark_note})",
                        "error",
                    )
                except Exception as ne:
                    logger.debug(f"[DelayedWave] notify_callback 异常: {ne}")

        title_str = str(snapshot.get("title", item.title)).strip()
        first_asset_stem = Path(asset_files[0]).stem if asset_files else "artwork"
        safe_title = sanitize_ascii_stem(title_str, fallback_stem=first_asset_stem)[:40]
        work_cache_dir = get_monthly_cache_dir(self.cache_dir)

        custom_boxes_map = snapshot.get("custom_censor_boxes", {}) or {}
        captured_ratios_map = snapshot.get("custom_censor_captured_ratios", {}) or {}

        # 3. 解析与补全 GIF 配对视频 (与手动发布对齐: comfyui_output_dir 树 + 素材所在目录查同 stem MP4)
        x_video_paths = dict(
            snapshot.get("x_video_paths")
            or platform_opts.get("x_video_paths")
            or {}
        )
        gif_candidates = [str(af) for af in asset_files if str(af).lower().endswith(".gif")]
        if gif_candidates:
            comfy_dir = cfg.get("comfyui_output_dir", "")
            extra_search_dirs = []
            for af in asset_files:
                p_af = Path(af)
                if p_af.exists():
                    extra_search_dirs.append(p_af.parent)
            all_source_refs = (
                snapshot.get("original_source_images", [])
                + snapshot.get("raw_images", [])
                + snapshot.get("images", [])
                + list(asset_files)
            )
            for orig in all_source_refs:
                try:
                    p_orig = Path(orig)
                    if p_orig.exists():
                        extra_search_dirs.append(p_orig.parent)
                    elif p_orig.parent.exists():
                        extra_search_dirs.append(p_orig.parent)
                except Exception:
                    pass

            unpaired_gifs = []
            for g in gif_candidates:
                pg = Path(g)
                existing_v = (
                    x_video_paths.get(g)
                    or x_video_paths.get(pg.name)
                    or x_video_paths.get(pg.stem)
                )
                if not (existing_v and Path(existing_v).exists()):
                    unpaired_gifs.append(g)

            if unpaired_gifs:
                try:
                    from video_engine import pair_gifs_with_videos
                    paired_map = pair_gifs_with_videos(
                        unpaired_gifs,
                        output_dir=comfy_dir or None,
                        extra_search_dirs=list(set(extra_search_dirs)),
                        recursive_extra=True,
                    )
                    for g_path, matched in paired_map.items():
                        if matched:
                            x_video_paths[str(g_path)] = str(matched)
                            x_video_paths[Path(g_path).name] = str(matched)
                            x_video_paths[Path(g_path).stem] = str(matched)
                except Exception as pe:
                    logger.warning(f"[DelayedWave] GIF 配对视频解析异常: {pe}")

                for g in unpaired_gifs:
                    if x_video_paths.get(g):
                        continue
                    pg = Path(g)
                    stems_to_try = [pg.stem, re.sub(r"^\d{2,}_", "", pg.stem)]
                    matched_mp4 = None
                    for s_dir in set(extra_search_dirs):
                        if not s_dir.exists():
                            continue
                        for st in stems_to_try:
                            direct_mp4 = s_dir / f"{st}.mp4"
                            if direct_mp4.exists():
                                matched_mp4 = direct_mp4
                                break
                        if matched_mp4:
                            break
                    if not matched_mp4 and comfy_dir and Path(comfy_dir).exists():
                        for st in stems_to_try:
                            for cand in Path(comfy_dir).rglob(f"{st}.mp4"):
                                if cand.is_file():
                                    matched_mp4 = cand
                                    break
                            if matched_mp4:
                                break
                    if matched_mp4:
                        x_video_paths[str(g)] = str(matched_mp4)
                        x_video_paths[pg.name] = str(matched_mp4)
                        x_video_paths[pg.stem] = str(matched_mp4)
                        clean_stem = re.sub(r"^\d{2,}_", "", pg.stem)
                        x_video_paths[clean_stem] = str(matched_mp4)

            for k, v in list(x_video_paths.items()):
                if v and Path(v).exists():
                    kp = Path(k)
                    x_video_paths[kp.name] = str(v)
                    x_video_paths[kp.stem] = str(v)
                    clean_stem = re.sub(r"^\d{2,}_", "", kp.stem)
                    if clean_stem != kp.stem:
                        x_video_paths[clean_stem] = str(v)

        # 4. 逐素材按各平台现行设置渲染变体 (打码 -> 水印 -> ×0.5 降档)
        da_images: list[str] = []
        x_images: list[str] = []
        bluesky_images: list[str] = []
        civitai_images: list[str] = []

        is_single_gif_post = (
            len(asset_files) == 1
            and str(asset_files[0]).lower().endswith(".gif")
        )
        paired_video_for_x = None
        if is_single_gif_post:
            g0 = str(asset_files[0])
            pg0 = Path(g0)
            paired_video_for_x = (
                x_video_paths.get(g0)
                or x_video_paths.get(pg0.name)
                or x_video_paths.get(pg0.stem)
                or x_video_paths.get(re.sub(r"^\d{2,}_", "", pg0.stem))
            )

        x_video_final_path: Optional[str] = None
        bsky_video_final_path: Optional[str] = None

        any_censor_needed = any([is_da_censor, is_x_censor, is_bsky_censor, is_civitai_censor])
        censor_eng = self._get_censor_engine(cfg) if any_censor_needed else None
        wm_eng = self._get_watermark_engine() if wm_info else None

        for idx, af in enumerate(asset_files):
            p_af = Path(af)
            is_gif = (p_af.suffix.lower() == ".gif")
            ext = "gif" if is_gif else (p_af.suffix.lstrip(".").lower() or "jpg")
            if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
                ext = "jpg"

            clean_stem = re.sub(r"^\d{2,}_", "", p_af.stem)
            clean_name = f"{clean_stem}{p_af.suffix}"
            custom_boxes = (
                custom_boxes_map.get(str(p_af))
                or custom_boxes_map.get(p_af.name)
                or custom_boxes_map.get(clean_name)
                or custom_boxes_map.get(clean_stem)
            )
            if not custom_boxes:
                for orig_k, b_val in custom_boxes_map.items():
                    if Path(orig_k).name == clean_name or Path(orig_k).stem == clean_stem:
                        custom_boxes = b_val
                        break
            captured_ratio = (
                captured_ratios_map.get(str(p_af))
                or captured_ratios_map.get(p_af.name)
                or captured_ratios_map.get(clean_name)
                or captured_ratios_map.get(clean_stem)
            )
            if captured_ratio is None:
                for orig_k, r_val in captured_ratios_map.items():
                    if Path(orig_k).name == clean_name or Path(orig_k).stem == clean_stem:
                        captured_ratio = r_val
                        break

            censor_variant_half: Optional[Path] = None
            raw_variant_half: Optional[Path] = None

            if is_gif:
                # Civitai 从不上传 GIF 本体，上传的是配对 MP4；GIF 只是配对钥匙，保持原路径原名
                civitai_images.append(str(p_af))

                c_params = None
                if any_censor_needed and censor_eng is not None:
                    with Image.open(p_af) as im_g:
                        gw, gh = im_g.size
                        first_bgr = cv2.cvtColor(np.array(im_g.convert("RGB")), cv2.COLOR_RGB2BGR)
                    base_boxes = custom_boxes if custom_boxes else censor_eng.detect_sensitive_regions(None, img_bgr=first_bgr)
                    resolved_boxes = censor_eng.resolve_final_boxes(
                        base_boxes,
                        gw,
                        gh,
                        mode=mode,
                        custom_ratio=ratio,
                        user_customized=bool(custom_boxes),
                        captured_ratio=captured_ratio,
                    )
                    c_params = {
                        "enabled": True,
                        "style": style,
                        "mode": mode,
                        "boxes": resolved_boxes,
                        "mosaic_size": censor_mosaic,
                        "blur_ksize": censor_blur,
                        "opacity": censor_opacity,
                    }

                if any_censor_needed:
                    tmp_c_gif = work_cache_dir / f"{item.id}_{idx:02d}_censor.gif"
                    half_c_gif = work_cache_dir / f"{item.id}_{idx:02d}_censor_half.gif"
                    render_composite_gif_frames(p_af, tmp_c_gif, censor_params=c_params, wm_params=wm_info, watermark_engine=wm_eng)
                    censor_variant_half = downscale_image_half(tmp_c_gif, half_c_gif)

                if not all([is_da_censor, is_x_censor, is_bsky_censor]):
                    half_r_gif = work_cache_dir / f"{item.id}_{idx:02d}_raw_half.gif"
                    if wm_info:
                        tmp_r_gif = work_cache_dir / f"{item.id}_{idx:02d}_raw_wm.gif"
                        render_composite_gif_frames(p_af, tmp_r_gif, censor_params=None, wm_params=wm_info, watermark_engine=wm_eng)
                        raw_variant_half = downscale_image_half(tmp_r_gif, half_r_gif)
                    else:
                        raw_variant_half = downscale_image_half(p_af, half_r_gif)

                da_gif_src = censor_variant_half if is_da_censor else raw_variant_half
                clean_da_stem = safe_title if idx == 0 else f"{safe_title}_{idx + 1}"
                clean_da_gif = work_cache_dir / f"{clean_da_stem}.gif"
                if da_gif_src and Path(da_gif_src).exists():
                    if str(clean_da_gif) != str(da_gif_src):
                        shutil.copy2(da_gif_src, clean_da_gif)
                    da_images.append(str(clean_da_gif))

                x_gif_src = censor_variant_half if is_x_censor else raw_variant_half
                if x_gif_src:
                    x_images.append(str(x_gif_src))

                bsky_gif_src = censor_variant_half if is_bsky_censor else raw_variant_half
                if "bluesky" in target_platforms and not bsky_video_final_path:
                    if paired_video_for_x and Path(paired_video_for_x).exists():
                        bsky_video_final_path = str(paired_video_for_x)
                    elif bsky_gif_src and Path(bsky_gif_src).exists():
                        mp4_out = work_cache_dir / f"{item.id}_{idx:02d}_bsky.mp4"
                        try:
                            from video_engine import convert_gif_to_mp4
                            convert_gif_to_mp4(bsky_gif_src, mp4_out)
                            bsky_video_final_path = str(mp4_out)
                        except Exception as bve:
                            logger.warning(f"[DelayedWave] Bluesky GIF 转 MP4 异常: {bve}")

                if is_single_gif_post and paired_video_for_x and Path(paired_video_for_x).exists():
                    x_video_final_path = str(paired_video_for_x)
                    x_images = [str(paired_video_for_x)]
            else:
                # 静态图管线: 打码 -> 水印 -> ×0.5 降档
                if any_censor_needed and censor_eng is not None:
                    censored_bgr, _ = censor_eng.apply_censor(
                        str(p_af),
                        output_path=None,
                        style=style,
                        mode=mode,
                        custom_ratio=ratio,
                        custom_boxes=custom_boxes,
                        captured_ratio=captured_ratio,
                        mosaic_size=censor_mosaic,
                        blur_ksize=censor_blur,
                        opacity=censor_opacity,
                    )
                    tmp_c_full = work_cache_dir / f"{item.id}_{idx:02d}_censored_full.{ext}"
                    if wm_info and wm_eng is not None:
                        wm_eng.apply_watermark(
                            censored_bgr,
                            text=wm_info["text"],
                            font_name=wm_info["font_name"],
                            font_size_pct=wm_info["font_size_pct"],
                            opacity=wm_info["opacity"],
                            position=wm_info["position"],
                            color_hex=wm_info["color_hex"],
                            enable_stroke=wm_info["stroke"],
                            output_path=tmp_c_full,
                        )
                    else:
                        cv2.imwrite(str(tmp_c_full), censored_bgr)
                    half_c_path = work_cache_dir / f"{item.id}_{idx:02d}_censored_half.{ext}"
                    censor_variant_half = downscale_image_half(tmp_c_full, half_c_path)

                if not all([is_da_censor, is_x_censor, is_bsky_censor, is_civitai_censor]):
                    half_r_path = work_cache_dir / f"{item.id}_{idx:02d}_raw_half.{ext}"
                    if wm_info and wm_eng is not None:
                        tmp_r_full = work_cache_dir / f"{item.id}_{idx:02d}_raw_wm_full.{ext}"
                        wm_eng.apply_watermark(
                            str(p_af),
                            text=wm_info["text"],
                            font_name=wm_info["font_name"],
                            font_size_pct=wm_info["font_size_pct"],
                            opacity=wm_info["opacity"],
                            position=wm_info["position"],
                            color_hex=wm_info["color_hex"],
                            enable_stroke=wm_info["stroke"],
                            output_path=tmp_r_full,
                        )
                        raw_variant_half = downscale_image_half(tmp_r_full, half_r_path)
                    else:
                        raw_variant_half = downscale_image_half(p_af, half_r_path)

                da_src = censor_variant_half if is_da_censor else raw_variant_half
                clean_da_stem = safe_title if idx == 0 else f"{safe_title}_{idx + 1}"
                clean_da_path = work_cache_dir / f"{clean_da_stem}.{ext}"
                if da_src and Path(da_src).exists():
                    if str(clean_da_path) != str(da_src):
                        shutil.copy2(da_src, clean_da_path)
                    da_images.append(str(clean_da_path))

                x_src = censor_variant_half if is_x_censor else raw_variant_half
                if x_src:
                    x_images.append(str(x_src))

                bsky_src = censor_variant_half if is_bsky_censor else raw_variant_half
                if bsky_src:
                    bluesky_images.append(str(bsky_src))

                civ_src = censor_variant_half if is_civitai_censor else raw_variant_half
                if civ_src:
                    civitai_images.append(str(civ_src))

        # 5. 平台规则对齐:
        # - X 图单 ≤ 4；单 GIF 帖设置 x_video 与 x_source_video_path
        if not x_video_final_path and len(x_images) > 4:
            x_images = x_images[:4]

        # - Bluesky 单帖单 embed 规范：若有视频则 bluesky_images = []，否则静态图 ≤ 4
        discarded_for_bsky = 0
        if bsky_video_final_path:
            discarded_for_bsky = max(0, len(asset_files) - 1)
            bluesky_images = []
        elif len(bluesky_images) > 4:
            discarded_for_bsky = len(bluesky_images) - 4
            bluesky_images = bluesky_images[:4]

        # - Civitai 注入 platform_options 与顶层预设 (resources/tool/channel/max_images)
        civitai_limit_val = int(
            platform_opts.get(
                "civitai_max_images_per_post",
                defaults.get("civitai_max_images_per_post", cfg.get("civitai_max_images_per_post", 20)),
            )
        )
        civitai_limit_val = min(20, max(1, civitai_limit_val))
        civitai_res_val = platform_opts.get(
            "civitai_resources",
            defaults.get("civitai_resources", cfg.get("civitai_resources", [])),
        )
        civitai_scope_val = platform_opts.get(
            "civitai_resources_scope",
            defaults.get("civitai_resources_scope", cfg.get("civitai_resources_scope", "all")),
        )
        civitai_tool_val = platform_opts.get(
            "civitai_tool",
            defaults.get("civitai_tool", cfg.get("civitai_tool", "ComfyUI")),
        )
        civitai_channel_val = platform_opts.get(
            "civitai_channel",
            defaults.get("civitai_channel", cfg.get("civitai_channel", "auto")),
        )
        da_limit_val = int(
            platform_opts.get(
                "deviantart_max_images_per_deviation",
                defaults.get("deviantart_max_images_per_deviation", cfg.get("deviantart_max_images_per_deviation", 10)),
            )
        )

        platform_opts.setdefault("is_ai_generated", bool(snapshot.get("is_ai", True)))
        platform_opts["civitai_max_images_per_post"] = civitai_limit_val
        platform_opts["civitai_resources"] = civitai_res_val
        platform_opts["civitai_resources_scope"] = civitai_scope_val
        platform_opts["civitai_tool"] = civitai_tool_val
        platform_opts["civitai_channel"] = civitai_channel_val
        platform_opts["deviantart_max_images_per_deviation"] = da_limit_val
        platform_opts["x_video_paths"] = x_video_paths

        media_roles: dict[str, str] = {}
        for f_p in da_images + x_images + bluesky_images + civitai_images:
            if str(f_p).lower().endswith(".gif"):
                media_roles[str(f_p)] = "low_gif"
            elif not str(f_p).lower().endswith(".mp4"):
                media_roles[str(f_p)] = "low_images"
        if x_video_final_path:
            media_roles[str(x_video_final_path)] = "mp4_renders"
        if bsky_video_final_path:
            media_roles[str(bsky_video_final_path)] = "mp4_renders"
        for mp4_orig in x_video_paths.values():
            if mp4_orig:
                media_roles[str(mp4_orig)] = "mp4_originals"

        sig_tpl = (
            snapshot.get("sig_template")
            or snapshot.get("signature_template")
            or cfg.get("signature_template")
            or CANONICAL_SIGNATURE_TEMPLATE
        )

        payload = {
            "title": snapshot.get("title", item.title),
            "description": snapshot.get("description", ""),
            "sig_template": sig_tpl,
            "signature_template": sig_tpl,
            "short_text": snapshot.get("short_text", ""),
            "tags": snapshot.get("tags", []),
            "is_nsfw": snapshot.get("is_nsfw", False),
            "is_ai": snapshot.get("is_ai", False),
            "images": da_images,
            "raw_images": list(asset_files),
            "da_images": da_images,
            "deviantart_max_images_per_deviation": da_limit_val,
            "x_images": x_images,
            "x_video": x_video_final_path,
            "x_source_video_path": paired_video_for_x if is_single_gif_post else None,
            "bluesky_images": bluesky_images,
            "bluesky_video": bsky_video_final_path,
            "bluesky_discarded_images_count": discarded_for_bsky,
            "civitai_images": civitai_images,
            "civitai_channel": civitai_channel_val,
            "civitai_max_images_per_post": civitai_limit_val,
            "civitai_resources": civitai_res_val,
            "civitai_resources_scope": civitai_scope_val,
            "civitai_tool": civitai_tool_val,
            "x_video_paths": x_video_paths,
            "platform_options": platform_opts,
            "_media_roles": media_roles,
        }

        # 短文案标签守卫: 公开波只警告不弹窗
        st = payload.get("short_text")
        if st and snapshot.get("short_text_dirty") and any(p in target_platforms for p in ("x_twitter", "bluesky")):
            from x_text_utils import get_missing_hashtags
            missing_wave = get_missing_hashtags(
                text=st,
                tags=snapshot.get("tags", []),
                is_ai=snapshot.get("is_ai", False),
                add_ai_tags=platform_opts.get("bluesky_ai_hashtags", True),
            )
            if missing_wave:
                logger.warning(
                    f"[短文案标签守卫] 公开波检测到短文案为手动编辑且标签未并入 ({len(missing_wave)} 个标签: {missing_wave})，免弹窗保持原样分发"
                )

        # 6. 确定执行顺序: 若包含 DeviantArt，优先执行 DA 以获取当帖引流链接
        has_da = "deviantart" in target_platforms

        captured_da_url = None
        default_da_homepage = defaults.get("x_da_link", "https://www.deviantart.com/foxtontonton")
        actual_traffic_link = default_da_homepage
        publish_results: dict[str, Any] = {}

        # 执行 DeviantArt
        if has_da:
            da_disp = disps_map.get("deviantart")
            if not da_disp:
                errors["deviantart"] = "DeviantArt 分发器未就绪"
                publish_results["deviantart"] = (False, errors["deviantart"], [])
            else:
                if hasattr(da_disp, "check_login"):
                    logged_in = da_disp.check_login()
                    if not logged_in:
                        logger.warning("[DelayedWave] DeviantArt 门禁自检未通过: 未登录")
                        errors["deviantart"] = "门禁拦截: DeviantArt 账号未登录或会话失效"
                        publish_results["deviantart"] = (False, errors["deviantart"], [])

                if "deviantart" not in errors:
                    try:
                        ret = da_disp.publish(payload)
                        da_ok, da_msg = (ret[0], ret[1]) if isinstance(ret, tuple) else (bool(ret), str(ret))
                        da_comp = ret[2] if (isinstance(ret, tuple) and len(ret) > 2) else []
                        publish_results["deviantart"] = (da_ok, str(da_msg), da_comp)
                        if da_ok:
                            if "deviantart" in errors:
                                del errors["deviantart"]
                            logger.info(f"[DelayedWave] DeviantArt 发布成功: {da_msg}")
                            if getattr(da_disp, "last_published_url", None):
                                captured_da_url = da_disp.last_published_url
                            elif da_msg:
                                m = re.search(r"https?://(?:www\.)?deviantart\.com/[^/\s]+/art/[^/\s]+", str(da_msg))
                                if m:
                                    captured_da_url = m.group(0).split("?")[0].split("#")[0].rstrip("/")
                        else:
                            errors["deviantart"] = str(da_msg)
                            logger.error(f"[DelayedWave] DeviantArt 发布失败: {da_msg}")
                    except Exception as da_e:
                        errors["deviantart"] = str(da_e)
                        publish_results["deviantart"] = (False, str(da_e), [])
                        logger.error(f"[DelayedWave] DeviantArt 执行异常: {da_e}")

        # 7. 处理当帖引流链接透传
        if not captured_da_url:
            # DA 不在本轮目标或本轮发布失败，尝试从快照或发布历史检索已发布的 DA 当帖链接 (重试/补发场景)
            captured_da_url = (
                snapshot.get("da_post_url")
                or platform_opts.get("da_post_url")
            )
            if not captured_da_url:
                try:
                    hist_mgr = self._get_history_manager()
                    if hist_mgr and hasattr(hist_mgr, "find_published_da_url"):
                        history_da_url = hist_mgr.find_published_da_url(asset_files)
                        if history_da_url:
                            captured_da_url = history_da_url
                            logger.info(f"[DelayedWave] 从历史发布记录回填 DA 当帖引流链接: {captured_da_url}")
                except Exception as he:
                    logger.debug(f"[DelayedWave] 检索历史 DA 链接异常: {he}")

        if captured_da_url:
            actual_traffic_link = captured_da_url.split("?")[0].split("#")[0].rstrip("/")
            logger.info(f"[DelayedWave] 成功捕获 DA 当帖引流链接: {actual_traffic_link}")
        else:
            actual_traffic_link = default_da_homepage

        payload["da_post_url"] = captured_da_url
        payload["actual_traffic_link"] = actual_traffic_link
        payload["x_da_link"] = actual_traffic_link
        payload["bluesky_da_link"] = actual_traffic_link
        platform_opts["da_post_url"] = captured_da_url
        platform_opts["x_da_link"] = actual_traffic_link
        platform_opts["bluesky_da_link"] = actual_traffic_link

        st = payload.get("short_text")
        if st and actual_traffic_link != default_da_homepage:
            if default_da_homepage in st:
                payload["short_text"] = st.replace(default_da_homepage, actual_traffic_link)
            elif default_da_homepage.rstrip("/") in st:
                payload["short_text"] = st.replace(default_da_homepage.rstrip("/"), actual_traffic_link)

        # 8. 执行其余免费平台 (X / Bluesky / Civitai)
        remaining_platforms = [p for p in target_platforms if p != "deviantart"]
        for plat in remaining_platforms:
            disp = disps_map.get(plat)
            if not disp:
                errors[plat] = f"分发器 {plat} 未找到"
                publish_results[plat] = (False, errors[plat], [])
                continue

            if hasattr(disp, "check_login"):
                try:
                    if not disp.check_login():
                        logger.warning(f"[DelayedWave] {disp.display_name} 门禁自检未通过: 未登录")
                        errors[plat] = f"门禁拦截: {disp.display_name} 账号未登录或会话失效"
                        publish_results[plat] = (False, errors[plat], [])
                        continue
                except Exception as c_err:
                    logger.debug(f"[DelayedWave] 检查登录异常: {c_err}")

            try:
                ret = disp.publish(payload)
                ok, msg = (ret[0], ret[1]) if isinstance(ret, tuple) else (bool(ret), str(ret))
                comp = ret[2] if (isinstance(ret, tuple) and len(ret) > 2) else []
                publish_results[plat] = (ok, str(msg), comp)
                if ok:
                    if plat in errors:
                        del errors[plat]
                    logger.info(f"[DelayedWave] {disp.display_name} 发布成功: {msg}")
                else:
                    errors[plat] = str(msg)
                    logger.error(f"[DelayedWave] {disp.display_name} 发布失败: {msg}")
            except Exception as e:
                errors[plat] = str(e)
                publish_results[plat] = (False, str(e), [])
                logger.error(f"[DelayedWave] {disp.display_name} 执行异常: {e}")

        # 9. 若为无水印发布，为各平台发布结果补充「无水印发布」注记，随后写入 publish_history
        if watermark_note and publish_results:
            for _plat_k, _res_val in list(publish_results.items()):
                if isinstance(_res_val, tuple) and len(_res_val) >= 2:
                    _ok_v, _msg_v = _res_val[0], str(_res_val[1])
                    _comp_v = _res_val[2] if len(_res_val) > 2 else []
                    if watermark_note not in _msg_v:
                        _msg_v = f"{_msg_v} ({watermark_note})"
                    publish_results[_plat_k] = (_ok_v, _msg_v, _comp_v)

        if publish_results:
            try:
                hist_mgr = self._get_history_manager()
                hist_mgr.append_record(
                    title=str(payload.get("title", item.title)),
                    image_paths=[str(af) for af in asset_files],
                    publish_results=publish_results,
                    payload=payload,
                )
            except Exception as he:
                logger.warning(f"[DelayedWave] 写入发布历史记录异常: {he}")

            try:
                data_root = self.cache_dir.parent
                archive_publish_run_outputs(
                    payload=payload,
                    publish_results=publish_results,
                    base_archive_dir=data_root / "archive",
                    pool_file=data_root / "patreon_bundle_pool.json",
                )
            except Exception as ae:
                logger.error(f"[DelayedWave] 归档发送文件异常（不改判发布结果）: {ae}")

        # 10. 状态裁决
        all_success = len(errors) == 0
        final_status = "done" if all_success else "failed"
        self.manager.update_item_status(
            item.id,
            status=final_status,
            last_error=errors,
            result_note=watermark_note,
        )

        item.status = final_status
        item.last_error = errors
        item.result_note = watermark_note
        if watermark_note:
            item.material_snapshot["result_note"] = watermark_note

        if self.on_item_finished:
            try:
                self.on_item_finished(item, all_success)
            except Exception as cb_e:
                logger.debug(f"[DelayedWave] 回调通知异常: {cb_e}")

        logger.info(f"[DelayedWave] 队列项 {item.id} 执行完毕: 成功={all_success}, 状态={final_status}, 异常平台={list(errors.keys())}")
        return all_success, errors

    def process_due_items(
        self,
        current_time: Optional[datetime] = None,
        exclude_ids: Optional[set[str]] = None,
    ) -> int:
        """
        扫描并串行执行所有到期队列项:
        - 逾期项打印日志注明「逾期补发 + 原定时间」
        - 过滤 exclude_ids (如启动时用户选择「暂缓」的队列项)
        - 同一时刻多项到期时串行执行，避免会话打架
        - 返回执行的项目数
        """
        now = current_time or datetime.now()
        due_items = self.manager.get_due_items(current_time=now)
        if exclude_ids:
            due_items = [it for it in due_items if it.id not in exclude_ids]
        if not due_items:
            return 0

        logger.info(f"[DelayedWave] 扫描到 {len(due_items)} 个到期公开波任务，开始串行分发...")
        processed_count = 0

        for it in due_items:
            try:
                due_dt = datetime.fromisoformat(it.due_at)
                if (now - due_dt).total_seconds() > 600:
                    logger.info(f"[DelayedWave] 逾期补发 + 原定时间: {it.due_at} (当前时刻: {now.isoformat()})")
            except Exception:
                pass

            self.execute_item(it)
            processed_count += 1
            time.sleep(0.2)

        return processed_count
