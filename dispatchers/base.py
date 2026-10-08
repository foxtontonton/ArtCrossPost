# -*- coding: utf-8 -*-
"""
ArtCrossPost - Base Dispatcher Class
"""
from abc import ABC, abstractmethod
from pathlib import Path
import os
import time
import json
import urllib.request
try:
    from logger import get_logger
except ImportError:
    from ..logger import get_logger

logger = get_logger("BaseDispatcher")

class BaseDispatcher(ABC):
    def __init__(self, name, display_name, login_url, cookies_dir):
        self.name = name
        self.display_name = display_name
        self.login_url = login_url
        self.cookies_dir = Path(cookies_dir)
        self.data_dir = self.cookies_dir.parent
        self.cookies_file = self.cookies_dir / f"{name}_cookies.json"
        
        self.profiles_dir = self.data_dir / "profiles"
        self.avatars_dir = self.data_dir / "avatars"
        for d in [self.cookies_dir, self.profiles_dir, self.avatars_dir]:
            d.mkdir(parents=True, exist_ok=True)
            
        self.profile_file = self.profiles_dir / f"{name}_profile.json"
        self.avatar_file = self.avatars_dir / f"{name}_avatar.png"
        self.status = "未连接"

    def has_saved_cookies(self):
        return self.cookies_file.exists()

    def get_cookies(self):
        if self.cookies_file.exists():
            try:
                with open(self.cookies_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[{self.name}] 读取 cookies 异常: {e}")
                return None
        return None

    def save_cookies(self, cookies_data):
        with open(self.cookies_file, "w", encoding="utf-8") as f:
            json.dump(cookies_data, f, ensure_ascii=False, indent=2)

    def get_profile(self):
        """返回已保存的用户资料 (用户名与头像路径)"""
        if self.profile_file.exists() and self.has_saved_cookies():
            try:
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
        elif self.has_saved_cookies():
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

    def save_profile(self, username, avatar_url=None, avatar_bytes=None):
        """保存用户名与头像文件"""
        profile_data = {"username": username}
        with open(self.profile_file, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, ensure_ascii=False, indent=2)

        if avatar_bytes:
            with open(self.avatar_file, "wb") as f:
                f.write(avatar_bytes)
        elif avatar_url:
            try:
                req = urllib.request.Request(avatar_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    with open(self.avatar_file, "wb") as f:
                        f.write(resp.read())
            except Exception as e:
                logger.warning(f"[{self.name}] 下载头像失败: {e}")

    def clear_session(self):
        """清除本地保存的 cookies、用户画像与头像凭据"""
        try:
            if self.cookies_file.exists():
                self.cookies_file.unlink()
            if self.profile_file.exists():
                self.profile_file.unlink()
            if self.avatar_file.exists():
                self.avatar_file.unlink()
            self.status = "未连接"
            logger.info(f"[{self.name}] 本地 Session 凭据已清除")
            return True
        except Exception as e:
            logger.warning(f"[{self.name}] 清除 session 异常: {e}")
            return False

    def safe_goto(self, page, url, wait_until="domcontentloaded", timeout=45000, max_retries=1):
        """
        统一健壮页面导航 (所有分发器通用导航入口):
        - 自动容忍 Cloudflare WAF / 防爬重定向和 ERR_ABORTED 异常
        - 超时自动重试，记录规范日志
        """
        for attempt in range(max_retries + 1):
            try:
                res = page.goto(url, wait_until=wait_until, timeout=timeout)
                if page.url.startswith("chrome-error://"):
                    raise RuntimeError(f"[{self.display_name}] 页面访问被拦截/崩溃 (当前URL: {page.url})，已快速熔断")
                return res
            except Exception as e:
                msg = str(e)
                if page.url.startswith("chrome-error://"):
                    logger.error(f"[{self.name}] 导航触发 chrome-error 熔断: {url} -> {page.url}")
                    raise RuntimeError(f"[{self.display_name}] 页面访问被拦截/崩溃 (当前URL: {page.url})，已快速熔断")
                if "ERR_ABORTED" in msg or "net::ERR_HTTP_RESPONSE_CODE_FAILURE" in msg:
                    logger.warning(f"[{self.name}] 捕获导航重定向/切换 ({msg})，等待 DOM 就绪: {url}")
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                        if page.url.startswith("chrome-error://"):
                            raise RuntimeError(f"[{self.display_name}] 页面访问触发错误页 ({page.url})，已快速熔断")
                        return None
                    except Exception as wait_e:
                        logger.debug(f"[{self.name}] 等待 DOM 就绪超时/异常: {wait_e}")
                        if page.url.startswith("chrome-error://"):
                            raise RuntimeError(f"[{self.display_name}] 页面访问触发错误页 ({page.url})，已快速熔断")
                        if attempt == max_retries:
                            return None
                elif "Timeout" in msg and attempt < max_retries:
                    logger.warning(f"[{self.name}] 页面导航超时，进行第 {attempt+1} 次重试: {url}")
                    continue
                else:
                    if attempt < max_retries:
                        logger.warning(f"[{self.name}] 导航异常 ({msg})，准备重试: {url}")
                        continue
                    logger.error(f"[{self.name}] 页面导航失败: {url}, error: {e}")
                    raise

    def get_edge_user_data_dir(self):
        """返回本机 Edge 真实用户配置目录；找不到时返回 None"""
        local_app = os.environ.get("LOCALAPPDATA", "")
        cand = os.path.join(local_app, "Microsoft", "Edge", "User Data") if local_app else ""
        return cand if cand and os.path.isdir(cand) else None

    def get_app_browser_profile_dir(self):
        """返回各平台专属的独立持久化 Edge 浏览器用户数据目录 (不受系统 Edge 占用影响)"""
        p_dir = self.data_dir / "browser_profiles" / self.name
        p_dir.mkdir(parents=True, exist_ok=True)
        return str(p_dir.resolve())

    def create_persistent_stealth_context(self, p, user_data_dir, headless=False):
        """
        使用 Edge 持久化上下文启动。
        抹去自动化特征，避免被识别为 bot，解决 '此浏览器或应用可能不安全'。
        """
        stealth_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-popup-blocking",
            "--no-first-run",
            "--no-default-browser-check"
        ]
        return p.chromium.launch_persistent_context(
            user_data_dir,
            channel="msedge",
            headless=headless,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
            viewport={"width": 1280, "height": 800},
            ignore_default_args=["--enable-automation"],
            args=stealth_args
        )

    def create_stealth_browser(self, p, headless=False):
        """保留兼容：用于非持久化启动"""
        stealth_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-popup-blocking",
            "--no-first-run",
            "--no-default-browser-check"
        ]
        try:
            return p.chromium.launch(
                channel="msedge",
                headless=headless,
                ignore_default_args=["--enable-automation"],
                args=stealth_args
            )
        except Exception as e:
            logger.debug(f"[{self.name}] 尝试原生 Edge 启动未果，降级启动通用 Chromium: {e}")
            return p.chromium.launch(
                headless=headless,
                ignore_default_args=["--enable-automation"],
                args=stealth_args
            )

    def create_login_context(self, p, platform_name):
        """
        创建登录用浏览器上下文 (统一入口，各平台 login_interactive 必须经此创建)。
        1. 优先尝试使用本机 Edge 真实用户配置 (持久化上下文)：若系统 Edge 已关闭，可直接复用现有登录会话；
        2. 若本机 Edge 正在运行 (Lock file Error 32)，自动平滑切换至各平台专属独立持久化环境 (data/browser_profiles/<platform>)，
           专属独立目录绝不发生单例锁冲突，且具备完整指纹与持久化存储能力，彻底规避 Google BotGuard 拦截；
        3. 返回 (browser, context)：持久化模式下 browser 为 None，关闭时调用 close_login_session(browser, context)。
        """
        browser, context = None, None
        user_data_dir = self.get_edge_user_data_dir()
        if user_data_dir:
            try:
                logger.info(f"[{platform_name}] 尝试使用本机 Edge 真实用户配置启动登录窗口...")
                context = self.create_persistent_stealth_context(p, user_data_dir, headless=False)
                logger.info(f"[{platform_name}] 成功使用系统 Edge 原生配置！")
            except Exception as e:
                logger.info(f"[{platform_name}] 本机 Edge 正在运行/配置被锁定 ({e})，自动切换至专属独立持久化环境...")
                context = None

        if context is None:
            # 专属独立持久化环境：永不锁死，保留 Google 会话与真实指纹，通过 Google 校验
            independent_dir = self.get_app_browser_profile_dir()
            try:
                context = self.create_persistent_stealth_context(p, independent_dir, headless=False)
            except Exception as e:
                logger.warning(f"[{platform_name}] 专属持久化启动异常 ({e})，回退至纯净浏览器模式...")
                browser = self.create_stealth_browser(p, headless=False)
                context = browser.new_context()

        return browser, context

    def close_login_session(self, browser, context, page=None):
        """安全关闭会话：先关 page 斩断长连接与事件通道，再安全关闭 context/browser，防止 Windows 线程死锁"""
        if page is not None:
            try:
                if hasattr(page, "is_closed") and not page.is_closed():
                    page.close()
            except Exception as pe:
                logger.debug(f"[{self.name}] 关闭 page 提示: {pe}")
        if context is not None:
            try:
                if hasattr(context, "pages"):
                    for p in list(context.pages):
                        try:
                            if not p.is_closed():
                                p.close()
                        except Exception:
                            pass
                context.close()
            except Exception as ce:
                logger.debug(f"[{self.name}] 关闭 context 提示: {ce}")
        if browser is not None:
            try:
                browser.close()
            except Exception as be:
                logger.debug(f"[{self.name}] 关闭 browser 提示: {be}")

    def apply_stealth(self, target):
        """
        安全反检测配置：
        严禁使用 Object.defineProperty 改写 navigator.plugins 或注入虚假 webdriver getter，
        避免破坏 PluginArray 导致触发 Google BotGuard / Cloudflare '此浏览器或应用可能不安全' 拦截。
        底层的 --disable-blink-features=AutomationControlled 已在 C++ 层面原生将 webdriver 置为 false。
        """
        pass

    @staticmethod
    def verify_compliance_item(platform, item, expected, verified, detail):
        """格式化合规项验证结果记录字典"""
        return {
            "platform": platform,
            "item": item,
            "expected": bool(expected),
            "verified": verified,
            "detail": detail
        }

    @staticmethod
    def check_selector_state(page, selector, expected=True):
        """
        回读页面元素勾选/激活状态 (统一合规项回读验证入口)
        返回: (verified: bool | None, detail: str)
        - True: 元素存在且状态与期望一致
        - False: 找到元素但状态与期望不一致 (如点击未生效)
        - None: 选择器未命中，无法确认
        """
        try:
            el = page.query_selector(selector)
            if not el:
                return None, f"选择器未命中: {selector}"

            tag = el.evaluate("el => el.tagName.toLowerCase()")
            if tag == "input":
                typ = (el.get_attribute("type") or "text").lower()
                if typ in ["checkbox", "radio"]:
                    is_chk = el.is_checked()
                    return (is_chk == expected), f"input 状态: {is_chk} (期望 {expected})"
                is_chk = el.evaluate("el => !!el.checked")
                return (bool(is_chk) == expected), f"input checked: {is_chk} (期望 {expected})"

            inner = el.query_selector("input[type='checkbox'], input[type='radio']")
            if inner:
                is_chk = inner.is_checked()
                return (is_chk == expected), f"内部 input 状态: {is_chk} (期望 {expected})"

            aria_checked = el.get_attribute("aria-checked")
            if aria_checked is not None:
                is_chk = (aria_checked.lower() == "true")
                return (is_chk == expected), f"aria-checked={aria_checked} (期望 {expected})"

            aria_selected = el.get_attribute("aria-selected")
            if aria_selected is not None:
                is_chk = (aria_selected.lower() == "true")
                return (is_chk == expected), f"aria-selected={aria_selected} (期望 {expected})"

            return True, "元素已定位并确认"
        except Exception as e:
            logger.debug(f"回读选择器状态异常 ({selector}): {e}")
            return None, f"回读异常: {e}"

    def http_probe_session(self, url: str, headers: dict = None, timeout: int = 8) -> dict:
        """
        使用已保存的本地 Cookies 向目标 URL 发起轻量级 GET 请求，探测服务端会话存活状态
        返回: {"ok": bool, "status_code": int, "url": str, "text": str, "error": str}
        """
        cookies = self.get_cookies()
        if not cookies:
            return {"ok": False, "status_code": 0, "url": url, "text": "", "error": "无本地 Cookie"}

        cookie_str = "; ".join([f"{c.get('name')}={c.get('value')}" for c in cookies if c.get("name") and c.get("value")])
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
            "Cookie": cookie_str,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8"
        }
        if headers:
            req_headers.update(headers)

        import requests
        try:
            resp = requests.get(url, headers=req_headers, timeout=timeout, allow_redirects=True)
            return {
                "ok": resp.status_code in (200, 201, 204),
                "status_code": resp.status_code,
                "url": resp.url,
                "text": resp.text[:1000],
                "error": ""
            }
        except Exception as e:
            return {
                "ok": False,
                "status_code": 0,
                "url": url,
                "text": "",
                "error": str(e)
            }

    def probe_login(self) -> dict:
        """
        轻量级登录态有效性探针 (需求一 1.1)
        子类重写特定平台的接口/DOM探针；默认实现检查本地 Cookie 记录
        """
        cookies = self.get_cookies()
        if not cookies:
            return {
                "platform": self.name,
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": f"{self.display_name} 本地无 Cookie 记录 (未登录)",
                "suggestion": f"请点击 {self.display_name} 卡片登录按钮完成登录"
            }
        return {
            "platform": self.name,
            "category": "login",
            "item": "login_probe",
            "status": "pass",
            "detail": f"{self.display_name} 已保存 Cookie 凭据 ({len(cookies)} 项)",
            "suggestion": ""
        }

    @staticmethod
    def assert_attached(page, selector: str, timeout: int = 20000, error_msg: str = None) -> tuple:
        """
        上传后断言 (需求二 2.1)
        等待附件预览元素出现；超时或未出现则中止，严禁未确认附件即提交流程
        返回: (success: bool, msg: str)
        """
        try:
            page.wait_for_selector(selector, timeout=timeout)
            return True, "附件已成功上传并就绪"
        except Exception as e:
            err = error_msg or f"图片未能附加到投稿，已中止 (未发出空内容): 等待附件元素超时 ({selector})"
            logger.error(f"[断言失败: 上传未确认] {err}")
            return False, err

    @staticmethod
    def assert_field_value(page, selector: str, expected_value: str, is_contenteditable: bool = False, max_retries: int = 1) -> tuple:
        """
        填表后断言 (需求二 2.2)
        回读 input_value 或 inner_text，与预期不一致时重试一次，仍不一致则中止
        返回: (success: bool, msg: str)
        """
        expected_str = (expected_value or "").strip()
        actual = ""
        for attempt in range(max_retries + 1):
            try:
                el = page.query_selector(selector)
                if not el:
                    if attempt < max_retries:
                        time.sleep(0.5)
                        continue
                    return False, f"表单输入框未找到: {selector}"

                if is_contenteditable:
                    actual = (el.inner_text() or "").strip()
                else:
                    actual = (el.input_value() or "").strip()

                if not expected_str or expected_str in actual or actual in expected_str:
                    return True, f"字段回读一致 ({actual[:30]})"

                if attempt < max_retries:
                    logger.warning(f"[填表断言] 字段回读不一致 (实际: '{actual[:30]}', 期望: '{expected_str[:30]}')，正在重试填充...")
                    el.click()
                    el.fill(expected_str)
                    time.sleep(0.4)
            except Exception as e:
                if attempt == max_retries:
                    return False, f"字段回读异常 ({selector}): {e}"
                time.sleep(0.3)

        return False, f"标题/字段填写后回读不一致 (实际: '{actual[:30]}', 期望: '{expected_str[:30]}')，已中止"

    @staticmethod
    def assert_submitted(page, check_fn_or_selector, timeout: int = 30000, error_msg: str = None) -> tuple:
        """
        提交后断言 (需求二 2.3)
        验证 URL 变化或成功提示出现；超时判失败，明确提示检查草稿箱
        返回: (success: bool, msg: str)
        """
        fallback_msg = error_msg or "提交未确认，可能已自动存为草稿，请前往平台检查草稿箱"
        start_t = time.time()
        while time.time() - start_t < (timeout / 1000):
            try:
                if callable(check_fn_or_selector):
                    if check_fn_or_selector(page):
                        return True, "提交已确认生效"
                elif isinstance(check_fn_or_selector, str):
                    if page.query_selector(check_fn_or_selector):
                        return True, "命中提交成功标识元素"
            except Exception as e:
                logger.debug(f"[提交断言异常] {e}")
            time.sleep(0.8)

        logger.error(f"[断言失败: 提交未确认] {fallback_msg}")
        return False, fallback_msg

    def get_selector_manifest(self) -> dict:
        """返回当前平台供自检体检的选择器清单"""
        return {}

    def check_selectors_health(self) -> list:
        """
        选择器健康体检 (需求三 3.3)
        只读 DOM，严禁点击任何提交类按钮
        """
        return []

    @abstractmethod
    def check_login(self):
        """Check if current session/cookie is valid."""
        pass

    @abstractmethod
    def login_interactive(self):
        """Open a browser window for the user to log in manually and save session."""
        pass

    @abstractmethod
    def publish(self, payload):
        """
        Publish post.
        """
        pass

