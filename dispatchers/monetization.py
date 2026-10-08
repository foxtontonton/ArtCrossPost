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

        has_session = any(c.get("name") in ("session_id", "patreon_device_id") for c in cookies)
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

    def login_interactive(self):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                # 统一登录上下文入口：优先本机 Edge 真实用户配置 (可复用 Google 会话，
                # 解决 Patreon "Continue with Google" 被拦截为不安全浏览器)，失败自动回退临时会话
                browser, context = self.create_login_context(p, "Patreon")
                existing_cookies = self.get_cookies()
                if existing_cookies:
                    context.add_cookies(existing_cookies)

                page = context.new_page()
                login_page = page
                self.safe_goto(page, "https://www.patreon.com/home", timeout=40000)
                try:
                    page.wait_for_selector("span[data-tag='user-name'], a[href*='/posts/new'], button[data-tag='user-profile-menu']", timeout=5000)
                except Exception as e:
                    logger.debug(f"[Patreon] 等待主页特征元素超时: {e}")

                def is_patreon_logged_in(p):
                    try:
                        url = p.url.lower()
                        if "/login" in url or "/signup" in url or "/join" in url:
                            return False
                        has_login_btn = p.query_selector("a[href*='/login'], a:has-text('Log in'), button:has-text('Log In')") is not None
                        has_user_indicator = p.query_selector("span[data-tag='user-name'], a[href*='/posts/new'], button[data-tag='user-profile-menu'], button[aria-label*='user menu' i], a[href*='/dashboard']") is not None
                        if has_user_indicator:
                            return True
                        cks = context.cookies()
                        has_session = any(c.get("name") in ("session_id", "patreon_device_id") for c in cks)
                        if has_session and not has_login_btn:
                            return True
                        return False
                    except Exception:
                        return False

                login_page = page  # 实际检测到登录态的页面（可能是 OAuth 弹窗）；必须在 if 外初始化，已登录跳过时仍需用它提取资料
                if not is_patreon_logged_in(page):
                    logger.info("[Patreon] 尚未登录，正在前往登录页面...")
                    self.safe_goto(page, self.login_url)
                    logger.info("[Patreon] 请在弹出的浏览器中完成登录...")

                    logged_in = False
                    start_time = time.time()
                    last_url_log = 0
                    while time.time() - start_time < 300:
                        if page.is_closed():
                            break
                        try:
                            # 检查上下文内所有页面（含 Google OAuth 弹窗）：任一登录成功即算成功
                            for p in context.pages:
                                try:
                                    if not p.is_closed() and is_patreon_logged_in(p):
                                        logged_in = True
                                        login_page = p
                                        if p != page:
                                            logger.info(f"[Patreon] 在新页面检测到登录态: {p.url[:80]}")
                                        break
                                except Exception:
                                    continue
                            if logged_in:
                                break
                            # 每 15 秒记录一次各页面 URL，便于定位卡死位置（如 OAuth 回调白屏）
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
                        # 收尾前对所有页面再做一次登录态确认
                        try:
                            for p in context.pages:
                                if not p.is_closed() and is_patreon_logged_in(p):
                                    logged_in = True
                                    login_page = p
                                    break
                        except Exception:
                            pass
                    if not logged_in:
                        if not page.is_closed():
                            self.close_login_session(browser, context)
                        return False, "Patreon 登录未完成或窗口已关闭"

                time.sleep(1)  # 等待登录后页面状态稳定
                # 提取用户名与头像（从实际登录的页面提取，可能是 OAuth 弹窗页）
                user_name = "Patreon 创作者"
                avatar_src = None
                try:
                    name_elem = login_page.query_selector("header span[data-tag='user-name'], nav span")
                    if name_elem:
                        user_name = name_elem.inner_text().strip() or user_name
                    avatar_elem = login_page.query_selector("header img, nav img")
                    if avatar_elem:
                        avatar_src = avatar_elem.get_attribute("src")
                    self.save_profile(username=user_name, avatar_url=avatar_src)
                except Exception as e:
                    logger.warning(f"[Patreon] 提取资料异常: {e}")

                cookies = context.cookies()
                self.save_cookies(cookies)
                if not page.is_closed():
                    self.close_login_session(browser, context)
                return True, f"登录成功，已保存 Patreon 会话与账号: {user_name}！"
        except Exception as e:
            logger.error(f"[Patreon] 登录遇到异常: {e}")
            return False, f"登录遇到异常: {e}"

    def publish(self, payload):
        """
        通过 Playwright 执行 Patreon 原生发布后台:
        - 上传无码原图
        - 设置可见性 (All Patrons / Public / Tiers)
        - 勾选官方 Adult Content (18+)
        """
        images = payload.get("raw_images", payload.get("images", [])) # 必须是无码原图
        if not images:
            return False, "Patreon 发布失败: 未选择图片", []

        title = payload.get("title", "") or "New Creation"
        desc = payload.get("description", "")
        is_adult = payload.get("is_nsfw", False)
        visibility = payload.get("platform_options", {}).get("patreon_visibility", "patrons")

        cookies = self.get_cookies()
        if not cookies:
            return False, "Patreon 未登录，请先点击登录按钮获取会话", []

        logger.info(f"[Patreon] 开始原生发布: 标题={title}, 无码原图={len(images)}张, 原生权限={visibility}, 原生Adult={is_adult}")
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
                self.apply_stealth(page)
                self.safe_goto(page, "https://www.patreon.com/posts/new", timeout=40000)
                try:
                    page.wait_for_selector("button:has-text('Images'), div[role='button']:has-text('Images')", timeout=15000)
                except Exception as e:
                    logger.debug(f"[Patreon] 等待帖子类型选择按钮超时: {e}")

                # 1. 选择发布类型为 Images
                img_post_type = page.query_selector("button:has-text('Images'), div[role='button']:has-text('Images')")
                if img_post_type:
                    img_post_type.click()
                    time.sleep(0.5)  # 等待发布类型切换就绪

                # 2. 上传无码高清原图
                file_input = page.query_selector("input[type='file']")
                if file_input:
                    file_input.set_input_files(images)
                    title_sel = "input[placeholder*='Post title'], input[name='title']"
                    att_ok, att_msg = self.assert_attached(
                        page,
                        title_sel,
                        timeout=20000,
                        error_msg="Patreon 图片上传未完成或表单未渲染，已中止发布"
                    )
                    if not att_ok:
                        self.close_login_session(browser, context)
                        return False, att_msg, compliance
                    time.sleep(1)  # 等待图片上传与表单渲染

                # 3. 填写标题与正文
                title_sel = "input[placeholder*='Post title'], input[name='title']"
                title_input = page.query_selector(title_sel)
                if title_input:
                    title_input.fill(title)
                    time.sleep(0.3)

                f_ok, f_msg = self.assert_field_value(page, title_sel, expected_value=title, max_retries=1)
                if not f_ok:
                    self.close_login_session(browser, context)
                    return False, f"Patreon 标题填写断言失败: {f_msg}", compliance

                desc_area = page.query_selector("div[contenteditable='true'], textarea")
                if desc_area:
                    desc_area.fill(desc)

                # 4. 勾选官方原生 Adult Content (18+) 开关
                adult_selector = "label:has-text('Adult content') input, input[name*='adult_content']"
                if is_adult:
                    try:
                        adult_cb = page.query_selector(adult_selector)
                        if adult_cb and not adult_cb.is_checked():
                            adult_cb.click()
                            time.sleep(0.3)  # 等待成人开关点击生效
                    except Exception as e:
                        logger.warning(f"[Patreon] 勾选 Adult Content 异常: {e}")

                    v_adult, det_adult = self.check_selector_state(page, adult_selector, expected=True)
                    compliance.append(self.verify_compliance_item("patreon", "adult_content", True, v_adult, det_adult))
                else:
                    compliance.append(self.verify_compliance_item("patreon", "adult_content", False, True, "未要求开启成人内容"))

                # 5. 设置可见范围 (Who can see this post?)
                try:
                    if visibility == "public":
                        pub_radio = page.query_selector("label:has-text('Public') input")
                        if pub_radio: pub_radio.click()
                    else:
                        pat_radio = page.query_selector("label:has-text('Patrons only') input, label:has-text('All patrons') input")
                        if pat_radio: pat_radio.click()
                    time.sleep(0.3)  # 等待受众单选生效
                except Exception as e:
                    logger.warning(f"[Patreon] 设置可见范围异常: {e}")

                # 6. 点击发布
                publish_btn = page.query_selector("button:has-text('Publish now'), button:has-text('Publish')")
                if not publish_btn:
                    self.close_login_session(browser, context)
                    return False, "Patreon 未定位到发布按钮，已保留未提交", compliance

                try:
                    publish_btn.click()
                except Exception as e:
                    publish_btn.evaluate("el => el.click()")

                sub_ok, sub_msg = self.assert_submitted(
                    page,
                    lambda p: "/posts/new" not in p.url.lower(),
                    timeout=20000,
                    error_msg="Patreon 提交未确认，作品可能已存为草稿，请前往网页确认"
                )
                self.close_login_session(browser, context)
                if not sub_ok:
                    return False, sub_msg, compliance

                return True, "Patreon 发布成功 (已对齐无码原图分发、赞助者可见及 18+ 标记)", compliance
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

        auth_cookie_names = [".AspNet.ApplicationCookie", "ASP.NET_SessionId", ".AspNetCore.Cookies", "ko-fi.auth", "user_id"]
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

        probe = self.http_probe_session("https://ko-fi.com/manage/posts/new", timeout=8)
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
            "detail": f"Ko-fi 发帖端点鉴权成功 (会话存活, HTTP {probe.get('status_code')})",
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

                page = context.new_page()
                login_page = page
                self.safe_goto(page, "https://ko-fi.com/home", timeout=40000)
                try:
                    page.wait_for_selector("a[href*='/manage'], a[href*='/settings'], div[data-testid='user-avatar']", timeout=5000)
                except Exception as e:
                    logger.debug(f"[Ko-fi] 等待主页特征元素超时: {e}")

                def is_kofi_logged_in(p):
                    try:
                        url = p.url.lower()
                        if "/account/login" in url or "/login" in url or "/signup" in url or "/register" in url:
                            return False
                        has_login_btn = p.query_selector("a[href*='/account/login'], a[href*='/login'], a:has-text('Log in'), button:has-text('Log In')") is not None
                        has_user_indicator = p.query_selector("a[href*='/manage'], a[href*='/settings'], a[href*='/account/logout'], div[data-testid='user-avatar'], a[href*='/dashboard']") is not None
                        if has_user_indicator:
                            return True
                        cks = context.cookies()
                        auth_cookie_names = [".aspnet.applicationcookie", "asp.net_sessionid", ".aspnetcore.cookies", "ko-fi.auth", "user_id"]
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
        images = payload.get("raw_images", payload.get("images", [])) # 必须是无码原图
        if not images:
            return False, "Ko-fi 发布失败: 未选择图片", []

        title = payload.get("title", "") or "New Artwork"
        desc = payload.get("description", "")
        visibility = payload.get("platform_options", {}).get("kofi_visibility", "supporters")
        is_adult = payload.get("is_nsfw", False)

        cookies = self.get_cookies()
        if not cookies:
            return False, "Ko-fi 未登录，请先点击登录按钮获取会话", []

        logger.info(f"[Ko-fi] 开始原生发布: 标题={title}, 无码原图={len(images)}张, 原生权限={visibility}")
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
                self.safe_goto(page, "https://ko-fi.com/manage/posts/new", timeout=40000)
                try:
                    page.wait_for_selector("button:has-text('Image'), div:has-text('Image Post')", timeout=15000)
                except Exception as e:
                    logger.debug(f"[Ko-fi] 等待帖子类型选择按钮超时: {e}")

                # 1. 选择 Image 帖子类型
                img_tab = page.query_selector("button:has-text('Image'), div:has-text('Image Post')")
                if img_tab:
                    img_tab.click()
                    time.sleep(0.5)  # 等待发布类型切换

                # 2. 上传无码原图
                file_input = page.query_selector("input[type='file']")
                if file_input:
                    file_input.set_input_files(images)
                    title_sel = "input[placeholder*='Title'], input[name='title']"
                    att_ok, att_msg = self.assert_attached(
                        page,
                        title_sel,
                        timeout=20000,
                        error_msg="Ko-fi 图片上传未完成或标题框未渲染，已中止发布"
                    )
                    if not att_ok:
                        self.close_login_session(browser, context)
                        return False, att_msg, compliance
                    time.sleep(1)  # 等待图片上传与表单渲染

                # 3. 填写标题与描述
                title_sel = "input[placeholder*='Title'], input[name='title']"
                title_input = page.query_selector(title_sel)
                if title_input:
                    title_input.fill(title)
                    time.sleep(0.3)

                f_ok, f_msg = self.assert_field_value(page, title_sel, expected_value=title, max_retries=1)
                if not f_ok:
                    self.close_login_session(browser, context)
                    return False, f"Ko-fi 标题填写断言失败: {f_msg}", compliance

                desc_area = page.query_selector("div[contenteditable='true'], textarea")
                if desc_area:
                    desc_area.fill(desc)

                # 4. 设置 Audience
                try:
                    if visibility == "everyone":
                        every_btn = page.query_selector("label:has-text('Everyone') input, button:has-text('Everyone')")
                        if every_btn: every_btn.click()
                    else:
                        supp_btn = page.query_selector("label:has-text('Supporters') input, button:has-text('Supporters Only')")
                        if supp_btn: supp_btn.click()
                    time.sleep(0.3)  # 等待受众单选生效
                except Exception as e:
                    logger.warning(f"[Ko-fi] 设置受众异常: {e}")

                # 5. 发布
                publish_btn = page.query_selector("button:has-text('Publish'), button[type='submit']")
                if not publish_btn:
                    self.close_login_session(browser, context)
                    return False, "Ko-fi 未定位到发布按钮，已保留未提交", compliance

                try:
                    publish_btn.click()
                except Exception as e:
                    publish_btn.evaluate("el => el.click()")

                sub_ok, sub_msg = self.assert_submitted(
                    page,
                    lambda p: "/manage/posts/new" not in p.url.lower(),
                    timeout=20000,
                    error_msg="Ko-fi 提交未确认，作品可能已存为草稿，请前往网页确认"
                )
                self.close_login_session(browser, context)
                if not sub_ok:
                    return False, sub_msg, compliance

                return True, "Ko-fi 发布成功 (已对齐无码原图分发与 Supporters Only 受众权限)", compliance
        except Exception as e:
            logger.error(f"[Ko-fi] 自动化发布异常: {e}")
            return False, f"Ko-fi 自动化发布异常: {e}", compliance
