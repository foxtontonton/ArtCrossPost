# -*- coding: utf-8 -*-
"""
ArtCrossPost - Unified Logging System
- 写入控制台 (stdout) 与每日滚动日志文件 data/logs/artcrosspost_YYYY-MM-DD.log
- 自动清理超过 7 天的历史日志
- 消除裸 except / print，提供规范的分级日志 (DEBUG / INFO / WARNING / ERROR)
"""
import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime

# 基础路径配置
BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "data" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

def _cleanup_expired_logs(logs_dir: Path, keep_days: int = 7):
    """清理保存超过 keep_days 天的旧日志文件"""
    try:
        now = time.time()
        cutoff = now - (keep_days * 86400)
        for log_file in logs_dir.glob("artcrosspost_*.log"):
            if log_file.is_file():
                try:
                    if log_file.stat().st_mtime < cutoff:
                        log_file.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass

def get_current_log_file() -> Path:
    """获取当天的日志文件绝对路径"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    return LOGS_DIR / f"artcrosspost_{today_str}.log"

class DailyFileHandler(logging.FileHandler):
    """自动按天切换目标文件并维持写入的 FileHandler"""
    def __init__(self, logs_dir: Path, encoding="utf-8"):
        self.logs_dir = logs_dir
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        log_path = self.logs_dir / f"artcrosspost_{self.current_date}.log"
        super().__init__(str(log_path), encoding=encoding)

    def emit(self, record):
        now_date = datetime.now().strftime("%Y-%m-%d")
        if now_date != self.current_date:
            self.current_date = now_date
            self.close()
            new_path = self.logs_dir / f"artcrosspost_{self.current_date}.log"
            self.baseFilename = os.path.abspath(str(new_path))
            self.stream = self._open()
            _cleanup_expired_logs(self.logs_dir, keep_days=7)
        super().emit(record)

_ROOT_LOGGER_INITIALIZED = False

def init_root_logger():
    global _ROOT_LOGGER_INITIALIZED
    if _ROOT_LOGGER_INITIALIZED:
        return

    _cleanup_expired_logs(LOGS_DIR, keep_days=7)

    root_logger = logging.getLogger("ArtCrossPost")
    root_logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # 1. 控制台输出处理器 (INFO 及以上)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 2. 每日滚动文件处理器 (DEBUG 及以上)
    file_handler = DailyFileHandler(LOGS_DIR, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    _ROOT_LOGGER_INITIALIZED = True

def get_logger(name: str = "") -> logging.Logger:
    """获取指定模块命名的子 Logger"""
    init_root_logger()
    if not name or name == "ArtCrossPost":
        return logging.getLogger("ArtCrossPost")
    return logging.getLogger(f"ArtCrossPost.{name}")

# 默认主 logger 实例
logger = get_logger()
