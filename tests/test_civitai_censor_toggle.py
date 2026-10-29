# -*- coding: utf-8 -*-
"""
Unit tests for Civitai Censor Toggle (feature/civitai-censor-toggle)
严格覆盖需求:
1. 配置默认值 False 与持久化
2. UI Civitai 平台选项面板中打码保护复选框接法与联动
3. "套用到所有平台"按钮自动覆盖 Civitai
4. 选图路由:
   - 开关开 (总开且平台开) -> Civitai 拿到打码版路径
   - 开关关 (总开但平台关) -> 原图
   - 总开关关 (总关且平台开) -> 原图 (与逻辑验证)
5. 定时发布快照捕获 Civitai 打码选项
"""
import copy
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image

from config import DEFAULT_CONFIG
from app import MainWindow
from schedule_manager import ScheduleManager
from history_manager import PublishHistoryManager


@pytest.fixture(scope="session")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def window(tmp_path, qapp):
    img_p = tmp_path / "test_art.png"
    im = Image.new("RGB", (100, 100), color=(120, 180, 240))
    im.save(img_p, "PNG")

    test_cfg = copy.deepcopy(DEFAULT_CONFIG)
    test_cfg["defaults"]["civitai_censor"] = False

    with patch("app.load_config", return_value=test_cfg), \
         patch("app.save_config"), \
         patch("app.CensorEngine") as mock_ce_cls, \
         patch("app.WatermarkEngine"):

        mock_ce = MagicMock()
        mock_ce.apply_censor.return_value = (None, [[10, 10, 50, 50]])
        mock_ce_cls.return_value = mock_ce

        win = MainWindow()
        win.notify = MagicMock()
        win.image_paths = [str(img_p)]
        win.data_dir = tmp_path
        win.history_manager = PublishHistoryManager(tmp_path)
        win.schedule_manager = ScheduleManager(storage_path=tmp_path / "scheduled_posts.json")
        for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
            if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                getattr(win, t_attr).stop()
        yield win
        for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
            if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                getattr(win, t_attr).stop()


def test_default_config_civitai_censor_false():
    """验证 config.py 默认配置中 defaults.civitai_censor 为 False"""
    assert "defaults" in DEFAULT_CONFIG
    assert "civitai_censor" in DEFAULT_CONFIG["defaults"]
    assert DEFAULT_CONFIG["defaults"]["civitai_censor"] is False


def test_ui_civitai_censor_registration_and_persistence(window):
    """验证 UI 中 Civitai 打码保护复选框的注册、状态改变与配置持久化"""
    assert "civitai" in window.platform_controls
    civitai_ctrl = window.platform_controls["civitai"]
    assert "options" in civitai_ctrl
    assert "censor" in civitai_ctrl["options"]

    cb = civitai_ctrl["options"]["censor"]
    # 默认应为 False
    assert cb.isChecked() is False

    # 勾选变化时应触发配置保存并更新 config["defaults"]["civitai_censor"]
    cb.setChecked(True)
    window._sync_config_from_ui()
    assert window.config.get("defaults", {}).get("civitai_censor") is True

    # 联动总开关逻辑: 当总打码开关为关时，勾选平台打码保护，自动触发总开关开启
    window.auto_censor_cb.setChecked(False)
    cb.setChecked(False)
    assert window.auto_censor_cb.isChecked() is False

    cb.setChecked(True)
    assert window.auto_censor_cb.isChecked() is True


def test_apply_censor_to_all_platforms_covers_civitai(window):
    """验证 '套用到所有平台' 按钮自动同步覆盖 Civitai"""
    cb = window.platform_controls["civitai"]["options"]["censor"]

    # 1. 总开关开启并套用到所有平台
    window.auto_censor_cb.setChecked(True)
    window._apply_censor_to_all_platforms()
    assert cb.isChecked() is True
    assert window.config["defaults"]["civitai_censor"] is True

    # 2. 总开关关闭并套用到所有平台
    window.auto_censor_cb.setChecked(False)
    window._apply_censor_to_all_platforms()
    assert cb.isChecked() is False
    assert window.config["defaults"]["civitai_censor"] is False


def test_routing_civitai_censor_on_gets_censored_image(window):
    """选图路由测试 1: 开关开 (总打码开 且 Civitai 打码开) -> Civitai 拿到打码版路径"""
    window.title_input.setText("Censor Test Title")
    window.auto_censor_cb.setChecked(True)
    window.watermark_enabled_cb.setChecked(False)

    for name, ctrl in window.platform_controls.items():
        ctrl["checkbox"].setChecked(name == "civitai")
    window.platform_controls["civitai"]["options"]["censor"].setChecked(True)

    with patch("app.cv2.imwrite"), patch("app.PublishThread"):
        window._start_publishing()

    assert window._last_publish_payload is not None
    c_images = window._last_publish_payload.get("civitai_images", [])
    assert len(c_images) == 1
    assert "censored" in c_images[0]


def test_routing_civitai_censor_off_gets_raw_image(window):
    """选图路由测试 2: 开关关 (总打码开 但 Civitai 打码关) -> Civitai 拿到原图路径"""
    window.title_input.setText("Censor Test Title")
    window.auto_censor_cb.setChecked(True)
    window.watermark_enabled_cb.setChecked(False)

    for name, ctrl in window.platform_controls.items():
        ctrl["checkbox"].setChecked(name == "civitai")
    window.platform_controls["civitai"]["options"]["censor"].setChecked(False)

    with patch("app.cv2.imwrite"), patch("app.PublishThread"):
        window._start_publishing()

    assert window._last_publish_payload is not None
    c_images = window._last_publish_payload.get("civitai_images", [])
    assert len(c_images) == 1
    assert "censored" not in c_images[0]
    assert c_images[0] == window.image_paths[0]


def test_routing_master_censor_off_civitai_on_gets_raw_image(window):
    """选图路由测试 3: 总开关关 (与逻辑: 总打码关 且 Civitai 打码开) -> 原图"""
    window.title_input.setText("Censor Test Title")
    window.watermark_enabled_cb.setChecked(False)

    for name, ctrl in window.platform_controls.items():
        ctrl["checkbox"].setChecked(name == "civitai")
    # 先勾选平台打码，再关闭总打码开关以测试与逻辑
    window.platform_controls["civitai"]["options"]["censor"].setChecked(True)
    window.auto_censor_cb.setChecked(False)
    assert window.auto_censor_cb.isChecked() is False
    assert window.platform_controls["civitai"]["options"]["censor"].isChecked() is True

    with patch("app.cv2.imwrite"), patch("app.PublishThread"):
        window._start_publishing()

    assert window._last_publish_payload is not None
    c_images = window._last_publish_payload.get("civitai_images", [])
    assert len(c_images) == 1
    assert "censored" not in c_images[0]
    assert c_images[0] == window.image_paths[0]


def test_scheduled_snapshot_captures_civitai_censor(window):
    """验证定时发布快照能完整记录 Civitai 打码保护选项"""
    window.title_input.setText("Scheduled Test Art")
    window.platform_controls["civitai"]["checkbox"].setChecked(True)

    # 1. 勾选 Civitai 打码
    window.platform_controls["civitai"]["options"]["censor"].setChecked(True)
    snap1 = window._capture_current_post_snapshot()
    assert snap1 is not None
    assert "platform_options" in snap1
    assert snap1["platform_options"].get("civitai_censor") is True

    # 2. 取消勾选 Civitai 打码
    window.platform_controls["civitai"]["options"]["censor"].setChecked(False)
    snap2 = window._capture_current_post_snapshot()
    assert snap2 is not None
    assert snap2["platform_options"].get("civitai_censor") is False
