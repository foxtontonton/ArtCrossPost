# -*- coding: utf-8 -*-
"""
ArtCrossPost - DeviantArt Dispatcher (对齐 DA 投稿后台原生设置)
页面地址: https://www.deviantart.com/submit
原生字段对齐:
1. Created using AI tools: 官方专用 AI 勾选框 (必勾防封)
2. Mature Content: 成人分级开关 -> Moderate / Strict -> Nudity / Sexual themes
3. 原生图片上传与标题/描述填充
"""
import os
import time
from .base import BaseDispatcher
try:
    from logger import get_logger
except ImportError:
    from ..logger import get_logger

logger = get_logger("DeviantArt")

class DeviantArtDispatcher(BaseDispatcher):
    def __init__(self, cookies_dir):
        super().__init__(
            name="deviantart",
            display_name="DeviantArt",
            login_url="https://www.deviantart.com/users/login",
            cookies_dir=cookies_dir
        )

    def _extract_userinfo(self, cookie_val):
        """从 userinfo cookie 中解析出 username 和 usericon"""
        import urllib.parse
        import json
        val = urllib.parse.unquote(cookie_val or "")
        username = None
        usericon = None
        if "{" in val and "}" in val:
            try:
                json_part = val[val.find("{"):val.rfind("}")+1]
                data = json.loads(json_part)
                if isinstance(data, dict):
                    username = data.get("username", "").strip() or None
                    usericon = data.get("usericon", "").strip() or data.get("avatar", "").strip() or data.get("avatar_url", "").strip() or None
            except Exception as e:
                logger.debug(f"[DeviantArt] 解析 userinfo cookie 异常: {e}")
        return username, usericon

    def probe_login(self) -> dict:
        """
        DeviantArt 登录态有效性深度探针 (需求一 1.1)
        校验本地 Cookies 是否包含 auth_secure / auth 鉴权令牌与 userinfo，并向服务端进行轻量探针
        """
        cookies = self.get_cookies()
        if not cookies:
            return {
                "platform": "deviantart",
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": "DeviantArt 本地无 Cookie 记录 (未登录)",
                "suggestion": "请点击 DeviantArt 卡片登录按钮完成登录"
            }

        has_auth = any(c.get("name") in ("auth_secure", "auth") and c.get("value") for c in cookies)
        username = None
        for c in cookies:
            if c.get("name") == "userinfo":
                un, _ = self._extract_userinfo(c.get("value", ""))
                if un:
                    username = un
                    break

        if not has_auth and not username:
            return {
                "platform": "deviantart",
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": "DeviantArt Cookie 中缺少有效认证凭证 auth_secure",
                "suggestion": "请点击 DeviantArt 卡片登录按钮重新登录"
            }

        display_user = f"@{username}" if username else "Auth 就绪"
        probe = self.http_probe_session("https://www.deviantart.com/", timeout=8)
        if probe.get("error"):
            return {
                "platform": "deviantart",
                "category": "login",
                "item": "login_probe",
                "status": "pass",
                "detail": f"DeviantArt 凭据有效 ({display_user})，网络探测轻微超时: {probe['error']}",
                "suggestion": ""
            }

        final_url = probe.get("url", "").lower()
        if "/users/login" in final_url:
            return {
                "platform": "deviantart",
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": "DeviantArt 服务端已过期 (重定向至登录页)",
                "suggestion": "请点击登录按钮重新获取会话"
            }

        return {
            "platform": "deviantart",
            "category": "login",
            "item": "login_probe",
            "status": "pass",
            "detail": f"DeviantArt 会话有效 ({display_user}, HTTP {probe.get('status_code')}, 共 {len(cookies)} 项 Cookie)",
            "suggestion": ""
        }

    def check_login(self):
        cookies = self.get_cookies()
        if not cookies:
            return False, "未登录 (无 Cookie 记录)"
        probe = self.probe_login()
        if probe.get("status") == "pass":
            return True, probe.get("detail")
        return False, probe.get("detail")

    def get_selector_manifest(self) -> dict:
        return {
            "upload_input": "input[type='file']",
            "title_input": "input[placeholder*='title' i], input[name='title']",
            "ai_checkbox": "input[name*='is_ai' i], label:has-text('Created using AI tools') input, label:has-text('Created using AI tools')",
            "mature_checkbox": "label:has-text('Mature Content') input, div:has-text('Mature Content') input, label:has-text('Mature Content')",
            "submit_button": "button:has-text('Submit'), button[type='submit']"
        }

    def check_selectors_health(self) -> list:
        """只读 DOM 检查 DeviantArt 投稿页面上传入口健康度"""
        cookies = self.get_cookies()
        if not cookies:
            return [{
                "platform": "deviantart",
                "category": "selector",
                "item": "selectors_all",
                "status": "unknown",
                "detail": "DeviantArt 未登录，跳过投稿页选择器体检",
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
                self.safe_goto(page, "https://www.deviantart.com", timeout=25000)
                self.safe_goto(page, "https://www.deviantart.com/submit", timeout=35000)
                try:
                    page.wait_for_selector("input[type='file']", timeout=15000)
                except Exception as e:
                    logger.debug(f"[DA] 投稿页上传入口等待超时，体检将标记为 unknown: {e}")

                manifest = self.get_selector_manifest()
                upload_el = page.query_selector(manifest["upload_input"])
                results.append({
                    "platform": "deviantart",
                    "category": "selector",
                    "item": "upload_input",
                    "status": "pass" if upload_el else "unknown",
                    "detail": f"选择器命中: {upload_el is not None} (文件上传入口)",
                    "suggestion": "如页面结构更新请检查 submit 页面上传控件" if not upload_el else ""
                })

                for item_k, sel in [("title_input", manifest["title_input"]), ("ai_checkbox", manifest["ai_checkbox"]), ("mature_checkbox", manifest["mature_checkbox"]), ("submit_button", manifest["submit_button"])]:
                    el = page.query_selector(sel)
                    is_hit = (el is not None)
                    results.append({
                        "platform": "deviantart",
                        "category": "selector",
                        "item": item_k,
                        "status": "pass" if is_hit else "unknown",
                        "detail": f"选择器 `{sel}` 探测: {'命中' if is_hit else '未展开(只读体检未附加素材)'}",
                        "suggestion": "" if is_hit else "DeviantArt 表单项将在素材上传后动态挂载"
                    })
                self.close_login_session(browser, context)
        except Exception as e:
            results.append({
                "platform": "deviantart",
                "category": "selector",
                "item": "page_load",
                "status": "unknown",
                "detail": f"DeviantArt 投稿页访问异常: {e}",
                "suggestion": "海外站点请检查网络代理连接"
            })
        return results

    def get_profile(self):
        is_ok, _ = self.check_login()
        if not is_ok:
            return {
                "username": "未登录",
                "avatar": None,
                "logged_in": False
            }
        return super().get_profile()

    def login_interactive(self):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                # 统一登录上下文入口：优先本机 Edge 真实用户配置 (可复用 Google 会话，
                # 解决 "此浏览器或应用可能不安全" 拦截)，失败自动回退临时会话
                browser, context = self.create_login_context(p, "DeviantArt")
                existing_cookies = self.get_cookies()
                if existing_cookies:
                    context.add_cookies(existing_cookies)

                self.apply_stealth(context)
                context.on("page", lambda new_p: self.apply_stealth(new_p))

                # 复用已有主标签页，彻底清理启动时的冗余空白页
                page = context.pages[0] if context.pages else context.new_page()
                self.clean_context_tabs(context)
                self.apply_stealth(page)

                def is_da_logged_in(p, ctx):
                    # 1. 检查 Cookie 中是否包含已登录凭证
                    for c in ctx.cookies():
                        if c.get("name") in ("auth_secure", "auth"):
                            return True
                        if c.get("name") == "userinfo":
                            un, _ = self._extract_userinfo(c.get("value", ""))
                            if un:
                                return True
                    # 2. 检查 DOM 中是否出现了已登录特征 (仅在 DeviantArt 页面且非登录页检查，绝不干扰 Google OAuth 弹窗)
                    if p and not p.is_closed():
                        try:
                            url = p.url.lower()
                            if "deviantart.com" in url and "/users/login" not in url and "about:blank" not in url:
                                user_el = p.query_selector("header a[data-hook='user_link']:not([href*='/login']), button[data-hook='user_dropdown'], a[href*='/submit']:not([href*='/login']), header button[aria-label*='User']")
                                if user_el:
                                    t = user_el.inner_text().strip()
                                    if t and "log in" not in t.lower() and "join" not in t.lower():
                                        return True
                        except Exception as e:
                            logger.debug(f"[DeviantArt] 检查登录状态 DOM 异常: {e}")
                    return False

                # 直接导航至登录页面（使用 safe_goto，容忍 WAF 拦截与重定向，彻底杜绝 ERR_ABORTED）
                logger.info("[DeviantArt] 正在打开登录页面...")
                self.safe_goto(page, self.login_url, timeout=45000)
                logger.info("[DeviantArt] 请在弹出的浏览器中完成登录 (支持账号密码、Google等)...")

                # 循环等待用户登录完成，最长等待 5 分钟 (300秒)
                logged_in = False
                start_time = time.time()
                while time.time() - start_time < 300:
                    active_pages = [pg for pg in context.pages if not pg.is_closed()]
                    if not active_pages:
                        break

                    try:
                        for pg in active_pages:
                            if is_da_logged_in(pg, context):
                                logged_in = True
                                break
                        if logged_in:
                            break
                    except Exception as e:
                        logger.debug(f"[DeviantArt] 轮询等待登录异常: {e}")
                    time.sleep(1.5)  # 登录轮询间隔，避免高频查询触发平台风控

                # 确保主页面引用为 DeviantArt 页面而不是已关闭的 OAuth 弹窗
                for pg in context.pages:
                    if not pg.is_closed() and "deviantart.com" in pg.url.lower():
                        page = pg
                        break

                if not logged_in and not is_da_logged_in(page, context):
                    self.close_login_session(browser, context)
                    return False, "DeviantArt 登录未完成或窗口已关闭"

                time.sleep(1)  # 等待登录后页面状态稳定
                # 提取用户名与头像
                user_name = "DeviantArt 艺术家"
                avatar_src = None

                # 1. 优先从 userinfo cookie 中解析
                for c in context.cookies():
                    if c.get("name") == "userinfo":
                        un, icon = self._extract_userinfo(c.get("value", ""))
                        if un:
                            user_name = un
                        if icon:
                            avatar_src = icon
                        if un and icon:
                            break

                # 2. 从所有活跃页面的 DOM 中提取用户名和头像
                for pg in context.pages:
                    if pg.is_closed():
                        continue
                    try:
                        if user_name == "DeviantArt 艺术家":
                            name_elem = pg.query_selector("header a[data-hook='user_link']:not([href*='/login']), a[data-hook='user_link']:not([href*='/login'])")
                            if name_elem and name_elem.inner_text().strip():
                                user_name = name_elem.inner_text().strip()

                        if not avatar_src:
                            avatar_elem = pg.query_selector("header img[alt*='avatar'], img[data-hook='user_avatar'], header button[data-hook='user_dropdown'] img, header a[data-hook='user_link'] img, header button[aria-label*='User'] img")
                            if avatar_elem:
                                avatar_src = avatar_elem.get_attribute("src")
                    except Exception as e:
                        logger.debug(f"[DeviantArt] 提取活跃页面个人资料异常: {e}")

                # 3. 兜底根据用户名构造官方头像 URL
                if not avatar_src and user_name and user_name != "DeviantArt 艺术家":
                    u = user_name.lower()
                    if len(u) >= 2:
                        avatar_src = f"https://a.deviantart.net/avatars/{u[0]}/{u[1]}/{u}.png"

                # 4. 尝试通过 Playwright 上下文下载头像（带 Cookie 和 Edge 请求头，彻底避免 CDN 防盗链 403）
                avatar_bytes = None
                if avatar_src:
                    try:
                        res = context.request.get(avatar_src, timeout=10000)
                        if res.ok:
                            avatar_bytes = res.body()
                    except Exception as e:
                        logger.warning(f"[DeviantArt] Playwright 抓取头像异常: {e}")

                self.save_profile(username=user_name, avatar_url=avatar_src if not avatar_bytes else None, avatar_bytes=avatar_bytes)

                cookies = context.cookies()
                self.save_cookies(cookies)
                if not page.is_closed():
                    self.close_login_session(browser, context)
                return True, f"登录成功，已同步 DeviantArt 账号: {user_name}！"
        except Exception as e:
            logger.error(f"[DeviantArt] 登录遇到异常: {e}")
            return False, f"登录遇到异常: {e}"

    def publish(self, payload):
        """
        通过 Playwright 执行 DeviantArt 原生发布后台操作
        """
        images = payload.get("da_images") or payload.get("images", [])
        if not images:
            return False, "DeviantArt 发布失败: 未选择图片", []

        title = payload.get("title", "") or "Untitled"
        desc = payload.get("description", "")
        _tags = payload.get("tags", [])
        da_mature_opt = payload.get("platform_options", {}).get("deviantart_mature", "strict" if payload.get("is_nsfw", False) else "none")
        is_mature = (da_mature_opt != "none")
        mature_level = "moderate" if da_mature_opt == "moderate" else "strict"
        is_ai = payload.get("platform_options", {}).get("deviantart_ai", payload.get("platform_options", {}).get("is_ai_generated", payload.get("is_ai", False)))

        cookies = self.get_cookies()
        if not cookies:
            return False, "DeviantArt 未登录，请先点击登录按钮获取会话", []

        logger.info(f"[DeviantArt] 开始原生投稿: 标题={title}, 原生AI开关={is_ai}, 原生Mature={is_mature} ({mature_level})")
        compliance = []

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                profile_dir = self.get_app_browser_profile_dir()
                if os.path.isdir(profile_dir):
                    logger.info(f"[DeviantArt] 使用独立持久化环境启动 ({profile_dir})...")
                    context = self.create_persistent_stealth_context(p, profile_dir, headless=True)
                    browser = None
                else:
                    browser = self.create_stealth_browser(p, headless=True)
                    context = browser.new_context()
                    context.add_cookies(cookies)

                page = context.pages[0] if context.pages else context.new_page()
                self.apply_stealth(page)

                # 1. 访问主站以建立 CloudFront / WAF 会话，彻底杜绝 403 拦截
                self.safe_goto(page, "https://www.deviantart.com", timeout=30000)
                page.wait_for_timeout(1500)

                # 2. 直达投稿入口
                self.safe_goto(page, "https://www.deviantart.com/submit", timeout=45000)
                try:
                    page.wait_for_selector("input[type='file']", timeout=25000)
                except Exception as e:
                    logger.debug(f"[DeviantArt] 等待上传文件控件超时: {e}")

                all_fis = page.query_selector_all("input[type='file']")
                if not all_fis:
                    self.close_login_session(browser, context)
                    return False, "DeviantArt 投稿页未能定位到文件上传控件 (请检查网络代理或重新登录)", []

                # 3. 按素材格式智能分发 (动图 GIF 匹配 accept='image/gif'，静态图匹配 accept='image/png|jpg')
                is_gif = images[0].lower().endswith(".gif")
                target_fi = None
                for fi in all_fis:
                    acc = (fi.get_attribute("accept") or "").lower()
                    if is_gif and "gif" in acc:
                        target_fi = fi
                        break
                    elif not is_gif and ("png" in acc or "jpg" in acc or "jpeg" in acc):
                        target_fi = fi
                        break

                if not target_fi:
                    for fi in all_fis:
                        acc = (fi.get_attribute("accept") or "").lower()
                        if "image" in acc:
                            target_fi = fi
                            break
                if not target_fi:
                    target_fi = all_fis[-1]

                target_fi.set_input_files(images[0])
                logger.info(f"[DeviantArt] 文件已提交 ({'GIF动图' if is_gif else '静态图片'})，等待上传处理与 Studio 弹窗渲染...")

                # 4. 等待上传与表单渲染 (大动图有羊驼动画，放宽等待时间至 75 秒)
                title_sel = "input[name='title'], input[placeholder*='Add your title here'], div[role='dialog'] input[type='text']:not([placeholder*='Search'])"
                att_ok, att_msg = self.assert_attached(
                    page,
                    title_sel,
                    timeout=75000,
                    error_msg="DeviantArt 素材上传超时或 Studio 模态框未渲染，已中止发布 (避免存为无图草稿)"
                )
                if not att_ok:
                    self.close_login_session(browser, context)
                    return False, att_msg, compliance

                page.wait_for_timeout(1000)

                # 5. 填写标题 (彻底覆盖默认填充的文件名，如 Public Send 0)
                title_input = page.query_selector(title_sel)
                if title_input:
                    title_input.click()
                    title_input.fill(title)
                    page.wait_for_timeout(400)
                    logger.info(f"[DeviantArt] 标题已填入: {title}")

                f_ok, f_msg = self.assert_field_value(page, title_sel, expected_value=title, max_retries=1)
                if not f_ok:
                    self.close_login_session(browser, context)
                    return False, f"DeviantArt 标题断言失败: {f_msg}", compliance

                # 6. 填写描述
                desc_area = page.query_selector(
                    "div.tiptap[contenteditable='true'], div[role='textbox'], textarea[name='description'], div[contenteditable='true']"
                )
                if desc_area and desc:
                    desc_area.click()
                    desc_area.fill(desc)
                    page.wait_for_timeout(300)

                # 7. 勾选官方专用 AI 创作标识: "Created using AI tools"
                ai_selector = "input[type='checkbox'][name='isAiGenerated'], input[name='isAiGenerated'], label:has-text('Created using AI tools') input"
                try:
                    ai_checkbox = page.query_selector(ai_selector)
                    if not ai_checkbox:
                        ai_checkbox = page.query_selector("label:has-text('Created using AI tools')")
                    if ai_checkbox:
                        is_chk = ai_checkbox.is_checked() if hasattr(ai_checkbox, "is_checked") else False
                        if is_ai and not is_chk:
                            ai_checkbox.evaluate("el => el.click()")
                        elif not is_ai and is_chk:
                            ai_checkbox.evaluate("el => el.click()")
                        page.wait_for_timeout(300)
                except Exception as e:
                    logger.warning(f"[DeviantArt] 勾选 Created using AI tools 异常: {e}")

                # 回读验证 AI 标识
                v_res, v_det = self.check_selector_state(page, ai_selector, expected=is_ai)
                compliance.append(self.verify_compliance_item("deviantart", "ai_generated", is_ai, v_res if is_ai else True, v_det))

                # 8. 勾选官方 Mature Content 成人内容设置 (对齐 DA 原生表单规范，补全二级分类必选)
                def is_element_checked(el):
                    try:
                        aria_chk = el.get_attribute("aria-checked")
                        if aria_chk == "true":
                            return True
                        if aria_chk == "false":
                            return False
                        tag = (el.evaluate("e => e.tagName") or "").lower()
                        if tag == "input":
                            return el.is_checked()
                        return el.evaluate("""e => {
                            if (e.getAttribute('aria-checked') === 'true') return true;
                            if (e.classList.contains('active') || e.classList.contains('selected') || e.classList.contains('checked')) return true;
                            const inp = e.querySelector('input');
                            if (inp && inp.checked) return true;
                            return false;
                        }""")
                    except Exception:
                        return False

                def click_safely(el):
                    try:
                        el.scroll_into_view_if_needed()
                    except Exception:
                        pass
                    try:
                        el.click()
                    except Exception:
                        el.evaluate("e => e.click()")

                mature_selector = ".ReactModal__Content input[name='matureContent'], input[name='matureContent']"
                try:
                    mature_elem = page.query_selector(mature_selector)
                    if not mature_elem:
                        mature_elem = page.query_selector("label:has-text('Mature'), button:has-text('Mature')")
                    if mature_elem:
                        is_chk = is_element_checked(mature_elem)
                        if is_mature and not is_chk:
                            page.evaluate("() => { const el = document.querySelector(\".ReactModal__Content input[name='matureContent']\") || document.querySelector(\"input[name='matureContent']\"); if (el) el.click(); }")
                        elif not is_mature and is_chk:
                            page.evaluate("() => { const el = document.querySelector(\".ReactModal__Content input[name='matureContent']\") || document.querySelector(\"input[name='matureContent']\"); if (el) el.click(); }")
                        page.wait_for_timeout(300)
                    logger.info(f"[DeviantArt] Mature 内容设置完成: 开启={is_mature}")
                except Exception as e:
                    logger.warning(f"[DeviantArt] 设置 Mature Content 异常: {e}")

                # 回读验证 Mature 状态
                v_mat_res, v_mat_det = self.check_selector_state(page, mature_selector, expected=is_mature)
                compliance.append(self.verify_compliance_item("deviantart", "mature_content", is_mature, v_mat_res if is_mature else True, v_mat_det))

                # 8.5 相册选择 (Gallery Selection: 为空跳过，非空时在 Submit 前设置相册)
                gallery = payload.get("platform_options", {}).get("deviantart_gallery", "").strip()
                if gallery:
                    logger.info(f"[DeviantArt] 正在定位相册选择器并选择相册: '{gallery}'...")
                    gallery_matched = False
                    try:
                        # 1. <select> 模糊匹配与选择
                        select_els = page.query_selector_all(".ReactModal__Content select, form select, select")
                        for s_el in select_els:
                            try:
                                options = s_el.query_selector_all("option")
                                opt_map = {}
                                for opt in options:
                                    t = (opt.inner_text() or "").strip()
                                    v = opt.get_attribute("value") or t
                                    if t:
                                        opt_map[t] = v
                                matched_val = None
                                for t, v in opt_map.items():
                                    if gallery.lower() in t.lower() or t.lower() in gallery.lower():
                                        matched_val = v
                                        break
                                if matched_val is not None:
                                    s_el.select_option(value=matched_val)
                                    gallery_matched = True
                                    logger.info(f"[DeviantArt] 通过 <select> 成功匹配并选择相册: '{gallery}'")
                                    break
                            except Exception as e_sel:
                                logger.debug(f"[DeviantArt] <select> 匹配尝试异常: {e_sel}")

                        # 2. 按钮/弹窗式选择器定位与匹配
                        if not gallery_matched:
                            gallery_triggers = [
                                ".ReactModal__Content button:has-text('Gallery')",
                                ".ReactModal__Content button:has-text('Galleries')",
                                "button:has-text('Add to Gallery')",
                                "button:has-text('Choose gallery')",
                                "button:has-text('Featured')",
                                "[data-testid*='gallery' i]",
                                "[aria-label*='gallery' i]",
                                "div[role='combobox']:has-text('Gallery')",
                                ".ReactModal__Content div[role='button']:has-text('Gallery')"
                            ]
                            for g_sel in gallery_triggers:
                                g_btn = page.query_selector(g_sel)
                                if g_btn and g_btn.is_visible():
                                    try:
                                        click_safely(g_btn)
                                        page.wait_for_timeout(600)
                                        item_sels = [
                                            f"[role='option']:has-text('{gallery}')",
                                            f"[role='menuitem']:has-text('{gallery}')",
                                            f"[role='checkbox']:has-text('{gallery}')",
                                            f"li:has-text('{gallery}')",
                                            f"button:has-text('{gallery}')",
                                            f"label:has-text('{gallery}')",
                                            f"div:has-text('{gallery}')"
                                        ]
                                        for it_sel in item_sels:
                                            items = page.query_selector_all(it_sel)
                                            for it in reversed(items):
                                                it_txt = (it.inner_text() or "").strip()
                                                if gallery.lower() in it_txt.lower():
                                                    click_safely(it)
                                                    page.wait_for_timeout(400)
                                                    gallery_matched = True
                                                    logger.info(f"[DeviantArt] 通过列表项成功匹配并点击相册: '{gallery}'")
                                                    break
                                            if gallery_matched:
                                                break

                                        # 检查并点击确认/完成按钮 (Done / Select)
                                        confirm_btn = page.query_selector(".ReactModal__Content button:has-text('Done'), button:has-text('Done'), button:has-text('Select')")
                                        if confirm_btn and confirm_btn.is_visible():
                                            click_safely(confirm_btn)
                                            page.wait_for_timeout(300)

                                        if gallery_matched:
                                            break
                                    except Exception as e_click:
                                        logger.debug(f"[DeviantArt] 尝试相册触发器 {g_sel} 异常: {e_click}")

                        if not gallery_matched:
                            logger.error(f"[DeviantArt] 未找到指定相册 '{gallery}'，已阻断发布以防止发布到错误相册")
                            self.close_login_session(browser, context)
                            return False, f"DeviantArt 指定相册 '{gallery}' 未能匹配或定位，已阻断发布以防止作品发布到错误相册", compliance
                        else:
                            # 3. 选择后回读确认选中项文本 (严格阻断式回读校验)
                            confirmed = False
                            for _ in range(6):
                                page.wait_for_timeout(500)
                                try:
                                    confirmed = page.evaluate("""(g) => {
                                        const modal = document.querySelector('.ReactModal__Content') || document.body;
                                        return modal.innerText.toLowerCase().includes(g.toLowerCase());
                                    }""", gallery)
                                    if confirmed:
                                        break
                                except Exception:
                                    pass
                            if confirmed:
                                logger.info(f"[DeviantArt] 回读确认相册选定成功: '{gallery}'")
                            else:
                                logger.error(f"[DeviantArt] 回读未能确认相册 '{gallery}' 文本，已阻断发布以防止错位")
                                self.close_login_session(browser, context)
                                return False, f"DeviantArt 相册回读校验失败: 表单中未能确认选定相册 '{gallery}' 文本，已阻断发布以防止作品发布到错误相册", compliance
                    except Exception as e_gallery:
                        logger.error(f"[DeviantArt] 设置相册 '{gallery}' 过程发生异常: {e_gallery}")
                        self.close_login_session(browser, context)
                        return False, f"DeviantArt 设置相册 '{gallery}' 异常: {e_gallery}，已阻断发布以防止作品发布到错误相册", compliance

                # 9. 提交发布 (精准定位 Submit 按钮，严禁误触 Save to Studio / Stash / Draft)
                logger.info("[DeviantArt] 正在定位正式发布按钮 (Submit)...")
                submit_btn = None
                candidate_sels = [
                    ".ReactModal__Content button:text-is('Submit')",
                    "button:text-is('Submit')",
                    ".ReactModal__Content button:has-text('Submit'):not(:has-text('Studio')):not(:has-text('Group')):not(:has-text('subscriber')):not(:has-text('Gallery'))",
                    "button:has-text('Submit Now')",
                    "button:has-text('Submit')"
                ]
                for sel in candidate_sels:
                    btns = page.query_selector_all(sel)
                    for b in btns:
                        txt = b.inner_text().strip().lower()
                        # 严禁误触存草稿或取消
                        if any(kw in txt for kw in ["stash", "draft", "cancel", "save", "studio", "group", "gallery", "subscriber"]):
                            continue
                        if "submit" in txt:
                            submit_btn = b
                            break
                    if submit_btn:
                        break

                if not submit_btn:
                    self.close_login_session(browser, context)
                    return False, "DeviantArt 未定位到发布提交按钮 (Submit)，已保留在草稿中未发布", compliance

                # 必须轮询等待按钮解禁 (放宽至 60 秒等待大文件在 DA 端完全转码就绪)
                btn_ready = False
                wait_btn_start = time.time()
                while time.time() - wait_btn_start < 60:
                    if submit_btn.is_enabled() and submit_btn.get_attribute("aria-disabled") != "true":
                        btn_ready = True
                        break
                    elapsed = int(time.time() - wait_btn_start)
                    if elapsed > 0 and elapsed % 10 == 0:
                        logger.info(f"[DeviantArt] 正在等待素材转码完成与发布按钮激活 ({elapsed}s)...")
                    time.sleep(1.0)

                if not btn_ready:
                    # 采集页面是否有未满足的必填校验错误
                    err_hint = ""
                    err_elem = page.query_selector("div[role='alert'], span.error, p[class*='error'], div[class*='error-message'], span[data-error]")
                    if err_elem:
                        err_hint = f" (页面提示: {err_elem.inner_text().strip()})"
                    self.close_login_session(browser, context)
                    return False, f"DeviantArt 发布按钮处于禁用状态无法提交{err_hint}，已中止以防存为半成品草稿", compliance

                # 保险二：时序后置确认与二次强行回填真实作品标题 (彻底覆盖 DA 转码完成时用文件名冲掉的标题)
                try:
                    import json as _json
                    page.evaluate(f"""() => {{
                        const el = document.querySelector(".ReactModal__Content input[name='title']") || document.querySelector("{title_sel}");
                        if (el) {{
                            el.value = {_json.dumps(title)};
                            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                    }}""")
                    logger.info(f"[DeviantArt] 提交前二次强行回填真实标题完成: {title}")
                except Exception as te:
                    logger.warning(f"[DeviantArt] 提交前二次回填标题提示: {te}")

                logger.info("[DeviantArt] 发布按钮就绪，正在点击提交正式发布...")
                # 穿透点击优先，规避 ReactModal__Overlay 拦截指针事件
                try:
                    submit_btn.evaluate("el => el.click()")
                except Exception as e:
                    logger.debug(f"[DeviantArt] evaluate 点击异常，尝试普通点击: {e}")
                    submit_btn.click()

                # 点击后等待 1 秒并检查是否有立即弹出的表单错误提示 (如分类未选或受限)
                time.sleep(1.0)
                err_box = page.query_selector("div[role='alert'], span.error, p[class*='error'], div[class*='error-message'], span[data-error]")
                if err_box:
                    err_txt = (err_box.inner_text() or "").strip()
                    if err_txt:
                        logger.warning(f"[DeviantArt] 点击提交后捕获到表单拦截提示: {err_txt}")

                def is_da_submitted(p):
                    try:
                        curr_u = p.url.lower()
                        # 核心成功判定 1: 页面 URL 成功跳转至正式作品展示页 (/art/ 或 /deviation/)
                        if "/art/" in curr_u or "/deviation/" in curr_u:
                            return True
                        # 核心成功判定 2: 官方发布成功提示标识 (Toast 或 成功展示页)
                        success_indicator = p.query_selector(
                            "div:has-text('Your deviation has been submitted'), div:has-text('Published'), h1:has-text('Success'), div[data-testid='toast']:has-text('submitted')"
                        )
                        if success_indicator:
                            return True
                    except Exception as e_chk:
                        logger.debug(f"[DeviantArt] 提交状态探测异常 (通常处于页面跳转瞬态): {e_chk}")
                        try:
                            curr_u = p.url.lower()
                            if "/art/" in curr_u or "/deviation/" in curr_u:
                                return True
                        except Exception:
                            pass
                    return False

                # 阶段 1：首轮提交后 8 秒监测。若未离开 /submit 则触发自愈补全与二次挽救提交
                logger.info("[DeviantArt] 正在等待提交响应并跳转至作品页 (/art/)...")
                first_submit_passed = False
                for _ in range(8):
                    time.sleep(1.0)
                    if is_da_submitted(page):
                        first_submit_passed = True
                        break

                if not first_submit_passed:
                    err_box = page.query_selector("div[role='alert'], span.error, p[class*='error'], div[class*='error-message'], span[data-error]")
                    if err_box:
                        logger.warning(f"[DeviantArt] 首次点击未完成跳转，页面显示提示: {err_box.inner_text().strip()}")
                    logger.warning("[DeviantArt] 首次点击 8 秒内未离开投稿页或跳转作品页，触发自动二次补全与挽救提交...")
                    # 1. 确保 Mature Content 保持选中
                    if is_mature:
                        try:
                            page.evaluate("() => { const el = document.querySelector(\".ReactModal__Content input[name='matureContent']\"); if (el && !el.checked) el.click(); }")
                        except Exception:
                            pass

                    # 2. 重新强制回填标题
                    try:
                        import json as _json
                        page.evaluate(f"""() => {{
                            const el = document.querySelector(".ReactModal__Content input[name='title']") || document.querySelector("{title_sel}");
                            if (el) {{
                                el.value = {_json.dumps(title)};
                                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }}
                        }}""")
                        logger.info(f"[DeviantArt] 自愈阶段已强制回填标题: {title}")
                    except Exception as te:
                        logger.debug(f"[DeviantArt] 自愈阶段标题回填提示: {te}")

                    # 3. 重新穿透点击 Submit 按钮
                    re_submit_btn = None
                    for sel in candidate_sels:
                        btns = page.query_selector_all(sel)
                        for b in btns:
                            txt = b.inner_text().strip().lower()
                            if any(kw in txt for kw in ["stash", "draft", "cancel", "save", "studio", "group", "gallery", "subscriber"]):
                                continue
                            if "submit" in txt:
                                re_submit_btn = b
                                break
                        if re_submit_btn:
                            break

                    if re_submit_btn:
                        try:
                            re_submit_btn.evaluate("el => el.click()")
                        except Exception as e:
                            logger.debug(f"[DeviantArt] 自愈阶段点击异常: {e}")
                        logger.info("[DeviantArt] 自愈补全完毕，已执行第二次挽救提交！")

                logger.info("[DeviantArt] 等待发布结果确认 (等待跳转至 /art/ 作品页或官方提示)...")
                sub_ok, sub_msg = self.assert_submitted(
                    page,
                    is_da_submitted,
                    timeout=45000,
                    error_msg="DeviantArt 提交超时未跳转至作品展示页 (/art/)，作品可能仅存为草稿箱，请前往网页确认发布"
                )

                if not sub_ok:
                    err_detail = ""
                    try:
                        err_elem = page.query_selector("div[role='alert'], span.error, p[class*='error'], div[class*='error-message'], span[data-error]")
                        if err_elem:
                            err_detail = f" (页面错误拦截: {err_elem.inner_text().strip()})"
                    except Exception:
                        pass
                    self.close_login_session(browser, context, page)
                    return False, f"{sub_msg}{err_detail}", compliance

                # 提交成功！留足 10 秒缓冲时间给 DA 后端进行转码与草稿箱异步销毁
                logger.info("[DeviantArt] 投稿主页已确认跳转作品页！等待 10 秒供 DA 后台完成数据库索引与草稿销毁...")
                time.sleep(10)

                # 作品展示页实际相册反查 (若指定相册，反向核验页面文本)
                if gallery and ("/art/" in page.url.lower() or "/deviation/" in page.url.lower()):
                    try:
                        dev_text = (page.inner_text("body") or "").lower()
                        if gallery.lower() in dev_text:
                            logger.info(f"[DeviantArt] 作品展示页相册反查成功: 页面已确认归属相册 '{gallery}'")
                        else:
                            logger.warning(f"[DeviantArt] 作品展示页相册反查提示: 页面文本未显式包含 '{gallery}' (可能收起在集合卡片中)")
                    except Exception as ge:
                        logger.debug(f"[DeviantArt] 作品展示页反查相册提示: {ge}")

                # 成功离开 /submit 后的最终真发布核验：访问 Studio Drafts 验证草稿彻底销毁与出库
                logger.info("[DeviantArt] 正在对 DeviantArt Studio 草稿箱执行反向核验 (验证草稿是否彻底销毁/出库)...")
                draft_cleared = False
                studio_page = None
                try:
                    studio_page = context.new_page()
                    self.apply_stealth(studio_page)
                    self.safe_goto(studio_page, "https://www.deviantart.com/studio", timeout=25000)
                    studio_page.wait_for_timeout(2000)

                    # 尝试点击 Drafts 标签页
                    try:
                        drafts_tab = studio_page.query_selector("a:has-text('Drafts'), button:has-text('Drafts'), span:has-text('Drafts')")
                        if drafts_tab:
                            drafts_tab.click()
                            studio_page.wait_for_timeout(1500)
                    except Exception as dte:
                        logger.debug(f"[DeviantArt] Studio 点击 Drafts 标签页提示: {dte}")

                    import re
                    clean_title = title.strip()
                    safe_slug = re.sub(r'[\\/*?:"<>|\s]+', '_', clean_title)
                    keywords_to_check = [clean_title]
                    if safe_slug and safe_slug != clean_title:
                        keywords_to_check.append(safe_slug)

                    studio_text = studio_page.inner_text("body")
                    title_in_drafts = any(kw.lower() in studio_text.lower() for kw in keywords_to_check if kw)

                    if title_in_drafts:
                        # 缓冲 5 秒刷新复核 (防止 DA 异步数据库索引毫秒级微小延迟，总缓冲达 15+ 秒)
                        logger.info("[DeviantArt] 探测到草稿暂存，等待 5 秒后执行最终状态复核...")
                        time.sleep(5)
                        studio_page.reload()
                        studio_page.wait_for_timeout(2000)
                        try:
                            drafts_tab = studio_page.query_selector("a:has-text('Drafts'), button:has-text('Drafts'), span:has-text('Drafts')")
                            if drafts_tab:
                                drafts_tab.click()
                                studio_page.wait_for_timeout(1500)
                        except Exception:
                            pass
                        studio_text_retry = studio_page.inner_text("body")
                        still_in_drafts = any(kw.lower() in studio_text_retry.lower() for kw in keywords_to_check if kw)
                        if still_in_drafts:
                            logger.error(f"[DeviantArt] 草稿箱反向核验失败: 作品 '{title}' 依然滞留在 Studio Drafts 中未完成出库！")
                            draft_cleared = False
                        else:
                            logger.info("[DeviantArt] 复核通过：作品已从 Studio Drafts 彻底出库！")
                            draft_cleared = True
                    else:
                        logger.info("[DeviantArt] 草稿箱反向核验通过：Studio Drafts 中已无同名草稿，作品已正式销毁草稿并出库！")
                        draft_cleared = True

                except Exception as se:
                    logger.warning(f"[DeviantArt] 草稿箱反向核验网络异常 ({se})，以投稿主页面成功跳转为准")
                    draft_cleared = True
                finally:
                    if studio_page:
                        try:
                            studio_page.close()
                        except Exception:
                            pass

                if not draft_cleared:
                    self.close_login_session(browser, context, page)
                    return False, f"DeviantArt 提交未能正式出库，作品 '{title}' 仍在草稿箱 (Drafts) 中未成功发布，已拦截虚假成功报告", compliance

                logger.info("[DeviantArt] 作品正式上架且草稿已销毁确认！执行安全网络缓冲等待 (3秒)...")
                time.sleep(3)

                self.close_login_session(browser, context, page)
                return True, "DeviantArt 已正式发布并出库 (草稿箱已销毁，作品正式上架)", compliance
        except Exception as e:
            logger.error(f"[DeviantArt] 自动化发布异常: {e}")
            return False, f"DeviantArt 自动化发布异常: {e}", compliance
