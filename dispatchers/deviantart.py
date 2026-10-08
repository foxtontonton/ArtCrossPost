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
        import urllib.parse, json
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

                page = context.new_page()
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
        images = payload.get("images", [])
        if not images:
            return False, "DeviantArt 发布失败: 未选择图片", []

        title = payload.get("title", "") or "Untitled"
        desc = payload.get("description", "")
        tags = payload.get("tags", [])
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

                # 8. 勾选官方 Mature Content 成人内容设置
                # 8. 勾选官方 Mature Content 成人内容设置 (对齐 DA 原生表单规范，补全二级分类必选)
                mature_selector = "input[type='checkbox'][name='matureContent'], input[name='matureContent'], label:has-text('Mature') input"
                try:
                    mature_checkbox = page.query_selector(mature_selector)
                    if not mature_checkbox:
                        mature_checkbox = page.query_selector("label:has-text('Mature')")
                    if mature_checkbox:
                        is_chk = mature_checkbox.is_checked() if hasattr(mature_checkbox, "is_checked") else False
                        if is_mature and not is_chk:
                            mature_checkbox.evaluate("el => el.click()")
                        elif not is_mature and is_chk:
                            mature_checkbox.evaluate("el => el.click()")
                        page.wait_for_timeout(300)

                    # 8.1 若开启 Mature，必须补全二级分类与级别（否则 Submit Now 按钮处于 disabled 状态无法正式提交）
                    if is_mature:
                        page.wait_for_timeout(400)
                        # 选定分级: Strict 或 Moderate
                        try:
                            lvl_text = "Strict" if mature_level == "strict" else "Moderate"
                            lvl_elem = page.query_selector(f"input[value='{mature_level}'], label:has-text('{lvl_text}') input, label:has-text('{lvl_text}')")
                            if lvl_elem:
                                lvl_elem.evaluate("el => el.click()")
                                page.wait_for_timeout(200)
                                logger.info(f"[DeviantArt] 已选定 Mature 分级: {lvl_text}")
                        except Exception as le:
                            logger.debug(f"[DeviantArt] 勾选 Mature 分级选项提示: {le}")

                        # 勾选具体类别必选项: Nudity (裸体) 与 Sexual themes (性暗示/内容)
                        sub_cat_sels = [
                            "input[name*='nudity'], input[id*='nudity'], label:has-text('Nudity') input, label:has-text('Nudity')",
                            "input[name*='sexual'], input[id*='sexual'], label:has-text('Sexual') input, label:has-text('Sexual themes') input, label:has-text('Sexual themes')"
                        ]
                        for sub_sel in sub_cat_sels:
                            try:
                                sub_el = page.query_selector(sub_sel)
                                if sub_el:
                                    is_sub_c = sub_el.is_checked() if hasattr(sub_el, "is_checked") else False
                                    if not is_sub_c:
                                        sub_el.evaluate("el => el.click()")
                                        page.wait_for_timeout(200)
                                        logger.info(f"[DeviantArt] 成功勾选 Mature 二级分类: {sub_sel}")
                            except Exception as se:
                                logger.debug(f"[DeviantArt] 勾选 Mature 二级分类提示: {se}")
                except Exception as e:
                    logger.warning(f"[DeviantArt] 设置 Mature Content 异常: {e}")

                # 回读验证 Mature 状态
                v_mat_res, v_mat_det = self.check_selector_state(page, mature_selector, expected=is_mature)
                compliance.append(self.verify_compliance_item("deviantart", "mature_content", is_mature, v_mat_res if is_mature else True, v_mat_det))

                # 9. 提交发布 (精准定位 Submit Now 按钮，严禁误触 Save to Stash / Save as draft)
                logger.info("[DeviantArt] 正在定位正式发布按钮 (Submit Now)...")
                submit_btn = None
                candidate_sels = [
                    "button:has-text('Submit Now')",
                    "button[type='submit']:has-text('Submit Now')",
                    "div[role='dialog'] button:has-text('Submit Now')",
                    "button:has-text('Submit')",
                    "button[type='submit']:has-text('Submit')"
                ]
                for sel in candidate_sels:
                    btns = page.query_selector_all(sel)
                    for b in btns:
                        txt = b.inner_text().strip().lower()
                        # 严禁误触存草稿或取消
                        if any(kw in txt for kw in ["stash", "draft", "cancel", "save to"]):
                            continue
                        if "submit" in txt:
                            submit_btn = b
                            break
                    if submit_btn:
                        break

                if not submit_btn:
                    self.close_login_session(browser, context)
                    return False, "DeviantArt 未定位到发布提交按钮 (Submit Now)，已保留在草稿中未发布", compliance

                # 必须轮询等待按钮解禁 (解除 disabled / aria-disabled="true")
                btn_ready = False
                wait_btn_start = time.time()
                while time.time() - wait_btn_start < 15:
                    if submit_btn.is_enabled() and submit_btn.get_attribute("aria-disabled") != "true":
                        btn_ready = True
                        break
                    time.sleep(0.8)

                if not btn_ready:
                    # 采集页面是否有未满足的必填校验错误
                    err_hint = ""
                    err_elem = page.query_selector("div[role='alert'], span.error, p[class*='error']")
                    if err_elem:
                        err_hint = f" (页面提示: {err_elem.inner_text().strip()})"
                    self.close_login_session(browser, context)
                    return False, f"DeviantArt 发布按钮处于禁用状态无法提交{err_hint}，已中止以防存为半成品草稿", compliance

                logger.info("[DeviantArt] 发布按钮就绪，正在点击提交正式发布...")
                try:
                    submit_btn.click()
                except Exception as e:
                    logger.debug(f"[DeviantArt] 普通点击提交失败，切换 evaluate 点击: {e}")
                    submit_btn.evaluate("el => el.click()")

                # 真正的成功判定条件 (彻底删除假断言 dialog is None)
                def is_da_submitted(p):
                    curr_u = p.url.lower()
                    # 判定 1: 页面 URL 彻底离开 /submit 路径，或进入具体作品展示页
                    if "/submit" not in curr_u and ("deviantart.com" in curr_u or "deviation" in curr_u or "/art/" in curr_u):
                        return True
                    if "/art/" in curr_u or "deviation" in curr_u:
                        return True
                    # 判定 2: 页面出现提交成功的官方提示标识
                    success_indicator = p.query_selector(
                        "div:has-text('Your deviation has been submitted'), div:has-text('Published'), h1:has-text('Success'), div[data-testid='toast']:has-text('submitted')"
                    )
                    if success_indicator:
                        return True
                    return False

                logger.info("[DeviantArt] 等待发布结果确认 (等待离开 /submit 或展示页渲染)...")
                sub_ok, sub_msg = self.assert_submitted(
                    page,
                    is_da_submitted,
                    timeout=45000,
                    error_msg="DeviantArt 提交超时未离开投稿页 (/submit)，作品可能仅存为草稿箱，请前往网页确认发布"
                )

                # 成功确认后必须安全缓冲 4 秒，确保底层异步流完全 flush，严禁秒关浏览器
                if sub_ok:
                    logger.info("[DeviantArt] 作品正式上架确认！执行安全网络缓冲等待 (4秒)...")
                    page.wait_for_timeout(4000)

                self.close_login_session(browser, context)
                if not sub_ok:
                    return False, sub_msg, compliance

                return True, "DeviantArt 发布成功 (已更新标题、对齐 Created using AI tools 及 Mature Content 二级分类并正式提交)", compliance
        except Exception as e:
            logger.error(f"[DeviantArt] 自动化发布异常: {e}")
            return False, f"DeviantArt 自动化发布异常: {e}", compliance
