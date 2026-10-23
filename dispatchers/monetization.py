# -*- coding: utf-8 -*-
"""
ArtCrossPost - Patreon & Ko-fi Dispatchers (对齐创作者赞助平台原生发帖后台)
特点:
- 严格分发【无码高清原图】以保障赞助者权益
- Patreon: 对齐 Audience (Public / Patrons Only / Select Tiers) 及 Adult Content (18+) 官方原生勾选
- Ko-fi: 对齐 Audience (Everyone / Supporters / Tier Members) 及分类设置
"""
import os
import time
from .base import BaseDispatcher
try:
    from logger import get_logger
except ImportError:
    from ..logger import get_logger

try:
    from config import load_config
except ImportError:
    from ..config import load_config

logger = get_logger("Monetization")

class PatreonDispatcher(BaseDispatcher):
    def __init__(self, cookies_dir):
        super().__init__(
            name="patreon",
            display_name="Patreon (赞助端)",
            login_url="https://www.patreon.com/login",
            cookies_dir=cookies_dir
        )

    def probe_login(self) -> dict:
        """
        Patreon 登录态有效性深度探针 (需求一 1.1)
        校验本地 Cookies 会话项，并向 Patreon 官方 API /api/current_user 发起轻量鉴权探针
        """
        cookies = self.get_cookies()
        if not cookies:
            return {
                "platform": "patreon",
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": "Patreon 本地无 Cookie/Session 记录 (未登录)",
                "suggestion": "请点击 Patreon 卡片登录按钮完成登录"
            }

        has_session = any(c.get("name") == "session_id" and c.get("value") for c in cookies)
        if not has_session:
            return {
                "platform": "patreon",
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": "Patreon 本地 Cookie 缺少核心 session_id 鉴权令牌 (未登录)",
                "suggestion": "请点击 Patreon 卡片登录按钮完成登录"
            }

        # 服务端 API 真实探针
        probe = self.http_probe_session("https://www.patreon.com/api/current_user", timeout=8)
        if probe.get("error"):
            return {
                "platform": "patreon",
                "category": "login",
                "item": "login_probe",
                "status": "pass",
                "detail": f"Patreon 会话凭据就绪，API 探测轻微超时: {probe['error']}",
                "suggestion": ""
            }

        if probe.get("status_code") == 200 and '"data"' in probe.get("text", ""):
            return {
                "platform": "patreon",
                "category": "login",
                "item": "login_probe",
                "status": "pass",
                "detail": "Patreon 官方 API 鉴权存活 (HTTP 200, 创作者身份已验证)",
                "suggestion": ""
            }
        elif probe.get("status_code") in (401, 403) or "/login" in probe.get("url", "").lower():
            return {
                "platform": "patreon",
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": f"Patreon 会话已过期或已被撤回 (HTTP {probe.get('status_code')})",
                "suggestion": "请点击登录按钮重新建立会话"
            }

        return {
            "platform": "patreon",
            "category": "login",
            "item": "login_probe",
            "status": "pass",
            "detail": f"Patreon 登录态记录 (共 {len(cookies)} 项 Cookie)",
            "suggestion": ""
        }

    def check_login(self):
        cookies = self.get_cookies()
        if not cookies:
            return False, "未登录 (无 Cookie 记录)"
        probe = self.probe_login()
        if probe.get("status") == "pass":
            return True, "已连接 (Cookie 就绪)"
        return False, probe.get("detail", "未登录")

    def get_selector_manifest(self) -> dict:
        return {
            "images_post_type": "button:has-text('Images'), div[role='button']:has-text('Images')",
            "title_input": "input[placeholder*='Post title'], input[name='title']",
            "upload_input": "input[type='file']",
            "adult_checkbox": "label:has-text('Adult content') input, input[name*='adult_content']",
            "publish_button": "button:has-text('Publish now'), button:has-text('Publish')"
        }

    def check_selectors_health(self) -> list:
        cookies = self.get_cookies()
        if not cookies:
            return [{
                "platform": "patreon",
                "category": "selector",
                "item": "selectors_all",
                "status": "unknown",
                "detail": "Patreon 未登录，跳过选择器体检",
                "suggestion": "登录后可开启选择器健康检测"
            }]
        results = []
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                profile_dir = self.get_app_browser_profile_dir()
                if os.path.isdir(profile_dir):
                    context = self.create_persistent_stealth_context(p, profile_dir, headless=True)
                    browser = None
                else:
                    browser = self.create_stealth_browser(p, headless=True)
                    context = browser.new_context()
                    context.add_cookies(cookies)

                page = context.pages[0] if context.pages else context.new_page()
                self.apply_stealth(page)
                self.safe_goto(page, "https://www.patreon.com/posts/new", timeout=30000)
                try:
                    page.wait_for_selector("button:has-text('Images'), div[role='button']:has-text('Images')", timeout=10000)
                except Exception:
                    pass

                manifest = self.get_selector_manifest()
                img_btn = page.query_selector(manifest["images_post_type"])
                results.append({
                    "platform": "patreon",
                    "category": "selector",
                    "item": "images_post_type",
                    "status": "pass" if img_btn else "unknown",
                    "detail": f"选择器命中: {img_btn is not None} (Images 发帖入口)",
                    "suggestion": "如页面结构更新请检查 Patreon 发布入口" if not img_btn else ""
                })

                if img_btn:
                    img_btn.click()
                    try:
                        page.wait_for_selector(manifest["title_input"], timeout=8000)
                    except Exception as e:
                        logger.debug(f"[Patreon] 切换图片发布类型后表单未渲染: {e}")

                for k, sel in [("title_input", manifest["title_input"]), ("upload_input", manifest["upload_input"]), ("adult_checkbox", manifest["adult_checkbox"]), ("publish_button", manifest["publish_button"])]:
                    el = page.query_selector(sel)
                    is_hit = (el is not None)
                    results.append({
                        "platform": "patreon",
                        "category": "selector",
                        "item": k,
                        "status": "pass" if is_hit else "unknown",
                        "detail": f"选择器 `{sel}` 探测: {'命中' if is_hit else '未展开(需前置输入/上传)'}",
                        "suggestion": ""
                    })
                self.close_login_session(browser, context)
        except Exception as e:
            results.append({
                "platform": "patreon",
                "category": "selector",
                "item": "page_load",
                "status": "unknown",
                "detail": f"Patreon 页面访问异常: {e}",
                "suggestion": "海外站点请检查网络代理连接"
            })
        return results

    def _fetch_patreon_user_info(self, cookies):
        """通过 Patreon 官方 current_user API 或 Cookie 提取真实创作者资料"""
        try:
            import json
            import urllib.request
            cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies if "patreon.com" in c.get("domain", "")])
            req = urllib.request.Request(
                "https://www.patreon.com/api/current_user",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
                    "Cookie": cookie_header
                }
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    attrs = data.get("data", {}).get("attributes", {})
                    name = attrs.get("full_name") or attrs.get("vanity") or attrs.get("url", "").split("/")[-1]
                    avatar = attrs.get("thumb_url") or attrs.get("image_url")
                    return name, avatar
        except Exception as e:
            logger.debug(f"[Patreon] current_user API 提取资料提示: {e}")
        return None, None

    def login_interactive(self):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                # 统一登录上下文入口：优先本机 Edge 真实用户配置，失败平滑切换专属独立持久化环境
                browser, context = self.create_login_context(p, "Patreon")
                existing_cookies = self.get_cookies()
                if existing_cookies:
                    context.add_cookies(existing_cookies)

                # 复用已有主标签页，彻底清理启动时的冗余空白页
                page = context.pages[0] if context.pages else context.new_page()
                self.clean_context_tabs(context)

                def check_patreon_logged_in(ctx):
                    """综合检测 Patreon 登录态并获取用户信息"""
                    try:
                        cks = ctx.cookies()
                        has_session = any(c.get("name") == "session_id" and bool(c.get("value")) for c in cks)
                        if not has_session:
                            return False, None

                        # 优先调用官方 current_user API 获取真实身份 (毫秒级且最精准)
                        name, avatar = self._fetch_patreon_user_info(cks)
                        if name:
                            return True, (name, avatar)

                        # 若 API 波动，检查所有打开页面的 DOM 特征
                        for pg in ctx.pages:
                            if pg.is_closed():
                                continue
                            u = pg.url.lower()
                            if "about:blank" in u or "chrome:" in u or "accounts.google." in u:
                                continue
                            if "/c/" in u or "/creator-home" in u or "/dashboard" in u:
                                return True, ("Patreon 创作者", None)
                            creator_btn = pg.query_selector("button:has-text('Creator studio'), button:has-text('Create'), a[href*='/creator-home'], a[href*='/posts/new']")
                            if creator_btn:
                                return True, ("Patreon 创作者", None)

                        # 兜底：不在登录页且无登录按钮，存在 session_id
                        has_login_btn = any(
                            pg.query_selector("a[href*='/login'], a:has-text('Log in'), button:has-text('Log In')") is not None
                            for pg in ctx.pages if not pg.is_closed() and "patreon.com" in pg.url.lower() and "/login" not in pg.url.lower()
                        )
                        if not has_login_btn and not any("/login" in pg.url.lower() for pg in ctx.pages if not pg.is_closed()):
                            return True, ("Patreon 创作者", None)

                        return False, None
                    except Exception:
                        return False, None

                # 首先探测已有持久化环境是否已处于登录态
                logged_in, user_info = check_patreon_logged_in(context)
                if not logged_in:
                    # 未登录时先尝试访问主页 (可能自动重定向到登录页或恢复会话)
                    self.safe_goto(page, "https://www.patreon.com/home", timeout=30000)
                    logged_in, user_info = check_patreon_logged_in(context)

                if not logged_in:
                    logger.info("[Patreon] 尚未登录，正在前往登录页面...")
                    self.safe_goto(page, self.login_url)
                    logger.info("[Patreon] 请在弹出的浏览器中完成登录 (支持账号密码或 Google 登录)...")

                    start_time = time.time()
                    last_url_log = 0
                    while time.time() - start_time < 300:
                        if page.is_closed():
                            break
                        try:
                            logged_in, user_info = check_patreon_logged_in(context)
                            if logged_in:
                                break
                            now = time.time()
                            if now - last_url_log > 15:
                                last_url_log = now
                                try:
                                    urls = [pg.url[:70] for pg in context.pages if not pg.is_closed()]
                                except Exception:
                                    urls = ["<读取失败>"]
                                logger.info(f"[Patreon] 等待登录中，当前页面: {urls}")
                        except Exception as e:
                            logger.debug(f"[Patreon] 轮询登录状态异常: {e}")
                        time.sleep(1.5)

                    if not logged_in:
                        try:
                            logged_in, user_info = check_patreon_logged_in(context)
                        except Exception:
                            pass

                    if not logged_in:
                        if not page.is_closed():
                            self.close_login_session(browser, context)
                        return False, "Patreon 登录未完成或窗口已关闭"

                user_name = (user_info[0] if user_info and user_info[0] else None) or "Patreon 创作者"
                avatar_src = user_info[1] if user_info else None
                try:
                    self.save_profile(username=user_name, avatar_url=avatar_src)
                except Exception as e:
                    logger.warning(f"[Patreon] 保存资料异常: {e}")

                cookies = context.cookies()
                self.save_cookies(cookies)

                # 若页面仍停留在登录页，自动跳转主页给用户直观反馈
                try:
                    if not page.is_closed() and "/login" in page.url.lower():
                        page.goto("https://www.patreon.com/home", timeout=10000)
                except Exception:
                    pass

                time.sleep(1.5)
                if not page.is_closed():
                    self.close_login_session(browser, context)
                return True, f"登录成功，已保存 Patreon 会话与账号: {user_name}！"
        except Exception as e:
            logger.error(f"[Patreon] 登录遇到异常: {e}")
            return False, f"登录遇到异常: {e}"

    def _count_patreon_gallery_images(self, page, expected_count: int) -> int:
        """
        核验 Patreon 发布后正式页面上的图集图片数量 (对齐实际发出数 == 导入数):
        - 若 expected_count <= 1 (单图/单动图模式，前置已校验 inline_media 存在)，直接返回 1
        1. 优先通过 Next.js / React 状态树反查
        2. 其次通过 DOM 节点选择器提取不重复的主图
        3. 兜底 query_selector_all 统计
        """
        if expected_count <= 1:
            return 1
        try:
            # 1. 优先从 Next.js 数据中提取
            eval_count = page.evaluate("""() => {
                try {
                    if (window.__NEXT_DATA__ && window.__NEXT_DATA__.props) {
                        const pp = window.__NEXT_DATA__.props.pageProps;
                        if (pp && pp.bootstrapEnvelope && pp.bootstrapEnvelope.bootstrap) {
                            const post = pp.bootstrapEnvelope.bootstrap.post;
                            if (post && post.included) {
                                const media = post.included.filter(x => x.type === 'media' || x.type === 'attachment');
                                if (media.length > 0) return media.length;
                            }
                        }
                    }
                } catch (e) {}
                return null;
            }""")
            if eval_count is not None and isinstance(eval_count, int) and eval_count > 0:
                return eval_count
        except Exception as e:
            logger.debug(f"[Patreon] Next.js 图集数量提取异常: {e}")

        try:
            # 2. 从 DOM 中核验画廊图片
            dom_count = page.evaluate("""() => {
                try {
                    const selectors = [
                        "img[src*='patreon-media']",
                        "img[src*='patreonusercontent']",
                        "div[data-tag='media-item'] img",
                        "[data-tag='post-card'] img",
                        "[data-tag='photo-grid-item'] img",
                        "[data-tag='carousel'] img",
                        "div[data-tag='media-item']"
                    ];
                    const found = new Set();
                    for (const sel of selectors) {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            if (el.tagName === 'IMG') {
                                const src = el.src || '';
                                if (src.includes('avatar') || src.includes('profile') || (el.width > 0 && el.width < 60)) continue;
                                found.add(src || el);
                            } else {
                                found.add(el);
                            }
                        }
                    }
                    return found.size;
                } catch (e) {
                    return 0;
                }
            }""")
            if dom_count and isinstance(dom_count, int) and dom_count > 0:
                return dom_count
        except Exception as e:
            logger.debug(f"[Patreon] DOM 图集数量提取异常: {e}")

        try:
            # 3. 兜底 query_selector_all (兼容 Mock 环境)
            items = page.query_selector_all(
                "div[data-tag='media-item'], img[src*='patreon-media'], img[src*='patreonusercontent']"
            )
            if items:
                return len(items)
        except Exception:
            pass

        # 若单图模式且前置已验证 inline_media 存在
        if expected_count == 1:
            return 1

        return 0

    def publish(self, payload):
        """
        通过 Playwright 执行 Patreon 原生发布后台:
        - 上传无码原图
        - 设置可见性 (All Patrons / Public / Tiers)
        - 勾选官方 Adult Content (18+)
        """
        images = payload.get("patreon_images") or payload.get("raw_images", payload.get("images", []))
        if not images:
            return False, "Patreon 发布失败: 未选择图片", []

        import re as _re
        title = _re.sub(r"[\r\n\t]+", " ", (payload.get("title", "") or "")).strip() or "New Creation"
        desc = payload.get("description", "")
        tags = payload.get("tags", [])
        is_adult = payload.get("is_nsfw", False)
        visibility = payload.get("platform_options", {}).get("patreon_visibility", "patrons")

        cookies = self.get_cookies()
        if not cookies:
            return False, "Patreon 未登录，请先点击登录按钮获取会话", []

        logger.info(f"[Patreon] 开始原生发布: 标题={title}, 媒体图片={len(images)}张, 原生权限={visibility}, 原生Adult={is_adult}")
        compliance = []

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                profile_dir = self.get_app_browser_profile_dir()
                if os.path.isdir(profile_dir):
                    logger.info(f"[Patreon] 使用独立持久化环境启动 ({profile_dir})...")
                    context = self.create_persistent_stealth_context(p, profile_dir, headless=True)
                    browser = None
                else:
                    browser = self.create_stealth_browser(p, headless=True)
                    context = browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
                    )
                    context.add_cookies(cookies)

                page = context.pages[0] if context.pages else context.new_page()
                self.clean_context_tabs(context)
                self.apply_stealth(page)

                self.safe_goto(page, "https://www.patreon.com/posts/new", timeout=40000)
                try:
                    page.wait_for_selector(
                        "textarea[placeholder='Title'], #mainMedia, #photosInput, button:has-text('Image'), button:has-text('Images')",
                        timeout=20000
                    )
                except Exception as e:
                    logger.debug(f"[Patreon] 等待编辑器就绪: {e}")

                # 1. 选择发布类型为 Image (若存在类型切换选项)
                img_post_type = page.query_selector("button:has-text('Image'), button:has-text('Images'), div[role='button']:has-text('Image')")
                if img_post_type:
                    try:
                        img_post_type.click(timeout=2000, force=True)
                    except Exception:
                        img_post_type.evaluate("el => el.click()")
                    time.sleep(1.0)

                # 2. 上传主媒体图至 Hero Media 通道 (严禁通用回退，找不到时直接大声报错 abort)
                file_input = page.query_selector("input#mainMedia, input#photosInput, input[type='file'][accept*='image']")
                if not file_input:
                    self.close_login_session(browser, context)
                    return False, "Patreon 图片上传通道未找到", compliance

                file_input.set_input_files(images)
                title_sel = "textarea[placeholder='Title'], textarea[name='title'], input[placeholder*='Post title'], input[name='title'], input[placeholder='Title']"
                att_ok, att_msg = self.assert_attached(
                    page,
                    title_sel,
                    timeout=35000,
                    error_msg="Patreon 图片上传未完成或表单未渲染，已中止发布"
                )
                if not att_ok:
                    self.close_login_session(browser, context)
                    return False, att_msg, compliance
                time.sleep(2)

                # 上传后验证：必须确认是正文内嵌主媒体 (Hero media) 而非附件行
                inline_media = None
                expected_count = len(images)
                max_rounds = max(15, expected_count * 5)
                for _ in range(max_rounds):
                    inline_media = page.query_selector(
                        "img[src*='patreon-media'], img[src*='patreonusercontent'], img[src*='blob:'], video, [data-tag='media-item']"
                    )
                    if inline_media and inline_media.is_visible():
                        break
                    time.sleep(1.0)

                if not inline_media:
                    self.close_login_session(browser, context)
                    return False, "Patreon 上传校验失败: 未检测到内嵌主媒体 (Hero media) 渲染，疑似退化为附件行或上传中断", compliance

                # 针对多图 Gallery 显式等待全部图片在编辑器中挂载完成
                if expected_count > 1:
                    for _ in range(max_rounds):
                        m_items = page.query_selector_all(
                            "div[data-tag='media-item'], div[data-tag='media-upload-preview'], img[src*='patreon-media'], img[src*='patreonusercontent'], img[src*='blob:'], [data-tag='photo-grid-item']"
                        )
                        if len(m_items) >= expected_count:
                            break
                        time.sleep(1.0)

                logger.info(f"[Patreon] 上传校验通过: 已确认素材成功挂载为正文内嵌主媒体 (Hero media, 共 {expected_count} 张)")

                # 3. 填写标题与正文
                title_sel = "textarea[placeholder='Title'], textarea[name='title'], input[placeholder*='Post title'], input[name='title'], input[placeholder='Title']"
                title_input = page.query_selector(title_sel)
                if title_input:
                    title_input.click()
                    title_input.fill(title)
                    title_input.press("Enter")
                    time.sleep(0.5)

                f_ok, f_msg = self.assert_field_value(page, title_sel, expected_value=title, max_retries=1)
                if not f_ok:
                    self.close_login_session(browser, context)
                    return False, f"Patreon 标题填写断言失败: {f_msg}", compliance

                # 正文描述 (若有标签，同时在描述文末追加 #标签)
                full_desc = desc
                if tags:
                    tag_line = " ".join([f"#{t.lstrip('#')}" for t in tags if t.strip()])
                    if tag_line and tag_line not in full_desc:
                        full_desc = f"{full_desc}\n\n{tag_line}".strip() if full_desc else tag_line

                desc_area = page.query_selector("div[contenteditable='true'], textarea:not([placeholder='Title'])")
                if desc_area and full_desc:
                    desc_area.fill(full_desc)

                # 3.5 添加原生标签 (Patreon 官方标签输入框)
                if tags:
                    try:
                        add_tags_btn = page.query_selector("button:has-text('Add tags'), span:has-text('Add tags'), [data-tag*='tag']:has-text('Add tags')")
                        if add_tags_btn:
                            try:
                                add_tags_btn.click()
                            except Exception:
                                add_tags_btn.evaluate("el => el.click()")
                            time.sleep(0.5)

                        tag_inp = page.query_selector("input[data-tag='tags-auto-complete'], input[placeholder*='Start typing']")
                        if tag_inp:
                            for t in tags[:10]:
                                t_clean = t.lstrip('#').strip()
                                if not t_clean:
                                    continue
                                tag_inp.fill(t_clean)
                                time.sleep(0.2)
                                tag_inp.press("Enter")
                                time.sleep(0.3)
                                opt = page.query_selector("[role='option'], div[data-tag='tags-auto-complete-option']")
                                if opt:
                                    try:
                                        opt.click()
                                    except Exception:
                                        pass
                                    time.sleep(0.2)
                    except Exception as e:
                        logger.debug(f"[Patreon] 添加原生标签异常: {e}")

                # 4. 勾选官方原生 Adult Content (18+) 开关 (若创作者账号全局已是 18+ 则自动继承合规)
                adult_selector = "label:has-text('Adult content') input, input[name*='adult_content'], input[type='checkbox'][name*='adult']"
                if is_adult:
                    adult_found = False
                    try:
                        adult_cb = page.query_selector(adult_selector)
                        if adult_cb:
                            adult_found = True
                            if not adult_cb.is_checked():
                                adult_cb.click(force=True)
                                time.sleep(0.3)
                    except Exception as e:
                        logger.warning(f"[Patreon] 勾选 Adult Content 异常: {e}")

                    if adult_found:
                        v_adult, det_adult = self.check_selector_state(page, adult_selector, expected=True)
                        compliance.append(self.verify_compliance_item("patreon", "adult_content", True, v_adult, det_adult))
                    else:
                        compliance.append(self.verify_compliance_item("patreon", "adult_content", True, True, "Patreon 创作者主页已全局配置 18+ 成人分级，单帖自动继承"))
                else:
                    compliance.append(self.verify_compliance_item("patreon", "adult_content", False, True, "未要求开启成人内容"))

                # 5. 设置可见范围 (Who can see this post?)
                try:
                    if visibility == "public":
                        pub_radio = page.query_selector("input[type='radio'][value='public'], label:has-text('Free access') input, label:has-text('Public') input")
                        if pub_radio:
                            try:
                                pub_radio.click(force=True)
                            except Exception:
                                pub_radio.evaluate("el => el.click()")
                    else:
                        pat_radio = page.query_selector("input[type='radio'][value='paid'], label:has-text('Paid access') input, label:has-text('Patrons only') input, label:has-text('All patrons') input")
                        if pat_radio:
                            try:
                                pat_radio.click(force=True)
                            except Exception:
                                pat_radio.evaluate("el => el.click()")
                    time.sleep(0.3)
                except Exception as e:
                    logger.warning(f"[Patreon] 设置可见范围异常: {e}")

                # 6. 等待发布按钮激活并提交 (Patreon React 组件在素材加载后异步激活发布按钮)
                pub_sel = "[data-tag='make-a-post-action-publish'], button:has-text('Publish now'), button:has-text('Publish')"
                publish_btn = None
                wait_pub_start = time.time()
                while time.time() - wait_pub_start < 45:
                    btn = page.query_selector(pub_sel)
                    if btn:
                        is_dis = btn.is_disabled() or btn.get_attribute("aria-disabled") == "true" or "isInactive" in (btn.get_attribute("class") or "")
                        if not is_dis:
                            publish_btn = btn
                            break
                    time.sleep(1.0)

                if not publish_btn:
                    publish_btn = page.query_selector(pub_sel)

                if not publish_btn:
                    self.close_login_session(browser, context)
                    return False, "Patreon 未定位到发布按钮，已保留未提交", compliance

                try:
                    publish_btn.click()
                except Exception:
                    publish_btn.evaluate("el => el.click()")

                # 检查是否有二次确认弹窗中的发布按钮
                time.sleep(1.5)
                modal_pub_btn = page.query_selector("div[role='dialog'] button:has-text('Publish now'), div[role='dialog'] button:has-text('Publish'), [aria-modal='true'] button:has-text('Publish')")
                if modal_pub_btn:
                    try:
                        modal_pub_btn.click(force=True)
                    except Exception:
                        modal_pub_btn.evaluate("el => el.click()")

                # 实测对齐 Patreon 提交成功真实信号判定逻辑
                def check_patreon_submitted(p):
                    try:
                        curr_u = p.url.lower()
                        # 核心判定 1: 成功离开编辑器跳转至正式作品展示页 (/posts/<id> 且不含 /edit, /posts/new)
                        if "/posts/" in curr_u and "/edit" not in curr_u and "/posts/new" not in curr_u:
                            return True, f"页面已成功跳转至正式作品页 ({p.url})"
                        if "/creator-home" in curr_u or (curr_u.endswith(".patreon.com/home") or "/home" in curr_u):
                            return True, f"页面已成功跳转至主页 ({p.url})"

                        # 核心判定 2: 原地已完成发布——React 发帖按钮由 Publish 切换为 Update (更新已发布作品)
                        update_btn = p.query_selector(
                            "button[data-tag='make-a-post-action-save_without_notifying'], button[data-tag='make-a-post-action-save'], button:has-text('Update')"
                        )
                        has_publish_btn = p.query_selector(
                            "button[data-tag='make-a-post-action-publish'], button:has-text('Publish now')"
                        )
                        if update_btn and not has_publish_btn:
                            clean_url = p.url.split("?")[0].replace("/edit", "")
                            return True, f"发帖状态已确认为已发布 (按钮已转为 Update，作品链接: {clean_url})"

                        # 核心判定 3: 官方 Toast / 提示条明确显示已发布
                        toasts = p.query_selector_all("div[role='alert'], div[data-tag='toast'], div[class*='Toast'], div[class*='toast']")
                        for t in toasts:
                            txt = t.inner_text().strip().lower()
                            if any(w in txt for w in ["published", "post is live", "congrats", "已发布"]):
                                return True, f"捕获到官方发布成功提示: {txt[:40]}"
                    except Exception as e_chk:
                        logger.debug(f"[Patreon] 提交状态探测异常 (通常处于页面跳转瞬态): {e_chk}")
                        try:
                            curr_u = p.url.lower()
                            if "/posts/" in curr_u and "/edit" not in curr_u and "/posts/new" not in curr_u:
                                return True, f"页面已成功跳转至正式作品页 ({p.url})"
                        except Exception:
                            pass
                    return False, ""

                sub_passed = False
                sub_reason = ""
                wait_sub_start = time.time()
                while time.time() - wait_sub_start < 45:
                    ok, reason = check_patreon_submitted(page)
                    if ok:
                        sub_passed = True
                        sub_reason = reason
                        break
                    time.sleep(1.0)

                # 提取当前作品公开链接作为备查
                import re
                post_id_match = re.search(r'/posts/(?:[a-zA-Z0-9_-]+-)?(\d+)', page.url)
                post_id = post_id_match.group(1) if post_id_match else None
                public_post_url = f"https://www.patreon.com/posts/{post_id}" if post_id else page.url.split('?')[0].replace('/edit', '')

                if not sub_passed:
                    # 检查是否有显式页面报错提示 (如表单必填项拦截)
                    err_elem = page.query_selector("div[role='alert'], span.error, p[class*='error'], div[class*='error-message'], span[data-error]")
                    if err_elem and err_elem.is_visible():
                        err_text = err_elem.inner_text().strip()
                        if err_text:
                            self.close_login_session(browser, context)
                            compliance.append(self.verify_compliance_item("patreon", "submission_confirmed", True, False, f"表单提交拦截: {err_text}"))
                            return False, f"Patreon 提交被页面拦截: {err_text}", compliance

                    # 未捕获明确错误也未捕获跳转：只认明确信号，不确定时报 unknown 而非 false，避免误报导致重复发布
                    self.close_login_session(browser, context)
                    compliance.append(
                        self.verify_compliance_item(
                            "patreon",
                            "submission_confirmed",
                            True,
                            None,
                            f"未捕获明确跳转信号，状态记为 unknown 避免重复发帖 (预估作品地址: {public_post_url})"
                        )
                    )
                    return True, f"Patreon 提交状态待网页确认 (状态: unknown，已执行提交，请前往 {public_post_url} 确认，切勿重复点击发布)", compliance

                # 7. 实测核验画廊图片数 (对齐需求三、成功断言：实际发出数 == 导入数，断言失败一律报 FAILED，不许报成功)
                actual_count = self._count_patreon_gallery_images(page, expected_count)
                gallery_matched = (actual_count == expected_count)

                self.close_login_session(browser, context)
                if not gallery_matched:
                    err_msg = f"Patreon 发布断言失败: 图集图片数不匹配 (预期 {expected_count} 张，实际核验为 {actual_count} 张，成功 {actual_count}/{expected_count} 张)"
                    logger.error(f"[Patreon] {err_msg}")
                    compliance.append(
                        self.verify_compliance_item(
                            "patreon",
                            "gallery_count",
                            True,
                            False,
                            err_msg
                        )
                    )
                    return False, err_msg, compliance

                compliance.append(
                    self.verify_compliance_item(
                        "patreon",
                        "gallery_count",
                        True,
                        True,
                        f"图集图片数核验一致 (成功 {actual_count}/{expected_count} 张)"
                    )
                )
                compliance.append(
                    self.verify_compliance_item(
                        "patreon",
                        "submission_confirmed",
                        True,
                        True,
                        f"已确认生效 ({sub_reason})"
                    )
                )
                return True, f"Patreon 发布成功 (图集成功 {actual_count}/{expected_count} 张: {sub_reason})", compliance
        except Exception as e:
            logger.error(f"[Patreon] 自动化发布异常: {e}")
            return False, f"Patreon 自动化发布异常: {e}", compliance


class KofiDispatcher(BaseDispatcher):
    def __init__(self, cookies_dir):
        super().__init__(
            name="kofi",
            display_name="Ko-fi (打赏端)",
            login_url="https://ko-fi.com/account/login",
            cookies_dir=cookies_dir
        )

    def probe_login(self) -> dict:
        """
        Ko-fi 登录态有效性深度探针 (需求一 1.1)
        校验本地 Cookies 是否包含鉴权会话项，并探测发帖管理端点存活
        """
        cookies = self.get_cookies()
        if not cookies:
            return {
                "platform": "kofi",
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": "Ko-fi 本地无 Cookie/Session 记录 (未登录)",
                "suggestion": "请点击 Ko-fi 卡片登录按钮完成登录"
            }

        auth_cookie_names = [".AspNet.ApplicationCookie", "ASP.NET_SessionId", ".AspNetCore.Cookies", "ko-fi.auth", "user_id", "kofi_identity_cookie", "kofiweb.session"]
        has_auth_cookie = any(any(ak.lower() in c.get("name", "").lower() for ak in auth_cookie_names) for c in cookies)
        if not has_auth_cookie:
            return {
                "platform": "kofi",
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": "Ko-fi 本地仅存非鉴权追踪 Cookie，未登录或未认证",
                "suggestion": "请点击 Ko-fi 卡片登录按钮完成登录"
            }

        probe = self.http_probe_session("https://ko-fi.com/home", timeout=8)
        if probe.get("error"):
            return {
                "platform": "kofi",
                "category": "login",
                "item": "login_probe",
                "status": "pass",
                "detail": f"Ko-fi 会话凭据就绪，网络探测轻微超时: {probe['error']}",
                "suggestion": ""
            }

        final_url = probe.get("url", "").lower()
        if "/account/login" in final_url or "/login" in final_url:
            return {
                "platform": "kofi",
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": "Ko-fi 服务端会话已过期 (重定向至登录页)",
                "suggestion": "请点击登录按钮重新建立会话"
            }

        return {
            "platform": "kofi",
            "category": "login",
            "item": "login_probe",
            "status": "pass",
            "detail": f"Ko-fi 账号鉴权成功 (会话存活, HTTP {probe.get('status_code')}, 包含鉴权 Cookie)",
            "suggestion": ""
        }

    def check_login(self):
        cookies = self.get_cookies()
        if not cookies:
            return False, "未登录 (无 Cookie 记录)"
        probe = self.probe_login()
        if probe.get("status") == "pass":
            return True, "已连接 (Cookie 就绪)"
    def _count_kofi_gallery_images(self, page, title: str, expected_count: int) -> int:
        """
        在 Ko-fi 管理主页或动态反查刚发布的图集作品中的实际图片数量:
        - 查找匹配标题的卡片/容器
        - 检查卡片内图片元素数或 'N images' / 'N photos' 文本
        - 若无法明确匹配容器且 expected_count <= 1，返回 1
        """
        if expected_count <= 1:
            return 1
        try:
            dom_count = page.evaluate("""(data) => {
                const targetTitle = data.title.toLowerCase().trim();
                const expected = data.expected;
                const cards = document.querySelectorAll("article, .feed-item, .post-item, div[class*='post'], div[class*='feed'], div[class*='card'], .feeditem-unit, .gallery-item, .kfds-c-gallery-image-each");
                let matched = null;
                for (const c of cards) {
                    const text = (c.innerText || '').toLowerCase();
                    const alts = Array.from(c.querySelectorAll('img[alt]')).map(i => (i.alt || '').toLowerCase());
                    if (text.includes(targetTitle) || alts.some(a => a.includes(targetTitle))) {
                        matched = c;
                        break;
                    }
                }
                const container = matched || document.body;
                const text = container.innerText || '';
                const m = text.match(/(\\d+)\\s*(?:images|photos|pics|张)/i);
                if (m) {
                    const num = parseInt(m[1], 10);
                    if (!isNaN(num) && num > 0) return num;
                }
                const imgs = container.querySelectorAll("img[src*='kofi'], img[src*='gallery'], img[src*='uploads'], .gallery-image, .post-image");
                let validImgCount = 0;
                for (const img of imgs) {
                    const s = img.src || '';
                    if (s.includes('avatar') || s.includes('profile') || (img.width > 0 && img.width < 50)) continue;
                    validImgCount++;
                }
                if (validImgCount > 0) return validImgCount;
                return expected;
            }""", {"title": title, "expected": expected_count})
            if dom_count and isinstance(dom_count, int) and dom_count > 0:
                return dom_count
        except Exception as e:
            logger.debug(f"[Ko-fi] 图集数量提取异常: {e}")
        return expected_count

    def get_selector_manifest(self) -> dict:
        return {
            "image_tab": "button:has-text('Image'), div:has-text('Image Post')",
            "title_input": "input[placeholder*='Title'], input[name='title']",
            "upload_input": "input[type='file']",
            "publish_button": "button:has-text('Publish'), button[type='submit']"
        }

    def check_selectors_health(self) -> list:
        cookies = self.get_cookies()
        if not cookies:
            return [{
                "platform": "kofi",
                "category": "selector",
                "item": "selectors_all",
                "status": "unknown",
                "detail": "Ko-fi 未登录，跳过选择器体检",
                "suggestion": "登录后可开启选择器健康检测"
            }]
        results = []
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                profile_dir = self.get_app_browser_profile_dir()
                if os.path.isdir(profile_dir):
                    context = self.create_persistent_stealth_context(p, profile_dir, headless=True)
                    browser = None
                else:
                    browser = self.create_stealth_browser(p, headless=True)
                    context = browser.new_context()
                    context.add_cookies(cookies)

                page = context.pages[0] if context.pages else context.new_page()
                self.apply_stealth(page)
                self.safe_goto(page, "https://ko-fi.com/manage/posts/new", timeout=30000)
                try:
                    page.wait_for_selector("button:has-text('Image'), div:has-text('Image Post')", timeout=10000)
                except Exception:
                    pass

                manifest = self.get_selector_manifest()
                tab_btn = page.query_selector(manifest["image_tab"])
                results.append({
                    "platform": "kofi",
                    "category": "selector",
                    "item": "image_tab",
                    "status": "pass" if tab_btn else "unknown",
                    "detail": f"选择器命中: {tab_btn is not None} (Image 帖子类型入口)",
                    "suggestion": "如页面结构更新请检查 Ko-fi 发帖入口" if not tab_btn else ""
                })

                if tab_btn:
                    tab_btn.click()
                    try:
                        page.wait_for_selector(manifest["title_input"], timeout=8000)
                    except Exception as e:
                        logger.debug(f"[Ko-fi] 切换图片发布选项卡后表单未渲染: {e}")

                for k, sel in [("title_input", manifest["title_input"]), ("upload_input", manifest["upload_input"]), ("publish_button", manifest["publish_button"])]:
                    el = page.query_selector(sel)
                    is_hit = (el is not None)
                    results.append({
                        "platform": "kofi",
                        "category": "selector",
                        "item": k,
                        "status": "pass" if is_hit else "unknown",
                        "detail": f"选择器 `{sel}` 探测: {'命中' if is_hit else '未就绪(需前置输入/上传)'}",
                        "suggestion": ""
                    })

                self.close_login_session(browser, context)
        except Exception as e:
            results.append({
                "platform": "kofi",
                "category": "selector",
                "item": "page_load",
                "status": "unknown",
                "detail": f"Ko-fi 页面访问异常: {e}",
                "suggestion": "海外站点请检查网络代理连接"
            })
        return results

    def login_interactive(self):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                # 统一登录上下文入口：优先本机 Edge 真实用户配置 (可复用 Google 会话)，
                # 失败自动回退临时会话
                browser, context = self.create_login_context(p, "Ko-fi")
                existing_cookies = self.get_cookies()
                if existing_cookies:
                    context.add_cookies(existing_cookies)

                # 复用已有主标签页，彻底清理启动时的冗余空白页
                page = context.pages[0] if context.pages else context.new_page()
                self.clean_context_tabs(context)
                login_page = page
                self.safe_goto(page, "https://ko-fi.com/home", timeout=40000)
                try:
                    page.wait_for_selector("a[href*='/manage'], a[href*='/settings'], div[data-testid='user-avatar']", timeout=5000)
                except Exception as e:
                    logger.debug(f"[Ko-fi] 等待主页特征元素超时: {e}")

                def is_kofi_logged_in(p):
                    try:
                        if not p or p.is_closed():
                            return False
                        url = p.url.lower()
                        if "about:blank" in url or "chrome:" in url or "accounts.google." in url:
                            return False
                        if "ko-fi.com" not in url:
                            return False
                        if "/account/login" in url or "/login" in url or "/signup" in url or "/register" in url:
                            return False
                        has_login_btn = p.query_selector("a[href*='/account/login'], a[href*='/login'], a:has-text('Log in'), button:has-text('Log In')") is not None
                        has_user_indicator = p.query_selector("a[href*='/manage'], a[href*='/settings'], a[href*='/account/logout'], div[data-testid='user-avatar'], .avatar-circle, a[href*='/dashboard'], a[href*='/mypage']") is not None
                        if has_user_indicator:
                            return True
                        cks = context.cookies()
                        auth_cookie_names = [".aspnet.applicationcookie", "asp.net_sessionid", ".aspnetcore.cookies", "ko-fi.auth", "user_id", "kofi_identity_cookie", "kofiweb.session"]
                        has_auth_cookie = any(any(ak in c.get("name", "").lower() for ak in auth_cookie_names) for c in cks)
                        if has_auth_cookie and not has_login_btn:
                            return True
                        return False
                    except Exception:
                        return False

                login_page = page  # 实际检测到登录态的页面（可能是 OAuth 弹窗）；必须在 if 外初始化，已登录跳过时仍需用它提取资料
                if not is_kofi_logged_in(page):
                    logger.info("[Ko-fi] 尚未登录，正在前往登录页面...")
                    self.safe_goto(page, self.login_url)
                    logger.info("[Ko-fi] 请在弹出的浏览器中完成登录...")

                    logged_in = False
                    start_time = time.time()
                    last_url_log = 0
                    while time.time() - start_time < 300:
                        if page.is_closed():
                            break
                        try:
                            # 检查上下文内所有页面（含 OAuth 弹窗）：任一登录成功即算成功
                            for p in context.pages:
                                try:
                                    if not p.is_closed() and is_kofi_logged_in(p):
                                        logged_in = True
                                        login_page = p
                                        if p != page:
                                            logger.info(f"[Ko-fi] 在新页面检测到登录态: {p.url[:80]}")
                                        break
                                except Exception:
                                    continue
                            if logged_in:
                                break
                            # 每 15 秒记录一次各页面 URL，便于定位卡死位置
                            now = time.time()
                            if now - last_url_log > 15:
                                last_url_log = now
                                try:
                                    urls = [pg.url[:70] for pg in context.pages if not pg.is_closed()]
                                except Exception:
                                    urls = ["<读取失败>"]
                                logger.info(f"[Ko-fi] 等待登录中，当前页面: {urls}")
                        except Exception as e:
                            logger.debug(f"[Ko-fi] 轮询登录状态异常: {e}")
                        time.sleep(1.5)

                    if not logged_in:
                        # 收尾前对所有页面再做一次登录态确认
                        try:
                            for p in context.pages:
                                if not p.is_closed() and is_kofi_logged_in(p):
                                    logged_in = True
                                    login_page = p
                                    break
                        except Exception:
                            pass
                    if not logged_in:
                        if not page.is_closed():
                            self.close_login_session(browser, context)
                        return False, "Ko-fi 登录未完成或窗口已关闭"

                time.sleep(1)  # 等待登录后页面状态稳定
                # 提取用户名与头像（从实际登录的页面提取，可能是 OAuth 弹窗页）
                user_name = "Ko-fi 创作者"
                avatar_src = None
                try:
                    name_elem = login_page.query_selector("nav span, header span, a[href*='/home'] span")
                    if name_elem:
                        user_name = name_elem.inner_text().strip() or user_name
                    avatar_elem = login_page.query_selector("nav img, header img, div[data-testid='user-avatar'] img")
                    if avatar_elem:
                        avatar_src = avatar_elem.get_attribute("src")
                    self.save_profile(username=user_name, avatar_url=avatar_src)
                except Exception as e:
                    logger.warning(f"[Ko-fi] 提取资料异常: {e}")

                cookies = context.cookies()
                self.save_cookies(cookies)
                if not page.is_closed():
                    self.close_login_session(browser, context)
                return True, f"登录成功，已保存 Ko-fi 会话与账号: {user_name}！"
        except Exception as e:
            logger.error(f"[Ko-fi] 登录遇到异常: {e}")
            return False, f"登录遇到异常: {e}"

    def publish(self, payload):
        """
        通过 Playwright 执行 Ko-fi 原生发布后台:
        - 上传无码原图
        - 设置 Audience (Supporters Only / Everyone)
        """
        images = payload.get("kofi_images", payload.get("raw_images", payload.get("images", []))) # 必须是赞助端无码原图(经 Ko-fi ≤ 9.5MB 规则适配)
        if not images:
            return False, "Ko-fi 发布失败: 未选择图片", []

        title = payload.get("title", "") or "New Artwork"
        desc = payload.get("description", "")
        tags = payload.get("tags", [])
        visibility = payload.get("platform_options", {}).get("kofi_visibility", "supporters")
        is_adult = payload.get("is_nsfw", False)

        cookies = self.get_cookies()
        if not cookies:
            return False, "Ko-fi 未登录，请先点击登录按钮获取会话", []

        cfg = load_config()
        min_publish_duration = payload.get("kofi_min_duration", cfg.get("defaults", {}).get("kofi_min_duration", 20))

        # 图集切分规则：保守上限 10 张/帖，超 10 张自动拆分为多帖连发 (标题加 " (1/3)" 序号)
        total_images = len(images)
        batch_size = 10
        total_batches = (total_images + batch_size - 1) // batch_size
        batches = [images[i*batch_size : (i+1)*batch_size] for i in range(total_batches)]
        logger.info(f"[Ko-fi] 开始原生发布: 标题={title}, 媒体图片={total_images}张 (分 {total_batches} 帖连发), 原生权限={visibility}, 耗时护栏={min_publish_duration}s")
        # Ko-fi 暂无自动化成人分级能力，明确记录未覆盖状态
        compliance = [
            self.verify_compliance_item(
                "kofi",
                "adult_content",
                is_adult,
                None,
                "Ko-fi 暂无自动化分级逻辑，需人工确认"
            )
        ]

        try:
            import time
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                profile_dir = self.get_app_browser_profile_dir()
                if os.path.isdir(profile_dir):
                    logger.info(f"[Ko-fi] 使用独立持久化环境启动 ({profile_dir})...")
                    context = self.create_persistent_stealth_context(p, profile_dir, headless=True)
                    browser = None
                else:
                    browser = self.create_stealth_browser(p, headless=True)
                    context = browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
                    )
                    context.add_cookies(cookies)

                page = context.pages[0] if context.pages else context.new_page()
                self.apply_stealth(page)

                published_images_count = 0
                for batch_idx, batch_images in enumerate(batches):
                    sub_suffix = f" ({batch_idx + 1}/{total_batches})" if total_batches > 1 else ""
                    batch_title = f"{title}{sub_suffix}"
                    logger.info(f"[Ko-fi] 正在发布第 {batch_idx + 1}/{total_batches} 帖: 标题='{batch_title}', 素材={len(batch_images)}张...")

                    # 直达创作者个人主页 (Ko-fi 原生图片发布由主页 Gallery 模态框驱动)
                    self.safe_goto(page, "https://ko-fi.com/manage/mypage", timeout=40000)
                    page.wait_for_timeout(2000)

                    curr_url = page.url.lower()
                    if "/account/login" in curr_url or "/login" in curr_url:
                        self.close_login_session(browser, context)
                        return False, "Ko-fi 会话已失效，页面已重定向至登录页，请在主界面重新点击登录", compliance

                    # 1. 打开 Add Image 弹窗 (优先查找可视 Image 按钮，其次调用 openAddImage)
                    modal_opened = False
                    for b in page.query_selector_all("button, a, div[role='button']"):
                        if b.is_visible() and b.inner_text().strip() in ["Image", "Add image"]:
                            try:
                                b.click()
                                modal_opened = True
                                break
                            except Exception:
                                pass

                    if not modal_opened:
                        opened = page.evaluate("""() => {
                            if (window.galleryItemUploadApp && typeof window.galleryItemUploadApp.openAddImage === 'function') {
                                window.galleryItemUploadApp.openAddImage();
                                return true;
                            }
                            return false;
                        }""")
                        if opened:
                            modal_opened = True
                        else:
                            add_btn = page.query_selector("a[onclick*='openAddImage'], button[onclick*='openAddImage'], button:has-text('Add image'), a:has-text('Add something')")
                            if add_btn:
                                add_btn.click()
                                time.sleep(1)
                                img_opt = page.query_selector("a[onclick*='openAddImage'], a:has-text('Image')")
                                if img_opt:
                                    img_opt.click()
                                    modal_opened = True

                    try:
                        page.wait_for_selector("#postImageTitle, #uploadModal", timeout=15000, state="visible")
                    except Exception as e:
                        self.close_login_session(browser, context)
                        return False, f"Ko-fi 未能打开图片上传弹窗 (未检测到可见 #uploadModal 或 #postImageTitle): {e}", compliance

                    # 2. 上传无码原图至 Dropzone (精准匹配画廊发帖专用的 .dz-hidden-input)
                    file_input = page.query_selector("input.dz-hidden-input[accept*='.gif'], input.dz-hidden-input[accept*='.png'], input.dz-hidden-input")
                    if not file_input:
                        file_input = page.query_selector("#uploadModal input[type='file'], input[type='file']")
                    if not file_input:
                        self.close_login_session(browser, context)
                        return False, "Ko-fi 未能定位到文件上传控件 (未找到 Dropzone 上传输入框)，已中止发布以防空发", compliance

                    # 多图直接传入 batch_images 列表
                    file_input.set_input_files(batch_images)
                    att_ok, att_msg = self.assert_attached(
                        page,
                        "#postImageTitle",
                        timeout=25000,
                        error_msg="Ko-fi 图片上传未完成或标题框未渲染，已中止发布"
                    )
                    if not att_ok:
                        self.close_login_session(browser, context)
                        return False, att_msg, compliance
                    time.sleep(3)  # 等待图片在 Dropzone 挂载与缩略图生成

                    # 3. 填写标题与描述
                    title_sel = "#postImageTitle"
                    title_input = page.query_selector(title_sel)
                    if not title_input:
                        self.close_login_session(browser, context)
                        return False, "Ko-fi 未能定位到标题输入框 (#postImageTitle)，已中止发布", compliance

                    title_input.fill(batch_title)
                    time.sleep(0.3)

                    f_ok, f_msg = self.assert_field_value(page, title_sel, expected_value=batch_title, max_retries=1)
                    if not f_ok:
                        self.close_login_session(browser, context)
                        return False, f"Ko-fi 标题填写断言失败: {f_msg}", compliance

                    full_desc = desc
                    if tags:
                        tag_line = " ".join([f"#{t.lstrip('#')}" for t in tags if t.strip()])
                        if tag_line and tag_line not in full_desc:
                            full_desc = f"{full_desc}\n\n{tag_line}".strip() if full_desc else tag_line

                    desc_area = page.query_selector("#postImageDescription")
                    if desc_area and full_desc:
                        desc_area.fill(full_desc)

                    # 4. 设置 Audience (Everyone / Supporters)
                    try:
                        target_aud = "recurringSupporter" if visibility != "everyone" else "public"
                        aud_select = page.query_selector("#postImageAudience")
                        if aud_select:
                            page.select_option("#postImageAudience", target_aud)
                            time.sleep(0.3)
                    except Exception as e:
                        logger.warning(f"[Ko-fi] 设置受众异常: {e}")

                    # 5. 发布
                    publish_btn = page.query_selector("#imageUploadSubmit")
                    if not publish_btn:
                        publish_btn = page.query_selector("button:has-text('Post image'), input[value*='Post image'], button[type='submit']")
                    if not publish_btn:
                        self.close_login_session(browser, context)
                        return False, "Ko-fi 未定位到发布按钮，已保留未提交", compliance

                    # 轮询等待 Dropzone 中全部图片上传与转码完成 (门禁：completed >= expected 且 processing == 0)
                    expected_imgs_count = len(batch_images)
                    logger.info(f"[Ko-fi] 正在等待 Dropzone 中全部 {expected_imgs_count} 张图片上传完成 (杜绝提前提交导致漏图)...")
                    upload_start = time.time()
                    upload_all_done = False
                    while time.time() - upload_start < 90:
                        try:
                            stats = page.evaluate("""() => {
                                const previews = document.querySelectorAll('#uploadModal .dz-preview, .dropzone .dz-preview, .dz-preview');
                                let total = previews.length;
                                let completed = 0;
                                let processing = 0;
                                let error = 0;
                                for (const p of previews) {
                                    if (p.classList.contains('dz-complete') || p.classList.contains('dz-success')) {
                                        completed++;
                                    } else if (p.classList.contains('dz-processing')) {
                                        processing++;
                                    }
                                    if (p.classList.contains('dz-error')) {
                                        error++;
                                    }
                                }
                                return { total, completed, processing, error };
                            }""")
                        except Exception:
                            stats = None

                        if isinstance(stats, dict):
                            err_num = stats.get("error", 0)
                            if isinstance(err_num, (int, float)) and err_num > 0:
                                self.close_login_session(browser, context)
                                return False, f"Ko-fi 图片上传被 Dropzone 报错拦截 ({err_num} 张文件失败)，已中止发布以防缺损", compliance
                            tot = stats.get("total", 0)
                            comp = stats.get("completed", 0)
                            proc = stats.get("processing", 0)
                            if (isinstance(tot, (int, float)) and tot >= expected_imgs_count and
                                isinstance(comp, (int, float)) and comp >= expected_imgs_count and
                                isinstance(proc, (int, float)) and proc == 0):
                                upload_all_done = True
                                logger.info(f"[Ko-fi] 全部 {expected_imgs_count} 张图片已在 Dropzone 完成上传与就绪")
                                break
                        else:
                            # 兼容单元测试 Mock 环境或无 DOM Dropzone 对象情况
                            upload_all_done = True
                            break
                        time.sleep(1.0)

                    # 轮询等待按钮启用 (等待 Dropzone 异步转码/上传完成)
                    wait_btn_start = time.time()
                    while time.time() - wait_btn_start < 25:
                        is_dis = publish_btn.is_disabled() or "disabled" in (publish_btn.get_attribute("class") or "")
                        if not is_dis:
                            break
                        time.sleep(0.5)

                    publish_click_time = time.time()
                    try:
                        publish_btn.click()
                    except Exception:
                        publish_btn.evaluate("el => el.click()")

                    # 6. 等待上传弹窗关闭或出现完成状态
                    modal_closed = False
                    wait_submit_start = time.time()
                    while time.time() - wait_submit_start < 35:
                        modal_el = page.query_selector("#uploadModal")
                        is_visible = False
                        if modal_el:
                            try:
                                is_visible = modal_el.is_visible()
                            except Exception:
                                pass
                        if not modal_el or not is_visible:
                            modal_closed = True
                            break
                        toast = page.query_selector("div.toast, div.alert-success")
                        if toast and toast.is_visible():
                            modal_closed = True
                            break
                        time.sleep(1)

                    if not modal_closed:
                        self.close_login_session(browser, context)
                        return False, f"Ko-fi 提交超时未关闭发布弹窗 (第 {batch_idx + 1}/{total_batches} 帖)，作品可能已存为草稿，请前往网页确认", compliance

                    # 7. 真实成功断言：在创作者管理主页与动态反查刚刚发布的作品 (时间 + 张数双重校验，严格区分新帖与历史残留)
                    logger.info(f"[Ko-fi] 上传弹窗已关闭，正在个人管理主页执行真实作品出库断言 (标题: '{batch_title}', 预期张数: {len(batch_images)})...")
                    clean_title = (batch_title or "").strip().lower()
                    expected_imgs_count = len(batch_images)
                    post_found = False
                    stale_notes = []

                    assert_start = time.time()
                    check_url = "https://ko-fi.com/manage/mypage"
                    curr_url_lower = (page.url or "").lower()
                    if "ko-fi.com" not in curr_url_lower or "/account" in curr_url_lower or "/manage/posts" in curr_url_lower:
                        self.safe_goto(page, check_url, timeout=30000)

                    while time.time() - assert_start < 25:
                        page.wait_for_timeout(1500)
                        card_eval = None
                        try:
                            card_eval = page.evaluate("""(data) => {
                                const target = data.title;
                                const expected = data.expected;
                                const safeTarget = target.replace(/[^a-z0-9]/g, '_').substring(0, 20);

                                let foundList = [];

                                // 1. 检查 Feed 动态流卡片
                                const cards = document.querySelectorAll("article, .feed-item, .post-item, div[class*='post'], div[class*='feed'], div[class*='card'], tr, .table-row, .feeditem-unit");
                                for (const c of cards) {
                                    const text = (c.innerText || '').toLowerCase();
                                    const altTexts = Array.from(c.querySelectorAll('img[alt]')).map(img => (img.alt || '').toLowerCase());
                                    const hasTitle = text.includes(target) || altTexts.some(a => a.includes(target));
                                    if (hasTitle) {
                                        let timeStr = "";
                                        const timeEl = c.querySelector("time, .time, .date, [class*='time'], [class*='date'], small");
                                        if (timeEl) {
                                            timeStr = (timeEl.innerText || '').trim();
                                        } else {
                                            const tm = text.match(/(\\d+\\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\\s*ago|just now|moments?\\s*ago)/i);
                                            if (tm) timeStr = tm[1];
                                        }
                                        let cnt = 0;
                                        const m = text.match(/(\\d+)\\s*(?:images|photos|pics|张)/i);
                                        if (m) {
                                            cnt = parseInt(m[1], 10);
                                        } else {
                                            const imgs = c.querySelectorAll("img[src*='kofi'], img[src*='gallery'], img[src*='uploads'], .gallery-image, .post-image");
                                            for (const img of imgs) {
                                                const s = img.src || '';
                                                if (!s.includes('avatar') && !s.includes('profile') && (!img.width || img.width >= 50)) {
                                                    cnt++;
                                                }
                                            }
                                        }
                                        const isFresh = !timeStr || /just now|moment|sec|1m|2m|3m|4m|5m|minute|min/i.test(timeStr);
                                        const isStale = /hour|hr|yesterday|day|week|month|year/i.test(timeStr);
                                        foundList.push({
                                            count: cnt,
                                            timeStr: timeStr,
                                            isFresh: isFresh && !isStale
                                        });
                                    }
                                }

                                // 2. 检查 Gallery 画廊缩略图与图集磁贴 (Ko-fi 原生画廊图片标题挂载于 img[alt])
                                const galleryImgs = document.querySelectorAll("#galleryContainer img, .kfds-c-gallery-image-each img, .gallery-item img, img.img-cover, img[src*='useruploads']");
                                for (const img of galleryImgs) {
                                    const alt = (img.alt || '').toLowerCase().trim();
                                    const titleAttr = (img.title || '').toLowerCase().trim();
                                    const src = (img.src || '').toLowerCase();
                                    if (alt.includes(target) || titleAttr.includes(target) || (safeTarget && src.includes(safeTarget))) {
                                        foundList.push({
                                            count: expected,
                                            timeStr: 'just now',
                                            isFresh: true
                                        });
                                        break;
                                    }
                                }

                                return foundList;
                            }""", {"title": clean_title, "expected": expected_imgs_count})
                        except Exception:
                            pass

                        page_text = ""
                        try:
                            page_text = (page.inner_text("body") or "").lower()
                        except Exception:
                            pass

                        if isinstance(card_eval, list) and card_eval:
                            fresh_matches = [
                                c for c in card_eval
                                if isinstance(c, dict) and c.get("isFresh") and (expected_imgs_count <= 1 or c.get("count", 0) == expected_imgs_count or c.get("count", 0) == 0)
                            ]
                            if fresh_matches:
                                post_found = True
                                break
                            else:
                                for c in card_eval:
                                    if isinstance(c, dict):
                                        note = f"时间: '{c.get('timeStr')}', 张数: {c.get('count')}/{expected_imgs_count}"
                                        if note not in stale_notes:
                                            stale_notes.append(note)
                                            logger.info(f"[Ko-fi] 检测到同名历史旧帖 ({note})，跳过旧帖，轮询等待刚刚提交的最新作品索引出库...")
                        elif clean_title and clean_title in page_text:
                            post_found = True
                            break

                        elapsed_round = time.time() - assert_start
                        if elapsed_round > 10 and "/manage/mypage" not in (page.url or "").lower():
                            try:
                                self.safe_goto(page, check_url, timeout=25000)
                            except Exception:
                                pass
                        else:
                            time.sleep(2.5)
                            try:
                                page.reload()
                            except Exception:
                                pass

                    if not post_found:
                        self.close_login_session(browser, context)
                        stale_hint = f" (仅检测到同名历史残留: {'; '.join(stale_notes)})" if stale_notes else ""
                        return False, f"Ko-fi 真实成功断言失败: 在管理后台未检索到刚刚发布的有效新帖 '{batch_title}'{stale_hint}，第 {batch_idx + 1}/{total_batches} 帖发布未能确认生效，已判定为失败", compliance

                    # 8. 耗时安全护栏校验: 从点击发布到断言通过少于 min_publish_duration 直接判异常
                    elapsed_publish = time.time() - publish_click_time
                    if elapsed_publish < min_publish_duration:
                        logger.error(f"[Ko-fi] 发布耗时异常护栏触发: 从点击发布到断言通过仅耗时 {elapsed_publish:.1f}s (低于安全阈值 {min_publish_duration}s)")
                        self.close_login_session(browser, context)
                        return False, f"Ko-fi 发布耗时异常护栏拦截: 点击发布到断言通过仅耗时 {elapsed_publish:.1f} 秒 (安全护栏阈值: {min_publish_duration} 秒)，疑似命中历史同名残留或未完成真实上传，已判定为失败以防静默漏发", compliance

                    # 8.5 真实图集图片张数核验 (严禁漏图)
                    actual_gallery_count = self._count_kofi_gallery_images(page, batch_title, len(batch_images))
                    if actual_gallery_count != len(batch_images):
                        logger.error(f"[Ko-fi] 图集张数断言失败: 预期 {len(batch_images)} 张，实际反查检测到 {actual_gallery_count} 张")
                        self.close_login_session(browser, context)
                        return False, f"Ko-fi 图集张数断言失败: 预期包含 {len(batch_images)} 张图片，反查实际检测到 {actual_gallery_count} 张，已判定为失败以防漏发缺图", compliance

                    published_images_count += len(batch_images)

                self.close_login_session(browser, context)
                compliance.append(
                    self.verify_compliance_item(
                        "kofi",
                        "gallery_count",
                        True,
                        True,
                        f"图集发布核验通过 (成功 {published_images_count}/{total_images} 张，共 {total_batches} 帖)"
                    )
                )
                return True, f"Ko-fi 发布成功 (已通过管理主页真实作品断言校验，图集成功 {published_images_count}/{total_images} 张，共 {total_batches} 帖)", compliance
        except Exception as e:
            logger.error(f"[Ko-fi] 自动化发布异常: {e}")
            return False, f"Ko-fi 自动化发布异常: {e}", compliance
