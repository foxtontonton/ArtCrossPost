import sys
from unittest.mock import MagicMock
if "onnxruntime" not in sys.modules:
    sys.modules["onnxruntime"] = MagicMock()
# -*- coding: utf-8 -*-
"""
Tests for Scheduled Publishing (定时发布 v1)
Covering:
- 快照序列化 / 反序列化
- 过期检测 (启动扫描)
- QTimer / 轮询触发判定 (mock 时间)
- 多个任务到期时的创建时间排队顺序
- 状态流转 (pending -> running -> completed / failed / skipped / cancelled)
- 重新定时 (reschedule)
- MainWindow 快照捕获、并发冲突防互斥与发布完成生命周期
- ScheduledPublishDialog 与 ExpiredTasksDialog 交互测试
"""
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image

from schedule_manager import ScheduledTask, ScheduleManager, ISO_FORMAT
from schedule_dialogs import ScheduledPublishDialog, ExpiredTasksDialog
from app import MainWindow


@pytest.fixture(scope="session")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


class TestScheduledTaskModel:
    """测试 ScheduledTask 数据模型与快照序列化"""

    def test_serialization_and_deserialization(self):
        snapshot_data = {
            "title": "Cosmic Voyage",
            "description": "Illustration of a deep space ship.",
            "tags": ["scifi", "spaceship", "conceptart"],
            "image_paths": ["/path/to/img1.png", "/path/to/img2.png"],
            "selected_platforms": ["x_twitter", "deviantart"],
            "platform_options": {
                "x_sensitive": True,
                "deviantart_mature": "strict"
            },
            "is_nsfw": True,
            "is_ai": False
        }
        target_time = "2026-10-24 15:30:00"

        task = ScheduledTask(target_time=target_time, snapshot=snapshot_data)
        d = task.to_dict()

        assert d["id"] == task.id
        assert d["target_time"] == target_time
        assert d["status"] == "pending"
        assert d["snapshot"]["title"] == "Cosmic Voyage"
        assert len(d["snapshot"]["image_paths"]) == 2

        # 反序列化
        restored = ScheduledTask.from_dict(d)
        assert restored.id == task.id
        assert restored.target_time == target_time
        assert restored.status == "pending"
        assert restored.snapshot["tags"] == ["scifi", "spaceship", "conceptart"]

    def test_expiration_detection(self):
        past_time = "2026-10-20 10:00:00"
        future_time = "2026-10-25 10:00:00"
        now = datetime(2026, 10, 23, 12, 0, 0)

        task_past = ScheduledTask(target_time=past_time, snapshot={"title": "Old"})
        task_future = ScheduledTask(target_time=future_time, snapshot={"title": "New"})

        assert task_past.is_expired(now=now) is True
        assert task_past.is_ready(now=now) is True

        assert task_future.is_expired(now=now) is False
        assert task_future.is_ready(now=now) is False

    def test_non_pending_tasks_are_not_expired_or_ready(self):
        past_time = "2026-10-20 10:00:00"
        now = datetime(2026, 10, 23, 12, 0, 0)

        for non_pending in ["completed", "failed", "skipped", "cancelled", "running"]:
            task = ScheduledTask(target_time=past_time, snapshot={}, status=non_pending)
            assert task.is_expired(now=now) is False
            assert task.is_ready(now=now) is False


class TestScheduleManager:
    """测试 ScheduleManager 任务生命周期与排队控制"""

    def test_add_and_persistence(self, tmp_path):
        json_file = tmp_path / "scheduled_posts.json"
        mgr = ScheduleManager(storage_path=json_file)

        task = mgr.add_task(
            target_time="2026-10-24 18:00:00",
            snapshot={"title": "Cyberpunk Girl", "image_paths": ["/a.png"]}
        )

        assert json_file.exists()
        assert len(mgr.tasks) == 1
        assert mgr.tasks[0].id == task.id

        # 重新加载验证持久化
        new_mgr = ScheduleManager(storage_path=json_file)
        assert len(new_mgr.tasks) == 1
        assert new_mgr.tasks[0].snapshot["title"] == "Cyberpunk Girl"

    def test_check_expired_tasks_on_startup(self, tmp_path):
        json_file = tmp_path / "scheduled_posts.json"
        mgr = ScheduleManager(storage_path=json_file)

        now = datetime(2026, 10, 23, 12, 0, 0)

        t_expired = mgr.add_task("2026-10-22 08:00:00", {"title": "Expired Post"})
        t_valid = mgr.add_task("2026-10-24 08:00:00", {"title": "Future Post"})

        expired_list = mgr.check_expired_tasks(now=now)
        assert len(expired_list) == 1
        assert expired_list[0].id == t_expired.id

    def test_queue_ordering_by_created_at_when_multiple_ready(self, tmp_path):
        """
        规则 6: 多个任务同时到点 -> 按创建时间顺序 (created_at 升序) 执行
        """
        json_file = tmp_path / "scheduled_posts.json"
        mgr = ScheduleManager(storage_path=json_file)

        now = datetime(2026, 10, 23, 12, 0, 0)

        # 构造两个已到期的任务，一个创建于 10:00，一个创建于 11:00
        t1 = ScheduledTask(
            target_time="2026-10-23 11:30:00",
            snapshot={"title": "Task 1 (Created First)"},
            created_at="2026-10-23 10:00:00"
        )
        t2 = ScheduledTask(
            target_time="2026-10-23 11:30:00",
            snapshot={"title": "Task 2 (Created Second)"},
            created_at="2026-10-23 11:00:00"
        )

        mgr.tasks = [t2, t1]  # 乱序放入
        mgr.save_tasks()

        # 首次获取应为创建时间最早的 t1
        first_ready = mgr.get_next_ready_task(now=now)
        assert first_ready is not None
        assert first_ready.id == t1.id

        # 模拟 t1 标记为 running
        mgr.mark_running(t1.id)

        # 再次获取下一个 ready 任务应为 t2
        second_ready = mgr.get_next_ready_task(now=now)
        assert second_ready is not None
        assert second_ready.id == t2.id

    def test_cancel_task(self, tmp_path):
        json_file = tmp_path / "scheduled_posts.json"
        mgr = ScheduleManager(storage_path=json_file)

        task = mgr.add_task("2026-10-24 10:00:00", {"title": "To Cancel"})
        assert mgr.cancel_task(task.id) is True

        assert task.status == "cancelled"
        assert task.result_detail == "用户手动取消"
        assert len(mgr.get_pending_tasks()) == 0

    def test_mark_completed_and_failed(self, tmp_path):
        json_file = tmp_path / "scheduled_posts.json"
        mgr = ScheduleManager(storage_path=json_file)

        t_ok = mgr.add_task("2026-10-24 10:00:00", {"title": "Will Pass"})
        t_fail = mgr.add_task("2026-10-24 10:00:00", {"title": "Will Fail"})

        # 完成标记
        mgr.mark_completed(t_ok.id, detail="所有平台发布成功")
        assert t_ok.status == "completed"
        assert t_ok.result_detail == "所有平台发布成功"

        # 失败标记 (规则 7: 标 failed + 记录详细日志，不自动重试)
        mgr.mark_failed(t_fail.id, error_msg="X (Twitter) 未登录凭据过期")
        assert t_fail.status == "failed"
        assert "未登录" in t_fail.result_detail

    def test_reschedule_task(self, tmp_path):
        json_file = tmp_path / "scheduled_posts.json"
        mgr = ScheduleManager(storage_path=json_file)

        now = datetime(2026, 10, 23, 12, 0, 0)
        task = mgr.add_task("2026-10-23 09:00:00", {"title": "Expired"})
        assert task.is_expired(now) is True

        # 重新定时到明天
        new_time = "2026-10-24 09:00:00"
        mgr.reschedule_task(task.id, new_time)

        assert task.target_time == new_time
        assert task.status == "pending"
        assert task.is_expired(now) is False


class TestMainWindowScheduleIntegration:
    """测试 MainWindow 与定时发布机制的集成交互"""

    @pytest.fixture
    def window(self, tmp_path, qapp, monkeypatch):
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
        img_p = tmp_path / "art.jpg"
        im = Image.new("RGB", (200, 200), color=(100, 150, 200))
        im.save(img_p, "JPEG")

        with patch("app.CensorEngine"), patch("app.WatermarkEngine"):
            win = MainWindow()
            win.notify = MagicMock()
            win.image_paths = [str(img_p)]
            win.data_dir = tmp_path
            win.schedule_manager = ScheduleManager(storage_path=tmp_path / "scheduled_posts.json")
            if hasattr(win, "_schedule_poll_timer"):
                win._schedule_poll_timer.stop()
            yield win
            for t_attr in ("_draft_save_timer", "_layout_save_timer", "_config_save_timer", "_preview_timer", "x_counter_timer", "_schedule_poll_timer"):
                if hasattr(win, t_attr) and getattr(win, t_attr).isActive():
                    getattr(win, t_attr).stop()

    def test_capture_current_post_snapshot(self, window):
        window.title_input.setText("Anime Goddess")
        window.desc_input.setPlainText("Detailed illustration.")
        window.tags_input.setText("anime, goddess, fantasy")
        if "x_twitter" in window.platform_controls:
            window.platform_controls["x_twitter"]["checkbox"].setChecked(True)

        snapshot = window._capture_current_post_snapshot()
        assert snapshot is not None
        assert snapshot["title"] == "Anime Goddess"
        assert snapshot["description"] == "Detailed illustration."
        assert "anime" in snapshot["tags"]
        assert len(snapshot["image_paths"]) == 1
        assert "x_twitter" in snapshot["selected_platforms"]

    def test_poll_scheduled_tasks_triggers_ready_task(self, window):
        task = window.schedule_manager.add_task(
            target_time="2026-10-20 10:00:00",
            snapshot={"title": "Scheduled Art", "image_paths": window.image_paths}
        )

        with patch.object(window, "_execute_scheduled_task") as mock_exec:
            window._poll_scheduled_tasks()
            mock_exec.assert_called_once()
            called_task = mock_exec.call_args[0][0]
            assert called_task.id == task.id

    def test_concurrency_busy_wait_prevents_overlapping_execution(self, window):
        task = window.schedule_manager.add_task(
            target_time="2026-10-20 10:00:00",
            snapshot={"title": "Scheduled Art", "image_paths": window.image_paths}
        )

        # 模拟当前已有发布在执行中
        window._is_scheduled_task_running = True

        with patch.object(window, "_execute_scheduled_task") as mock_exec:
            window._poll_scheduled_tasks()
            # 必须等待当前发布结束，不得重叠并发触发
            mock_exec.assert_not_called()

    def test_completed_callback_marks_task_status(self, window):
        task = window.schedule_manager.add_task(
            target_time="2026-10-20 10:00:00",
            snapshot={"title": "Scheduled Art", "image_paths": window.image_paths}
        )
        window._current_scheduled_task_id = task.id
        window._is_scheduled_task_running = True

        # 模拟发布成功
        window._on_completed({"x_twitter": (True, "Posted", [])})

        assert task.status == "completed"
        assert window._is_scheduled_task_running is False
        assert window._current_scheduled_task_id is None

    def test_failed_callback_marks_task_failed(self, window):
        task = window.schedule_manager.add_task(
            target_time="2026-10-20 10:00:00",
            snapshot={"title": "Scheduled Art", "image_paths": window.image_paths}
        )
        window._current_scheduled_task_id = task.id
        window._is_scheduled_task_running = True

        # 模拟发布失败
        window._on_completed({"x_twitter": (False, "Login Expired", [])})

        assert task.status == "failed"
        assert window._is_scheduled_task_running is False
        assert window._current_scheduled_task_id is None

    def test_scheduled_publish_dialog_ui(self, window):
        window.schedule_manager.add_task(
            target_time="2026-10-25 18:00:00",
            snapshot={"title": "Future Art", "selected_platforms": ["x_twitter"]}
        )
        dlg = ScheduledPublishDialog(window, window.schedule_manager)
        assert dlg.table.rowCount() == 1
        assert "Future Art" in dlg.table.item(0, 2).text()
        assert "x_twitter" in dlg.table.item(0, 3).text()

    def test_expired_tasks_dialog_ui(self, window):
        task = window.schedule_manager.add_task(
            target_time="2026-10-20 08:00:00",
            snapshot={"title": "Old Expired Art"}
        )
        dlg = ExpiredTasksDialog(window, [task], window.schedule_manager)
        assert dlg.table.rowCount() == 1

        # 测试点击"跳过"按钮
        dlg._action_skip(task.id, 0)
        assert task.status == "skipped"
