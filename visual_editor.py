# -*- coding: utf-8 -*-
"""
ArtCrossPost - 可视化打码框微调器 (Visual Censor Dialog)
允许用户直接在作品原图上用鼠标拖拽画框、精准对准关键黏膜/部位，
实时预览打码效果，彻底杜绝打歪或漏打！
"""
try:
    import onnxruntime
except Exception:
    pass

import cv2
import numpy as np
from pathlib import Path
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QSlider, QFrame
)
from PyQt5.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QBrush, QColor, QPixmap, QImage, QFont

DIALOG_DARK_STYLE = """
QDialog {
    background-color: #13131a;
    color: #e2e8f0;
}
QWidget {
    color: #e2e8f0;
    font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;
    font-size: 14px;
}
QComboBox {
    background-color: #171720;
    border: 1px solid #3a3a4d;
    border-radius: 6px;
    padding: 6px 10px;
    color: #e2e8f0;
}
QComboBox QAbstractItemView {
    background-color: #1c1c24;
    color: #e2e8f0;
    selection-background-color: #4f46e5;
    selection-color: #ffffff;
    border: 1px solid #3a3a4d;
    outline: none;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #2e2e3d;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #6366f1;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #a5b4fc;
    border: 2px solid #6366f1;
    width: 16px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 8px;
}
QPushButton {
    background-color: #262635;
    color: #e2e8f0;
    font-weight: bold;
    border-radius: 6px;
    padding: 8px 14px;
    border: 1px solid #3a3a4d;
}
QPushButton:hover {
    background-color: #35354a;
    border-color: #6366f1;
}
"""

class CensorCanvas(QFrame):
    """可交互画布，支持鼠标框选与实时打码效果展示"""
    boxes_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #121217; border: 1px solid #3e3e50; border-radius: 6px;")
        self.img_bgr = None
        self.img_h = 0
        self.img_w = 0
        self.boxes = []  # 图像坐标系 [[x1, y1, x2, y2], ...]
        self.drawing = False
        self.start_pt = None
        self.current_pt = None
        
        # 实时预览参数
        self.style = "black_bar"
        self.mode = "moderate"
        self.custom_ratio = 0.55
        self.censor_engine = None

    def load_image(self, img_path, censor_engine, initial_boxes=None):
        self.censor_engine = censor_engine
        self.img_bgr = cv2.imread(str(img_path))
        if self.img_bgr is None:
            return False
        self.img_h, self.img_w = self.img_bgr.shape[:2]
        
        if initial_boxes is not None and len(initial_boxes) > 0:
            self.boxes = [list(b) for b in initial_boxes]
        else:
            # 自动运行 AI 检测获取初始推荐框
            self.boxes = self.censor_engine.detect_sensitive_regions(img_path, self.img_bgr)
            
        self.update()
        return True

    def set_render_params(self, style, mode, ratio):
        self.style = style
        self.mode = mode
        self.custom_ratio = ratio
        self.update()

    def clear_boxes(self):
        self.boxes.clear()
        self.update()
        self.boxes_changed.emit()

    def reset_ai_boxes(self, img_path):
        if self.censor_engine and self.img_bgr is not None:
            self.boxes = self.censor_engine.detect_sensitive_regions(img_path, self.img_bgr)
            self.update()
            self.boxes_changed.emit()

    def _get_draw_scale(self):
        """计算画布缩放比与原点偏移，保证图像居中等比显示"""
        cw = self.width()
        ch = self.height()
        if self.img_w == 0 or self.img_h == 0:
            return 1.0, 0, 0
        scale = min(cw / self.img_w, ch / self.img_h)
        ox = (cw - self.img_w * scale) / 2.0
        oy = (ch - self.img_h * scale) / 2.0
        return scale, ox, oy

    def _widget_to_img_coord(self, pt):
        scale, ox, oy = self._get_draw_scale()
        ix = int((pt.x() - ox) / scale)
        iy = int((pt.y() - oy) / scale)
        return max(0, min(self.img_w, ix)), max(0, min(self.img_h, iy))

    def _img_to_widget_coord(self, ix, iy):
        scale, ox, oy = self._get_draw_scale()
        wx = int(ix * scale + ox)
        wy = int(iy * scale + oy)
        return wx, wy

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.img_bgr is not None:
            self.drawing = True
            self.start_pt = event.pos()
            self.current_pt = event.pos()

    def mouseMoveEvent(self, event):
        if self.drawing:
            self.current_pt = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drawing:
            self.drawing = False
            ix1, iy1 = self._widget_to_img_coord(self.start_pt)
            ix2, iy2 = self._widget_to_img_coord(event.pos())
            x1, x2 = min(ix1, ix2), max(ix1, ix2)
            y1, y2 = min(iy1, iy2), max(iy1, iy2)
            
            # 过滤太小的误触点击
            if (x2 - x1) > 10 and (y2 - y1) > 10:
                self.boxes.append([x1, y1, x2, y2])
                self.boxes_changed.emit()
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.img_bgr is None:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 1. 渲染带打码效果的图像
        preview_bgr = self.img_bgr.copy()
        for raw_b in self.boxes:
            c_box = self.censor_engine.compute_censor_box(
                raw_b, self.img_w, self.img_h, mode=self.mode, custom_ratio=self.custom_ratio
            )
            x1, y1, x2, y2 = c_box
            if x2 > x1 and y2 > y1:
                roi = preview_bgr[y1:y2, x1:x2]
                if self.style == "black_bar":
                    preview_bgr[y1:y2, x1:x2] = (0, 0, 0)
                elif self.style == "mosaic":
                    rw, rh = max(1, x2 - x1), max(1, y2 - y1)
                    cell = max(4, int(20 * (self.custom_ratio or 1.0)))
                    sw = max(1, rw // cell)
                    sh = max(1, rh // cell)
                    sm = cv2.resize(roi, (sw, sh), interpolation=cv2.INTER_LINEAR)
                    pix = cv2.resize(sm, (rw, rh), interpolation=cv2.INTER_NEAREST)
                    preview_bgr[y1:y2, x1:x2] = pix
                elif self.style == "blur":
                    ksize = 45
                    preview_bgr[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (ksize, ksize), 30)

        # BGR 转换成 QImage 显示
        rgb = cv2.cvtColor(preview_bgr, cv2.COLOR_BGR2RGB)
        bytes_per_line = 3 * self.img_w
        qimg = QImage(rgb.data, self.img_w, self.img_h, bytes_per_line, QImage.Format_RGB888)

        scale, ox, oy = self._get_draw_scale()
        draw_w = int(self.img_w * scale)
        draw_h = int(self.img_h * scale)
        painter.drawImage(QRect(int(ox), int(oy), draw_w, draw_h), qimg)

        # 2. 高亮画出打码边界框（方便查看准确度）
        pen = QPen(QColor(99, 102, 241), 2, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor(99, 102, 241, 40)))

        for idx, b in enumerate(self.boxes):
            wx1, wy1 = self._img_to_widget_coord(b[0], b[1])
            wx2, wy2 = self._img_to_widget_coord(b[2], b[3])
            painter.drawRect(wx1, wy1, wx2 - wx1, wy2 - wy1)
            painter.drawText(wx1 + 4, wy1 + 16, f"#{idx+1}")

        # 3. 绘制当前正在拖拽的画框
        if self.drawing and self.start_pt and self.current_pt:
            active_pen = QPen(QColor(239, 68, 68), 2, Qt.SolidLine)
            painter.setPen(active_pen)
            painter.setBrush(QBrush(QColor(239, 68, 68, 60)))
            rx = min(self.start_pt.x(), self.current_pt.x())
            ry = min(self.start_pt.y(), self.current_pt.y())
            rw = abs(self.start_pt.x() - self.current_pt.x())
            rh = abs(self.start_pt.y() - self.current_pt.y())
            painter.drawRect(rx, ry, rw, rh)

        painter.end()


class VisualCensorDialog(QDialog):
    def __init__(self, img_path, censor_engine, current_style="black_bar", current_mode="moderate", current_ratio=0.55, initial_boxes=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("✏️ 交互式精准打码微调器 - 直接在图上框选关键部位")
        self.resize(1000, 780)
        self.setStyleSheet(DIALOG_DARK_STYLE)
        self.img_path = img_path
        self.censor_engine = censor_engine
        self.saved_boxes = None

        self._build_ui(current_style, current_mode, current_ratio, initial_boxes)

    def _build_ui(self, style, mode, ratio, initial_boxes):
        layout = QVBoxLayout(self)

        # 顶部工具控制栏
        top_bar = QFrame()
        top_bar.setStyleSheet("background-color: #2b2b36; border-radius: 8px; padding: 6px;")
        top_layout = QHBoxLayout(top_bar)

        top_layout.addWidget(QLabel("样式:"))
        self.style_combo = QComboBox()
        self.style_combo.addItems(["纯黑条 (Black Bar)", "经典马赛克 (Mosaic)", "高斯模糊 (Blur)"])
        style_idx = 0 if style == "black_bar" else (1 if style == "mosaic" else 2)
        self.style_combo.setCurrentIndex(style_idx)
        self.style_combo.currentIndexChanged.connect(self._on_param_change)
        top_layout.addWidget(self.style_combo)

        top_layout.addWidget(QLabel("遮挡模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["适中局部 (标准推荐)", "轻量细条 (极简线条)", "严实全遮"])
        mode_idx = 0 if mode == "moderate" else (1 if mode == "slender" else 2)
        self.mode_combo.setCurrentIndex(mode_idx)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_change)
        top_layout.addWidget(self.mode_combo)

        top_layout.addWidget(QLabel("尺寸微调:"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(20, 120)
        self.slider.setValue(int(ratio * 100))
        self.slider.valueChanged.connect(self._on_param_change)
        top_layout.addWidget(self.slider)

        self.ratio_lbl = QLabel(f"{int(ratio * 100)}%")
        self.ratio_lbl.setStyleSheet("color: #6366f1; font-weight: bold;")
        top_layout.addWidget(self.ratio_lbl)

        reset_ai_btn = QPushButton("🔄 重新运行 AI 识别")
        reset_ai_btn.clicked.connect(lambda: self.canvas.reset_ai_boxes(self.img_path))
        top_layout.addWidget(reset_ai_btn)

        clear_btn = QPushButton("🗑️ 清空所有框")
        clear_btn.clicked.connect(self.canvas.clear_boxes)
        top_layout.addWidget(clear_btn)

        layout.addWidget(top_bar)

        # 画布
        self.canvas = CensorCanvas(self)
        self.canvas.load_image(self.img_path, self.censor_engine, initial_boxes)
        self.canvas.set_render_params(style, mode, ratio)
        layout.addWidget(self.canvas, 1)

        # 底部提示与操作按钮
        bottom_bar = QHBoxLayout()
        tip_lbl = QLabel("💡 提示: 用鼠标在图上按住并拖拽，即可精准给任意器官或接触点打码！支持框选多个部位。")
        tip_lbl.setStyleSheet("color: #9d9daf; font-size: 14px;")
        bottom_bar.addWidget(tip_lbl)
        bottom_bar.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        bottom_bar.addWidget(cancel_btn)

        save_btn = QPushButton("✅ 保存并应用打码位置")
        save_btn.setStyleSheet("background-color: #10b981; font-weight: bold; padding: 10px 20px;")
        save_btn.clicked.connect(self._save_and_apply)
        bottom_bar.addWidget(save_btn)

        layout.addLayout(bottom_bar)

    def _on_mode_change(self, idx):
        if idx == 0:
            self.slider.setValue(55)
        elif idx == 1:
            self.slider.setValue(30)
        elif idx == 2:
            self.slider.setValue(100)
        self._on_param_change()

    def _on_param_change(self):
        styles = ["black_bar", "mosaic", "blur"]
        modes = ["moderate", "slender", "full"]
        st = styles[self.style_combo.currentIndex()]
        md = modes[self.mode_combo.currentIndex()]
        rt = self.slider.value() / 100.0
        self.ratio_lbl.setText(f"{self.slider.value()}%")
        self.canvas.set_render_params(st, md, rt)

    def _save_and_apply(self):
        self.saved_boxes = list(self.canvas.boxes)
        self.accept()

    def get_result(self):
        styles = ["black_bar", "mosaic", "blur"]
        modes = ["moderate", "slender", "full"]
        return {
            "boxes": self.saved_boxes,
            "style": styles[self.style_combo.currentIndex()],
            "mode": modes[self.mode_combo.currentIndex()],
            "ratio": self.slider.value() / 100.0
        }
