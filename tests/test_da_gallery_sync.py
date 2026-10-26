# -*- coding: utf-8 -*-
"""
ArtCrossPost - DeviantArt Gallery Synchronization Unit Tests
测试覆盖 需求一：相册列表同步 (feature/da-gallery-sync):
1. 下拉列表解析 (HTTP 方式与 Playwright 降级方式)
2. 新相册出现后可被正确选中与持久化，原有选中状态在刷新时保持
3. 刷新失败必须明确弹窗报错 (QMessageBox.critical)，严禁静默沿用或吞异常
4. 刷新成功更新 config["deviantart_known_galleries"] 并同步设置弹窗与主界面相册下拉框
"""
import sys
import json
from unittest.mock import MagicMock, patch
if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()
import pytest
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import QThread

from dispatchers.deviantart import DeviantArtDispatcher
from app import MainWindow, SettingsDialog, get_da_gallery_from_widget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def mock_da_dispatcher(tmp_path):
    disp = DeviantArtDispatcher(cookies_dir=tmp_path)
    # 模拟账号登录态与 userinfo cookie
    userinfo_payload = json.dumps({"username": "foxtontonton", "usericon": "https://example.com/icon.jpg"})
    disp.get_cookies = MagicMock(return_value=[
        {"name": "userinfo", "value": userinfo_payload, "domain": ".deviantart.com"}
    ])
    return disp


@pytest.fixture
def mock_main_window(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
        win = MainWindow()
        win.data_dir = tmp_path
        win.notify = MagicMock()
        yield win

        for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
            if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                getattr(win, t_attr).stop()
        if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
            win.thread.quit()
            win.thread.wait()
        if hasattr(win, "_da_sync_worker") and isinstance(win._da_sync_worker, QThread) and win._da_sync_worker.isRunning():
            win._da_sync_worker.quit()
            win._da_sync_worker.wait()


class TestDaGallerySync:
    """需求 一：DeviantArt 相册列表同步测试"""

    def test_fetch_galleries_success_http(self, mock_da_dispatcher):
        """测试 1: 通过 HTTP 带 Cookie 会话快速成功拉取并解析相册列表"""
        html_content = """
        <html>
            <body>
                <a href="/foxtontonton/gallery/1001/featured">Featured\n<span>12 deviations</span></a>
                <a href="/foxtontonton/gallery/1002/fan-works">Fan works\n<span>5 deviations</span></a>
                <a href="/foxtontonton/gallery/1003/nsfw---ai-training-clip">NSFW - Ai Training Clip</a>
                <a href="/foxtontonton/gallery/1004/cast-of-characters">Cast of Characters</a>
                <a href="/foxtontonton/gallery/all">All Deviations (Not gallery)</a>
            </body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html_content

        with patch("requests.Session.get", return_value=mock_resp):
            ok, gals, msg = mock_da_dispatcher.fetch_galleries()

        assert ok is True
        assert msg == ""
        # 验证 Featured 置顶，且成功解析各个相册名称
        assert gals[0] == "Featured"
        assert "Fan works" in gals
        assert "NSFW - Ai Training Clip" in gals
        assert "Cast of Characters" in gals
        assert "All Deviations (Not gallery)" not in gals
        assert len(gals) == 4

    def test_fetch_galleries_fallback_playwright(self, mock_da_dispatcher):
        """测试 2: 当 HTTP 请求失败时，平滑降级走 Playwright 浏览器渲染拉取"""
        mock_pw_links = [
            "Featured",
            "Fan works",
            "NSFW - Ai Training Clip",
            "Film set",
            "Cage - A muscle growth story",
        ]

        # 模拟 HTTP 抛出异常
        with patch("requests.Session.get", side_effect=Exception("HTTP connection reset")):
            # 模拟 Playwright 页面求值返回相册列表
            mock_page = MagicMock()
            mock_page.evaluate.return_value = mock_pw_links
            mock_context = MagicMock()
            mock_context.pages = [mock_page]

            with patch.object(mock_da_dispatcher, "create_persistent_stealth_context", return_value=mock_context), \
                 patch.object(mock_da_dispatcher, "safe_goto"):
                ok, gals, msg = mock_da_dispatcher.fetch_galleries()

        assert ok is True
        assert msg == ""
        assert gals[0] == "Featured"
        assert gals == mock_pw_links

    def test_fetch_galleries_not_logged_in_error(self, tmp_path):
        """测试 3: 未登录状态下拉取相册应明确报错，不静默沿用旧列表"""
        disp = DeviantArtDispatcher(cookies_dir=tmp_path)
        disp.get_cookies = MagicMock(return_value=[])

        ok, gals, msg = disp.fetch_galleries()
        assert ok is False
        assert gals == []
        assert "本地无 Cookie" in msg and "登录" in msg

    def test_fetch_galleries_both_fail_explicit_error(self, mock_da_dispatcher):
        """测试 4: HTTP 与 Playwright 均失败时必须明确报错"""
        with patch("requests.Session.get", side_effect=Exception("Network unreachable")), \
             patch("playwright.sync_api.sync_playwright", side_effect=Exception("Browser launch failed")):
            ok, gals, msg = mock_da_dispatcher.fetch_galleries()

        assert ok is False
        assert gals == []
        assert "无法连接 DeviantArt 账号拉取相册" in msg

    def test_combos_population_and_new_gallery_selectable(self, mock_main_window):
        """测试 5: 相册下拉框数据解析、新相册追加并可被选中、原有选中项保持"""
        win = mock_main_window
        da_combo = win.platform_controls["deviantart"]["options"]["gallery"]

        # 重置为已知标准测试配置
        initial_galleries = [
            "Featured",
            "NSFW - Ai Training Clip",
            "Cast of Characters",
            "Cage - A muscle growth story",
            "Film set"
        ]
        win.config["deviantart_known_galleries"] = initial_galleries
        win._populate_da_gallery_combo(da_combo)

        # 初始默认配置
        assert da_combo.count() >= 5
        assert da_combo.itemData(0) == ""
        assert da_combo.itemText(0) == "默认相册 (Featured)"

        # 模拟用户当前选中了 "Cast of Characters"
        cast_idx = -1
        for i in range(da_combo.count()):
            if da_combo.itemText(i) == "Cast of Characters":
                cast_idx = i
                break
        assert cast_idx > 0
        da_combo.setCurrentIndex(cast_idx)
        assert get_da_gallery_from_widget(da_combo) == "Cast of Characters"

        # 模拟同步到了包含新相册 "Fan works" 的列表
        new_galleries = [
            "Featured",
            "Fan works",
            "NSFW - Ai Training Clip",
            "Cast of Characters",
            "Film set"
        ]
        win.config["deviantart_known_galleries"] = new_galleries
        win._populate_da_gallery_combo(da_combo)

        # 验证 1: 刷新后之前选中的 "Cast of Characters" 依然保持选中状态
        assert get_da_gallery_from_widget(da_combo) == "Cast of Characters"

        # 验证 2: 新相册 "Fan works" 出现在下拉列表中并可被选中
        fan_idx = -1
        for i in range(da_combo.count()):
            if da_combo.itemText(i) == "Fan works":
                fan_idx = i
                break
        assert fan_idx > 0
        da_combo.setCurrentIndex(fan_idx)
        assert get_da_gallery_from_widget(da_combo) == "Fan works"

        # 验证 3: 选回默认相册时返回空字符串
        da_combo.setCurrentIndex(0)
        assert get_da_gallery_from_widget(da_combo) == ""

    def test_sync_da_galleries_success_flow(self, mock_main_window, qapp):
        """测试 6: 主界面点击刷新相册成功完整流程 (配置写入 + 下拉框刷新 + 弹窗提示)"""
        win = mock_main_window
        da_disp = win.platform_controls["deviantart"]["dispatcher"]
        remote_galleries = ["Featured", "Fan works", "Custom Gallery 2026"]

        da_disp.fetch_galleries = MagicMock(return_value=(True, remote_galleries, ""))

        with patch.object(QMessageBox, "information") as mock_info, \
             patch.object(QMessageBox, "critical") as mock_crit:
            # 触发同步
            win.sync_da_galleries()
            # 等待 worker 线程结束并处理 Qt 事件
            if hasattr(win, "_da_sync_worker"):
                win._da_sync_worker.wait(3000)
            qapp.processEvents()

            # 验证配置已更新
            assert win.config["deviantart_known_galleries"] == remote_galleries

            # 验证主界面相册下拉框包含 Custom Gallery 2026
            da_combo = win.platform_controls["deviantart"]["options"]["gallery"]
            combo_items = [da_combo.itemText(i) for i in range(da_combo.count())]
            assert "Custom Gallery 2026" in combo_items

            # 验证成功弹窗调用，未调用错误弹窗
            mock_info.assert_called_once()
            assert "成功拉取到 3 个相册" in mock_info.call_args[0][2]
            mock_crit.assert_not_called()

    def test_sync_da_galleries_failure_shows_critical_and_preserves_config(self, mock_main_window, qapp):
        """测试 7: 主界面刷新失败时必须触发 QMessageBox.critical 且不篡改原有配置"""
        win = mock_main_window
        da_disp = win.platform_controls["deviantart"]["dispatcher"]
        orig_galleries = list(win.config.get("deviantart_known_galleries", []))

        da_disp.fetch_galleries = MagicMock(return_value=(False, [], "DA 服务器 503 拒绝访问"))

        with patch.object(QMessageBox, "information") as mock_info, \
             patch.object(QMessageBox, "critical") as mock_crit:
            win.sync_da_galleries()
            if hasattr(win, "_da_sync_worker"):
                win._da_sync_worker.wait(3000)
            qapp.processEvents()

            # 验证错误弹窗被调用，且包含具体错误原因
            mock_crit.assert_called_once()
            assert "DA 服务器 503 拒绝访问" in mock_crit.call_args[0][2]
            mock_info.assert_not_called()

            # 验证原有配置完好无损，未被静默清空或修改
            assert win.config.get("deviantart_known_galleries") == orig_galleries

    def test_settings_dialog_sync_and_populate(self, mock_main_window, qapp):
        """测试 8: 设置对话框中的刷新相册列表按钮与下拉框联动测试"""
        win = mock_main_window
        da_disp = win.platform_controls["deviantart"]["dispatcher"]

        dlg = SettingsDialog(win)
        win._active_settings_dialog = dlg

        remote_galleries = ["Featured", "Fan works", "Gallery From Settings"]
        da_disp.fetch_galleries = MagicMock(return_value=(True, remote_galleries, ""))

        with patch.object(QMessageBox, "information") as mock_info:
            dlg._on_sync_da_galleries_clicked()
            if hasattr(win, "_da_sync_worker"):
                win._da_sync_worker.wait(3000)
            qapp.processEvents()

            # 验证设置弹窗内的下拉框已同步更新
            dlg_items = [dlg.def_da_gallery_combo.itemText(i) for i in range(dlg.def_da_gallery_combo.count())]
            assert "Gallery From Settings" in dlg_items
            mock_info.assert_called_once()
