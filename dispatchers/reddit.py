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
        if profile.get("logged_in") and profile.get("username"):
            return True, f"已连接 (浏览器: u/{profile['username']})"

        cookies = self.get_cookies()
        if cookies:
            has_session = any(c.get("name") in ("reddit_session", "token", "session") for c in cookies)
            if has_session:
                u = profile.get("username", "Impressive_Bad6222")
                return True, f"已连接 (浏览器: u/{u})"

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
                    self.save_profile(username=final_user)
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

        has_browser_session = bool(self.get_cookies() or self.get_profile().get("logged_in"))
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

                    # 2. 切换至“Images & Video”标签页
                    tab_clicked = False
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
                            tab_btn = page.query_selector(t_sel)
                            if tab_btn and tab_btn.is_visible():
                                tab_btn.click(timeout=2000)
                                tab_clicked = True
                                time.sleep(1.5)
                                break
                        except Exception:
                            continue

                    if not tab_clicked:
                        logger.debug(f"[Reddit] 未显式点击到 Images 标签，尝试直接查找文件上传控件")

                    # 3. 定位图片上传控件并提交文件
                    fi_selectors = [
                        "input[type='file'][accept*='image']",
                        "input[type='file']"
                    ]
                    file_input = None
                    for fi_sel in fi_selectors:
                        try:
                            page.wait_for_selector(fi_sel, timeout=5000)
                            file_input = page.query_selector(fi_sel)
                            if file_input:
                                break
                        except Exception:
                            continue

                    if not file_input:
                        err_msg = f"Reddit {label} 未能定位到文件上传控件，已中止发布以防文字单独泄露"
                        logger.error(f"[Reddit] {err_msg}")
                        results.append((label, False, err_msg))
                        compliance.append(self.verify_compliance_item(
                            "reddit", f"post_created:{label}", True, False, err_msg
                        ))
                        break

                    logger.info(f"[Reddit] {label} 正在上传 {len(images)} 张图片素材...")
                    file_input.set_input_files(images)
                    time.sleep(3.0)

                    # 断言图片已成功附加与预览渲染
                    att_ok, att_msg = self.assert_attached(
                        page,
                        "img[src*='preview.redd.it'], img[src*='i.redd.it'], img[src*='blob:'], [aria-label*='Remove'], button[aria-label*='delete'], [data-testid*='media'], shreddit-media-item",
                        timeout=25000,
                        error_msg=f"Reddit {label} 图片上传未就绪或预览未渲染"
                    )
                    if not att_ok:
                        results.append((label, False, att_msg))
                        compliance.append(self.verify_compliance_item(
                            "reddit", f"post_created:{label}", True, False, att_msg
                        ))
                        break

                    # 4. 填写帖子标题
                    title_sel = "textarea[placeholder*='Title'], input[placeholder*='Title'], [aria-label*='Title'], textarea[name='title']"
                    title_input = page.query_selector(title_sel)
                    if not title_input:
                        err_msg = f"Reddit {label} 未找到标题输入框"
                        results.append((label, False, err_msg))
                        compliance.append(self.verify_compliance_item(
                            "reddit", f"post_created:{label}", True, False, err_msg
                        ))
                        break

                    title_input.click()
                    title_input.fill(title)
                    time.sleep(1.0)

                    # 5. 设置 NSFW 与 Spoiler 标记
                    if is_nsfw:
                        nsfw_btn = page.query_selector("button:has-text('NSFW'), button:has-text('18+'), button[aria-label*='NSFW']")
                        if nsfw_btn:
                            aria_pressed = nsfw_btn.get_attribute("aria-pressed") or ""
                            cls = nsfw_btn.get_attribute("class") or ""
                            if aria_pressed != "true" and "active" not in cls.lower():
                                nsfw_btn.click()
                                time.sleep(0.5)
                                logger.info(f"[Reddit] {label} 已标记 NSFW")

                    if spoiler:
                        spoiler_btn = page.query_selector("button:has-text('Spoiler'), button[aria-label*='Spoiler']")
                        if spoiler_btn:
                            aria_pressed = spoiler_btn.get_attribute("aria-pressed") or ""
                            cls = spoiler_btn.get_attribute("class") or ""
                            if aria_pressed != "true" and "active" not in cls.lower():
                                spoiler_btn.click()
                                time.sleep(0.5)
                                logger.info(f"[Reddit] {label} 已标记 Spoiler")

                    # 6. 发帖前安全验证二次排查
                    challenged, reason = self.check_humanity_challenge(page)
                    if challenged:
                        err_msg = f"Reddit 提交前触发安全挑战: {reason}。为保护版主账号，已立即中止发布！"
                        logger.error(f"[Reddit] {err_msg}")
                        results.append((label, False, err_msg))
                        break

                    # 7. 提交发帖
                    post_btn_selectors = [
                        "button:has-text('Post'):not([disabled])",
                        "button[type='submit']:has-text('Post'):not([disabled])",
                        "button:has-text('发布'):not([disabled])",
                        "shreddit-composer button:has-text('Post')"
                    ]
                    post_btn = None
                    for p_sel in post_btn_selectors:
                        post_btn = page.query_selector(p_sel)
                        if post_btn and post_btn.is_visible() and not post_btn.is_disabled():
                            break

                    if not post_btn:
                        err_msg = f"Reddit {label} 发帖按钮不可用 (可能必填项未满足或被锁定)"
                        logger.error(f"[Reddit] {err_msg}")
                        results.append((label, False, err_msg))
                        compliance.append(self.verify_compliance_item(
                            "reddit", f"post_created:{label}", True, False, err_msg
                        ))
                        break

                    post_btn.click()
                    logger.info(f"[Reddit] {label} 已点击发布按钮，等待发帖成功与 URL 生成...")

                    # 8. 等待成功跳转至评论/详情页
                    post_url = None
                    deadline = time.time() + 35
                    while time.time() < deadline:
                        challenged, reason = self.check_humanity_challenge(page)
                        if challenged:
                            err_msg = f"Reddit 提交后遭遇安全挑战: {reason}"
                            results.append((label, False, err_msg))
                            break

                        curr_url = page.url
                        if "/comments/" in curr_url:
                            post_url = curr_url
                            break

                        alert_el = page.query_selector("[role='alert'], div[class*='toast'], div:has-text('Something went wrong')")
                        if alert_el and alert_el.is_visible():
                            err_txt = alert_el.inner_text().strip()
                            if err_txt:
                                logger.warning(f"[Reddit] 页面警告弹窗: {err_txt}")

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
            submit_kwargs = dict(title=title, nsfw=is_nsfw, spoiler=spoiler)
            if is_subreddit:
                if opts.get("reddit_flair_id"):
                    submit_kwargs["flair_id"] = opts["reddit_flair_id"]
                if opts.get("reddit_flair_text"):
                    submit_kwargs["flair_text"] = opts["reddit_flair_text"]
            try:
                target = reddit.subreddit(target_name)
                _ = target.display_name
                if len(images) == 1:
                    submission = target.submit_image(images[0], **submit_kwargs)
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
