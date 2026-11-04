# -*- coding: utf-8 -*-
"""
ArtCrossPost - Delayed Public Wave Executor (延迟公开波自动执行器)
- 串行执行到期公开波队列项
- 执行前门禁自检 (登录态与限制探测)
- 单平台失败不阻塞其余平台
- 免费平台现场 ×0.5 降档
- 两阶段 DA 链接透传引流
- 失败状态记录与显式告警
"""

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from delayed_wave_manager import DelayedQueueItem, DelayedQueueManager, downscale_image_half

logger = logging.getLogger("ArtCrossPost")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"


class DelayedWaveExecutor:
    """公开波串行执行器"""

    def __init__(
        self,
        manager: DelayedQueueManager,
        dispatchers_factory: Optional[Callable[[], list]] = None,
        on_item_finished: Optional[Callable[[DelayedQueueItem, bool], None]] = None,
    ):
        self.manager = manager
        self.dispatchers_factory = dispatchers_factory
        self.on_item_finished = on_item_finished

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

        # 1. 准备免费平台降档素材 (×0.5 现场缩放，不修改自包含快照原件)
        asset_files = snapshot.get("asset_files", [])
        if not asset_files:
            # 兼容读取 images / raw_images
            asset_files = snapshot.get("images", [])

        downscaled_images = []
        for idx, af in enumerate(asset_files):
            p_af = Path(af)
            if not p_af.exists():
                logger.error(f"[DelayedWave] 素材文件丢失: {af}")
                errors["_assets"] = f"素材文件丢失: {af}"
                self.manager.update_item_status(item.id, status="failed", last_error=errors)
                return False, errors

            # 现场降档为 ×0.5
            half_dest = CACHE_DIR / f"{item.id}_{idx:02d}_half{p_af.suffix}"
            try:
                downscaled = downscale_image_half(p_af, half_dest)
                downscaled_images.append(str(downscaled))
            except Exception as de:
                logger.warning(f"[DelayedWave] 降档异常 ({de})，使用素材原尺寸: {af}")
                downscaled_images.append(str(p_af))

        # 构建分发 payload
        payload = {
            "title": snapshot.get("title", item.title),
            "description": snapshot.get("description", ""),
            "short_text": snapshot.get("short_text", ""),
            "tags": snapshot.get("tags", []),
            "is_nsfw": snapshot.get("is_nsfw", False),
            "is_ai": snapshot.get("is_ai", False),
            "images": downscaled_images,
            "raw_images": downscaled_images,
            "da_images": downscaled_images,
            "x_images": downscaled_images,
            "bluesky_images": downscaled_images,
            "civitai_images": downscaled_images,
            "platform_options": snapshot.get("platform_options", {}),
        }

        # 2. 确定执行顺序: 若包含 DeviantArt，优先执行 DA 以获取当帖引流链接
        has_da = "deviantart" in target_platforms
        has_followers = any(p in ("x_twitter", "bluesky") for p in target_platforms)

        captured_da_url = None
        default_da_homepage = "https://www.deviantart.com/foxtontonton"
        actual_traffic_link = default_da_homepage

        # 执行 DeviantArt
        if has_da:
            da_disp = disps_map.get("deviantart")
            if not da_disp:
                errors["deviantart"] = "DeviantArt 分发器未就绪"
            else:
                # 门禁自检: 检查登录态
                if hasattr(da_disp, "check_login"):
                    logged_in = da_disp.check_login()
                    if not logged_in:
                        logger.warning("[DelayedWave] DeviantArt 门禁自检未通过: 未登录")
                        errors["deviantart"] = "门禁拦截: DeviantArt 账号未登录或会话失效"

                if "deviantart" not in errors:
                    try:
                        ret = da_disp.publish(payload)
                        da_ok, da_msg = (ret[0], ret[1]) if isinstance(ret, tuple) else (bool(ret), str(ret))
                        if da_ok:
                            if "deviantart" in errors:
                                del errors["deviantart"]
                            logger.info(f"[DelayedWave] DeviantArt 发布成功: {da_msg}")
                            # 捕获当帖链接
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
                        logger.error(f"[DelayedWave] DeviantArt 执行异常: {da_e}")

        # 3. 处理当帖引流链接透传
        if captured_da_url:
            actual_traffic_link = captured_da_url.split("?")[0].split("#")[0].rstrip("/")
            logger.info(f"[DelayedWave] 成功捕获 DA 当帖引流链接: {actual_traffic_link}")
        else:
            actual_traffic_link = default_da_homepage

        payload["da_post_url"] = captured_da_url
        payload["actual_traffic_link"] = actual_traffic_link
        payload["x_da_link"] = actual_traffic_link
        payload["bluesky_da_link"] = actual_traffic_link

        # 两阶段短文案与描述替换
        st = payload.get("short_text")
        if st and actual_traffic_link != default_da_homepage:
            if default_da_homepage in st:
                payload["short_text"] = st.replace(default_da_homepage, actual_traffic_link)
            elif default_da_homepage.rstrip("/") in st:
                payload["short_text"] = st.replace(default_da_homepage.rstrip("/"), actual_traffic_link)

        # 4. 执行其余免费平台 (X / Bluesky / Civitai)
        remaining_platforms = [p for p in target_platforms if p != "deviantart"]
        for plat in remaining_platforms:
            disp = disps_map.get(plat)
            if not disp:
                errors[plat] = f"分发器 {plat} 未找到"
                continue

            # 门禁自检
            if hasattr(disp, "check_login"):
                try:
                    if not disp.check_login():
                        logger.warning(f"[DelayedWave] {disp.display_name} 门禁自检未通过: 未登录")
                        errors[plat] = f"门禁拦截: {disp.display_name} 账号未登录或会话失效"
                        continue
                except Exception as c_err:
                    logger.debug(f"[DelayedWave] 检查登录异常: {c_err}")

            try:
                ret = disp.publish(payload)
                ok, msg = (ret[0], ret[1]) if isinstance(ret, tuple) else (bool(ret), str(ret))
                if ok:
                    if plat in errors:
                        del errors[plat]
                    logger.info(f"[DelayedWave] {disp.display_name} 发布成功: {msg}")
                else:
                    errors[plat] = str(msg)
                    logger.error(f"[DelayedWave] {disp.display_name} 发布失败: {msg}")
            except Exception as e:
                errors[plat] = str(e)
                logger.error(f"[DelayedWave] {disp.display_name} 执行异常: {e}")

        # 5. 状态裁决
        all_success = len(errors) == 0
        final_status = "done" if all_success else "failed"
        self.manager.update_item_status(item.id, status=final_status, last_error=errors)

        item.status = final_status
        item.last_error = errors

        if self.on_item_finished:
            try:
                self.on_item_finished(item, all_success)
            except Exception as cb_e:
                logger.debug(f"[DelayedWave] 回调通知异常: {cb_e}")

        logger.info(f"[DelayedWave] 队列项 {item.id} 执行完毕: 成功={all_success}, 状态={final_status}, 异常平台={list(errors.keys())}")
        return all_success, errors

    def process_due_items(self, current_time: Optional[datetime] = None) -> int:
        """
        扫描并串行执行所有到期队列项:
        - 逾期项打印日志注明「逾期补发 + 原定时间」
        - 同一时刻多项到期时串行执行，避免会话打架
        - 返回执行的项目数
        """
        now = current_time or datetime.now()
        due_items = self.manager.get_due_items(current_time=now)
        if not due_items:
            return 0

        logger.info(f"[DelayedWave] 扫描到 {len(due_items)} 个到期公开波任务，开始串行分发...")
        processed_count = 0

        for it in due_items:
            # 补发语义判定
            try:
                due_dt = datetime.fromisoformat(it.due_at)
                # 逾期超过 10 分钟视为逾期补发
                if (now - due_dt).total_seconds() > 600:
                    logger.info(f"[DelayedWave] 逾期补发 + 原定时间: {it.due_at} (当前时刻: {now.isoformat()})")
            except Exception:
                pass

            self.execute_item(it)
            processed_count += 1
            time.sleep(1.0)

        return processed_count
