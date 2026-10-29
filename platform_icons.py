# -*- coding: utf-8 -*-
"""
ArtCrossPost - 平台官方图标与个人主页交互模块 (platform_icons)
- 统一图标加载 helper: platform_icon(platform_key) -> QIcon
- 图标按钮控件: ClickablePlatformIcon(QToolButton)
- 个人主页映射与浏览器唤起: open_platform_profile(platform_key, parent_window=None)
"""
import logging
from pathlib import Path
from typing import Optional, Set

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QToolButton

logger = logging.getLogger("ArtCrossPost.PlatformIcons")

# 平台官方 favicon 文件名固定映射 (放入 assets/icons/)
ICON_FILENAME_MAP = {
    "x": "x.png",
    "x_twitter": "x.png",
    "twitter": "x.png",
    "deviantart": "deviantart.png",
    "da": "deviantart.png",
    "patreon": "patreon.png",
    "pat": "patreon.png",
    "civitai": "civitai.png",
    "civit": "civitai.png",
    "kofi": "kofi.png",
    "ko-fi": "kofi.png",
}

# 个人主页默认映射字典 (已核验，供 config 及回退使用)
DEFAULT_PLATFORM_PROFILE_URLS = {
    "x": "https://x.com/FOXTONTONTON",
    "deviantart": "https://www.deviantart.com/foxtontonton",
    "patreon": "https://patreon.com/foxtontonton",
    "civitai": "https://civitai.red/user/FOXTONTONTON",
    "kofi": "https://ko-fi.com/foxtontonton",
}

# 记录缺失告警的集合，满足“日志记一次即可”的要求
_LOGGED_MISSING_KEYS: Set[str] = set()


def get_platform_clean_key(platform_key: str) -> str:
    """标准化平台 key，将 x_twitter -> x, ko-fi -> kofi 等"""
    if not platform_key:
        return ""
    k = str(platform_key).strip().lower()
    if k in ("x", "x_twitter", "twitter"):
        return "x"
    if k in ("deviantart", "da"):
        return "deviantart"
    if k in ("patreon", "pat"):
        return "patreon"
    if k in ("civitai", "civit"):
        return "civitai"
    if k in ("kofi", "ko-fi"):
        return "kofi"
    return k


def get_platform_display_name(platform_key: str, fallback: Optional[str] = None) -> str:
    """获取平台的简要中文展示名称，用于 Tooltip 呈现"""
    clean_k = get_platform_clean_key(platform_key)
    names = {
        "x": "X",
        "deviantart": "DeviantArt",
        "patreon": "Patreon",
        "civitai": "Civitai",
        "kofi": "Ko-fi",
    }
    return names.get(clean_k, fallback or str(platform_key))


def get_platform_profile_url(platform_key: str, config: Optional[dict] = None) -> str:
    """获取指定平台的个人主页 URL，优先从 config['platform_profile_urls'] 读取，回退至默认映射"""
    clean_k = get_platform_clean_key(platform_key)
    if not config:
        try:
            from config import load_config
            config = load_config()
        except Exception:
            config = {}
    urls = config.get("platform_profile_urls", {}) if isinstance(config, dict) else {}
    if not isinstance(urls, dict):
        urls = {}
    return urls.get(clean_k) or urls.get(platform_key) or DEFAULT_PLATFORM_PROFILE_URLS.get(clean_k, "")


def platform_icon(platform_key: str) -> QIcon:
    """
    统一 Helper: platform_icon(platform_key) -> QIcon
    从 assets/icons/ 按 key 加载对应官方 favicon 图标。
    - 对已知 key (x, deviantart, patreon, civitai, kofi 及别名) 返回非空 QIcon
    - 图标文件缺失或损坏时返回空 QIcon，日志记一次，绝不报错或崩溃
    - 对未知 key 返回空 QIcon，不抛异常
    """
    if not platform_key:
        return QIcon()

    raw_k = str(platform_key).strip().lower()
    filename = ICON_FILENAME_MAP.get(raw_k)
    if not filename:
        return QIcon()

    icons_dir = Path(__file__).resolve().parent / "assets" / "icons"
    icon_path = icons_dir / filename

    if not icon_path.is_file():
        if raw_k not in _LOGGED_MISSING_KEYS:
            _LOGGED_MISSING_KEYS.add(raw_k)
            logger.warning(f"[PlatformIcon] 未找到平台图标文件: {icon_path}")
        return QIcon()

    try:
        icon = QIcon(str(icon_path))
        if icon.isNull():
            if raw_k not in _LOGGED_MISSING_KEYS:
                _LOGGED_MISSING_KEYS.add(raw_k)
                logger.warning(f"[PlatformIcon] 平台图标文件损坏或无法解码: {icon_path}")
            return QIcon()
        return icon
    except Exception as e:
        if raw_k not in _LOGGED_MISSING_KEYS:
            _LOGGED_MISSING_KEYS.add(raw_k)
            logger.warning(f"[PlatformIcon] 加载平台图标异常 ({platform_key}): {e}")
        return QIcon()


def open_platform_profile(platform_key: str, parent_window=None) -> bool:
    """
    使用系统默认浏览器打开我在该平台的主页。
    - 打开失败 (无默认浏览器等) 时状态栏/日志提示，不弹窗不崩溃
    """
    import webbrowser

    cfg = parent_window.config if (parent_window and hasattr(parent_window, "config")) else None
    url = get_platform_profile_url(platform_key, cfg)
    if not url:
        msg = f"未找到平台 [{platform_key}] 的个人主页链接"
        logger.warning(f"[PlatformIcon] {msg}")
        if parent_window and hasattr(parent_window, "notify"):
            parent_window.notify(f"⚠️ {msg}", level="warning")
        elif parent_window and hasattr(parent_window, "statusBar"):
            parent_window.statusBar().showMessage(msg, 3000)
        return False

    try:
        opened = webbrowser.open(url)
        if not opened:
            # 部分 Windows 环境 webbrowser.open 返回 False 但实际启动，尝试 QDesktopServices 兜底
            from PyQt5.QtGui import QDesktopServices
            from PyQt5.QtCore import QUrl
            opened = QDesktopServices.openUrl(QUrl(url))
        logger.info(f"[PlatformIcon] 打开个人主页: {url}")
        return True
    except Exception as e:
        err_msg = f"无法调起默认浏览器打开主页 ({url}): {e}"
        logger.error(f"[PlatformIcon] {err_msg}")
        if parent_window and hasattr(parent_window, "notify"):
            parent_window.notify(f"⚠️ {err_msg}", level="warning")
        elif parent_window and hasattr(parent_window, "statusBar"):
            parent_window.statusBar().showMessage(err_msg, 3000)
        return False


class ClickablePlatformIcon(QToolButton):
    """
    可点击的独立平台官方图标控件:
    - 尺寸约 18px (或 16px)，平滑缩放
    - 悬停时光标变手型 (PointingHandCursor)
    - Tooltip 显示 '打开我的 XX 主页'
    - 点击用系统默认浏览器打开主页，独立事件，绝不触发旁侧复选框勾选变化
    - 图标文件缺失或损坏时只显示文字，自身自动隐藏，不报错不崩溃
    """
    def __init__(self, platform_key: str, size: int = 18, parent_window=None, parent=None):
        super().__init__(parent)
        self.platform_key = platform_key
        self.parent_window = parent_window
        self.icon_size_px = size

        icon = platform_icon(platform_key)
        if icon.isNull():
            self.hide()
            self.setVisible(False)
            return

        disp_name = get_platform_display_name(platform_key)
        self.setIcon(icon)
        self.setIconSize(QSize(size, size))
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"打开我的 {disp_name} 主页")
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 0.12);
                border-radius: 3px;
            }
        """)
        self.clicked.connect(self._on_clicked)

    def _on_clicked(self):
        open_platform_profile(self.platform_key, parent_window=self.parent_window)
