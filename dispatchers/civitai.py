# -*- coding: utf-8 -*-
"""
ArtCrossPost - Civitai Dispatcher
专为 Civitai 原生发帖流程设计的自动化分发器:
- 支持 PNG/JPG/WebP 静态图与 MP4/WebM 视频原生上传 (单帖最多 20 张)
- 帖中 GIF 自动替换为配对的高清视频 (复用 x_video_path)；无配对则跳过并附带明细说明
- 配对视频自动验证 Civitai 平台限制 (≤750MB、≤245s、≤4K 分辨率)
- 标题、ProseMirror 富文本描述与多标签逐个输入回车排版
- 发布成功 URL 正则断言 (civitai.com/posts/<数字>) 与帖子链接回填
- 失败时自动检测并走编辑页 Delete Post 清理孤儿草稿，防社区草稿堆积
"""
import os
import re
import time
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional, Union

from .base import BaseDispatcher

try:
    from logger import get_logger
except ImportError:
    from ..logger import get_logger

try:
    from config import load_config
except ImportError:
    from ..config import load_config

try:
    from video_engine import probe_video_info, get_ffmpeg_path
except ImportError:
    try:
        from ..video_engine import probe_video_info, get_ffmpeg_path
    except ImportError:
        probe_video_info = None
        get_ffmpeg_path = lambda: None

logger = get_logger("Civitai")

# Civitai 官方平台硬性门禁规范
CIVITAI_MAX_MEDIA_LIMIT = 20
CIVITAI_MAX_VIDEO_SIZE_MB = 750.0
CIVITAI_MAX_VIDEO_DURATION_SEC = 245.0
CIVITAI_MAX_VIDEO_DIMENSION = 4096

CIVITAI_SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
CIVITAI_SUPPORTED_VIDEO_EXTS = {".mp4", ".webm"}
CIVITAI_ALL_ACCEPTED_EXTS = CIVITAI_SUPPORTED_IMAGE_EXTS | CIVITAI_SUPPORTED_VIDEO_EXTS

POST_URL_REGEX = re.compile(r"^https?://(?:www\.)?civitai\.com/posts/(\d+)(?:[/?#]|$)")
DRAFT_EDIT_REGEX = re.compile(r"^https?://(?:www\.)?civitai\.com/posts/(\d+)/edit")


def validate_civitai_video_constraints(
    video_path: Union[str, Path],
    max_size_mb: float = CIVITAI_MAX_VIDEO_SIZE_MB,
    max_duration_sec: float = CIVITAI_MAX_VIDEO_DURATION_SEC,
    max_dim: int = CIVITAI_MAX_VIDEO_DIMENSION,
    ffmpeg_exe: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    自查 Civitai 视频格式限制:
    - 视频文件存在且非空
    - 文件体积 ≤ 750 MB
    - 视频时长 ≤ 245 秒 (04分05秒)
    - 视频分辨率 ≤ 4K (长边 ≤ 4096px 且总像素 ≤ 4096*2160)
    """
    vp = Path(video_path) if video_path else None
    if not vp or not vp.exists() or not vp.is_file():
        name_hint = vp.name if vp else "未知路径"
        return False, f"视频文件不存在: {name_hint}"

    f_size = vp.stat().st_size
    if f_size <= 0:
        return False, f"视频文件大小为 0: {vp.name}"

    size_mb = f_size / (1024 * 1024)
    if size_mb > max_size_mb:
        return False, f"Civitai 限制视频体积 ≤ {max_size_mb:.0f}MB (当前 {vp.name}: {size_mb:.2f}MB)"

    if probe_video_info is None:
        return True, f"未检测到视频探针模块，仅校验体积放行 ({size_mb:.2f}MB)"

    exe = ffmpeg_exe or (get_ffmpeg_path() if get_ffmpeg_path else None)
    try:
        info = probe_video_info(vp, ffmpeg_exe=exe)
    except Exception as e:
        logger.warning(f"[Civitai] 解析视频元数据失败 ({vp.name}): {e}，按体积放行")
        return True, f"视频体积合规 ({size_mb:.2f}MB)，无法探测时长/分辨率"

    duration = info.get("duration_sec")
    if duration is not None and duration > max_duration_sec:
        return False, f"Civitai 限制视频时长 ≤ {max_duration_sec:.0f}s (当前 {vp.name}: {duration:.1f}s)"

    w = info.get("width") or 0
    h = info.get("height") or 0
    if w > 0 and h > 0:
        if max(w, h) > max_dim or (w * h > 4096 * 2160):
            return False, f"Civitai 限制视频分辨率 ≤ 4K (当前 {vp.name}: {w}x{h})"

    return True, f"视频合规 ({size_mb:.2f}MB, {duration if duration is not None else 0:.1f}s, {w}x{h})"


def prepare_civitai_media(
    media_items: List[Union[str, Path]],
    x_video_paths: Optional[Dict[str, str]] = None,
    max_limit: int = CIVITAI_MAX_MEDIA_LIMIT,
    ffmpeg_exe: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """
    Civitai 专属媒体预检与筛选过滤规则:
    1. 只收 png/jpg/webp 图片与 mp4/webm 视频
    2. 帖中 GIF → 自动替换为其配对视频 (复用 x_video_path)；无配对 → 该张跳过并在注明原因
    3. 配对视频必须符合 Civitai 限制 (≤750MB, ≤245s, ≤4K)，否则跳过并注明
    4. 媒体数超上限 (min(max_limit, 20)) → 抛出异常阻断发布 (不静默截断)
    返回: (accepted_media_paths, skipped_notes)
    """
    effective_limit = min(CIVITAI_MAX_MEDIA_LIMIT, max(1, int(max_limit or CIVITAI_MAX_MEDIA_LIMIT)))
    x_vids = x_video_paths or {}

    accepted: List[str] = []
    skipped_notes: List[str] = []

    for item in media_items:
        p = Path(item)
        if not p.exists() or not p.is_file():
            skipped_notes.append(f"素材文件不存在: '{p.name}'，已跳过")
            continue

        ext = p.suffix.lower()

        # 1. 静态合规图片
        if ext in CIVITAI_SUPPORTED_IMAGE_EXTS:
            accepted.append(str(p))

        # 2. 原生合规视频
        elif ext in CIVITAI_SUPPORTED_VIDEO_EXTS:
            v_ok, v_msg = validate_civitai_video_constraints(p, ffmpeg_exe=ffmpeg_exe)
            if v_ok:
                accepted.append(str(p))
            else:
                skipped_notes.append(f"视频 '{p.name}' 未通过 Civitai 限制 ({v_msg})，已跳过")

        # 3. 动图 GIF: 尝试配对视频替换
        elif ext == ".gif":
            paired = (
                x_vids.get(p)
                or x_vids.get(str(p))
                or x_vids.get(p.name)
                or x_vids.get(p.stem)
            )
            if paired and Path(paired).is_file():
                paired_path = Path(paired)
                v_ok, v_msg = validate_civitai_video_constraints(paired_path, ffmpeg_exe=ffmpeg_exe)
                if v_ok:
                    accepted.append(str(paired_path))
                    logger.info(f"[Civitai] GIF '{p.name}' 已自动替换为配对视频 '{paired_path.name}'")
                else:
                    skipped_notes.append(f"GIF '{p.name}' 配对视频未通过 Civitai 限制 ({v_msg})，已跳过")
            else:
                skipped_notes.append("Civitai 不支持 GIF 且无配对视频，已跳过")

        # 4. 其他不支持格式
        else:
            skipped_notes.append(f"Civitai 不支持格式 '{ext}' ('{p.name}')，已跳过")

    # 媒体数自查与门禁拦截
    if not accepted:
        err = "Civitai 未包含任何有效媒体 (全部被跳过或无可用素材)"
        if skipped_notes:
            err += f" (原因: {'；'.join(skipped_notes)})"
        raise ValueError(err)

    if len(accepted) > effective_limit:
        raise ValueError(
            f"媒体数量 ({len(accepted)}) 超过 Civitai 上限 ({effective_limit} 张)，请在控制台删减后再发布 (已配置上限: {effective_limit})"
        )

    return accepted, skipped_notes


class CivitaiDispatcher(BaseDispatcher):
    def __init__(self, cookies_dir):
        super().__init__(
            name="civitai",
            display_name="Civitai",
            login_url="https://civitai.com",
            cookies_dir=cookies_dir
        )

    def get_app_browser_profile_dir(self) -> str:
        """返回 Civitai 专属独立持久化 Edge 浏览器目录"""
        p_dir = self.data_dir / "browser_profiles" / "civitai"
        p_dir.mkdir(parents=True, exist_ok=True)
        return str(p_dir.resolve())

    def has_saved_cookies(self) -> bool:
        """检查是否有保存的 Cookie 文件，或者持久化 profile 中已建立 Cookie 会话"""
        if self.cookies_file.exists():
            return True
        prof_cookie = Path(self.get_app_browser_profile_dir()) / "Default" / "Network" / "Cookies"
        return prof_cookie.exists()

    def get_profile(self) -> dict:
        """返回已保存的用户资料 (用户名与头像路径)"""
        if self.profile_file.exists():
            try:
                import json
                with open(self.profile_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    avatar_p = str(self.avatar_file) if self.avatar_file.exists() else None
                    return {
                        "username": data.get("username", "已登录用户"),
                        "avatar": avatar_p,
                        "logged_in": True
                    }
            except Exception as e:
                logger.warning(f"[{self.name}] 读取 profile 异常: {e}")

        cookies = self.get_cookies()
        if cookies and any("token" in c.get("name", "").lower() or "civ" in c.get("name", "").lower() for c in cookies):
            return {
                "username": "已登录用户",
                "avatar": None,
                "logged_in": True
            }

        prof_cookie = Path(self.get_app_browser_profile_dir()) / "Default" / "Network" / "Cookies"
        if prof_cookie.exists():
            return {
                "username": "已保存凭据",
                "avatar": None,
                "logged_in": True
            }

        return {
            "username": "未登录",
            "avatar": None,
            "logged_in": False
        }

    def probe_login(self) -> dict:
        """轻量探针检查登录态"""
        prof = self.get_profile()
        if prof.get("logged_in"):
            return {
                "platform": "civitai",
                "category": "login",
                "item": "login_probe",
                "status": "pass",
                "detail": f"Civitai 会话有效 ({prof.get('username')})",
                "suggestion": ""
            }
        return {
            "platform": "civitai",
            "category": "login",
            "item": "login_probe",
            "status": "fail",
            "detail": "Civitai 本地无 Cookie/Session 记录 (未登录)",
            "suggestion": "请点击 Civitai 卡片登录按钮完成登录"
        }

    def check_login(self) -> Tuple[bool, str]:
        probe = self.probe_login()
        if probe.get("status") == "pass":
            return True, probe.get("detail")
        return False, probe.get("detail")

    def get_selector_manifest(self) -> dict:
        return {
            "upload_input": "input[type='file']",
            "title_input": "textarea[placeholder*='title' i], textarea[name='title']",
            "description_editor": "div.ProseMirror",
            "tag_button": "[data-tour='post:tag'], div[class*='Alert-root']:has-text('Tag')",
            "publish_button": "button:has-text('Publish')",
            "delete_post_button": "button:has-text('Delete Post')"
        }

    def check_selectors_health(self) -> list:
        """只读 DOM 检查 Civitai 发帖入口关键选择器健康度"""
        results = []
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                profile_dir = self.get_app_browser_profile_dir()
                context = self.create_persistent_stealth_context(p, profile_dir, headless=True)
                page = context.pages[0] if context.pages else context.new_page()
                self.safe_goto(page, "https://civitai.com/posts/create", timeout=25000)
                try:
                    page.wait_for_selector("input[type='file']", timeout=10000)
                except Exception as e:
                    logger.debug(f"[Civitai] 发帖页关键控件等待超时: {e}")

                manifest = self.get_selector_manifest()
                for key, sel in manifest.items():
                    elem = page.query_selector(sel)
                    st = "pass" if elem is not None else "unknown"
                    results.append({
                        "platform": "civitai",
                        "category": "selector",
                        "item": key,
                        "status": st,
                        "detail": f"选择器命中: {elem is not None} (选择器: `{sel}`)",
                        "suggestion": f"如 Civitai 前端更新请调整选择器 `{sel}`" if not elem else ""
                    })
                context.close()
        except Exception as e:
            results.append({
                "platform": "civitai",
                "category": "selector",
                "item": "page_load",
                "status": "unknown",
                "detail": f"Civitai 体检页面访问异常: {e}",
                "suggestion": "请检查网络代理与梯子连接"
            })
        return results

    def login_interactive(self):
        """唤起独立持久化 Edge 浏览器供用户登录 Civitai 并持久化凭据"""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                profile_dir = self.get_app_browser_profile_dir()
                context = self.create_persistent_stealth_context(p, profile_dir, headless=False)
                page = context.pages[0] if context.pages else context.new_page()

                logger.info("[Civitai] 正在打开 Civitai 页面以建立登录会话...")
                self.safe_goto(page, "https://civitai.com", timeout=45000)

                # 等待用户完成登录 (检测登录标志或等待窗口关闭)
                logger.info("[Civitai] 请在弹出的浏览器中完成登录，登录成功后请勿直接关闭，程序将自动捕获凭据...")
                for _ in range(120):
                    time.sleep(1)
                    if page.is_closed():
                        break
                    # 检测是否有已登录标识 (例如右上角 User Avatar 或 Buzz 标识)
                    user_el = page.query_selector("button img[src*='image'], button[aria-haspopup='menu']:has-text('FO'), div[class*='Avatar']")
                    if user_el:
                        break

                # 提取用户名
                user_name = "Civitai 用户"
                try:
                    for el in page.query_selector_all("button, a, span"):
                        t = el.inner_text().strip()
                        if t and 2 <= len(t) <= 30 and ("@" in t or el.get_attribute("aria-label")):
                            user_name = t
                            break
                except Exception:
                    pass

                self.save_profile(username=user_name)
                try:
                    cookies = context.cookies("https://civitai.com")
                    self.save_cookies(cookies)
                except Exception as ce:
                    logger.warning(f"[Civitai] 导出 Cookie 异常: {ce}")

                context.close()
                return True, f"Civitai 连接成功！已保存登录会话 ({user_name})"
        except Exception as e:
            logger.error(f"[Civitai] 登录交互异常: {e}")
            return False, f"登录失败: {e}"

    def delete_orphan_draft(self, page, draft_id: Optional[str] = None) -> bool:
        """
        清理因流程失败产生的孤儿草稿 (需求 11):
        尝试在当前编辑页或导航至 /posts/<draft_id>/edit，点击 Delete Post 并确认删除。
        """
        try:
            if draft_id and not page.url.endswith(f"/{draft_id}/edit"):
                self.safe_goto(page, f"https://civitai.com/posts/{draft_id}/edit", timeout=30000)
                time.sleep(2)

            del_btn = page.query_selector("button:has-text('Delete Post')")
            if not del_btn:
                logger.warning(f"[Civitai] 未在页面上找到 Delete Post 按钮 (当前 URL: {page.url})")
                return False

            logger.info("[Civitai] 正在执行孤儿草稿清理: 点击侧边栏 Delete Post...")
            del_btn.click()
            time.sleep(1.5)

            # 在弹出的 Mantine Modal 中确认
            modal_del = page.query_selector(".mantine-Modal-content button:has-text('Delete Post')")
            if not modal_del:
                modal_del = page.query_selector("div[role='dialog'] button:has-text('Delete Post')")

            if modal_del:
                modal_del.click()
                time.sleep(3)
                logger.info(f"[Civitai] ✅ 已成功清理孤儿草稿 (ID: {draft_id or 'current'})")
                return True
            else:
                logger.warning("[Civitai] 孤儿草稿删除弹窗中未找到确认按钮！")
                return False
        except Exception as e:
            logger.error(
                f"[Civitai] ⚠️ 警告: 尝试清理孤儿草稿失败！草稿可能残留于: "
                f"https://civitai.com/posts/{draft_id or ''}/edit，请创作者手动前往清理。异常: {e}"
            )
            return False

    def publish(self, payload: dict) -> Tuple[bool, str, List[dict]]:
        """
        通过 Edge 持久化上下文执行 Civitai 原生发帖流程 (需求 7-11):
        1. 媒体过滤 (GIF 换配对视频、约束检查、≤20 拦截)
        2. 打开 /posts/create，一次性向 input[type='file'] 注入全部素材
        3. 轮询等待 Publish 按钮激活 + SAVED 状态 (图片 60s，含视频 180s)
        4. 填标题、ProseMirror 描述、逐个输入标签回车
        5. 点击 Publish，断言 URL 匹配 civitai.com/posts/<数字>
        6. 失败时尝试清理孤儿草稿
        """
        title = (payload.get("title") or "").strip()
        desc = (payload.get("description") or "").strip()
        tags = payload.get("tags") or []
        x_video_paths = payload.get("x_video_paths") or payload.get("platform_options", {}).get("x_video_paths") or {}

        max_limit = (
            payload.get("civitai_max_images_per_post")
            or payload.get("platform_options", {}).get("civitai_max_images_per_post")
            or load_config().get("defaults", {}).get("civitai_max_images_per_post", 20)
        )

        media_candidates = (
            payload.get("civitai_images")
            or payload.get("raw_images")
            or payload.get("images")
            or []
        )

        # 1. 媒体过滤与自检 (规则 3-6)
        try:
            accepted_media, skipped_notes = prepare_civitai_media(
                media_candidates,
                x_video_paths=x_video_paths,
                max_limit=max_limit
            )
        except ValueError as ve:
            logger.error(f"[Civitai] 发布前媒体门禁拦截: {ve}")
            return False, str(ve), []

        has_video = any(Path(m).suffix.lower() in CIVITAI_SUPPORTED_VIDEO_EXTS for m in accepted_media)
        wait_publish_timeout = 180 if has_video else 60

        logger.info(
            f"[Civitai] 开始分发作品: 媒体数={len(accepted_media)} (含视频={has_video}), 标题='{title}', "
            f"等待超时={wait_publish_timeout}s, 跳过项={len(skipped_notes)}"
        )

        detected_draft_id: Optional[str] = None

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                profile_dir = self.get_app_browser_profile_dir()
                context = self.create_persistent_stealth_context(p, profile_dir, headless=True)
                page = context.pages[0] if context.pages else context.new_page()

                # Step 7: 打开 /posts/create
                logger.info("[Civitai] 正在进入发布页: https://civitai.com/posts/create ...")
                self.safe_goto(page, "https://civitai.com/posts/create", timeout=45000)
                time.sleep(2)

                # 定位 Dropzone input
                file_input = page.query_selector("input[type='file']")
                if not file_input:
                    context.close()
                    return False, "发帖页未找到文件上传控件 (input[type='file'])", []

                # 一次性向 input[type='file'] 注入全部媒体
                logger.info(f"[Civitai] 正在批量注入 {len(accepted_media)} 项素材至上传控件...")
                file_input.set_input_files([str(m) for m in accepted_media])

                # Step 8: 轮询等 Publish 按钮激活 + SAVED 状态
                logger.info(f"[Civitai] 等待媒体上传就绪与草稿保存 (超时阈值: {wait_publish_timeout}s)...")
                is_ready = False
                for sec in range(1, wait_publish_timeout + 1):
                    time.sleep(1)

                    # 记录草稿 ID (如果 URL 已跳转到 /posts/<id>/edit)
                    cur_url = page.url
                    draft_match = DRAFT_EDIT_REGEX.search(cur_url)
                    if draft_match:
                        detected_draft_id = draft_match.group(1)

                    pub_btn = page.query_selector("button:has-text('Publish')")
                    pub_enabled = pub_btn and not pub_btn.is_disabled()

                    saved_el = page.query_selector("div:has-text('SAVED'), span:has-text('SAVED')")

                    if pub_enabled and saved_el:
                        logger.info(f"[Civitai] 媒体上传与草稿保存就绪 (耗时 {sec}s)")
                        is_ready = True
                        break

                if not is_ready:
                    # 超时未激活，尝试清理孤儿草稿
                    logger.error(f"[Civitai] 等待发布就绪超时 ({wait_publish_timeout}s)")
                    if detected_draft_id:
                        self.delete_orphan_draft(page, detected_draft_id)
                    context.close()
                    return False, f"等待媒体上传/转码就绪超时 ({wait_publish_timeout}s)", []

                # 再次提取草稿 ID
                draft_match = DRAFT_EDIT_REGEX.search(page.url)
                if draft_match:
                    detected_draft_id = draft_match.group(1)

                # Step 9: 填标题、描述、标签
                # 9.1 标题
                if title:
                    logger.info(f"[Civitai] 填写标题: {title}")
                    title_box = page.query_selector("textarea[placeholder*='title' i], textarea[name='title']")
                    if title_box:
                        title_box.fill(title)
                        time.sleep(0.5)

                # 9.2 描述 (ProseMirror 富文本)
                if desc:
                    logger.info("[Civitai] 填写富文本描述...")
                    desc_box = page.query_selector("div.ProseMirror")
                    if desc_box:
                        desc_box.click()
                        time.sleep(0.3)
                        desc_box.fill(desc)
                        time.sleep(0.5)

                # 9.3 标签逐个输入回车 (单个失败跳过并记日志，不中断)
                if tags:
                    logger.info(f"[Civitai] 正在注入 {len(tags)} 个标签...")
                    for t in tags:
                        tag_str = str(t).strip().lstrip("#")
                        if not tag_str:
                            continue
                        try:
                            tag_btn = page.query_selector("[data-tour='post:tag'], div[class*='Alert-root']:has-text('Tag'), div:has-text('+ Tag')")
                            if tag_btn:
                                tag_btn.click()
                                time.sleep(0.4)

                            # 查找激活的 Mantine 补全输入框
                            tag_input = page.query_selector("input[placeholder*='tag' i], input[placeholder*='search' i], .mantine-Autocomplete-input, div[role='combobox'] input")
                            if not tag_input:
                                tag_input = page.query_selector(":focus")

                            if tag_input:
                                tag_input.fill(tag_str)
                                time.sleep(0.3)
                                tag_input.press("Enter")
                                time.sleep(0.4)
                            else:
                                logger.warning(f"[Civitai] 未找到标签输入框，跳过标签 '{tag_str}'")
                        except Exception as tag_err:
                            logger.warning(f"[Civitai] 输入标签 '{tag_str}' 失败，跳过并不中断主流程: {tag_err}")

                time.sleep(1)

                # Step 10: 点击 Publish，断言成功 URL
                pub_btn = page.query_selector("button:has-text('Publish'):not([disabled])")
                if not pub_btn:
                    logger.error("[Civitai] 点击发布前 Publish 按钮不可点击")
                    if detected_draft_id:
                        self.delete_orphan_draft(page, detected_draft_id)
                    context.close()
                    return False, "发布按钮处于禁用状态，无法提交发布", []

                logger.info("[Civitai] 点击 Publish 按钮提交发布...")
                pub_btn.click()

                # 等待 URL 成功跳转至 /posts/<数字>
                published_url = None
                for _ in range(25):
                    time.sleep(1)
                    cur_url = page.url
                    match = POST_URL_REGEX.search(cur_url)
                    if match and not cur_url.endswith("/edit") and not cur_url.endswith("/create"):
                        published_url = f"https://civitai.com/posts/{match.group(1)}"
                        break

                if not published_url:
                    # 匹配失败
                    logger.error(f"[Civitai] 提交后 URL 未跳转至成功帖子页面 (当前 URL: {page.url})")
                    if detected_draft_id:
                        self.delete_orphan_draft(page, detected_draft_id)
                    context.close()
                    return False, f"发布提交未确认成功 (页面未跳转至公开帖子形态，当前 URL: {page.url})", []

                # 发布成功，孤儿草稿已转为正式帖，无需清理草稿
                logger.info(f"[Civitai] ✅ 发布成功！帖子地址: {published_url}")
                context.close()

                success_msg = f"发布成功 ({published_url})"
                if skipped_notes:
                    success_msg += f"；注: {'；'.join(skipped_notes)}"

                compliance = [
                    {"item": "post_url", "verified": True, "detail": published_url, "expected": True}
                ]
                return True, success_msg, compliance

        except Exception as e:
            logger.error(f"[Civitai] 发帖流程异常: {e}")
            # 异常时若已生成草稿，尝试在独立会话中清理
            if detected_draft_id:
                try:
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as p:
                        profile_dir = self.get_app_browser_profile_dir()
                        ctx = self.create_persistent_stealth_context(p, profile_dir, headless=True)
                        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                        self.delete_orphan_draft(pg, detected_draft_id)
                        ctx.close()
                except Exception as del_err:
                    logger.error(
                        f"[Civitai] ⚠️ 警告: 异常后清理孤儿草稿失败！草稿可能残留于: "
                        f"https://civitai.com/posts/{detected_draft_id}/edit，请手动检查清理。异常: {del_err}"
                    )

            return False, f"Civitai 发帖流程异常: {e}", []
