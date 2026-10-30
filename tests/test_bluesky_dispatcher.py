# -*- coding: utf-8 -*-
"""
ArtCrossPost - Bluesky Dispatcher 单元测试
覆盖 AT Protocol XRPC API 路线、字素簇计数、Facets UTF-8 字节偏移、P0-1 真成功回读断言、
成人分级 self-labels、AI 话题标签、多图上限、串推 reply 链、删除测试帖及自检探针。
"""
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from PIL import Image

from dispatchers.bluesky import (
    BlueskyDispatcher,
    MAX_BLOB_SIZE,
    MAX_GRAPHEMES,
    count_graphemes,
    extract_facets,
    split_into_thread,
    truncate_text,
)


@pytest.fixture
def temp_cookies_dir(tmp_path):
    cd = tmp_path / "cookies"
    cd.mkdir()
    return cd


@pytest.fixture
def sample_image(tmp_path):
    img_path = tmp_path / "test_artwork.png"
    im = Image.new("RGB", (200, 200), color=(120, 160, 220))
    im.save(img_path, format="PNG")
    return img_path


@pytest.fixture
def large_image(tmp_path):
    import os
    img_path = tmp_path / "test_large.png"
    # 随机噪点图片，无法被 PNG 压缩，生成 >950KB 文件
    im = Image.frombytes("RGB", (700, 700), os.urandom(700 * 700 * 3))
    im.save(img_path, format="PNG")
    assert img_path.stat().st_size > MAX_BLOB_SIZE
    return img_path


# ==============================================================================
# 1. 字素簇计数 (UAX #29 Grapheme Cluster) 测试
# ==============================================================================

def test_count_graphemes_ascii_and_multilingual():
    assert count_graphemes("Hello World") == 11
    assert count_graphemes("你好，世界！") == 6
    assert count_graphemes("こんにちは") == 5


def test_count_graphemes_combined_emojis():
    # 单独字符与复合 Emoji (ZWJ 序列、修饰符序列)
    # 👩‍💻 = 4 code points, 但属于 1 个字素簇
    zwj_emoji = "\U0001F469\u200D\U0001F4BB"
    assert count_graphemes(zwj_emoji) == 1

    # 👍🏽 = 👍 + 🏽 (肤色修饰符), 1 个字素簇
    skin_tone_emoji = "\U0001F44D\U0001F3FD"
    assert count_graphemes(skin_tone_emoji) == 1

    # 复合文本
    text = f"Art by Fox {zwj_emoji} {skin_tone_emoji}!"
    # "Art by Fox " (11) + zwj(1) + " " (1) + skin(1) + "!" (1) = 15
    assert count_graphemes(text) == 15


# ==============================================================================
# 2. ATProto Facets 提取与 UTF-8 字节偏移量测试
# ==============================================================================

def test_extract_facets_urls_with_utf8_byte_offsets():
    # 核心测试：中文字符占用 3 字节 UTF-8，byteStart / byteEnd 必须基于 UTF-8 字节而非字符数
    text = "作品展示：https://bsky.app/profile/test.bsky.social 请多多指教"
    facets = extract_facets(text)
    assert len(facets) == 1
    facet = facets[0]
    assert facet["features"][0]["$type"] == "app.bsky.richtext.facet#link"
    assert facet["features"][0]["uri"] == "https://bsky.app/profile/test.bsky.social"

    # 验证 byteStart 与 byteEnd
    byte_start = facet["index"]["byteStart"]
    byte_end = facet["index"]["byteEnd"]
    encoded = text.encode("utf-8")
    extracted_slice = encoded[byte_start:byte_end].decode("utf-8")
    assert extracted_slice == "https://bsky.app/profile/test.bsky.social"


def test_extract_facets_hashtags():
    text = "Original anime illustration #AIArt #GenerativeArt"
    facets = extract_facets(text)
    tags = [f["features"][0].get("tag") for f in facets if f["features"][0]["$type"] == "app.bsky.richtext.facet#tag"]
    assert "AIArt" in tags
    assert "GenerativeArt" in tags


# ==============================================================================
# 3. 文本自然截断与拆串推 (Thread) 测试
# ==============================================================================

def test_truncate_text_under_limit():
    short_text = "This is a short post."
    assert truncate_text(short_text, max_graphemes=300) == short_text


def test_truncate_text_over_limit():
    long_text = "段落内容。" * 80  # 400 字素
    truncated = truncate_text(long_text, max_graphemes=300)
    assert count_graphemes(truncated) <= 300
    assert truncated.endswith("...")


def test_split_into_thread():
    long_text = "第一段说明故事背景。\n\n第二段深入介绍角色设定。\n\n第三段赞助支持感谢。" * 15
    chunks = split_into_thread(long_text, max_graphemes=300)
    assert len(chunks) > 1
    for chunk in chunks:
        assert count_graphemes(chunk) <= 300


# ==============================================================================
# 4. 凭据存储、获取画像与会话测试
# ==============================================================================

def test_credentials_save_load_clear(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)
    assert not disp.has_saved_cookies()

    data = {"handle": "user.bsky.social", "app_password": "test-password-1234"}
    disp._save_credentials(data)
    assert disp.has_saved_cookies()

    profile = disp.get_profile()
    assert profile["logged_in"] is True
    assert profile["username"] == "user.bsky.social"

    disp.clear_session()
    assert not disp.has_saved_cookies()
    assert disp.get_profile()["logged_in"] is False


def test_check_login_with_active_session(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)
    disp._save_credentials({
        "handle": "foxtontonton.bsky.social",
        "app_password": "xxxx-xxxx-xxxx-xxxx",
        "accessJwt": "mock-access-jwt",
        "refreshJwt": "mock-refresh-jwt",
        "did": "did:plc:mock123"
    })

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        ok, msg = disp.check_login()
        assert ok is True
        assert "@foxtontonton.bsky.social" in msg


def test_check_login_refresh_expired_session(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)
    disp._save_credentials({
        "handle": "fox.bsky.social",
        "app_password": "xxxx-xxxx-xxxx-xxxx",
        "accessJwt": "expired-jwt",
        "refreshJwt": "valid-refresh-jwt",
        "did": "did:plc:mock123"
    })

    with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
        # getSession 失败 (401 过期)
        get_resp = MagicMock()
        get_resp.status_code = 401
        mock_get.return_value = get_resp

        # refreshSession 成功返回新 token
        post_resp = MagicMock()
        post_resp.status_code = 200
        post_resp.json.return_value = {
            "accessJwt": "new-access-jwt",
            "refreshJwt": "new-refresh-jwt",
            "did": "did:plc:mock123",
            "handle": "fox.bsky.social"
        }
        mock_post.return_value = post_resp

        ok, msg = disp.check_login()
        assert ok is True
        assert "@fox.bsky.social" in msg

        creds = disp._load_credentials()
        assert creds["accessJwt"] == "new-access-jwt"


# ==============================================================================
# 5. 图片 Blob 上传与 >950KB 门禁自动压缩测试
# ==============================================================================

def test_upload_image_blob_normal(temp_cookies_dir, sample_image):
    disp = BlueskyDispatcher(temp_cookies_dir)

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "blob": {
                "$type": "blob",
                "ref": {"$link": "bafkreibmockcid"},
                "mimeType": "image/png",
                "size": 12345
            }
        }
        mock_post.return_value = mock_resp

        blob, err = disp._upload_image_blob(str(sample_image), "mock-jwt")
        assert blob is not None
        assert err == ""
        assert blob["ref"]["$link"] == "bafkreibmockcid"


def test_upload_image_blob_auto_compress(temp_cookies_dir, large_image):
    disp = BlueskyDispatcher(temp_cookies_dir)

    uploaded_payloads = []
    with patch("requests.post") as mock_post:
        def _fake_post(url, data=None, headers=None, timeout=None):
            uploaded_payloads.append(data)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "blob": {
                    "$type": "blob",
                    "ref": {"$link": "compressed-blob"},
                    "mimeType": headers.get("Content-Type"),
                    "size": len(data)
                }
            }
            return mock_resp

        mock_post.side_effect = _fake_post

        blob, err = disp._upload_image_blob(str(large_image), "mock-jwt")
        assert blob is not None
        assert len(uploaded_payloads) == 1
        # 验证发送给 API 的字节数已经自动压缩降至 <= MAX_BLOB_SIZE (950KB)
        assert len(uploaded_payloads[0]) <= MAX_BLOB_SIZE


# ==============================================================================
# 6. P0-1 真成功回读断言测试
# ==============================================================================

def test_assert_post_published_pass(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)

    mock_thread_data = {
        "thread": {
            "$type": "app.bsky.feed.defs#threadViewPost",
            "post": {
                "uri": "at://did:plc:123/app.bsky.feed.post/3mock",
                "cid": "bafymock",
                "record": {
                    "text": "测试正文 #AIArt",
                    "embed": {
                        "$type": "app.bsky.embed.images",
                        "images": [{"alt": "", "image": {}}]
                    },
                    "labels": {
                        "$type": "com.atproto.label.defs#selfLabels",
                        "values": [{"val": "porn"}]
                    }
                }
            }
        }
    }

    with patch("requests.get") as mock_get:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = mock_thread_data
        mock_get.return_value = resp

        ok, err = disp._assert_post_published(
            uri="at://did:plc:123/app.bsky.feed.post/3mock",
            expected_text="测试正文 #AIArt",
            expected_image_count=1,
            expected_label="porn",
            access_jwt="jwt"
        )
        assert ok is True
        assert err == ""


def test_assert_post_published_text_mismatch(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)
    mock_thread_data = {
        "thread": {
            "post": {
                "record": {
                    "text": "回读到的不同正文",
                    "embed": {"$type": "app.bsky.embed.images", "images": []}
                }
            }
        }
    }

    with patch("requests.get") as mock_get:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = mock_thread_data
        mock_get.return_value = resp

        ok, err = disp._assert_post_published(
            uri="at://did:plc:123/app.bsky.feed.post/3mock",
            expected_text="原定的正文",
            expected_image_count=0,
            expected_label=None,
            access_jwt="jwt"
        )
        assert ok is False
        assert "回读正文不一致" in err


def test_assert_post_published_image_count_mismatch(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)
    mock_thread_data = {
        "thread": {
            "post": {
                "record": {
                    "text": "正文一致",
                    "embed": {
                        "$type": "app.bsky.embed.images",
                        "images": [{"alt": "", "image": {}}]  # 只有 1 张图
                    }
                }
            }
        }
    }

    with patch("requests.get") as mock_get:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = mock_thread_data
        mock_get.return_value = resp

        ok, err = disp._assert_post_published(
            uri="at://did:plc:123/app.bsky.feed.post/3mock",
            expected_text="正文一致",
            expected_image_count=2,  # 预期 2 张
            expected_label=None,
            access_jwt="jwt"
        )
        assert ok is False
        assert "回读图片数不一致" in err


def test_assert_post_published_missing_nsfw_label(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)
    mock_thread_data = {
        "thread": {
            "post": {
                "record": {
                    "text": "NSFW 帖子",
                    "labels": {"values": []}  # 缺少 porn 标签
                }
            }
        }
    }

    with patch("requests.get") as mock_get:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = mock_thread_data
        mock_get.return_value = resp

        ok, err = disp._assert_post_published(
            uri="at://did:plc:123/app.bsky.feed.post/3mock",
            expected_text="NSFW 帖子",
            expected_image_count=0,
            expected_label="porn",
            access_jwt="jwt"
        )
        assert ok is False
        assert "回读成人分级标签不一致" in err


# ==============================================================================
# 7. 全流程自动发布 publish() 测试 (含 P0-1 ~ P0-5 业务规则)
# ==============================================================================

def test_publish_full_success_with_nsfw_and_ai(temp_cookies_dir, sample_image):
    disp = BlueskyDispatcher(temp_cookies_dir)
    disp._save_credentials({
        "handle": "foxtontonton.bsky.social",
        "app_password": "test-password",
        "accessJwt": "mock-jwt",
        "refreshJwt": "mock-refresh",
        "did": "did:plc:test12345"
    })

    created_records = []

    def _mock_post(url, json=None, data=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if "uploadBlob" in url:
            resp.json.return_value = {"blob": {"$type": "blob", "ref": {"$link": "cid1"}}}
        elif "createRecord" in url:
            created_records.append(json["record"])
            resp.json.return_value = {
                "uri": "at://did:plc:test12345/app.bsky.feed.post/3mockrkey",
                "cid": "mockcid123"
            }
        return resp

    def _mock_get(url, params=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if "getSession" in url:
            return resp
        if "getPostThread" in url:
            # 回读刚才创建的记录
            last_record = created_records[-1] if created_records else {}
            resp.json.return_value = {
                "thread": {
                    "post": {
                        "uri": params.get("uri"),
                        "cid": "mockcid123",
                        "record": last_record
                    }
                }
            }
            return resp
        return resp

    payload = {
        "title": "测试作品标题",
        "description": "这是一段作品描述内容",
        "tags": ["anime", "illustration"],
        "bluesky_images": [str(sample_image)],
        "is_nsfw": True,
        "is_ai_generated": True,
        "platform_options": {
            "bluesky_censor": True,
            "bluesky_label": "porn",
            "bluesky_ai_hashtags": True
        }
    }

    with patch("requests.post", side_effect=_mock_post), patch("requests.get", side_effect=_mock_get):
        ok, msg = disp.publish(payload)
        assert ok is True
        assert "https://bsky.app/profile/foxtontonton.bsky.social/post/3mockrkey" in msg

        # 检查发出的 record 字段
        rec = created_records[0]
        # P0-4: AI 话题标签与 Facets
        assert "#AIArt #GenerativeArt" in rec["text"]
        facets = rec.get("facets", [])
        tag_names = [f["features"][0].get("tag") for f in facets if f["features"][0]["$type"] == "app.bsky.richtext.facet#tag"]
        assert "AIArt" in tag_names
        assert "GenerativeArt" in tag_names

        # P0-3: 成人分级 self-labels
        labels = rec.get("labels", {})
        assert labels.get("$type") == "com.atproto.label.defs#selfLabels"
        assert labels["values"][0]["val"] == "porn"

        # P0-5: 图片 embed 与尺寸宽高比
        embed = rec.get("embed", {})
        assert embed.get("$type") == "app.bsky.embed.images"
        assert len(embed["images"]) == 1
        assert embed["images"][0]["aspectRatio"]["width"] == 200
        assert embed["images"][0]["aspectRatio"]["height"] == 200


def test_publish_multi_images_cap_4(temp_cookies_dir, sample_image):
    disp = BlueskyDispatcher(temp_cookies_dir)
    disp._save_credentials({
        "handle": "fox.bsky.social",
        "app_password": "test-password",
        "accessJwt": "mock-jwt",
        "refreshJwt": "mock-refresh",
        "did": "did:plc:test123"
    })

    uploaded_blobs_count = [0]
    created_records = []

    def _mock_post(url, json=None, data=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if "uploadBlob" in url:
            uploaded_blobs_count[0] += 1
            resp.json.return_value = {"blob": {"$type": "blob", "ref": {"$link": f"cid{uploaded_blobs_count[0]}"}}}
        elif "createRecord" in url:
            created_records.append(json["record"])
            resp.json.return_value = {"uri": "at://did:plc:test123/app.bsky.feed.post/3mockrkey", "cid": "mockcid"}
        return resp

    def _mock_get(url, params=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if "getPostThread" in url:
            resp.json.return_value = {
                "thread": {"post": {"record": created_records[-1]}}
            }
        return resp

    # 提供 6 张图片，超出单帖上限 4 张
    payload = {
        "title": "多图作品",
        "description": "测试超额图片丢弃",
        "bluesky_images": [str(sample_image)] * 6,
        "is_nsfw": False,
        "is_ai_generated": False,
        "platform_options": {"bluesky_ai_hashtags": False}
    }

    with patch("requests.post", side_effect=_mock_post), patch("requests.get", side_effect=_mock_get):
        ok, msg = disp.publish(payload)
        assert ok is True
        # 严格断言：只上传了 4 张图片
        assert uploaded_blobs_count[0] == 4
        rec = created_records[0]
        assert len(rec["embed"]["images"]) == 4
        # 非 NSFW 帖子不得携带成人标签
        assert "labels" not in rec


def test_publish_thread_reply_chain(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)
    disp._save_credentials({
        "handle": "fox.bsky.social",
        "app_password": "test-password",
        "accessJwt": "mock-jwt",
        "refreshJwt": "mock-refresh",
        "did": "did:plc:test123"
    })

    created_records = []
    created_uris = []

    def _mock_post(url, json=None, data=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if "createRecord" in url:
            created_records.append(json["record"])
            post_idx = len(created_records)
            uri = f"at://did:plc:test123/app.bsky.feed.post/rkey{post_idx}"
            cid = f"cid{post_idx}"
            created_uris.append(uri)
            resp.json.return_value = {"uri": uri, "cid": cid}
        return resp

    def _mock_get(url, params=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if "getPostThread" in url:
            target_uri = params.get("uri")
            idx = created_uris.index(target_uri)
            resp.json.return_value = {
                "thread": {"post": {"record": created_records[idx]}}
            }
        return resp

    # 480 字素长文 (>300)，拆成串推
    long_desc = "这是第一篇长推文案段落。" * 40
    payload = {
        "title": "长推标题",
        "description": long_desc,
        "platform_options": {
            "bluesky_over_limit_action": "thread",
            "bluesky_ai_hashtags": False
        }
    }

    with patch("requests.post", side_effect=_mock_post), patch("requests.get", side_effect=_mock_get):
        ok, msg = disp.publish(payload)
        assert ok is True
        # 串帖数量应 >= 2
        assert len(created_records) >= 2

        # 检查串帖 reply 链引用
        first_post = created_records[0]
        assert "reply" not in first_post

        second_post = created_records[1]
        assert "reply" in second_post
        assert second_post["reply"]["root"]["uri"] == created_uris[0]
        assert second_post["reply"]["parent"]["uri"] == created_uris[0]


def test_publish_over_limit_no_action_ask_returns_false(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)
    disp._save_credentials({
        "handle": "fox.bsky.social",
        "app_password": "test-password",
        "accessJwt": "mock-jwt",
        "did": "did:plc:test123"
    })

    long_desc = "超限文本段落。" * 60
    payload = {
        "title": "长文标题",
        "description": long_desc,
        "platform_options": {
            # 未提供 bluesky_over_limit_action，policy 为 ask
            "bluesky_over_limit_policy": "ask",
            "bluesky_ai_hashtags": False
        }
    }

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        ok, msg = disp.publish(payload)
        assert ok is False
        assert "超限且未指定处理方式" in msg


def test_publish_over_limit_fallback_to_policy_truncate(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)
    disp._save_credentials({
        "handle": "fox.bsky.social",
        "app_password": "test-password",
        "accessJwt": "mock-jwt",
        "did": "did:plc:test123"
    })

    created_records = []

    def _mock_post(url, json=None, data=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if "createRecord" in url:
            created_records.append(json["record"])
            resp.json.return_value = {"uri": "at://did:plc:test123/app.bsky.feed.post/3rkey", "cid": "mockcid"}
        return resp

    def _mock_get(url, params=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if "getPostThread" in url:
            resp.json.return_value = {"thread": {"post": {"record": created_records[-1]}}}
        return resp

    long_desc = "超限文本截断测试段落。" * 40
    payload = {
        "title": "截断测试",
        "description": long_desc,
        "platform_options": {
            # 未提供 bluesky_over_limit_action，policy 为 truncate
            "bluesky_over_limit_policy": "truncate",
            "bluesky_ai_hashtags": False
        }
    }

    with patch("requests.post", side_effect=_mock_post), patch("requests.get", side_effect=_mock_get):
        ok, msg = disp.publish(payload)
        assert ok is True
        assert len(created_records) == 1
        assert count_graphemes(created_records[0]["text"]) <= MAX_GRAPHEMES
        assert created_records[0]["text"].endswith("...")


def test_publish_over_limit_fallback_to_policy_thread(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)
    disp._save_credentials({
        "handle": "fox.bsky.social",
        "app_password": "test-password",
        "accessJwt": "mock-jwt",
        "did": "did:plc:test123"
    })

    created_records = []

    def _mock_post(url, json=None, data=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if "createRecord" in url:
            created_records.append(json["record"])
            resp.json.return_value = {"uri": f"at://did:plc:test123/app.bsky.feed.post/rkey{len(created_records)}", "cid": "mockcid"}
        return resp

    def _mock_get(url, params=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if "getPostThread" in url:
            resp.json.return_value = {"thread": {"post": {"record": created_records[-1]}}}
        return resp

    long_desc = "超限串推策略回退测试段落。" * 40
    payload = {
        "title": "串推测试",
        "description": long_desc,
        "platform_options": {
            # 未提供 bluesky_over_limit_action，policy 为 thread
            "bluesky_over_limit_policy": "thread",
            "bluesky_ai_hashtags": False
        }
    }

    with patch("requests.post", side_effect=_mock_post), patch("requests.get", side_effect=_mock_get):
        ok, msg = disp.publish(payload)
        assert ok is True
        assert len(created_records) >= 2


# ==============================================================================
# 8. 删除帖子 delete_post() 与自检探针测试
# ==============================================================================

def test_delete_post(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)
    disp._save_credentials({
        "handle": "fox.bsky.social",
        "app_password": "test-password",
        "accessJwt": "mock-jwt",
        "did": "did:plc:test123"
    })

    with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
        get_resp = MagicMock()
        get_resp.status_code = 200
        mock_get.return_value = get_resp

        post_resp = MagicMock()
        post_resp.status_code = 200
        mock_post.return_value = post_resp

        ok, msg = disp.delete_post("at://did:plc:test123/app.bsky.feed.post/3mockrkey")
        assert ok is True
        assert "3mockrkey" in msg

        # 验证调用 deleteRecord 的参数
        call_kwargs = mock_post.call_args[1]
        assert "/com.atproto.repo.deleteRecord" in mock_post.call_args[0][0]
        assert call_kwargs["json"]["rkey"] == "3mockrkey"


def test_probe_login_and_selector_health(temp_cookies_dir):
    disp = BlueskyDispatcher(temp_cookies_dir)

    # 未录入凭据
    res_unlogged = disp.probe_login()
    assert res_unlogged["status"] == "fail"
    assert "未录入" in res_unlogged["detail"]

    # 已录入且 session 有效
    disp._save_credentials({
        "handle": "fox.bsky.social",
        "app_password": "test-password",
        "accessJwt": "mock-jwt",
        "did": "did:plc:test123"
    })
    with patch("requests.get") as mock_get:
        resp = MagicMock()
        resp.status_code = 200
        mock_get.return_value = resp

        res_ok = disp.probe_login()
        assert res_ok["status"] == "pass"
        assert "@fox.bsky.social" in res_ok["detail"]

    # 选择器健康自检
    health = disp.check_selectors_health()
    assert len(health) == 1
    assert health[0]["status"] == "pass"
