import logging
from pathlib import Path
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QPushButton

logger = logging.getLogger("ArtCrossPost.UIIcons")

ICONS_DIR = Path(__file__).resolve().parent / "assets" / "icons"
_LOAD_FAILED_CACHE = set()


def get_icon_path(name: str) -> Path:
    """获取 assets/icons/ 下的图标绝对路径"""
    filename = name if name.endswith(".svg") else f"{name}.svg"
    return ICONS_DIR / filename


def render_svg_pixmap(renderer: QSvgRenderer, size: QSize, color: QColor, scale: float = 1.0) -> QPixmap:
    """渲染指定尺寸与颜色的抗锯齿矢量 Pixmap (支持 High-DPI 多倍率缩放平滑不发虚)"""
    phys_w = max(1, int(size.width() * scale))
    phys_h = max(1, int(size.height() * scale))
    img = QImage(phys_w, phys_h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)

    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter)

    # 动态着色：以原矢量路径 Alpha 遮罩为基准注入主题色
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(img.rect(), color)
    painter.end()

    pix = QPixmap.fromImage(img)
    pix.setDevicePixelRatio(scale)
    return pix


def get_ui_svg_icon(
    name: str,
    size: int = 18,
    normal_color: str = "#e2e8f0",
    disabled_color: str = "#64748b"
) -> QIcon:
    """加载并渲染带主题自适应着色与多倍率平滑缩放的 QIcon

    Args:
        name: 图标名称 (例如 "refresh" 或 "refresh.svg")
        size: 逻辑像素尺寸 (默认 18)
        normal_color: 启用态颜色 HEX (默认 #e2e8f0，契合全暗色主题规范)
        disabled_color: 禁用态颜色 HEX (默认 #64748b)

    Returns:
        QIcon 实例，若文件缺失或解码失败返回空图标 QIcon()，不崩溃
    """
    path = get_icon_path(name)
    if not path.is_file():
        if name not in _LOAD_FAILED_CACHE:
            logger.warning(f"[UIIcons] SVG 图标文件不存在: {path}")
            _LOAD_FAILED_CACHE.add(name)
        return QIcon()

    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        if name not in _LOAD_FAILED_CACHE:
            logger.warning(f"[UIIcons] 无法解析 SVG 图标: {path}")
            _LOAD_FAILED_CACHE.add(name)
        return QIcon()

    icon = QIcon()
    base_size = QSize(size, size)
    col_normal = QColor(normal_color)
    col_disabled = QColor(disabled_color)

    # 生成 1x, 2x, 3x 多分辨率位图，保证高分屏/Retina 与标准屏均极致清晰不发虚
    for scale in (1.0, 2.0, 3.0):
        pix_normal = render_svg_pixmap(renderer, base_size, col_normal, scale)
        icon.addPixmap(pix_normal, QIcon.Normal, QIcon.Off)
        pix_disabled = render_svg_pixmap(renderer, base_size, col_disabled, scale)
        icon.addPixmap(pix_disabled, QIcon.Disabled, QIcon.Off)

    return icon


def create_ui_icon_button(
    icon_name: str,
    tooltip: str = "",
    btn_size: int = 28,
    icon_size: int = 18,
    object_name: str = "iconBtn",
    parent=None
) -> QPushButton:
    """创建统一规格的纯图标按钮 (约 28×28 按钮、18–20px 图标)

    Args:
        icon_name: 图标名称 (如 "refresh.svg")
        tooltip: 鼠标悬停提示
        btn_size: 按钮边长 (默认 28)
        icon_size: 图标尺寸 (默认 18)
        object_name: 样式表选择器名 (默认 "iconBtn")
        parent: 父控件
    """
    btn = QPushButton(parent)
    btn.setObjectName(object_name)
    btn.setFixedSize(btn_size, btn_size)
    btn.setCursor(Qt.PointingHandCursor)

    icon = get_ui_svg_icon(icon_name, size=icon_size)
    if not icon.isNull():
        btn.setIcon(icon)
        btn.setIconSize(QSize(icon_size, icon_size))

    if tooltip:
        btn.setToolTip(tooltip)

    # 紧凑内边距确保 18–20px 图标居中显示
    btn.setStyleSheet("padding: 2px;")
    return btn
