# -*- coding: utf-8 -*-
"""
Tests for P2-1: 数据导出与导入 (Data Export & Import)
"""
import sys
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()
import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread
from data_transfer import export_user_data, import_user_data


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def mock_data_env(tmp_path):
    """构建用于测试的完整数据目录"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 1. presets.json (配置)
    (data_dir / "presets.json").write_text(json.dumps({"ui_theme": "deep_navy", "test_key": "val1"}), encoding="utf-8")

    # 2. draft.json (草稿)
    (data_dir / "draft.json").write_text(json.dumps({"title": "Draft Title", "tags": "art, test"}), encoding="utf-8")

    # 3. scheduled_posts.json (定时任务)
    (data_dir / "scheduled_posts.json").write_text(json.dumps([{"id": "t1", "title": "Scheduled"}]), encoding="utf-8")

    # 4. publish_history.json (发布历史)
    (data_dir / "publish_history.json").write_text(json.dumps([{"id": "h1", "title": "History Post"}]), encoding="utf-8")

    # 5. window_state.json (窗口记忆)
    (data_dir / "window_state.json").write_text(json.dumps({"width": 1280, "height": 800}), encoding="utf-8")

    # 6. cookies 目录及凭据 (登录态)
    cookies_dir = data_dir / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    (cookies_dir / "x_twitter.json").write_text(json.dumps([{"name": "auth_token", "value": "secret_token_123"}]), encoding="utf-8")
    (cookies_dir / "deviantart.json").write_text(json.dumps([{"name": "userinfo", "value": "da_session_456"}]), encoding="utf-8")

    # 7. cache 临时目录 (不应被打包)
    cache_dir = data_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "temp_render.png").write_bytes(b"ephemeral_bytes")

    return data_dir


@pytest.fixture
def mock_main_window(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    from app import MainWindow

    with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
        win = MainWindow()
        win.data_dir = tmp_path / "data"
        win.data_dir.mkdir(parents=True, exist_ok=True)
        win.notify = MagicMock()

        yield win

        # Cleanup timers
        for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
            if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                getattr(win, t_attr).stop()
        if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
            win.thread.quit()
            win.thread.wait()


class TestDataTransfer:
    def test_export_user_data_contains_all_key_files(self, mock_data_env, tmp_path):
        """
        单测要求 1:
        导出后解压断言关键文件齐全（config、草稿、profile/登录态、定时任务、历史记录），
        并包含登录凭证防泄漏提示元数据。
        """
        zip_dest = tmp_path / "ArtCrossPost_Export.zip"
        res_path = export_user_data(mock_data_env, zip_dest)
        assert res_path.exists()

        # 解压到临时提取目录进行断言
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with zipfile.ZipFile(res_path, "r") as zf:
            zf.extractall(extract_dir)

        # 1. 验证关键文件齐全
        assert (extract_dir / "presets.json").exists()
        assert json.loads((extract_dir / "presets.json").read_text(encoding="utf-8"))["ui_theme"] == "deep_navy"

        assert (extract_dir / "draft.json").exists()
        assert json.loads((extract_dir / "draft.json").read_text(encoding="utf-8"))["title"] == "Draft Title"

        assert (extract_dir / "scheduled_posts.json").exists()
        assert (extract_dir / "publish_history.json").exists()
        assert (extract_dir / "window_state.json").exists()

        # 2. 验证平台登录凭证 profile (cookies) 完整导出
        assert (extract_dir / "cookies" / "x_twitter.json").exists()
        assert (extract_dir / "cookies" / "deviantart.json").exists()

        # 3. 验证 cache 目录不包含在压缩包内
        assert not (extract_dir / "cache").exists()

        # 4. 验证元数据提示语
        meta_file = extract_dir / "backup_metadata.json"
        assert meta_file.exists()
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        assert "包含登录凭证，请妥善保管" in meta.get("warning", "")

    def test_import_user_data_creates_backup_and_restores(self, mock_data_env, tmp_path):
        """
        单测要求 2:
        导入后断言数据正确恢复，且在覆盖前把现有 data/ 完整备份为 data_backup_时间戳/
        """
        # 1. 先用 mock_data_env 制作一个目标备份包 (内容为 Version 2)
        v2_data_dir = tmp_path / "v2_data"
        v2_data_dir.mkdir()
        (v2_data_dir / "presets.json").write_text(json.dumps({"ui_theme": "restored_theme"}), encoding="utf-8")
        (v2_data_dir / "draft.json").write_text(json.dumps({"title": "Restored Draft Title"}), encoding="utf-8")

        v2_cookies = v2_data_dir / "cookies"
        v2_cookies.mkdir()
        (v2_cookies / "x_twitter.json").write_text(json.dumps([{"name": "restored_token", "value": "token_v2"}]), encoding="utf-8")

        backup_zip = tmp_path / "RestorePackage.zip"
        export_user_data(v2_data_dir, backup_zip)

        # 2. 针对现有的 mock_data_env 执行导入覆盖
        success, msg, backup_dir = import_user_data(backup_zip, mock_data_env)
        assert success is True
        assert "数据恢复成功" in msg
        assert backup_dir is not None
        assert backup_dir.exists()
        assert "data_backup_" in backup_dir.name

        # 3. 验证安全备份目录中保留了覆盖前的旧数据
        old_draft = json.loads((backup_dir / "draft.json").read_text(encoding="utf-8"))
        assert old_draft["title"] == "Draft Title"

        # 4. 验证现有数据目录已经正确恢复为新数据
        new_draft = json.loads((mock_data_env / "draft.json").read_text(encoding="utf-8"))
        assert new_draft["title"] == "Restored Draft Title"

        new_config = json.loads((mock_data_env / "presets.json").read_text(encoding="utf-8"))
        assert new_config["ui_theme"] == "restored_theme"

        new_cookies = json.loads((mock_data_env / "cookies" / "x_twitter.json").read_text(encoding="utf-8"))
        assert new_cookies[0]["name"] == "restored_token"

    def test_mainwindow_export_import_integration(self, mock_main_window, tmp_path):
        """验证 MainWindow 的导出/导入交互方法及通知"""
        win = mock_main_window
        (win.data_dir / "draft.json").write_text(json.dumps({"title": "Original UI Post"}), encoding="utf-8")

        # 导出
        export_file = tmp_path / "ui_backup.zip"
        res = win._export_all_data(str(export_file))
        assert res.exists()
        assert any("数据已成功导出" in str(c) for c in win.notify.call_args_list)

        # 导入一个假包测试错误处理
        invalid_file = tmp_path / "corrupted.zip"
        invalid_file.write_text("not a real zip")
        succ, err_msg, _ = win._import_all_data(str(invalid_file))
        assert succ is False
