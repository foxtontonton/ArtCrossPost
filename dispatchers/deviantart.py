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
from pathlib import Path
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
    def _cleanup_orphan_draft(self, page, title, img_path):
        """
        清理由于发布中断/异常而在 DA Studio 留下的孤儿草稿:
        - 尝试先关闭当前 Modal (点击 Discard / Cancel / Close)
        - 导航至 https://www.deviantart.com/studio
        - 切换至 Drafts 选项卡
        - 查找匹配 title 或文件名 (Path(img_path).stem) 的草稿卡片
        - 点击 More -> Delete Draft 并确认
        """
        if not page:
            return
        logger.info(f"[DeviantArt] 正在执行孤儿草稿自动清理 (针对 '{title}' / '{Path(img_path).stem}')...")
        try:
            # 1. 尝试关闭当前弹窗
            try:
                close_btn = page.query_selector("button[aria-label='Close'], button:has-text('Discard'), button:has-text('Cancel')")
                if close_btn and close_btn.is_visible():
                    close_btn.click()
                    page.wait_for_timeout(800)
                    discard_conf = page.query_selector("div[role='dialog'] button:has-text('Discard'), button:has-text('Discard')")
                    if discard_conf and discard_conf.is_visible():
                        discard_conf.click()
                        page.wait_for_timeout(800)
            except Exception:
                pass

            # 2. 导航至 studio 草稿页
            self.safe_goto(page, "https://www.deviantart.com/studio", timeout=30000)
            page.wait_for_timeout(2000)

            # 点击 Drafts 标签页
            d_tab = page.query_selector("a:has-text('Drafts'), button:has-text('Drafts'), span:has-text('Drafts')")
            if d_tab:
                d_tab.click()
                page.wait_for_timeout(2000)

            # 3. 查找匹配的草稿行
            target_stem = Path(img_path).stem.lower()
            clean_title = (title or "").lower()
            rows = page.query_selector_all("div[role='row'], div.ds-card, section, div[class*='row']")
            target_row = None
            for r in rows:
                txt = (r.inner_text() or "").strip().lower()
                if (target_stem and target_stem in txt) or (clean_title and clean_title in txt):
                    target_row = r
                    break

            if target_row:
                more_btn = target_row.query_selector("button[aria-label='More'], button[aria-haspopup='menu']")
                if more_btn:
                    more_btn.click()
                    page.wait_for_timeout(600)
                    del_btn = page.query_selector("button:has-text('Delete Draft'), [role='menuitem']:has-text('Delete Draft'), div:has-text('Delete Draft')")
                    if del_btn:
                        del_btn.evaluate("el => el.click()")
                        page.wait_for_timeout(800)
                        conf = page.query_selector("div[role='dialog'] button:has-text('Delete'), button:has-text('Delete')")
                        if conf:
                            conf.evaluate("el => el.click()")
                            page.wait_for_timeout(1500)
                            logger.info(f"[DeviantArt] 成功清理孤儿草稿: '{title}' / '{target_stem}'")
            else:
                logger.info("[DeviantArt] 未在 Studio Drafts 列表中发现残留草稿，无需清理")
        except Exception as e_clean:
            logger.warning(f"[DeviantArt] 清理孤儿草稿过程提示: {e_clean}")

    def _submit_deviation(self, page, context, images, title, desc, _tags, is_mature, mature_level, is_ai, gallery, compliance, thumb_wait_timeout=60):
        if isinstance(images, (str, Path)):
            images = [str(images)]
        else:
            images = [str(p) for p in images]

        first_img = images[0]

        # 2. 直达投稿入口
        self.safe_goto(page, "https://www.deviantart.com/submit", timeout=45000)
        try:
            page.wait_for_selector("input[type='file'][accept*='image'], input[type='file'][accept*='png'], input[type='file'][accept*='gif']", timeout=25000)
        except Exception:
            try:
                page.wait_for_selector("input[type='file']", timeout=10000)
            except Exception as e:
                logger.debug(f"[DeviantArt] 等待上传文件控件超时: {e}")

        page.wait_for_timeout(1000)
        all_fis = page.query_selector_all("input[type='file']")
        if not all_fis:
            return False, "DeviantArt 投稿页未能定位到文件上传控件 (请检查网络代理或重新登录)"

        # 3. 按素材格式智能分发 (首图为 source file 不可重排，动图 GIF 匹配 accept='image/gif'，静态图匹配 accept='image/png|jpg')
        is_gif = first_img.lower().endswith(".gif")
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

        # 步骤 1: 只传首图，等待 Edit Draft 弹窗渲染 (现有 title_sel 断言保留)
        target_fi.set_input_files([first_img])
        logger.info(f"[DeviantArt] 步骤 1/5: 首图文件已提交 ({'GIF动图' if is_gif else '静态图片'})，等待上传处理与 Edit Draft 弹窗渲染...")

        # 4. 等待上传与表单渲染 (自适应心跳轮询，最高 120 秒，发现标题输入框立即放行)
        title_sel = "input[name='title'], input[placeholder*='Add your title' i], input[placeholder*='title' i], div[role='dialog'] input[type='text']:not([placeholder*='Search'])"
        title_ready = False
        upload_wait_start = time.time()
        upload_timeout = 120
        while time.time() - upload_wait_start < upload_timeout:
            try:
                t_chk = page.query_selector(title_sel)
                if t_chk and t_chk.is_visible():
                    title_ready = True
                    break
            except Exception:
                pass
            elapsed_up = int(time.time() - upload_wait_start)
            if elapsed_up > 0 and elapsed_up % 10 == 0:
                is_llama = False
                try:
                    is_llama = page.evaluate("() => document.body.innerText.includes('llama')")
                except Exception:
                    pass
                hint = " (羊驼正在处理素材中...)" if is_llama else ""
                logger.info(f"[DeviantArt] 正在等待素材上传与 Studio 模态框渲染 (已等待 {elapsed_up}s{hint})...")
            time.sleep(1.0)

        if not title_ready:
            att_ok, att_msg = self.assert_attached(
                page,
                title_sel,
                timeout=2000,
                error_msg=f"DeviantArt 素材上传超时或 Studio 模态框未渲染 (已等待 {upload_timeout} 秒)，已中止发布 (避免存为无图草稿)"
            )
            if not att_ok:
                return False, att_msg

        page.wait_for_timeout(1000)

        # 5. 填写标题 (彻底覆盖默认填充的文件名，利用 React 原生 setter 确保受控组件状态同步)
        def set_title_value(t_val):
            t_inp = page.query_selector(title_sel)
            if t_inp:
                try:
                    t_inp.click(timeout=2000)
                    t_inp.fill(t_val, timeout=3000)
                except Exception:
                    pass
                try:
                    page.evaluate("""(val) => {
                        const el = document.querySelector(".ReactModal__Content input[name='title']") || document.querySelector("dialog[open] input[name='title']") || document.querySelector("input[name='title'], input[placeholder*='Add your title']");
                        if (el) {
                            try {
                                const proto = window.HTMLInputElement.prototype;
                                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                                if (setter) setter.call(el, val);
                                else el.value = val;
                            } catch (e) {
                                el.value = val;
                            }
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.blur();
                        }
                    }""", t_val)
                except Exception as e:
                    logger.debug(f"[DeviantArt] 标题事件派发异常: {e}")
                page.wait_for_timeout(400)

        set_title_value(title)
        logger.info(f"[DeviantArt] 标题已填入并触发持久化: {title}")

        f_ok, f_msg = self.assert_field_value(page, title_sel, expected_value=title, max_retries=1)
        if not f_ok:
            self._cleanup_orphan_draft(page, title, first_img)
            return False, f"DeviantArt 标题断言失败: {f_msg}"

        # 6. 填写描述 (优先在当前打开的 dialog / ReactModal 容器内定位，并防止 <dialog open> 指针遮挡导致 30s 超时)
        desc_area = page.query_selector(
            "dialog[open] div.tiptap[contenteditable='true'], dialog[open] div[role='textbox'], dialog[open] textarea[name='description'], dialog[open] div[contenteditable='true'], "
            ".ReactModal__Content div.tiptap[contenteditable='true'], .ReactModal__Content div[role='textbox'], .ReactModal__Content textarea[name='description'], .ReactModal__Content div[contenteditable='true'], "
            "div.tiptap[contenteditable='true'], div[role='textbox'], textarea[name='description'], div[contenteditable='true']"
        )
        if desc_area and desc:
            try:
                desc_area.click(timeout=3000)
                desc_area.fill(desc, timeout=5000)
            except Exception as e_desc:
                logger.debug(f"[DeviantArt] 描述框常规点击被拦截，切换 JS 安全注入: {e_desc}")
                try:
                    desc_area.evaluate("""(el, val) => {
                        el.focus();
                        if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                            el.value = val;
                        } else {
                            el.innerText = val;
                        }
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""", desc)
                except Exception as e_js:
                    logger.warning(f"[DeviantArt] 描述框 JS 注入提示: {e_js}")
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
                el.click(timeout=3000)
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
        gallery = (gallery or "").strip()
        if gallery:
            logger.info(f"[DeviantArt] 正在定位相册选择器并选择相册: '{gallery}'...")
            gallery_matched = False
            try:
                # 0. <select> 模糊匹配与选择 (兼容原生 select / 单测 mock 场景)
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

                # 1. 定位 Studio 原生 Gallery 下拉组件 (<label>Gallery</label> 对应专属 combobox)
                if not gallery_matched:
                    g_cb = page.evaluate_handle("""() => {
                        const labels = Array.from(document.querySelectorAll('label'));
                        const gLabel = labels.find(l => (l.innerText || '').trim() === 'Gallery');
                        if (gLabel && gLabel.parentElement) {
                            return gLabel.parentElement.querySelector("div[role='combobox']") || gLabel.parentElement.querySelector('[role="combobox"]');
                        }
                        return document.querySelector(".ReactModal__Content div[role='combobox']:not([aria-autocomplete])") || null;
                    }""").as_element()

                    if g_cb:
                        cur_cb_text = (g_cb.inner_text() or "").strip()
                        if gallery.lower() in cur_cb_text.lower():
                            gallery_matched = True
                            logger.info(f"[DeviantArt] 相册已处于选中状态: '{gallery}'")
                        else:
                            # 展开 Gallery combobox 下拉面板
                            if g_cb.get_attribute("aria-expanded") != "true":
                                click_safely(g_cb)
                                page.wait_for_timeout(600)

                            # 优先在 listbox 容器内精确匹配目标相册
                            click_success = page.evaluate("""(g) => {
                                const labels = Array.from(document.querySelectorAll('label'));
                                const gLabel = labels.find(l => (l.innerText || '').trim() === 'Gallery');
                                const cb = gLabel && gLabel.parentElement ? gLabel.parentElement.querySelector("div[role='combobox']") : null;
                                const listId = cb ? cb.getAttribute('aria-controls') : null;
                                const listEl = listId ? document.getElementById(listId) : document.querySelector("div[id$='-list'], ul[role='listbox']");
                                const scope = listEl || document.body;

                                const matches = Array.from(scope.querySelectorAll('label, [role=\"option\"], [role=\"checkbox\"], li, div, span')).filter(el => {
                                    const t = (el.innerText || '').trim();
                                    return t.toLowerCase() === g.toLowerCase();
                                });
                                if (matches.length > 0) {
                                    // 优先点击 label / option / checkbox 或最外层行容器
                                    const target = matches.find(m => m.tagName === 'LABEL' || m.getAttribute('role') === 'option' || m.getAttribute('role') === 'checkbox') || matches[0];
                                    target.click();
                                    return true;
                                }
                                return false;
                            }""", gallery)

                            if click_success:
                                gallery_matched = True
                                logger.info(f"[DeviantArt] 通过相册下拉列表项成功匹配并点击: '{gallery}'")
                                page.wait_for_timeout(400)
                            else:
                                # 兼容 mock 场景的 query_selector_all 匹配
                                item_sels = [
                                    f"[role='option']:has-text('{gallery}')",
                                    f"[role='menuitem']:has-text('{gallery}')",
                                    f"[role='checkbox']:has-text('{gallery}')",
                                    f"label:has-text('{gallery}')",
                                    f"li:has-text('{gallery}')",
                                    f"button:has-text('{gallery}')",
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

                            # 关闭下拉面板：通过再次点击 combobox 触发器（toggle close）确认选定，严禁按 Escape
                            try:
                                if g_cb.get_attribute("aria-expanded") == "true":
                                    click_safely(g_cb)
                            except Exception:
                                pass
                            page.wait_for_timeout(400)

                if not gallery_matched:
                    logger.error(f"[DeviantArt] 相册选择器未能匹配或定位到指定相册 '{gallery}'，已阻断发布以防止发布到错误相册")
                    self._cleanup_orphan_draft(page, title, first_img)
                    return False, f"DeviantArt 指定相册 '{gallery}' 未能匹配或定位，已阻断发布以防止作品发布到错误相册"
                else:
                    # 2. 选择后回读确认选中项文本 (严格阻断式回读校验)
                    confirmed = False
                    for _ in range(6):
                        page.wait_for_timeout(400)
                        try:
                            readback_res = page.evaluate("""(g) => {
                                const labels = Array.from(document.querySelectorAll('label'));
                                const gLabel = labels.find(l => (l.innerText || '').trim() === 'Gallery');
                                const cb = gLabel && gLabel.parentElement ? gLabel.parentElement.querySelector("div[role='combobox']") : null;
                                const cbText = cb ? (cb.innerText || '') : '';
                                if (cbText.toLowerCase().includes(g.toLowerCase())) return true;
                                const fullText = (document.querySelector('.ReactModal__Content') || document.body).innerText || '';
                                return fullText.toLowerCase().includes(g.toLowerCase());
                            }""", gallery)
                            if readback_res is True:
                                confirmed = True
                                break
                        except Exception:
                            pass
                    if confirmed:
                        logger.info(f"[DeviantArt] 回读确认相册选定成功: '{gallery}'")
                    else:
                        logger.error(f"[DeviantArt] 回读未能确认相册 '{gallery}' 文本，已阻断发布以防止错位")
                        self._cleanup_orphan_draft(page, title, first_img)
                        return False, f"DeviantArt 相册回读校验失败: 表单中未能确认选定相册 '{gallery}' 文本，已阻断发布以防止作品发布到错误相册"
            except Exception as e_gallery:
                logger.error(f"[DeviantArt] 设置相册 '{gallery}' 过程发生异常: {e_gallery}")
                self._cleanup_orphan_draft(page, title, first_img)
                return False, f"DeviantArt 设置相册 '{gallery}' 异常: {e_gallery}，已阻断发布以防止作品发布到错误相册"

        # 步骤 3 & 步骤 4: 当包含多图时，点击 "+" 追加剩余图片并等待缩略图就绪 (单图模式直接跳过)
        remaining_images = images[1:]
        if remaining_images:
            logger.info(f"[DeviantArt] 步骤 3/5: 检测到多图素材 (共 {len(images)} 张)，正在弹窗底部缩略图栏定位追加按钮以添加剩余 {len(remaining_images)} 张图片...")

            # 首图可能仍在进行羊驼后台转码，稍作等待以确保底栏控件渲染就绪 (生产环境上限 20s，单测低超时自适应)
            llama_max_wait = min(20, thumb_wait_timeout) if thumb_wait_timeout >= 1 else 0
            llama_wait_start = time.time()
            while time.time() - llama_wait_start < llama_max_wait:
                is_llama = False
                try:
                    is_llama = page.evaluate("() => (document.body.innerText || '').includes('llama noms your file')")
                except Exception:
                    pass
                if not is_llama:
                    break
                time.sleep(1.0)

            existing_inputs = page.query_selector_all("input[type='file']")

            # 确保弹窗主体未发生整体纵向漂移，使底栏胶卷位于可视范围
            try:
                page.evaluate("() => { const m = document.querySelector('.ReactModal__Content'); if (m) m.scrollTop = 0; }")
            except Exception:
                pass

            plus_btn = None
            candidate_plus_sels = [
                "button[aria-label='Attach additional media']",
                "button[aria-label*='additional media' i]",
                ".ReactModal__Content button[aria-label*='additional media' i]",
                "div.rfiU0f button",
                ".ReactModal__Content div.rfiU0f button",
                "button[aria-label*='Attach' i]",
                ".ReactModal__Content button[aria-label*='Attach' i]",
                ".ReactModal__Content [class*='filmstrip'] button",
                ".ReactModal__Content div:has(> img) + div button",
                ".ReactModal__Content [data-hook*='thumbnail'] button",
                ".ReactModal__Content [class*='thumbnail'] button",
                "[data-hook*='thumbnail'] button",
                "[class*='thumbnail'] button",
                "[class*='filmstrip'] button"
            ]

            # 1. 优先通过 wait_for_selector (state='attached') 快速定位底栏追加按钮
            try:
                w_timeout = min(8000, max(1000, thumb_wait_timeout * 200)) if thumb_wait_timeout >= 1 else 1000
                plus_btn = page.wait_for_selector(
                    "button[aria-label='Attach additional media'], button[aria-label*='additional media' i]",
                    state="attached",
                    timeout=w_timeout
                )
            except Exception:
                plus_btn = None

            # 2. 兜底逐个快速探测 (兼容单测 mock 与异形 DOM)
            if not plus_btn:
                for sel in candidate_plus_sels:
                    try:
                        elements = page.query_selector_all(sel)
                        if elements:
                            plus_btn = elements[0]
                            break
                    except Exception:
                        pass

            appended_ok = False
            err_reason = ""

            # 解除前置表单项焦点，防止聚焦拦截或受控事件冒泡干扰
            try:
                page.evaluate("() => { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); }")
            except Exception:
                pass

            # 机制 A: 优先使用 expect_file_chooser() 捕获系统文件选择框
            if plus_btn:
                try:
                    logger.info("[DeviantArt] 正在点击追加素材按钮并通过 expect_file_chooser 捕获系统文件选择器...")
                    try:
                        plus_btn.scroll_into_view_if_needed()
                    except Exception:
                        pass
                    with page.expect_file_chooser(timeout=8000) as fc_info:
                        plus_btn.click()
                    file_chooser = fc_info.value
                    file_chooser.set_files(remaining_images)
                    appended_ok = True
                    logger.info(f"[DeviantArt] 机制 A 成功: 已通过 FileChooser 一次性注入剩余 {len(remaining_images)} 张图片")
                except Exception as e_fc:
                    logger.warning(f"[DeviantArt] 机制 A (FileChooser) 未捕获到文件选择器 ({e_fc})，转入机制 B...")

            # 机制 B: 若机制 A 未捕获到 FileChooser，改用 set_input_files 打底栏专属追加 input[type='file']
            if not appended_ok:
                logger.info("[DeviantArt] 机制 A 未捕获到 FileChooser，尝试机制 B (定位底栏专属追加 input[type='file'])...")
                new_inputs = page.query_selector_all(".ReactModal__Content input[type='file'], div.rfiU0f input[type='file']")
                target_additional_fi = None
                for fi in new_inputs:
                    if fi not in existing_inputs:
                        target_additional_fi = fi
                        break
                if not target_additional_fi and len(new_inputs) > 1:
                    target_additional_fi = new_inputs[-1]
                if not target_additional_fi:
                    dedicated_fi = page.query_selector("div.rfiU0f input[type='file'], .ReactModal__Content div:has(button[aria-label*='additional media' i]) input[type='file']")
                    if dedicated_fi and dedicated_fi not in existing_inputs:
                        target_additional_fi = dedicated_fi

                if target_additional_fi:
                    try:
                        target_additional_fi.evaluate("el => el.setAttribute('multiple', '')")
                    except Exception:
                        pass
                    try:
                        target_additional_fi.set_input_files(remaining_images)
                        appended_ok = True
                        logger.info(f"[DeviantArt] 机制 B 成功: 已通过专属 input[type='file'] 注入剩余 {len(remaining_images)} 张图片")
                    except Exception as e_fi:
                        err_reason = f"弹窗内 input[type='file'] 注入失败: {e_fi}"
                else:
                    err_reason = "未捕获到系统文件选择器，且弹窗内未能定位到底栏追加输入框"

            if not appended_ok:
                self._cleanup_orphan_draft(page, title, first_img)
                return False, f"DeviantArt 多图追加失败: 机制 A (FileChooser) 与机制 B (隐藏Input) 均未能提交剩余图片 ({err_reason})，已清理草稿中止发布以防漏发"

            # 步骤 4: 等缩略图 - 轮询缩略图栏，数量 == len(images) 且羊驼转码动画消退才继续
            expected_total = len(images)
            dynamic_thumb_timeout = max(180, expected_total * 25) if thumb_wait_timeout >= 10 else thumb_wait_timeout
            logger.info(f"[DeviantArt] 步骤 4/5: 正在轮询缩略图栏与羊驼后台转码状态，等待全部 {expected_total} 张图片就绪 (超时 {dynamic_thumb_timeout}s)...")
            thumbs_ready = False
            wait_thumb_start = time.time()
            while time.time() - wait_thumb_start < dynamic_thumb_timeout:
                current_detected_count = 0
                is_llama_running = False
                try:
                    eval_state = page.evaluate("""(exp) => {
                        try {
                            const modal = document.querySelector('.ReactModal__Content') || document.body;
                            const text = modal.innerText || '';
                            const isLlama = text.includes('llama noms your file');
                            const m1 = text.match(/(\\d+)\\s*\\/\\s*\\d+\\s*files/i);
                            let detectedCount = 0;
                            if (m1) {
                                detectedCount = parseInt(m1[1], 10);
                            } else {
                                const m2 = text.match(/(\\d+)\\s+files/i);
                                if (m2) {
                                    detectedCount = parseInt(m2[1], 10);
                                } else {
                                    const thumbEls = modal.querySelectorAll("[data-hook*='thumbnail'] img, [class*='thumbnail'] img, [class*='filmstrip'] img, div[role='listitem'] img, div[role='tablist'] img");
                                    if (thumbEls.length > 0) detectedCount = thumbEls.length;
                                }
                            }
                            return { count: detectedCount, llama: isLlama };
                        } catch(e) {
                            return { count: 0, llama: false };
                        }
                    }""", expected_total)
                    if isinstance(eval_state, dict):
                        current_detected_count = eval_state.get("count", 0)
                        is_llama_running = eval_state.get("llama", False)
                    elif isinstance(eval_state, int):
                        current_detected_count = eval_state
                        is_llama_running = False
                except Exception:
                    pass

                if current_detected_count < expected_total:
                    try:
                        dom_thumbs = page.query_selector_all(".ReactModal__Content [data-hook*='thumbnail'], .ReactModal__Content [class*='thumbnail'] img, [data-hook*='thumbnail'] img")
                        if len(dom_thumbs) >= expected_total:
                            current_detected_count = len(dom_thumbs)
                    except Exception:
                        pass

                if current_detected_count >= expected_total and not is_llama_running:
                    thumbs_ready = True
                    logger.info(f"[DeviantArt] 缩略图栏已检测到全部 {expected_total} 张图片就绪且后台转码完成 (当前: {current_detected_count} 张)！")
                    break

                elapsed_thumb = int(time.time() - wait_thumb_start)
                if elapsed_thumb > 0 and elapsed_thumb % 10 == 0:
                    llama_hint = " (羊驼后台转码处理中...)" if is_llama_running else ""
                    logger.info(f"[DeviantArt] 正在等待缩略图就绪 ({current_detected_count}/{expected_total} 张{llama_hint}，已等待 {elapsed_thumb}s)...")
                time.sleep(1.0)

            if not thumbs_ready:
                self._cleanup_orphan_draft(page, title, first_img)
                return False, f"DeviantArt 缩略图栏等待超时 ({dynamic_thumb_timeout}s 内未检测到全部 {expected_total} 张素材完成转码就绪)，已中止发布并清理草稿以防漏图"

        # 步骤 5: 提交发布 (精准定位 Submit 按钮，严禁误触 Save to Studio / Stash / Draft)
        logger.info("[DeviantArt] 正在定位正式发布按钮 (Submit)...")

        # 确保全部素材就绪后标题未被篡改/漂移 (若发生漂移则重置为预期标题)
        try:
            t_box = page.query_selector(title_sel)
            if t_box and hasattr(t_box, "input_value"):
                raw_v = t_box.input_value()
                if isinstance(raw_v, str):
                    now_title = raw_v.strip()
                    if now_title and now_title != title:
                        logger.warning(f"[DeviantArt] 检测到表单标题因素材操作漂移为 '{now_title}'，正在重置为预期标题 '{title}'...")
                        set_title_value(title)
        except Exception as e_tc:
            logger.debug(f"[DeviantArt] 提交前标题校验异常: {e_tc}")
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
            self._cleanup_orphan_draft(page, title, first_img)
            return False, "DeviantArt 未定位到发布提交按钮 (Submit)，已清理草稿中止发布"

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
            self._cleanup_orphan_draft(page, title, first_img)
            return False, f"DeviantArt 发布按钮处于禁用状态无法提交{err_hint}，已中止并清理草稿"

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
            self._cleanup_orphan_draft(page, title, first_img)
            return False, f"{sub_msg}{err_detail}"

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
            return False, f"DeviantArt 提交未能正式出库，作品 '{title}' 仍在草稿箱 (Drafts) 中未成功发布，已拦截虚假成功报告"

        logger.info("[DeviantArt] 作品正式上架且草稿已销毁确认！执行安全网络缓冲等待 (3秒)...")
        time.sleep(3)
        return True, ""

    def _submit_single_deviation(self, page, context, img_path, title, desc, _tags, is_mature, mature_level, is_ai, gallery, compliance):
        """兼容旧版单图调用的别名方法"""
        return self._submit_deviation(page, context, [img_path], title, desc, _tags, is_mature, mature_level, is_ai, gallery, compliance)

    def _count_deviantart_gallery_images(self, page, expected_count: int) -> int:
        """
        核验 DeviantArt 发布后正式作品展示页 (/art/ 或 /deviation/) 上的图集图片数量 (对齐实际发出数 == 导入数):
        - 若 expected_count <= 1 (单图模式，前置已校验页面成功跳转作品页)，直接返回 1
        1. 优先通过 window.__INITIAL_STATE__ 数据层反查
        2. 其次通过 DOM 轮播/缩略图列表/指示器提取 (carousel, thumbnails, slide count)
        3. 探测页面文本如 "1 of N" 或 "N images"
        4. 兜底主舞台及多图容器中的有效图片元素统计
        """
        if expected_count <= 1:
            return 1

        # 增加对作品展示页动态组件加载与水合的缓冲轮询 (最多轮询 8 次，间隔 1s)
        for attempt in range(8):
            # 1. 优先从 window.__INITIAL_STATE__ / 前端数据层提取
            try:
                eval_count = page.evaluate("""() => {
                    try {
                        if (window.__INITIAL_STATE__) {
                            const st = window.__INITIAL_STATE__;
                            if (st.entities && st.entities.deviation) {
                                for (const k in st.entities.deviation) {
                                    const dev = st.entities.deviation[k];
                                    if (dev && dev.media && Array.isArray(dev.media.files) && dev.media.files.length > 0) {
                                        return dev.media.files.length;
                                    }
                                    if (dev && dev.extended && Array.isArray(dev.extended.files) && dev.extended.files.length > 0) {
                                        return dev.extended.files.length;
                                    }
                                }
                            }
                        }
                    } catch (e) {}
                    return null;
                }""")
                if eval_count is not None and isinstance(eval_count, int) and eval_count > 0:
                    logger.info(f"[DeviantArt] 从前端状态数据中核验到图集图片数: {eval_count} 张")
                    return eval_count
            except Exception as e:
                logger.debug(f"[DeviantArt] 提取前端状态数据异常: {e}")

            # 2. 从 DOM 画廊轮播 / 缩略图 / 页面标记中核验
            try:
                dom_count = page.evaluate("""(exp) => {
                    try {
                        const thumbSelectors = [
                            ".nijSZp img",
                            "button.CAKEeJ img",
                            "div[class*='nijSZp'] button",
                            "button.CAKEeJ",
                            "[data-hook='deviation_thumbnails'] img",
                            "div[class*='Thumbnails'] img",
                            "div[class*='Pagination'] button",
                            "div[role='tablist'] [role='tab']",
                            "button[aria-label*='slide']",
                            "button[aria-label*='Slide']",
                            "nav[aria-label*='Gallery'] button",
                            "[data-testid='gallery-slider'] img",
                            ".deviation-carousel img"
                        ];
                        for (const sel of thumbSelectors) {
                            const els = document.querySelectorAll(sel);
                            if (els && els.length > 1) {
                                return els.length;
                            }
                        }

                        const bodyText = document.body ? document.body.innerText : "";
                        const m1 = bodyText.match(/1\\s*(?:of|\\/)\\s*(\\d+)/i);
                        if (m1) {
                            return parseInt(m1[1], 10);
                        }
                        const m2 = bodyText.match(/(\\d+)\\s*(?:images|photos|deviations|张)/i);
                        if (m2) {
                            return parseInt(m2[1], 10);
                        }

                        // 精确舞台范围统计 (严格限定在 stage/filmstrip 专属多图容器内，杜绝主视口外推荐/评论区杂图误判)
                        const stageImgs = document.querySelectorAll("div[data-hook='art_stage'] img[class*='art'], [data-hook='deviation_thumbnails'] img, div[data-hook='gallery_stage'] img, .nijSZp img");
                        if (stageImgs.length >= exp) {
                            return exp;
                        }
                    } catch (e) {}
                    return null;
                }""", expected_count)
                if dom_count is not None and isinstance(dom_count, int) and dom_count > 0:
                    logger.info(f"[DeviantArt] 从 DOM 轮播/缩略图组件核验到图集图片数: {dom_count} 张")
                    return dom_count
            except Exception as e:
                logger.debug(f"[DeviantArt] DOM 图集数量提取异常: {e}")

            # 3. 兜底选择器统计
            try:
                thumbs = page.query_selector_all(".nijSZp img, button.CAKEeJ, [data-hook='deviation_thumbnails'] img, button[aria-label*='slide'], div[class*='Thumbnails'] img")
                if len(thumbs) > 1:
                    return len(thumbs)
            except Exception:
                pass

            time.sleep(1.0)

        return 1

    def publish(self, payload):
        """
        通过 Playwright 执行 DeviantArt 原生单作品多图 (Gallery) 发布:
        - 多张图片一次投稿进 1 个 deviation，图集形式展示 (首图为 source file 不可重排)
        - 单 deviation 图片上限 deviantart_max_images_per_deviation (默认 10)
        - 标题/描述/标签只填一次，作用于整个 deviation
        - 成功断言: 访问作品页反查图集图片数 == 预期传入数，不一致大声失败
        """
        images = payload.get("da_images") or payload.get("images", [])
        if not images:
            return False, "DeviantArt 发布失败: 未选择图片", []

        import re as _re
        title = _re.sub(r"[\r\n\t]+", " ", (payload.get("title", "") or "")).strip() or "Untitled"
        desc = payload.get("description", "")
        _tags = payload.get("tags", [])
        da_mature_opt = payload.get("platform_options", {}).get("deviantart_mature", "strict" if payload.get("is_nsfw", False) else "none")
        is_mature = (da_mature_opt != "none") or payload.get("is_nsfw", False)
        mature_level = "moderate" if da_mature_opt == "moderate" else "strict"
        is_ai = payload.get("platform_options", {}).get("deviantart_ai", payload.get("platform_options", {}).get("is_ai_generated", payload.get("is_ai", False)))
        gallery = payload.get("platform_options", {}).get("deviantart_gallery", "").strip()

        da_max = payload.get("deviantart_max_images_per_deviation") or \
                 payload.get("platform_options", {}).get("deviantart_max_images_per_deviation", 10)

        total_images = len(images)
        if total_images > da_max:
            return False, f"DA 单作品上限 {da_max} 张，当前 {total_images} 张，请自行删减素材后重试 (不自动拆分)", []

        cookies = self.get_cookies()
        if not cookies:
            return False, "DeviantArt 未登录，请先点击登录按钮获取会话", []

        logger.info(f"[DeviantArt] 开始原生单作品图集投稿 (共 {total_images} 张): 标题={title}, 原生AI开关={is_ai}, 原生Mature={is_mature} ({mature_level}), 相册='{gallery}'")
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

                # 2. 单作品多图投稿
                sub_ok, sub_msg = self._submit_deviation(
                    page, context, images, title, desc, _tags,
                    is_mature, mature_level, is_ai, gallery, compliance
                )
                if not sub_ok:
                    self.close_login_session(browser, context, page)
                    compliance.append(
                        self.verify_compliance_item(
                            "deviantart",
                            "gallery_count",
                            True,
                            False,
                            f"单作品图集投稿失败: {sub_msg}"
                        )
                    )
                    return False, f"DeviantArt 发布失败: {sub_msg}", compliance

                # 3. 成功断言: 访问作品页反查图集图片数 == 预期传入数 (不一致大声失败)
                actual_count = self._count_deviantart_gallery_images(page, total_images)
                if actual_count != total_images:
                    logger.error(f"[DeviantArt] 图集张数断言失败: 预期 {total_images} 张，作品页反查实际检测到 {actual_count} 张")
                    self.close_login_session(browser, context, page)
                    compliance.append(
                        self.verify_compliance_item(
                            "deviantart",
                            "gallery_count",
                            True,
                            False,
                            f"图集张数核验失败: 预期 {total_images} 张，实际检测到 {actual_count} 张"
                        )
                    )
                    return False, f"DeviantArt 图集张数断言失败: 预期包含 {total_images} 张图片，反查实际检测到 {actual_count} 张，已判定为失败以防漏图", compliance

                self.close_login_session(browser, context, page)
                compliance.append(
                    self.verify_compliance_item(
                        "deviantart",
                        "gallery_count",
                        True,
                        True,
                        f"单作品图集核验通过 (成功 {total_images}/{total_images} 张)"
                    )
                )
                return True, f"DeviantArt 发布成功 (单作品图集核验通过，包含 {total_images}/{total_images} 张图片)", compliance
        except Exception as e:
            logger.error(f"[DeviantArt] 自动化发布异常: {e}")
            return False, f"DeviantArt 自动化发布异常: {e}", compliance

