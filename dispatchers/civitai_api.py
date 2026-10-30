# -*- coding: utf-8 -*-
"""
ArtCrossPost - Civitai API Client & MCP Protocol Dispatcher
使用官方 MCP 协议 (https://mcp.civitai.com/mcp) 与 REST API 驱动发帖与断言。
"""
import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional, Set, Tuple, Union

import requests
from PIL import Image

try:
    from logger import get_logger
    logger = get_logger("ArtCrossPost.CivitaiAPI")
except ImportError:
    logger = logging.getLogger("ArtCrossPost.CivitaiAPI")

try:
    from signature_utils import render_description_to_html
except ImportError:
    try:
        from ..signature_utils import render_description_to_html
    except ImportError:
        def render_description_to_html(desc: str, sig_template=None) -> str:
            return f"<p>{desc}</p>"

try:
    from video_engine import probe_video_info
except ImportError:
    try:
        from ..video_engine import probe_video_info
    except ImportError:
        probe_video_info = None

try:
    from .civitai import (
        parse_civitai_resources,
        prepare_civitai_media,
        CIVITAI_SUPPORTED_IMAGE_EXTS,
        CIVITAI_SUPPORTED_VIDEO_EXTS,
        CIVITAI_MAX_MEDIA_LIMIT
    )
except ImportError:
    from dispatchers.civitai import (
        parse_civitai_resources,
        prepare_civitai_media,
        CIVITAI_SUPPORTED_IMAGE_EXTS,
        CIVITAI_SUPPORTED_VIDEO_EXTS,
        CIVITAI_MAX_MEDIA_LIMIT
    )


MCP_URL = "https://mcp.civitai.com/mcp"
REST_IMAGES_URL = "https://civitai.com/api/v1/images"
POSTS_PAGE_URL = "https://civitai.com/posts"
CIVITAI_MODELS_API_URL = "https://civitai.com/api/v1/models"


class CivitaiApiError(Exception):
    """Civitai MCP 或业务错误 (包含服务端原话)"""
    pass


class CivitaiTimeoutError(CivitaiApiError):
    """建帖超时 (结果未知，禁止盲目重试)"""
    pass


class CivitaiAssertionError(CivitaiApiError):
    """建帖后回读或断言未通过"""
    pass


def redact_api_key(text: Any, api_key: Optional[str]) -> str:
    """消除文本中可能泄露的真实 API Key"""
    if not text:
        return ""
    s = str(text)
    if api_key and str(api_key).strip() and str(api_key).strip() in s:
        s = s.replace(str(api_key).strip(), "[REDACTED_API_KEY]")
    return s


def mask_civitai_api_key(key: Optional[str]) -> str:
    """掩码展示 API Key (仅末尾 4 位可见)"""
    if not key or not str(key).strip():
        return "未配置"
    k = str(key).strip()
    if len(k) <= 8:
        return "••••••••"
    return f"••••••••{k[-4:]}"


def get_civitai_api_key(data_dir: Optional[Path] = None) -> Optional[str]:
    """
    读取 Civitai API Key:
    1. 优先读取环境变量 CIVITAI_API_KEY
    2. 其次读取本机 data/civitai_credentials.json 中的 api_key
    若均未配置则返回 None
    """
    env_k = os.environ.get("CIVITAI_API_KEY", "").strip()
    if env_k:
        return env_k

    if data_dir is None:
        data_dir = Path(__file__).resolve().parent.parent / "data"

    creds_file = Path(data_dir) / "civitai_credentials.json"
    if creds_file.exists():
        try:
            with open(creds_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                val = data.get("api_key", "").strip()
                if val:
                    return val
        except Exception as e:
            logger.warning(f"[Civitai API] 读取凭据文件失败: {e}")
    return None


def save_civitai_api_key(api_key: str, data_dir: Optional[Path] = None) -> bool:
    """持久化 Civitai API Key 至 data/civitai_credentials.json"""
    if data_dir is None:
        data_dir = Path(__file__).resolve().parent.parent / "data"

    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    creds_file = d / "civitai_credentials.json"
    try:
        with open(creds_file, "w", encoding="utf-8") as f:
            json.dump({"api_key": api_key.strip()}, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"[Civitai API] 保存凭据失败: {e}")
        return False


def clear_civitai_credentials(data_dir: Optional[Path] = None) -> bool:
    """清除本机存储的 Civitai API Key 凭据"""
    if data_dir is None:
        data_dir = Path(__file__).resolve().parent.parent / "data"

    creds_file = Path(data_dir) / "civitai_credentials.json"
    if creds_file.exists():
        try:
            creds_file.unlink()
            return True
        except Exception as e:
            logger.warning(f"[Civitai API] 删除凭据文件失败: {e}")
            return False
    return True


def probe_local_media_dimensions(file_path: Union[str, Path]) -> Tuple[int, int]:
    """探测本地媒体素材的真实宽度和高度 (w, h)"""
    p = Path(file_path)
    ext = p.suffix.lower()

    if ext in CIVITAI_SUPPORTED_IMAGE_EXTS or ext == ".gif":
        try:
            with Image.open(p) as img:
                return img.size
        except Exception as e:
            logger.warning(f"[Civitai API] PIL 探测图片尺寸失败 ({p.name}): {e}")

    if ext in CIVITAI_SUPPORTED_VIDEO_EXTS:
        if probe_video_info is not None:
            try:
                info = probe_video_info(p)
                w = info.get("width") or 0
                h = info.get("height") or 0
                if w > 0 and h > 0:
                    return w, h
            except Exception as e:
                logger.warning(f"[Civitai API] probe_video_info 探测尺寸失败 ({p.name}): {e}")

        # 备选回退使用 imageio_ffmpeg
        try:
            import imageio_ffmpeg
            gen = imageio_ffmpeg.read_frames(str(p))
            meta = gen.__next__()  # header info
            if isinstance(meta, dict) and "size" in meta:
                return meta["size"][0], meta["size"][1]
        except Exception:
            pass

    return 1024, 1024  # 兜底尺寸


def resolve_civitai_model_version(
    resource_item: Any,
    api_key: Optional[str] = None,
    session: Optional[requests.Session] = None,
    models_api_url: str = CIVITAI_MODELS_API_URL,
    timeout: float = 15.0
) -> Tuple[Optional[int], Optional[str]]:
    """
    通过 Civitai REST API 查询模型元数据，将用户配置的模型 ID / 模型 URL / {id, version}
    解析为具体的 modelVersionId:
    - version 字段为纯数字且命中列表中版本 id -> 用它
    - version 字段为名称 -> 按 name 不区分大小写匹配
    - 无 version 或匹配不到 -> 取列表第一个 (最新版) 并在结果注明降级 (与浏览器通道"降级默认版本"口径一致)
    - REST 查不到/失败 -> 按未绑定资源处理并注明；任何情况下不许把模型 ID 当 modelVersionId 传出

    返回: (resolved_version_id, note)
    """
    if not isinstance(resource_item, dict) or "id" not in resource_item or not isinstance(resource_item.get("id"), int):
        parsed = parse_civitai_resources([resource_item])
        if not parsed:
            return None, "资源配置解析失败，已按未绑定模型版本处理"
        item = parsed[0]
    else:
        item = resource_item

    model_id = item.get("id")
    version_req = item.get("version")

    if not model_id or not isinstance(model_id, int):
        return None, "资源配置解析失败，已按未绑定模型版本处理"

    url = f"{models_api_url.rstrip('/')}/{model_id}"
    headers = {"Accept": "application/json"}
    if api_key and str(api_key).strip():
        headers["Authorization"] = f"Bearer {str(api_key).strip()}"

    s = session or requests
    try:
        resp = s.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"[Civitai API] REST 查询模型 {model_id} 失败 (HTTP {resp.status_code})")
            return None, f"模型 {model_id} 查询失败，已按未绑定模型版本处理"
        data = resp.json()
    except Exception as e:
        logger.warning(f"[Civitai API] REST 查询模型 {model_id} 异常: {e}")
        return None, f"模型 {model_id} 查询失败，已按未绑定模型版本处理"

    model_versions = data.get("modelVersions") or []
    if not model_versions:
        logger.warning(f"[Civitai API] 模型 {model_id} 无可用版本")
        return None, f"模型 {model_id} 无可用版本，已按未绑定模型版本处理"

    # 1. version 字段为纯数字且命中列表中版本 id -> 用它
    if version_req is not None and str(version_req).strip():
        v_str = str(version_req).strip()
        if v_str.isdigit():
            target_vid = int(v_str)
            for v in model_versions:
                if v.get("id") == target_vid:
                    return target_vid, None

    # 2. version 字段为名称 -> 按 name 不区分大小写匹配
    if version_req is not None and str(version_req).strip():
        target_name = str(version_req).strip().lower()
        # 精确匹配 (不区分大小写)
        for v in model_versions:
            v_name = str(v.get("name") or "").strip().lower()
            if v_name == target_name:
                return v.get("id"), None
        # 子串/模糊匹配 (不区分大小写，如 "V6" 匹配 "V6 (start with this one)")
        for v in model_versions:
            v_name = str(v.get("name") or "").strip().lower()
            if target_name in v_name:
                return v.get("id"), None

    # 3. 无 version 或匹配不到 -> 取列表第一个 (最新版) 并在结果注明降级
    default_v = model_versions[0]
    default_vid = default_v.get("id")
    if not default_vid:
        return None, f"模型 {model_id} 默认版本无效，已按未绑定模型版本处理"

    if version_req is not None and str(version_req).strip():
        note = f"模型 {model_id} 未匹配到版本 '{version_req}'，已降级默认版本 ({default_vid})"
    else:
        note = f"模型 {model_id} 未指定版本，已降级默认版本 ({default_vid})"

    return default_vid, note


def resolve_civitai_channel(
    channel_config: Optional[str],
    resources: Any,
    api_key: Optional[str],
    session: Optional[requests.Session] = None,
    models_api_url: Optional[str] = None
) -> Tuple[str, Optional[str], Optional[int]]:
    """
    Civitai 混合通道路由决策核心:
    配置形态: civitai_channel (auto / api / browser)

    路由规则:
    1. channel == "browser":
       固定走浏览器通道
    2. channel == "api":
       - 若无 API Key: 报错中止，禁止静默回退
       - 若有 API Key: 走 API 通道；
         若有有效资源且 ≥1，取首个资源解析 modelVersionId；
         若资源 ≥2，附加提示 API 仅能携带首个资源
    3. channel == "auto" (默认):
       - 生效资源数 ≥ 2: 回退浏览器通道，注明 "已回退浏览器通道（资源 N 个）"
       - 生效资源数 ≤ 1:
         - 若无 API Key: 回退浏览器通道，注明 "已回退浏览器通道（未配置 API Key）"
         - 若有 API Key: 走 API 通道，恰 1 个解析 modelVersionId，解析失败按未配处理并在结果注明

    返回: (selected_channel, note, model_version_id)
    """
    ch = (channel_config or "auto").lower().strip()
    if ch not in ("auto", "api", "browser"):
        ch = "auto"

    has_key = bool(api_key and str(api_key).strip())
    parsed_res = parse_civitai_resources(resources) if resources else []
    raw_has_items = bool(resources and isinstance(resources, (list, tuple)) and len(resources) > 0)
    parse_failed = raw_has_items and len(parsed_res) == 0

    if ch == "browser":
        return "browser", None, None

    models_url = models_api_url or CIVITAI_MODELS_API_URL

    if ch == "api":
        if not has_key:
            raise CivitaiApiError("未配置 Civitai API Key，无法使用 API 通道发布（请在 Civitai 卡片录入 Key 或设置 CIVITAI_API_KEY 环境变量）")
        if len(parsed_res) >= 2:
            mid = parsed_res[0]["id"]
            ver_id, ver_note = resolve_civitai_model_version(
                parsed_res[0],
                api_key=api_key,
                session=session,
                models_api_url=models_url
            )
            base_note = f"API 通道仅支持绑定 1 个模型版本，已携带首个资源 ({mid})"
            final_note = f"{base_note}；{ver_note}" if ver_note else base_note
            return "api", final_note, ver_id
        elif len(parsed_res) == 1:
            ver_id, ver_note = resolve_civitai_model_version(
                parsed_res[0],
                api_key=api_key,
                session=session,
                models_api_url=models_url
            )
            return "api", ver_note, ver_id
        else:
            note = "资源配置解析失败，已按未绑定模型版本处理" if parse_failed else None
            return "api", note, None

    # ch == "auto"
    if len(parsed_res) >= 2:
        return "browser", f"已回退浏览器通道（资源 {len(parsed_res)} 个）", None

    if not has_key:
        return "browser", "已回退浏览器通道（未配置 API Key）", None

    # len(parsed_res) <= 1 且 has_key
    if len(parsed_res) == 1:
        ver_id, ver_note = resolve_civitai_model_version(
            parsed_res[0],
            api_key=api_key,
            session=session,
            models_api_url=models_url
        )
        return "api", ver_note, ver_id
    else:
        note = "资源配置解析失败，已按未绑定模型版本处理" if parse_failed else None
        return "api", note, None



class CivitaiApiClient:
    """Civitai MCP 客户端封装"""

    def __init__(
        self,
        api_key: str,
        mcp_url: str = MCP_URL,
        rest_url: str = REST_IMAGES_URL,
        timeout: float = 60.0,
        session: Optional[requests.Session] = None
    ):
        self.api_key = str(api_key).strip()
        self.mcp_url = mcp_url
        self.rest_url = rest_url
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session_id: Optional[str] = None
        self._msg_id = 0
        self.last_created_image_ids: List[int] = []

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _get_headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _parse_mcp_response(self, resp: requests.Response) -> dict:
        # 捕获服务端返回的 session id
        sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid

        text = resp.text.strip() if resp.text else ""
        if text.startswith("data:"):
            # SSE 帧解析
            lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("data:")]
            if lines:
                return json.loads(lines[0][5:].strip())
        try:
            return resp.json()
        except Exception:
            return {"raw_text": text, "status_code": resp.status_code}

    def _call_mcp(self, method: str, params: Optional[dict] = None, is_notification: bool = False) -> dict:
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method
        }
        if not is_notification:
            payload["id"] = self._next_id()
        if params is not None:
            payload["params"] = params

        try:
            resp = self.session.post(
                self.mcp_url,
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout
            )
        except requests.Timeout as te:
            raise CivitaiTimeoutError(f"请求 Civitai MCP 超时 ({self.timeout}s)") from te
        except Exception as e:
            sanitized = redact_api_key(str(e), self.api_key)
            raise CivitaiApiError(f"请求 Civitai MCP 发生网络异常: {sanitized}") from e

        if resp.status_code == 406:
            raise CivitaiApiError("Civitai MCP 返回 406 Not Acceptable (请核对请求头 Accept 是否包含 application/json 与 text/event-stream)")
        if resp.status_code >= 400 and resp.status_code != 200:
            err_msg = redact_api_key(resp.text[:300], self.api_key)
            raise CivitaiApiError(f"Civitai MCP HTTP 错误 {resp.status_code}: {err_msg}")

        parsed = self._parse_mcp_response(resp)

        # 检查 JSON-RPC 顶层 error
        if "error" in parsed:
            err_obj = parsed["error"]
            code = err_obj.get("code")
            msg = err_obj.get("message") or str(err_obj)
            clean_msg = redact_api_key(msg, self.api_key)
            raise CivitaiApiError(f"Civitai MCP RPC 错误 ({code}): {clean_msg}")

        return parsed

    def handshake(self) -> dict:
        """执行标准 MCP 握手: initialize -> notifications/initialized"""
        init_res = self._call_mcp("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ArtCrossPost", "version": "1.0.0"}
        })
        self._call_mcp("notifications/initialized", is_notification=True)
        return init_res

    def whoami(self) -> dict:
        """调用 whoami 工具获取当前用户状态"""
        res = self._call_mcp("tools/call", {
            "name": "whoami",
            "arguments": {}
        })
        return res

    def upload_media(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        """
        逐文件 base64 上传素材至 upload_image 工具:
        - 探测 MIME 类型
        - 返回 {"uuid": uuid, "width": w, "height": h, "type": "image"|"video"}
        - 宽高若服务端探测缺失，自动用本地探测值补齐
        """
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            raise CivitaiApiError(f"待上传素材文件不存在: {p.name}")

        ext = p.suffix.lower()
        if ext in (".mp4", ".webm"):
            content_type = "video/mp4" if ext == ".mp4" else "video/webm"
            media_type = "video"
        elif ext == ".png":
            content_type = "image/png"
            media_type = "image"
        elif ext in (".jpg", ".jpeg"):
            content_type = "image/jpeg"
            media_type = "image"
        elif ext == ".webp":
            content_type = "image/webp"
            media_type = "image"
        elif ext == ".gif":
            content_type = "image/gif"
            media_type = "image"
        else:
            raise CivitaiApiError(f"不支持的素材类型: {ext}")

        local_w, local_h = probe_local_media_dimensions(p)

        try:
            with open(p, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            raise CivitaiApiError(f"读取素材文件失败 ({p.name}): {e}")

        res = self._call_mcp("tools/call", {
            "name": "upload_image",
            "arguments": {
                "data": b64_data,
                "contentType": content_type
            }
        })

        result_payload = res.get("result", {})
        if result_payload.get("isError"):
            err_content = result_payload.get("content", [{}])[0].get("text", "上传素材失败")
            clean_err = redact_api_key(err_content, self.api_key)
            raise CivitaiApiError(f"Civitai 上传素材失败 ({p.name}): {clean_err}")

        structured = result_payload.get("structuredContent") or {}
        uuid_val = structured.get("uuid")
        if not uuid_val:
            # 尝试从 content 文本匹配 UUID
            text_val = result_payload.get("content", [{}])[0].get("text", "")
            uuid_m = re.search(r"([0-9a-fA-F-]{36})", text_val)
            if uuid_m:
                uuid_val = uuid_m.group(1)

        if not uuid_val:
            raise CivitaiApiError(f"Civitai 上传素材未返回有效 UUID ({p.name})")

        w = structured.get("width") or local_w
        h = structured.get("height") or local_h

        return {
            "uuid": uuid_val,
            "width": int(w),
            "height": int(h),
            "type": media_type
        }

    def create_post(
        self,
        title: str,
        detail_html: str,
        images: List[Dict[str, Any]],
        tags: Optional[List[str]] = None,
        model_version_id: Optional[int] = None,
        publish: bool = True
    ) -> int:
        """
        一次性调用 create_post (publish=True)
        - tags: 标签列表
        - images: [{"uuid": ..., "width": ..., "height": ..., "type": ...}]
        - modelVersionId: 恰 1 个模型版本 ID (若有)
        - detail: 必须传 HTML 渲染版
        """
        if not images:
            raise CivitaiApiError("建帖失败: 图片/视频列表不能为空")

        args: Dict[str, Any] = {
            "title": title or "",
            "detail": detail_html or "",
            "images": images,
            "publish": publish
        }
        if tags and isinstance(tags, (list, tuple)):
            clean_tags = [str(t).strip() for t in tags if str(t).strip()]
            if clean_tags:
                args["tags"] = clean_tags

        if model_version_id is not None:
            try:
                args["modelVersionId"] = int(model_version_id)
            except Exception:
                pass

        try:
            res = self._call_mcp("tools/call", {
                "name": "create_post",
                "arguments": args
            })
        except CivitaiTimeoutError as te:
            # 明确报结果未知，严禁重试
            raise CivitaiTimeoutError("create_post 调用超时，结果未知（防重复发帖，未执行重试）") from te

        result_payload = res.get("result", {})
        if result_payload.get("isError"):
            content_list = result_payload.get("content", [])
            err_text = content_list[0].get("text") if content_list else None
            if not err_text:
                structured_err = result_payload.get("structuredContent", {}).get("error")
                err_text = structured_err or "建帖失败 (isError=True)"
            clean_err = redact_api_key(err_text, self.api_key)
            raise CivitaiApiError(f"Civitai create_post 失败: {clean_err}")

        structured = result_payload.get("structuredContent") or {}
        post_id = structured.get("id")
        image_ids = structured.get("imageIds") or []
        try:
            self.last_created_image_ids = [int(x) for x in image_ids if str(x).isdigit()]
        except Exception:
            self.last_created_image_ids = []

        if not post_id:
            # 尝试从 content 文本解析 ID
            text_val = result_payload.get("content", [{}])[0].get("text", "")
            id_m = re.search(r"(?:Post created|ID:?|Post #)\s*(\d+)", text_val, re.IGNORECASE)
            if id_m:
                post_id = int(id_m.group(1))

        if not post_id:
            raise CivitaiApiError("Civitai create_post 未返回帖子 ID")

        return int(post_id)

    def get_post(self, post_id: int) -> dict:
        """调用 get_post 工具回读帖子状态 (并通过 tRPC 补充 modelVersionId 等高级字段)"""
        res = self._call_mcp("tools/call", {
            "name": "get_post",
            "arguments": {"id": int(post_id)}
        })
        try:
            trpc_url = f"https://civitai.com/api/trpc/post.get?input={json.dumps({'json': {'id': int(post_id)}})}"
            r = self.session.get(
                trpc_url,
                headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
                timeout=10
            )
            if r.status_code == 200:
                t_json = r.json().get("result", {}).get("data", {}).get("json", {})
                m_vid = t_json.get("modelVersionId")
                if m_vid:
                    res.setdefault("result", {}).setdefault("structuredContent", {})["modelVersionId"] = m_vid
        except Exception as e:
            logger.debug(f"[Civitai API] tRPC 补充 modelVersionId 略过: {e}")
        return res

    def delete_post(self, post_id: int) -> dict:
        """调用 delete_post 工具删除帖子 (用于失败清理或孤儿帖清理)"""
        res = self._call_mcp("tools/call", {
            "name": "delete_post",
            "arguments": {"id": int(post_id)}
        })
        return res

    def poll_rest_images(
        self,
        post_id: int,
        expected_count: Union[int, Collection[int]],
        max_retries: int = 5,
        delay: float = 3.0
    ) -> Tuple[bool, List[dict], str]:
        """
        轮询 REST GET /api/v1/images?postId= 核验图片数量
        ≤5 次 × 3 秒
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        }
        url = f"{self.rest_url}?postId={post_id}"

        expected_set = {expected_count} if isinstance(expected_count, int) else set(expected_count)
        last_count = 0
        last_items = []
        for attempt in range(1, max_retries + 1):
            time.sleep(delay)
            try:
                r = self.session.get(url, headers=headers, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    items = data.get("items", [])
                    last_items = items
                    last_count = len(items)
                    if last_count in expected_set:
                        return True, items, f"第 {attempt} 次轮询核验成功 (媒体数: {last_count})"
            except Exception as e:
                logger.warning(f"[Civitai API] REST 轮询第 {attempt} 次异常: {e}")

        exp_str = str(expected_count) if isinstance(expected_count, int) else "/".join(map(str, sorted(expected_set)))
        return False, last_items, f"REST 轮询 {max_retries} 次后媒体数量仍不匹配 (当前: {last_count}, 预期: {exp_str})"


def publish_via_api(
    payload: dict,
    api_key: str,
    model_version_id: Optional[int] = None,
    channel_notes: Optional[List[str]] = None
) -> Tuple[bool, str, List[dict]]:
    """
    通过 Civitai API 官方通道完成发布与成功断言:
    1. 门禁与素材处理 (复用 prepare_civitai_media，GIF 配对视频替换、上限门禁保持一致)
    2. 签名描述 HTML 转换 (复用 render_description_to_html)
    3. MCP 握手与逐素材 upload_image 上传
    4. 一次性调用 create_post (publish=True)
    5. 成功断言链:
       ① create_post 返回帖 ID 且无 isError
       ② get_post 回读 published == true (仅回读可退避重试一次)
       ③ REST /api/v1/images?postId= 轮询媒体数 == 本帖媒体数
    6. 失败处理: 若建帖后断言未通过，立即尝试 delete_post 清理并注明
    """
    title = (payload.get("title") or "").strip()
    desc = (payload.get("description") or "").strip()
    tags = payload.get("tags") or []
    x_video_paths = payload.get("x_video_paths") or payload.get("platform_options", {}).get("x_video_paths") or {}
    max_limit = (
        payload.get("civitai_max_images_per_post")
        or payload.get("platform_options", {}).get("civitai_max_images_per_post")
        or 20
    )

    media_candidates = (
        payload.get("civitai_images")
        or payload.get("raw_images")
        or payload.get("images")
        or []
    )

    notes = list(channel_notes or [])

    # 1. 媒体过滤与自检 (复用现行规范)
    try:
        accepted_media, skipped_notes = prepare_civitai_media(
            media_candidates,
            x_video_paths=x_video_paths,
            max_limit=max_limit
        )
    except ValueError as ve:
        err_msg = redact_api_key(str(ve), api_key)
        logger.error(f"[Civitai API] 素材门禁拦截: {err_msg}")
        return False, err_msg, []

    if skipped_notes:
        notes.extend(skipped_notes)

    # 工具标注说明: API 通道不带工具标注，仅在用户配置了 tool 时明示未携带标注
    tool_val = payload.get("civitai_tool") or payload.get("platform_options", {}).get("civitai_tool")
    has_tool = bool(tool_val and str(tool_val).strip())
    tool_note = "Civitai（API）：未携带工具标注" if has_tool else None
    if tool_note:
        notes.append(tool_note)

    # 2. 描述转换为 HTML
    detail_html = render_description_to_html(desc)

    client = CivitaiApiClient(api_key=api_key)

    # 3. MCP 握手
    try:
        client.handshake()
    except Exception as e:
        err_msg = redact_api_key(str(e), api_key)
        logger.error(f"[Civitai API] 握手失败: {err_msg}")
        return False, f"Civitai（API）：握手失败: {err_msg}", []

    # 4. 上传素材
    uploaded_images: List[Dict[str, Any]] = []
    for m_path in accepted_media:
        try:
            up_info = client.upload_media(m_path)
            uploaded_images.append(up_info)
        except Exception as e:
            err_msg = redact_api_key(str(e), api_key)
            logger.error(f"[Civitai API] 上传素材失败 ({Path(m_path).name}): {err_msg}")
            return False, f"Civitai（API）：上传素材失败: {err_msg}", []

    # 5. 建帖
    post_id: Optional[int] = None
    try:
        post_id = client.create_post(
            title=title,
            detail_html=detail_html,
            images=uploaded_images,
            tags=tags,
            model_version_id=model_version_id,
            publish=True
        )
    except CivitaiTimeoutError as te:
        # 建帖超时报"结果未知"、禁止盲目重试建帖（防重复帖）
        err_msg = redact_api_key(str(te), api_key)
        logger.error(f"[Civitai API] 建帖超时: {err_msg}")
        return False, f"Civitai（API）：create_post 调用超时，结果未知（防重复发帖，未执行重试）", []
    except Exception as e:
        err_msg = redact_api_key(str(e), api_key)
        logger.error(f"[Civitai API] 建帖失败: {err_msg}")
        return False, f"Civitai（API）：建帖失败: {err_msg}", []

    post_url = f"{POSTS_PAGE_URL}/{post_id}"

    # 6. 建帖后成功断言链 (断言失败必须尝试 delete_post 清理并注明)
    try:
        # 断言 2: get_post 回读 published == true (仅回读可退避重试一次)
        gp_res = None
        try:
            gp_res = client.get_post(post_id)
        except Exception as gp_err:
            logger.warning(f"[Civitai API] get_post 首次回读异常 ({gp_err})，退避 1.5s 重试一次...")
            time.sleep(1.5)
            gp_res = client.get_post(post_id)

        gp_payload = gp_res.get("result", {})
        if gp_payload.get("isError"):
            clean_err = redact_api_key(gp_payload.get("content", [{}])[0].get("text", "get_post 失败"), api_key)
            raise CivitaiAssertionError(f"get_post 回读失败: {clean_err}")

        gp_struct = gp_payload.get("structuredContent") or {}
        is_published = bool(gp_struct.get("published") or gp_struct.get("publishedAt"))
        if not is_published:
            raise CivitaiAssertionError("get_post 回读 published 状态不为 true")

        # 断言 3: REST GET /api/v1/images?postId= 轮询（≤5 次×3 秒）
        # 区分静态图片与视频：
        # - 静态图片：Civitai 服务端同步完成扫描，REST /api/v1/images 返回全部图片对象
        # - 视频媒体：Civitai 需经后台异步 GPU 转码，初始处于 ingestion: Pending/Analyzing 队列，
        #   在 MCP 建帖阶段已分配并核验对应数量的 imageIds，REST 接口在转码完成前不暴露未完成视频。
        image_media = [m for m in uploaded_images if m.get("type") != "video"]
        video_media = [m for m in uploaded_images if m.get("type") == "video"]

        rest_items = []
        expected_counts = {len(image_media), len(uploaded_images)} if video_media else {len(uploaded_images)}
        rest_ok, rest_items, rest_msg = client.poll_rest_images(
            post_id=post_id,
            expected_count=expected_counts,
            max_retries=5 if image_media else 1,
            delay=3.0 if image_media else 1.0
        )
        if not rest_ok:
            raise CivitaiAssertionError(rest_msg)

        if video_media:
            created_ids = getattr(client, "last_created_image_ids", [])
            if not created_ids or len(created_ids) != len(uploaded_images):
                actual_cnt = len(created_ids) if created_ids else 0
                raise CivitaiAssertionError(
                    f"含视频帖子素材未在建帖时完整绑定 imageIds (预期 {len(uploaded_images)}, 实际 {actual_cnt})"
                )
            notes.append("视频转码已在后台排队处理")

        # 提取回读详情
        readback_tags = set()
        for item in rest_items:
            for tg in item.get("tags", []):
                if isinstance(tg, dict) and "name" in tg:
                    readback_tags.add(tg["name"])
                elif isinstance(tg, str):
                    readback_tags.add(tg)

    except Exception as assertion_err:
        clean_assert_err = redact_api_key(str(assertion_err), api_key)
        logger.error(f"[Civitai API] 成功断言未通过: {clean_assert_err}，尝试删除清理草稿帖 #{post_id}...")

        cleanup_note = ""
        try:
            client.delete_post(post_id)
            cleanup_note = f"（已自动调用 delete_post 销毁未达标测试帖 #{post_id}）"
            logger.info(f"[Civitai API] 已成功清理测试帖 #{post_id}")
        except Exception as del_err:
            cleanup_note = f"（尝试清理测试帖 #{post_id} 失败: {redact_api_key(str(del_err), api_key)}）"
            logger.error(f"[Civitai API] 清理测试帖 #{post_id} 失败: {cleanup_note}")

        return False, f"Civitai（API）：成功断言未通过: {clean_assert_err} {cleanup_note}", []

    # 全部断言通过，组装成功结果
    rest_video_count = sum(
        1 for item in rest_items
        if isinstance(item, dict) and (
            item.get("type") == "video"
            or str(item.get("mimeType", "")).startswith("video/")
        )
    )
    unindexed_video_count = max(0, len(video_media) - rest_video_count)
    verified_count = len(rest_items) + unindexed_video_count

    compliance: List[Dict[str, Any]] = [
        {"category": "channel", "channel": "api", "detail": "API 官方通道"},
        {"category": "post_id", "post_id": post_id, "url": post_url},
        {"category": "media_count", "uploaded": len(uploaded_images), "verified": verified_count},
    ]
    if tool_note:
        compliance.append({"category": "tool_annotation", "status": "omitted", "detail": tool_note})
    if video_media:
        compliance.append({"category": "video_status", "status": "queued_transcoding", "video_count": len(video_media)})
    if model_version_id:
        compliance.append({"category": "model_version", "modelVersionId": model_version_id})
    if readback_tags:
        compliance.append({"category": "readback_tags", "tags": sorted(list(readback_tags))})

    notes_str = f" ({'; '.join(notes)})" if notes else ""
    success_msg = f"{post_url} [API 官方通道]{notes_str}"
    return True, success_msg, compliance
