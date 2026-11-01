import ast
import glob
import inspect
import os
import sys
from unittest.mock import MagicMock, patch
import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread

if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()

from dispatchers.civitai import CivitaiDispatcher


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def mock_main_window(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    from app import MainWindow

    with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
        win = MainWindow()
        win.notify = MagicMock()
        yield win

        for t_attr in (
            "_draft_save_timer",
            "_layout_save_timer",
            "_config_save_timer",
            "_preview_timer",
            "x_counter_timer",
            "_schedule_poll_timer",
            "_font_apply_timer",
            "_thumb_size_timer",
        ):
            if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                getattr(win, t_attr).stop()
        if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
            win.thread.quit()
            win.thread.wait()


def test_ast_no_duplicate_methods_in_dispatchers_and_app():
    """遍历 dispatchers/*.py 与 app.py 所有 ClassDef，断言无同名方法被重复定义遮盖"""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_files = glob.glob(os.path.join(repo_root, "dispatchers", "*.py")) + [
        os.path.join(repo_root, "app.py")
    ]

    duplicates_found = []
    for filepath in target_files:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                seen_methods = set()
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if item.name in seen_methods:
                            duplicates_found.append(f"{os.path.basename(filepath)}::{node.name}::{item.name}")
                        seen_methods.add(item.name)

    assert not duplicates_found, f"Found shadowed/duplicate methods in classes: {duplicates_found}"


def test_civitai_methods_signatures():
    """断言 CivitaiDispatcher 中 prompt_api_key 与 login_interactive 的方法签名与参数解耦"""
    # 1. prompt_api_key 必须存在且包含 parent_window 参数
    assert hasattr(CivitaiDispatcher, "prompt_api_key")
    sig_prompt = inspect.signature(CivitaiDispatcher.prompt_api_key)
    assert "parent_window" in sig_prompt.parameters
    assert sig_prompt.parameters["parent_window"].default is None

    # 2. login_interactive 必须存在且支持无参调用 (只有 self 无其它必选参数)
    assert hasattr(CivitaiDispatcher, "login_interactive")
    sig_login = inspect.signature(CivitaiDispatcher.login_interactive)
    non_self_required = [
        p.name for p in sig_login.parameters.values()
        if p.name != "self" and p.default is inspect.Parameter.empty
    ]
    assert len(non_self_required) == 0, f"login_interactive has unexpected required params: {non_self_required}"


def test_open_civitai_key_dialog_normal(mock_main_window):
    """测试主窗口 _open_civitai_key_dialog 正常调用 prompt_api_key 并更新状态"""
    win = mock_main_window
    mock_civitai = MagicMock(spec=CivitaiDispatcher)
    mock_civitai.name = "civitai"
    mock_civitai.prompt_api_key.return_value = (True, "Civitai Key 校验成功")

    with patch.object(win, "dispatchers", [mock_civitai]), \
         patch.object(win, "_update_civitai_key_status_badge") as mock_update_badge, \
         patch.object(win, "_refresh_all_platform_statuses") as mock_refresh:

        win._open_civitai_key_dialog()

        mock_civitai.prompt_api_key.assert_called_once_with(parent_window=win)
        mock_update_badge.assert_called_once()
        win.notify.assert_called_once_with("Civitai API Key 录入成功！", level="success")
        mock_refresh.assert_called_once()


def test_open_civitai_key_dialog_exception_handled(mock_main_window):
    """测试当 prompt_api_key 抛出未知异常时，_open_civitai_key_dialog 优雅捕获并 notify 错误且不闪退"""
    win = mock_main_window
    mock_civitai = MagicMock(spec=CivitaiDispatcher)
    mock_civitai.name = "civitai"
    mock_civitai.prompt_api_key.side_effect = RuntimeError("网络阻断或弹窗异常")

    with patch.object(win, "dispatchers", [mock_civitai]):
        # 不应抛出异常使程序崩溃
        win._open_civitai_key_dialog()

        win.notify.assert_called_once()
        args, kwargs = win.notify.call_args
        assert "Civitai Key 录入异常" in args[0]
        assert kwargs.get("level") == "error"
