# -*- coding: utf-8 -*-
"""
RedditDispatcher 单测 (全 mock，不触网)
"""
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dispatchers.reddit import RedditDispatcher


def _make_dispatcher(tmp_path, secrets=None):
    disp = RedditDispatcher(tmp_path)
    if secrets is not None:
        disp.secrets_file.write_text(json.dumps(secrets), encoding="utf-8")
    return disp


def _fake_praw(monkeypatch, submission_url="/r/BlackLatexMale/comments/abc123/test/"):
    """向 sys.modules 注入假 praw 模块"""
    fake = types.ModuleType("praw")
    reddit = MagicMock()
    submission = MagicMock()
    submission.permalink = submission_url
    subreddit = MagicMock()
    subreddit.display_name = "BlackLatexMale"
    subreddit.submit_image = MagicMock(return_value=submission)
    subreddit.submit_gallery = MagicMock(return_value=submission)
    reddit.subreddit = MagicMock(return_value=subreddit)
    me = MagicMock()
    me.name = "test_user"
    reddit.user.me = MagicMock(return_value=me)
    fake.Reddit = MagicMock(return_value=reddit)
    monkeypatch.setitem(sys.modules, "praw", fake)
    return reddit, subreddit, submission


SECRETS = {
    "client_id": "cid",
    "client_secret": "csec",
    "username": "test_user",
    "password": "pw",
}


def test_reddit_no_secrets_loud_failure(tmp_path, monkeypatch):
    """凭据文件缺失：publish / check_login 必须大声失败，绝不静默。"""
    disp = _make_dispatcher(tmp_path, secrets=None)
    (tmp_path / "a.png").write_bytes(b"fake")
    ok, msg, _ = disp.publish({"images": [str(tmp_path / "a.png")]})
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


def test_reddit_no_subreddit_configured(tmp_path, monkeypatch):
    """未指定分区且配置无默认值：大声报错。"""
    _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    monkeypatch.setattr(disp, "_default_subreddit", lambda: "")
    img = tmp_path / "a.png"
    img.write_bytes(b"fake")
    ok, msg, _ = disp.publish({"images": [str(img)], "platform_options": {}})
    assert ok is False
    assert "未指定目标分区" in msg


def test_reddit_single_image_submit(tmp_path, monkeypatch):
    """单图走 submit_image，NSFW/spoiler 标记透传，返回帖子 URL。"""
    reddit, subreddit, submission = _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    img = tmp_path / "a.png"
    img.write_bytes(b"fake")
    payload = {
        "images": [str(img)],
        "title": "test title",
        "is_nsfw": True,
        "platform_options": {"reddit_subreddit": "BlackLatexMale"},
    }
    ok, msg, compliance = disp.publish(payload)
    assert ok is True
    assert "https://www.reddit.com/r/BlackLatexMale/comments/abc123/test/" in msg
    subreddit.submit_image.assert_called_once()
    kwargs = subreddit.submit_image.call_args
    assert kwargs.kwargs.get("nsfw") is True
    reddit.subreddit.assert_called_with("BlackLatexMale")
    assert any(c["item"] == "post_created" for c in compliance)


def test_reddit_multi_image_gallery(tmp_path, monkeypatch):
    """多图走 submit_gallery。"""
    reddit, subreddit, submission = _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    imgs = []
    for i in range(3):
        p = tmp_path / f"{i}.png"
        p.write_bytes(b"fake")
        imgs.append(str(p))
    ok, msg, _ = disp.publish({
        "images": imgs,
        "title": "gallery",
        "platform_options": {"reddit_subreddit": "BlackLatexMale"},
    })
    assert ok is True
    subreddit.submit_gallery.assert_called_once()
    subreddit.submit_image.assert_not_called()


def test_reddit_title_truncated(tmp_path, monkeypatch):
    """标题超 300 字符自动截断。"""
    reddit, subreddit, submission = _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    img = tmp_path / "a.png"
    img.write_bytes(b"fake")
    ok, _, _ = disp.publish({
        "images": [str(img)],
        "title": "t" * 500,
        "platform_options": {"reddit_subreddit": "BlackLatexMale"},
    })
    assert ok is True
    sent_title = subreddit.submit_image.call_args.kwargs.get("title") \
        or subreddit.submit_image.call_args.args[1]
    assert len(sent_title) == 300


def test_reddit_check_login_ok(tmp_path, monkeypatch):
    _fake_praw(monkeypatch)
    disp = _make_dispatcher(tmp_path, secrets=SECRETS)
    ok, msg = disp.check_login()
    assert ok is True
    assert "test_user" in msg
