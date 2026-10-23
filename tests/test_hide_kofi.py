# -*- coding: utf-8 -*-
"""
Tests for P0-2: Ko-fi 移出发布列表 (Hide Ko-fi from Publish List by Default)
"""
import sys
import json
from unittest.mock import MagicMock, patch
if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()
import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread


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
        win.data_dir = tmp_path
        win.notify = MagicMock()

        yield win

        # Cleanup timers to avoid crash on teardown
        for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
            if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                getattr(win, t_attr).stop()
        if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
            win.thread.quit()
            win.thread.wait()


class TestHideKofi:
    def test_default_config_hides_kofi(self, mock_main_window):
        """
        验证默认配置 (platform_kofi_enabled=False) 下：
        - platform_controls 中不包含 kofi
        - 界面平台勾选列表中不显示 Ko-fi
        """
        win = mock_main_window
        assert win.config.get("platform_kofi_enabled") is False

        # 检查 platform_controls 字典
        assert "kofi" not in win.platform_controls
        assert "x_twitter" in win.platform_controls
        assert "deviantart" in win.platform_controls
        assert "patreon" in win.platform_controls

    def test_config_true_displays_kofi(self, mock_main_window):
        """
        验证配置改 True 后：
        - platform_controls 中出现 kofi
        - 界面动态刷新后展示 Ko-fi 卡片
        """
        win = mock_main_window

        # 改为 True 并刷新
        win.config["platform_kofi_enabled"] = True
        win._refresh_platform_cards()

        assert "kofi" in win.platform_controls
        assert "Ko-fi" in win.platform_controls["kofi"]["checkbox"].text()

        # 改回 False 并刷新
        win.config["platform_kofi_enabled"] = False
        win._refresh_platform_cards()

        assert "kofi" not in win.platform_controls

    def test_legacy_draft_with_kofi_ignored_when_disabled(self, mock_main_window, tmp_path):
        """
        验证旧草稿里若存有 Ko-fi 勾选状态：
        - 加载时自动忽略，绝不勾选 Ko-fi
        - 发布的 selected 列表中不含 Ko-fi
        """
        win = mock_main_window
        win.config["platform_kofi_enabled"] = False
        win._refresh_platform_cards()

        # 模拟包含 Ko-fi 的旧草稿
        draft_file = tmp_path / "draft.json"
        draft_data = {
            "title": "Legacy Artwork",
            "tags": "test, art",
            "description": "A great piece",
            "selected_platforms": ["x_twitter", "kofi", "deviantart"]
        }
        draft_file.write_text(json.dumps(draft_data, ensure_ascii=False), encoding="utf-8")

        win._load_draft()

        # 验证标题已加载
        assert win.title_input.text() == "Legacy Artwork"

        # 验证 platform_controls 中不存在 kofi，且被选中的平台只有 x_twitter 和 deviantart
        selected_plats = [name for name, ctrl in win.platform_controls.items() if ctrl["checkbox"].isChecked()]
        assert "kofi" not in selected_plats
        assert "x_twitter" in selected_plats
        assert "deviantart" in selected_plats

    def test_dispatcher_code_preserved(self):
        """
        验证 dispatcher 代码保留不删：
        - KofiDispatcher 仍可正常导入和实例化
        - get_all_dispatchers 仍返回包含 KofiDispatcher 在内的 4 个分发器
        """
        from dispatchers.monetization import KofiDispatcher
        from dispatchers import get_all_dispatchers
        from pathlib import Path

        d = KofiDispatcher(Path("dummy"))
        assert d.name == "kofi"
        assert "Ko-fi" in d.display_name

        all_disps = get_all_dispatchers(Path("dummy"))
        names = [disp.name for disp in all_disps]
        assert "kofi" in names
        assert len(all_disps) >= 4  # 支持包含新接入平台 (如 Civitai)

    def test_settings_dialog_toggle_kofi(self, mock_main_window):
        """
        验证设置对话框中切换 Ko-fi 平台开关能够联动主界面更新
        """
        win = mock_main_window
        from app import SettingsDialog

        dlg = SettingsDialog(parent=win, target_tab=2)
        assert hasattr(dlg, "kofi_enable_check")
        assert dlg.kofi_enable_check.isChecked() is False

        # 勾选开启
        dlg.kofi_enable_check.setChecked(True)
        assert win.config["platform_kofi_enabled"] is True
        assert "kofi" in win.platform_controls

        # 取消勾选关闭
        dlg.kofi_enable_check.setChecked(False)
        assert win.config["platform_kofi_enabled"] is False
        assert "kofi" not in win.platform_controls
        dlg.close()
