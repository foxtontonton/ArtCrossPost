# -*- coding: utf-8 -*-
"""
Tests for P1-2: 发布历史与重复图片指纹预警 (Publish History & Image Fingerprint Warning)
"""
import sys
import json
from unittest.mock import MagicMock, patch
if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()
from pathlib import Path
from PIL import Image
import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread
from history_manager import PublishHistoryManager, compute_image_fingerprints
from history_dialog import PublishHistoryDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def dummy_image(tmp_path):
    img_path = tmp_path / "sample_art.png"
    im = Image.new("RGB", (64, 64), color="red")
    im.save(img_path)
    return img_path


@pytest.fixture
def mock_main_window(tmp_path, qapp, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    from app import MainWindow

    with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
        win = MainWindow()
        win.data_dir = tmp_path
        win.history_manager = PublishHistoryManager(win.data_dir)
        win.notify = MagicMock()

        yield win

        # Cleanup timers
        for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
            if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                getattr(win, t_attr).stop()
        if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
            win.thread.quit()
            win.thread.wait()


class TestPublishHistory:
    def test_fingerprint_generation(self, dummy_image):
        """验证能够正确计算 SHA-256 和 dHash 指纹"""
        fp = compute_image_fingerprints(dummy_image)
        assert "sha256" in fp
        assert len(fp["sha256"]) == 64
        assert "dhash" in fp
        assert len(fp["dhash"]) > 0

    def test_append_record_and_reverse_loading(self, tmp_path, dummy_image):
        """
        验证每次发布结束后追加一条记录到 data/publish_history.json:
        时间戳、图片指纹哈希、各平台发布成败状态、帖子直链，按倒序排列
        """
        mgr = PublishHistoryManager(tmp_path)

        rec1 = mgr.append_record(
            title="Artwork 1",
            image_paths=[str(dummy_image)],
            publish_results={
                "deviantart": (True, "https://www.deviantart.com/art/111", []),
                "x_twitter": (False, "Timed out", [])
            }
        )

        rec2 = mgr.append_record(
            title="Artwork 2",
            image_paths=[str(dummy_image)],
            publish_results={
                "deviantart": (True, "https://www.deviantart.com/art/222", []),
                "x_twitter": (True, "https://x.com/status/333", [])
            }
        )

        history = mgr.load_history()
        assert len(history) == 2
        # 倒序检查 (rec2 在前，rec1 在后)
        assert history[0]["title"] == "Artwork 2"
        assert history[1]["title"] == "Artwork 1"

        # 检查记录字段
        assert "timestamp" in history[0]
        assert len(history[0]["image_fingerprints"]) == 1
        assert history[0]["image_fingerprints"][0]["sha256"] != ""
        assert history[0]["platforms"]["deviantart"]["success"] is True
        assert history[0]["platforms"]["deviantart"]["url"] == "https://www.deviantart.com/art/222"
        assert history[0]["platforms"]["x_twitter"]["success"] is True

    def test_duplicate_image_warning_banner_on_import(self, mock_main_window, dummy_image):
        """
        验证导入图片时查历史：
        若命中已发布过的图片指纹，界面顶部黄色横幅提示：
        '该图片曾于 YYYY-MM-DD 发布至 [DA, X]，请确认是否重复发布'
        """
        win = mock_main_window

        # 预设一条历史记录
        custom_history = [{
            "id": "test_id",
            "timestamp": "2026-10-21 15:30:00",
            "title": "Old Masterpiece",
            "image_fingerprints": [compute_image_fingerprints(dummy_image)],
            "platforms": {
                "deviantart": {"success": True, "url": "https://deviantart.com/1", "message": "OK"},
                "x_twitter": {"success": True, "url": "https://x.com/1", "message": "OK"}
            }
        }]
        (win.data_dir / "publish_history.json").write_text(json.dumps(custom_history), encoding="utf-8")

        # 模拟导入该图片
        win.image_paths = [str(dummy_image)]
        win._check_image_duplicate_history()

        # 断言顶部黄色横幅显示并包含确切文本
        assert win.history_warning_banner.isVisible() is True
        banner_text = win.history_warning_banner_lbl.text()
        assert "该图片曾于 2026-10-21 发布至 [DA, X]，请确认是否重复发布" in banner_text

        # 清空图片后横幅自动隐藏
        win._clear_images()
        assert win.history_warning_banner.isVisible() is False

    def test_publish_completion_appends_to_history(self, mock_main_window, dummy_image):
        """验证发布完成回调时自动追加历史"""
        win = mock_main_window
        win.image_paths = [str(dummy_image)]
        win.title_input.setText("New Painting")

        results = {
            "deviantart": (True, "https://www.deviantart.com/new_art", []),
            "x_twitter": (True, "https://x.com/status/999", [])
        }

        win._on_completed(results)

        history = win.history_manager.load_history()
        assert len(history) >= 1
        assert history[0]["title"] == "New Painting"
        assert history[0]["platforms"]["deviantart"]["url"] == "https://www.deviantart.com/new_art"

    def test_history_dialog_table_ui(self, mock_main_window, dummy_image):
        """验证历史记录查看器弹窗能够正确展示倒序记录"""
        win = mock_main_window
        mgr = win.history_manager
        mgr.append_record("Art 1", [str(dummy_image)], {"deviantart": (True, "https://da.com/1", [])})
        mgr.append_record("Art 2", [str(dummy_image)], {"x_twitter": (True, "https://x.com/2", [])})

        dlg = PublishHistoryDialog(mgr, parent=win)
        assert dlg.table.rowCount() == 2
        # 第一行 (最新) 是 Art 2
        assert dlg.table.item(0, 1).text() == "Art 2"
        # 第二行是 Art 1
        assert dlg.table.item(1, 1).text() == "Art 1"
        dlg.close()

    def test_find_published_da_url_five_states(self, tmp_path, dummy_image):
        """
        验证 find_published_da_url 的回填五态:
        1. 带查询串/锚点/尾随斜杠 -> 清洗后规范帖子地址
        2. DA 失败 -> None
        3. 无命中 -> None
        4. 双命中 -> 取最新记录
        5. url 不合形 (非 /art/... 形状) -> None
        以及缺失/损坏安全容错 -> None
        """
        mgr = PublishHistoryManager(tmp_path)

        # 状态 3: 无命中 -> None (历史为空或无匹配)
        assert mgr.find_published_da_url([dummy_image]) is None

        # 状态 1: 带查询串/# 锚点/尾随斜杠 -> 清洗后规范帖子地址
        rec_query = {
            "id": "rec_query",
            "timestamp": "2026-10-25 10:00:00",
            "title": "Query Art",
            "image_fingerprints": [compute_image_fingerprints(dummy_image)],
            "platforms": {
                "deviantart": {
                    "success": True,
                    "url": "https://www.deviantart.com/artist/art/masterpiece-12345?param=value#section/",
                    "message": "OK"
                }
            }
        }
        (tmp_path / "publish_history.json").write_text(json.dumps([rec_query]), encoding="utf-8")
        assert mgr.find_published_da_url([dummy_image]) == "https://www.deviantart.com/artist/art/masterpiece-12345"

        # 状态 2: DA 失败 -> None
        rec_failed = {
            "id": "rec_failed",
            "timestamp": "2026-10-25 11:00:00",
            "title": "Failed Art",
            "image_fingerprints": [compute_image_fingerprints(dummy_image)],
            "platforms": {
                "deviantart": {
                    "success": False,
                    "url": "https://www.deviantart.com/artist/art/failed-12345",
                    "message": "Upload error"
                }
            }
        }
        (tmp_path / "publish_history.json").write_text(json.dumps([rec_failed]), encoding="utf-8")
        assert mgr.find_published_da_url([dummy_image]) is None

        # 状态 4: 双命中 -> 取最新记录 (publish_history.json 存储时按时间追加，load_history 倒序取最新在前)
        rec_old = {
            "id": "rec_old",
            "timestamp": "2026-10-20 08:00:00",
            "title": "Old Post",
            "image_fingerprints": [compute_image_fingerprints(dummy_image)],
            "platforms": {
                "deviantart": {
                    "success": True,
                    "url": "https://www.deviantart.com/artist/art/old-post-111",
                    "message": "OK"
                }
            }
        }
        rec_new = {
            "id": "rec_new",
            "timestamp": "2026-10-26 12:00:00",
            "title": "New Post",
            "image_fingerprints": [compute_image_fingerprints(dummy_image)],
            "platforms": {
                "deviantart": {
                    "success": True,
                    "url": "https://www.deviantart.com/artist/art/new-post-222",
                    "message": "OK"
                }
            }
        }
        (tmp_path / "publish_history.json").write_text(json.dumps([rec_old, rec_new]), encoding="utf-8")
        assert mgr.find_published_da_url([dummy_image]) == "https://www.deviantart.com/artist/art/new-post-222"

        # 状态 5: url 不合形 -> None (例如缺少 /art/ 的主页链接)
        rec_bad_shape = {
            "id": "rec_bad_shape",
            "timestamp": "2026-10-27 12:00:00",
            "title": "Bad Shape Art",
            "image_fingerprints": [compute_image_fingerprints(dummy_image)],
            "platforms": {
                "deviantart": {
                    "success": True,
                    "url": "https://www.deviantart.com/artist",
                    "message": "OK"
                }
            }
        }
        (tmp_path / "publish_history.json").write_text(json.dumps([rec_bad_shape]), encoding="utf-8")
        assert mgr.find_published_da_url([dummy_image]) is None

        # 容错测试: 历史文件损坏 -> 返回 None，严禁抛错
        (tmp_path / "publish_history.json").write_text("CORRUPTED JSON! {[[", encoding="utf-8")
        assert mgr.find_published_da_url([dummy_image]) is None

    def test_payload_backfill_conditions(self, mock_main_window, dummy_image):
        """
        验证 payload 回填条件两态:
        1. 未勾 DA 且历史命中 -> 四字段正确 (da_post_url, x_da_link, bluesky_da_link, platform_options 同名字段),
           且 payload["da_link_source"] == "history"
        2. 勾选 DA -> 不触碰历史回填逻辑 (find_published_da_url 不被调用)
        """
        win = mock_main_window
        target_da_post = "https://www.deviantart.com/artist/art/sample-piece-999"

        # 关闭打码与水印，纯测 payload 回填与分支
        win.auto_censor_cb.setChecked(False)
        win.watermark_enabled_cb.setChecked(False)

        # 写入一条命中记录
        history_rec = [{
            "id": "rec_target",
            "timestamp": "2026-10-28 10:00:00",
            "title": "Sample Masterpiece",
            "image_fingerprints": [compute_image_fingerprints(dummy_image)],
            "platforms": {
                "deviantart": {
                    "success": True,
                    "url": target_da_post,
                    "message": "OK"
                }
            }
        }]
        (win.data_dir / "publish_history.json").write_text(json.dumps(history_rec), encoding="utf-8")

        win.image_paths = [str(dummy_image)]
        win.title_input.setText("Condition Test")

        # 1. 未勾选 DA + 命中: 四字段均设为目标 DA 直链，置 da_link_source = "history"
        win.platform_controls["deviantart"]["checkbox"].setChecked(False)
        win.platform_controls["patreon"]["checkbox"].setChecked(True)

        with patch("app.PublishThread") as mock_thread_cls:
            mock_thread_inst = MagicMock()
            mock_thread_cls.return_value = mock_thread_inst

            win._start_publishing()

            assert win._last_publish_payload is not None
            p = win._last_publish_payload
            assert p.get("da_post_url") == target_da_post
            assert p.get("x_da_link") == target_da_post
            assert p.get("bluesky_da_link") == target_da_post
            assert p.get("platform_options", {}).get("da_post_url") == target_da_post
            assert p.get("platform_options", {}).get("x_da_link") == target_da_post
            assert p.get("platform_options", {}).get("bluesky_da_link") == target_da_post
            assert p.get("da_link_source") == "history"

        # 2. 勾选 DA: 现有两阶段逻辑不变，不调用 find_published_da_url，da_link_source 不为 history
        win.platform_controls["deviantart"]["checkbox"].setChecked(True)

        with patch.object(win.history_manager, "find_published_da_url", wraps=win.history_manager.find_published_da_url) as spy_find, \
             patch("app.PublishThread") as mock_thread_cls:
            mock_thread_inst = MagicMock()
            mock_thread_cls.return_value = mock_thread_inst

            win._start_publishing()

            spy_find.assert_not_called()
            p2 = win._last_publish_payload
            assert p2.get("da_link_source") != "history"

    def test_publish_thread_and_panel_history_traffic_info(self, tmp_path, qapp, monkeypatch):
        """
        验证 PublishThread 非两阶段路径下 da_link_source == 'history' 时:
        - 结果 _traffic_info 包含 post url、notice='取自发布历史'、is_post_link=True
        - 结果面板渲染出包含该帖直链及 (取自发布历史) 标识
        """
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
        from app import PublishThread, MainWindow
        from dispatchers.base import BaseDispatcher

        class SimpleDispatcher(BaseDispatcher):
            def __init__(self, name, display_name, cookies_dir):
                super().__init__(name=name, display_name=display_name, login_url="http://test", cookies_dir=cookies_dir)
            def check_login(self): return True
            def login_interactive(self): return True, "ok"
            def probe_login(self): return {"status": "ok"}
            def publish(self, payload): return True, "ok", []

        disp = SimpleDispatcher(name="patreon", display_name="Patreon", cookies_dir=tmp_path)
        dummy_file = tmp_path / "art.png"
        dummy_file.write_bytes(b"fake image bytes")
        hist_url = "https://www.deviantart.com/artist/art/history-post-777"
        payload = {
            "title": "History Test",
            "da_post_url": hist_url,
            "x_da_link": hist_url,
            "da_link_source": "history",
            "images": [str(dummy_file)],
        }

        thread = PublishThread([disp], payload)
        res_captured = {}
        thread.completed_signal.connect(lambda r: res_captured.update(r))
        thread.run()

        traffic_info = res_captured.get("_traffic_info")
        assert traffic_info is not None
        assert traffic_info.get("link") == hist_url
        assert traffic_info.get("notice") == "取自发布历史"
        assert traffic_info.get("is_post_link") is True

        # 面板渲染验证
        with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
            win = MainWindow()
            win.data_dir = tmp_path
            win._update_publish_results_panel(res_captured)

            found_text = False
            for i in range(win.publish_results_layout.count()):
                w = win.publish_results_layout.itemAt(i).widget()
                if w:
                    for child in w.children():
                        txt = getattr(child, "text", lambda: "")()
                        if "history-post-777" in txt and "取自发布历史" in txt:
                            found_text = True
                            break
            assert found_text is True

            for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
                if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                    getattr(win, t_attr).stop()
            if hasattr(win, "thread") and isinstance(win.thread, QThread) and win.thread.isRunning():
                win.thread.quit()


