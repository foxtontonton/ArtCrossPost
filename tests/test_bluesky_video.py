# -*- coding: utf-8 -*-
"""
ArtCrossPost - Bluesky 视频管线与 GIF 视频化单元测试 (test_bluesky_video.py)
测试范围:
1. GIF 转 MP4 视频转码规范 (H.264, yuv420p, 偶数宽高, +faststart, -an)
2. Bluesky 视频门禁校验 (<=300MB, <=600s)
3. 图片通道对 .gif 上传的明确拒绝 (防卫静默首帧降级)
4. P0-4 回读真成功断言对 embed 类型的校验 (区分 video 与 images embed)
5. getServiceAuth 与 poll_job_status 状态轮询序列 (完成/失败/超时)
6. 视频上传微服务 upload_video 流程
7. 单帖单 embed 规范: 视频帖舍弃同帖静态图并在结果中反映
8. 视频帖自标签 (self-labels) 与 AI 话题标签完整保留
9. 动图自动视频化发布测试 (Payload 传入 GIF，内部转 MP4 发视频)
10. 打码 GIF 合成失败时严格中止发布，杜绝无码原图降级
11. 单 GIF 配对视频复用 X 渲染产物
12. PublishThread 门禁阻断 raw .gif 走 Bluesky 图片通道
"""
import copy
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()

import numpy as np
import pytest
import requests
from PIL import Image
from PyQt5.QtWidgets import QApplication

from config import DEFAULT_CONFIG
from dispatchers.bluesky import (
    BSKY_HOST,
    BSKY_VIDEO_HOST,
    BSKY_VIDEO_SERVICE_DID,
    MAX_VIDEO_DURATION_SEC,
    MAX_VIDEO_SIZE_MB,
    XRPC_BASE,
    BlueskyDispatcher,
)
from video_engine import (
    convert_gif_to_mp4,
    probe_video_info,
    validate_bluesky_video_constraints,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def mock_main_window(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    cfg_copy = copy.deepcopy(DEFAULT_CONFIG)
    cfg_copy["platform_kofi_enabled"] = True
    monkeypatch.setattr("app.load_config", lambda: copy.deepcopy(cfg_copy))
    monkeypatch.setattr("app.save_config", lambda cfg: None)

    with patch("app.CensorEngine") as mock_ce, patch("app.WatermarkEngine") as mock_we:
        ce_instance = mock_ce.return_value
        dummy_bgr = np.zeros((100, 100, 3), dtype=np.uint8)
        ce_instance.apply_censor.return_value = (dummy_bgr, [[10, 10, 30, 30]])

        we_instance = mock_we.return_value
        def fake_apply_wm(img_or_bgr, **kwargs):
            out_p = kwargs.get("output_path")
            if out_p:
                Path(out_p).parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (100, 100), color=(10, 20, 30)).save(out_p)
            return dummy_bgr
        we_instance.apply_watermark.side_effect = fake_apply_wm

        from app import MainWindow
        win = MainWindow()
        win.censor_engine = ce_instance
        win.watermark_engine = we_instance
        yield win


@pytest.fixture
def temp_cookies_dir(tmp_path):
    cd = tmp_path / "cookies"
    cd.mkdir()
    return cd


@pytest.fixture
def dummy_gif(tmp_path):
    """生成具有奇数分辨率的动态 GIF (如 75x85)，用于验证偶数缩放补全"""
    gif_path = tmp_path / "test_odd.gif"
    im1 = Image.new("RGB", (75, 85), color=(255, 0, 0))
    im2 = Image.new("RGB", (75, 85), color=(0, 255, 0))
    im1.save(gif_path, "GIF", save_all=True, append_images=[im2], duration=100, loop=0)
    return gif_path


@pytest.fixture
def dummy_mp4(tmp_path):
    """生成用于测试的有效 MP4 文件"""
    gif_p = tmp_path / "source.gif"
    im1 = Image.new("RGB", (64, 64), color=(100, 150, 200))
    im2 = Image.new("RGB", (64, 64), color=(200, 150, 100))
    im1.save(gif_p, "GIF", save_all=True, append_images=[im2], duration=100, loop=0)
    mp4_p = tmp_path / "test_valid.mp4"
    return convert_gif_to_mp4(gif_p, mp4_p)


# ==============================================================================
# 1. GIF 转 MP4 视频转码规范测试
# ==============================================================================

def test_convert_gif_to_mp4_spec(dummy_gif, tmp_path):
    """
    P0-2: GIF 转码 MP4:
    - 偶数宽高对齐 (75x85 -> 74x84)
    - 编码: libx264
    - 像素格式: yuv420p
    - 时长 > 0
    """
    out_mp4 = tmp_path / "converted.mp4"
    res_path = convert_gif_to_mp4(dummy_gif, out_mp4)

    assert res_path.exists()
    assert res_path.stat().st_size > 0

    info = probe_video_info(res_path)
    assert info["width"] % 2 == 0, f"宽度必须对齐为偶数: {info['width']}"
    assert info["height"] % 2 == 0, f"高度必须对齐为偶数: {info['height']}"
    assert info["width"] == 74
    assert info["height"] == 84
    assert info.get("pix_fmt") == "yuv420p"
    assert info.get("codec") == "h264"
    assert info.get("duration_sec", 0) > 0


def test_convert_gif_to_mp4_missing_input(tmp_path):
    missing_gif = tmp_path / "not_found.gif"
    out_mp4 = tmp_path / "out.mp4"
    with pytest.raises(FileNotFoundError):
        convert_gif_to_mp4(missing_gif, out_mp4)


# ==============================================================================
# 2. Bluesky 视频门禁校验 (validate_bluesky_video_constraints)
# ==============================================================================

def test_validate_bluesky_video_constraints(dummy_mp4, tmp_path):
    # 正常视频校验通过
    ok, msg = validate_bluesky_video_constraints(dummy_mp4)
    assert ok is True
    assert "Bluesky 视频校验通过" in msg

    # 文件不存在
    ok, msg = validate_bluesky_video_constraints(tmp_path / "ghost.mp4")
    assert ok is False
    assert "不存在" in msg

    # 空文件
    empty_p = tmp_path / "empty.mp4"
    empty_p.write_bytes(b"")
    ok, msg = validate_bluesky_video_constraints(empty_p)
    assert ok is False
    assert "大小为 0" in msg

    # 体积超限拦截 (>300MB)
    with patch("pathlib.Path.stat") as mock_stat:
        mock_stat_res = MagicMock()
        mock_stat_res.st_size = int(305 * 1024 * 1024)
        mock_stat_res.st_mode = 0o100644
        mock_stat.return_value = mock_stat_res
        ok, msg = validate_bluesky_video_constraints(dummy_mp4, max_size_mb=300.0)
        assert ok is False
        assert "限制视频体积 ≤ 300MB" in msg

    # 时长超限拦截 (>600s / 10分钟)
    with patch("video_engine.probe_video_duration", return_value=615.0):
        ok, msg = validate_bluesky_video_constraints(dummy_mp4, max_duration_sec=600.0)
        assert ok is False
        assert "限制视频时长 ≤ 600s" in msg


# ==============================================================================
# 3. 废除静默首帧降级: _upload_image_blob 严格拒绝 .gif
# ==============================================================================

def test_upload_image_blob_rejects_gif(dummy_gif, temp_cookies_dir):
    """
    P0-3: 严禁用图片通道上传 GIF 造成静默压缩，必须明确返回错误拦截
    """
    disp = BlueskyDispatcher(cookies_dir=temp_cookies_dir)
    blob, err = disp._upload_image_blob(str(dummy_gif), access_jwt="test-jwt")
    assert blob is None
    assert "GIF 动图不支持通过图片通道上传" in err


# ==============================================================================
# 4. P0-4: 真成功断言 expected_embed_type 校验
# ==============================================================================

def test_assert_post_published_embed_type_verification(temp_cookies_dir):
    """
    P0-4: 断言升级:
    - 预期 app.bsky.embed.video 时，实际为 images embed 必须失败
    - 预期 app.bsky.embed.video 且包含有效 video ref 时成功
    """
    disp = BlueskyDispatcher(cookies_dir=temp_cookies_dir)

    # 1. 预期 video 但回读到 images embed
    mock_resp_images = MagicMock()
    mock_resp_images.status_code = 200
    mock_resp_images.json.return_value = {
        "thread": {
            "post": {
                "record": {
                    "text": "Hello video",
                    "embed": {
                        "$type": "app.bsky.embed.images",
                        "images": [{"image": {"$type": "blob"}}]
                    }
                }
            }
        }
    }

    with patch("requests.get", return_value=mock_resp_images):
        ok, err = disp._assert_post_published(
            uri="at://did:plc:123/app.bsky.feed.post/456",
            expected_text="Hello video",
            expected_image_count=0,
            expected_label=None,
            access_jwt="jwt",
            max_retries=1,
            expected_embed_type="app.bsky.embed.video"
        )
        assert ok is False
        assert "回读 embed 类型不一致" in err
        assert "app.bsky.embed.video" in err

    # 2. 预期 video 且回读到 video embed
    mock_resp_video = MagicMock()
    mock_resp_video.status_code = 200
    mock_resp_video.json.return_value = {
        "thread": {
            "post": {
                "record": {
                    "text": "Hello video",
                    "embed": {
                        "$type": "app.bsky.embed.video",
                        "video": {"$type": "blob", "ref": {"$link": "bafk..."}}
                    }
                }
            }
        }
    }

    with patch("requests.get", return_value=mock_resp_video):
        ok, err = disp._assert_post_published(
            uri="at://did:plc:123/app.bsky.feed.post/456",
            expected_text="Hello video",
            expected_image_count=0,
            expected_label=None,
            access_jwt="jwt",
            max_retries=1,
            expected_embed_type="app.bsky.embed.video"
        )
        assert ok is True
        assert err == ""


# ==============================================================================
# 5. getServiceAuth 与 poll_job_status 轮询机制
# ==============================================================================

def test_get_service_auth(temp_cookies_dir):
    disp = BlueskyDispatcher(cookies_dir=temp_cookies_dir)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"token": "service-token-xyz"}
    with patch("requests.get", return_value=mock_resp):
        token, err = disp.get_service_auth("my-jwt")
        assert token == "service-token-xyz"
        assert err == ""

    mock_fail = MagicMock()
    mock_fail.status_code = 401
    mock_fail.text = "Unauthorized"
    with patch("requests.get", return_value=mock_fail):
        token, err = disp.get_service_auth("my-jwt")
        assert token is None
        assert "getServiceAuth 失败" in err


def test_poll_job_status_sequences(temp_cookies_dir):
    disp = BlueskyDispatcher(cookies_dir=temp_cookies_dir)

    # 序列 1: 处理中 -> 完成并返回 blob
    resp_pending = MagicMock()
    resp_pending.status_code = 200
    resp_pending.json.return_value = {"jobStatus": {"state": "JOB_STATE_PROCESSING", "progress": 50}}

    resp_completed = MagicMock()
    resp_completed.status_code = 200
    expected_blob = {"$type": "blob", "ref": {"$link": "blob-123"}}
    resp_completed.json.return_value = {"jobStatus": {"state": "JOB_STATE_COMPLETED", "blob": expected_blob}}

    with patch("requests.get", side_effect=[resp_pending, resp_completed]):
        blob, err = disp.poll_job_status("job-1", service_token="tok", poll_interval=0.01, timeout_seconds=5)
        assert blob == expected_blob
        assert err == ""

    # 序列 2: 失败报错
    resp_failed = MagicMock()
    resp_failed.status_code = 200
    resp_failed.json.return_value = {"jobStatus": {"state": "JOB_STATE_FAILED", "error": "InvalidVideoCodec"}}

    with patch("requests.get", return_value=resp_failed):
        blob, err = disp.poll_job_status("job-2", service_token="tok", poll_interval=0.01, timeout_seconds=5)
        assert blob is None
        assert "视频转码失败: InvalidVideoCodec" in err

    # 序列 3: 超时
    with patch("requests.get", return_value=resp_pending):
        blob, err = disp.poll_job_status("job-3", service_token="tok", poll_interval=0.01, timeout_seconds=0.05)
        assert blob is None
        assert "超时" in err


# ==============================================================================
# 6. upload_video 流程测试
# ==============================================================================

def test_upload_video_pipeline(dummy_mp4, temp_cookies_dir):
    disp = BlueskyDispatcher(cookies_dir=temp_cookies_dir)

    with patch.object(disp, "get_service_auth", return_value=("svc-tok", "")), \
         patch("requests.post") as mock_post, \
         patch.object(disp, "poll_job_status") as mock_poll:

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {"jobStatus": {"jobId": "job-abc", "state": "JOB_STATE_CREATED"}}
        mock_post.return_value = mock_post_resp

        mock_poll.return_value = ({"$type": "blob", "ref": "mocked-blob"}, "")

        blob, err = disp.upload_video(str(dummy_mp4), access_jwt="jwt", did="did:plc:test")
        assert blob == {"$type": "blob", "ref": "mocked-blob"}
        assert err == ""
        assert mock_post.called
        assert mock_poll.called


def test_resolve_pds_did_from_credentials_and_network(temp_cookies_dir):
    """测试从凭据缓存与 PLC 目录 DID 文档中准确解析 PDS DID (did:web:<host>)"""
    disp = BlueskyDispatcher(cookies_dir=temp_cookies_dir)

    # 1. 从凭据缓存的 didDoc 中解析
    cached_doc = {
        "id": "did:plc:testuser123",
        "service": [
            {
                "id": "#atproto_pds",
                "type": "AtprotoPersonalDataServer",
                "serviceEndpoint": "https://amanita.us-east.host.bsky.network"
            }
        ]
    }
    disp._save_credentials({"did": "did:plc:testuser123", "didDoc": cached_doc})
    pds_did = disp.resolve_pds_did("did:plc:testuser123")
    assert pds_did == "did:web:amanita.us-east.host.bsky.network"

    # 2. 缓存无记录时从 plc.directory 获取
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": "did:plc:networkuser456",
        "service": [
            {
                "id": "#atproto_pds",
                "type": "AtprotoPersonalDataServer",
                "serviceEndpoint": "https://my-custom-pds.example.org/xrpc"
            }
        ]
    }
    with patch("requests.get", return_value=mock_resp):
        pds_did_net = disp.resolve_pds_did("did:plc:networkuser456")
        assert pds_did_net == "did:web:my-custom-pds.example.org"


def test_upload_video_401_audience_error_self_healing_retry(dummy_mp4, temp_cookies_dir):
    """测试视频上传遇到 401 受众不符错误时，自动从响应提取 PDS DID 并重新 mint token 成功重试一次"""
    disp = BlueskyDispatcher(cookies_dir=temp_cookies_dir)

    # Mock get_service_auth 记录调用历史
    auth_calls = []
    def fake_get_service_auth(jwt, aud=None, lxm=None, exp_seconds=1800, did=None):
        auth_calls.append(aud)
        return f"token-for-{aud}", ""

    # Mock POST uploadVideo: 第一次返回 401 受众错误（实机错误原文整串带双引号），第二次重试返回 200 成功
    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.text = 'invalid token: invalid token audience did:web:video.bsky.app, should be the user\'s PDS DID "did:web:amanita.us-east.host.bsky.network"'

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"jobStatus": {"jobId": "job-recovered", "state": "JOB_STATE_CREATED"}}

    with patch.object(disp, "get_service_auth", side_effect=fake_get_service_auth), \
         patch("requests.post", side_effect=[resp_401, resp_200]) as mock_post, \
         patch.object(disp, "poll_job_status") as mock_poll:

        mock_poll.return_value = ({"$type": "blob", "ref": "recovered-blob"}, "")

        blob, err = disp.upload_video(str(dummy_mp4), access_jwt="my-jwt", did="did:plc:testuser")
        assert blob == {"$type": "blob", "ref": "recovered-blob"}
        assert err == ""

        # 断言发生了自愈重试，重新请求了正确的 PDS DID
        assert len(auth_calls) == 2
        assert auth_calls[1] == "did:web:amanita.us-east.host.bsky.network"
        assert mock_post.call_count == 2


def test_did_extraction_regex_quotes_variants():
    """验证 401 自愈正则对带双引号、单引号、转义引号及无引号等实机原文均可准确提取 DID"""
    import re
    regex1 = r"should be (?:the user's PDS DID )?[\"'\\]*[\"']?(did:[a-zA-Z0-9_.:-]+)"
    regex2 = r"(?:expected|pds did)[:\s]+[\"'\\]*[\"']?(did:[a-zA-Z0-9_.:-]+)"

    # 实机原文: should be the user's PDS DID "did:web:xxx"
    msg_real = 'invalid token: invalid token audience did:web:video.bsky.app, should be the user\'s PDS DID "did:web:amanita.us-east.host.bsky.network"'
    m = re.search(regex1, msg_real, re.I)
    assert m and m.group(1) == "did:web:amanita.us-east.host.bsky.network"

    # JSON 序列化转义原文
    msg_json = '{"error":"Unauthorized","message":"invalid token: invalid token audience did:web:video.bsky.app, should be the user\'s PDS DID \\"did:web:amanita.us-east.host.bsky.network\\""}'
    m_json = re.search(regex1, msg_json, re.I)
    assert m_json and m_json.group(1) == "did:web:amanita.us-east.host.bsky.network"

    # 备用正则实机格式
    msg_alt = 'Bad audience: expected pds did: "did:web:pds.example.org"'
    m_alt = re.search(regex2, msg_alt, re.I)
    assert m_alt and m_alt.group(1) == "did:web:pds.example.org"


# ==============================================================================
# 7. 单帖单 embed 规范: 视频帖舍弃同帖静态图
# ==============================================================================

def test_publish_video_discards_static_images(dummy_mp4, tmp_path, temp_cookies_dir):
    """
    P0-2: 当 payload 中含有视频且含有 3 张静态图片时，
    根据单帖单 embed 规范，仅发布 1 个视频 embed，其余 3 张静态素材舍弃，
    并在返回信息中记录舍弃数量。
    """
    disp = BlueskyDispatcher(cookies_dir=temp_cookies_dir)

    static_images = []
    for i in range(3):
        p = tmp_path / f"extra_{i}.png"
        Image.new("RGB", (30, 30), color=(i * 10, i * 20, i * 30)).save(p, "PNG")
        static_images.append(str(p))

    payload = {
        "title": "Video Showcase",
        "description": "Short animation",
        "bluesky_video": str(dummy_mp4),
        "images": static_images,
        "bluesky_discarded_images_count": len(static_images)
    }

    created_records = []
    def mock_create_record(url, json=None, headers=None, timeout=None):
        created_records.append(json)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "uri": "at://did:plc:foxtest/app.bsky.feed.post/video001",
            "cid": "cid001"
        }
        return resp

    mock_blob = {"$type": "blob", "ref": {"$link": "blob-video-xyz"}, "mimeType": "video/mp4"}
    mock_session = (True, "", {"accessJwt": "mock_jwt", "did": "did:plc:foxtest", "handle": "foxtest.bsky.social"})

    with patch.object(disp, "_get_active_session", return_value=mock_session), \
         patch.object(disp, "upload_video", return_value=(mock_blob, "")), \
         patch("requests.post", side_effect=mock_create_record), \
         patch.object(disp, "_assert_post_published", return_value=(True, "")):

        ok, msg = disp.publish(payload)
        assert ok is True, f"发布失败: {msg}"
        assert "发布成功" in msg
        assert "已舍弃 3 张同帖素材" in msg
        assert len(created_records) == 1

        rec = created_records[0]["record"]
        assert rec["embed"]["$type"] == "app.bsky.embed.video"
        assert rec["embed"]["video"] == mock_blob
        assert "aspectRatio" in rec["embed"]


# ==============================================================================
# 8. 视频帖保留 self-labels 与 AI 话题标签
# ==============================================================================

def test_video_post_preserves_self_labels_and_ai_tags(dummy_mp4, temp_cookies_dir):
    """
    P0-5: 视频帖中 self-labels (porn) 与 AI 话题标签 (#AIArt #GenerativeArt) 必须完好保留
    """
    disp = BlueskyDispatcher(cookies_dir=temp_cookies_dir)

    payload = {
        "title": "NSFW AI Animation",
        "description": "Sensual clip",
        "bluesky_video": str(dummy_mp4),
        "is_nsfw": True,
        "platform_options": {
            "bluesky_label": "porn",
            "bluesky_ai_hashtags": True,
            "is_ai_generated": True
        }
    }

    created_records = []
    def mock_create_record(url, json=None, headers=None, timeout=None):
        created_records.append(json)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "uri": "at://did:plc:foxtest/app.bsky.feed.post/video002",
            "cid": "cid002"
        }
        return resp

    mock_blob = {"$type": "blob", "ref": {"$link": "blob-video-nsfw"}, "mimeType": "video/mp4"}
    mock_session = (True, "", {"accessJwt": "mock_jwt", "did": "did:plc:foxtest", "handle": "foxtest.bsky.social"})

    with patch.object(disp, "_get_active_session", return_value=mock_session), \
         patch.object(disp, "upload_video", return_value=(mock_blob, "")), \
         patch("requests.post", side_effect=mock_create_record), \
         patch.object(disp, "_assert_post_published", return_value=(True, "")):

        ok, msg = disp.publish(payload)
        assert ok is True, f"发布失败: {msg}"

        rec = created_records[0]["record"]
        assert "labels" in rec
        assert rec["labels"]["values"] == [{"val": "porn"}]

        text = rec["text"]
        assert "#AIArt" in text
        assert "#GenerativeArt" in text
        assert "facets" in rec
        tag_facets = [f for f in rec["facets"] if any(feat.get("$type") == "app.bsky.richtext.facet#tag" for feat in f.get("features", []))]
        assert len(tag_facets) >= 2


# ==============================================================================
# 9. 动图自动视频化发布测试 (Payload 传入 GIF，内部转 MP4 发视频)
# ==============================================================================

def test_publish_payload_with_gif_auto_converts_to_video(dummy_gif, temp_cookies_dir):
    """
    当 payload 仅含有 GIF 且未显式指定 bluesky_video 时，
    publish() 自动转码 GIF 为 MP4 走视频管线发布
    """
    disp = BlueskyDispatcher(cookies_dir=temp_cookies_dir)

    payload = {
        "title": "Animated GIF post",
        "description": "Now uploaded as MP4",
        "images": [str(dummy_gif)]
    }

    uploaded_videos = []
    def mock_upload_video(video_path, access_jwt, did, **kwargs):
        uploaded_videos.append(video_path)
        assert video_path.endswith(".mp4")
        assert Path(video_path).exists()
        return {"$type": "blob", "ref": {"$link": "blob-converted-mp4"}}, ""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "uri": "at://did:plc:foxtest/app.bsky.feed.post/gif2video01",
        "cid": "cid_gif"
    }

    mock_session = (True, "", {"accessJwt": "mock_jwt", "did": "did:plc:foxtest", "handle": "foxtest.bsky.social"})

    with patch.object(disp, "_get_active_session", return_value=mock_session), \
         patch.object(disp, "upload_video", side_effect=mock_upload_video), \
         patch("requests.post", return_value=mock_resp), \
         patch.object(disp, "_assert_post_published", return_value=(True, "")):

        ok, msg = disp.publish(payload)
        assert ok is True, f"发布失败: {msg}"
        assert len(uploaded_videos) == 1
        assert "发布成功" in msg
        assert "(视频)" in msg


# ==============================================================================
# 10. 打码 GIF 合成失败时严格中止，严禁回退发原图
# ==============================================================================

def test_app_publish_bsky_censor_gif_failure_aborts(mock_main_window, tmp_path, monkeypatch):
    """
    P0-3: 当开启打码保护时，Bluesky 的打码 GIF 若渲染失败，
    必须直接中止并弹窗/通知报错，严禁回退发原图！
    """
    win = mock_main_window
    art_title = "BskyFailAbortTest"
    win.title_input.setText(art_title)

    gif_path = tmp_path / f"{art_title}.gif"
    im1 = Image.new("RGB", (50, 50), color=(100, 100, 100))
    im2 = Image.new("RGB", (50, 50), color=(200, 200, 200))
    im1.save(gif_path, "GIF", save_all=True, append_images=[im2], duration=100, loop=0)
    win.image_paths = [str(gif_path)]

    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(name == "bluesky")
        if "censor" in ctrl["options"]:
            ctrl["options"]["censor"].setChecked(name == "bluesky")

    win.auto_censor_cb.setChecked(True)
    win.watermark_enabled_cb.setChecked(False)

    orig_composite = win._generate_composite_gif
    def mock_fail_composite(input_p, output_p, **kwargs):
        if "bsky" in str(output_p):
            raise RuntimeError("GPU Out of memory during GIF rendering")
        return orig_composite(input_p, output_p, **kwargs)

    monkeypatch.setattr(win, "_generate_composite_gif", mock_fail_composite)

    notifications = []
    def mock_notify(msg, level="info"):
        notifications.append((msg, level))

    monkeypatch.setattr(win, "notify", mock_notify)

    publish_started = []
    class DummyPublishThread:
        def __init__(self, selected, payload):
            publish_started.append(payload)
        def start(self):
            pass
        def isRunning(self):
            return False

    monkeypatch.setattr("app.PublishThread", DummyPublishThread)

    win._start_publishing()

    assert len(publish_started) == 0, "GIF 打码合成失败时必须立即中止发布，严禁继续提交"
    assert any("Bluesky 打码 GIF 合成失败" in msg for msg, lvl in notifications)
    assert any(lvl == "error" for msg, lvl in notifications)


# ==============================================================================
# 11. Bluesky 单 GIF 配对视频复用 X 渲染结果
# ==============================================================================

def test_app_publish_bsky_reuses_x_paired_video(mock_main_window, tmp_path, monkeypatch):
    """
    P0-1: 当同时发布 X 与 Bluesky 时，单 GIF 帖的配对视频如果打码参数一致，
    Bluesky 优先复用 X 的视频渲染产物，无需重复运行 VideoEngine。
    """
    win = mock_main_window
    art_title = "BskyReuseXVideoTest"
    win.title_input.setText(art_title)

    gif_path = tmp_path / f"{art_title}.gif"
    im1 = Image.new("RGB", (60, 60), color=(10, 20, 30))
    im2 = Image.new("RGB", (60, 60), color=(40, 50, 60))
    im1.save(gif_path, "GIF", save_all=True, append_images=[im2], duration=100, loop=0)
    win.image_paths = [str(gif_path)]

    paired_mp4 = tmp_path / f"{art_title}.mp4"
    convert_gif_to_mp4(gif_path, paired_mp4)
    win.x_video_paths = {str(gif_path): str(paired_mp4)}

    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(name in ("x_twitter", "bluesky"))
        if "censor" in ctrl["options"]:
            ctrl["options"]["censor"].setChecked(True)

    win.auto_censor_cb.setChecked(True)
    win.watermark_enabled_cb.setChecked(False)

    captured_payloads = []
    class DummyPublishThread:
        def __init__(self, selected, payload):
            captured_payloads.append(payload)
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()
        def start(self):
            pass
        def isRunning(self):
            return False

    monkeypatch.setattr("app.PublishThread", DummyPublishThread)

    rendered_mp4 = tmp_path / "x_rendered.mp4"
    paired_mp4.replace(rendered_mp4)
    win.x_video_paths[str(gif_path)] = str(rendered_mp4)

    class MockSignal:
        def __init__(self):
            self._callbacks = []
        def connect(self, cb):
            self._callbacks.append(cb)
        def emit(self, *args):
            for cb in self._callbacks:
                cb(*args)

    class DummyVideoProcessThread:
        def __init__(self, engine, in_p, **kwargs):
            self.progress_signal = MockSignal()
            self.finished_signal = MockSignal()
        def start(self):
            self.finished_signal.emit(True, str(rendered_mp4), True)
        def isRunning(self):
            return False

    monkeypatch.setattr("app.VideoProcessThread", DummyVideoProcessThread)

    # 拦截 QEventLoop 以防卡住
    from PyQt5.QtCore import QEventLoop
    monkeypatch.setattr(QEventLoop, "exec_", lambda self: 0)

    monkeypatch.setattr("app.validate_x_video_constraints", lambda *args, **kwargs: (True, "OK"))
    monkeypatch.setattr("app.validate_bluesky_video_constraints", lambda *args, **kwargs: (True, "OK"))

    win._start_publishing()

    assert len(captured_payloads) == 1
    pld = captured_payloads[0]
    assert pld["x_video"] is not None
    assert pld["bluesky_video"] == pld["x_video"]


# ==============================================================================
# 12. PublishThread 门禁拒绝未转码的 raw .gif 进入 Bluesky
# ==============================================================================

def test_publish_thread_gate_rejects_raw_gif_for_bluesky(dummy_gif, temp_cookies_dir):
    """
    PublishThread 素材自检门禁:
    若任何情况下 bluesky_images 包含未转码的 raw .gif，门禁必须直接拦截
    """
    from app import PublishThread

    disp = BlueskyDispatcher(cookies_dir=temp_cookies_dir)
    disp.probe_login = MagicMock(return_value={"status": "ok", "detail": "有效"})

    payload = {
        "title": "Raw GIF leak",
        "description": "Should be intercepted",
        "bluesky_images": [str(dummy_gif)],
        "images": [str(dummy_gif)],
    }

    thread = PublishThread([disp], payload)
    completed_results = []
    thread.completed_signal.connect(lambda res: completed_results.append(res))

    thread.run()

    assert len(completed_results) == 1
    res_dict = completed_results[0]
    assert "bluesky" in res_dict
    ok, err, _ = res_dict["bluesky"]
    assert ok is False
    assert "发布前门禁拦截" in err
    assert "不支持 GIF 动图通过图片通道上传" in err
