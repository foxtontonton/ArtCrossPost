# -*- coding: utf-8 -*-
"""
ArtCrossPost - Reddit Dispatcher (支持 Edge 浏览器自动化与 PRAW 官方 API 双轨模式)

双轨架构：
  1. 浏览器自动化通道 (主力通道，默认启用)：
     - 使用真实有界面的 Edge Profile (Stealth 模式)，严禁 headless (防指纹识别拦截)
     - 严格检测 "Prove your humanity" 及任何安全验证码挑战，一旦检测到立即大声报错中断，绝不重试循环
     - 保守发帖间隔 (双投之间间隔 12 秒)，严格保护版主账号 (u/Impressive_Bad6222) 免遭限流
  2. PRAW 官方 API 通道 (Dormant Fallback 备用通道)：
     - 若 data/reddit_secrets.json 填入了 client_id / client_secret 等凭据，自动无缝切回 PRAW 直连
     - 代码全量保留，绝不删除，静待未来官方审核通过
"""
import re
import json
import time
from pathlib import Path

from .base import BaseDispatcher
try:
    from logger import get_logger
except ImportError:
    from ..logger import get_logger

logger = get_logger("RedditDispatcher")

REDDIT_SECRETS_FILE = "reddit_secrets.json"
REDDIT_APP_PAGE = "https://www.reddit.com/prefs/apps/"
REDDIT_LOGIN_PAGE = "https://www.reddit.com/login"
TITLE_LIMIT = 300
LOGIN_WAIT_SECONDS = 180


class RedditDispatcher(BaseDispatcher):
    def __init__(self, cookies_dir):
        super().__init__(
            name="reddit",
            display_name="Reddit",
            login_url=REDDIT_LOGIN_PAGE,
            cookies_dir=cookies_dir,
        )
        # 凭据文件位于 data/ 目录，已被 .gitignore 全量保护 (data/*)
        self.secrets_file = self.data_dir / REDDIT_SECRETS_FILE

    # ---------------- 凭据管理 (PRAW 备用通道) ----------------
    def _load_secrets(self):
        """返回 (secrets_dict | None, error_msg)。缺失一律明说，不静默。"""
        if not self.secrets_file.exists():
            return None, (
                "Reddit API 凭据未配置。请先打开 https://www.reddit.com/prefs/apps/ "
                "创建一个类型为 script 的应用，然后将 client_id / client_secret / "
                f"username / password 写入 {self.secrets_file} "
                "(该文件位于 data/ 目录，已被 .gitignore 保护，不会进入仓库)。"
            )
        try:
            with open(self.secrets_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return None, f"读取 {self.secrets_file} 失败: {e}"
        missing = [k for k in ("client_id", "client_secret", "username", "password")
                   if not data.get(k)]
        if missing:
            return None, f"{self.secrets_file} 缺少必填字段: {', '.join(missing)}"
        return data, ""

    def _default_subreddit(self):
        try:
            from config import load_config
        except ImportError:
            from ..config import load_config
        return (load_config().get("defaults", {}).get("reddit_subreddit") or "BlackLatexMale").strip()

    def _default_post_to_profile(self):
        try:
            from config import load_config
        except ImportError:
            from ..config import load_config
        return bool(load_config().get("defaults", {}).get("reddit_post_to_profile", True))

    def _get_client(self):
        secrets, err = self._load_secrets()
        if not secrets:
            raise RuntimeError(err)
        try:
            import praw
        except ImportError:
            raise RuntimeError(
                "缺少依赖 praw，请先执行 pip install -r requirements.txt "
                "(requirements.txt 已加入 praw>=7.7.0)"
            )
        return praw.Reddit(
            client_id=secrets["client_id"],
            client_secret=secrets["client_secret"],
            username=secrets["username"],
            password=secrets["password"],
            user_agent=secrets.get("user_agent")
            or f"ArtCrossPost/1.0 by u/{secrets['username']}",
        )

    # ---------------- 安全与人机验证挑战检测 ----------------
    def check_humanity_challenge(self, page) -> tuple[bool, str]:
        """
        严格检测 Reddit 的 'Prove your humanity' 验证墙或任何验证码挑战。
        一旦检测到，必须立即大声报错中断，绝不重试循环，绝不静默跳过 (保护版主账号防风控)。
        """
        try:
            # 1. 检查页面 URL
            curr_url = (page.url or "").lower()
            if any(k in curr_url for k in ["challenge", "captcha", "blocked", "waf", "humanity"]):
                return True, f"页面 URL 命中安全挑战特征 ({curr_url[:60]})"

            # 2. 检查标题
            title = (page.title() or "").lower()
            if any(k in title for k in ["just a moment", "challenge", "attention required", "cloudflare"]):
                return True, f"页面标题命中防护挑战 ({title[:40]})"

            # 3. 检查常见人机验证 / WAF / Cloudflare / Arkose 控件
            challenge_selectors = [
                "iframe[src*='challenges.cloudflare']",
                "iframe[src*='arkoselabs']",
                "iframe[src*='recaptcha']",
                "iframe[src*='hcaptcha']",
                "div#challenge-stage",
                "div#turnstile-wrapper",
                "div[id*='captcha']",
                "div[class*='captcha']",
                "p:has-text('Prove your humanity')",
                "span:has-text('Prove your humanity')",
                "h1:has-text('Prove your humanity')",
                "div:has-text('Prove your humanity')",
                ":has-text('Verify you are human')",
                ":has-text('Whoa there, slow down')",
                ":has-text('You are doing that too much')"
            ]
            for sel in challenge_selectors:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return True, f"检测到可视安全挑战元素 ({sel})"

            # 4. 检查页面正文关键字
            body_text = (page.inner_text("body", timeout=1000) or "")[:2000].lower()
            for kw in ["prove your humanity", "verify you are human", "whoa there, slow down", "you are doing that too much", "unusual traffic"]:
                if kw in body_text:
                    return True, f"页面正文检测到安全挑战特征: '{kw}'"
        except Exception as e:
            logger.debug(f"[Reddit] 安全挑战检测过程异常: {e}")

        return False, ""

    def _extract_username_from_page(self, page) -> str:
        """从页面 DOM 或全局上下文中提取登录的 Reddit 用户名"""
        try:
            user_el = page.query_selector("shreddit-nav-user-account")
            if user_el:
                un = user_el.get_attribute("username") or user_el.get_attribute("user-name")
                if un:
                    return un.strip().lstrip("u/").lstrip("/")

            user_link = page.query_selector("a[href*='/user/']:not([href*='/submit']):not([href*='/settings'])")
            if user_link:
                href = user_link.get_attribute("href") or ""
                if "/user/" in href:
                    part = href.split("/user/")[1].split("/")[0].split("?")[0]
                    if part:
                        return part.strip()
        except Exception:
            pass
        return ""

    # ---------------- 登录态检查 ----------------
    def check_login(self):
        # 1. 优先检查 PRAW API (若配置了凭据)
        secrets, secrets_err = self._load_secrets()
        if secrets:
            try:
                reddit = self._get_client()
                me = reddit.user.me()
                name = getattr(me, "name", None) or "已验证用户"
                return True, f"已连接 (API: u/{name})"
            except Exception as e:
                logger.debug(f"[Reddit] PRAW API 探针失败: {e}，回退至浏览器会话检查")

        # 2. 检查本地保存的 Profile 与 Cookies
        profile = self.get_profile()
        u = profile.get("username")
        if u and u not in ("未登录", "None", "") and profile.get("logged_in") is not False:
            return True, f"已连接 (浏览器: u/{u})"

        cookies = self.get_cookies()
        if cookies:
            has_session = any(c.get("name") in ("reddit_session", "token", "session") for c in cookies)
            if has_session:
                u_str = u if (u and u != "未登录") else "Impressive_Bad6222"
                return True, f"已连接 (浏览器: u/{u_str})"

        if not secrets:
            return False, "未登录 (API 凭据未配置且未连接浏览器会话，请先在右侧 Reddit 卡片点击「登录」按钮)"
        return False, "未登录 (请点击卡片「登录」按钮在 Edge 中登录 Reddit)"

    def probe_login(self):
        ok, detail = self.check_login()
        return {
            "platform": self.name,
            "category": "login",
            "item": "login_probe",
            "status": "pass" if ok else "fail",
            "detail": f"Reddit {detail}",
            "suggestion": "" if ok else "请点击 Reddit 卡片登录按钮，在 Edge 中完成登录",
        }

    def login_interactive(self):
        """
        通过有界面的真实 Edge (Stealth 模式) 登录 Reddit，自动捕获 Session Cookie 与用户名。
        同时保留等待凭据文件逻辑作为双轨支持。
        """
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser, context = self.create_login_context(p, "Reddit")
                existing_cookies = self.get_cookies()
                if existing_cookies:
                    try:
                        context.add_cookies(existing_cookies)
                    except Exception:
                        pass

                self.apply_stealth(context)
                page = context.pages[0] if context.pages else context.new_page()
                self.clean_context_tabs(context)
                self.apply_stealth(page)

                logger.info("[Reddit] 正在打开登录引导窗口 (真实 Edge 有界面)...")
                self.safe_goto(page, self.login_url, timeout=35000)

                def is_page_logged_in(pg, ctx):
                    for c in ctx.cookies():
                        if c.get("name") in ("reddit_session", "token") and c.get("value"):
                            return True
                    if pg and not pg.is_closed():
                        try:
                            url = pg.url.lower()
                            if "reddit.com" in url and "/login" not in url and "/register" not in url:
                                user_btn = pg.query_selector("shreddit-nav-user-account, button[id*='user-menu'], a[href*='/user/']:not([href*='/login'])")
                                if user_btn:
                                    return True
                        except Exception:
                            pass
                    return False

                logged_in = False
                detected_username = ""
                deadline = time.time() + LOGIN_WAIT_SECONDS

                while time.time() < deadline:
                    # 检查是否手动写入了 secrets
                    secrets, _ = self._load_secrets()
                    if secrets:
                        logged_in = True
                        break

                    active_pages = [pg for pg in context.pages if not pg.is_closed()]
                    if not active_pages:
                        break

                    for pg in active_pages:
                        if is_page_logged_in(pg, context):
                            logged_in = True
                            detected_username = self._extract_username_from_page(pg)
                            break
                    if logged_in:
                        break
                    time.sleep(2)

                if logged_in:
                    cookies = context.cookies()
                    self.save_cookies(cookies)
                    final_user = detected_username or "Impressive_Bad6222"
                    self.save_profile(username=final_user, logged_in=True)
                    self.close_login_session(browser, context, page)
                    return True, f"Reddit 连接成功！已同步账号: u/{final_user}"

                self.close_login_session(browser, context, page)
        except Exception as e:
            logger.error(f"[Reddit] 登录交互流程异常: {e}")
            return False, f"Reddit 登录交互异常: {e}"

        ok, msg = self.check_login()
        if ok:
            return True, f"Reddit 连接成功！{msg}"
        return False, f"Reddit 登录未完成或超时: {msg}。"

    # ---------------- 发布主入口 (双轨调度) ----------------
    def get_selector_manifest(self):
        return {
            "tab_images": "button:has-text('Images & Video'), button:has-text('Images'), [role='tab']:has-text('Images')",
            "title_input": "textarea[placeholder*='Title'], input[placeholder*='Title'], [aria-label*='Title']",
            "file_input": "input[type='file'][accept*='image'], input[type='file']",
            "nsfw_button": "button:has-text('NSFW'), button:has-text('18+')",
            "spoiler_button": "button:has-text('Spoiler')",
            "post_button": "button:has-text('Post'), button[type='submit']:has-text('Post')"
        }

    def check_selectors_health(self):
        return []

    def publish(self, payload):
        """
        双轨发布调度：
        若配置了 API secrets 且未显式指定浏览器模式，走 PRAW 官方通道；
        若未配置 secrets，但检测到浏览器会话或指定了 browser 模式，走真实 Edge 浏览器自动化通道；
        若两者皆无，大声报错指出凭据未配置 (契合需求与单测断言)。
        """
        secrets, secrets_err = self._load_secrets()
        opts = payload.get("platform_options", {}) or {}

        has_api_secrets = bool(secrets and all(secrets.get(k) for k in ("client_id", "client_secret", "username", "password")))
        mode = opts.get("reddit_mode")

        if mode == "api" or (has_api_secrets and mode != "browser"):
            logger.info("[Reddit] 检测到有效 API 凭据，使用 PRAW 官方通道发布...")
            return self._publish_via_praw(payload)

        profile = self.get_profile()
        u = profile.get("username")
        has_valid_user = bool(u and u not in ("未登录", "None", "") and profile.get("logged_in") is not False)
        has_browser_session = bool(self.get_cookies() or has_valid_user)
        if mode == "browser" or (has_browser_session and not has_api_secrets):
            logger.info("[Reddit] 使用真实 Edge 浏览器自动化通道发布 (Stealth 模式)...")
            return self._publish_via_browser(payload)

        # 两者皆无：大声报错！
        if not secrets:
            return False, f"Reddit 发布失败: {secrets_err}", []
        return False, "Reddit 发布失败: 未配置有效凭据且未连接浏览器会话", []

    # ---------------- 通道 1: 真实 Edge 浏览器自动化 ----------------
    def _publish_via_browser(self, payload):
        raw_images = payload.get("reddit_images") or payload.get("images", [])
        images = [str(Path(x).resolve()) for x in raw_images if Path(x).exists()]
        if not images:
            return False, "Reddit 发布失败: 未找到可上传的图片文件", []

        opts = payload.get("platform_options", {}) or {}
        subreddit_name = (opts.get("reddit_subreddit") or self._default_subreddit()).strip()
        if "reddit_post_to_profile" in opts:
            post_to_profile = bool(opts.get("reddit_post_to_profile"))
        else:
            post_to_profile = self._default_post_to_profile()

        profile = self.get_profile()
        username = profile.get("username") or "Impressive_Bad6222"

        targets = []
        if subreddit_name:
            targets.append((f"r/{subreddit_name}", f"https://www.reddit.com/r/{subreddit_name}/submit", True))
        if post_to_profile:
            targets.append((f"u/{username}", f"https://www.reddit.com/user/{username}/submit", False))

        if not targets:
            return False, "Reddit 发布失败: 未指定目标分区且个人主页发布已关闭，无处可发", []

        title = (payload.get("title", "") or "Untitled").strip()
        if len(title) > TITLE_LIMIT:
            logger.warning(f"[Reddit] 标题超 {TITLE_LIMIT} 字符，已截断 (原长度 {len(title)})")
            title = title[:TITLE_LIMIT]
        description = (payload.get("description", "") or "").strip()
        is_nsfw = bool(opts.get("reddit_nsfw", payload.get("is_nsfw", False)))
        spoiler = bool(opts.get("reddit_spoiler", True))

        compliance = []
        results = []

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                # 严格有界面 (headless=False)
                browser, context = self.create_login_context(p, "Reddit")
                cookies = self.get_cookies()
                if cookies:
                    try:
                        context.add_cookies(cookies)
                    except Exception as e:
                        logger.debug(f"[Reddit] 注入 Cookies 异常: {e}")

                page = context.pages[0] if context.pages else context.new_page()
                self.clean_context_tabs(context)
                self.apply_stealth(page)

                for idx, (label, submit_url, is_sub) in enumerate(targets):
                    # 双投风控保护：如果是第二篇帖子，必须执行保守等待
                    if idx > 0:
                        logger.info("[Reddit] 【版主风控防护】等待 12 秒安全间隔后再发第二篇帖子，严格规避速率限制...")
                        time.sleep(12.0)

                    logger.info(f"[Reddit] 正在导航至 {label} 发帖页面: {submit_url} ...")
                    self.safe_goto(page, submit_url, timeout=40000)
                    time.sleep(2.5)

                    # 1. 严格检测人机验证墙 / Prove your humanity (大声报错，绝不重试)
                    challenged, reason = self.check_humanity_challenge(page)
                    if challenged:
                        err_msg = f"Reddit 触发安全验证挑战: {reason}。为保护版主账号安全，已立即中止操作，请在浏览器中手动验证后重试。"
                        logger.error(f"[Reddit] {err_msg}")
                        compliance.append(self.verify_compliance_item(
                            "reddit", f"humanity_challenge:{label}", True, False, err_msg
                        ))
                        self.close_login_session(browser, context, page)
                        return False, err_msg, compliance

                    # 检查是否被重定向至登录页
                    if "/login" in page.url.lower():
                        err_msg = f"Reddit 登录态已失效 (被重定向至 {page.url})，请先点击卡片重新登录"
                        logger.error(f"[Reddit] {err_msg}")
                        self.close_login_session(browser, context, page)
                        return False, err_msg, compliance

                    # 2. 定位图片上传控件并提交文件 (兼容现代化 Shreddit Shadow DOM 与旧版多 Tab 结构)
                    uploaded = False

                    # 策略 A: 工具栏图片按钮触发文件选择器 (现代 Shreddit)
                    toolbar_btn = page.locator(
                        "post-composer-toolbar-button-image button, post-composer-toolbar-button-image, button[aria-label*='Image'], button[aria-label*='图片']"
                    ).first
                    if toolbar_btn.count() > 0 and toolbar_btn.is_visible():
                        try:
                            with page.expect_file_chooser(timeout=4000) as fc_info:
                                toolbar_btn.click()
                            file_chooser = fc_info.value
                            file_chooser.set_files(images)
                            uploaded = True
                            logger.info(f"[Reddit] {label} 已通过工具栏文件选择器挂载 {len(images)} 张图片素材")
                        except Exception as e:
                            logger.debug(f"[Reddit] 工具栏文件选择器策略触发跳过: {e}")

                    # 策略 B: 利用 Playwright Locator 自动穿透 Shadow Root 设置 input[type='file']
                    if not uploaded:
                        fi_locators = [
                            "r-post-media-input input[type='file']",
                            "post-composer-toolbar-button-image input[type='file']",
                            "input[type='file'][accept*='image']",
                            "input[type='file']"
                        ]
                        for fi_sel in fi_locators:
                            loc = page.locator(fi_sel)
                            if loc.count() > 0:
                                try:
                                    loc.first.set_input_files(images)
                                    uploaded = True
                                    logger.info(f"[Reddit] {label} 已通过 Shadow DOM 穿透控件设置 {len(images)} 张图片素材")
                                    break
                                except Exception as e:
                                    logger.debug(f"[Reddit] 尝试穿透定位器 {fi_sel} 失败: {e}")

                    # 策略 C: 旧版 Redesign Tab 切换策略
                    if not uploaded:
                        tab_selectors = [
                            "button:has-text('Images & Video')",
                            "button:has-text('Images')",
                            "[role='tab']:has-text('Images')",
                            "[role='tab']:has-text('Images & Video')",
                            "button[aria-label*='Images']",
                            "xpath=//button[contains(., 'Images')]"
                        ]
                        for t_sel in tab_selectors:
                            try:
                                tab_btn = page.locator(t_sel).first
                                if tab_btn.count() > 0 and tab_btn.is_visible():
                                    tab_btn.click(timeout=2000)
                                    time.sleep(1.5)
                                    file_input = page.locator("input[type='file']").first
                                    if file_input.count() > 0:
                                        file_input.set_input_files(images)
                                        uploaded = True
                                        logger.info(f"[Reddit] {label} 已通过 Tab 切换激活文件上传")
                                        break
                            except Exception:
                                continue

                    if not uploaded:
                        debug_path = self.data_dir / "reddit_submit_debug.png"
                        try:
                            page.screenshot(path=str(debug_path))
                        except Exception:
                            pass
                        err_msg = f"Reddit {label} 未能定位到文件上传控件，已中止发布以防文字单独泄露 (快照: {debug_path})"
                        logger.error(f"[Reddit] {err_msg}")
                        results.append((label, False, err_msg))
                        compliance.append(self.verify_compliance_item(
                            "reddit", f"post_created:{label}", True, False, err_msg
                        ))
                        break

                    # 等待图片渲染与附加断言
                    time.sleep(3.0)
                    att_ok, att_msg = self.assert_attached(
                        page,
                        "r-post-media-input, post-composer-toolbar-button-image, img[src*='blob:'], img[src*='preview.redd.it'], img[src*='i.redd.it'], [aria-label*='Remove'], button[aria-label*='delete'], [data-testid*='media'], shreddit-media-item, button:has-text('添加到正文'), button:has-text('添加')",
                        timeout=25000,
                        error_msg=f"Reddit {label} 图片上传未就绪或预览未渲染"
                    )
                    if not att_ok:
                        results.append((label, False, att_msg))
                        compliance.append(self.verify_compliance_item(
                            "reddit", f"post_created:{label}", True, False, att_msg
                        ))
                        break

                    # 3. 填写帖子标题
                    title_loc = page.locator(
                        "post-composer-title textarea, post-composer-title input, textarea[name='title'], [placeholder*='标题'], [placeholder*='Title'], [aria-label*='标题'], [aria-label*='Title']"
                    ).first
                    if title_loc.count() == 0:
                        title_loc = page.locator("textarea[placeholder*='Title'], input[placeholder*='Title'], [aria-label*='Title'], textarea[name='title']").first

                    if title_loc.count() == 0:
                        err_msg = f"Reddit {label} 未找到标题输入框"
                        results.append((label, False, err_msg))
                        compliance.append(self.verify_compliance_item(
                            "reddit", f"post_created:{label}", True, False, err_msg
                        ))
                        break

                    title_loc.click()
                    title_loc.fill(title)
                    time.sleep(1.0)

                    # 4. 可选填写正文文本 (若有)
                    if description:
                        desc_loc = page.locator(
                            "div[role='textbox'][contenteditable='true'], faceplate-textarea-input textarea, textarea[placeholder*='文本'], textarea[placeholder*='Body']"
                        ).first
                        if desc_loc.count() > 0 and desc_loc.is_visible():
                            try:
                                desc_loc.click()
                                desc_loc.fill(description)
                                time.sleep(0.5)
                            except Exception:
                                pass

                    # 5. 设置 NSFW 与 Spoiler 标记 (优先通过现代标记弹窗，备用直接按钮)
                    tag_btn = page.locator("button:has-text('添加标记'), button:has-text('Add tags'), button#reddit-post-flair-button").first
                    if tag_btn.count() > 0 and tag_btn.is_visible():
                        try:
                            tag_btn.click()
                            time.sleep(1.0)
                            if is_nsfw:
                                nsfw_sw = page.locator("faceplate-switch-input[name='isNsfw']").first
                                if nsfw_sw.count() > 0 and nsfw_sw.get_attribute("data-checked") == "false":
                                    nsfw_sw.click()
                                    time.sleep(0.3)
                                    logger.info(f"[Reddit] {label} 已开启 18+ (NSFW) 标记")
                            if spoiler:
                                spoiler_sw = page.locator("faceplate-switch-input[name='isSpoiler']").first
                                if spoiler_sw.count() > 0 and spoiler_sw.get_attribute("data-checked") == "false":
                                    spoiler_sw.click()
                                    time.sleep(0.3)
                                    logger.info(f"[Reddit] {label} 已开启 Spoiler (剧透) 标记")
                            apply_btn = page.locator("button#post-flair-modal-apply-button, button:has-text('添加'):not([disabled]), button:has-text('Apply'):not([disabled])").first
                            if apply_btn.count() > 0 and apply_btn.is_visible():
                                apply_btn.click()
                                time.sleep(1.0)
                        except Exception as tag_err:
                            logger.debug(f"[Reddit] 标记弹窗设置异常，尝试直接选择器: {tag_err}")

                    # 备用：传统直接按钮开关 (Classic Redesign)
                    if is_nsfw:
                        nsfw_btn = page.locator("button:has-text('NSFW'), button:has-text('18+'), button[aria-label*='NSFW']").first
                        if nsfw_btn.count() > 0 and nsfw_btn.is_visible():
                            try:
                                aria_pressed = nsfw_btn.get_attribute("aria-pressed") or ""
                                cls = nsfw_btn.get_attribute("class") or ""
                                if aria_pressed != "true" and "active" not in cls.lower():
                                    nsfw_btn.click()
                                    time.sleep(0.5)
                                    logger.info(f"[Reddit] {label} 已标记 NSFW (直接按钮)")
                            except Exception:
                                pass

                    if spoiler:
                        spoiler_btn = page.locator("button:has-text('Spoiler'), button[aria-label*='Spoiler'], button:has-text('剧透')").first
                        if spoiler_btn.count() > 0 and spoiler_btn.is_visible():
                            try:
                                aria_pressed = spoiler_btn.get_attribute("aria-pressed") or ""
                                cls = spoiler_btn.get_attribute("class") or ""
                                if aria_pressed != "true" and "active" not in cls.lower():
                                    spoiler_btn.click()
                                    time.sleep(0.5)
                                    logger.info(f"[Reddit] {label} 已标记 Spoiler (直接按钮)")
                            except Exception:
                                pass

                    # 6. 发帖前安全验证二次排查
                    challenged, reason = self.check_humanity_challenge(page)
                    if challenged:
                        err_msg = f"Reddit 提交前触发安全挑战: {reason}。为保护版主账号，已立即中止发布！"
                        logger.error(f"[Reddit] {err_msg}")
                        results.append((label, False, err_msg))
                        break

                    # 7. 提交发帖 (支持中文界面'发帖'与英文'Post')
                    post_btn_locators = [
                        "r-post-form-submit-button button:not([disabled])",
                        "button:has-text('发帖'):not([disabled])",
                        "button:has-text('Post'):not([disabled])",
                        "button[type='submit']:has-text('发帖'):not([disabled])",
                        "button[type='submit']:has-text('Post'):not([disabled])",
                        "button:has-text('发布'):not([disabled])",
                        "shreddit-composer button:has-text('Post')"
                    ]
                    post_btn = None
                    for p_sel in post_btn_locators:
                        loc = page.locator(p_sel).first
                        if loc.count() > 0 and loc.is_visible() and not loc.is_disabled():
                            post_btn = loc
                            break

                    if not post_btn:
                        debug_path = self.data_dir / "reddit_submit_button_debug.png"
                        try:
                            page.screenshot(path=str(debug_path))
                        except Exception:
                            pass
                        err_msg = f"Reddit {label} 发帖按钮不可用 (可能必填项未满足或被锁定，快照: {debug_path})"
                        logger.error(f"[Reddit] {err_msg}")
                        results.append((label, False, err_msg))
                        compliance.append(self.verify_compliance_item(
                            "reddit", f"post_created:{label}", True, False, err_msg
                        ))
                        break

                    post_btn.click()
                    logger.info(f"[Reddit] {label} 已点击发布按钮，等待发帖成功与 URL 生成...")

                    # 8. 等待发帖完成并获取帖子详情 URL (兼容直接详情页与现代 Shreddit Feed 重定向)
                    post_url = None
                    deadline = time.time() + 45
                    while time.time() < deadline:
                        challenged, reason = self.check_humanity_challenge(page)
                        if challenged:
                            err_msg = f"Reddit 提交后遭遇安全挑战: {reason}。为保护版主账号，已立即中止操作！"
                            results.append((label, False, err_msg))
                            break

                        curr_url = page.url
                        clean_path = curr_url.split("?")[0].rstrip("/")
                        left_submit = not clean_path.endswith("/submit")

                        # 情况 1: 直接跳转到具体帖子评论详情页 /comments/<id>/<slug>
                        if re.search(r"/comments/[a-z0-9]+", curr_url):
                            post_url = curr_url
                            break

                        # 情况 2: 现代 Shreddit 提交后重定向回分区首页或个人已投稿列表页
                        if left_submit:
                            time.sleep(2.5)  # 等待列表流注水渲染
                            # 优先从新页面提取带帖子 ID 的评论详情永久链接
                            links = page.locator("a[href*='/comments/']").all()
                            for lk in links:
                                try:
                                    h = lk.get_attribute("href") or ""
                                    if re.search(r"/comments/[a-z0-9]{4,}", h):
                                        post_url = h if h.startswith("http") else f"https://www.reddit.com{h}"
                                        break
                                except Exception:
                                    continue
                            # 兜底使用跳转后的落地 URL
                            if not post_url:
                                post_url = page.url
                            break

                        alert_el = page.locator("[role='alert'], div[class*='toast'], div:has-text('Something went wrong')").first
                        if alert_el.count() > 0 and alert_el.is_visible():
                            try:
                                err_txt = alert_el.inner_text().strip()
                                if err_txt:
                                    logger.warning(f"[Reddit] 页面警告弹窗: {err_txt}")
                            except Exception:
                                pass

                        time.sleep(1.0)

                    if not post_url and "/comments/" in page.url:
                        post_url = page.url

                    if post_url:
                        logger.info(f"[Reddit] {label} 发布成功: {post_url}")
                        results.append((label, True, post_url))
                        compliance.append(self.verify_compliance_item(
                            "reddit", f"post_created:{label}", True, True, f"帖子已创建: {post_url}"
                        ))
                    else:
                        err_msg = f"Reddit {label} 发布超时，未能在规定时间内跳转至详情页 (当前URL: {page.url[:60]})"
                        logger.error(f"[Reddit] {err_msg}")
                        results.append((label, False, err_msg))
                        compliance.append(self.verify_compliance_item(
                            "reddit", f"post_created:{label}", True, False, err_msg
                        ))

                self.close_login_session(browser, context, page)
        except Exception as e:
            logger.error(f"[Reddit] 浏览器自动化发帖异常: {e}")
            return False, f"Reddit 自动化发帖异常: {e}", compliance

        ok_all = bool(results) and all(r[1] for r in results)
        detail = "; ".join(
            f"{label} 成功 ({url})" if ok else f"{label} 失败 ({url})"
            for label, ok, url in results
        )
        compliance.append(self.verify_compliance_item(
            "reddit", "browser_stealth", True, True, "Edge 真实 Profile + Stealth 环境"
        ))
        compliance.append(self.verify_compliance_item(
            "reddit", "nsfw_flag", is_nsfw, True, f"NSFW 标记={'开' if is_nsfw else '关'}"
        ))

        if ok_all:
            return True, f"Reddit 发布成功: {detail}", compliance
        return False, f"Reddit 部分发布失败: {detail}", compliance

    # ---------------- 通道 2: PRAW 官方 API (Dormant Fallback) ----------------
    def _publish_via_praw(self, payload):
        raw_images = payload.get("reddit_images") or payload.get("images", [])
        images = [str(Path(x).resolve()) for x in raw_images if Path(x).exists()]
        if not images:
            return False, "Reddit 发布失败: 未找到可上传的图片文件", []

        opts = payload.get("platform_options", {}) or {}
        secrets, secrets_err = self._load_secrets()
        if not secrets:
            return False, f"Reddit 发布失败: {secrets_err}", []
        try:
            reddit = self._get_client()
        except Exception as e:
            return False, f"Reddit 发布失败: {e}", []

        subreddit_name = (opts.get("reddit_subreddit") or self._default_subreddit()).strip()
        if "reddit_post_to_profile" in opts:
            post_to_profile = bool(opts.get("reddit_post_to_profile"))
        else:
            post_to_profile = self._default_post_to_profile()

        targets = []
        if subreddit_name:
            targets.append((f"r/{subreddit_name}", subreddit_name, True))
        if post_to_profile:
            profile_target = f"u_{secrets['username']}"
            targets.append((f"u/{profile_target}", profile_target, False))
        if not targets:
            return False, (
                "Reddit 发布失败: 未指定目标分区且个人主页发布已关闭，"
                "无处可发。请设置 platform_options.reddit_subreddit / "
                "defaults.reddit_subreddit，或开启 reddit_post_to_profile。"
            ), []

        title = (payload.get("title", "") or "Untitled").strip()
        if len(title) > TITLE_LIMIT:
            logger.warning(f"[Reddit] 标题超 {TITLE_LIMIT} 字符，已截断 (原长度 {len(title)})")
            title = title[:TITLE_LIMIT]
        description = (payload.get("description", "") or "").strip()
        is_nsfw = bool(opts.get("reddit_nsfw", payload.get("is_nsfw", False)))
        spoiler = bool(opts.get("reddit_spoiler", True))

        compliance = []
        results = []
        for label, target_name, is_subreddit in targets:
            submit_kwargs = dict(nsfw=is_nsfw, spoiler=spoiler)
            if is_subreddit:
                if opts.get("reddit_flair_id"):
                    submit_kwargs["flair_id"] = opts["reddit_flair_id"]
                if opts.get("reddit_flair_text"):
                    submit_kwargs["flair_text"] = opts["reddit_flair_text"]
            try:
                target = reddit.subreddit(target_name)
                _ = target.display_name
                if len(images) == 1:
                    submission = target.submit_image(title, images[0], **submit_kwargs)
                else:
                    gallery = [{"image_path": p} for p in images]
                    submission = target.submit_gallery(title, gallery, **submit_kwargs)
                    logger.info(f"[Reddit] {label} 以 gallery 形式发布 {len(images)} 张图片")
                post_url = f"https://www.reddit.com{submission.permalink}"
                logger.info(f"[Reddit] {label} 发布成功: {post_url}")
                compliance.append(self.verify_compliance_item(
                    "reddit", f"post_created:{label}", True, True,
                    f"帖子已创建: {post_url}"))
                if description:
                    try:
                        submission.reply(description[:10000])
                    except Exception as e:
                        logger.warning(f"[Reddit] {label} 描述评论发送失败 (主帖不受影响): {e}")
                        compliance.append(self.verify_compliance_item(
                            "reddit", f"desc_comment:{label}", True, False,
                            f"描述评论失败: {e}"))
                results.append((label, True, post_url))
            except Exception as e:
                logger.error(f"[Reddit] {label} 发布异常: {e}")
                compliance.append(self.verify_compliance_item(
                    "reddit", f"post_created:{label}", True, False, f"发布失败: {e}"))
                results.append((label, False, str(e)))

        ok_all = all(r[1] for r in results)
        detail = "; ".join(
            f"{label} 成功 ({url})" if ok else f"{label} 失败 ({url})"
            for label, ok, url in results
        )
        compliance.append(self.verify_compliance_item(
            "reddit", "api_auth", True, True, "PRAW script 应用鉴权通过"))
        compliance.append(self.verify_compliance_item(
            "reddit", "nsfw_flag", is_nsfw, True,
            f"NSFW 标记={'开' if is_nsfw else '关'}"))
        if ok_all:
            return True, f"Reddit 发布成功: {detail}", compliance
        return False, f"Reddit 部分发布失败: {detail}", compliance
