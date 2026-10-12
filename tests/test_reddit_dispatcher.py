# -*- coding: utf-8 -*-
"""
RedditDispatcher 单测 (全 mock，不触网)
"""
import json
import sys
import types
from unittest.mock import MagicMock

from dispatchers.reddit import RedditDispatcher


def _make_dispatcher(tmp_path, secrets=None):
    disp = RedditDispatcher(tmp_path)
    if secrets is not None:
        disp.secrets_file.write_text(json.dumps(secrets), encoding="utf-8")
    return disp


def _fake_praw(monkeypatch, submission_url="/r/BlackLatexMale/comments/abc123/test/"):
    """向 sys.modules 注入假 praw 模块；按目标名返回独立 subreddit mock"""
    fake = types.ModuleType("praw")
    reddit = MagicMock()
    targets = {}

    def fake_subreddit(name):
        if name not in targets:
            submission = MagicMock()
            submission.permalink = (submission_url if not name.startswith("u_")
                                    else "/user/test_user/comments/def456/test/")
            sub = MagicMock()
            sub.display_name = name
            sub.submit_image = MagicMock(return_value=submission)
            sub.submit_gallery = MagicMock(return_value=submission)
            targets[name] = sub
        return targets[name]

    reddit.subreddit = MagicMock(side_effect=fake_subreddit)
    me = MagicMock()
    me.name = "test_user"
    reddit.user.me = MagicMock(return_value=me)
    fake.Reddit = MagicMock(return_value=reddit)
    monkeypatch.setitem(sys.modules, "praw", fake)
    return reddit, targets


SECRETS = {
    "client_id": "cid",
    "client_secret": "csec",
    "username": "test_user",
    "password": "pw",
}


def _img(tmp_path, name="a.png"):
    p = tmp_path / name
    p.write_bytes(b"fake")
    return str(p)


def test_reddit_no_secrets_loud_failure(tmp_path, monkeypatch):
    """凭据文件缺失：publish / check_login 必须大声失败，绝不静默。"""
    disp = _make_dispatcher(tmp_path, secrets=None)
    ok, msg, _ = disp.publish({"images": [_img(tmp_path)]})
    assert ok is False
    assert "凭据未配置" in msg
    ok2, msg2 = disp.check_login()
    assert ok2 is False
    assert "鉴权失败" in msg2 or "凭据未配置" in msg2


def test_reddit_no_image_file(tmp_path, monkeypatch):
    """图片文件不存在：直接报错。"""
    _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    ok, msg, _ = disp.publish({"images": [str(tmp_path / "missing.png")]})
    assert ok is False
    assert "未找到可上传的图片文件" in msg


def test_reddit_no_target_loud_failure(tmp_path, monkeypatch):
    """未指定分区且关闭个人主页发布：大声报错，无处可发。"""
    _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    monkeypatch.setattr(disp, "_default_subreddit", lambda: "")
    monkeypatch.setattr(disp, "_default_post_to_profile", lambda: False)
    ok, msg, _ = disp.publish({"images": [_img(tmp_path)], "platform_options": {}})
    assert ok is False
    assert "无处可发" in msg


def test_reddit_profile_only_when_no_subreddit(tmp_path, monkeypatch):
    """未指定分区但开启个人主页发布：只发个人主页。"""
    _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    monkeypatch.setattr(disp, "_default_subreddit", lambda: "")
    monkeypatch.setattr(disp, "_default_post_to_profile", lambda: True)
    ok, msg, _ = disp.publish({"images": [_img(tmp_path)], "platform_options": {}})
    assert ok is True
    assert "u/u_test_user" in msg
    assert "r/BlackLatexMale" not in msg


def test_reddit_dual_post_subreddit_and_profile(tmp_path, monkeypatch):
    """默认双投：分区帖 + 个人主页帖同时发出；flair 只进分区帖。"""
    reddit, targets = _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    ok, msg, compliance = disp.publish({
        "images": [_img(tmp_path)],
        "title": "dual",
        "is_nsfw": True,
        "platform_options": {
            "reddit_subreddit": "BlackLatexMale",
            "reddit_flair_id": "flair123",
        },
    })
    assert ok is True
    assert "r/BlackLatexMale" in msg and "u/u_test_user" in msg
    assert "BlackLatexMale" in targets and "u_test_user" in targets
    targets["BlackLatexMale"].submit_image.assert_called_once()
    targets["u_test_user"].submit_image.assert_called_once()
    assert targets["BlackLatexMale"].submit_image.call_args.kwargs.get("flair_id") == "flair123"
    assert "flair_id" not in targets["u_test_user"].submit_image.call_args.kwargs


def test_reddit_partial_failure_is_loud(tmp_path, monkeypatch):
    """双投中一个失败：整体返回 False，大声说明哪边失败。"""
    reddit, targets = _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    payload = {
        "images": [_img(tmp_path)],
        "title": "dual",
        "platform_options": {"reddit_subreddit": "BlackLatexMale"},
    }
    ok, _, _ = disp.publish(payload)
    assert ok is True
    targets["u_test_user"].submit_image.side_effect = RuntimeError("boom")
    ok, msg, _ = disp.publish(payload)
    assert ok is False
    assert "u/u_test_user" in msg and "失败" in msg
    assert "r/BlackLatexMale" in msg and "成功" in msg


def test_reddit_single_image_submit(tmp_path, monkeypatch):
    """单图走 submit_image，NSFW/spoiler 标记透传，返回帖子 URL。"""
    reddit, targets = _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    payload = {
        "images": [_img(tmp_path)],
        "title": "test title",
        "is_nsfw": True,
        "platform_options": {
            "reddit_subreddit": "BlackLatexMale",
            "reddit_post_to_profile": False,
        },
    }
    ok, msg, compliance = disp.publish(payload)
    assert ok is True
    assert "https://www.reddit.com/r/BlackLatexMale/comments/abc123/test/" in msg
    subreddit = targets["BlackLatexMale"]
    subreddit.submit_image.assert_called_once()
    assert subreddit.submit_image.call_args.kwargs.get("nsfw") is True
    reddit.subreddit.assert_any_call("BlackLatexMale")
    assert any(c["item"] == "post_created:r/BlackLatexMale" for c in compliance)


def test_reddit_multi_image_gallery(tmp_path, monkeypatch):
    """多图走 submit_gallery。"""
    reddit, targets = _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    imgs = [_img(tmp_path, f"{i}.png") for i in range(3)]
    ok, msg, _ = disp.publish({
        "images": imgs,
        "title": "gallery",
        "platform_options": {
            "reddit_subreddit": "BlackLatexMale",
            "reddit_post_to_profile": False,
        },
    })
    assert ok is True
    subreddit = targets["BlackLatexMale"]
    subreddit.submit_gallery.assert_called_once()
    subreddit.submit_image.assert_not_called()


def test_reddit_title_truncated(tmp_path, monkeypatch):
    """标题超 300 字符自动截断。"""
    reddit, targets = _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    ok, _, _ = disp.publish({
        "images": [_img(tmp_path)],
        "title": "t" * 500,
        "platform_options": {
            "reddit_subreddit": "BlackLatexMale",
            "reddit_post_to_profile": False,
        },
    })
    assert ok is True
    subreddit = targets["BlackLatexMale"]
    sent_title = subreddit.submit_image.call_args.kwargs.get("title") \
        or subreddit.submit_image.call_args.args[1]
    assert len(sent_title) == 300


def test_reddit_check_login_ok(tmp_path, monkeypatch):
    _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    ok, msg = disp.check_login()
    assert ok is True
    assert "test_user" in msg


def test_reddit_prefers_reddit_images(tmp_path, monkeypatch):
    """优先使用 payload['reddit_images'] 覆盖默认 images"""
    _, targets = _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    img_raw = _img(tmp_path, "raw.png")
    img_custom = _img(tmp_path, "reddit_custom.png")
    ok, _, _ = disp.publish({
        "images": [img_raw],
        "reddit_images": [img_custom],
        "title": "custom img test",
        "platform_options": {
            "reddit_subreddit": "BlackLatexMale",
            "reddit_post_to_profile": False,
        },
    })
    assert ok is True
    subreddit = targets["BlackLatexMale"]
    called_img = subreddit.submit_image.call_args.args[0]
    assert "reddit_custom.png" in called_img
