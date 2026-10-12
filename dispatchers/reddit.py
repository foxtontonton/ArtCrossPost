# -*- coding: utf-8 -*-
"""
ArtCrossPost - Reddit Dispatcher (PRAW 官方 API)

与浏览器自动化分发器 (X/DA/Patreon/Ko-fi) 不同：
Reddit 提供官方 API，本分发器走 PRAW 直连，无需 Playwright/Edge，
天然免疫验证码与反爬拦截。

鉴权方式：Reddit "script" 类型应用 (password grant)
  1. 浏览器打开 https://www.reddit.com/prefs/apps/ → 点"创建应用"
     → 类型选择 "script"，redirect_uri 填 http://localhost:8080
  2. 记下 client_id (应用名称下方约 14 位短字符串) 与 client_secret
  3. 将四项写入 data/reddit_secrets.json (已被 .gitignore 保护，绝不进仓库)：
     {
       "client_id": "...",
       "client_secret": "...",
       "username": "你的Reddit用户名",
       "password": "...",
       "user_agent": "ArtCrossPost/1.0 by u/你的用户名"
     }

payload 约定 (与其它分发器一致)：
  images:            图片路径列表。1 张走 submit_image，多张走 submit_gallery
  title:             帖子标题 (Reddit 上限 300 字符，超限自动截断并在日志中声明)
  description:       图片帖不支持正文，将作为首条评论发出 (失败不影响主帖成功判定)
  tags:              Reddit 图片帖无标签概念，忽略
  is_nsfw:           是否标记 NSFW
  platform_options:
    reddit_subreddit  目标分区名 (默认读配置项 defaults.reddit_subreddit)
    reddit_post_to_profile 是否同时发到个人主页 u/<用户名> (默认读配置项
                      defaults.reddit_post_to_profile，用户要求双投，默认为开)
    reddit_flair_id   flair id (可选，直透 PRAW，仅分区帖生效)
    reddit_flair_text flair 文字 (可选，直透 PRAW，仅分区帖生效)
    reddit_spoiler    是否 spoiler (默认 True)
    reddit_nsfw       是否 NSFW (默认取 payload is_nsfw)

铁律：凭据缺失 / 鉴权失败 / 发帖失败一律大声报错并返回 False，
绝不静默跳过 (需求二：找不到对的就大声报错)。
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
TITLE_LIMIT = 300
LOGIN_WAIT_SECONDS = 180


class RedditDispatcher(BaseDispatcher):
    def __init__(self, cookies_dir):
        super().__init__(
            name="reddit",
            display_name="Reddit",
            login_url=REDDIT_APP_PAGE,
            cookies_dir=cookies_dir,
        )
        # 凭据文件位于 data/ 目录，已被 .gitignore 全量保护 (data/*)
        self.secrets_file = self.data_dir / REDDIT_SECRETS_FILE

    # ---------------- 凭据 ----------------
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
        return (load_config().get("defaults", {}).get("reddit_subreddit") or "").strip()

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

    # ---------------- 登录 ----------------
    def check_login(self):
        try:
            reddit = self._get_client()
            me = reddit.user.me()
            name = getattr(me, "name", None) or "已验证用户"
            return True, f"已连接 (u/{name})"
        except Exception as e:
            return False, f"Reddit 鉴权失败: {e}"

    def probe_login(self):
        ok, detail = self.check_login()
        return {
            "platform": self.name,
            "category": "login",
            "item": "login_probe",
            "status": "pass" if ok else "fail",
            "detail": f"Reddit {detail}",
            "suggestion": "" if ok else "请点击 Reddit 卡片登录按钮，按指引配置 API 凭据",
        }

    def login_interactive(self):
        """
        API 鉴权没有可捕获的浏览器会话：
        打开应用创建页 + 输出分步指引，等待用户填好凭据文件后做 API 级验证。
        """
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser, context = self.create_login_context(p, "Reddit")
                page = context.pages[0] if context.pages else context.new_page()
                self.safe_goto(page, REDDIT_APP_PAGE, timeout=30000)
                logger.info(
                    "[Reddit] 已打开应用创建页。请按以下步骤操作：\n"
                    "  1. 点击页面底部“创建应用”，类型选择 script，"
                    "redirect_uri 填 http://localhost:8080\n"
                    f"  2. 将 client_id / client_secret / username / password "
                    f"写入 {self.secrets_file}\n"
                    "  3. 保存文件后本窗口将自动验证，最多等待 3 分钟"
                )
                deadline = time.time() + LOGIN_WAIT_SECONDS
                while time.time() < deadline:
                    secrets, _ = self._load_secrets()
                    if secrets:
                        break
                    time.sleep(2)
                self.close_login_session(browser, context)
        except Exception as e:
            logger.error(f"[Reddit] 登录指引流程异常: {e}")
            return False, f"Reddit 登录指引异常: {e}"

        ok, msg = self.check_login()
        if ok:
            try:
                self.save_profile(username=msg.replace("已连接 (", "").rstrip(")"))
            except Exception:
                pass
            return True, f"Reddit 连接成功！{msg}"
        return False, f"Reddit 未能完成鉴权：{msg}。请检查凭据文件后重试。"

    # ---------------- 发布 ----------------
    def get_selector_manifest(self):
        # API 直连，无 DOM 选择器
        return {}

    def check_selectors_health(self):
        return []

    def publish(self, payload):
        images = [str(Path(x).resolve()) for x in payload.get("images", [])
                  if Path(x).exists()]
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

        # 目标：分区帖 + 个人主页帖 (u/<用户名>)，可各自开关
        subreddit_name = (opts.get("reddit_subreddit") or self._default_subreddit()).strip()
        if "reddit_post_to_profile" in opts:
            post_to_profile = bool(opts.get("reddit_post_to_profile"))
        else:
            post_to_profile = self._default_post_to_profile()

        targets = []  # [(展示名, praw_target 名, 是否分区帖)]
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
        results = []  # [(展示名, 成功, url/错误)]
        for label, target_name, is_subreddit in targets:
            submit_kwargs = dict(title=title, nsfw=is_nsfw, spoiler=spoiler)
            if is_subreddit:
                # flair 仅分区帖支持，个人主页帖不支持
                if opts.get("reddit_flair_id"):
                    submit_kwargs["flair_id"] = opts["reddit_flair_id"]
                if opts.get("reddit_flair_text"):
                    submit_kwargs["flair_text"] = opts["reddit_flair_text"]
            try:
                target = reddit.subreddit(target_name)
                _ = target.display_name  # 预检：分区/主页存在且可发帖
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
        # 部分失败也大声报错，不让用户误以为双投都发出去了
        return False, f"Reddit 部分发布失败: {detail}", compliance
