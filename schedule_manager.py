# -*- coding: utf-8 -*-
"""
ArtCrossPost - Schedule Manager (定时发布管理模块 v1)
2026-10-17 / 2026-10-23 规范:
1. 触发方式: 应用常驻 + QTimer 轮询 (v1 规范)
2. 存储: 快照持久化到 data/scheduled_posts.json，重启后恢复队列
3. 快照内容: 图片路径及顺序、标题、描述、标签、已选平台、平台选项、打码/水印参数
4. 过期任务: 启动检测，提供 立即发布 / 跳过 / 重新定时
5. 并发冲突: 队列等待，多个到点按创建时间升序执行
6. 失败处理: 失败标记 failed + 记录详细日志，不自动重试
"""
import os
import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

try:
    from logger import get_logger
    logger = get_logger("ScheduleManager")
except ImportError:
    import logging
    logger = logging.getLogger("ScheduleManager")


DEFAULT_SCHEDULED_POSTS_FILE = Path("data") / "scheduled_posts.json"
ISO_FORMAT = "%Y-%m-%d %H:%M:%S"


class ScheduledTask:
    """定时发布任务实体"""

    def __init__(
        self,
        target_time: str,
        snapshot: Dict[str, Any],
        task_id: Optional[str] = None,
        created_at: Optional[str] = None,
        status: str = "pending",
        result_detail: str = ""
    ):
        self.id = task_id or f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.created_at = created_at or datetime.now().strftime(ISO_FORMAT)
        self.target_time = target_time  # YYYY-MM-DD HH:MM:SS
        self.status = status  # pending, running, completed, failed, skipped, cancelled
        self.result_detail = result_detail
        self.snapshot = snapshot or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "target_time": self.target_time,
            "status": self.status,
            "result_detail": self.result_detail,
            "snapshot": self.snapshot
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduledTask":
        return cls(
            task_id=data.get("id"),
            created_at=data.get("created_at"),
            target_time=data.get("target_time", ""),
            status=data.get("status", "pending"),
            result_detail=data.get("result_detail", ""),
            snapshot=data.get("snapshot", {})
        )

    def parse_target_datetime(self) -> Optional[datetime]:
        try:
            return datetime.strptime(self.target_time, ISO_FORMAT)
        except Exception:
            try:
                # 兼容 ISO 带 T 格式
                return datetime.fromisoformat(self.target_time)
            except Exception:
                return None

    def parse_created_datetime(self) -> Optional[datetime]:
        try:
            return datetime.strptime(self.created_at, ISO_FORMAT)
        except Exception:
            try:
                return datetime.fromisoformat(self.created_at)
            except Exception:
                return None

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """检查任务是否已过时 (status 为 pending 且 target_time < now)"""
        if self.status != "pending":
            return False
        dt = self.parse_target_datetime()
        if not dt:
            return False
        cur_time = now or datetime.now()
        return dt < cur_time

    def is_ready(self, now: Optional[datetime] = None) -> bool:
        """检查任务是否到期应触发 (status 为 pending 且 target_time <= now)"""
        if self.status != "pending":
            return False
        dt = self.parse_target_datetime()
        if not dt:
            return False
        cur_time = now or datetime.now()
        return dt <= cur_time


class ScheduleManager:
    """定时任务队列与持久化管理器"""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = Path(storage_path) if storage_path else DEFAULT_SCHEDULED_POSTS_FILE
        self.tasks: List[ScheduledTask] = []
        self.load_tasks()

    def load_tasks(self) -> List[ScheduledTask]:
        """从 JSON 文件加载持久化任务"""
        self.tasks = []
        if not self.storage_path.exists():
            return self.tasks

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
                if isinstance(raw_list, list):
                    for item in raw_list:
                        if isinstance(item, dict):
                            self.tasks.append(ScheduledTask.from_dict(item))
            logger.info(f"[ScheduleManager] 成功加载 {len(self.tasks)} 项定时发布任务")
        except Exception as e:
            logger.error(f"[ScheduleManager] 读取定时任务文件失败: {e}")

        return self.tasks

    def save_tasks(self) -> bool:
        """保存任务列表至 JSON 文件 (带目录自建与安全写入)"""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = [t.to_dict() for t in self.tasks]
            temp_file = self.storage_path.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            temp_file.replace(self.storage_path)
            return True
        except Exception as e:
            logger.error(f"[ScheduleManager] 保存定时任务失败: {e}")
            return False

    def add_task(self, target_time: str, snapshot: Dict[str, Any]) -> ScheduledTask:
        """创建并追加一个新定时任务"""
        task = ScheduledTask(target_time=target_time, snapshot=snapshot)
        self.tasks.append(task)
        self.save_tasks()
        logger.info(f"[ScheduleManager] 已添加定时任务 {task.id} (计划时间: {target_time})")
        return task

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def cancel_task(self, task_id: str) -> bool:
        """取消 pending 任务"""
        task = self.get_task(task_id)
        if task and task.status == "pending":
            task.status = "cancelled"
            task.result_detail = "用户手动取消"
            self.save_tasks()
            logger.info(f"[ScheduleManager] 任务 {task_id} 已取消")
            return True
        return False

    def get_pending_tasks(self) -> List[ScheduledTask]:
        """获取所有待执行任务"""
        return [t for t in self.tasks if t.status == "pending"]

    def check_expired_tasks(self, now: Optional[datetime] = None) -> List[ScheduledTask]:
        """检测已过时的 pending 任务 (供软件启动时弹窗处理)"""
        cur_time = now or datetime.now()
        return [t for t in self.tasks if t.is_expired(cur_time)]

    def get_next_ready_task(self, now: Optional[datetime] = None) -> Optional[ScheduledTask]:
        """
        获取当前到期应执行的下一个任务:
        规则 6: 多个任务同时到点 -> 按创建时间顺序 (created_at 升序) 执行
        """
        cur_time = now or datetime.now()
        ready_tasks = [t for t in self.tasks if t.is_ready(cur_time)]
        if not ready_tasks:
            return None

        def sort_key(task: ScheduledTask):
            cdt = task.parse_created_datetime()
            return cdt or datetime.min

        ready_tasks.sort(key=sort_key)
        return ready_tasks[0]

    def mark_running(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task:
            task.status = "running"
            self.save_tasks()
            return True
        return False

    def mark_completed(self, task_id: str, detail: str = "") -> bool:
        task = self.get_task(task_id)
        if task:
            task.status = "completed"
            task.result_detail = detail
            self.save_tasks()
            logger.info(f"[ScheduleManager] 任务 {task_id} 执行成功完成")
            return True
        return False

    def mark_failed(self, task_id: str, error_msg: str) -> bool:
        """规则 7: 发布失败 -> 任务标 failed + 记日志，不自动重试"""
        task = self.get_task(task_id)
        if task:
            task.status = "failed"
            task.result_detail = error_msg
            self.save_tasks()
            logger.warning(f"[ScheduleManager] 任务 {task_id} 发布失败: {error_msg}")
            return True
        return False

    def mark_skipped(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task:
            task.status = "skipped"
            task.result_detail = "启动时用户选择跳过执行"
            self.save_tasks()
            return True
        return False

    def reschedule_task(self, task_id: str, new_target_time: str) -> bool:
        """重新设置执行时间并恢复为 pending"""
        task = self.get_task(task_id)
        if task:
            task.target_time = new_target_time
            task.status = "pending"
            task.result_detail = "重新定时"
            self.save_tasks()
            logger.info(f"[ScheduleManager] 任务 {task_id} 已重新定时至 {new_target_time}")
            return True
        return False
