# -*- coding: utf-8 -*-
"""
ArtCrossPost - 多平台艺术创作一键同步分发工具 (PyQt5 客户端 - 纯暗色旗舰全尺寸版)
集成:
1. 综合效果实时预览框 (打码与水印双重同框合成，支持字体选择与平铺防盗印)
2. 内嵌通知栏与事件日志系统 (彻底取缔阻塞弹窗 QMessageBox，沉浸式实时反馈)
3. 启动即全尺寸/最大化展示 (showMaximized，三栏黄金分割布局)
4. 深度纯暗色调 (Pure Dark Mode)，去除一切白底与高亮刺眼反差
"""
try:
    from logger import get_logger, logger
except ImportError:
    import logging
    logger = logging.getLogger("ArtCrossPost")

# 必须在导入 cv2 / PyQt5 之前预先导入 onnxruntime，
# 彻底解决 Windows 下 OpenCV 与 ONNX Runtime 的 DLL 初始化例程冲突
try:
    import onnxruntime
except Exception as e:
    logger.debug(f"预导入 onnxruntime 提示: {e}")

import sys
import os
from pathlib import Path
from datetime import datetime
import json
import numpy as np
import cv2
from PIL import Image

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QCheckBox, QComboBox,
    QFileDialog, QListWidget, QListWidgetItem, QGroupBox, QSplitter,
    QProgressBar, QScrollArea, QFrame, QSlider, QSizePolicy, QMenu,
    QTextBrowser
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QByteArray, QEvent, QRect, QPoint, QObject
from PyQt5.QtGui import QIcon, QFont, QPixmap, QColor, QPainter, QBrush, QImage, QPalette, QMovie, QPainterPath, QPen, QFontMetrics

from config import load_config, save_config, load_window_state, save_window_state, COOKIES_DIR, CACHE_DIR
from censor_engine import CensorEngine
from watermark_engine import WatermarkEngine
from dispatchers import get_all_dispatchers
from x_text_utils import calculate_x_char_count, truncate_x_text, split_into_x_threads

DARK_STYLE = """
QMainWindow {
    background-color: #121218;
}
QWidget {
    color: #e2e8f0;
    font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;
    font-size: 14px;
}
QGroupBox {
    border: 1px solid #2e2e3e;
    background-color: #181822;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 16px;
    font-weight: bold;
    font-size: 15px;
    color: #a5b4fc;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    background-color: transparent;
}
QLineEdit, QTextEdit {
    background-color: #0c0d14;
    border: 1px solid #282b3c;
    border-radius: 6px;
    padding: 8px;
    color: #f1f5f9;
    font-size: 14px;
}
QLineEdit:focus, QTextEdit:focus {
    border: 1px solid #6366f1;
    background-color: #10111a;
}
QPushButton, QPushButton#secondaryBtn, QPushButton#accentBtn {
    background-color: #3730a3;
    color: #ffffff;
    font-weight: bold;
    font-size: 13px;
    border-radius: 6px;
    padding: 8px 14px;
    border: 1px solid #4f46e5;
}
QPushButton:hover, QPushButton#secondaryBtn:hover, QPushButton#accentBtn:hover {
    background-color: #4338ca;
    border: 1px solid #6366f1;
    color: #ffffff;
}
QPushButton:pressed, QPushButton#secondaryBtn:pressed, QPushButton#accentBtn:pressed {
    background-color: #312e81;
    border: 1px solid #4338ca;
}
QPushButton:disabled, QPushButton#secondaryBtn:disabled, QPushButton#accentBtn:disabled {
    background-color: #1e1e2d;
    border: 1px solid #2d2d42;
    color: #64748b;
}
QPushButton#publishBtn {
    background-color: #059669;
    color: #ffffff;
    font-size: 16px;
    font-weight: bold;
    padding: 12px;
    border-radius: 8px;
    border: 1px solid #10b981;
}
QPushButton#publishBtn:hover {
    background-color: #10b981;
    border-color: #34d399;
}
QPushButton#publishBtn:disabled {
    background-color: #1a2e26;
    color: #059669;
    border: 1px solid #059669;
}
QCheckBox {
    spacing: 8px;
    font-size: 14px;
    color: #e2e8f0;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #4b5563;
    background-color: #161622;
}
QCheckBox::indicator:checked {
    background-color: #4f46e5;
    border-color: #4f46e5;
}
QComboBox {
    background-color: #181822;
    border: 1px solid #374151;
    border-radius: 6px;
    padding: 6px 10px;
    color: #e2e8f0;
    font-size: 14px;
}
QComboBox:hover {
    border-color: #6366f1;
}
QComboBox QAbstractItemView {
    background-color: #181822;
    color: #e2e8f0;
    border: 1px solid #374151;
    selection-background-color: #4338ca;
    selection-color: #ffffff;
    outline: none;
    padding: 4px;
}
QListWidget {
    background-color: #13131a;
    border: 1px solid #374151;
    border-radius: 6px;
    color: #e2e8f0;
    font-size: 13px;
}
QListWidget::item {
    padding: 4px 8px;
    border-radius: 4px;
    color: #e2e8f0;
}
QListWidget::item:hover {
    background-color: #242434;
}
QListWidget::item:selected {
    background-color: #4338ca;
    color: #ffffff;
}
QProgressBar {
    border: 1px solid #374151;
    border-radius: 6px;
    text-align: center;
    background-color: #161622;
    color: #e2e8f0;
    font-size: 13px;
}
QProgressBar::chunk {
    background-color: #4f46e5;
    border-radius: 5px;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #2a2a38;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #6366f1;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #818cf8;
    border: 2px solid #4338ca;
    width: 16px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 8px;
}
QScrollArea {
    border: none;
    background-color: #121218;
}
QScrollBar:vertical {
    border: none;
    background: #121218;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2a2a3a;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background: #4f46e5;
}
QScrollBar:horizontal {
    border: none;
    background: #121218;
    height: 8px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #2a2a3a;
    border-radius: 4px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover {
    background: #4f46e5;
}
QSplitter::handle {
    background-color: #1c1c28;
}
QSplitter::handle:hover {
    background-color: #4f46e5;
}
QMenu {
    background-color: #181822;
    color: #e2e8f0;
    border: 1px solid #374151;
    padding: 4px;
}
QMenu::item:selected {
    background-color: #4338ca;
    color: #ffffff;
}
"""

class UnifiedPreviewCanvas(QLabel):
    """大尺寸综合效果实时预览画布（打码 + 水印双重合成，支持图片与 GIF 动图，支持直接在画布上鼠标调整/框选/缩放打码位置）"""
    boxes_changed = pyqtSignal(list)
    box_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)

        self._movie = None
        self._pixmap = None
        self._gif_path = None
        self._orig_gif_size = None
        self._orig_img_size = None
        self._censor_info = None
        self._watermark_info = None
        self._cached_geometry = None  # P0-2: 缓存画布渲染几何数据，避免鼠标拖拽及重绘时重复缩放

        # 交互式打码框状态
        self._censor_boxes = []  # 图像原图坐标系 [[x1, y1, x2, y2], ...]
        self._censor_enabled = False
        self._selected_box_idx = -1
        self._drag_mode = "none"  # "none", "move", "resize_tl", "resize_tc", "resize_tr", "resize_ml", "resize_mr", "resize_bl", "resize_bc", "resize_br", "create"
        self._drag_start_pt = QPoint()
        self._drag_start_box = []
        self._current_mouse_pt = QPoint()

        self._placeholder_text = (
            "🖼️ 暂未导入图片\n\n"
            "• 支持 PNG / JPG / WebP / GIF 动图\n"
            "• 打码与水印效果将在此统一合成预览\n"
            "• 鼠标可直接在此调整打码框位置与大小"
        )
        self.clear_image()

    def set_gif(self, gif_path: str):
        if self._gif_path == str(gif_path) and self._movie and self._movie.isValid():
            return

        self._stop_movie()
        self._pixmap = None
        self._gif_path = str(gif_path)
        self._cached_geometry = None
        self.setStyleSheet("""
            background-color: #0c0c12;
            border: 1px solid #374151;
            border-radius: 8px;
        """)

        # 快速获取动图原生尺寸，避免调用 jumpToFrame 导致播放卡顿
        try:
            with Image.open(str(gif_path)) as im:
                self._orig_gif_size = QSize(im.width, im.height)
                self._orig_img_size = self._orig_gif_size
        except Exception as e:
            logger.debug(f"读取动图原生尺寸异常: {e}")
            self._orig_gif_size = QSize(800, 600)
            self._orig_img_size = self._orig_gif_size

        self._movie = QMovie(str(gif_path))
        self._update_movie_size()
        self.setMovie(self._movie)
        self._movie.start()

    def set_gif_overlay(self, censor_info: dict, watermark_info: dict):
        """更新动图播放时的动态覆盖图层参数，立即重绘，零磁盘写入，零重编码"""
        self._censor_info = censor_info
        self._watermark_info = watermark_info
        if censor_info:
            self._censor_boxes = [list(b) for b in censor_info.get("boxes", [])]
            self._censor_enabled = censor_info.get("enabled", False)
        self.update()

    def set_censor_boxes(self, boxes: list, orig_w: int, orig_h: int, enabled: bool = True):
        """同步外部打码框列表至画布内部交互状态"""
        self._censor_boxes = [list(b) for b in (boxes or [])]
        if orig_w > 0 and orig_h > 0:
            self._orig_img_size = QSize(orig_w, orig_h)
            self._cached_geometry = None
        self._censor_enabled = enabled
        if self._selected_box_idx >= len(self._censor_boxes):
            self._selected_box_idx = -1
        self.update()

    def get_gif_orig_size(self):
        if self._orig_gif_size and self._orig_gif_size.isValid():
            return self._orig_gif_size.width(), self._orig_gif_size.height()
        return 0, 0

    def set_image(self, pixmap: QPixmap, censor_boxes: list = None, orig_w: int = None, orig_h: int = None, censor_enabled: bool = False):
        self._stop_movie()
        self._pixmap = pixmap
        self._cached_geometry = None
        if orig_w and orig_h:
            self._orig_img_size = QSize(orig_w, orig_h)
        elif pixmap and not pixmap.isNull():
            self._orig_img_size = pixmap.size()
        else:
            self._orig_img_size = None

        self._censor_boxes = [list(b) for b in (censor_boxes or [])]
        self._censor_enabled = censor_enabled
        if self._selected_box_idx >= len(self._censor_boxes):
            self._selected_box_idx = -1

        self.setStyleSheet("""
            background-color: #0c0c12;
            border: 1px solid #374151;
            border-radius: 8px;
        """)
        self._update_display()
        self.update()

    def _stop_movie(self):
        if self._movie:
            self._movie.stop()
            self._movie = None
        self._gif_path = None
        self._orig_gif_size = None
        self._cached_geometry = None

    def clear_image(self):
        self._stop_movie()
        self._pixmap = None
        self._censor_info = None
        self._watermark_info = None
        self._censor_boxes = []
        self._censor_enabled = False
        self._selected_box_idx = -1
        self._cached_geometry = None
        self.setStyleSheet("""
            background-color: #0c0c12;
            border: 1px dashed #2e2e3e;
            border-radius: 8px;
            color: #64748b;
            font-size: 15px;
            line-height: 1.6;
        """)
        self.setText(self._placeholder_text)

    def _update_movie_size(self):
        self._cached_geometry = None
        if self._movie and self._movie.isValid() and self._orig_gif_size:
            target = self.size()
            scaled = self._orig_gif_size.scaled(
                max(10, target.width() - 16),
                max(10, target.height() - 16),
                Qt.KeepAspectRatio
            )
            self._movie.setScaledSize(scaled)

    def _update_display(self):
        self._cached_geometry = None
        if self._pixmap and not self._pixmap.isNull():
            target_size = self.size()
            scaled = self._pixmap.scaled(
                max(10, target_size.width() - 12),
                max(10, target_size.height() - 12),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            super().setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._cached_geometry = None
        if self._movie:
            self._update_movie_size()
        elif self._pixmap:
            self._update_display()

    def get_display_geometry(self):
        """返回当前内容在控件内部的实际渲染区域 (orig_w, orig_h, ox, oy, dw, dh, scale_x, scale_y) (P0-2: 缓存加速与纯数学换算)"""
        if self._cached_geometry is not None:
            return self._cached_geometry

        if self._movie and self._movie.isValid() and self._orig_gif_size:
            orig_w = self._orig_gif_size.width()
            orig_h = self._orig_gif_size.height()
            scaled_size = self._movie.scaledSize()
            dw = scaled_size.width()
            dh = scaled_size.height()
        elif self._pixmap and not self._pixmap.isNull():
            orig_w = self._orig_img_size.width() if self._orig_img_size else self._pixmap.width()
            orig_h = self._orig_img_size.height() if self._orig_img_size else self._pixmap.height()
            if orig_w <= 0 or orig_h <= 0:
                return 0, 0, 0, 0, 0, 0, 1.0, 1.0
            target_w = max(10, self.width() - 12)
            target_h = max(10, self.height() - 12)
            scale = min(target_w / orig_w, target_h / orig_h)
            dw = max(1, int(orig_w * scale))
            dh = max(1, int(orig_h * scale))
        else:
            return 0, 0, 0, 0, 0, 0, 1.0, 1.0

        if orig_w <= 0 or orig_h <= 0 or dw <= 0 or dh <= 0:
            return 0, 0, 0, 0, 0, 0, 1.0, 1.0

        ox = (self.width() - dw) // 2
        oy = (self.height() - dh) // 2
        scale_x = dw / orig_w
        scale_y = dh / orig_h
        self._cached_geometry = (orig_w, orig_h, ox, oy, dw, dh, scale_x, scale_y)
        return self._cached_geometry

    def img_to_widget_rect(self, box):
        orig_w, orig_h, ox, oy, dw, dh, sx, sy = self.get_display_geometry()
        x1, y1, x2, y2 = box
        wx = ox + int(x1 * sx)
        wy = oy + int(y1 * sy)
        ww = max(1, int((x2 - x1) * sx))
        wh = max(1, int((y2 - y1) * sy))
        return QRect(wx, wy, ww, wh)

    def widget_to_img_point(self, pt: QPoint):
        orig_w, orig_h, ox, oy, dw, dh, sx, sy = self.get_display_geometry()
        if sx <= 0 or sy <= 0:
            return 0, 0
        ix = int((pt.x() - ox) / sx)
        iy = int((pt.y() - oy) / sy)
        return max(0, min(orig_w, ix)), max(0, min(orig_h, iy))

    def _get_handles_for_rect(self, r: QRect):
        return {
            "resize_tl": QPoint(r.left(), r.top()),
            "resize_tc": QPoint(r.center().x(), r.top()),
            "resize_tr": QPoint(r.right(), r.top()),
            "resize_ml": QPoint(r.left(), r.center().y()),
            "resize_mr": QPoint(r.right(), r.center().y()),
            "resize_bl": QPoint(r.left(), r.bottom()),
            "resize_bc": QPoint(r.center().x(), r.bottom()),
            "resize_br": QPoint(r.right(), r.bottom()),
        }

    def _hit_test(self, pt: QPoint):
        orig_w, orig_h, ox, oy, dw, dh, sx, sy = self.get_display_geometry()
        img_rect = QRect(ox, oy, dw, dh)
        if not img_rect.contains(pt):
            return "none", -1

        if not self._censor_enabled:
            return "none", -1

        # 1. 优先测试当前选中框的 8 个拖拽调整控制柄 (半径 10px)
        if 0 <= self._selected_box_idx < len(self._censor_boxes):
            box = self._censor_boxes[self._selected_box_idx]
            r = self.img_to_widget_rect(box)
            handles = self._get_handles_for_rect(r)
            for mode, h_pt in handles.items():
                if (pt - h_pt).manhattanLength() <= 10:
                    return mode, self._selected_box_idx

        # 2. 测试是否落在某个框内部 (后绘制的框优先命中)
        for idx in range(len(self._censor_boxes) - 1, -1, -1):
            r = self.img_to_widget_rect(self._censor_boxes[idx])
            if r.contains(pt):
                return "move", idx

        # 3. 落在图像空白区域 -> 准备按住拖拽画新框
        return "create", -1

    def _update_cursor_for_hit(self, mode):
        if mode in ("resize_tl", "resize_br"):
            self.setCursor(Qt.SizeFDiagCursor)
        elif mode in ("resize_tr", "resize_bl"):
            self.setCursor(Qt.SizeBDiagCursor)
        elif mode in ("resize_tc", "resize_bc"):
            self.setCursor(Qt.SizeVerCursor)
        elif mode in ("resize_ml", "resize_mr"):
            self.setCursor(Qt.SizeHorCursor)
        elif mode == "move":
            self.setCursor(Qt.SizeAllCursor)
        elif mode == "create":
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            mode, idx = self._hit_test(event.pos())
            self._drag_mode = mode
            self._drag_start_pt = event.pos()
            self._current_mouse_pt = event.pos()

            if mode in ("move", "resize_tl", "resize_tc", "resize_tr", "resize_ml", "resize_mr", "resize_bl", "resize_bc", "resize_br"):
                self._selected_box_idx = idx
                self._drag_start_box = list(self._censor_boxes[idx])
                self.box_selected.emit(idx)
                self.update()
            elif mode == "create":
                self._selected_box_idx = -1
                self.update()
        elif event.button() == Qt.RightButton:
            mode, idx = self._hit_test(event.pos())
            if idx >= 0:
                self._selected_box_idx = idx
                self.update()
                self._show_box_context_menu(event.globalPos(), idx)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._current_mouse_pt = event.pos()
        if self._drag_mode == "none":
            mode, _ = self._hit_test(event.pos())
            self._update_cursor_for_hit(mode)
        elif self._drag_mode == "move" and 0 <= self._selected_box_idx < len(self._censor_boxes):
            orig_w, orig_h, ox, oy, dw, dh, sx, sy = self.get_display_geometry()
            dx = int((event.pos().x() - self._drag_start_pt.x()) / max(0.001, sx))
            dy = int((event.pos().y() - self._drag_start_pt.y()) / max(0.001, sy))
            b = self._drag_start_box
            bw = b[2] - b[0]
            bh = b[3] - b[1]
            nx1 = max(0, min(orig_w - bw, b[0] + dx))
            ny1 = max(0, min(orig_h - bh, b[1] + dy))
            self._censor_boxes[self._selected_box_idx] = [nx1, ny1, nx1 + bw, ny1 + bh]
            self.update()
        elif self._drag_mode.startswith("resize_") and 0 <= self._selected_box_idx < len(self._censor_boxes):
            orig_w, orig_h, ox, oy, dw, dh, sx, sy = self.get_display_geometry()
            dx = int((event.pos().x() - self._drag_start_pt.x()) / max(0.001, sx))
            dy = int((event.pos().y() - self._drag_start_pt.y()) / max(0.001, sy))
            b = list(self._drag_start_box)
            mode = self._drag_mode

            if "l" in mode:
                b[0] = max(0, min(b[2] - 10, b[0] + dx))
            if "r" in mode:
                b[2] = max(b[0] + 10, min(orig_w, b[2] + dx))
            if "t" in mode:
                b[1] = max(0, min(b[3] - 10, b[1] + dy))
            if "b" in mode:
                b[3] = max(b[1] + 10, min(orig_h, b[3] + dy))

            self._censor_boxes[self._selected_box_idx] = b
            self.update()
        elif self._drag_mode == "create":
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._drag_mode == "create":
                ix1, iy1 = self.widget_to_img_point(self._drag_start_pt)
                ix2, iy2 = self.widget_to_img_point(event.pos())
                x1, x2 = min(ix1, ix2), max(ix1, ix2)
                y1, y2 = min(iy1, iy2), max(iy1, iy2)
                if (x2 - x1) >= 12 and (y2 - y1) >= 12:
                    new_b = [x1, y1, x2, y2]
                    self._censor_boxes.append(new_b)
                    self._selected_box_idx = len(self._censor_boxes) - 1
                    self.boxes_changed.emit(list(self._censor_boxes))
            elif self._drag_mode in ("move", "resize_tl", "resize_tc", "resize_tr", "resize_ml", "resize_mr", "resize_bl", "resize_bc", "resize_br"):
                self.boxes_changed.emit(list(self._censor_boxes))

            self._drag_mode = "none"
            mode, _ = self._hit_test(event.pos())
            self._update_cursor_for_hit(mode)
            self.update()
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        event.ignore()

    def _show_box_context_menu(self, global_pos, idx):
        menu = QMenu(self)
        del_act = menu.addAction("🗑️ 删除此打码框 (Delete)")
        clear_act = menu.addAction("🧹 清除当前所有打码框")
        chosen = menu.exec_(global_pos)
        if chosen == del_act:
            self.delete_selected_box()
        elif chosen == clear_act:
            self.clear_all_boxes()

    def delete_selected_box(self):
        if 0 <= self._selected_box_idx < len(self._censor_boxes):
            del self._censor_boxes[self._selected_box_idx]
            self._selected_box_idx = -1
            self.boxes_changed.emit(list(self._censor_boxes))
            self.update()

    def clear_all_boxes(self):
        self._censor_boxes = []
        self._selected_box_idx = -1
        self.boxes_changed.emit([])
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected_box()
        super().keyPressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)

        orig_w, orig_h, ox, oy, dw, dh, scale_x, scale_y = self.get_display_geometry()
        if orig_w <= 0 or orig_h <= 0 or dw <= 0 or dh <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 1. 动图图层覆盖 (若为 GIF)
        if self._movie and self._movie.isValid():
            # 1.1 动图实时动态打码图层覆盖 (黑条/马赛克/模糊)
            if self._censor_info and self._censor_info.get("enabled"):
                boxes = self._censor_boxes if self._censor_boxes else self._censor_info.get("boxes", [])
                style = self._censor_info.get("style", "black_bar")
                curr_pm = self._movie.currentPixmap() if style in ("mosaic", "blur") else None

                for box in boxes:
                    bx1, by1, bx2, by2 = box
                    dx1 = ox + int(bx1 * scale_x)
                    dy1 = oy + int(by1 * scale_y)
                    box_w = max(1, int((bx2 - bx1) * scale_x))
                    box_h = max(1, int((by2 - by1) * scale_y))
                    rect = QRect(dx1, dy1, box_w, box_h)

                    if style == "black_bar":
                        painter.fillRect(rect, QColor(0, 0, 0))
                    elif style == "mosaic" and curr_pm and not curr_pm.isNull():
                        pm_w = curr_pm.width()
                        pm_h = curr_pm.height()
                        rx = max(0, min(pm_w - 1, int(bx1 * (pm_w / orig_w))))
                        ry = max(0, min(pm_h - 1, int(by1 * (pm_h / orig_h))))
                        rw = max(1, min(pm_w - rx, int((bx2 - bx1) * (pm_w / orig_w))))
                        rh = max(1, min(pm_h - ry, int((by2 - by1) * (pm_h / orig_h))))
                        roi = curr_pm.copy(rx, ry, rw, rh)
                        small = roi.scaled(max(3, rw // 15), max(3, rh // 15), Qt.IgnoreAspectRatio, Qt.FastTransformation)
                        pixelated = small.scaled(box_w, box_h, Qt.IgnoreAspectRatio, Qt.FastTransformation)
                        painter.drawPixmap(dx1, dy1, pixelated)
                    elif style == "blur" and curr_pm and not curr_pm.isNull():
                        pm_w = curr_pm.width()
                        pm_h = curr_pm.height()
                        rx = max(0, min(pm_w - 1, int(bx1 * (pm_w / orig_w))))
                        ry = max(0, min(pm_h - 1, int(by1 * (pm_h / orig_h))))
                        rw = max(1, min(pm_w - rx, int((bx2 - bx1) * (pm_w / orig_w))))
                        rh = max(1, min(pm_h - ry, int((by2 - by1) * (pm_h / orig_h))))
                        roi = curr_pm.copy(rx, ry, rw, rh)
                        small = roi.scaled(max(4, rw // 16), max(4, rh // 16), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                        blurred = small.scaled(box_w, box_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                        painter.drawPixmap(dx1, dy1, blurred)

            # 1.2 动图动态文字水印图层覆盖 (字体族/加粗/精确方位计算)
            if self._watermark_info and self._watermark_info.get("enabled"):
                text = self._watermark_info.get("text", "").strip()
                if text:
                    font_family = self._watermark_info.get("font_family") or self._watermark_info.get("font_name", "Microsoft YaHei")
                    is_bold = self._watermark_info.get("is_bold", True)
                    base_font_size = self._watermark_info.get("font_size", 36)
                    opacity = max(0.05, min(1.0, self._watermark_info.get("opacity", 0.65)))
                    pos = self._watermark_info.get("position", "bottom_right")
                    color_hex = self._watermark_info.get("color_hex", "#ffffff")
                    enable_stroke = self._watermark_info.get("stroke", True)

                    scaled_font_sz = max(10, int(base_font_size * scale_y))
                    font = QFont(font_family, scaled_font_sz, QFont.Bold if is_bold else QFont.Normal)
                    painter.setFont(font)
                    metrics = painter.fontMetrics()
                    tw = metrics.horizontalAdvance(text) if hasattr(metrics, "horizontalAdvance") else metrics.width(text)
                    ascent = metrics.ascent()
                    descent = metrics.descent()

                    margin_x = int(24 * scale_x)
                    margin_y = int(24 * scale_y)

                    if pos == "bottom_right":
                        tx = ox + dw - tw - margin_x
                        ty = oy + dh - margin_y - descent
                    elif pos == "bottom_left":
                        tx = ox + margin_x
                        ty = oy + dh - margin_y - descent
                    elif pos == "top_right":
                        tx = ox + dw - tw - margin_x
                        ty = oy + margin_y + ascent
                    elif pos == "top_left":
                        tx = ox + margin_x
                        ty = oy + margin_y + ascent
                    elif pos == "center":
                        tx = ox + (dw - tw) // 2
                        ty = oy + (dh + ascent - descent) // 2
                    elif pos == "bottom_center":
                        tx = ox + (dw - tw) // 2
                        ty = oy + dh - margin_y - descent
                    else:
                        tx = ox + dw - tw - margin_x
                        ty = oy + dh - margin_y - descent

                    base_col = QColor(color_hex)
                    fill_color = QColor(base_col.red(), base_col.green(), base_col.blue(), int(opacity * 255))

                    path = QPainterPath()
                    path.addText(tx, ty, font, text)

                    if enable_stroke:
                        stroke_w = max(2, int(scaled_font_sz / 9))
                        painter.strokePath(path, QPen(QColor(0, 0, 0, int(opacity * 220)), stroke_w))

                    painter.fillPath(path, QBrush(fill_color))

        # 2. 交互式打码控制层（动图与静态图片共用，只要打码开启即显示高亮边框、8向拖拽控制手柄和编号徽章）
        if self._censor_enabled and self._censor_boxes:
            for idx, box in enumerate(self._censor_boxes):
                r = self.img_to_widget_rect(box)
                is_selected = (idx == self._selected_box_idx)

                if is_selected:
                    # 选中高亮边框 (青色醒目，带8个控制锚点)
                    pen = QPen(QColor(6, 182, 212), 2.5, Qt.SolidLine)
                    painter.setPen(pen)
                    painter.setBrush(QBrush(QColor(6, 182, 212, 35)))
                    painter.drawRect(r)

                    # 绘制 8 个缩放控制点 (圆形白色带青边)
                    handles = self._get_handles_for_rect(r)
                    h_pen = QPen(QColor(6, 182, 212), 2)
                    h_brush = QBrush(QColor(255, 255, 255))
                    painter.setPen(h_pen)
                    painter.setBrush(h_brush)
                    for h_pt in handles.values():
                        painter.drawEllipse(h_pt, 5, 5)

                    # 编号徽章
                    badge_rect = QRect(r.left(), max(0, r.top() - 20), 40, 18)
                    painter.fillRect(badge_rect, QColor(6, 182, 212, 220))
                    painter.setPen(QColor(255, 255, 255))
                    b_font = QFont("Microsoft YaHei", 9, QFont.Bold)
                    painter.setFont(b_font)
                    painter.drawText(badge_rect, Qt.AlignCenter, f"#{idx+1}")
                else:
                    # 未选中边框 (优雅紫罗兰虚线)
                    pen = QPen(QColor(129, 140, 248), 1.5, Qt.DashLine)
                    painter.setPen(pen)
                    painter.setBrush(QBrush(QColor(99, 102, 241, 20)))
                    painter.drawRect(r)

                    badge_rect = QRect(r.left(), max(0, r.top() - 18), 32, 16)
                    painter.fillRect(badge_rect, QColor(99, 102, 241, 180))
                    painter.setPen(QColor(255, 255, 255))
                    b_font = QFont("Microsoft YaHei", 8, QFont.Bold)
                    painter.setFont(b_font)
                    painter.drawText(badge_rect, Qt.AlignCenter, f"#{idx+1}")

        # 3. 正在拖拽画新框的动态预览
        if self._drag_mode == "create" and self._drag_start_pt and self._current_mouse_pt:
            rx = min(self._drag_start_pt.x(), self._current_mouse_pt.x())
            ry = min(self._drag_start_pt.y(), self._current_mouse_pt.y())
            rw = abs(self._drag_start_pt.x() - self._current_mouse_pt.x())
            rh = abs(self._drag_start_pt.y() - self._current_mouse_pt.y())
            create_rect = QRect(rx, ry, rw, rh)

            pen = QPen(QColor(236, 72, 153), 2, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor(236, 72, 153, 50)))
            painter.drawRect(create_rect)

            tip_rect = QRect(rx, max(0, ry - 22), 80, 20)
            painter.fillRect(tip_rect, QColor(236, 72, 153, 220))
            painter.setPen(QColor(255, 255, 255))
            b_font = QFont("Microsoft YaHei", 9, QFont.Bold)
            painter.setFont(b_font)
            painter.drawText(tip_rect, Qt.AlignCenter, "+ 新建打码")

        painter.end()


class NotificationBar(QFrame):
    """内嵌通知栏组件，零弹窗沉浸式用户反馈与事件日志"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notificationBar")
        self.setStyleSheet("""
            QFrame#notificationBar {
                background-color: #14141e;
                border-top: 1px solid #2a2a38;
                padding: 4px 10px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # 头部单行通知条
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        # 状态等级徽章
        self.badge_lbl = QLabel("● 就绪")
        self.badge_lbl.setStyleSheet("""
            background-color: #1f2937;
            color: #9ca3af;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 13px;
            font-weight: bold;
        """)
        top_row.addWidget(self.badge_lbl)

        # 核心信息文本
        self.msg_lbl = QLabel("欢迎使用 ArtCrossPost - 请导入图片并开始分发配置")
        self.msg_lbl.setStyleSheet("color: #e2e8f0; font-size: 14px;")
        self.msg_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top_row.addWidget(self.msg_lbl, 1)

        # 时间戳
        self.time_lbl = QLabel(datetime.now().strftime("%H:%M:%S"))
        self.time_lbl.setStyleSheet("color: #6b7280; font-size: 12px;")
        top_row.addWidget(self.time_lbl)

        # 展开/收起日志按钮
        self.toggle_log_btn = QPushButton("📜 查看日志 (0)")
        self.toggle_log_btn.setObjectName("secondaryBtn")
        self.toggle_log_btn.setFixedHeight(28)
        self.toggle_log_btn.clicked.connect(self._toggle_log)
        top_row.addWidget(self.toggle_log_btn)

        # 关闭/清除按钮
        close_btn = QPushButton("✕")
        close_btn.setObjectName("secondaryBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.clear_banner)
        top_row.addWidget(close_btn)

        layout.addLayout(top_row)

        # 可展开的富文本日志抽屉
        self.log_drawer = QTextEdit()
        self.log_drawer.setReadOnly(True)
        self.log_drawer.setFixedHeight(110)
        self.log_drawer.setStyleSheet("""
            background-color: #0c0c12;
            border: 1px solid #2a2a38;
            border-radius: 6px;
            font-family: 'Consolas', 'Segoe UI Mono', monospace;
            font-size: 13px;
            color: #cbd5e1;
            padding: 6px;
        """)
        self.log_drawer.setVisible(False)
        layout.addWidget(self.log_drawer)

        self._log_count = 0
        self._auto_clear_timer = QTimer(self)
        self._auto_clear_timer.setSingleShot(True)
        self._auto_clear_timer.timeout.connect(self._on_auto_clear)

    def notify(self, message: str, level: str = "info", auto_dismiss: bool = True):
        now_str = datetime.now().strftime("%H:%M:%S")
        self.time_lbl.setText(now_str)
        self.msg_lbl.setText(message)

        if level == "success":
            badge_text = "✅ 成功"
            badge_style = "background-color: #065f46; color: #34d399; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 13px;"
            color_hex = "#34d399"
        elif level == "warning":
            badge_text = "⚠️ 警告"
            badge_style = "background-color: #78350f; color: #fbbf24; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 13px;"
            color_hex = "#fbbf24"
        elif level == "error":
            badge_text = "❌ 错误"
            badge_style = "background-color: #7f1d1d; color: #f87171; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 13px;"
            color_hex = "#f87171"
            self.expand_log(True)
        else:
            badge_text = "ℹ️ 提示"
            badge_style = "background-color: #312e81; color: #a5b4fc; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 13px;"
            color_hex = "#818cf8"

        self.badge_lbl.setText(badge_text)
        self.badge_lbl.setStyleSheet(badge_style)

        # 记录到日志抽屉
        self._log_count += 1
        self.toggle_log_btn.setText(f"📜 查看日志 ({self._log_count})")
        log_line = f"<font color='#6b7280'>[{now_str}]</font> <font color='{color_hex}'><b>[{badge_text}]</b></font> {message}"
        self.log_drawer.append(log_line)

        if auto_dismiss and level != "error":
            self._auto_clear_timer.start(8000)

    def append_rich_log(self, html_content: str):
        self.log_drawer.append(html_content)
        self._log_count += 1
        self.toggle_log_btn.setText(f"📜 查看日志 ({self._log_count})")

    def _toggle_log(self):
        is_vis = self.log_drawer.isVisible()
        self.expand_log(not is_vis)

    def expand_log(self, show: bool = True):
        self.log_drawer.setVisible(show)
        self.toggle_log_btn.setText("📜 收起日志" if show else f"📜 查看日志 ({self._log_count})")

    def _on_auto_clear(self):
        self.badge_lbl.setText("● 就绪")
        self.badge_lbl.setStyleSheet("background-color: #1f2937; color: #9ca3af; padding: 3px 8px; border-radius: 4px; font-size: 13px;")

    def clear_banner(self):
        self._auto_clear_timer.stop()
        self._on_auto_clear()
        self.msg_lbl.setText("就绪 - 等待操作")


class LoginThread(QThread):
    finished_signal = pyqtSignal(bool, str, dict)

    def __init__(self, dispatcher):
        super().__init__()
        self.dispatcher = dispatcher

    def run(self):
        try:
            success, msg = self.dispatcher.login_interactive()
            profile = self.dispatcher.get_profile()
            self.finished_signal.emit(success, msg, profile)
        except Exception as e:
            self.finished_signal.emit(False, str(e), {})


class PublishThread(QThread):
    progress_signal = pyqtSignal(int, str)
    completed_signal = pyqtSignal(dict)

    def __init__(self, selected_dispatchers, payload):
        super().__init__()
        self.dispatchers = selected_dispatchers
        self.payload = payload

    def run(self):
        results = {}
        valid_dispatchers = []

        # ---------------- 需求一：发布前自查门禁 (Pre-publish Gate) ----------------
        self.progress_signal.emit(0, "正在执行发布前自查门禁 (轻量探测登录态与素材限制)...")

        title = (self.payload.get("title") or "").strip()
        images = self.payload.get("images", [])
        raw_images = self.payload.get("raw_images", images)

        # 1.3 通用文本非空检查 (至少 1 字符)
        if not title:
            err_msg = "发布前门禁拦截: 作品标题不能为空，请先在标题栏填写作品标题"
            logger.error(f"[Pre-publish Gate] {err_msg}")
            for disp in self.dispatchers:
                results[disp.name] = (False, err_msg, [])
            self.progress_signal.emit(100, f"⚠️ {err_msg}")
            self.completed_signal.emit(results)
            return

        gate_skipped_count = 0
        for disp in self.dispatchers:
            disp_images = raw_images if disp.name in ("patreon", "kofi") else images

            # 1.1 登录态轻量探针 (probe_login: 不唤起浏览器)
            if hasattr(disp, "probe_login"):
                try:
                    probe = disp.probe_login()
                    if probe.get("status") == "fail":
                        fail_reason = probe.get("detail", "登录态失效")
                        logger.warning(f"[Pre-publish Gate] [{disp.display_name}] 登录态探针未通过: {fail_reason}，门禁阶段已跳过")
                        results[disp.name] = (False, f"发布前门禁拦截: {fail_reason}", [])
                        gate_skipped_count += 1
                        continue
                except Exception as e:
                    logger.debug(f"[Pre-publish Gate] [{disp.display_name}] 探针执行异常: {e}")

            # 1.2 素材合规检查 (validate_assets)
            asset_err = None
            if not disp_images:
                asset_err = "未选择任何作品图片素材"
            else:
                for img_p in disp_images:
                    p_obj = Path(img_p)
                    if not p_obj.exists():
                        asset_err = f"素材文件不存在: {p_obj.name}"
                        break
                    f_size = p_obj.stat().st_size
                    if f_size == 0:
                        asset_err = f"素材文件大小为 0: {p_obj.name}"
                        break

                    # 平台专属大小限制
                    if disp.name == "x_twitter":
                        if p_obj.suffix.lower() == ".gif" and f_size > 15 * 1024 * 1024:
                            asset_err = f"X (Twitter) 限制 GIF ≤ 15MB (当前 {f_size / 1024 / 1024:.2f}MB)"
                            break
                        elif p_obj.suffix.lower() in [".png", ".jpg", ".jpeg"] and f_size > 5 * 1024 * 1024:
                            asset_err = f"X (Twitter) 限制单张静态图 ≤ 5MB (当前 {f_size / 1024 / 1024:.2f}MB)"
                            break

            if asset_err:
                logger.warning(f"[Pre-publish Gate] [{disp.display_name}] 素材限制未通过: {asset_err}，门禁阶段已跳过")
                results[disp.name] = (False, f"发布前门禁拦截: {asset_err}", [])
                gate_skipped_count += 1
                continue

            valid_dispatchers.append(disp)

        if not valid_dispatchers:
            self.progress_signal.emit(100, "所有选中平台均未通过发布前门禁自检，发布已终止 (详见下方统计)")
            self.completed_signal.emit(results)
            return

        if gate_skipped_count > 0:
            self.progress_signal.emit(5, f"门禁已拦截 {gate_skipped_count} 个未就绪平台，继续为剩余 {len(valid_dispatchers)} 个平台发布...")
            self.msleep(800)

        # ---------------- 针对通过门禁的平台正式分发 ----------------
        valid_total = len(valid_dispatchers)
        for idx, disp in enumerate(valid_dispatchers):
            step = 5 + int(((idx) / max(1, valid_total)) * 90)
            self.progress_signal.emit(step, f"正在向 {disp.display_name} 发布内容...")

            try:
                ret = disp.publish(self.payload)
                if isinstance(ret, tuple) and len(ret) >= 3:
                    success, msg, compliance = ret[0], ret[1], ret[2]
                elif isinstance(ret, tuple) and len(ret) == 2:
                    success, msg = ret
                    compliance = []
                else:
                    success, msg, compliance = bool(ret), str(ret), []
            except Exception as e:
                success, msg, compliance = False, str(e), []

            # 针对网络/超时类异常自动重试 1 次 (P1-7)
            if not success and any(err_k in str(msg).lower() for err_k in ["timeout", "timed out", "net::err", "connection", "err_aborted"]):
                logger.warning(f"[{disp.display_name}] 捕获网络/超时波动 ({msg})，触发自动重试 (1/1)...")
                self.progress_signal.emit(step, f"[{disp.display_name}] 网络波动，正在自动重试 (1/1)...")
                self.msleep(1500)
                try:
                    ret_retry = disp.publish(self.payload)
                    if isinstance(ret_retry, tuple) and len(ret_retry) >= 3:
                        success, msg, compliance = ret_retry[0], ret_retry[1], ret_retry[2]
                    elif isinstance(ret_retry, tuple) and len(ret_retry) == 2:
                        success, msg = ret_retry
                        compliance = []
                    else:
                        success, msg, compliance = bool(ret_retry), str(ret_retry), []
                    if success:
                        logger.info(f"[{disp.display_name}] 自动重试成功！")
                except Exception as e2:
                    success, msg, compliance = False, f"重试后依然失败: {e2}", []
                    logger.error(f"[{disp.display_name}] 自动重试异常: {e2}")

            results[disp.name] = (success, msg, compliance)
            self.msleep(600)

        self.progress_signal.emit(100, "全平台分发已完成！")
        self.completed_signal.emit(results)


class DiagnosticThread(QThread):
    progress_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(dict, str)

    def __init__(self, data_dir, dispatchers):
        super().__init__()
        self.data_dir = data_dir
        self.dispatchers = dispatchers

    def run(self):
        try:
            from diagnostics import DiagnosticsEngine
            engine = DiagnosticsEngine(self.data_dir, self.dispatchers)
            report = engine.run_full_diagnostics(progress_callback=lambda p, msg: self.progress_signal.emit(p, msg))
            report_path = str(engine.report_file)
            self.finished_signal.emit(report, report_path)
        except Exception as e:
            logger.error(f"[DiagnosticThread] 自检执行异常: {e}")
            err_report = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "summary": {"total": 1, "pass": 0, "fail": 1, "unknown": 0, "health_score": 0},
                "items": [{
                    "platform": "system",
                    "category": "system",
                    "item": "diagnostics_crash",
                    "status": "fail",
                    "detail": f"自检执行异常: {e}",
                    "suggestion": "请查看控制台输出以获取错误调用栈"
                }]
            }
            self.finished_signal.emit(err_report, "")


class NoWheelEventFilter(QObject):
    """
    全局滚轮/中键事件过滤器（选择性拦截）：
    - 拦截：滚轮会误触改变数值的控件（QComboBox / QSlider），保持防误触行为；
    - 放行：以滚动浏览为主要用途的控件（QTextEdit / QTextBrowser / QScrollArea / QListWidget），
      例如日志抽屉需要滚轮翻阅日志。
    注意：QTextEdit 等的滚轮事件实际发往其 viewport，需沿 parent 链向上查找所属控件。
    未命中白名单的控件默认保持拦截（与旧行为一致，零回归风险）。
    """
    # 滚轮=滚动浏览 → 放行白名单
    _WHEEL_SCROLL_OK = (QTextEdit, QTextBrowser, QScrollArea, QListWidget)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel:
            target = obj
            while isinstance(target, QWidget):
                if isinstance(target, self._WHEEL_SCROLL_OK):
                    return super().eventFilter(obj, event)  # 放行，交由控件原生处理
                target = target.parent()
            event.ignore()
            return True  # 默认拦截，保持原有防误触行为
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease) and hasattr(event, "button"):
            if event.button() == Qt.MidButton:
                event.ignore()
                return True
        return super().eventFilter(obj, event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ArtCrossPost - 多平台创作同步分发助手 (全尺寸 & 水印打码双核实时预览版)")
        self.setMinimumSize(1260, 780)
        self.setStyleSheet(DARK_STYLE)

        self.data_dir = Path(__file__).resolve().parent / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._last_publish_payload = None
        self._last_failed_dispatchers = []

        self.config = load_config()
        self.censor_engine = CensorEngine(
            default_style=self.config["censor"]["style"],
            default_mode=self.config["censor"].get("coverage_mode", "moderate"),
            padding_ratio=self.config["censor"]["padding_ratio"]
        )
        self.watermark_engine = WatermarkEngine()
        self.dispatchers = get_all_dispatchers(COOKIES_DIR)
        
        self.image_paths = []
        self.custom_censor_boxes = {}  # {image_path: [[x1, y1, x2, y2], ...]}
        self.current_preview_index = 0
        self._current_rendered_pixmap = None

        # 预览防抖定时器 (80ms，保证拖动滑块时平滑不掉帧)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._do_refresh_live_preview)

        # 界面布局与尺寸自动持久化防抖定时器 (400ms，拖动调整或分栏变化后自动存储)
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.timeout.connect(self._save_window_layout)

        # 草稿自动保存防抖定时器 (1000ms，输入停止 1 秒后自动持久化，P1-6)
        self._draft_save_timer = QTimer(self)
        self._draft_save_timer.setSingleShot(True)
        self._draft_save_timer.setInterval(1000)
        self._draft_save_timer.timeout.connect(self._save_draft)

        # 绑定应用退出时自动保存，并全局安装滚轮/中键拦截器
        app_inst = QApplication.instance()
        if app_inst:
            app_inst.aboutToQuit.connect(self._save_window_layout)
            self._wheel_filter = NoWheelEventFilter(self)
            app_inst.installEventFilter(self._wheel_filter)

        self._is_restoring_layout = True
        self._build_ui()
        self._restore_window_layout()
        self._is_restoring_layout = False
        self._load_draft()

    def _save_draft(self):
        """P1-6: 自动将当前草稿保存至 data/draft.json"""
        draft_file = self.data_dir / "draft.json"
        if not hasattr(self, "title_input") or not hasattr(self, "tags_input") or not hasattr(self, "desc_input"):
            return
        title = self.title_input.text().strip()
        tags = self.tags_input.text().strip()
        desc = self.desc_input.toPlainText().strip()
        if not title and not tags and not desc:
            if draft_file.exists():
                try:
                    draft_file.unlink(missing_ok=True)
                except Exception as e:
                    logger.debug(f"清理空草稿文件异常: {e}")
            return
        data = {
            "title": self.title_input.text(),
            "tags": self.tags_input.text(),
            "description": self.desc_input.toPlainText(),
            "updated_at": datetime.now().isoformat()
        }
        try:
            with open(draft_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug("草稿自动保存成功")
        except Exception as e:
            logger.warning(f"草稿自动保存失败: {e}")

    def _load_draft(self):
        """P1-6: 启动时自动恢复上次草稿"""
        draft_file = self.data_dir / "draft.json"
        if not draft_file.exists():
            return
        try:
            with open(draft_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            t = data.get("title", "")
            tags = data.get("tags", "")
            desc = data.get("description", "")
            if t and hasattr(self, "title_input"):
                self.title_input.setText(t)
            if tags and hasattr(self, "tags_input"):
                self.tags_input.setText(tags)
            if desc and hasattr(self, "desc_input"):
                self.desc_input.setPlainText(desc)
            if t or tags or desc:
                self.notify("📝 已恢复上次未发布的草稿内容", level="info")
                logger.info("已成功恢复上次草稿内容")
        except Exception as e:
            logger.warning(f"读取草稿失败: {e}")

    def _clear_draft(self):
        """P1-6: 发布成功后清理草稿"""
        draft_file = self.data_dir / "draft.json"
        if draft_file.exists():
            try:
                draft_file.unlink(missing_ok=True)
                logger.info("发布成功，已清理草稿文件")
            except Exception as e:
                logger.warning(f"清理草稿文件失败: {e}")

    def _retry_failed_publishing(self):
        """P1-7: 手动重试上次失败的平台"""
        if not self._last_failed_dispatchers or not self._last_publish_payload:
            self.notify("没有可重试的失败项或未找到上次发布数据", level="warning")
            return

        if hasattr(self, "retry_btn"):
            self.retry_btn.hide()
        self.publish_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.notify(f"开始重试发布 {len(self._last_failed_dispatchers)} 个失败平台...", level="info")

        self.thread = PublishThread(self._last_failed_dispatchers, self._last_publish_payload)
        self.thread.progress_signal.connect(self._on_progress)
        self.thread.completed_signal.connect(self._on_completed)
        self.thread.start()

    def notify(self, message: str, level: str = "info", auto_dismiss: bool = True):
        """全局内嵌通知方法，取缔所有弹出式 QMessageBox"""
        if hasattr(self, "notification_bar"):
            self.notification_bar.notify(message, level=level, auto_dismiss=auto_dismiss)

    def _schedule_layout_save(self):
        """延迟 400ms 防抖保存窗口和布局"""
        if getattr(self, "_is_restoring_layout", False):
            return
        if hasattr(self, "_layout_save_timer"):
            self._layout_save_timer.start(400)

    def _save_window_layout(self):
        """记录并持久化窗口尺寸、位置、最大化状态与三栏分割线比例"""
        if getattr(self, "_is_restoring_layout", False):
            return

        try:
            if self.isMinimized():
                # 最小化状态不覆写正常几何尺寸，防止重新打开时变为 0 尺寸或任务栏隐匿
                return

            is_max = self.isMaximized()
            geom_ba = self.saveGeometry()
            geom_hex = bytes(geom_ba.toHex()).decode("ascii")

            # 提取常规状态下的坐标与宽高 (即使当前是最大化，normalGeometry 也会记录解除最大化后的矩形)
            norm_rect = self.normalGeometry()
            normal_geom = {
                "x": norm_rect.x(),
                "y": norm_rect.y(),
                "width": norm_rect.width(),
                "height": norm_rect.height()
            }

            splitter_sizes = self.main_splitter.sizes() if hasattr(self, "main_splitter") else [430, 780, 450]
            splitter_hex = ""
            if hasattr(self, "main_splitter"):
                splitter_ba = self.main_splitter.saveState()
                splitter_hex = bytes(splitter_ba.toHex()).decode("ascii")

            state_data = {
                "is_maximized": is_max,
                "geometry_hex": geom_hex,
                "normal_geometry": normal_geom,
                "splitter_sizes": splitter_sizes,
                "splitter_state_hex": splitter_hex,
                "last_saved": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            save_window_state(state_data)
        except Exception as e:
            logger.warning(f"[UI] 保存窗口布局失败: {e}")

    def _restore_window_layout(self):
        """开机/重启后恢复上一次记录的窗口大小与界面分栏布局"""
        state = load_window_state()
        if not state:
            # 首次启动：使用默认三栏比例并最大化全屏展示
            if hasattr(self, "main_splitter"):
                self.main_splitter.setSizes([430, 780, 450])
            self.showMaximized()
            return

        try:
            # 1. 恢复窗口位置与尺寸
            restored = False
            geom_hex = state.get("geometry_hex")
            if geom_hex:
                try:
                    restored = self.restoreGeometry(QByteArray.fromHex(geom_hex.encode("ascii")))
                except Exception as e:
                    logger.debug(f"[UI] 恢复几何状态异常: {e}")
                    restored = False

            if not restored:
                # 几何数据无效时的平稳降级回退
                norm = state.get("normal_geometry", {})
                w = max(1000, norm.get("width", 1400))
                h = max(700, norm.get("height", 850))
                x = max(0, norm.get("x", 100))
                y = max(0, norm.get("y", 100))
                self.setGeometry(x, y, w, h)

            # 2. 恢复三栏分割线比例
            spl_hex = state.get("splitter_state_hex")
            if spl_hex and hasattr(self, "main_splitter"):
                try:
                    self.main_splitter.restoreState(QByteArray.fromHex(spl_hex.encode("ascii")))
                except Exception as e:
                    logger.debug(f"[UI] 恢复分栏分割线状态异常: {e}")

            # 根据记录的状态决定是以最大化还是常规窗口显示
            if state.get("is_maximized", False):
                self.showMaximized()
            else:
                self.show()

            # 在窗口显示后再应用一次精确的像素尺寸列表（确保高分屏与不同缩放比例自适应）
            spl_sizes = state.get("splitter_sizes")
            if spl_sizes and hasattr(self, "main_splitter") and isinstance(spl_sizes, list) and len(spl_sizes) == 3:
                if all(isinstance(s, int) and s > 50 for s in spl_sizes):
                    self.main_splitter.setSizes(spl_sizes)
        except Exception as e:
            logger.warning(f"[UI] 恢复窗口布局异常: {e}")
            if hasattr(self, "main_splitter"):
                self.main_splitter.setSizes([430, 780, 450])
            self.showMaximized()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_layout_save()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._schedule_layout_save()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._schedule_layout_save()

    def wheelEvent(self, event):
        event.ignore()

    def closeEvent(self, event):
        if hasattr(self, "_layout_save_timer"):
            self._layout_save_timer.stop()
        self._save_window_layout()
        self._sync_config_from_ui()
        super().closeEvent(event)

    def _sync_config_from_ui(self):
        """将界面当前的常用开关与设置回写至 presets.json，保证下次启动继承偏好"""
        try:
            # 1. 水印参数
            if hasattr(self, "watermark_enabled_cb"):
                self.config["watermark"]["enabled"] = self.watermark_enabled_cb.isChecked()
            if hasattr(self, "watermark_text_input"):
                self.config["watermark"]["text"] = self.watermark_text_input.text()
            if hasattr(self, "watermark_font_combo"):
                self.config["watermark"]["font_family"] = self.watermark_font_combo.currentText()
            if hasattr(self, "watermark_size_slider"):
                self.config["watermark"]["font_size"] = self.watermark_size_slider.value()
            if hasattr(self, "watermark_opacity_slider"):
                self.config["watermark"]["opacity"] = self.watermark_opacity_slider.value() / 100.0
            if hasattr(self, "watermark_pos_combo"):
                self.config["watermark"]["position"] = self.watermark_pos_combo.currentData() or "bottom_right"
            if hasattr(self, "watermark_color_combo"):
                self.config["watermark"]["color"] = self.watermark_color_combo.currentData() or "#ffffff"
            if hasattr(self, "watermark_stroke_cb"):
                self.config["watermark"]["stroke"] = self.watermark_stroke_cb.isChecked()

            # 2. 打码参数
            if hasattr(self, "censor_style_combo"):
                style_keys = ["black_bar", "mosaic", "blur"]
                idx = self.censor_style_combo.currentIndex()
                if 0 <= idx < len(style_keys):
                    self.config["censor"]["style"] = style_keys[idx]
            if hasattr(self, "censor_mode_combo"):
                mode_keys = ["slender", "moderate", "full"]
                idx = self.censor_mode_combo.currentIndex()
                if 0 <= idx < len(mode_keys):
                    self.config["censor"]["coverage_mode"] = mode_keys[idx]
            if hasattr(self, "censor_slider"):
                self.config["censor"]["coverage_scale"] = self.censor_slider.value() / 100.0

            # 3. 基础默认开关
            if hasattr(self, "ai_checkbox"):
                self.config["defaults"]["is_ai_generated"] = self.ai_checkbox.isChecked()
            if hasattr(self, "nsfw_checkbox"):
                self.config["defaults"]["x_sensitive"] = self.nsfw_checkbox.isChecked()

            save_config(self.config)
        except Exception as e:
            logger.warning(f"[Config] 同步界面配置失败: {e}")

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_vlayout = QVBoxLayout(central)
        root_vlayout.setContentsMargins(10, 10, 10, 6)
        root_vlayout.setSpacing(8)

        # 核心主体：三栏黄金分割 Splitter
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.splitterMoved.connect(lambda pos, idx: self._schedule_layout_save())
        root_vlayout.addWidget(self.main_splitter, 1)

        # ==================== 第 1 栏 (左侧)：统一内容与图像保护控制 ====================
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_content = QWidget()
        left_content.setStyleSheet("background-color: #121218;")
        left_layout = QVBoxLayout(left_content)
        left_layout.setContentsMargins(4, 4, 10, 4)
        left_layout.setSpacing(12)

        # 1. 基础信息
        meta_group = QGroupBox("1. 创作基础信息与合规开关")
        meta_layout = QVBoxLayout(meta_group)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("作品标题 (Title)...")
        meta_layout.addWidget(QLabel("作品标题:"))
        meta_layout.addWidget(self.title_input)

        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText("通用标签 (以逗号或空格分隔，如: oc, anime, 修仙)...")
        meta_layout.addWidget(QLabel("通用标签:"))
        meta_layout.addWidget(self.tags_input)

        # 全局开关：NSFW 与 AI 创作
        toggle_layout = QVBoxLayout()
        self.nsfw_checkbox = QCheckBox("🔥 包含成人/敏感内容 (NSFW - 自动配置各平台分级)")
        self.nsfw_checkbox.stateChanged.connect(self._on_nsfw_toggled)
        toggle_layout.addWidget(self.nsfw_checkbox)

        self.ai_checkbox = QCheckBox("🤖 本作为 AI 工具创作 (严防封号，各平台自动同步勾选 AI 标识)")
        self.ai_checkbox.setChecked(self.config["defaults"]["is_ai_generated"])
        self.ai_checkbox.stateChanged.connect(self._on_master_ai_toggled)
        toggle_layout.addWidget(self.ai_checkbox)
        meta_layout.addLayout(toggle_layout)
        left_layout.addWidget(meta_group)

        # 2. 正文描述
        desc_group = QGroupBox("2. 正文描述与赞助引流")
        desc_layout = QVBoxLayout(desc_group)
        self.desc_input = QTextEdit()
        self.desc_input.setPlaceholderText("输入作品描述、剧情背景、世界观或想对粉丝说的话...")
        self.desc_input.setMaximumHeight(110)
        desc_layout.addWidget(self.desc_input)

        sig_layout = QHBoxLayout()
        append_sig_btn = QPushButton("一键追加赞助引流尾巴 (Patreon/Ko-fi)")
        append_sig_btn.setObjectName("secondaryBtn")
        append_sig_btn.clicked.connect(self._append_signature)
        sig_layout.addWidget(append_sig_btn)

        sig_layout.addStretch()
        self.x_char_counter_lbl = QLabel("X (Twitter): 0 / 280")
        self.x_char_counter_lbl.setStyleSheet("color: #9ca3af; font-size: 13px; font-weight: bold;")
        sig_layout.addWidget(self.x_char_counter_lbl)

        desc_layout.addLayout(sig_layout)
        left_layout.addWidget(desc_group)

        # X (Twitter) 实时字数统计防抖定时器 (80ms，避免输入打字卡顿)
        self.x_counter_timer = QTimer(self)
        self.x_counter_timer.setSingleShot(True)
        self.x_counter_timer.setInterval(80)
        self.x_counter_timer.timeout.connect(self._update_x_char_counter)

        self.title_input.textChanged.connect(lambda: self.x_counter_timer.start())
        self.desc_input.textChanged.connect(lambda: self.x_counter_timer.start())
        self.title_input.textChanged.connect(lambda: self._draft_save_timer.start())
        self.tags_input.textChanged.connect(lambda: self._draft_save_timer.start())
        self.desc_input.textChanged.connect(lambda: self._draft_save_timer.start())

        # 3. 素材与打码/水印保护控制
        media_group = QGroupBox("3. 图片素材与打码/水印保护设置")
        media_layout = QVBoxLayout(media_group)

        btn_row = QHBoxLayout()
        add_img_btn = QPushButton("📁 导入作品图片")
        add_img_btn.setObjectName("accentBtn")
        add_img_btn.clicked.connect(self._select_images)
        btn_row.addWidget(add_img_btn)

        clear_img_btn = QPushButton("清空图片")
        clear_img_btn.setObjectName("secondaryBtn")
        clear_img_btn.clicked.connect(self._clear_images)
        btn_row.addWidget(clear_img_btn)
        media_layout.addLayout(btn_row)

        self.img_list_widget = QListWidget()
        self.img_list_widget.setMaximumHeight(85)
        self.img_list_widget.currentRowChanged.connect(self._on_image_selection_changed)
        media_layout.addWidget(self.img_list_widget)

        # 3.1 打码控制卡片
        censor_box = QFrame()
        censor_box.setStyleSheet("background-color: #181822; border: 1px solid #2e2e3e; border-radius: 8px; padding: 8px;")
        censor_vlayout = QVBoxLayout(censor_box)

        # 行 1: 打码开关 + 样式
        c_row1 = QHBoxLayout()
        self.auto_censor_cb = QCheckBox("🛡️ 启用敏感部位打码 (公开发布端生效)")
        self.auto_censor_cb.setChecked(True)
        self.auto_censor_cb.stateChanged.connect(self._schedule_preview_refresh)
        c_row1.addWidget(self.auto_censor_cb)

        c_row1.addWidget(QLabel("样式:"))
        self.censor_style_combo = QComboBox()
        self.censor_style_combo.addItems(["纯黑条 (Black Bar)", "经典马赛克 (Mosaic)", "高斯模糊 (Blur)"])
        self.censor_style_combo.currentIndexChanged.connect(self._schedule_preview_refresh)
        c_row1.addWidget(self.censor_style_combo)
        censor_vlayout.addLayout(c_row1)

        # 行 2: 遮挡模式 + 尺寸滑块
        c_row2 = QHBoxLayout()
        c_row2.addWidget(QLabel("模式:"))
        self.censor_mode_combo = QComboBox()
        self.censor_mode_combo.addItems([
            "适中局部 (标准推荐)",
            "轻量细条 (极简保留90%)",
            "严实全遮 (100%覆盖)"
        ])
        self.censor_mode_combo.currentIndexChanged.connect(self._on_censor_mode_changed)
        c_row2.addWidget(self.censor_mode_combo)

        c_row2.addWidget(QLabel("尺寸:"))
        self.censor_slider = QSlider(Qt.Horizontal)
        self.censor_slider.setRange(20, 120)
        self.censor_slider.setValue(55)
        self.censor_slider.valueChanged.connect(self._on_censor_slider_changed)
        c_row2.addWidget(self.censor_slider)

        self.censor_ratio_label = QLabel("55%")
        self.censor_ratio_label.setMinimumWidth(38)
        self.censor_ratio_label.setStyleSheet("color: #818cf8; font-weight: bold;")
        c_row2.addWidget(self.censor_ratio_label)
        censor_vlayout.addLayout(c_row2)

        # 行 3: 交互式打码操作栏 (直接在中间预览框交互)
        c_row3 = QHBoxLayout()
        self.clear_censor_btn = QPushButton("🧹 清除打码框")
        self.clear_censor_btn.setObjectName("secondaryBtn")
        self.clear_censor_btn.clicked.connect(self._clear_current_censor_boxes)
        c_row3.addWidget(self.clear_censor_btn)

        self.reset_censor_btn = QPushButton("🤖 恢复 AI 识别")
        self.reset_censor_btn.setObjectName("secondaryBtn")
        self.reset_censor_btn.clicked.connect(self._reset_current_censor_boxes)
        c_row3.addWidget(self.reset_censor_btn)
        censor_vlayout.addLayout(c_row3)

        tip_censor_lbl = QLabel("💡 提示: 在中央预览框中直接鼠标拖动可挪动位置，拖动8个锚点调整大小，空白处拖拽新增，选中后按 Delete 删除。")
        tip_censor_lbl.setWordWrap(True)
        tip_censor_lbl.setStyleSheet("color: #94a3b8; font-size: 12px; margin-top: 2px;")
        censor_vlayout.addWidget(tip_censor_lbl)
        media_layout.addWidget(censor_box)

        # 3.2 水印保护卡片
        wm_config = self.config.get("watermark", {})
        wm_box = QFrame()
        wm_box.setStyleSheet("background-color: #181822; border: 1px solid #2e2e3e; border-radius: 8px; padding: 8px; margin-top: 6px;")
        wm_vlayout = QVBoxLayout(wm_box)

        # 行 1: 水印开关 + 发布应用开关
        w_row1 = QHBoxLayout()
        self.watermark_enabled_cb = QCheckBox("💧 启用文字水印 (Watermark)")
        self.watermark_enabled_cb.setChecked(wm_config.get("enabled", False))
        self.watermark_enabled_cb.stateChanged.connect(self._schedule_preview_refresh)
        w_row1.addWidget(self.watermark_enabled_cb)

        self.watermark_apply_publish_cb = QCheckBox("发布时自动应用水印")
        self.watermark_apply_publish_cb.setChecked(wm_config.get("apply_to_publish", True))
        w_row1.addWidget(self.watermark_apply_publish_cb)
        wm_vlayout.addLayout(w_row1)

        # 行 2: 水印文字输入
        w_row2 = QHBoxLayout()
        w_row2.addWidget(QLabel("水印文字:"))
        self.watermark_text_input = QLineEdit()
        self.watermark_text_input.setText(wm_config.get("text", "@FOXTONTONTON"))
        self.watermark_text_input.setPlaceholderText("输入水印签名，如 @FOXTONTONTON ...")
        self.watermark_text_input.textChanged.connect(self._schedule_preview_refresh)
        w_row2.addWidget(self.watermark_text_input)
        wm_vlayout.addLayout(w_row2)

        # 行 3: 字体选择 (精选常用艺术字体 + 系统全量字体)
        w_row3 = QHBoxLayout()
        w_row3.addWidget(QLabel("选择字体:"))
        self.watermark_font_combo = QComboBox()
        available_fonts = self.watermark_engine.get_available_font_names()
        self.watermark_font_combo.addItems(available_fonts)
        default_font = wm_config.get("font_family", "微软雅黑 (Microsoft YaHei)")
        font_idx = self.watermark_font_combo.findText(default_font)
        if font_idx >= 0:
            self.watermark_font_combo.setCurrentIndex(font_idx)
        self.watermark_font_combo.currentIndexChanged.connect(self._schedule_preview_refresh)
        w_row3.addWidget(self.watermark_font_combo)
        wm_vlayout.addLayout(w_row3)

        # 行 4: 字号滑块 + 透明度滑块
        w_row4 = QHBoxLayout()
        w_row4.addWidget(QLabel("字号:"))
        self.watermark_size_slider = QSlider(Qt.Horizontal)
        self.watermark_size_slider.setRange(1, 120)
        self.watermark_size_slider.setValue(wm_config.get("font_size", 38))
        self.watermark_size_slider.valueChanged.connect(self._on_watermark_size_changed)
        w_row4.addWidget(self.watermark_size_slider)

        self.watermark_size_lbl = QLabel(f"{self.watermark_size_slider.value()}px")
        self.watermark_size_lbl.setMinimumWidth(38)
        self.watermark_size_lbl.setStyleSheet("color: #06b6d4; font-weight: bold;")
        w_row4.addWidget(self.watermark_size_lbl)

        w_row4.addWidget(QLabel("不透明度:"))
        self.watermark_opacity_slider = QSlider(Qt.Horizontal)
        self.watermark_opacity_slider.setRange(10, 100)
        self.watermark_opacity_slider.setValue(int(wm_config.get("opacity", 0.65) * 100))
        self.watermark_opacity_slider.valueChanged.connect(self._on_watermark_opacity_changed)
        w_row4.addWidget(self.watermark_opacity_slider)

        self.watermark_opacity_lbl = QLabel(f"{self.watermark_opacity_slider.value()}%")
        self.watermark_opacity_lbl.setMinimumWidth(38)
        self.watermark_opacity_lbl.setStyleSheet("color: #06b6d4; font-weight: bold;")
        w_row4.addWidget(self.watermark_opacity_lbl)
        wm_vlayout.addLayout(w_row4)

        # 行 5: 水印位置 + 颜色 + 描边
        w_row5 = QHBoxLayout()
        w_row5.addWidget(QLabel("位置:"))
        self.watermark_pos_combo = QComboBox()
        self.watermark_pos_combo.addItem("右下角 (经典防盗标)", "bottom_right")
        self.watermark_pos_combo.addItem("左下角", "bottom_left")
        self.watermark_pos_combo.addItem("右上角", "top_right")
        self.watermark_pos_combo.addItem("左上角", "top_left")
        self.watermark_pos_combo.addItem("正中央", "center")
        self.watermark_pos_combo.addItem("底部居中", "bottom_center")
        self.watermark_pos_combo.addItem("全图倾斜平铺 (防盗图推荐)", "tiled")
        default_pos = wm_config.get("position", "bottom_right")
        for i in range(self.watermark_pos_combo.count()):
            if self.watermark_pos_combo.itemData(i) == default_pos:
                self.watermark_pos_combo.setCurrentIndex(i)
                break
        self.watermark_pos_combo.currentIndexChanged.connect(self._schedule_preview_refresh)
        w_row5.addWidget(self.watermark_pos_combo)

        w_row5.addWidget(QLabel("颜色:"))
        self.watermark_color_combo = QComboBox()
        self.watermark_color_combo.addItem("纯白 (#FFFFFF)", "#ffffff")
        self.watermark_color_combo.addItem("暖金 (#F59E0B)", "#f59e0b")
        self.watermark_color_combo.addItem("科技青 (#06B6D4)", "#06b6d4")
        self.watermark_color_combo.addItem("炭黑 (#1F2937)", "#1f2937")
        self.watermark_color_combo.addItem("浅灰 (#D1D5DB)", "#d1d5db")
        default_color = wm_config.get("color", "#ffffff")
        for i in range(self.watermark_color_combo.count()):
            if self.watermark_color_combo.itemData(i) == default_color:
                self.watermark_color_combo.setCurrentIndex(i)
                break
        self.watermark_color_combo.currentIndexChanged.connect(self._schedule_preview_refresh)
        w_row5.addWidget(self.watermark_color_combo)

        self.watermark_stroke_cb = QCheckBox("高对比防混色描边")
        self.watermark_stroke_cb.setChecked(wm_config.get("stroke", True))
        self.watermark_stroke_cb.stateChanged.connect(self._schedule_preview_refresh)
        w_row5.addWidget(self.watermark_stroke_cb)
        wm_vlayout.addLayout(w_row5)

        media_layout.addWidget(wm_box)
        left_layout.addWidget(media_group)

        left_scroll.setWidget(left_content)
        self.main_splitter.addWidget(left_scroll)

        # ==================== 第 2 栏 (中间)：综合效果实时预览框 ====================
        center_widget = QWidget()
        center_widget.setStyleSheet("background-color: #121218;")
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(4, 0, 4, 0)
        center_layout.setSpacing(8)

        preview_group = QGroupBox("🖼️ 综合效果实时预览框 (打码与水印双重同框合成)")
        preview_vlayout = QVBoxLayout(preview_group)
        preview_vlayout.setContentsMargins(10, 14, 10, 10)
        preview_vlayout.setSpacing(8)

        # 预览顶部工具栏
        p_top_bar = QHBoxLayout()
        self.preview_info_lbl = QLabel("当前未选择图片")
        self.preview_info_lbl.setStyleSheet("color: #a5b4fc; font-weight: bold; font-size: 14px;")
        p_top_bar.addWidget(self.preview_info_lbl, 1)

        self.prev_img_btn = QPushButton("◀ 上一张")
        self.prev_img_btn.setObjectName("secondaryBtn")
        self.prev_img_btn.clicked.connect(self._prev_preview_image)
        p_top_bar.addWidget(self.prev_img_btn)

        self.next_img_btn = QPushButton("下一张 ▶")
        self.next_img_btn.setObjectName("secondaryBtn")
        self.next_img_btn.clicked.connect(self._next_preview_image)
        p_top_bar.addWidget(self.next_img_btn)

        refresh_preview_btn = QPushButton("🔄 立即刷新合成")
        refresh_preview_btn.setObjectName("accentBtn")
        refresh_preview_btn.clicked.connect(lambda: self._schedule_preview_refresh(instant=True))
        p_top_bar.addWidget(refresh_preview_btn)

        export_preview_btn = QPushButton("💾 导出预览图")
        export_preview_btn.setObjectName("secondaryBtn")
        export_preview_btn.clicked.connect(self._export_preview_image)
        p_top_bar.addWidget(export_preview_btn)

        preview_vlayout.addLayout(p_top_bar)

        # 核心主画布 (自适应保持宽高比，抗锯齿高质量渲染)
        self.preview_canvas = UnifiedPreviewCanvas()
        self.preview_canvas.boxes_changed.connect(self._on_canvas_boxes_changed)
        preview_vlayout.addWidget(self.preview_canvas, 1)

        # 预览底部参数指示条
        self.preview_status_lbl = QLabel("🛡️ 打码: 未开启  |  💧 水印: 未开启")
        self.preview_status_lbl.setStyleSheet("color: #9ca3af; font-size: 13px; padding-top: 4px;")
        preview_vlayout.addWidget(self.preview_status_lbl)

        center_layout.addWidget(preview_group)
        self.main_splitter.addWidget(center_widget)

        # ==================== 第 3 栏 (右侧)：目标平台矩阵与发布控制 ====================
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_content = QWidget()
        right_content.setStyleSheet("background-color: #121218;")
        right_layout = QVBoxLayout(right_content)
        right_layout.setContentsMargins(10, 4, 4, 4)
        right_layout.setSpacing(12)

        plat_group = QGroupBox("4. 目标平台矩阵设置 (显示登录身份与合规项)")
        plat_layout = QVBoxLayout(plat_group)

        self.platform_controls = {}
        for disp in self.dispatchers:
            card = self._create_platform_card(disp)
            plat_layout.addWidget(card)

        right_layout.addWidget(plat_group)

        # 5. 发布控制台
        exec_group = QGroupBox("5. 发布控制台")
        exec_layout = QVBoxLayout(exec_group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        exec_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("就绪 - 点击下方按钮开始全平台分发")
        self.status_label.setStyleSheet("color: #9ca3af; font-size: 14px;")
        exec_layout.addWidget(self.status_label)

        # 内嵌式 X (Twitter) 字数超限确认条 (严格遵守红线 #8: 严禁弹窗 QMessageBox)
        self.x_limit_confirm_widget = QFrame()
        self.x_limit_confirm_widget.setStyleSheet(
            "background-color: #241612; border: 1px solid #f59e0b; border-radius: 8px; padding: 10px; margin-top: 6px; margin-bottom: 6px;"
        )
        x_confirm_layout = QVBoxLayout(self.x_limit_confirm_widget)
        x_confirm_layout.setContentsMargins(8, 8, 8, 8)
        x_confirm_layout.setSpacing(8)

        x_confirm_title = QLabel("⚠️ X (Twitter) 文本字数超限决策")
        x_confirm_title.setStyleSheet("color: #f59e0b; font-weight: bold; font-size: 14px;")
        x_confirm_layout.addWidget(x_confirm_title)

        self.x_limit_msg_lbl = QLabel()
        self.x_limit_msg_lbl.setStyleSheet("color: #e2e8f0; font-size: 13px; line-height: 1.4;")
        self.x_limit_msg_lbl.setWordWrap(True)
        x_confirm_layout.addWidget(self.x_limit_msg_lbl)

        x_confirm_btns = QHBoxLayout()
        x_confirm_btns.setSpacing(6)

        btn_x_trunc = QPushButton("✂️ 截断发布")
        btn_x_trunc.setStyleSheet("background-color: #d97706; color: #ffffff; font-weight: bold; padding: 6px 10px; border-radius: 6px;")
        btn_x_trunc.clicked.connect(lambda: self._proceed_publishing_with_x_action("truncate"))
        x_confirm_btns.addWidget(btn_x_trunc)

        btn_x_thread = QPushButton("🧵 拆成串推")
        btn_x_thread.setStyleSheet("background-color: #0284c7; color: #ffffff; font-weight: bold; padding: 6px 10px; border-radius: 6px;")
        btn_x_thread.clicked.connect(lambda: self._proceed_publishing_with_x_action("thread"))
        x_confirm_btns.addWidget(btn_x_thread)

        btn_x_cancel = QPushButton("❌ 取消 X 发布")
        btn_x_cancel.setStyleSheet("background-color: #374151; color: #e2e8f0; padding: 6px 10px; border-radius: 6px;")
        btn_x_cancel.clicked.connect(self._cancel_x_publish_and_proceed)
        x_confirm_btns.addWidget(btn_x_cancel)

        x_confirm_layout.addLayout(x_confirm_btns)
        self.x_limit_confirm_widget.hide()
        exec_layout.addWidget(self.x_limit_confirm_widget)

        # 发布与体检按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.publish_btn = QPushButton("🚀 一键全平台同步发布")
        self.publish_btn.setObjectName("publishBtn")
        self.publish_btn.clicked.connect(self._start_publishing)
        btn_row.addWidget(self.publish_btn, stretch=3)

        self.diagnose_btn = QPushButton("🔍 一键自检")
        self.diagnose_btn.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: #ffffff;
                font-weight: bold;
                padding: 10px 12px;
                border-radius: 6px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:disabled {
                background-color: #374151;
                color: #9ca3af;
            }
        """)
        self.diagnose_btn.clicked.connect(self._run_diagnostics)
        btn_row.addWidget(self.diagnose_btn, stretch=1)

        exec_layout.addLayout(btn_row)

        self.retry_btn = QPushButton("🔄 重试失败项")
        self.retry_btn.setObjectName("secondaryBtn")
        self.retry_btn.setStyleSheet("""
            QPushButton {
                background-color: #d97706;
                color: #ffffff;
                font-weight: bold;
                padding: 10px;
                border-radius: 6px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #b45309;
            }
        """)
        self.retry_btn.hide()
        self.retry_btn.clicked.connect(self._retry_failed_publishing)
        exec_layout.addWidget(self.retry_btn)

        right_layout.addWidget(exec_group)

        # ==================== 嵌入式诊断报告面板 (需求四，绝对禁止 QMessageBox) ====================
        self.diag_panel = QFrame()
        self.diag_panel.setObjectName("diagPanel")
        self.diag_panel.setStyleSheet("""
            #diagPanel {
                background-color: #1a1a26;
                border: 1px solid #3b82f6;
                border-radius: 8px;
                padding: 8px;
                margin-top: 10px;
            }
        """)
        diag_vbox = QVBoxLayout(self.diag_panel)
        diag_vbox.setContentsMargins(8, 8, 8, 8)
        diag_vbox.setSpacing(8)

        diag_header = QHBoxLayout()
        self.diag_title_lbl = QLabel("📋 <b>系统健康与全平台自检报告</b>")
        self.diag_title_lbl.setStyleSheet("color: #60a5fa; font-size: 14px;")
        diag_header.addWidget(self.diag_title_lbl)
        diag_header.addStretch()

        self.diag_export_btn = QPushButton("📄 导出报告 (.md)")
        self.diag_export_btn.setStyleSheet("background-color: #2563eb; color: #ffffff; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: bold;")
        self.diag_export_btn.clicked.connect(self._open_diagnostic_report_file)
        diag_header.addWidget(self.diag_export_btn)

        self.diag_close_btn = QPushButton("✕ 收起")
        self.diag_close_btn.setStyleSheet("background-color: #374151; color: #e2e8f0; padding: 4px 8px; border-radius: 4px; font-size: 12px;")
        self.diag_close_btn.clicked.connect(lambda: self.diag_panel.hide())
        diag_header.addWidget(self.diag_close_btn)
        diag_vbox.addLayout(diag_header)

        self.diag_summary_lbl = QLabel("")
        self.diag_summary_lbl.setStyleSheet("color: #cbd5e1; font-size: 13px;")
        diag_vbox.addWidget(self.diag_summary_lbl)

        self.diag_text_browser = QTextBrowser()
        self.diag_text_browser.setOpenExternalLinks(True)
        self.diag_text_browser.setStyleSheet("""
            QTextBrowser {
                background-color: #121218;
                border: 1px solid #2d2d3d;
                border-radius: 6px;
                color: #e2e8f0;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                font-size: 13px;
                padding: 8px;
            }
        """)
        self.diag_text_browser.setMinimumHeight(240)
        diag_vbox.addWidget(self.diag_text_browser)

        self.diag_panel.hide()
        right_layout.addWidget(self.diag_panel)

        right_layout.addStretch()

        right_scroll.setWidget(right_content)
        self.main_splitter.addWidget(right_scroll)

        # 三栏初始默认宽度比例分配 (左 430px, 中 780px, 右 450px)
        self.main_splitter.setSizes([430, 780, 450])

        # ==================== 底部嵌入式全局通知栏与事件日志 ====================
        self.notification_bar = NotificationBar()
        root_vlayout.addWidget(self.notification_bar)

        # 初始化 X 字符统计状态
        self._update_x_char_counter()

    def _create_platform_card(self, disp):
        card = QFrame()
        card.setStyleSheet("background-color: #181822; border: 1px solid #2e2e3e; border-radius: 8px; margin-bottom: 6px; padding: 6px;")
        layout = QVBoxLayout(card)

        # 头部：开关 + 头像 + 用户名 + 状态灯 + 登录按钮
        header = QHBoxLayout()

        cb = QCheckBox(disp.display_name)
        cb.setChecked(True)
        cb.setStyleSheet("font-weight: bold; font-size: 15px;")
        header.addWidget(cb)

        avatar_lbl = QLabel()
        avatar_lbl.setFixedSize(36, 36)
        avatar_lbl.setStyleSheet("border-radius: 18px; background-color: #242432; border: 1px solid #374151;")
        avatar_lbl.setAlignment(Qt.AlignCenter)
        header.addWidget(avatar_lbl)

        profile = disp.get_profile()
        self._render_avatar(avatar_lbl, profile.get("avatar"))

        user_info_layout = QVBoxLayout()
        username_lbl = QLabel(profile.get("username", "未登录"))
        username_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #e2e8f0;")
        
        status_text = "● 已就绪" if profile.get("logged_in") else "○ 未连接"
        status_color = "#10b981" if profile.get("logged_in") else "#9ca3af"
        status_lbl = QLabel(status_text)
        status_lbl.setStyleSheet(f"color: {status_color}; font-size: 12px;")

        user_info_layout.addWidget(username_lbl)
        user_info_layout.addWidget(status_lbl)
        header.addLayout(user_info_layout)
        header.addStretch()

        login_btn = QPushButton("登录/更新Cookie")
        login_btn.setObjectName("secondaryBtn")
        login_btn.clicked.connect(lambda: self._handle_platform_login(disp, status_lbl, username_lbl, avatar_lbl))
        header.addWidget(login_btn)

        layout.addLayout(header)

        # 平台专有选项行
        opts_layout = QHBoxLayout()
        opts = {}

        if disp.name == "x_twitter":
            x_box = QVBoxLayout()
            x_box.setSpacing(6)
            x_row1 = QHBoxLayout()
            x_row2 = QHBoxLayout()

            x_sens = QCheckBox("原生敏感警告 (Nudity 打标)")
            x_sens.setChecked(self.config["defaults"].get("x_sensitive", True))
            x_row1.addWidget(x_sens)
            opts["sensitive"] = x_sens

            x_ai = QCheckBox("官方「Made with AI」披露勾选")
            x_ai.setChecked(True)
            x_row1.addWidget(x_ai)
            opts["ai"] = x_ai

            x_prem = QCheckBox("✨ X Premium 账号 (上限 25,000 字符)")
            x_prem.setChecked(self.config["defaults"].get("x_premium", False))
            x_prem.stateChanged.connect(self._on_x_premium_toggled)
            x_row2.addWidget(x_prem)
            opts["premium"] = x_prem

            x_policy_combo = QComboBox()
            x_policy_combo.addItem("超限: 每次询问", "ask")
            x_policy_combo.addItem("超限: 自动截断", "truncate")
            x_policy_combo.addItem("超限: 自动串推", "thread")
            cur_policy = self.config["defaults"].get("x_over_limit_policy", "ask")
            for p_idx in range(x_policy_combo.count()):
                if x_policy_combo.itemData(p_idx) == cur_policy:
                    x_policy_combo.setCurrentIndex(p_idx)
                    break
            x_policy_combo.currentIndexChanged.connect(self._on_x_policy_changed)
            x_row2.addWidget(x_policy_combo)
            opts["policy"] = x_policy_combo

            x_box.addLayout(x_row1)
            x_box.addLayout(x_row2)
            opts_layout.addLayout(x_box)

        elif disp.name == "deviantart":
            opts_layout.addWidget(QLabel("Mature:"))
            da_combo = QComboBox()
            da_combo.addItems(["Strict (严格成人)", "Moderate (轻度)", "None (全年龄)"])
            opts_layout.addWidget(da_combo)
            opts["mature"] = da_combo

            da_ai = QCheckBox("Created using AI tools")
            da_ai.setChecked(True)
            opts_layout.addWidget(da_ai)
            opts["ai"] = da_ai

        elif disp.name == "patreon":
            opts_layout.addWidget(QLabel("可见范围:"))
            pat_combo = QComboBox()
            pat_combo.addItems(["仅赞助者 (Patrons Only)", "公开 (Public)", "指定档位 (Tiers)"])
            opts_layout.addWidget(pat_combo)
            opts["visibility"] = pat_combo

            pat_ai = QCheckBox("AI 内容披露")
            pat_ai.setChecked(True)
            opts_layout.addWidget(pat_ai)
            opts["ai"] = pat_ai

        elif disp.name == "kofi":
            opts_layout.addWidget(QLabel("可见范围:"))
            kofi_combo = QComboBox()
            kofi_combo.addItems(["仅打赏/会员 (Supporters)", "所有人公开 (Everyone)", "会员专属档位"])
            opts_layout.addWidget(kofi_combo)
            opts["visibility"] = kofi_combo

            kofi_ai = QCheckBox("AI 创作成品说明")
            kofi_ai.setChecked(True)
            opts_layout.addWidget(kofi_ai)
            opts["ai"] = kofi_ai

        layout.addLayout(opts_layout)

        self.platform_controls[disp.name] = {
            "checkbox": cb,
            "dispatcher": disp,
            "options": opts,
            "status_lbl": status_lbl,
            "username_lbl": username_lbl,
            "avatar_lbl": avatar_lbl
        }
        return card

    def _render_avatar(self, label, avatar_path):
        if avatar_path and Path(avatar_path).exists():
            pix = QPixmap(str(avatar_path))
            if not pix.isNull():
                label.setPixmap(pix.scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        label.setText("👤")
        label.setStyleSheet("border-radius: 18px; background-color: #374151; font-size: 16px;")

    def _on_master_ai_toggled(self, state):
        is_ai = (state == Qt.Checked)
        for ctrl in self.platform_controls.values():
            if "ai" in ctrl["options"]:
                ctrl["options"]["ai"].setChecked(is_ai)
        self.notify(f"已同步更新全平台 AI 创作标识为: {'启用' if is_ai else '禁用'}", level="info")

    def _on_nsfw_toggled(self, state):
        is_nsfw = (state == Qt.Checked)
        if "x_twitter" in self.platform_controls and "sensitive" in self.platform_controls["x_twitter"]["options"]:
            self.platform_controls["x_twitter"]["options"]["sensitive"].setChecked(is_nsfw)
        if "deviantart" in self.platform_controls and "mature" in self.platform_controls["deviantart"]["options"]:
            self.platform_controls["deviantart"]["options"]["mature"].setCurrentIndex(0 if is_nsfw else 2)
        self.notify(f"已同步更新各平台敏感/分级设定为: {'成人/敏感' if is_nsfw else '全年龄'}", level="info")

    def _get_x_char_limit(self):
        if hasattr(self, "platform_controls") and "x_twitter" in self.platform_controls:
            prem_cb = self.platform_controls["x_twitter"]["options"].get("premium")
            if prem_cb and prem_cb.isChecked():
                return 25000
        return 25000 if self.config["defaults"].get("x_premium", False) else 280

    def _update_x_char_counter(self):
        if not hasattr(self, "x_char_counter_lbl"):
            return
        title = self.title_input.text().strip()
        desc = self.desc_input.toPlainText().strip()
        full_text = f"{title}\n\n{desc}".strip() if title else desc.strip()
        count = calculate_x_char_count(full_text)
        limit = self._get_x_char_limit()

        ratio = count / max(1, limit)
        if count > limit:
            color = "#ef4444"
            tip = f" (超限 {count - limit} 字)"
        elif ratio >= 0.9:
            color = "#fbbf24"
            tip = " (接近上限)"
        else:
            color = "#9ca3af"
            tip = ""

        self.x_char_counter_lbl.setText(f"X 字数: <font color='{color}'><b>{count} / {limit}</b></font>{tip}")

        # 若当前内嵌确认条显示但文本已被用户精简到限额内，自动隐藏确认条并恢复发布按钮
        if hasattr(self, "x_limit_confirm_widget") and self.x_limit_confirm_widget.isVisible() and count <= limit:
            self.x_limit_confirm_widget.hide()
            self.publish_btn.setEnabled(True)

    def _on_x_premium_toggled(self, state):
        is_prem = (state == Qt.Checked)
        self.config["defaults"]["x_premium"] = is_prem
        save_config(self.config)
        self._update_x_char_counter()
        desc = "25,000 字符 (✨ Premium)" if is_prem else "280 字符 (普通)"
        self.notify(f"X (Twitter) 字符上限已设置为: {desc}", level="info")

    def _on_x_policy_changed(self, idx):
        if "x_twitter" in self.platform_controls and "policy" in self.platform_controls["x_twitter"]["options"]:
            policy = self.platform_controls["x_twitter"]["options"]["policy"].currentData()
            self.config["defaults"]["x_over_limit_policy"] = policy
            save_config(self.config)
            p_map = {"ask": "每次询问", "truncate": "自动截断", "thread": "自动串推"}
            self.notify(f"已保存 X 默认超限策略为: {p_map.get(policy, policy)}", level="info")

    def _proceed_publishing_with_x_action(self, action):
        self.x_limit_confirm_widget.hide()
        self._pending_x_action = action
        self.publish_btn.setEnabled(True)
        self._start_publishing()

    def _cancel_x_publish_and_proceed(self):
        self.x_limit_confirm_widget.hide()
        if hasattr(self, "_pending_x_action"):
            del self._pending_x_action

        # 取消勾选 X 平台
        if "x_twitter" in self.platform_controls:
            self.platform_controls["x_twitter"]["checkbox"].setChecked(False)

        other_selected = [
            ctrl["dispatcher"] for name, ctrl in self.platform_controls.items()
            if ctrl["checkbox"].isChecked() and name != "x_twitter"
        ]

        if other_selected:
            self.publish_btn.setEnabled(True)
            self.notify("已取消本次 X (Twitter) 发布，正在继续分发其余平台...", level="info")
            self._start_publishing()
        else:
            self.publish_btn.setEnabled(True)
            self.notify("已取消本次发布，请在正文框精简文本后再试。", level="info")

    def _on_censor_mode_changed(self, idx):
        if idx == 0:
            self.censor_slider.setValue(55)
        elif idx == 1:
            self.censor_slider.setValue(30)
        elif idx == 2:
            self.censor_slider.setValue(100)
        self._schedule_preview_refresh()

    def _on_censor_slider_changed(self, val):
        self.censor_ratio_label.setText(f"{val}%")
        self._schedule_preview_refresh()

    def _on_watermark_size_changed(self, val):
        self.watermark_size_lbl.setText(f"{val}px")
        self._schedule_preview_refresh()

    def _on_watermark_opacity_changed(self, val):
        self.watermark_opacity_lbl.setText(f"{val}%")
        self._schedule_preview_refresh()

    def _get_current_censor_params(self):
        styles = ["black_bar", "mosaic", "blur"]
        modes = ["moderate", "slender", "full"]
        return (
            styles[self.censor_style_combo.currentIndex()],
            modes[self.censor_mode_combo.currentIndex()],
            self.censor_slider.value() / 100.0
        )

    def _schedule_preview_refresh(self, instant=False):
        """调度实时预览渲染（防抖刷新，即时响应用户参数微调）"""
        if instant:
            self._preview_timer.stop()
            self._do_refresh_live_preview()
        else:
            self._preview_timer.start(80)

    def _pil_to_qpixmap(self, pil_img: Image.Image) -> QPixmap:
        """高性能将内存中的 PIL 图像转化为 QPixmap，避免硬盘读写延迟"""
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format_RGBA8888)
        return QPixmap.fromImage(qimg)

    def _do_refresh_live_preview(self):
        """在同一个预览框中统一执行打码与水印双重合成 (支持静态图片与动态 GIF)"""
        if not self.image_paths:
            self.preview_canvas.clear_image()
            self.preview_info_lbl.setText("当前未选择图片")
            self.preview_status_lbl.setText("🛡️ 打码: 未开启  |  💧 水印: 未开启")
            self._current_rendered_pixmap = None
            self._current_rendered_gif_path = None
            return

        if self.current_preview_index >= len(self.image_paths):
            self.current_preview_index = 0
        elif self.current_preview_index < 0:
            self.current_preview_index = 0

        img_path = self.image_paths[self.current_preview_index]
        if not Path(img_path).exists():
            return

        is_gif = img_path.lower().endswith(".gif")

        # ----------------- 针对动态 GIF 动图的高性能硬件级实时预览分支 -----------------
        if is_gif:
            try:
                # 1. 确保画布加载原版 GIF（内部自适应判断：已载入则绝不重复重新播放，0毫秒零开销）
                self.preview_canvas.set_gif(img_path)
                orig_w, orig_h = self.preview_canvas.get_gif_orig_size()

                # 2. 准备动态打码参数 (AI检测首次自动缓存，滑动滑块时纯算术缩放，无需重复推断与重复存盘)
                censor_active = self.auto_censor_cb.isChecked()
                censor_desc = "未开启"
                final_boxes = []
                style = "black_bar"

                if censor_active:
                    style, mode, ratio = self._get_current_censor_params()
                    custom_boxes = self.custom_censor_boxes.get(img_path)
                    if custom_boxes and len(custom_boxes) > 0:
                        base_boxes = custom_boxes
                    else:
                        if not hasattr(self, "_detected_boxes_cache"):
                            self._detected_boxes_cache = {}
                        if img_path not in self._detected_boxes_cache:
                            # 仅对动图第一帧运行一次 AI 识别并缓存
                            try:
                                with Image.open(img_path) as im:
                                    first_fr_bgr = cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2BGR)
                                    self._detected_boxes_cache[img_path] = self.censor_engine.detect_sensitive_regions(None, img_bgr=first_fr_bgr)
                            except Exception as e:
                                logger.debug(f"动图首帧 AI 检测异常: {e}")
                                self._detected_boxes_cache[img_path] = []
                        base_boxes = self._detected_boxes_cache[img_path]

                    w_ref = orig_w if orig_w > 0 else 800
                    h_ref = orig_h if orig_h > 0 else 600
                    # 用户手调框已是最终形态：仅钳制越界，禁止重复应用覆盖模式换算
                    final_boxes = self.censor_engine.resolve_final_boxes(
                        base_boxes, w_ref, h_ref, mode=mode, custom_ratio=ratio,
                        user_customized=bool(custom_boxes))

                    style_map = {"black_bar": "纯黑条", "mosaic": "马赛克", "blur": "高斯模糊"}
                    censor_desc = f"{style_map.get(style, style)} ({int(ratio*100)}%, {len(final_boxes)}处)"

                # 3. 准备动态水印参数
                wm_active = self.watermark_enabled_cb.isChecked()
                wm_desc = "未开启"
                wm_info = {"enabled": False}

                if wm_active:
                    text = self.watermark_text_input.text().strip()
                    font_name = self.watermark_font_combo.currentText()
                    family, is_bold = self.watermark_engine.get_font_family(font_name)
                    font_size = self.watermark_size_slider.value()
                    opacity = self.watermark_opacity_slider.value() / 100.0
                    pos = self.watermark_pos_combo.currentData() or "bottom_right"
                    color_hex = self.watermark_color_combo.currentData() or "#ffffff"
                    enable_stroke = self.watermark_stroke_cb.isChecked()

                    wm_info = {
                        "enabled": True,
                        "text": text,
                        "font_name": font_name,
                        "font_family": family,
                        "is_bold": is_bold,
                        "font_size": font_size,
                        "opacity": opacity,
                        "position": pos,
                        "color_hex": color_hex,
                        "stroke": enable_stroke
                    }
                    wm_desc = f"'{text}' ({self.watermark_pos_combo.currentText()}, {int(opacity*100)}%)"

                # 4. 毫秒级应用实时图层覆盖至播放画布（零磁盘写入，零重编码，60FPS流畅）
                censor_info = {
                    "enabled": censor_active,
                    "style": style,
                    "boxes": final_boxes
                }
                self.preview_canvas.set_censor_boxes(final_boxes, orig_w, orig_h, enabled=censor_active)
                self.preview_canvas.set_gif_overlay(censor_info, wm_info)

                self._current_rendered_pixmap = None
                self._current_rendered_gif_path = None

                total = len(self.image_paths)
                cur = self.current_preview_index + 1
                self.preview_info_lbl.setText(f"当前: {Path(img_path).name} [GIF 动图 {orig_w}×{orig_h}]  ({cur}/{total})")
                self.preview_status_lbl.setText(f"🛡️ 打码: {censor_desc}  |  💧 水印: {wm_desc}  [▶ 动图硬件级极速播放中]")
                return
            except Exception as e:
                self.notify(f"GIF 动图实时合成失败: {e}", level="error")
                return

        # ----------------- 针对常规静态图片的处理分支 -----------------
        try:
            # 1. 第一阶段：打码处理 (若开启)
            censor_active = self.auto_censor_cb.isChecked()
            censor_desc = "未开启"
            boxes = []
            if censor_active:
                style, mode, ratio = self._get_current_censor_params()
                custom_boxes = self.custom_censor_boxes.get(img_path)
                censored_bgr, boxes = self.censor_engine.apply_censor(
                    img_path,
                    output_path=None,
                    style=style,
                    mode=mode,
                    custom_ratio=ratio,
                    custom_boxes=custom_boxes
                )
                img_step = censored_bgr
                style_map = {"black_bar": "纯黑条", "mosaic": "马赛克", "blur": "高斯模糊"}
                censor_desc = f"{style_map.get(style, style)} ({int(ratio*100)}%, {len(boxes)}处)"
            else:
                img_step = img_path

            # 2. 第二阶段：水印处理 (若开启)
            wm_active = self.watermark_enabled_cb.isChecked()
            wm_desc = "未开启"
            if wm_active:
                text = self.watermark_text_input.text().strip()
                font_name = self.watermark_font_combo.currentText()
                font_size = self.watermark_size_slider.value()
                opacity = self.watermark_opacity_slider.value() / 100.0
                pos = self.watermark_pos_combo.currentData() or "bottom_right"
                color_hex = self.watermark_color_combo.currentData() or "#ffffff"
                enable_stroke = self.watermark_stroke_cb.isChecked()

                pil_composite, _ = self.watermark_engine.apply_watermark(
                    img_step,
                    text=text,
                    font_name=font_name,
                    font_size=font_size,
                    opacity=opacity,
                    position=pos,
                    color_hex=color_hex,
                    enable_stroke=enable_stroke
                )
                wm_desc = f"'{text}' ({self.watermark_pos_combo.currentText()}, {int(opacity*100)}%)"
            else:
                if isinstance(img_step, np.ndarray):
                    rgb = cv2.cvtColor(img_step, cv2.COLOR_BGR2RGB)
                    pil_composite = Image.fromarray(rgb)
                else:
                    pil_composite = Image.open(str(img_step)).convert("RGB")

            # 3. 内存直接合成并呈现在中央画布 (同步传递原图尺寸与可交互打码框)
            w, h = pil_composite.size
            pixmap = self._pil_to_qpixmap(pil_composite)
            self._current_rendered_pixmap = pixmap
            self._current_rendered_gif_path = None
            active_censor_boxes = boxes if censor_active else []
            self.preview_canvas.set_image(pixmap, censor_boxes=active_censor_boxes, orig_w=w, orig_h=h, censor_enabled=censor_active)

            # 4. 更新顶部指示与状态栏文本
            w, h = pil_composite.size
            total = len(self.image_paths)
            cur = self.current_preview_index + 1
            self.preview_info_lbl.setText(f"当前: {Path(img_path).name} [{w}×{h}]  ({cur}/{total})")
            self.preview_status_lbl.setText(f"🛡️ 打码: {censor_desc}  |  💧 水印: {wm_desc}")

        except Exception as e:
            self.notify(f"预览合成失败: {e}", level="error")

    def _prev_preview_image(self):
        if self.image_paths and self.current_preview_index > 0:
            self.current_preview_index -= 1
            self.img_list_widget.setCurrentRow(self.current_preview_index)
            self._schedule_preview_refresh(instant=True)

    def _next_preview_image(self):
        if self.image_paths and self.current_preview_index < len(self.image_paths) - 1:
            self.current_preview_index += 1
            self.img_list_widget.setCurrentRow(self.current_preview_index)
            self._schedule_preview_refresh(instant=True)

    def _on_image_selection_changed(self, row):
        if 0 <= row < len(self.image_paths):
            self.current_preview_index = row
            self._schedule_preview_refresh(instant=True)

    def _generate_composite_gif(self, input_gif_path, output_gif_path, censor_params=None, wm_params=None):
        """
        单通道高能效 GIF 动图全帧合成生成器：
        在单次遍历中一并完成打码与水印压制，避免双重 I/O 与重复内存编码，性能提升 200%+
        """
        from PIL import ImageSequence
        import cv2
        import numpy as np

        im = Image.open(str(input_gif_path))
        duration = im.info.get("duration", 100)
        loop = im.info.get("loop", 0)

        censor_boxes = []
        censor_style = "black_bar"
        if censor_params and censor_params.get("enabled"):
            censor_boxes = censor_params.get("boxes", [])
            censor_style = censor_params.get("style", "black_bar")

        wm_enabled = wm_params and wm_params.get("enabled") and bool(wm_params.get("text"))

        out_frames = []
        for f_idx, frame in enumerate(ImageSequence.Iterator(im)):
            fr_rgb = frame.convert("RGB")
            fr_bgr = cv2.cvtColor(np.array(fr_rgb), cv2.COLOR_RGB2BGR)
            h, w = fr_bgr.shape[:2]

            # 1. 压制打码
            if censor_boxes:
                for b in censor_boxes:
                    x1, y1, x2, y2 = b
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(w, x2), min(h, y2)
                    if x2 <= x1 or y2 <= y1:
                        continue
                    roi = fr_bgr[y1:y2, x1:x2]
                    if censor_style == "black_bar":
                        fr_bgr[y1:y2, x1:x2] = (0, 0, 0)
                    elif censor_style == "mosaic":
                        rw, rh = max(1, x2 - x1), max(1, y2 - y1)
                        cell = max(4, rw // 15)
                        small_w = max(1, rw // cell)
                        small_h = max(1, rh // cell)
                        small = cv2.resize(roi, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
                        fr_bgr[y1:y2, x1:x2] = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
                    elif censor_style == "blur":
                        fr_bgr[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (45, 45), 30)

            # 2. 压制水印
            fr_pil = Image.fromarray(cv2.cvtColor(fr_bgr, cv2.COLOR_BGR2RGB))
            if wm_enabled:
                fr_pil, _ = self.watermark_engine.apply_watermark(
                    fr_pil,
                    text=wm_params.get("text", ""),
                    font_name=wm_params.get("font_name", "微软雅黑 (Microsoft YaHei)"),
                    font_size=wm_params.get("font_size", 36),
                    opacity=wm_params.get("opacity", 0.65),
                    position=wm_params.get("position", "bottom_right"),
                    color_hex=wm_params.get("color_hex", "#ffffff"),
                    enable_stroke=wm_params.get("stroke", True)
                )

            out_frames.append(fr_pil.convert("RGB"))

            # 每 10 帧让渡一次 UI 事件循环，防止界面被标记为未响应
            if f_idx % 10 == 0:
                QApplication.processEvents()

        out_p = Path(output_gif_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        if out_frames:
            # 优先保存为高保真优化 GIF (自适应 256 色调色板 + 逐帧透明度/差分优化)
            p_frames = [f.convert("P", palette=Image.ADAPTIVE, colors=256) for f in out_frames]
            p_frames[0].save(
                str(out_p),
                save_all=True,
                append_images=p_frames[1:],
                duration=duration,
                loop=loop,
                optimize=True
            )
            # 针对超大尺寸/超多帧动图，严格把控在 13.8MB 内，彻底杜绝 X (Twitter) 15MB 强校验拒收与海外平台长耗时
            target_limit_bytes = int(13.8 * 1024 * 1024)
            current_frames = out_frames
            color_steps = [192, 128, 96]
            step_idx = 0

            while out_p.stat().st_size > target_limit_bytes:
                cur_size_mb = out_p.stat().st_size / (1024 * 1024)
                if step_idx < len(color_steps):
                    colors = color_steps[step_idx]
                    step_idx += 1
                    logger.info(f"[GIF Optimizer] 动图体积 {cur_size_mb:.2f}MB 超出 13.8MB 限制，自适应降级至 {colors} 色调色板...")
                    p_frames = [f.convert("P", palette=Image.ADAPTIVE, colors=colors) for f in current_frames]
                else:
                    cur_w, cur_h = current_frames[0].size
                    scale = min(0.85, (target_limit_bytes / out_p.stat().st_size) ** 0.5)
                    new_w = max(320, int(cur_w * scale))
                    new_h = max(240, int(cur_h * scale))
                    if new_w >= cur_w or new_h >= cur_h:
                        raise RuntimeError(
                            f"GIF 体积无法压缩至 13.8MB 以内（当前 {cur_size_mb:.2f}MB，"
                            f"已达 320x240 分辨率与 96 色下限）。请手动降低帧数/分辨率后重试，X 限制 GIF ≤ 15MB。"
                        )
                    logger.info(f"[GIF Optimizer] 动图仍超限 ({cur_size_mb:.2f}MB)，等比优化分辨率: {cur_w}x{cur_h} -> {new_w}x{new_h}...")
                    current_frames = [f.resize((new_w, new_h), Image.Resampling.LANCZOS) for f in current_frames]
                    p_frames = [f.convert("P", palette=Image.ADAPTIVE, colors=96) for f in current_frames]

                p_frames[0].save(
                    str(out_p),
                    save_all=True,
                    append_images=p_frames[1:],
                    duration=duration,
                    loop=loop,
                    optimize=True
                )
        return str(out_p)

    def _export_preview_image(self):
        if not self.image_paths:
            self.notify("当前无渲染图可供导出！", level="warning")
            return

        current_file = self.image_paths[self.current_preview_index]
        orig_name = Path(current_file).stem
        is_gif = current_file.lower().endswith(".gif")

        if is_gif:
            default_path = str(CACHE_DIR / f"{orig_name}_composite.gif")
            save_path, _ = QFileDialog.getSaveFileName(
                self, "导出当前打码水印 GIF 动图", default_path, "GIF 动图 (*.gif)"
            )
            if save_path:
                censor_active = self.auto_censor_cb.isChecked()
                wm_active = self.watermark_enabled_cb.isChecked()

                if not censor_active and not wm_active:
                    import shutil
                    shutil.copyfile(str(current_file), save_path)
                    self.notify(f"GIF 动图已原样导出至: {Path(save_path).name}", level="success")
                else:
                    self.notify("⏳ 正在为所有动图帧压制打码与水印，请稍候...", level="info")
                    QApplication.processEvents()

                    # 提取打码框与水印设置
                    censor_params = None
                    if censor_active:
                        style, mode, ratio = self._get_current_censor_params()
                        custom_boxes = self.custom_censor_boxes.get(current_file)
                        orig_w, orig_h = self.preview_canvas.get_gif_orig_size()
                        w_ref = orig_w if orig_w > 0 else 800
                        h_ref = orig_h if orig_h > 0 else 600
                        base_boxes = custom_boxes or getattr(self, "_detected_boxes_cache", {}).get(current_file, [])
                        # 用户手调框已是最终形态：仅钳制越界，禁止重复应用覆盖模式换算
                        final_boxes = self.censor_engine.resolve_final_boxes(
                            base_boxes, w_ref, h_ref, mode=mode, custom_ratio=ratio,
                            user_customized=bool(custom_boxes))
                        censor_params = {"enabled": True, "style": style, "boxes": final_boxes}

                    wm_params = None
                    if wm_active:
                        wm_params = {
                            "enabled": True,
                            "text": self.watermark_text_input.text().strip(),
                            "font_name": self.watermark_font_combo.currentText(),
                            "font_size": self.watermark_size_slider.value(),
                            "opacity": self.watermark_opacity_slider.value() / 100.0,
                            "position": self.watermark_pos_combo.currentData() or "bottom_right",
                            "color_hex": self.watermark_color_combo.currentData() or "#ffffff",
                            "stroke": self.watermark_stroke_cb.isChecked()
                        }

                    try:
                        self._generate_composite_gif(current_file, save_path, censor_params, wm_params)
                        self.notify(f"✅ GIF 动图已成功导出至: {Path(save_path).name}", level="success")
                    except RuntimeError as e:
                        self.notify(f"❌ GIF 压缩失败，导出中止: {e}", level="error")
                        logger.error(f"[导出中止] {e}")
        else:
            if self._current_rendered_pixmap is None:
                self.notify("当前无渲染合成图可供导出！", level="warning")
                return
            default_path = str(CACHE_DIR / f"{orig_name}_composite.png")
            save_path, _ = QFileDialog.getSaveFileName(
                self, "导出当前打码水印合成图", default_path, "PNG 图片 (*.png);;JPG 图片 (*.jpg)"
            )
            if save_path:
                self._current_rendered_pixmap.save(save_path)
                self.notify(f"合成图已成功导出至: {Path(save_path).name}", level="success")

    def _on_canvas_boxes_changed(self, boxes):
        if self.image_paths and 0 <= self.current_preview_index < len(self.image_paths):
            current_img = self.image_paths[self.current_preview_index]
            self.custom_censor_boxes[current_img] = boxes
            self._schedule_preview_refresh(instant=True)
            self.notify(f"已同步更新当前作品打码框位置与尺寸 (共 {len(boxes)} 处)！", level="info")

    def _clear_current_censor_boxes(self):
        if not self.image_paths:
            self.notify("请先导入图片", level="warning")
            return
        current_img = self.image_paths[self.current_preview_index]
        self.custom_censor_boxes[current_img] = []
        self.preview_canvas.clear_all_boxes()
        self._schedule_preview_refresh(instant=True)
        self.notify("已清除当前作品的所有打码框！在中央预览图中空白处拖拽可新增打码框。", level="info")

    def _reset_current_censor_boxes(self):
        if not self.image_paths:
            self.notify("请先导入图片", level="warning")
            return
        current_img = self.image_paths[self.current_preview_index]
        if current_img in self.custom_censor_boxes:
            del self.custom_censor_boxes[current_img]
        if hasattr(self, "_detected_boxes_cache") and current_img in self._detected_boxes_cache:
            del self._detected_boxes_cache[current_img]
        self._schedule_preview_refresh(instant=True)
        self.notify("已恢复 AI 自动识别并推荐打码位置！", level="success")

    def _open_visual_censor_editor(self):
        self.notify("💡 交互式打码模式已就绪：请直接在中央预览框中用鼠标拖拽调整打码框位置与大小，在空白处拖拽可新增打码框，选中后按 Delete 即可删除！", level="info")

    def _handle_platform_login(self, disp, status_lbl, username_lbl, avatar_lbl):
        status_lbl.setText("● 正在打开浏览器...")
        status_lbl.setStyleSheet("color: #fbbf24;")
        self.notify(f"正在唤起 Edge 浏览器登录 {disp.display_name}，请在弹出的浏览器中完成登录...", level="info")

        if not hasattr(self, "_active_login_threads"):
            self._active_login_threads = {}

        thread = LoginThread(disp)
        self._active_login_threads[disp.name] = thread
        thread.finished_signal.connect(
            lambda success, msg, profile: self._on_platform_login_done(disp, status_lbl, username_lbl, avatar_lbl, success, msg, profile)
        )
        thread.start()

    def _on_platform_login_done(self, disp, status_lbl, username_lbl, avatar_lbl, success, msg, profile):
        username_lbl.setText(profile.get("username", "已登录用户"))
        self._render_avatar(avatar_lbl, profile.get("avatar"))

        if success:
            status_lbl.setText("● 已就绪")
            status_lbl.setStyleSheet("color: #10b981;")
            self.notify(f"{disp.display_name} 账号已连接成功！已同步用户信息", level="success")
        else:
            status_lbl.setText("○ 未连接")
            status_lbl.setStyleSheet("color: #ef4444;")
            self.notify(f"{disp.display_name} 登录未能完成: {msg}", level="warning")

    def _append_signature(self):
        sig = self.config.get("signature_template", "")
        current = self.desc_input.toPlainText()
        if sig not in current:
            self.desc_input.setPlainText(current + sig)
            self.notify("已在描述末尾追加赞助引流签名模版！", level="info")

    def _select_images(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择作品图片", "", "图片与动图文件 (*.png *.jpg *.jpeg *.webp *.gif)"
        )
        if files:
            added = 0
            for f in files:
                if f not in self.image_paths:
                    self.image_paths.append(f)
                    self.img_list_widget.addItem(Path(f).name)
                    added += 1
            if added > 0:
                self.current_preview_index = 0
                self.img_list_widget.setCurrentRow(0)
                self._schedule_preview_refresh(instant=True)
                self.notify(f"已成功导入 {added} 张素材，效果已在中央预览框中实时合成！", level="success")

    def _clear_images(self):
        count = len(self.image_paths)
        self.image_paths.clear()
        self.custom_censor_boxes.clear()
        self.img_list_widget.clear()
        self.current_preview_index = 0
        self.preview_canvas.clear_image()
        self.preview_info_lbl.setText("当前未选择图片")
        self.preview_status_lbl.setText("🛡️ 打码: 未开启  |  💧 水印: 未开启")
        self._current_rendered_pixmap = None
        self._current_rendered_gif_path = None
        self.notify(f"已清空全部 {count} 张素材图片及打码缓存", level="info")

    def _start_publishing(self):
        if not self.image_paths:
            self.notify("发布内容必须包含至少一张作品图片！", level="warning")
            return

        selected = []
        for name, ctrl in self.platform_controls.items():
            if ctrl["checkbox"].isChecked():
                selected.append(ctrl["dispatcher"])

        if not selected:
            self.notify("请至少勾选一个发布平台！", level="warning")
            return

        # 需求一: 标题合规门禁快速拦截
        if not self.title_input.text().strip():
            self.notify("⚠️ 发布前门禁拦截: 请先填写作品标题！", level="warning")
            return

        # 准备分发图片资源
        raw_images = [str(p) for p in self.image_paths]
        public_images = []

        censor_enabled = self.auto_censor_cb.isChecked()
        wm_enabled = self.watermark_enabled_cb.isChecked()
        apply_wm_publish = self.watermark_apply_publish_cb.isChecked()

        style, mode, ratio = self._get_current_censor_params()
        wm_text = self.watermark_text_input.text().strip()
        wm_font = self.watermark_font_combo.currentText()
        wm_size = self.watermark_size_slider.value()
        wm_opacity = self.watermark_opacity_slider.value() / 100.0
        wm_pos = self.watermark_pos_combo.currentData() or "bottom_right"
        wm_color = self.watermark_color_combo.currentData() or "#ffffff"
        wm_stroke = self.watermark_stroke_cb.isChecked()

        self.notify("正在准备全平台素材 (公开发布端打码/水印处理，赞助端高清原图)...", level="info")

        for idx, p in enumerate(self.image_paths):
            is_gif = p.lower().endswith(".gif")
            ext = "gif" if is_gif else "png"

            # 水印参数字典
            wm_info = {
                "enabled": True,
                "text": wm_text,
                "font_name": wm_font,
                "font_size": wm_size,
                "opacity": wm_opacity,
                "position": wm_pos,
                "color_hex": wm_color,
                "stroke": wm_stroke
            } if (wm_enabled and apply_wm_publish and wm_text) else None

            # 公开发布端图片处理 (X / DeviantArt: 敏感部位打码 + 创作者水印)
            if is_gif:
                pub_out_path = CACHE_DIR / f"public_send_{idx}.gif"
                c_params = None
                if censor_enabled:
                    orig_w, orig_h = 800, 600
                    try:
                        with Image.open(p) as tmp_im:
                            orig_w, orig_h = tmp_im.width, tmp_im.height
                    except Exception as e:
                        logger.debug(f"读取动图尺寸异常: {e}")
                    custom_boxes = self.custom_censor_boxes.get(p)
                    base_boxes = custom_boxes or getattr(self, "_detected_boxes_cache", {}).get(p, [])
                    final_boxes = self.censor_engine.resolve_final_boxes(
                        base_boxes, orig_w, orig_h, mode=mode, custom_ratio=ratio,
                        user_customized=bool(custom_boxes))
                    c_params = {"enabled": True, "style": style, "boxes": final_boxes}

                try:
                    self._generate_composite_gif(p, pub_out_path, censor_params=c_params, wm_params=wm_info)
                except RuntimeError as e:
                    self.notify(f"GIF 压缩失败，发布已中止: {e}", level="error")
                    logger.error(f"[发布准备] {e}")
                    return
                public_images.append(str(pub_out_path))
            else:
                if censor_enabled:
                    custom_boxes = self.custom_censor_boxes.get(p)
                    censored_bgr, _ = self.censor_engine.apply_censor(
                        p,
                        output_path=None,
                        style=style,
                        mode=mode,
                        custom_ratio=ratio,
                        custom_boxes=custom_boxes
                    )
                    pub_out_path = CACHE_DIR / f"public_send_{idx}.{ext}"
                    if wm_info:
                        self.watermark_engine.apply_watermark(
                            censored_bgr,
                            text=wm_text,
                            font_name=wm_font,
                            font_size=wm_size,
                            opacity=wm_opacity,
                            position=wm_pos,
                            color_hex=wm_color,
                            enable_stroke=wm_stroke,
                            output_path=pub_out_path
                        )
                    else:
                        cv2.imwrite(str(pub_out_path), censored_bgr)
                    public_images.append(str(pub_out_path))
                elif wm_info:
                    pub_out_path = CACHE_DIR / f"public_send_{idx}.{ext}"
                    self.watermark_engine.apply_watermark(
                        p,
                        text=wm_text,
                        font_name=wm_font,
                        font_size=wm_size,
                        opacity=wm_opacity,
                        position=wm_pos,
                        color_hex=wm_color,
                        enable_stroke=wm_stroke,
                        output_path=pub_out_path
                    )
                    public_images.append(str(pub_out_path))
                else:
                    public_images.append(str(p))

            # 对公开发布端静态图片进行 4.8MB 安全体积校验 (针对 X/Twitter 5MB 门禁自适应压缩)
            if not is_gif and Path(public_images[-1]).exists() and Path(public_images[-1]).stat().st_size > 4.8 * 1024 * 1024:
                big_p = Path(public_images[-1])
                opt_jpg = CACHE_DIR / f"public_send_{idx}_opt.jpg"
                try:
                    with Image.open(str(big_p)) as im_big:
                        im_big.convert("RGB").save(str(opt_jpg), "JPEG", quality=88, optimize=True)
                    public_images[-1] = str(opt_jpg)
                    logger.info(f"[Image Optimizer] 静态图超限，已自动高质量转码 JPEG: {big_p.stat().st_size / 1024 / 1024:.2f}MB -> {opt_jpg.stat().st_size / 1024 / 1024:.2f}MB")
                except Exception as e:
                    logger.debug(f"[Image Optimizer] 静态图转码异常: {e}")

        tags = [t.strip() for t in self.tags_input.text().replace("，", ",").split(",") if t.strip()]

        platform_options = {
            "is_ai_generated": self.ai_checkbox.isChecked(),
        }

        if "x_twitter" in self.platform_controls:
            opts = self.platform_controls["x_twitter"]["options"]
            if "sensitive" in opts:
                platform_options["x_sensitive"] = opts["sensitive"].isChecked()
            if "ai" in opts:
                platform_options["x_ai"] = opts["ai"].isChecked()
            if "premium" in opts:
                platform_options["x_premium"] = opts["premium"].isChecked()
                platform_options["x_char_limit"] = 25000 if opts["premium"].isChecked() else 280
            if "policy" in opts:
                platform_options["x_over_limit_policy"] = opts["policy"].currentData()

        if "deviantart" in self.platform_controls:
            opts = self.platform_controls["deviantart"]["options"]
            if "mature" in opts:
                txt = opts["mature"].currentText().lower()
                if "moderate" in txt:
                    platform_options["deviantart_mature"] = "moderate"
                elif "none" in txt:
                    platform_options["deviantart_mature"] = "none"
                else:
                    platform_options["deviantart_mature"] = "strict"
            if "ai" in opts:
                platform_options["deviantart_ai"] = opts["ai"].isChecked()

        if "patreon" in self.platform_controls:
            opts = self.platform_controls["patreon"]["options"]
            if "visibility" in opts:
                txt = opts["visibility"].currentText().lower()
                if "public" in txt or "公开" in txt:
                    platform_options["patreon_visibility"] = "public"
                elif "tiers" in txt or "档位" in txt:
                    platform_options["patreon_visibility"] = "tiers"
                else:
                    platform_options["patreon_visibility"] = "patrons"
            if "ai" in opts:
                platform_options["patreon_ai"] = opts["ai"].isChecked()

        if "kofi" in self.platform_controls:
            opts = self.platform_controls["kofi"]["options"]
            if "visibility" in opts:
                txt = opts["visibility"].currentText().lower()
                if "everyone" in txt or "公开" in txt or "所有" in txt:
                    platform_options["kofi_visibility"] = "everyone"
                else:
                    platform_options["kofi_visibility"] = "supporters"
            if "ai" in opts:
                platform_options["kofi_ai"] = opts["ai"].isChecked()

        # 针对 X (Twitter) 进行字数超限决策检查
        is_x_selected = any(d.name == "x_twitter" for d in selected)
        title_val = self.title_input.text().strip()
        desc_val = self.desc_input.toPlainText().strip()
        full_x_text = f"{title_val}\n\n{desc_val}".strip() if title_val else desc_val.strip()
        x_count = calculate_x_char_count(full_x_text)
        x_limit = platform_options.get("x_char_limit", 280)

        x_action = getattr(self, "_pending_x_action", None)
        if hasattr(self, "_pending_x_action"):
            del self._pending_x_action

        if is_x_selected and x_count > x_limit and not x_action:
            policy = platform_options.get("x_over_limit_policy", "ask")
            if policy == "truncate":
                x_action = "truncate"
            elif policy == "thread":
                x_action = "thread"
            else: # "ask"
                has_sig = any(m in full_x_text[-160:].lower() for m in ["patreon", "ko-fi", "fanbox", "fantia", "---"])
                sig_hint = "<br><font color='#34d399'>💡 检测到正文末尾包含赞助签名，选择截断将优先保护签名。</font>" if has_sig else ""
                self.x_limit_msg_lbl.setText(
                    f"X (Twitter) 文本计为 <b>{x_count}</b> 字符 (限额 <b>{x_limit}</b> 字符)，已超限 <b>{x_count - x_limit}</b> 字。{sig_hint}<br>请确认发布方式："
                )
                self.x_limit_confirm_widget.show()
                self.publish_btn.setEnabled(False)
                self.notify(f"⚠️ X 推文超限 ({x_count}/{x_limit})，请在右侧发布控制台确认处理方式", level="warning")
                return

        if x_action:
            platform_options["x_over_limit_action"] = x_action

        payload = {
            "title": self.title_input.text().strip(),
            "description": self.desc_input.toPlainText().strip(),
            "tags": tags,
            "images": public_images,            # 打码/水印图发给公开发布平台 (X, DeviantArt)
            "raw_images": raw_images,           # 无码高清原图发给赞助平台 (Patreon, Ko-fi)
            "is_nsfw": self.nsfw_checkbox.isChecked(),
            "is_ai": self.ai_checkbox.isChecked(),
            "platform_options": platform_options
        }

        self.publish_btn.setEnabled(False)
        if hasattr(self, "retry_btn"):
            self.retry_btn.hide()
        self._last_publish_payload = payload
        self.progress_bar.setValue(0)
        self.notify(f"开始向已选中的 {len(selected)} 个平台分发作品...", level="info")

        self.thread = PublishThread(selected, payload)
        self.thread.progress_signal.connect(self._on_progress)
        self.thread.completed_signal.connect(self._on_completed)
        self.thread.start()

    def _on_progress(self, percent, text):
        self.progress_bar.setValue(percent)
        self.status_label.setText(text)
        self.notify(text, level="info", auto_dismiss=False)

    def _on_completed(self, results):
        self.publish_btn.setEnabled(True)
        success_count = sum(1 for v in results.values() if v[0])
        total_count = len(results)

        summary_rows = []
        unconfirmed_warnings = []

        for k, v in results.items():
            success = v[0]
            msg = v[1]
            compliance = v[2] if len(v) > 2 else []

            status_color = "#34d399" if success else "#f87171"
            status_text = "成功" if success else "失败"
            summary_rows.append(f"<font color='{status_color}'>● {k}: {status_text}</font> ({msg})")

            # 合规项状态回读统计 (只对 expected=True 的项进行审核)
            expected_items = [c for c in compliance if c.get("expected", True)]
            if expected_items:
                verified_items = [c for c in expected_items if c.get("verified") is True]
                unconfirmed_items = [c for c in expected_items if c.get("verified") is not True]

                if not unconfirmed_items:
                    summary_rows.append(f"&nbsp;&nbsp;&nbsp;&nbsp;<font color='#34d399'>✓ 合规项已确认 ({len(verified_items)}/{len(expected_items)})</font>")
                else:
                    for item in unconfirmed_items:
                        v_state = item.get("verified")
                        state_tag = "点击未生效" if v_state is False else "未确认"
                        detail = item.get("detail", "")
                        item_key = item.get("item", "")
                        summary_rows.append(
                            f"&nbsp;&nbsp;&nbsp;&nbsp;<font color='#fbbf24'>⚠️ 合规项 [{item_key}] {state_tag}: {detail}</font>"
                        )
                        unconfirmed_warnings.append(f"{k} [{item_key}]: {detail}")

        all_ok = (success_count == total_count)
        if all_ok:
            self._clear_draft()
            if hasattr(self, "retry_btn"):
                self.retry_btn.hide()
        else:
            failed_keys = [k for k, v in results.items() if not v[0]]
            self._last_failed_dispatchers = [d for d in self.dispatchers if d.name in failed_keys]
            if hasattr(self, "retry_btn") and self._last_failed_dispatchers:
                self.retry_btn.setText(f"🔄 重试失败项 ({len(self._last_failed_dispatchers)} 个平台)")
                self.retry_btn.show()

        msg_level = "success" if all_ok else ("warning" if success_count > 0 else "error")
        summary_msg = f"全平台分发已结束！共 {total_count} 个平台，成功 {success_count} 个"

        # 内嵌通知条与富文本日志汇报（严格遵守红线 #8: 严禁 QMessageBox）
        self.notify(summary_msg, level=msg_level, auto_dismiss=False)
        self.notification_bar.append_rich_log(
            f"<br><b>📊 【全平台分发结果统计】</b><br>" + "<br>".join(summary_rows) + "<br>"
        )
        self.notification_bar.expand_log(True)

        # 若存在未确认合规项，额外触发常驻通知告警
        if unconfirmed_warnings:
            warn_msg = "⚠️ 合规项未完全确认 (请人工核验): " + "; ".join(unconfirmed_warnings[:2])
            if len(unconfirmed_warnings) > 2:
                warn_msg += f" 等共 {len(unconfirmed_warnings)} 项"
            self.notify(warn_msg, level="warning", auto_dismiss=False)

    def _run_diagnostics(self):
        """启动后台一键体检诊断 (需求三)"""
        if getattr(self, "_diag_thread", None) and self._diag_thread.isRunning():
            self.notify("⚠️ 系统自检正在运行中，请稍候...", level="warning")
            return

        self.diagnose_btn.setEnabled(False)
        self.publish_btn.setEnabled(False)
        self.diag_panel.show()
        self.diag_title_lbl.setText("📋 <b>系统自检中... 正在进行深度探针体检</b>")
        self.diag_summary_lbl.setText("<font color='#60a5fa'>⏳ 正在串行执行环境依赖、平台连通性、登录态与选择器健康体检...</font>")
        self.diag_text_browser.setHtml("<p style='color:#94a3b8;'>正在执行系统全平台体检，请稍候...<br>自检采用轻量只读 DOM 探测，绝不影响您的已有草稿或触发发布动作。</p>")
        self.progress_bar.setValue(0)
        self.notify("🔍 开始全平台一键自检与环境体检...", level="info")

        all_dispatchers = [ctrl["dispatcher"] for ctrl in self.platform_controls.values()]
        self._diag_thread = DiagnosticThread(self.data_dir, all_dispatchers)
        self._diag_thread.progress_signal.connect(self._on_diag_progress)
        self._diag_thread.finished_signal.connect(self._on_diag_finished)
        self._diag_thread.start()

    def _on_diag_progress(self, percent, text):
        self.progress_bar.setValue(percent)
        self.status_label.setText(text)
        self.notify(text, level="info", auto_dismiss=False)

    def _on_diag_finished(self, report, report_path):
        self.diagnose_btn.setEnabled(True)
        self.publish_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        self._last_diag_report_path = report_path

        summary = report.get("summary", {})
        score = summary.get("health_score", 0)
        p_cnt = summary.get("pass", 0)
        u_cnt = summary.get("unknown", 0)
        f_cnt = summary.get("fail", 0)

        score_color = "#34d399" if score >= 80 else ("#fbbf24" if score >= 60 else "#f87171")
        self.diag_title_lbl.setText(f"📋 <b>系统自检报告 (健康评分: <font color='{score_color}'>{score}%</font>)</b>")
        self.diag_summary_lbl.setText(
            f"体检结果: <font color='#34d399'><b>{p_cnt} 项正常</b></font> | "
            f"<font color='#fbbf24'><b>{u_cnt} 项待确认</b></font> | "
            f"<font color='#f87171'><b>{f_cnt} 项异常</b></font>"
        )

        # 渲染 HTML 报告
        html_lines = ["<body style='font-family:sans-serif; color:#e2e8f0; font-size:13px;'>"]
        categories = {
            "env": "🖥️ 运行环境与依赖",
            "network": "🌐 平台网络连通性",
            "login": "🔑 平台登录态存活探针",
            "selector": "🎯 投稿页选择器健康度"
        }

        items = report.get("items", [])
        for cat_k, cat_title in categories.items():
            cat_items = [it for it in items if it.get("category") == cat_k]
            if not cat_items:
                continue
            html_lines.append(f"<h4 style='color:#818cf8; margin-top:10px; margin-bottom:4px;'>{cat_title}</h4>")
            html_lines.append("<ul style='margin-top:0px; padding-left:18px;'>")
            for it in cat_items:
                st = it.get("status", "unknown")
                badge = "<font color='#34d399'>[PASS]</font>" if st == "pass" else ("<font color='#f87171'>[FAIL]</font>" if st == "fail" else "<font color='#fbbf24'>[WARN]</font>")
                p_tag = f"<b>[{it.get('platform', '')}]</b> " if it.get('platform') and it.get('platform') != 'env' else ""
                detail = it.get("detail", "")
                sugg = it.get("suggestion", "")
                sugg_html = f" <font color='#60a5fa'><i>(建议: {sugg})</i></font>" if sugg else ""
                html_lines.append(f"<li style='margin-bottom:4px;'>{badge} {p_tag}{detail}{sugg_html}</li>")
            html_lines.append("</ul>")

        html_lines.append("</body>")
        self.diag_text_browser.setHtml("".join(html_lines))

        msg = f"自检完成！健康评分: {score}% (通过 {p_cnt}, 警告 {u_cnt}, 失败 {f_cnt})"
        lvl = "success" if f_cnt == 0 else "warning"
        self.notify(msg, level=lvl)

    def _open_diagnostic_report_file(self):
        """打开导出的诊断报告 Markdown 文件"""
        rpt_path = getattr(self, "_last_diag_report_path", None) or (self.data_dir / "diagnostic_report.md")
        p_obj = Path(rpt_path)
        if p_obj.exists():
            try:
                os.startfile(str(p_obj.resolve()))
                self.notify(f"已在默认编辑器中打开诊断报告: {p_obj.name}", level="info")
            except Exception as e:
                self.notify(f"打开报告失败: {e}，报告位于: {p_obj.resolve()}", level="warning")
        else:
            self.notify("暂未生成诊断报告文件，请先点击 [一键自检]", level="warning")


def setup_dark_palette(app):
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#121218"))
    palette.setColor(QPalette.WindowText, QColor("#e2e8f0"))
    palette.setColor(QPalette.Base, QColor("#161620"))
    palette.setColor(QPalette.AlternateBase, QColor("#1c1c26"))
    palette.setColor(QPalette.ToolTipBase, QColor("#1c1c26"))
    palette.setColor(QPalette.ToolTipText, QColor("#e2e8f0"))
    palette.setColor(QPalette.Text, QColor("#e2e8f0"))
    palette.setColor(QPalette.Button, QColor("#22222e"))
    palette.setColor(QPalette.ButtonText, QColor("#e2e8f0"))
    palette.setColor(QPalette.Highlight, QColor("#4f46e5"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

def main():
    app = QApplication(sys.argv)
    setup_dark_palette(app)
    window = MainWindow()
    if not window.isVisible():
        window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
