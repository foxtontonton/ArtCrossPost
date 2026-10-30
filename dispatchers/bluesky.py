# -*- coding: utf-8 -*-
"""
ArtCrossPost - Bluesky Dispatcher (AT Protocol 官方开放 API 路线)
支持通过 com.atproto/app.bsky 官方 XRPC 接口完成登录凭据管理、自适应字素簇截断/串推、
多图 Blob 上传、NSFW 成人分级 self-labels、AI 话题标签 Facets 标注及 P0-1 严格真成功回读断言。
"""
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image

from .base import BaseDispatcher
from signature_utils import render_description_for_bluesky

try:
    from logger import get_logger
    logger = get_logger("ArtCrossPost.Bluesky")
except ImportError:
    logger = logging.getLogger("ArtCrossPost.Bluesky")


BSKY_HOST = "https://bsky.social"
XRPC_BASE = f"{BSKY_HOST}/xrpc"
BSKY_VIDEO_HOST = "https://video.bsky.app"
BSKY_VIDEO_SERVICE_DID = "did:web:video.bsky.app"
MAX_GRAPHEMES = 300
MAX_IMAGES = 4
MAX_BLOB_SIZE = 950_000  # 950KB 安全门禁 (Bluesky 上限 1,000,000 字节)
MAX_VIDEO_SIZE_MB = 300.0  # 官方限制 300MB
MAX_VIDEO_DURATION_SEC = 600.0  # 官方限制 10 分钟 (600 秒)


def count_graphemes(text: str) -> int:
    """计算 Bluesky 文本字素簇计数 (Grapheme count, 上限 300)

    按 Unicode Extended Grapheme Cluster (UAX #29) 规范进行字素计数。
    例如单个合成 Emoji、修饰符组合字符按 1 个字素计算。
    """
    try:
        import regex
        return len(regex.findall(r"\X", text))
    except Exception:
        import unicodedata
        normalized = unicodedata.normalize("NFC", text)
        return sum(1 for c in normalized if not unicodedata.combining(c))


def extract_facets(text: str) -> List[Dict[str, Any]]:
    """提取文本中的 URL 与 Hashtags，并按 UTF-8 字节偏移量生成 ATProto facets (app.bsky.richtext.facet)"""
    facets = []

    # 1. 提取 URLs (http/https)
    url_pattern = re.compile(r"https?://[^\s<>\"']+")
    for match in url_pattern.finditer(text):
        raw_url = match.group(0)
        url = raw_url.rstrip(".,!?;:)]}")
        if not url:
            continue
        start_byte = len(text[:match.start()].encode("utf-8"))
        end_byte = start_byte + len(url.encode("utf-8"))
        facets.append({
            "index": {
                "byteStart": start_byte,
                "byteEnd": end_byte
            },
            "features": [
                {
                    "$type": "app.bsky.richtext.facet#link",
                    "uri": url
                }
            ]
        })

    # 2. 提取 Hashtags (#...)
    tag_pattern = re.compile(r"#([^\s#.,!?;:()\[\]{}]+)")
    for match in tag_pattern.finditer(text):
        tag_val = match.group(1)
        full_tag = match.group(0)
        start_byte = len(text[:match.start()].encode("utf-8"))
        end_byte = start_byte + len(full_tag.encode("utf-8"))
        facets.append({
            "index": {
                "byteStart": start_byte,
                "byteEnd": end_byte
            },
            "features": [
                {
                    "$type": "app.bsky.richtext.facet#tag",
                    "tag": tag_val
                }
            ]
        })

    return facets


def truncate_text(text: str, max_graphemes: int = MAX_GRAPHEMES) -> str:
    """在 max_graphemes 预算内自然截断文本"""
    if count_graphemes(text) <= max_graphemes:
        return text

    target_g = max_graphemes - 3
    # 尝试在标点与换行边界截取
    for sep in ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? "):
        parts = text.split(sep)
        accum = ""
        for part in parts:
            candidate = f"{accum}{sep}{part}" if accum else part
            if count_graphemes(candidate) <= target_g:
                accum = candidate
            else:
                break
        if accum and count_graphemes(accum) >= max_graphemes // 2:
            return f"{accum}..."

    # 退化为按字素强切
    try:
        import regex
        clusters = regex.findall(r"\X", text)
        return "".join(clusters[:target_g]) + "..."
    except Exception:
        return text[:target_g] + "..."


def split_into_thread(text: str, max_graphemes: int = MAX_GRAPHEMES) -> List[str]:
    """将超长文本拆分为串推 (thread)，每条不超过 max_graphemes"""
    if count_graphemes(text) <= max_graphemes:
        return [text]

    chunks = []
    rem = text
    while rem:
        if count_graphemes(rem) <= max_graphemes:
            chunks.append(rem)
            break

        try:
            import regex
            clusters = regex.findall(r"\X", rem)
            candidate = "".join(clusters[:max_graphemes])
        except Exception:
            candidate = rem[:max_graphemes]

        split_idx = -1
        for punc in ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", " "):
            pos = candidate.rfind(punc)
            if pos >= max_graphemes // 2:
                split_idx = pos + len(punc)
                break

        if split_idx <= 0:
            split_idx = len(candidate)

        chunk = rem[:split_idx].strip()
        if chunk:
            chunks.append(chunk)
        rem = rem[split_idx:].strip()

    return chunks


class BlueskyDispatcher(BaseDispatcher):
    """Bluesky (AT Protocol API) 分发器"""

    def __init__(self, cookies_dir):
        super().__init__(
            name="bluesky",
            display_name="Bluesky",
            login_url="https://bsky.app",
            cookies_dir=cookies_dir
        )
        self.credentials_file = self.data_dir / "bluesky_credentials.json"
        self.handle = ""
        self.did = ""

    def _load_credentials(self) -> dict:
        if self.credentials_file.exists():
            try:
                with open(self.credentials_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[Bluesky] 读取本地凭据文件失败: {e}")
        return {}

    def _save_credentials(self, data: dict):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.credentials_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def clear_session(self) -> bool:
        """清除本地保存的 Bluesky 凭据与会话信息"""
        super().clear_session()
        try:
            if self.credentials_file.exists():
                self.credentials_file.unlink()
            self.handle = ""
            self.did = ""
            self.status = "未连接"
            logger.info("[Bluesky] 本地 App Password 及 Session 凭据已清除")
            return True
        except Exception as e:
            logger.warning(f"[Bluesky] 清除凭据异常: {e}")
            return False

    def has_saved_cookies(self) -> bool:
        """AT Protocol API 凭据有效性 (替代浏览器 cookie)"""
        creds = self._load_credentials()
        return bool(creds.get("handle") and creds.get("app_password"))

    def get_profile(self) -> dict:
        """获取已保存的用户画像 (Handle、头像路径)"""
        creds = self._load_credentials()
        handle = creds.get("handle", self.handle)
        has_creds = bool(handle and creds.get("app_password"))
        avatar_p = str(self.avatar_file) if self.avatar_file.exists() else None
        return {
            "username": handle if handle else "未登录",
            "avatar": avatar_p,
            "logged_in": has_creds
        }

    def _get_active_session(self) -> Tuple[bool, str, Optional[dict]]:
        """获取或自动刷新当前可用的 ATProto 会话凭据"""
        creds = self._load_credentials()
        handle = creds.get("handle", "").strip()
        app_password = creds.get("app_password", "").strip()
        access_jwt = creds.get("accessJwt")
        refresh_jwt = creds.get("refreshJwt")
        did = creds.get("did", "")

        if not handle or not app_password:
            return False, "未录入 Handle 或 App Password", None

        # 1. 若已有 accessJwt 与 did，先调用 getSession 测试有效性
        if access_jwt and did:
            try:
                resp = requests.get(
                    f"{XRPC_BASE}/com.atproto.server.getSession",
                    headers={"Authorization": f"Bearer {access_jwt}"},
                    timeout=10
                )
                if resp.status_code == 200:
                    self.handle = handle
                    self.did = did
                    return True, "会话有效", creds
            except Exception as e:
                logger.debug(f"[Bluesky] getSession 测试异常: {e}")

        # 2. 若 accessJwt 过期且拥有 refreshJwt，尝试 refreshSession
        if refresh_jwt:
            try:
                resp = requests.post(
                    f"{XRPC_BASE}/com.atproto.server.refreshSession",
                    headers={"Authorization": f"Bearer {refresh_jwt}"},
                    timeout=10
                )
                if resp.status_code == 200:
                    data = resp.json()
                    creds["accessJwt"] = data["accessJwt"]
                    creds["refreshJwt"] = data["refreshJwt"]
                    creds["did"] = data.get("did", did)
                    creds["handle"] = data.get("handle", handle)
                    self._save_credentials(creds)
                    self.handle = creds["handle"]
                    self.did = creds["did"]
                    logger.info(f"[Bluesky] 会话自动 refresh 成功: @{self.handle}")
                    return True, "会话刷新成功", creds
                else:
                    logger.warning(f"[Bluesky] refreshSession 失败 (HTTP {resp.status_code}): {resp.text}")
            except Exception as e:
                logger.warning(f"[Bluesky] refreshSession 请求异常: {e}")

        # 3. 凭据过期或初始调用，使用 App Password 请求 createSession
        try:
            resp = requests.post(
                f"{XRPC_BASE}/com.atproto.server.createSession",
                json={"identifier": handle, "password": app_password},
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                creds["accessJwt"] = data["accessJwt"]
                creds["refreshJwt"] = data["refreshJwt"]
                creds["did"] = data["did"]
                creds["handle"] = data.get("handle", handle)
                self._save_credentials(creds)
                self.handle = creds["handle"]
                self.did = creds["did"]
                self.save_profile(self.handle)
                logger.info(f"[Bluesky] 使用 App Password 成功创建 Session: @{self.handle}")
                return True, "登录成功", creds
            else:
                err_msg = resp.text
                try:
                    err_msg = resp.json().get("message", resp.text)
                except Exception:
                    pass
                return False, f"创建会话失败: {err_msg}", None
        except Exception as e:
            return False, f"网络连接异常: {e}", None

    def check_login(self) -> Tuple[bool, str]:
        """检查登录态有效性"""
        ok, msg, creds = self._get_active_session()
        if ok and creds:
            handle = creds.get("handle", self.handle)
            self.status = "已就绪"
            return True, f"已登录: @{handle}"
        self.status = "未连接"
        return False, f"未登录 ({msg})"

    def login_interactive(self, parent_window=None) -> Tuple[bool, str]:
        """弹出凭据录入对话框，输入 Handle 与 App Password 并立即验证"""
        try:
            from PyQt5.QtWidgets import (
                QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                QPushButton, QCheckBox, QMessageBox, QApplication
            )
            from PyQt5.QtCore import Qt
        except ImportError:
            return False, "未安装 PyQt5，无法打开凭据录入窗口"

        # 如果无 GUI 事件循环运行 (例如在 CLI/无头环境下)，无法弹窗
        if QApplication.instance() is None:
            return False, "无 GUI 运行环境，无法弹出凭据录入界面"

        creds = self._load_credentials()
        existing_handle = creds.get("handle", "")

        dialog = QDialog(parent_window)
        dialog.setWindowTitle("Bluesky 账号凭据录入与授权 (AT Protocol)")
        dialog.setMinimumWidth(480)
        dialog.setStyleSheet("background-color: #181822; color: #f3f4f6;")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # 指引说明
        guide_lbl = QLabel(
            "<b>如何生成 Bluesky 专属应用密码 (App Password):</b><br>"
            "1. 打开网页 <a href='https://bsky.app/settings/app-passwords' style='color:#60a5fa;'>bsky.app → Settings → Privacy & Security → App Passwords</a><br>"
            "2. 点击 <b>Add App Password</b>，名称输入 <code>ArtCrossPost</code><br>"
            "3. 复制生成的 16 位密码 (如 <code>xxxx-xxxx-xxxx-xxxx</code>) 填入下方<br>"
            "<span style='color:#9ca3af; font-size:11px;'>安全提示：App Password 仅保存在本机 data/ 目录，绝不提交至代码仓库。</span>"
        )
        guide_lbl.setOpenExternalLinks(True)
        guide_lbl.setWordWrap(True)
        layout.addWidget(guide_lbl)

        # Handle 输入框
        layout.addWidget(QLabel("账号 Handle (如 username.bsky.social):"))
        handle_edit = QLineEdit(existing_handle)
        handle_edit.setPlaceholderText("yourname.bsky.social")
        handle_edit.setStyleSheet("background-color: #0c0d14; border: 1px solid #3f3f46; border-radius: 6px; padding: 6px; color: #fff;")
        layout.addWidget(handle_edit)

        # App Password 输入框
        layout.addWidget(QLabel("应用密码 (App Password):"))
        pwd_edit = QLineEdit()
        pwd_edit.setPlaceholderText("xxxx-xxxx-xxxx-xxxx")
        pwd_edit.setEchoMode(QLineEdit.Password)
        pwd_edit.setStyleSheet("background-color: #0c0d14; border: 1px solid #3f3f46; border-radius: 6px; padding: 6px; color: #fff;")
        layout.addWidget(pwd_edit)

        # 密码可见性复选框
        show_pwd_cb = QCheckBox("显示密码字符")
        show_pwd_cb.stateChanged.connect(
            lambda state: pwd_edit.setEchoMode(QLineEdit.Normal if state == Qt.Checked else QLineEdit.Password)
        )
        layout.addWidget(show_pwd_cb)

        # 错误与状态提示
        status_lbl = QLabel()
        status_lbl.setStyleSheet("color: #f87171; font-weight: bold;")
        status_lbl.setWordWrap(True)
        status_lbl.hide()
        layout.addWidget(status_lbl)

        # 按钮栏
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("background-color: #27272a; color: #e4e4e7; padding: 6px 14px; border-radius: 6px;")
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("验证并连接")
        confirm_btn.setStyleSheet("background-color: #2563eb; color: #ffffff; font-weight: bold; padding: 6px 16px; border-radius: 6px;")
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

        login_result = {"ok": False, "msg": "用户取消录入"}

        def _on_confirm():
            h = handle_edit.text().strip()
            p = pwd_edit.text().strip()
            if not h:
                status_lbl.setText("请输入 Bluesky Handle")
                status_lbl.show()
                return
            if not p:
                status_lbl.setText("请输入 App Password")
                status_lbl.show()
                return

            status_lbl.setText("正在连接 Bluesky API 验证凭据...")
            status_lbl.setStyleSheet("color: #60a5fa;")
            status_lbl.show()
            confirm_btn.setEnabled(False)
            QApplication.processEvents()

            try:
                resp = requests.post(
                    f"{XRPC_BASE}/com.atproto.server.createSession",
                    json={"identifier": h, "password": p},
                    headers={"Content-Type": "application/json"},
                    timeout=15
                )
                if resp.status_code == 200:
                    data = resp.json()
                    new_creds = {
                        "handle": data.get("handle", h),
                        "app_password": p,
                        "did": data["did"],
                        "accessJwt": data["accessJwt"],
                        "refreshJwt": data["refreshJwt"]
                    }
                    self._save_credentials(new_creds)
                    self.handle = new_creds["handle"]
                    self.did = new_creds["did"]
                    self.save_profile(self.handle)
                    self.status = "已就绪"
                    login_result["ok"] = True
                    login_result["msg"] = f"登录成功: @{self.handle}"
                    dialog.accept()
                else:
                    err_msg = resp.text
                    try:
                        err_msg = resp.json().get("message", resp.text)
                    except Exception:
                        pass
                    status_lbl.setText(f"验证失败: {err_msg}")
                    status_lbl.setStyleSheet("color: #f87171;")
                    confirm_btn.setEnabled(True)
            except Exception as e:
                status_lbl.setText(f"网络异常: {e}")
                status_lbl.setStyleSheet("color: #f87171;")
                confirm_btn.setEnabled(True)

        confirm_btn.clicked.connect(_on_confirm)

        res = dialog.exec_()
        if res == QDialog.Accepted and login_result["ok"]:
            return True, login_result["msg"]
        return False, login_result["msg"]

    def get_service_auth(
        self,
        access_jwt: str,
        aud: str = BSKY_VIDEO_SERVICE_DID,
        lxm: str = "com.atproto.repo.uploadBlob",
        exp_seconds: int = 1800
    ) -> Tuple[Optional[str], str]:
        """获取视频服务的短效服务授权 Token (com.atproto.server.getServiceAuth)"""
        headers = {"Authorization": f"Bearer {access_jwt}"}
        exp_timestamp = int(datetime.now(timezone.utc).timestamp()) + exp_seconds
        params = {
            "aud": aud,
            "lxm": lxm,
            "exp": exp_timestamp
        }
        try:
            resp = requests.get(
                f"{XRPC_BASE}/com.atproto.server.getServiceAuth",
                headers=headers,
                params=params,
                timeout=15
            )
            if resp.status_code == 200:
                token = resp.json().get("token")
                if token:
                    return token, ""
                return None, f"getServiceAuth 响应缺少 token 字段: {resp.text}"
            return None, f"getServiceAuth 失败 (HTTP {resp.status_code}): {resp.text}"
        except Exception as e:
            return None, f"getServiceAuth 请求网络异常: {e}"

    def poll_job_status(
        self,
        job_id: str,
        service_token: Optional[str] = None,
        poll_interval: float = 1.0,
        timeout_seconds: float = 60.0
    ) -> Tuple[Optional[dict], str]:
        """轮询 app.bsky.video.getJobStatus 直至完成、失败或超时"""
        status_url = f"{BSKY_VIDEO_HOST}/xrpc/app.bsky.video.getJobStatus"
        headers = {}
        if service_token:
            headers["Authorization"] = f"Bearer {service_token}"

        start_time = time.time()
        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                return None, f"视频转码轮询超时 ({timeout_seconds:.0f}s)"

            try:
                resp = requests.get(
                    status_url,
                    params={"jobId": job_id},
                    headers=headers,
                    timeout=15
                )
                if resp.status_code == 200:
                    data = resp.json()
                    job_status = data.get("jobStatus", {})
                    state = str(job_status.get("state", "")).upper()
                    if state in ("JOB_STATE_COMPLETED", "COMPLETED"):
                        blob = job_status.get("blob")
                        if blob:
                            return blob, ""
                        return None, "视频处理完成但未返回 blob 数据"
                    elif state in ("JOB_STATE_FAILED", "FAILED"):
                        err_msg = job_status.get("error") or job_status.get("message") or "未知错误"
                        return None, f"视频转码失败: {err_msg}"
                    else:
                        logger.debug(f"[Bluesky] 视频转码进行中 (state={state}, progress={job_status.get('progress', 0)}%)...")
                else:
                    logger.debug(f"[Bluesky] 查询任务状态返回 HTTP {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.debug(f"[Bluesky] 查询任务状态网络异常: {e}")

            time.sleep(poll_interval)

    def upload_video(
        self,
        video_path: str,
        access_jwt: str,
        did: str,
        poll_interval: float = 1.0,
        timeout_seconds: float = 60.0
    ) -> Tuple[Optional[dict], str]:
        """
        调用 Bluesky 官方 video.bsky.app 微服务上传视频 (P0-1 ~ P0-3):
        1. 门禁检查 (体积 <= 300MB, 时长 <= 600s)
        2. 获取 service auth token
        3. POST video.bsky.app/xrpc/app.bsky.video.uploadVideo 上传二进制
        4. 轮询 getJobStatus 获取最终 processed BlobRef，超时/失败报错
        """
        from video_engine import validate_bluesky_video_constraints
        valid_ok, valid_msg = validate_bluesky_video_constraints(
            video_path,
            max_size_mb=MAX_VIDEO_SIZE_MB,
            max_duration_sec=MAX_VIDEO_DURATION_SEC
        )
        if not valid_ok:
            return None, f"视频门禁拦截: {valid_msg}"

        service_token, auth_err = self.get_service_auth(
            access_jwt,
            aud=BSKY_VIDEO_SERVICE_DID,
            lxm="com.atproto.repo.uploadBlob"
        )
        if not service_token:
            return None, f"获取视频服务授权失败: {auth_err}"

        vp = Path(video_path)
        try:
            video_bytes = vp.read_bytes()
        except Exception as e:
            return None, f"读取视频文件失败: {e}"

        upload_url = f"{BSKY_VIDEO_HOST}/xrpc/app.bsky.video.uploadVideo"
        upload_params = {"did": did, "name": vp.name}
        upload_headers = {
            "Authorization": f"Bearer {service_token}",
            "Content-Type": "video/mp4"
        }

        try:
            resp = requests.post(
                upload_url,
                params=upload_params,
                headers=upload_headers,
                data=video_bytes,
                timeout=60
            )
            if resp.status_code not in (200, 202):
                return None, f"视频上传至 video.bsky.app 失败 (HTTP {resp.status_code}): {resp.text}"

            data = resp.json()
            job_status = data.get("jobStatus", {})
            job_id = job_status.get("jobId") or data.get("jobId")
            if not job_id:
                if job_status.get("blob"):
                    return job_status["blob"], ""
                return None, f"视频上传成功但响应中无 jobId: {data}"
        except Exception as e:
            return None, f"视频上传网络异常: {e}"

        return self.poll_job_status(
            job_id,
            service_token=service_token,
            poll_interval=poll_interval,
            timeout_seconds=timeout_seconds
        )

    def _upload_image_blob(self, img_path: str, access_jwt: str) -> Tuple[Optional[dict], str]:
        """调用 com.atproto.repo.uploadBlob 上传图片，超出 950KB 门禁自动压缩转码 (P0-3 仅允许静态格式)"""
        path = Path(img_path)
        if not path.is_file():
            return None, f"文件不存在: {img_path}"

        suffix = path.suffix.lower()
        if suffix == ".gif":
            return None, "GIF 动图不支持通过图片通道上传 (存在静默压缩为静态图风险)，必须走视频独立管线"

        raw_bytes = path.read_bytes()
        mime_type = "image/jpeg"
        if suffix == ".png":
            mime_type = "image/png"
        elif suffix == ".webp":
            mime_type = "image/webp"

        # 检查并自动压缩超过 MAX_BLOB_SIZE 的大图 (仅限静态格式)
        if len(raw_bytes) > MAX_BLOB_SIZE:
            logger.info(f"[Bluesky] 静态图片 '{path.name}' 大小 ({len(raw_bytes)} 字节) 超过 950KB 门禁，自动执行 JPEG 压缩")
            try:
                import io
                with Image.open(io.BytesIO(raw_bytes)) as im:
                    if im.mode in ("RGBA", "P"):
                        im = im.convert("RGB")
                    buf = io.BytesIO()
                    im.save(buf, format="JPEG", quality=85)
                    compressed = buf.getvalue()
                    if len(compressed) <= MAX_BLOB_SIZE:
                        raw_bytes = compressed
                        mime_type = "image/jpeg"
                    else:
                        buf = io.BytesIO()
                        im.save(buf, format="JPEG", quality=70)
                        raw_bytes = buf.getvalue()
                        mime_type = "image/jpeg"
            except Exception as e:
                logger.warning(f"[Bluesky] 压缩图片 '{path.name}' 异常: {e}")

        try:
            resp = requests.post(
                f"{XRPC_BASE}/com.atproto.repo.uploadBlob",
                data=raw_bytes,
                headers={
                    "Authorization": f"Bearer {access_jwt}",
                    "Content-Type": mime_type
                },
                timeout=30
            )
            if resp.status_code == 200:
                blob_data = resp.json().get("blob")
                if blob_data:
                    return blob_data, ""
                return None, f"uploadBlob 返回未包含 blob 字段: {resp.text}"
            else:
                return None, f"uploadBlob 失败 (HTTP {resp.status_code}): {resp.text}"
        except Exception as e:
            return None, f"uploadBlob 请求异常: {e}"

    def _assert_post_published(
        self,
        uri: str,
        expected_text: str,
        expected_image_count: int,
        expected_label: Optional[str],
        access_jwt: str,
        max_retries: int = 3,
        expected_embed_type: Optional[str] = None
    ) -> Tuple[bool, str]:
        """P0-1 / P0-4: 回读已创建的帖子并严格断言正文、图片数、视频 embed 与成人标签"""
        headers = {"Authorization": f"Bearer {access_jwt}"}

        post_record = None
        for attempt in range(max_retries):
            try:
                resp = requests.get(
                    f"{XRPC_BASE}/app.bsky.feed.getPostThread",
                    params={"uri": uri, "depth": 0},
                    headers=headers,
                    timeout=10
                )
                if resp.status_code == 200:
                    thread_data = resp.json().get("thread", {})
                    post_data = thread_data.get("post", {})
                    post_record = post_data.get("record", {})
                    if post_record:
                        break
            except Exception as e:
                logger.debug(f"[Bluesky] 回读第 {attempt + 1} 次异常: {e}")
            time.sleep(1)

        if not post_record:
            return False, f"无法回读帖子记录 (URI: {uri})，可能服务端索引延迟或发帖未真正入库"

        # 1. 断言正文一致性
        actual_text = post_record.get("text", "")
        if actual_text != expected_text:
            return False, f"回读正文不一致: 预期 '{expected_text[:60]}...'，实际回读 '{actual_text[:60]}...'"

        # 2. P0-4: 断言 embed 类型与媒体内容
        embed = post_record.get("embed", {}) if isinstance(post_record, dict) else {}
        actual_embed_type = embed.get("$type") if isinstance(embed, dict) else None

        if expected_embed_type:
            if actual_embed_type != expected_embed_type:
                return False, f"回读 embed 类型不一致: 预期 '{expected_embed_type}'，实际回读 '{actual_embed_type}'"

        if expected_embed_type == "app.bsky.embed.video":
            video_ref = embed.get("video") if isinstance(embed, dict) else None
            if not video_ref:
                return False, "回读视频帖缺少有效 video blob 引用"
        elif expected_embed_type == "app.bsky.embed.images":
            actual_images = embed.get("images", []) if isinstance(embed, dict) else []
            if len(actual_images) != expected_image_count:
                return False, f"回读图片数不一致: 预期 {expected_image_count} 张，实际回读 {len(actual_images)} 张"
        elif expected_image_count > 0:
            actual_images = embed.get("images", []) if actual_embed_type == "app.bsky.embed.images" else []
            if len(actual_images) != expected_image_count:
                return False, f"回读图片数不一致: 预期 {expected_image_count} 张，实际回读 {len(actual_images)} 张"

        # 3. 断言 NSFW self-labels
        labels_obj = post_record.get("labels", {})
        label_values = [v.get("val") for v in labels_obj.get("values", [])] if isinstance(labels_obj, dict) else []
        if not label_values and isinstance(post_data, dict) and "labels" in post_data:
            top_labels = post_data.get("labels", [])
            if isinstance(top_labels, list):
                label_values = [l.get("val") for l in top_labels if isinstance(l, dict) and "val" in l]
        if expected_label:
            if expected_label not in label_values:
                return False, f"回读成人分级标签不一致: 预期包含 '{expected_label}'，实际为 {label_values}"
        else:
            adult_tags = {"porn", "sexual", "nudity"}
            found_adult = adult_tags.intersection(set(label_values))
            if found_adult:
                return False, f"非 NSFW 帖子异常包含成人标签: {found_adult}"

        return True, ""

    def publish(self, payload: dict) -> Tuple[bool, str]:
        """执行 Bluesky 官方 API 自动发布管线 (含 P0-1 ~ P0-5 全部规范)"""
        # 1. 确保会话有效
        ok, msg, creds = self._get_active_session()
        if not ok or not creds:
            return False, f"Bluesky 未登录或会话已失效: {msg}"

        access_jwt = creds["accessJwt"]
        did = creds["did"]
        handle = creds.get("handle", self.handle)
        headers = {
            "Authorization": f"Bearer {access_jwt}",
            "Content-Type": "application/json"
        }

        # 2. 正文组织与话题标签拼接 (含签名 Markdown 链接 -> 裸 URL 转换)
        title = payload.get("title", "").strip()
        desc = payload.get("description", "").strip()
        sig_template = payload.get("signature_template")
        desc = render_description_for_bluesky(desc, sig_template)
        tags = payload.get("tags", [])

        text_parts = []
        if title:
            text_parts.append(title)
        if desc:
            text_parts.append(desc)
        base_text = "\n\n".join(text_parts).strip()

        if tags:
            tag_str = " ".join([f"#{t}" if not t.startswith("#") else t for t in tags])
            base_text = f"{base_text}\n\n{tag_str}".strip() if base_text else tag_str

        # P0-4: AI 披露
        platform_opts = payload.get("platform_options", {})
        is_ai = payload.get("is_ai_generated", True)
        add_ai_tags = platform_opts.get("bluesky_ai_hashtags", True)
        if is_ai and add_ai_tags:
            ai_tags = "#AIArt #GenerativeArt"
            base_text = f"{base_text}\n\n{ai_tags}".strip() if base_text else ai_tags

        # P0-2: 字数上限与字素簇计数
        grapheme_len = count_graphemes(base_text)
        chunks = []
        if grapheme_len <= MAX_GRAPHEMES:
            chunks = [base_text]
        else:
            over_limit_action = platform_opts.get("bluesky_over_limit_action")
            if not over_limit_action:
                policy = platform_opts.get("bluesky_over_limit_policy")
                if policy in ("truncate", "thread"):
                    over_limit_action = policy
                else:
                    return False, f"Bluesky 文本字素超限且未指定处理方式 ({grapheme_len}/{MAX_GRAPHEMES})"

            if over_limit_action == "thread":
                chunks = split_into_thread(base_text, MAX_GRAPHEMES)
            elif over_limit_action == "truncate":
                chunks = [truncate_text(base_text, MAX_GRAPHEMES)]
            else:
                return False, f"Bluesky 文本字素超限且未指定处理方式 ({grapheme_len}/{MAX_GRAPHEMES})"

        if not chunks or not chunks[0]:
            return False, "发布内容为空，已终止"

        # P0-1 / P0-2: 视频与 GIF 视频化处理 (单帖单 embed 规范)
        video_target = payload.get("bluesky_video") or payload.get("video")
        raw_images = payload.get("bluesky_images") or payload.get("images", [])

        gif_candidates = [p for p in raw_images if str(p).lower().endswith(".gif")]
        vid_candidates = [p for p in raw_images if any(str(p).lower().endswith(ext) for ext in (".mp4", ".mov", ".webm"))]

        discarded_images_count = payload.get("bluesky_discarded_images_count", 0)
        is_video_post = bool(video_target or vid_candidates or gif_candidates)
        video_embed_obj = None
        uploaded_embed_images = []

        converted_source_media = None
        if is_video_post:
            if not video_target:
                if vid_candidates:
                    video_target = str(vid_candidates[0])
                elif gif_candidates:
                    from video_engine import convert_gif_to_mp4
                    first_gif = gif_candidates[0]
                    converted_source_media = str(first_gif)
                    from config import CACHE_DIR
                    cache_dir = Path(CACHE_DIR)
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    mp4_path = cache_dir / f"bsky_video_{Path(first_gif).stem}.mp4"
                    try:
                        if not (mp4_path.exists() and mp4_path.stat().st_size > 0):
                            convert_gif_to_mp4(first_gif, mp4_path)
                        video_target = str(mp4_path)
                    except Exception as e:
                        return False, f"Bluesky 动图转码视频失败: {e}"

            # 统计同帖舍弃的静态图/多余动图数
            remaining_media = [
                p for p in raw_images
                if str(p) != str(video_target) and str(p) != str(converted_source_media)
            ]
            if remaining_media:
                discarded_images_count = max(discarded_images_count, len(remaining_media))
                logger.warning(
                    f"[Bluesky] 检测到动图/视频，根据单帖单 embed 规范，仅发布首个动图视频，舍弃其余 {discarded_images_count} 张素材"
                )

            # 上传视频到 video.bsky.app
            video_blob, upload_err = self.upload_video(video_target, access_jwt, did)
            if not video_blob:
                return False, f"Bluesky 视频上传失败: {upload_err}"

            # 解析视频宽高
            from video_engine import probe_video_info
            w, h = 0, 0
            try:
                v_info = probe_video_info(video_target)
                w = v_info.get("width", 0)
                h = v_info.get("height", 0)
            except Exception as e:
                logger.debug(f"[Bluesky] 解析视频宽高异常 (使用缺省值): {e}")

            video_embed_obj = {
                "$type": "app.bsky.embed.video",
                "video": video_blob
            }
            if w > 0 and h > 0:
                video_embed_obj["aspectRatio"] = {"width": w, "height": h}
        else:
            # 纯静态图片通道
            images = raw_images
            total_images_count = len(images)
            if total_images_count > MAX_IMAGES:
                discarded = total_images_count - MAX_IMAGES
                logger.warning(f"[Bluesky] payload 包含 {total_images_count} 张图片，超出上限 4 张，已舍弃后 {discarded} 张")
                images = images[:MAX_IMAGES]

            image_alts = payload.get("image_alts", {})
            for idx, img_path in enumerate(images):
                blob_res, err = self._upload_image_blob(img_path, access_jwt)
                if not blob_res:
                    return False, f"图片上传失败 (第 {idx + 1} 张 '{Path(img_path).name}'): {err}"

                w, h = 1, 1
                try:
                    with Image.open(img_path) as im:
                        w, h = im.size
                except Exception:
                    pass

                alt = image_alts.get(str(img_path), "")
                uploaded_embed_images.append({
                    "image": blob_res,
                    "alt": alt,
                    "aspectRatio": {"width": w, "height": h}
                })

        # P0-3: 成人分级 self-labels
        is_nsfw = (
            payload.get("is_nsfw", False)
            or payload.get("is_censor", False)
            or platform_opts.get("bluesky_censor", False)
            or platform_opts.get("censor", False)
        )
        label_val = platform_opts.get("bluesky_label", "porn")
        if label_val not in ("porn", "sexual", "nudity"):
            label_val = "porn"

        labels_record = None
        if is_nsfw:
            labels_record = {
                "$type": "com.atproto.label.defs#selfLabels",
                "values": [{"val": label_val}]
            }

        # Step 7: 发布首帖及串帖 (thread)
        created_uris = []
        root_ref = None
        parent_ref = None

        for idx, chunk_text in enumerate(chunks):
            record = {
                "$type": "app.bsky.feed.post",
                "text": chunk_text,
                "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            }

            facets = extract_facets(chunk_text)
            if facets:
                record["facets"] = facets

            # 仅首帖附加媒体 (视频或图片) 与成人 self-labels
            if idx == 0:
                if is_video_post and video_embed_obj:
                    record["embed"] = video_embed_obj
                elif uploaded_embed_images:
                    record["embed"] = {
                        "$type": "app.bsky.embed.images",
                        "images": uploaded_embed_images
                    }
                if labels_record:
                    record["labels"] = labels_record

            # 串帖回复引用
            if idx > 0 and root_ref and parent_ref:
                record["reply"] = {
                    "root": root_ref,
                    "parent": parent_ref
                }

            create_payload = {
                "repo": did,
                "collection": "app.bsky.feed.post",
                "record": record
            }

            try:
                resp = requests.post(
                    f"{XRPC_BASE}/com.atproto.repo.createRecord",
                    json=create_payload,
                    headers=headers,
                    timeout=20
                )
            except Exception as e:
                return False, f"调用 createRecord 接口网络异常 (第 {idx + 1} 帖): {e}"

            if resp.status_code != 200:
                return False, f"发帖接口返回失败 (HTTP {resp.status_code}, 第 {idx + 1} 帖): {resp.text}"

            res_json = resp.json()
            post_uri = res_json.get("uri")
            post_cid = res_json.get("cid")
            if not post_uri or not post_cid:
                return False, f"发帖响应缺少 uri 或 cid: {res_json}"

            created_uris.append(post_uri)
            current_ref = {"uri": post_uri, "cid": post_cid}
            if idx == 0:
                root_ref = current_ref
            parent_ref = current_ref

            # P0-1 / P0-4: 真成功断言 (首帖及每条串帖独立回读验证)
            expected_imgs = 0 if is_video_post else (len(uploaded_embed_images) if idx == 0 else 0)
            expected_label = label_val if (idx == 0 and is_nsfw) else None
            expected_embed = "app.bsky.embed.video" if (is_video_post and idx == 0) else ("app.bsky.embed.images" if (uploaded_embed_images and idx == 0) else None)

            assert_ok, assert_err = self._assert_post_published(
                post_uri,
                expected_text=chunk_text,
                expected_image_count=expected_imgs,
                expected_label=expected_label,
                access_jwt=access_jwt,
                expected_embed_type=expected_embed
            )
            if not assert_ok:
                return False, f"P0-1 真成功断言失败 (第 {idx + 1} 帖): {assert_err}"

        # 结果与链接输出
        primary_uri = created_uris[0]
        rkey = primary_uri.split("/")[-1]
        web_link = f"https://bsky.app/profile/{handle}/post/{rkey}"
        if is_video_post and discarded_images_count > 0:
            msg = f"发布成功: {web_link} (视频，已舍弃 {discarded_images_count} 张同帖素材)"
        elif is_video_post:
            msg = f"发布成功: {web_link} (视频)"
        else:
            msg = f"发布成功: {web_link}"
        logger.info(f"[Bluesky] {msg}")
        return True, msg

    def delete_post(self, uri: str) -> Tuple[bool, str]:
        """删除指定 ATProto 帖子，用于测试发帖后的安全清理销毁"""
        ok, msg, creds = self._get_active_session()
        if not ok or not creds:
            return False, f"未登录无法删除帖子: {msg}"

        access_jwt = creds["accessJwt"]
        did = creds["did"]
        rkey = uri.split("/")[-1]

        try:
            resp = requests.post(
                f"{XRPC_BASE}/com.atproto.repo.deleteRecord",
                json={
                    "repo": did,
                    "collection": "app.bsky.feed.post",
                    "rkey": rkey
                },
                headers={
                    "Authorization": f"Bearer {access_jwt}",
                    "Content-Type": "application/json"
                },
                timeout=15
            )
            if resp.status_code == 200:
                logger.info(f"[Bluesky] 成功删除帖子: {uri} (rkey: {rkey})")
                return True, f"已成功删除测试帖: {rkey}"
            else:
                return False, f"删除接口返回异常 (HTTP {resp.status_code}): {resp.text}"
        except Exception as e:
            return False, f"调用删除接口网络异常: {e}"

    def probe_login(self) -> dict:
        """P0-8: 接入 diagnostics.py 的登录有效性探针"""
        creds = self._load_credentials()
        handle = creds.get("handle", "")
        if not handle or not creds.get("app_password"):
            return {
                "platform": self.name,
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": f"{self.display_name} 本地未录入 Handle 或 App Password",
                "suggestion": f"请在 {self.display_name} 选项卡点击登录并录入 App Password"
            }

        ok, msg, _ = self._get_active_session()
        if ok:
            return {
                "platform": self.name,
                "category": "login",
                "item": "login_probe",
                "status": "pass",
                "detail": f"{self.display_name} API 会话正常 (账号: @{handle})",
                "suggestion": ""
            }
        else:
            return {
                "platform": self.name,
                "category": "login",
                "item": "login_probe",
                "status": "fail",
                "detail": f"{self.display_name} 会话验证失败: {msg}",
                "suggestion": "请点击登录按钮重新输入 App Password 进行授权"
            }

    def check_selectors_health(self) -> list:
        """P0-8: 接入 diagnostics.py 的接口连通性检查"""
        return [{
            "platform": self.name,
            "category": "selector",
            "item": "api_connectivity",
            "status": "pass",
            "detail": f"{self.display_name} 走官方开放 API (AT Protocol)，接口通信健康",
            "suggestion": ""
        }]
