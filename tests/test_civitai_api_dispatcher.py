# -*- coding: utf-8 -*-
"""
ArtCrossPost - Civitai API Dispatcher & Protocol Tests
覆盖 Civitai API 官方通道、通道判定矩阵、MCP 请求头与握手、
Payload 正确性、错误映射、断言链与自动清理、以及 Key 绝对卫生扫描。
"""
import io
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from PIL import Image

from dispatchers.civitai_api import (
    CivitaiApiClient,
    CivitaiApiError,
    CivitaiTimeoutError,
    CivitaiAssertionError,
    resolve_civitai_channel,
    resolve_civitai_model_version,
    CIVITAI_MODELS_API_URL,
    get_civitai_api_key,
    save_civitai_api_key,
    clear_civitai_credentials,
    mask_civitai_api_key,
    redact_api_key,
    publish_via_api,
    probe_local_media_dimensions
)
from dispatchers.civitai import CivitaiDispatcher
from signature_utils import CANONICAL_SIGNATURE_TEMPLATE


# =====================================================================
# 1. 通道判定矩阵测试 (资源 0/1/2+ × 三态 × key 有无)
# 针对 1 个资源形态，完整覆盖真实四种形态：
# 纯模型 ID、模型 URL、{id:模型, version:名称}、{id:模型, version:数字串}
# =====================================================================
@pytest.mark.parametrize("resources,count_label,shape_type", [
    ([], "0", "none"),
    ([257749], "1", "pure_id"),
    (["https://civitai.com/models/257749"], "1", "url"),
    ([{"id": 257749, "version": "V6"}], "1", "name"),
    ([{"id": 257749, "version": "290640"}], "1", "digit_str"),
    ([257749, 123456], "2+", "multi"),
])
@pytest.mark.parametrize("channel_setting", ["browser", "api", "auto"])
@pytest.mark.parametrize("has_key", [True, False])
def test_channel_resolution_matrix(monkeypatch, resources, count_label, shape_type, channel_setting, has_key):
    """验证通道判定矩阵全组合行为，覆盖真实四种资源形态与版本解析"""
    api_key = "sentinel_test_key_123" if has_key else None

    # Mock REST 模型元数据查询
    def mock_get(url, *args, **kwargs):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json.dumps({
            "id": 257749,
            "name": "Pony Diffusion V6 XL",
            "modelVersions": [
                {"id": 290640, "name": "V6 (start with this one)"},
                {"id": 298112, "name": "V6 Turbo DPO merge"}
            ]
        }).encode("utf-8")
        return resp

    monkeypatch.setattr(requests, "get", mock_get)

    # 1. 显式指定 browser: 恒为 browser
    if channel_setting == "browser":
        ch, note, m_id = resolve_civitai_channel(channel_setting, resources, api_key)
        assert ch == "browser"
        assert m_id is None

    # 2. 显式指定 api:
    elif channel_setting == "api":
        if not has_key:
            with pytest.raises(CivitaiApiError) as exc_info:
                resolve_civitai_channel(channel_setting, resources, api_key)
            assert "未配置 Civitai API Key" in str(exc_info.value)
        else:
            ch, note, m_id = resolve_civitai_channel(channel_setting, resources, api_key)
            assert ch == "api"
            if count_label == "0":
                assert m_id is None
            elif count_label == "1":
                assert m_id == 290640
                if shape_type in ("pure_id", "url"):
                    assert "已降级默认版本 (290640)" in note
                else:
                    assert note is None
            elif count_label == "2+":
                assert m_id == 290640
                assert "API 通道仅支持绑定 1 个模型版本" in note

    # 3. 自动模式 auto:
    elif channel_setting == "auto":
        ch, note, m_id = resolve_civitai_channel(channel_setting, resources, api_key)
        if count_label == "2+":
            # 资源数 >= 2 回退浏览器通道
            assert ch == "browser"
            assert note == "已回退浏览器通道（资源 2 个）"
            assert m_id is None
        elif not has_key:
            # 资源数 <= 1 但无 Key: 回退浏览器通道
            assert ch == "browser"
            assert note == "已回退浏览器通道（未配置 API Key）"
            assert m_id is None
        else:
            # 资源数 <= 1 且有 Key: 走 API 通道
            assert ch == "api"
            if count_label == "1":
                assert m_id == 290640
                if shape_type in ("pure_id", "url"):
                    assert "已降级默认版本 (290640)" in note
                else:
                    assert note is None
            else:
                assert m_id is None


def test_model_version_resolution_four_cases(monkeypatch):
    """
    必修 1 专项单测：覆盖模型版本解析四情形：
    ① 数字命中列表中的版本 ID
    ② 名称不区分大小写匹配
    ③ 降级默认（未指定版本或未匹配到，取首个最新版并注明降级）
    ④ 查不到/HTTP 失败（按未绑定处理并注明，绝不传出模型 ID）
    """
    mock_model_data = {
        "id": 257749,
        "name": "Pony Diffusion V6 XL",
        "modelVersions": [
            {"id": 290640, "name": "V6 (start with this one)"},
            {"id": 298112, "name": "V6 Turbo DPO merge"}
        ]
    }

    def mock_get(url, *args, **kwargs):
        resp = requests.Response()
        if "257749" in url:
            resp.status_code = 200
            resp._content = json.dumps(mock_model_data).encode("utf-8")
        elif "empty_ver" in url or "777777" in url:
            resp.status_code = 200
            resp._content = json.dumps({"id": 777777, "modelVersions": []}).encode("utf-8")
        elif "network_err" in url or "888888" in url:
            raise requests.ConnectionError("Simulated connection error")
        else:
            resp.status_code = 404
            resp._content = b'{"error": "Model not found"}'
        return resp

    monkeypatch.setattr(requests, "get", mock_get)

    # ① 数字命中：纯数字且命中列表中版本 ID -> 用它，无降级说明
    vid1, note1 = resolve_civitai_model_version({"id": 257749, "version": "298112"})
    assert vid1 == 298112
    assert note1 is None

    # ② 名称命中：按 name 不区分大小写匹配 -> 用它，无降级说明
    vid2, note2 = resolve_civitai_model_version({"id": 257749, "version": "turbo"})
    assert vid2 == 298112
    assert note2 is None

    vid2b, note2b = resolve_civitai_model_version({"id": 257749, "version": "v6 (START WITH THIS ONE)"})
    assert vid2b == 290640
    assert note2b is None

    # ③ 降级默认：未指定版本或匹配不到 -> 取列表第一个并注明降级
    # 3a. 未指定版本 (纯模型 ID / 模型 URL)
    vid3a, note3a = resolve_civitai_model_version(257749)
    assert vid3a == 290640
    assert "未指定版本，已降级默认版本 (290640)" in note3a

    vid3a_url, note3a_url = resolve_civitai_model_version("https://civitai.com/models/257749")
    assert vid3a_url == 290640
    assert "未指定版本，已降级默认版本 (290640)" in note3a_url

    # 3b. 指定了版本但匹配不到
    vid3b, note3b = resolve_civitai_model_version({"id": 257749, "version": "NonExistentVerXYZ"})
    assert vid3b == 290640
    assert "未匹配到版本 'NonExistentVerXYZ'，已降级默认版本 (290640)" in note3b

    # ④ 查不到/失败 -> 按未绑定资源处理并注明；任何情况下不许把模型 ID 当 modelVersionId 传出
    # 4a. HTTP 404
    vid4a, note4a = resolve_civitai_model_version(999999)
    assert vid4a is None
    assert vid4a != 999999
    assert "模型 999999 查询失败，已按未绑定模型版本处理" in note4a

    # 4b. 网络异常
    vid4b, note4b = resolve_civitai_model_version(888888)
    assert vid4b is None
    assert vid4b != 888888
    assert "模型 888888 查询失败，已按未绑定模型版本处理" in note4b

    # 4c. modelVersions 为空列表
    vid4c, note4c = resolve_civitai_model_version(777777)
    assert vid4c is None
    assert vid4c != 777777
    assert "模型 777777 无可用版本，已按未绑定模型版本处理" in note4c


def test_channel_resolution_parse_failure():
    """资源解析失败按未配处理并在结果注明"""
    api_key = "sentinel_key"
    invalid_resources = ["pure_model_name_invalid"]
    ch, note, m_id = resolve_civitai_channel("auto", invalid_resources, api_key)
    assert ch == "api"
    assert m_id is None
    assert "资源配置解析失败，已按未绑定模型版本处理" in note


# =====================================================================
# 2. 请求头、握手与 Session ID 复用测试
# =====================================================================
def test_mcp_handshake_and_headers(monkeypatch):
    """验证请求头必含 Bearer 与 Accept 双 MIME，且服务端 session id 被后续复用"""
    recorded_requests = []

    def mock_post(url, headers, json, timeout):
        recorded_requests.append({"url": url, "headers": headers, "json": json})
        resp = requests.Response()
        resp.status_code = 200
        # 模拟 initialize 时服务端在响应头下发 Mcp-Session-Id
        if json.get("method") == "initialize":
            resp.headers["Mcp-Session-Id"] = "sess_mock_uuid_888"
            resp._content = b'{"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "civitai"}}}'
        elif json.get("method") == "notifications/initialized":
            resp._content = b'{}'
        elif json.get("method") == "tools/call" and json.get("params", {}).get("name") == "whoami":
            resp._content = b'{"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "FOXTONTONTON"}]}}'
        else:
            resp._content = b'{"jsonrpc": "2.0", "result": {}}'
        return resp

    session_mock = requests.Session()
    monkeypatch.setattr(session_mock, "post", mock_post)

    client = CivitaiApiClient(api_key="my_secret_token_abc", session=session_mock)
    client.handshake()

    # 验证 initialize 调用
    req1 = recorded_requests[0]
    assert req1["headers"]["Authorization"] == "Bearer my_secret_token_abc"
    assert "application/json" in req1["headers"]["Accept"]
    assert "text/event-stream" in req1["headers"]["Accept"]
    assert req1["json"]["method"] == "initialize"

    # 验证 notifications/initialized 调用
    req2 = recorded_requests[1]
    assert req2["json"]["method"] == "notifications/initialized"
    # 验证 Mcp-Session-Id 已被捕获并携带
    assert req2["headers"].get("Mcp-Session-Id") == "sess_mock_uuid_888"

    # 验证后续 whoami 调用依然复用 Mcp-Session-Id
    client.whoami()
    req3 = recorded_requests[2]
    assert req3["headers"].get("Mcp-Session-Id") == "sess_mock_uuid_888"


def test_mcp_406_error_handling(monkeypatch):
    """缺 Accept 头或服务端返回 406 时抛出明确错误"""
    def mock_post(url, headers, json, timeout):
        resp = requests.Response()
        resp.status_code = 406
        resp._content = b'Not Acceptable'
        return resp

    session_mock = requests.Session()
    monkeypatch.setattr(session_mock, "post", mock_post)
    client = CivitaiApiClient(api_key="key", session=session_mock)
    with pytest.raises(CivitaiApiError) as exc_info:
        client.handshake()
    assert "406 Not Acceptable" in str(exc_info.value)


# =====================================================================
# 3. Payload 正确性 (detail HTML、签名真链接、单资源 modelVersionId、图序与 video type)
# =====================================================================
def test_create_post_payload_correctness(tmp_path, monkeypatch):
    """验证 create_post 传参: detail 为 HTML 带真超链接、单资源才有 modelVersionId、图片顺序与 video 标识"""
    img1 = tmp_path / "img1.png"
    img1.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")
    vid2 = tmp_path / "vid2.mp4"
    vid2.write_bytes(b"dummy_mp4_bytes")

    called_args = []

    def mock_post(url, headers, json, timeout):
        resp = requests.Response()
        resp.status_code = 200
        method = json.get("method")
        params = json.get("params", {})
        if method == "initialize":
            resp._content = b'{"jsonrpc": "2.0", "result": {}}'
        elif method == "notifications/initialized":
            resp._content = b'{}'
        elif method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {})
            called_args.append((tool_name, args))
            if tool_name == "upload_image":
                ct = args.get("contentType")
                uuid_str = "uuid_img_1" if "image" in ct else "uuid_vid_2"
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {
                        "structuredContent": {"uuid": uuid_str, "width": 800, "height": 600}
                    }
                })
            elif tool_name == "create_post":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {
                        "structuredContent": {
                            "id": 334455,
                            "published": True,
                            "publishedAt": "2026-10-30T20:00:00Z",
                            "imageIds": [101, 102]
                        }
                    }
                })
            elif tool_name == "get_post":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {
                        "structuredContent": {"id": 334455, "published": True, "publishedAt": "2026-10-30T20:00:00Z"}
                    }
                })
        return resp

    def mock_get(url, headers, timeout):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json_dump_bytes({
            "items": [
                {"id": 1, "tags": [{"name": "anime"}]},
                {"id": 2, "tags": [{"name": "anime"}]}
            ]
        })
        return resp

    session_mock = requests.Session()
    monkeypatch.setattr(session_mock, "post", mock_post)
    monkeypatch.setattr(session_mock, "get", mock_get)
    monkeypatch.setattr(requests, "Session", lambda: session_mock)

    payload = {
        "title": "Payload Test Title",
        "description": "Art body description.\n\n" + CANONICAL_SIGNATURE_TEMPLATE,
        "tags": ["anime", "illustration"],
        "civitai_images": [str(img1), str(vid2)],
        "civitai_tool": "ComfyUI"
    }

    ok, msg, compliance = publish_via_api(
        payload=payload,
        api_key="test_key_xyz",
        model_version_id=290640,
        channel_notes=["API 官方通道"]
    )
    assert ok is True
    assert "https://civitai.com/posts/334455" in msg
    assert "Civitai（API）：未携带工具标注" in msg

    # 核验 create_post 的调用参数
    create_call = [args for name, args in called_args if name == "create_post"][0]
    assert create_call["title"] == "Payload Test Title"
    assert create_call["publish"] is True
    assert create_call["modelVersionId"] == 290640
    assert create_call["tags"] == ["anime", "illustration"]

    # 验证 detail 为 HTML 且签名包含真超链接 <a> 且无未解析的 Markdown
    detail_html = create_call["detail"]
    assert detail_html.startswith("<p>Art body description.</p>")
    assert '<a href="https://x.com/FOXTONTONTON"' in detail_html
    assert '<a href="https://bsky.app/profile/foxtontonton.bsky.social"' in detail_html
    assert "[FOXTONTONTON]" not in detail_html

    # 验证图序与媒体类型 (图1: image, 图2: video)
    imgs = create_call["images"]
    assert len(imgs) == 2
    assert imgs[0]["uuid"] == "uuid_img_1"
    assert imgs[0]["type"] == "image"
    assert imgs[1]["uuid"] == "uuid_vid_2"
    assert imgs[1]["type"] == "video"


# =====================================================================
# 4. 错误映射 (-32602、业务错误带原话、isError 绝不报成功、超时结果未知)
# =====================================================================
def test_schema_validation_error_verbatim(monkeypatch):
    """验证 -32602 校验错误原样抛出并作为失败原因"""
    def mock_post(url, headers, json, timeout):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json_dump_bytes({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "MCP error -32602: Input validation error: Invalid arguments for tool create_post: Array must contain at least 1 element(s)"}],
                "isError": True
            }
        })
        return resp

    session_mock = requests.Session()
    monkeypatch.setattr(session_mock, "post", mock_post)
    client = CivitaiApiClient(api_key="key", session=session_mock)

    with pytest.raises(CivitaiApiError) as exc_info:
        client.create_post(title="T", detail_html="<p></p>", images=[{"uuid": "u1"}])
    assert "MCP error -32602: Input validation error" in str(exc_info.value)


def test_business_error_verbatim(monkeypatch):
    """验证业务错误 (如 Model version not found) 原话带出"""
    def mock_post(url, headers, json, timeout):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json_dump_bytes({
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [{"type": "text", "text": "Error: post.createWithImages: Model version not found."}],
                "isError": True
            }
        })
        return resp

    session_mock = requests.Session()
    monkeypatch.setattr(session_mock, "post", mock_post)
    client = CivitaiApiClient(api_key="key", session=session_mock)

    with pytest.raises(CivitaiApiError) as exc_info:
        client.create_post(title="T", detail_html="<p></p>", images=[{"uuid": "u1"}], model_version_id=999999)
    assert "Model version not found" in str(exc_info.value)


def test_create_post_timeout_reports_unknown_result(tmp_path, monkeypatch):
    """验证建帖超时报 '结果未知'，且严禁重试"""
    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    post_call_count = 0

    def mock_post(url, headers, json, timeout):
        nonlocal post_call_count
        resp = requests.Response()
        resp.status_code = 200
        method = json.get("method")
        params = json.get("params", {})
        if method == "initialize":
            resp._content = b'{"jsonrpc": "2.0", "result": {}}'
        elif method == "notifications/initialized":
            resp._content = b'{}'
        elif method == "tools/call":
            tool_name = params.get("name")
            if tool_name == "upload_image":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"uuid": "u_1", "width": 100, "height": 100}}
                })
            elif tool_name == "create_post":
                post_call_count += 1
                raise requests.Timeout("Network timed out")
        return resp

    session_mock = requests.Session()
    monkeypatch.setattr(session_mock, "post", mock_post)
    monkeypatch.setattr(requests, "Session", lambda: session_mock)

    payload = {"title": "T", "images": [str(img)]}
    ok, msg, comp = publish_via_api(payload=payload, api_key="key")
    assert ok is False
    assert "结果未知" in msg
    assert "未执行重试" in msg
    # 确保没有盲目重试建帖
    assert post_call_count == 1


# =====================================================================
# 5. 断言链与自动清理 (未 published / 计数不符 -> 失败且调用 delete_post)
# =====================================================================
def test_assertion_failure_triggers_delete_post(tmp_path, monkeypatch):
    """建帖后断言 published != true 触发 delete_post 清理孤儿帖"""
    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    deleted_post_ids = []

    def mock_post(url, headers, json, timeout):
        resp = requests.Response()
        resp.status_code = 200
        method = json.get("method")
        params = json.get("params", {})
        if method == "initialize":
            resp._content = b'{"jsonrpc": "2.0", "result": {}}'
        elif method == "notifications/initialized":
            resp._content = b'{}'
        elif method == "tools/call":
            tool = params.get("name")
            if tool == "upload_image":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"uuid": "u_1", "width": 100, "height": 100}}
                })
            elif tool == "create_post":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"id": 998877, "published": False}}
                })
            elif tool == "get_post":
                # 回读显示未发布
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"id": 998877, "published": False, "publishedAt": None}}
                })
            elif tool == "delete_post":
                deleted_post_ids.append(params.get("arguments", {}).get("id"))
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"ok": True, "id": 998877}}
                })
        return resp

    session_mock = requests.Session()
    monkeypatch.setattr(session_mock, "post", mock_post)
    monkeypatch.setattr(requests, "Session", lambda: session_mock)

    payload = {"title": "Assert Fail Test", "images": [str(img)]}
    ok, msg, comp = publish_via_api(payload=payload, api_key="key")
    assert ok is False
    assert "成功断言未通过" in msg
    assert "published 状态不为 true" in msg
    assert "已自动调用 delete_post 销毁未达标测试帖 #998877" in msg
    # 断言 delete_post 被正确调用
    assert 998877 in deleted_post_ids


def test_rest_image_count_mismatch_triggers_delete(tmp_path, monkeypatch):
    """REST /api/v1/images 轮询媒体数不符触发断言失败与 delete_post"""
    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    deleted_post_ids = []

    def mock_post(url, headers, json, timeout):
        resp = requests.Response()
        resp.status_code = 200
        method = json.get("method")
        params = json.get("params", {})
        if method == "initialize":
            resp._content = b'{"jsonrpc": "2.0", "result": {}}'
        elif method == "notifications/initialized":
            resp._content = b'{}'
        elif method == "tools/call":
            tool = params.get("name")
            if tool == "upload_image":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"uuid": "u_1", "width": 100, "height": 100}}
                })
            elif tool == "create_post":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"id": 112233, "published": True}}
                })
            elif tool == "get_post":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"id": 112233, "published": True, "publishedAt": "2026-10-30T12:00:00Z"}}
                })
            elif tool == "delete_post":
                deleted_post_ids.append(params.get("arguments", {}).get("id"))
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"ok": True, "id": 112233}}
                })
        return resp

    def mock_get(url, headers, timeout):
        # 模拟返回空列表，永远达不到预期数量 1
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json_dump_bytes({"items": []})
        return resp

    session_mock = requests.Session()
    monkeypatch.setattr(session_mock, "post", mock_post)
    monkeypatch.setattr(session_mock, "get", mock_get)
    monkeypatch.setattr(requests, "Session", lambda: session_mock)

    # 降低 poll delay 以加快测试速度
    with patch("time.sleep", return_value=None):
        payload = {"title": "REST Mismatch Test", "images": [str(img)]}
        ok, msg, comp = publish_via_api(payload=payload, api_key="key")

    assert ok is False
    assert "REST 轮询" in msg
    assert "媒体数量仍不匹配" in msg
    assert 112233 in deleted_post_ids



# =====================================================================
# 5b. 视频断言 fail-closed 与顺手优化单测
# =====================================================================
def test_video_assertion_fail_closed_when_image_ids_missing_or_insufficient(tmp_path, monkeypatch):
    """
    必修 2 专项单测：含视频时 create_post 返回的 imageIds 缺失或数量不足立即抛出 CivitaiAssertionError 并触发清理
    """
    img1 = tmp_path / "img1.png"
    img1.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")
    vid2 = tmp_path / "vid2.mp4"
    vid2.write_bytes(b"dummy_mp4_bytes")

    deleted_post_ids = []

    def make_mock_post(returned_image_ids):
        def mock_post(url, headers, json, timeout):
            resp = requests.Response()
            resp.status_code = 200
            method = json.get("method")
            params = json.get("params", {})
            if method == "initialize":
                resp._content = b'{"jsonrpc": "2.0", "result": {}}'
            elif method == "notifications/initialized":
                resp._content = b'{}'
            elif method == "tools/call":
                tool = params.get("name")
                if tool == "upload_image":
                    resp._content = json_dump_bytes({
                        "jsonrpc": "2.0",
                        "result": {"structuredContent": {"uuid": "u_test", "width": 100, "height": 100}}
                    })
                elif tool == "create_post":
                    resp._content = json_dump_bytes({
                        "jsonrpc": "2.0",
                        "result": {
                            "structuredContent": {
                                "id": 556677,
                                "published": True,
                                "imageIds": returned_image_ids
                            }
                        }
                    })
                elif tool == "get_post":
                    resp._content = json_dump_bytes({
                        "jsonrpc": "2.0",
                        "result": {"structuredContent": {"id": 556677, "published": True, "publishedAt": "2026-10-30T10:00:00Z"}}
                    })
                elif tool == "delete_post":
                    deleted_post_ids.append(params.get("arguments", {}).get("id"))
                    resp._content = json_dump_bytes({
                        "jsonrpc": "2.0",
                        "result": {"structuredContent": {"ok": True}}
                    })
            return resp
        return mock_post

    def mock_get(url, headers, timeout):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json_dump_bytes({"items": [{"id": 1}]})
        return resp

    payload = {"title": "Video Fail Closed Test", "civitai_images": [str(img1), str(vid2)]}

    # Case A: imageIds 为空列表 [] (修复前会被静默跳过，现必须 fail-closed 触发清理)
    deleted_post_ids.clear()
    session_mock_a = requests.Session()
    monkeypatch.setattr(session_mock_a, "post", make_mock_post([]))
    monkeypatch.setattr(session_mock_a, "get", mock_get)
    monkeypatch.setattr(requests, "Session", lambda: session_mock_a)

    ok_a, msg_a, _ = publish_via_api(payload=payload, api_key="key")
    assert ok_a is False
    assert "含视频帖子素材未在建帖时完整绑定 imageIds" in msg_a
    assert 556677 in deleted_post_ids

    # Case B: imageIds 数量不足 (预期 2 个，实际返回 1 个 [101])
    deleted_post_ids.clear()
    session_mock_b = requests.Session()
    monkeypatch.setattr(session_mock_b, "post", make_mock_post([101]))
    monkeypatch.setattr(session_mock_b, "get", mock_get)
    monkeypatch.setattr(requests, "Session", lambda: session_mock_b)

    ok_b, msg_b, _ = publish_via_api(payload=payload, api_key="key")
    assert ok_b is False
    assert "预期 2, 实际 1" in msg_b
    assert 556677 in deleted_post_ids


def test_verified_count_dedupes_video_in_rest(tmp_path, monkeypatch):
    """顺手优化 1：REST 中已包含视频时避免在 verified_count 中重复计数"""
    img1 = tmp_path / "img1.png"
    img1.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")
    vid2 = tmp_path / "vid2.mp4"
    vid2.write_bytes(b"dummy_mp4_bytes")

    def mock_post(url, headers, json, timeout):
        resp = requests.Response()
        resp.status_code = 200
        method = json.get("method")
        params = json.get("params", {})
        if method == "initialize":
            resp._content = b'{"jsonrpc": "2.0", "result": {}}'
        elif method == "notifications/initialized":
            resp._content = b'{}'
        elif method == "tools/call":
            tool = params.get("name")
            if tool == "upload_image":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"uuid": "u_test", "width": 100, "height": 100}}
                })
            elif tool == "create_post":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"id": 889900, "published": True, "imageIds": [1, 2]}}
                })
            elif tool == "get_post":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"id": 889900, "published": True, "publishedAt": "2026-10-30T10:00:00Z"}}
                })
        return resp

    def mock_get(url, headers, timeout):
        resp = requests.Response()
        resp.status_code = 200
        # REST 已同步包含 1 个 image 和 1 个 video
        resp._content = json_dump_bytes({
            "items": [
                {"id": 1, "type": "image"},
                {"id": 2, "type": "video"}
            ]
        })
        return resp

    session_mock = requests.Session()
    monkeypatch.setattr(session_mock, "post", mock_post)
    monkeypatch.setattr(session_mock, "get", mock_get)
    monkeypatch.setattr(requests, "Session", lambda: session_mock)

    payload = {"title": "Dedupe Video Test", "civitai_images": [str(img1), str(vid2)]}
    ok, msg, compliance = publish_via_api(payload=payload, api_key="key")
    assert ok is True
    # 验证 media_count 的 verified 为 2 (而不是 2 + 1 = 3)
    mc = [c for c in compliance if c.get("category") == "media_count"][0]
    assert mc["uploaded"] == 2
    assert mc["verified"] == 2


def test_tool_note_only_added_when_configured(tmp_path, monkeypatch):
    """顺手优化 2：未配置 tool 时不加 'Civitai（API）：未携带工具标注' 提示"""
    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    def mock_post(url, headers, json, timeout):
        resp = requests.Response()
        resp.status_code = 200
        method = json.get("method")
        params = json.get("params", {})
        if method == "initialize":
            resp._content = b'{"jsonrpc": "2.0", "result": {}}'
        elif method == "notifications/initialized":
            resp._content = b'{}'
        elif method == "tools/call":
            tool = params.get("name")
            if tool == "upload_image":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"uuid": "u_test", "width": 100, "height": 100}}
                })
            elif tool == "create_post":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"id": 123456, "published": True, "imageIds": [1]}}
                })
            elif tool == "get_post":
                resp._content = json_dump_bytes({
                    "jsonrpc": "2.0",
                    "result": {"structuredContent": {"id": 123456, "published": True, "publishedAt": "2026-10-30T10:00:00Z"}}
                })
        return resp

    def mock_get(url, headers, timeout):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json_dump_bytes({"items": [{"id": 1}]})
        return resp

    session_mock = requests.Session()
    monkeypatch.setattr(session_mock, "post", mock_post)
    monkeypatch.setattr(session_mock, "get", mock_get)
    monkeypatch.setattr(requests, "Session", lambda: session_mock)

    # 1. 未配置 tool
    payload_no_tool = {"title": "No Tool", "civitai_images": [str(img)]}
    ok1, msg1, comp1 = publish_via_api(payload=payload_no_tool, api_key="key")
    assert ok1 is True
    assert "Civitai（API）：未携带工具标注" not in msg1
    assert not any(c.get("category") == "tool_annotation" for c in comp1)

    # 2. 配置了 tool
    payload_with_tool = {"title": "With Tool", "civitai_images": [str(img)], "civitai_tool": "ComfyUI"}
    ok2, msg2, comp2 = publish_via_api(payload=payload_with_tool, api_key="key")
    assert ok2 is True
    assert "Civitai（API）：未携带工具标注" in msg2
    assert any(c.get("category") == "tool_annotation" for c in comp2)

# =====================================================================
# 6. Key 卫生扫描断言 (哨兵 Key 全文扫描断言不出现)
# =====================================================================
def test_sentinel_key_never_leaks(tmp_path, monkeypatch, caplog):
    """
    铁律验证: 设置带有明显特征的哨兵 Key，触发全流程异常与日志，
    扫描所有输出、日志记录、错误消息、结果字典，断言哨兵 Key 绝对不出现！
    """
    sentinel_key = "SENTINEL_SECRET_KEY_987654321_DO_NOT_LEAK"
    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    def mock_post(url, headers, json, timeout):
        # 故意让异常信息包含 headers (模拟第三方库抛出包含 raw auth 的异常)
        raise requests.RequestException(f"Connection error to {url} with headers {headers}")

    session_mock = requests.Session()
    monkeypatch.setattr(session_mock, "post", mock_post)
    monkeypatch.setattr(requests, "Session", lambda: session_mock)

    with caplog.at_level(logging.DEBUG):
        payload = {"title": "Sentinel Leak Test", "images": [str(img)]}
        ok, msg, comp = publish_via_api(payload=payload, api_key=sentinel_key)

    assert ok is False

    # 1. 扫描返回值 msg
    assert sentinel_key not in msg
    # 2. 扫描 compliance 结构体 JSON 序列化
    comp_json = json.dumps(comp)
    assert sentinel_key not in comp_json
    # 3. 扫描所有日志记录
    for record in caplog.records:
        assert sentinel_key not in record.message
        assert sentinel_key not in str(record.args)

    # 4. 掩码函数与脱敏函数测试
    masked = mask_civitai_api_key(sentinel_key)
    assert sentinel_key not in masked
    assert masked.endswith("LEAK")

    redacted = redact_api_key(f"error header: Bearer {sentinel_key}", sentinel_key)
    assert sentinel_key not in redacted
    assert "[REDACTED_API_KEY]" in redacted


# =====================================================================
# 7. CivitaiDispatcher 混合通道选路集成测试
# =====================================================================
def test_dispatcher_routes_to_api_channel(tmp_path, monkeypatch):
    """CivitaiDispatcher.publish 在 channel=api 时成功解析版本并路由至 publish_via_api"""
    disp = CivitaiDispatcher(cookies_dir=tmp_path / "cookies")

    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    monkeypatch.setattr("dispatchers.civitai_api.get_civitai_api_key", lambda data_dir=None: "valid_mock_api_key")

    def mock_get(url, *args, **kwargs):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json.dumps({
            "id": 257749,
            "name": "Pony Diffusion V6 XL",
            "modelVersions": [{"id": 290640, "name": "V6 (start with this one)"}]
        }).encode("utf-8")
        return resp

    monkeypatch.setattr(requests, "get", mock_get)

    called_api = False
    passed_version_id = None

    def fake_publish_via_api(payload, api_key, model_version_id=None, channel_notes=None):
        nonlocal called_api, passed_version_id
        called_api = True
        passed_version_id = model_version_id
        return True, "https://civitai.com/posts/778899 [API 官方通道]", [{"category": "channel", "channel": "api"}]

    monkeypatch.setattr("dispatchers.civitai_api.publish_via_api", fake_publish_via_api)

    payload = {
        "title": "API Dispatch Test",
        "civitai_channel": "api",
        "civitai_images": [str(img)],
        "civitai_resources": [257749]
    }

    success, msg, compliance = disp.publish(payload)
    assert success is True
    assert called_api is True
    assert passed_version_id == 290640
    assert "https://civitai.com/posts/778899" in msg


def test_dispatcher_routes_to_browser_channel_fallback(tmp_path, monkeypatch):
    """CivitaiDispatcher.publish 在 auto 且多资源时回退浏览器通道并附带说明"""
    disp = CivitaiDispatcher(cookies_dir=tmp_path / "cookies")

    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    monkeypatch.setattr("dispatchers.civitai_api.get_civitai_api_key", lambda data_dir=None: "valid_mock_api_key")

    called_browser = False

    def fake_publish_browser(payload):
        nonlocal called_browser
        called_browser = True
        return True, "https://civitai.com/posts/112233", [{"category": "post", "url": "https://civitai.com/posts/112233"}]

    monkeypatch.setattr(disp, "_publish_browser", fake_publish_browser)

    payload = {
        "title": "Browser Fallback Test",
        "civitai_channel": "auto",
        "civitai_images": [str(img)],
        "civitai_resources": [257749, 123456]  # 2 个资源触发回退
    }

    success, msg, compliance = disp.publish(payload)
    assert success is True
    assert called_browser is True
    assert "已回退浏览器通道（资源 2 个）" in msg


def test_dispatcher_api_mode_without_key_fails(tmp_path, monkeypatch):
    """CivitaiDispatcher.publish 在 channel=api 且无 key 时报错中止，严禁回退"""
    disp = CivitaiDispatcher(cookies_dir=tmp_path / "cookies")
    monkeypatch.setattr("dispatchers.civitai_api.get_civitai_api_key", lambda data_dir=None: None)

    payload = {
        "title": "API No Key Test",
        "civitai_channel": "api",
        "images": ["dummy.png"]
    }

    success, msg, compliance = disp.publish(payload)
    assert success is False
    assert "未配置 Civitai API Key" in msg


# Helper
def json_dump_bytes(data: dict) -> bytes:
    return json.dumps(data).encode("utf-8")
