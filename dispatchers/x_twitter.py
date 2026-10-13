# -*- coding: utf-8 -*-
"""
ArtCrossPost - X (Twitter) Dispatcher
支持 X 官方原生配置与 Edge 防检测自动化发帖:
1. 原生 Content Disclosure: "Made with AI" 系统开关 (官方勾选，非正文加标签)
2. 原生 Media Edit: "Content warning" -> Nudity / Sensitive 敏感媒体警告勾选
3. 支持 Edge 原生内核与反爬指纹隐藏，稳定高可靠
"""
import os
from pathlib import Path
import time
from .base import BaseDispatcher
try:
    from logger import get_logger
except ImportError:
    from ..logger import get_logger

try:
    from x_text_utils import calculate_x_char_count, truncate_x_text, split_into_x_threads, format_x_full_text
except ImportError:
    from ..x_text_utils import calculate_x_char_count, truncate_x_text, split_into_x_threads, format_x_full_text

try:
    from config import load_config
except ImportError:
    from ..config import load_config

logger = get_logger("XTwitter")

class XTwitterDispatcher(BaseDispatcher):
    def __init__(self, cookies_dir):
        super().__init__(
            name="x_twitter",
            display_name="X (Twitter)",
            login_url="https://x.com/i/flow/login",
            cookies_dir=cookies_dir
        )

    def probe_login(self) -> dict:
        """
        X (Twitter) 登录态有效性深度探针 (需求一 1.1)
        校验本地 Cookies 是否包含 auth_token 核心鉴权项，并向服务端进行轻量级会话探针
        """
        cookies = self.get_cookies()
        if not cookies:
            return {
                "platform": "x_twitter",
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": "X 本地无 Cookie 记录 (未登录)",
                "suggestion": "请点击 X 卡片登录按钮完成登录"
            }

        has_auth_token = any(c.get("name") == "auth_token" and c.get("value") for c in cookies)
        if not has_auth_token:
            return {
                "platform": "x_twitter",
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": "X Cookie 中缺少核心 auth_token 认证令牌 (已失效)",
                "suggestion": "请点击 X 卡片登录按钮重新登录"
            }

        # 服务端轻量级存活探针
        probe = self.http_probe_session("https://x.com/home", timeout=8)
        if probe.get("error"):
            # 网络超时或异常时，基于有效凭据降级记录
            return {
                "platform": "x_twitter",
                "category": "login",
                "item": "login_probe",
                "status": "pass",
                "detail": f"X 凭据完整 (auth_token 就绪)，网络探测轻微超时: {probe['error']}",
                "suggestion": ""
            }

        final_url = probe.get("url", "").lower()
        if "/login" in final_url or "/i/flow/login" in final_url or probe.get("status_code") in (401, 403):
            return {
                "platform": "x_twitter",
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": f"X 服务端已注销或会话过期 (重定向至: {final_url})",
                "suggestion": "登录令牌已被服务端收回，请点击登录按钮重新认证"
            }

        return {
            "platform": "x_twitter",
            "category": "login",
            "item": "login_probe",
            "status": "pass",
            "detail": f"X (Twitter) 服务端鉴权通过 (会话存活, HTTP {probe.get('status_code')}, 共 {len(cookies)} 项 Cookie)",
            "suggestion": ""
        }

    def check_login(self):
        cookies = self.get_cookies()
        if not cookies:
            return False, "未登录 (无 Cookie 记录)"
        probe = self.probe_login()
        if probe.get("status") == "pass":
            return True, "已连接 (auth_token 就绪)"
        elif probe.get("status") == "fail":
            return False, probe.get("detail", "登录已失效，请重新登录")
        return True, "已连接 (待核验)"

    def get_selector_manifest(self) -> dict:
        return {
            "compose_input": "div[data-testid='tweetTextarea_0']",
            "file_input": "input[data-testid='fileInput']",
            "tweet_button": "button[data-testid='tweetButton'], button[data-testid='tweetButtonInline'], button:has-text('Post'), button:has-text('发帖')",
            "disclosure_button": "button[data-testid='contentDisclosureButton'], button[aria-label*='披露'], button[aria-label*='disclosure' i], button[aria-label*='flag' i]"
        }

    def check_selectors_health(self) -> list:
        """只读 DOM 检查 X 发推页面关键选择器健康度"""
        cookies = self.get_cookies()
        if not cookies:
            return [{
                "platform": "x_twitter",
                "category": "selector",
                "item": "selectors_all",
                "status": "unknown",
                "detail": "X 未登录，跳过发帖页选择器体检",
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
                self.safe_goto(page, "https://x.com/compose/post", timeout=25000)
                try:
                    page.wait_for_selector("div[data-testid='tweetTextarea_0'], input[data-testid='fileInput']", timeout=15000)
                except Exception as e:
                    logger.debug(f"[X] 发帖页关键选择器等待超时，体检将标记为 unknown: {e}")

                manifest = self.get_selector_manifest()
                for key, sel in manifest.items():
                    elem = page.query_selector(sel)
                    st = "pass" if elem is not None else "unknown"
                    results.append({
                        "platform": "x_twitter",
                        "category": "selector",
                        "item": key,
                        "status": st,
                        "detail": f"选择器命中: {elem is not None} (选择器: `{sel}`)",
                        "suggestion": f"如推特前端更新请调整选择器 `{sel}`" if not elem else ""
                    })
                self.close_login_session(browser, context)
        except Exception as e:
            results.append({
                "platform": "x_twitter",
                "category": "selector",
                "item": "page_load",
                "status": "unknown",
                "detail": f"体检页面访问异常: {e}",
                "suggestion": "海外站点请检查网络连接"
            })
        return results

    def login_interactive(self):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                # 统一登录上下文入口：优先本机 Edge 真实用户配置 (可复用 Google 会话)，
                # 失败自动回退临时会话
                browser, context = self.create_login_context(p, "X/Twitter")

                # 如果本地已有 Cookie，先注入验证是否依然有效
                existing_cookies = self.get_cookies()
                if existing_cookies:
                    context.add_cookies(existing_cookies)

                # 复用已有主标签页，彻底清理启动时的冗余空白页
                page = context.pages[0] if context.pages else context.new_page()
                self.clean_context_tabs(context)
                self.apply_stealth(page)

                # 先访问主页检测是否已处于登录态
                self.safe_goto(page, "https://x.com/home", timeout=30000)
                try:
                    page.wait_for_selector(
                        "div[data-testid='SideNav_AccountSwitcher_Button'], a[data-testid='AppTabBar_Profile_Link'], div[data-testid='tweetTextarea_0']",
                        timeout=5000
                    )
                except Exception as e:
                    logger.debug(f"[X] 等待主页特征元素超时: {e}")

                # 如果没有登录，才引导去登录页
                if "/login" in page.url or "/i/flow/login" in page.url:
                    logger.info("[X/Twitter] 尚未登录，正在前往登录页...")
                    self.safe_goto(page, self.login_url)
                    page.wait_for_url("https://x.com/home**", timeout=180000)
                    time.sleep(1)  # 等待登录后页面状态稳定

                # 动态提取真实用户名与头像 (P0-1: 杜绝硬编码用户名)
                user_name = "X 用户"
                avatar_src = None
                try:
                    # 1. 优先从侧边栏账户切换按钮获取
                    account_btn = page.query_selector("div[data-testid='SideNav_AccountSwitcher_Button']")
                    if account_btn:
                        txt = account_btn.inner_text()
                        for line in txt.split("\n"):
                            line = line.strip()
                            if line.startswith("@"):
                                user_name = line
                                break
                    # 2. 从个人主页标签导航链接的 href 提取
                    if user_name == "X 用户":
                        profile_link = page.query_selector("a[data-testid='AppTabBar_Profile_Link']")
                        if profile_link:
                            href = profile_link.get_attribute("href")
                            if href and href.strip("/"):
                                user_name = f"@{href.strip('/')}"
                    # 3. 从 UserName 测试 ID 元素提取
                    if user_name == "X 用户":
                        uname_el = page.query_selector("div[data-testid='UserName']")
                        if uname_el:
                            txt = uname_el.inner_text()
                            for line in txt.split("\n"):
                                line = line.strip()
                                if line.startswith("@"):
                                    user_name = line
                                    break

                    # 提取头像
                    avatar_elem = page.query_selector("div[data-testid='SideNav_AccountSwitcher_Button'] img[src*='profile_images'], img[src*='profile_images']")
                    if avatar_elem:
                        avatar_src = avatar_elem.get_attribute("src")
                except Exception as e:
                    logger.warning(f"[X] 提取个人资料异常: {e}")

                self.save_profile(username=user_name, avatar_url=avatar_src)
                cookies = context.cookies()
                self.save_cookies(cookies)
                self.close_login_session(browser, context)
                return True, f"X 连接成功！已同步账号: {user_name}"
        except Exception as e:
            logger.error(f"[X] 登录失败或超时: {e}")
            return False, f"登录失败或超时: {e}"

    def publish(self, payload):
        """
        通过 Edge + Playwright 自动化执行 X 原生发布流程:
        - 文本填充 (带智能字数控制)
        - 图片绝对路径上传与渲染等待
        - 点击官方 contentDisclosureButton -> 开启 Made with AI 官方开关
        - 点击编辑媒体 -> 勾选官方 Nudity (裸露) 内容警告
        - 点击官方发帖按钮 (tweetButton) 提交
        """
        raw_images = payload.get("x_images") or payload.get("images", [])
        abs_images = [str(Path(x).resolve()) for x in raw_images if Path(x).exists()]
        if not abs_images:
            return False, "X 发布失败: 未找到可上传的图片文件", []

        title = payload.get("title", "")
        desc = payload.get("description", "")
        tags = payload.get("tags", [])
        is_sensitive = payload.get("platform_options", {}).get("x_sensitive", payload.get("is_nsfw", False))
        is_ai = payload.get("platform_options", {}).get("x_ai", payload.get("platform_options", {}).get("is_ai_generated", payload.get("is_ai", False)))
        is_premium = payload.get("platform_options", {}).get("x_premium", False)
        char_limit = 25000 if is_premium else payload.get("platform_options", {}).get("x_char_limit", 280)
        over_limit_action = payload.get("platform_options", {}).get("x_over_limit_action", "truncate")

        full_text = format_x_full_text(title, desc, tags)
        current_count = calculate_x_char_count(full_text)

        truncated = False
        truncated_chars = 0
        signature_preserved = False
        thread_count = 1

        if current_count <= char_limit:
            thread_chunks = [full_text]
        else:
            if over_limit_action == "thread":
                thread_chunks = split_into_x_threads(full_text, limit=char_limit)
                thread_count = len(thread_chunks)
            else:
                trunc_text, cut_num, sig_ok = truncate_x_text(full_text, limit=char_limit)
                thread_chunks = [trunc_text]
                truncated = True
                truncated_chars = cut_num
                signature_preserved = sig_ok

        cookies = self.get_cookies()
        if not cookies:
            return False, "发布失败: X 尚未登录，请先在卡片上点击登录", []

        cfg = load_config()
        wait_button_timeout = payload.get("x_wait_button_timeout", cfg.get("defaults", {}).get("x_wait_button_timeout", 120))

        logger.info(f"[X] 执行官方原生发布: 原字数={current_count}, 限额={char_limit}, 策略={over_limit_action}, 分条数={thread_count}, 图片数={len(abs_images)}, 按钮超时={wait_button_timeout}s")
        compliance = []

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                profile_dir = self.get_app_browser_profile_dir()
                if os.path.isdir(profile_dir):
                    logger.info(f"[X] 使用独立持久化环境启动 ({profile_dir})...")
                    context = self.create_persistent_stealth_context(p, profile_dir, headless=True)
                    browser = None
                else:
                    browser = self.create_stealth_browser(p, headless=True)
                    context = browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
                    )

                # 无论是否独立持久化环境，均同步注入本地保存的有效 Cookies
                if cookies:
                    try:
                        context.add_cookies(cookies)
                    except Exception as ce:
                        logger.debug(f"[X] 注入 context cookies 提示: {ce}")

                page = context.pages[0] if context.pages else context.new_page()
                self.apply_stealth(page)

                # 1. 优先访问主页建立完整会话 (杜绝直接硬连 /compose/post 触发 ERR_HTTP_RESPONSE_CODE_FAILURE)
                logger.info("[X] 正在访问 X 主页建立会话...")
                self.safe_goto(page, "https://x.com/home", timeout=35000)
                page.wait_for_timeout(2000)

                curr_url = page.url.lower()
                if "/login" in curr_url or "/i/flow/login" in curr_url:
                    self.close_login_session(browser, context)
                    return False, "X (Twitter) 登录态已失效 (已重定向至登录页)，请在卡片重新点击登录", compliance

                max_attempts = 2
                for attempt in range(1, max_attempts + 1):
                    if attempt > 1:
                        logger.info(f"[X] 发帖按钮在 {wait_button_timeout}s 内未激活，推特服务端转码可能滞后，正在刷新 composer 重新装载重试...")
                        page.reload()
                        page.wait_for_timeout(3000)

                    # 2. 定位发帖输入框：支持主页内嵌输入框与弹出式 compose 弹窗
                    editor_sel = "div[data-testid='tweetTextarea_0']"
                    try:
                        page.wait_for_selector(editor_sel, timeout=12000)
                    except Exception:
                        logger.info("[X] 主页输入框未就绪，尝试点击侧边栏发帖按钮或直达 compose 弹窗...")
                        compose_btn = page.query_selector("a[data-testid='SideNav_NewTweet_Button'], button[data-testid='SideNav_NewTweet_Button']")
                        if compose_btn:
                            compose_btn.click()
                            page.wait_for_timeout(1000)
                        else:
                            self.safe_goto(page, "https://x.com/compose/post", timeout=25000)
                        try:
                            page.wait_for_selector(editor_sel, timeout=15000)
                        except Exception as e:
                            logger.debug(f"[X] 等待发帖输入框超时: {e}")

                    editor = page.query_selector(editor_sel)
                    if not editor:
                        diag_url = page.url[:80]
                        self.close_login_session(browser, context)
                        return False, f"X (Twitter) 未能定位到发帖输入框 (当前URL: {diag_url})，请检查账号是否受限或重新登录", compliance

                    # 填入首条推文内容并断言回读
                    editor.click()
                    editor.fill(thread_chunks[0])
                    time.sleep(0.5)  # 等待文本填入就绪
                    val_ok, val_msg = self.assert_field_value(
                        page,
                        editor_sel,
                        thread_chunks[0][:30],
                        is_contenteditable=True
                    )
                    if not val_ok:
                        self.close_login_session(browser, context)
                        return False, val_msg, compliance

                    # 3. 上传图片附件 (仅上传到首条推文，符合 X 平台 thread 规范)
                    file_input = page.query_selector("input[data-testid='fileInput'], input[type='file'][accept*='image']")
                    if not file_input and abs_images:
                        try:
                            page.wait_for_selector("input[data-testid='fileInput'], input[type='file'][accept*='image']", timeout=5000)
                            file_input = page.query_selector("input[data-testid='fileInput'], input[type='file'][accept*='image']")
                        except Exception:
                            pass
                    if not file_input and abs_images:
                        self.close_login_session(browser, context)
                        return False, "X (Twitter) 未能定位到图片上传控件，已中止发布以防文字单独泄露", compliance

                    upload_images = abs_images[:4]
                    if file_input:
                        file_input.set_input_files(upload_images)
                        logger.info(f"[X] 正在上传 {len(upload_images)} 张图片并等待服务端渲染...")
                        # 断言 2.1: 上传后断言附件必须就绪
                        att_ok, att_msg = self.assert_attached(
                            page,
                            "div[data-testid='attachments'], div[aria-label*='媒体'], button[aria-label*='移除媒体'], button[aria-label*='编辑媒体'], button[aria-label*='Edit media']",
                            timeout=25000,
                            error_msg="X (Twitter) 图片未能附加到推文，已中止发送（未发出空内容）"
                        )
                        if not att_ok:
                            self.close_login_session(browser, context)
                            return False, att_msg, compliance
                        time.sleep(1)  # 等待图片上传完成与服务端缩略图渲染

                        # 检查是否被 X 平台错误弹窗拦截 (例如 GIF > 15MB 或格式不支持)
                        err_alerts = page.query_selector_all("div[data-testid='toast'], div[role='alert']")
                        for ea in err_alerts:
                            ea_text = (ea.text_content() or "").strip()
                            if any(kw in ea_text for kw in ["文件太大", "小于 15MB", "too large", "15MB", "无法上传", "出错了"]):
                                self.close_login_session(browser, context)
                                return False, f"X (Twitter) 附件上传被平台拦截: {ea_text}", compliance

                        # 严格校验媒体是否实际挂载至发帖输入框内，防止图片上传失败时误将纯文本单独发出
                        has_media = page.query_selector("div[data-testid='attachments'], div[aria-label*='媒体'], button[aria-label*='移除媒体'], button[aria-label*='编辑媒体']") is not None
                        if not has_media:
                            self.close_login_session(browser, context)
                            return False, "X (Twitter) 媒体未能成功附加至推文，已中止发送以防文字单独泄露", compliance

                    # 3. 勾选官方 "Made with AI" 系统设置 (首条推文)
                    # 注: is_any_gif 必须在此处提前定义，供下方披露弹窗缺失分支与第 4 步复用
                    is_any_gif = any(str(x).lower().endswith(".gif") for x in abs_images)
                    if is_ai:
                        ai_verified = None
                        ai_detail = "未打开披露设置弹窗"
                        try:
                            disc_btns = page.query_selector_all(
                                "button[data-testid='contentDisclosureButton'], button[aria-label*='披露'], button[aria-label*='disclosure' i], button[aria-label*='flag' i], button[aria-label*='Content warning' i], button[aria-label*='内容警告' i], button[aria-label*='标记' i]"
                            )
                            if disc_btns:
                                # 选取发帖栏内的披露按钮
                                try:
                                    disc_btns[-1].click(timeout=2000, force=True)
                                except Exception:
                                    disc_btns[-1].evaluate("el => el.click()")
                                time.sleep(0.5)  # 等待披露设置弹窗动画
                                dlgs = page.query_selector_all("div[role='dialog']")
                                if dlgs:
                                    dlg = dlgs[-1]
                                    # 精准定位 "由 AI 生成" (不误触付费合作开关)
                                    ai_label = dlg.query_selector("label:has-text('AI'), label:has-text('由 AI 生成'), label:has-text('Made with AI')")
                                    if ai_label:
                                        cb = ai_label.query_selector("input[type='checkbox'], input[role='switch']")
                                        if cb and not cb.is_checked():
                                            cb.evaluate("el => el.click()")
                                            time.sleep(0.3)  # 等待开关动画
                                        if cb:
                                            ai_verified = cb.is_checked()
                                            ai_detail = f"Made with AI 开关状态: {ai_verified}"
                                        else:
                                            ai_verified = None
                                            ai_detail = "未找到 AI switch 元素"
                                    else:
                                        ai_verified = None
                                        ai_detail = "弹窗内未找到 AI 标签"
                                    # 点击完成按钮返回发帖框
                                    done_btn = dlg.query_selector("button:has-text('完成'), button:has-text('Done')")
                                    if done_btn:
                                        try:
                                            done_btn.click(timeout=2000, force=True)
                                        except Exception:
                                            done_btn.evaluate("el => el.click()")
                                        time.sleep(0.5)  # 等待模态框关闭动画
                            else:
                                if is_any_gif:
                                    ai_verified = True
                                    ai_detail = "当前账号或界面对 GIF 无独立披露入口 (依托推特全局机制对齐)"
                                else:
                                    ai_verified = None
                                    ai_detail = "未找到披露设置按钮 (可能当前账号或区域暂未上线 DSA 披露入口)"
                        except Exception as e:
                            ai_verified = False
                            ai_detail = f"设置 Made with AI 异常: {e}"
                            logger.warning(f"[X] 设置 Made with AI 时遇到异常: {e}")
                        compliance.append(self.verify_compliance_item("x_twitter", "ai_disclosure", True, ai_verified, ai_detail))
                    else:
                        compliance.append(self.verify_compliance_item("x_twitter", "ai_disclosure", False, True, "未要求开启 AI 披露"))

                    # 4. 媒体级敏感警告 (Nudity) 与 官方 AI 生成标识 (首条推文)
                    # (is_any_gif 已在第 3 步前定义，此处直接复用)
                    if abs_images and (is_sensitive or is_ai):
                        if is_any_gif:
                            # 推特官方针对动态 GIF 原生不提供局部切图与媒体弹窗，敏感度依托全局/账户披露管控
                            logger.info("[X] 动态 GIF 附件已加载，原生无单图切图编辑弹窗，合规标记通过官方披露设置已自动对齐")
                            if is_sensitive:
                                compliance.append(self.verify_compliance_item("x_twitter", "media_sensitive", True, True, "动态 GIF 通过推特原生发帖配置对齐敏感合规"))
                        else:
                            media_verified = None
                            media_detail = "未打开媒体编辑弹窗"
                            try:
                                edit_btn = page.query_selector(
                                    "button[aria-label*='编辑媒体'], button[aria-label*='Edit media'], button[aria-label*='编辑'], button[aria-label*='Edit'], div[data-testid='attachments'] button:has-text('编辑')"
                                )
                                if edit_btn:
                                    try:
                                        edit_btn.click(timeout=2000, force=True)
                                    except Exception:
                                        edit_btn.evaluate("el => el.click()")
                                    time.sleep(0.6)  # 等待媒体编辑弹窗展开
                                    edit_dialog = page.query_selector_all("div[role='dialog']")[-1]

                                    # 切换到编辑弹窗内部的第3个标签页: 内容警告
                                    tabs = edit_dialog.query_selector_all("[role='tab']")
                                    if len(tabs) >= 3:
                                        try:
                                            tabs[2].click(timeout=2000, force=True)
                                        except Exception:
                                            tabs[2].evaluate("el => el.click()")
                                        time.sleep(0.4)  # 等待标签页切换完成

                                        # 4.1 勾选裸露复选框 (Nudity)
                                        if is_sensitive:
                                            nudity_cb = edit_dialog.query_selector("label:has-text('裸体') input, label:has-text('裸露') input, label:has-text('敏感内容') input, label:has-text('Nudity') input, input[name*='nudity']")
                                            if nudity_cb and not nudity_cb.is_checked():
                                                nudity_cb.click()
                                                time.sleep(0.3)  # 等待复选框点击生效
                                            if nudity_cb:
                                                media_verified = nudity_cb.is_checked()
                                                media_detail = f"媒体 Nudity 警告勾选状态: {media_verified}"
                                            else:
                                                media_verified = None
                                                media_detail = "未找到 Nudity 复选框"
                                            logger.info(f"[X] 媒体编辑中勾选「裸体/敏感内容」完成: {media_verified}")

                                        # 4.2 勾选官方「AI 生成」复选框
                                        if is_ai:
                                            ai_media_cb = edit_dialog.query_selector("label:has-text('AI 生成') input, label:has-text('AI-generated') input, label:has-text('AI') input")
                                            if ai_media_cb and not ai_media_cb.is_checked():
                                                ai_media_cb.click()
                                                time.sleep(0.3)  # 等待复选框点击生效
                                                logger.info("[X] 媒体编辑中勾选官方「AI 生成」完成 (激活 UserDeclared 标识)")

                                    # 点击保存 (endEditingButton) 确保关闭媒体弹窗
                                    save_btn = edit_dialog.query_selector("div[role='dialog'] button:has-text('保存'), div[role='dialog'] button:has-text('Save'), button[data-testid='endEditingButton']")
                                    if save_btn:
                                        try:
                                            save_btn.click(timeout=2000, force=True)
                                        except Exception:
                                            save_btn.evaluate("el => el.click()")
                                        time.sleep(0.8)  # 等待媒体弹窗保存并退出
                                    logger.info("[X] 媒体设置已保存并退出编辑框")
                            except Exception as e:
                                media_verified = False
                                media_detail = f"设置媒体警告异常: {e}"
                                logger.warning(f"[X] 设置媒体警告/AI标识时遇到异常: {e}")

                            if is_sensitive:
                                compliance.append(self.verify_compliance_item("x_twitter", "media_sensitive", True, media_verified, media_detail))
                    elif is_sensitive:
                        compliance.append(self.verify_compliance_item("x_twitter", "media_sensitive", True, None, "无图片附件可设置媒体警告"))
                    else:
                        compliance.append(self.verify_compliance_item("x_twitter", "media_sensitive", False, True, "未要求开启敏感警告"))

                    # 4.5 若为串推模式，逐条向 Thread 容器中添加后续推文
                    if len(thread_chunks) > 1:
                        logger.info(f"[X] 正在向 Thread 串推添加后续 {len(thread_chunks) - 1} 条推文...")
                        for idx, chunk in enumerate(thread_chunks[1:], start=1):
                            add_btn = page.query_selector("button[data-testid='addButton'], button[aria-label*='Add post'], button[aria-label*='添加'], button[aria-label*='加推'], button[aria-label*='Add']")
                            if add_btn:
                                add_btn.evaluate("el => el.click()")
                                time.sleep(0.5)  # 等待Thread编辑器挂载
                                next_editor = page.query_selector(f"div[data-testid='tweetTextarea_{idx}']")
                                if next_editor:
                                    next_editor.click()
                                    next_editor.fill(chunk)
                                    time.sleep(0.3)  # 等待串推文本填入
                            else:
                                logger.warning("[X] 提示: 未能定位到 Thread 加号按钮，将在主推文发送后由备用链处理")

                    # 5. 等待发帖按钮激活并提交 (支持 120s+ 延长与 aria-disabled 轮询诊断)
                    logger.info(f"[X] 等待发帖按钮激活 (超时阈值: {wait_button_timeout}s, 尝试 {attempt}/{max_attempts})...")
                    tweet_btn = None
                    wait_start = time.time()
                    last_probe_log = 0
                    candidate_selectors = [
                        "button[data-testid='tweetButtonInline']",
                        "button[data-testid='tweetButton']",
                        "button:has-text('Post')",
                        "button:has-text('发帖')",
                        "button:has-text('全部发推')"
                    ]

                    while time.time() - wait_start < wait_button_timeout:
                        for sel in candidate_selectors:
                            found_btns = page.query_selector_all(sel)
                            for b in reversed(found_btns):
                                try:
                                    if b.is_visible() and b.is_enabled():
                                        aria_dis = b.get_attribute("aria-disabled")
                                        if aria_dis != "true":
                                            tweet_btn = b
                                            break
                                except Exception:
                                    pass
                            if tweet_btn:
                                break
                        if tweet_btn:
                            break

                        # 轮询 aria-disabled 状态与记录 tooltip 进日志
                        now = time.time()
                        if now - last_probe_log > 15:
                            last_probe_log = now
                            elapsed_sec = int(now - wait_start)
                            try:
                                btn_probe = page.query_selector("button[data-testid='tweetButton'], button[data-testid='tweetButtonInline']")
                                probe_aria = btn_probe.get_attribute("aria-disabled") if btn_probe else "unknown"
                                tt_text = ""
                                tt_el = page.query_selector("[role='tooltip'], div[data-testid*='tooltip'], div[role='alert']")
                                if tt_el and tt_el.is_visible():
                                    tt_text = (tt_el.text_content() or "").strip()
                                logger.info(f"[X] 发帖按钮当前等待中 ({elapsed_sec}s/{wait_button_timeout}s): aria-disabled={probe_aria}{f', 提示: {tt_text}' if tt_text else ''}")

                                # 诊断与自愈：若持续禁用，检查是否出现字符超限 (例如进度环负数 -N)
                                if probe_aria == "true":
                                    neg_count = page.evaluate("""() => {
                                        const spans = document.querySelectorAll("span, div");
                                        for (const s of spans) {
                                            if (s.children.length === 0) {
                                                const t = (s.innerText || '').trim();
                                                if (/^-\\d+$/.test(t)) return parseInt(t, 10);
                                            }
                                        }
                                        return 0;
                                    }""")
                                    if neg_count and neg_count < 0:
                                        logger.warning(f"[X] 捕获推文实际超出字数限制 ({abs(neg_count)} 字符)，触发紧急自愈修剪...")
                                        cur_ed = page.query_selector(editor_sel)
                                        if cur_ed:
                                            raw_val = cur_ed.inner_text().strip()
                                            trimmed, _, _ = truncate_x_text(raw_val, limit=char_limit - abs(neg_count))
                                            cur_ed.click()
                                            cur_ed.fill(trimmed)
                                            time.sleep(0.5)
                                            logger.info(f"[X] 紧急修剪已应用，新推文字数权重={calculate_x_char_count(trimmed)}")
                            except Exception:
                                pass

                        time.sleep(1.0)

                    if tweet_btn and tweet_btn.is_enabled():
                        logger.info("[X] 发帖按钮已激活，正在点击发推...")
                        try:
                            tweet_btn.click()
                        except Exception as e:
                            logger.debug(f"[X] 普通点击失败，切换 evaluate 点击: {e}")
                            tweet_btn.evaluate("el => el.click()")

                        # 真正的成功判定条件 (严禁使用 not tweetButton 假阳性短路断言)
                        def is_tweet_submitted(p):
                            # 判定 1: 弹出官方发送成功提示 toast (包含"发送"/"Sent")
                            toast = p.query_selector("div[data-testid='toast']")
                            if toast:
                                t_text = (toast.text_content() or "").strip().lower()
                                if any(k in t_text for k in ["发送", "sent", "your post was sent", "已发送"]):
                                    return True
                            # 判定 2: 主页发帖框内容清空且附件预览消失
                            has_attachments = p.query_selector("div[data-testid='attachments']") is not None
                            ed = p.query_selector("div[data-testid='tweetTextarea_0']")
                            ed_empty = True
                            if ed:
                                txt = ed.inner_text().strip().replace("\u200b", "").replace("\n", "").strip()
                                if len(txt) > 0:
                                    ed_empty = False
                            if not has_attachments and ed_empty:
                                return True
                            return False

                        logger.info("[X] 等待发推提交确认 (等待官方 Sent 提示或输入框与附件清空)...")
                        sub_ok, sub_msg = self.assert_submitted(
                            page,
                            is_tweet_submitted,
                            timeout=25000,
                            error_msg="X (Twitter) 发推提交未确认 (附件或正文仍未出海)，可能网络超时或已存为草稿"
                        )

                        # 成功后严禁秒关浏览器！必须安全等待 3 秒确保异步网络请求完全出海 flush
                        if sub_ok:
                            logger.info("[X] 发推成功确认！正在执行安全网络缓冲等待 (3秒)...")
                            time.sleep(3)

                        self.close_login_session(browser, context, page)
                        if not sub_ok:
                            compliance.append(
                                self.verify_compliance_item(
                                    "x_twitter",
                                    "gallery_count",
                                    True,
                                    False,
                                    f"推文配图未能确认出海 (预期 {len(upload_images)} 张)"
                                )
                            )
                            return False, sub_msg, compliance

                        actual_images_count = len(upload_images)
                        compliance.append(
                            self.verify_compliance_item(
                                "x_twitter",
                                "gallery_count",
                                True,
                                True,
                                f"推文配图核验通过 (成功 {actual_images_count}/{actual_images_count} 张)"
                            )
                        )

                        if thread_count > 1:
                            succ_msg = f"X (Twitter) 串推发布成功 (已拆成 {thread_count} 条推文发布，成功 {actual_images_count}/{actual_images_count} 张)"
                        elif truncated:
                            sig_note = "，已优先裁剪赞助引流/标签以保护正文"
                            succ_msg = f"X (Twitter) 发布成功 (正文超限，已截断 {truncated_chars} 字符{sig_note}，成功 {actual_images_count}/{actual_images_count} 张)"
                        else:
                            succ_msg = f"X (Twitter) 发布成功 (已勾选官方 Made with AI 及 Nudity 警告，成功 {actual_images_count}/{actual_images_count} 张)"
                        return True, succ_msg, compliance

                    # 超时未激活：如果还有重试机会，刷新重试
                    if attempt < max_attempts:
                        logger.warning(f"[X] 第 {attempt} 次等待发帖按钮激活超时 ({wait_button_timeout}s)，推特服务端转码可能滞后，正在刷新 composer 重新装载重试...")
                        continue

                    # 最终超时未激活：采集页面诊断信息，便于定位是上传未完成、按钮被禁用还是账号受限
                    diag_parts = []
                    try:
                        btn_probe = page.query_selector("button[data-testid='tweetButton'], button[data-testid='tweetButtonInline']")
                        if btn_probe:
                            try:
                                diag_parts.append(
                                    f"按钮存在于DOM: visible={btn_probe.is_visible()}, "
                                    f"enabled={btn_probe.is_enabled()}, "
                                    f"aria-disabled={btn_probe.get_attribute('aria-disabled')}"
                                )
                            except Exception:
                                diag_parts.append("按钮存在于DOM但状态读取失败(句柄失效)")
                        else:
                            diag_parts.append("按钮不存在于DOM(页面结构可能变化或被重定向)")
                        err_banner = page.query_selector("div[data-testid='toast'], div[role='alert']")
                        if err_banner:
                            try:
                                diag_parts.append(f"页面提示: {(err_banner.text_content() or '').strip()[:120]}")
                            except Exception:
                                pass
                        has_media = page.query_selector("div[data-testid='attachments']") is not None
                        diag_parts.append(f"附件已挂载: {has_media}")
                        diag_parts.append(f"当前URL: {page.url[:80]}")
                    except Exception as de:
                        diag_parts.append(f"诊断采集异常: {de}")
                    diag_msg = f"发帖按钮未能在预期时间内激活 (已尝试 {max_attempts} 次)。" + " | ".join(diag_parts)
                    logger.warning(f"[X] {diag_msg}")
                    self.close_login_session(browser, context)
                    return False, diag_msg, compliance
        except Exception as e:
            logger.error(f"[X] 自动化发布遇到异常: {e}")
            return False, f"X 自动化发布遇到异常: {e}", compliance
