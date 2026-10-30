# -*- coding: utf-8 -*-
"""
Tests for 平台官方图标与个人主页交互 (feature/platform-icons):
1. assets/icons/ 五个文件存在且可解码
2. platform_icon helper 对五个已知 key 返回非空 QIcon、对未知 key 返回空图标不抛异常
3. 映射表五条 URL 逐字一致
4. 点击处理函数按 key 用系统默认浏览器打开正确 URL
5. 图标独立可点击，点击后对应平台勾选状态不变
6. 图标缺失或损坏时主窗口能正常构造，只显示文字，不报错不崩溃
"""
import copy
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()

from PIL import Image
import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QThread

from config import DEFAULT_CONFIG, load_config
from platform_icons import (
    platform_icon,
    get_platform_clean_key,
    get_platform_display_name,
    get_platform_profile_url,
    open_platform_profile,
    ClickablePlatformIcon,
    DEFAULT_PLATFORM_PROFILE_URLS,
)
from app import MainWindow, SettingsDialog


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

    with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
        win = MainWindow()

    win.data_dir = tmp_path
    win.notify = MagicMock()
    win._clear_draft = MagicMock()

    yield win

    if hasattr(win, "_schedule_poll_timer") and win._schedule_poll_timer.isActive():
        win._schedule_poll_timer.stop()
    if hasattr(win, "_draft_save_timer") and win._draft_save_timer.isActive():
        win._draft_save_timer.stop()
    if hasattr(win, "_layout_save_timer") and win._layout_save_timer.isActive():
        win._layout_save_timer.stop()
    if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
        win.thread.quit()
        win.thread.wait()


# ==============================================================================
# 1. assets/icons/ 文件存在性与解码测试
# ==============================================================================

def test_icon_files_exist_and_decodable():
    """验证 assets/icons/ 五个官方图标文件均存在且可通过 PIL 正常解码"""
    icons_dir = Path(__file__).resolve().parent.parent / "assets" / "icons"
    assert icons_dir.is_dir(), f"目录不存在: {icons_dir}"

    expected_files = {
        "x.png": (32, 32),
        "deviantart.png": (32, 32),
        "patreon.png": (256, 256),
        "civitai.png": (48, 48),
        "kofi.png": (64, 64),
    }

    for filename, min_expected_size in expected_files.items():
        file_path = icons_dir / filename
        assert file_path.exists(), f"图标文件缺失: {file_path}"
        with Image.open(file_path) as im:
            assert im.format == "PNG", f"{filename} 格式非 PNG: {im.format}"
            assert im.width > 0 and im.height > 0
            assert im.size == min_expected_size


# ==============================================================================
# 2. platform_icon 统一 Helper 测试
# ==============================================================================

def test_platform_icon_helper_known_and_unknown_keys(qapp):
    """
    验证 platform_icon helper:
    - 对五个已知 key 返回非空 QIcon
    - 支持常见别名 (x_twitter, ko-fi 等)
    - 对未知 key 或空 key 返回空图标 QIcon()，不抛出异常
    """
    known_keys = ["x", "deviantart", "patreon", "civitai", "kofi"]
    for k in known_keys:
        icon = platform_icon(k)
        assert isinstance(icon, QIcon), f"platform_icon({k}) 应返回 QIcon 实例"
        assert not icon.isNull(), f"已知 key [{k}] 返回的图标不应为空"

    # 别名测试
    assert not platform_icon("x_twitter").isNull()
    assert not platform_icon("twitter").isNull()
    assert not platform_icon("ko-fi").isNull()

    # 未知 key 与非法入参容错
    assert platform_icon("unknown_platform_xyz").isNull()
    assert platform_icon("").isNull()
    assert platform_icon(None).isNull()


# ==============================================================================
# 3. 个人主页映射表逐字一致性断言
# ==============================================================================

def test_platform_profile_urls_exact_match():
    """
    核验 platform_profile_urls 映射表与需求规约逐字一致:
    - x → https://x.com/FOXTONTONTON
    - deviantart → https://www.deviantart.com/foxtontonton
    - patreon → https://patreon.com/foxtontonton
    - civitai → https://civitai.red/user/FOXTONTONTON
    - kofi → https://ko-fi.com/foxtontonton
    """
    expected_mapping = {
        "x": "https://x.com/FOXTONTONTON",
        "deviantart": "https://www.deviantart.com/foxtontonton",
        "patreon": "https://patreon.com/foxtontonton",
        "civitai": "https://civitai.red/user/FOXTONTONTON",
        "kofi": "https://ko-fi.com/foxtontonton",
    }

    # 断言 DEFAULT_CONFIG 中的默认配置
    cfg_urls = DEFAULT_CONFIG.get("platform_profile_urls", {})
    assert cfg_urls == expected_mapping, "DEFAULT_CONFIG['platform_profile_urls'] 与需求不一致"

    # 断言 DEFAULT_PLATFORM_PROFILE_URLS 常量
    assert DEFAULT_PLATFORM_PROFILE_URLS == expected_mapping

    # 断言 get_platform_profile_url 各平台解析值
    for k, url in expected_mapping.items():
        assert get_platform_profile_url(k) == url
    assert get_platform_profile_url("x_twitter") == "https://x.com/FOXTONTONTON"
    assert get_platform_profile_url("ko-fi") == "https://ko-fi.com/foxtontonton"


# ==============================================================================
# 4. open_platform_profile 浏览器调用与容错测试
# ==============================================================================

def test_open_platform_profile_calls_browser(qapp):
    """验证点击处理函数按 key 用系统默认浏览器打开对应 URL"""
    test_cases = [
        ("x", "https://x.com/FOXTONTONTON"),
        ("x_twitter", "https://x.com/FOXTONTONTON"),
        ("deviantart", "https://www.deviantart.com/foxtontonton"),
        ("patreon", "https://patreon.com/foxtontonton"),
        ("civitai", "https://civitai.red/user/FOXTONTONTON"),
        ("kofi", "https://ko-fi.com/foxtontonton"),
    ]

    for key, expected_url in test_cases:
        with patch("webbrowser.open") as mock_open:
            mock_open.return_value = True
            ok = open_platform_profile(key)
            assert ok is True
            mock_open.assert_called_once_with(expected_url)


def test_open_platform_profile_failure_handling(qapp):
    """验证当调起浏览器发生异常时，不弹窗不崩溃，优雅提示并返回 False"""
    with patch("webbrowser.open", side_effect=OSError("Browser not found")):
        mock_win = MagicMock()
        ok = open_platform_profile("x", parent_window=mock_win)
        assert ok is False
        mock_win.notify.assert_called_once()
        assert "无法调起默认浏览器" in mock_win.notify.call_args[0][0]


# ==============================================================================
# 5. 防误触测试：图标点击不改变平台勾选框选中状态
# ==============================================================================

def test_icon_click_does_not_toggle_checkbox(mock_main_window):
    """
    防误触核心断言:
    发布平台勾选区中的图标必须为独立可点击控件，
    点击图标只打开主页，不得触发该平台勾选框的选中/取消。
    """
    win = mock_main_window

    for plat_name, ctrl in win.platform_controls.items():
        if plat_name not in ("x_twitter", "deviantart", "patreon", "kofi", "civitai"):
            continue
        cb = ctrl["checkbox"]
        icon_btn = ctrl["icon_btn"]

        assert isinstance(icon_btn, ClickablePlatformIcon)
        assert icon_btn.cursor().shape() == 13  # Qt.PointingHandCursor is 13

        initial_state = cb.isChecked()

        # 模拟点击图标
        with patch("webbrowser.open") as mock_open:
            mock_open.return_value = True
            icon_btn.click()
            mock_open.assert_called_once()

        # 勾选框状态必须毫发无伤
        assert cb.isChecked() == initial_state, f"点击 {plat_name} 图标后复选框状态被意外改变"

        # 切换状态后再试一次
        cb.setChecked(not initial_state)
        new_state = cb.isChecked()

        with patch("webbrowser.open") as mock_open:
            mock_open.return_value = True
            icon_btn.click()
            mock_open.assert_called_once()

        assert cb.isChecked() == new_state, f"二次点击 {plat_name} 图标后复选框状态被意外改变"


# ==============================================================================
# 6. 健壮性测试：图标缺失时只显示文字，主窗口正常构造
# ==============================================================================

def test_main_window_constructs_when_icons_missing(tmp_path, qapp, monkeypatch):
    """
    当 assets/icons 资源缺失时：
    主窗口仍能正常构造（不报错不崩溃），各平台勾选项仅显示纯文字。
    """
    icons_dir = Path(__file__).resolve().parent.parent / "assets" / "icons"
    temp_dir = Path(__file__).resolve().parent.parent / "assets" / "icons_hidden_for_test"

    # 临时改名隐藏图标目录
    if icons_dir.exists():
        shutil.move(str(icons_dir), str(temp_dir))

    try:
        # 重置已记录日志集合以验证警告输出
        from platform_icons import _LOGGED_MISSING_KEYS
        _LOGGED_MISSING_KEYS.clear()

        # 图标 helper 必须返回 isNull 为 True 的图标，绝不抛异常
        assert platform_icon("x").isNull()
        assert platform_icon("deviantart").isNull()

        # 构造 MainWindow 必须平稳通过
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
        cfg_copy = copy.deepcopy(DEFAULT_CONFIG)
        monkeypatch.setattr("app.load_config", lambda: copy.deepcopy(cfg_copy))
        monkeypatch.setattr("app.save_config", lambda cfg: None)

        with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
            win = MainWindow()

        # 验证各平台控件正常建立，图标控件已隐藏
        for name, ctrl in win.platform_controls.items():
            assert ctrl["checkbox"] is not None
            assert ctrl["icon_btn"].isHidden() or not ctrl["icon_btn"].isVisible()

        if hasattr(win, "_schedule_poll_timer") and win._schedule_poll_timer.isActive():
            win._schedule_poll_timer.stop()
        if hasattr(win, "_draft_save_timer") and win._draft_save_timer.isActive():
            win._draft_save_timer.stop()
        if hasattr(win, "_layout_save_timer") and win._layout_save_timer.isActive():
            win._layout_save_timer.stop()

    finally:
        # 恢复图标目录
        if temp_dir.exists():
            shutil.move(str(temp_dir), str(icons_dir))


# ==============================================================================
# 7. 结果面板与设置对话框图标呈现测试
# ==============================================================================

def test_results_panel_and_settings_icons(mock_main_window):
    """验证分发结果面板每行与系统设置平台账号均集成了 ClickablePlatformIcon"""
    win = mock_main_window

    # 1. 结果面板
    dummy_results = {
        "x_twitter": (True, "https://x.com/post/123", []),
        "deviantart": (False, "Network error", []),
        "patreon": (True, "https://patreon.com/post/456", []),
    }
    win._update_publish_results_panel(dummy_results)
    assert win.publish_results_panel.isVisible()

    result_icons = win.publish_results_panel.findChildren(ClickablePlatformIcon)
    assert len(result_icons) == 3, "结果面板每行平台名前应配置一个 ClickablePlatformIcon"
    for r_icon in result_icons:
        assert r_icon.icon_size_px == 16, "结果面板图标尺寸应约为 16px"

    # 2. 系统设置对话框
    dlg = SettingsDialog(win)
    settings_icons = dlg.findChildren(ClickablePlatformIcon)
    assert len(settings_icons) >= 4, "系统设置平台账号列表中应包含各平台官方图标"
