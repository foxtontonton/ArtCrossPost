# -*- coding: utf-8 -*-
"""
ArtCrossPost - AI Auto-Censor Engine
集成:
1. NudeNet AI 真实目标检测 (自动识别女性/男性隐私黏膜与器官暴露区域)
2. 3 级尺寸遮挡模式 (轻量细条 / 适中局部 / 严实全遮) 与连续微调滑块 (20% ~ 120%)
3. 自定义鼠标手动框选覆写 (支持多框、微调、拖拽，绝对精准不打歪)
4. 多种打码样式 (纯黑条、经典马赛克、高斯模糊)
"""
# 预先导入 onnxruntime，解决 Windows 下 OpenCV 与 ONNX Runtime 的 DLL 初始化例程冲突
try:
    from logger import get_logger
    logger = get_logger("CensorEngine")
except Exception:
    import logging
    logger = logging.getLogger("CensorEngine")

try:
    pass
except Exception as e:
    logger.debug(f"预导入 onnxruntime 提示: {e}")

import cv2
import numpy as np
from pathlib import Path

TARGET_CLASSES = [
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "ANUS_EXPOSED"
]

class CensorEngine:
    def __init__(self, default_style="black_bar", default_mode="moderate", padding_ratio=0.15):
        self.default_style = default_style
        self.default_mode = default_mode
        self.padding_ratio = padding_ratio
        self.detector = None
        self._init_detector()

    def _init_detector(self):
        """初始化 NudeDetector AI 模型"""
        try:
            from nudenet import NudeDetector
            self.detector = NudeDetector()
            logger.info("[CensorEngine] NudeDetector AI 真实检测模型已就绪！")
        except Exception as e:
            logger.warning(f"[CensorEngine] NudeDetector 初始化失败: {e}")
            self.detector = None

    def detect_sensitive_regions(self, img_path, img_bgr=None):
        """
        使用 AI 模型检测图片中的真实关键敏感区域坐标 [x1, y1, x2, y2]
        """
        boxes = []
        if self.detector is not None and img_path and Path(img_path).exists():
            try:
                detections = self.detector.detect(str(img_path))
                for det in detections:
                    cls_name = det.get("class", "")
                    score = det.get("score", 0.0)
                    if cls_name in TARGET_CLASSES and score >= 0.18:
                        x, y, w, h = det["box"]
                        boxes.append([int(x), int(y), int(x + w), int(y + h)])
                        logger.info(f"[CensorEngine] AI 捕获关键部位: {cls_name} (置信度: {score:.2f}, 坐标: {det['box']})")
            except Exception as e:
                logger.warning(f"[CensorEngine] AI 检测报错: {e}")

        # 如果 AI 模型未检出（例如二次元极度夸张线条或画风特殊），返回空列表，交由可视化手动框选或默认中心区
        if not boxes:
            if img_bgr is not None:
                h, w = img_bgr.shape[:2]
            elif img_path and Path(img_path).exists():
                tmp = cv2.imread(str(img_path))
                h, w = (tmp.shape[:2]) if tmp is not None else (800, 600)
            else:
                h, w = 800, 600

            print("[CensorEngine] 提示: AI 未能在该图自动捕获器官，使用推荐初始框（建议使用可视化画框微调）")
            boxes.append([
                int(w * 0.35),
                int(h * 0.52),
                int(w * 0.65),
                int(h * 0.72)
            ])
            
        return boxes

    def compute_censor_box(self, raw_box, img_w, img_h, mode="moderate", custom_ratio=None):
        """
        根据遮挡范围模式计算最终打码框坐标:
        - slender (轻量细条): 极窄水平黑条，保留大部分画面
        - moderate (适中局部): 覆盖中心 55% 核心焦点区，露出边缘肌肉和外侧轮廓
        - full (严实全遮): 100% 覆盖并外扩 15% 边距
        - 自定义比例 (0.2 ~ 1.2)
        """
        x1, y1, x2, y2 = raw_box
        bw = max(1, x2 - x1)
        bh = max(1, y2 - y1)
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        if custom_ratio is not None:
            ratio = max(0.15, min(1.3, float(custom_ratio)))
            tw = int(bw * ratio)
            th = int(bh * ratio)
        elif mode == "slender":
            tw = int(bw * 0.65)
            th = max(18, int(bh * 0.22))
        elif mode == "moderate":
            tw = int(bw * 0.55)
            th = int(bh * 0.55)
        elif mode == "full":
            pad_x = int(bw * self.padding_ratio)
            pad_y = int(bh * self.padding_ratio)
            return [max(0, x1 - pad_x), max(0, y1 - pad_y), min(img_w, x2 + pad_x), min(img_h, y2 + pad_y)]
        else:
            tw, th = int(bw * 0.55), int(bh * 0.55)

        hw = max(1, tw // 2)
        hh = max(1, th // 2)
        nx1 = max(0, cx - hw)
        ny1 = max(0, cy - hh)
        nx2 = min(img_w, cx + hw)
        ny2 = min(img_h, cy + hh)
        return [nx1, ny1, nx2, ny2]

    def resolve_final_boxes(self, boxes, img_w, img_h, mode="moderate", custom_ratio=None, user_customized=False):
        """
        将候选框解析为最终打码框 (统一入口，禁止绕过)。
        - user_customized=True (用户在画布上手调/手画的框): 已是最终形态，仅做
          越界钳制，**严禁**再套 compute_censor_box 做覆盖模式换算。否则每次预览
          刷新都会把框再收缩/移位一次，且越调越小。
        - user_customized=False (AI 检测框): 正常应用覆盖模式换算。
        """
        final = []
        for raw_b in boxes or []:
            if user_customized:
                try:
                    x1, y1, x2, y2 = (int(round(float(v))) for v in raw_b)
                except Exception as e:
                    logger.debug(f"[CensorEngine] 解析自定义打码框异常: {e}")
                    continue
                x1 = max(0, min(img_w, x1))
                y1 = max(0, min(img_h, y1))
                x2 = max(0, min(img_w, x2))
                y2 = max(0, min(img_h, y2))
                if x2 > x1 and y2 > y1:
                    final.append([x1, y1, x2, y2])
            else:
                cb = self.compute_censor_box(raw_b, img_w, img_h, mode=mode, custom_ratio=custom_ratio)
                if cb[2] > cb[0] and cb[3] > cb[1]:
                    final.append(cb)
        return final

    def apply_censor(self, image_path, output_path=None, style=None, mode=None, custom_ratio=None, custom_boxes=None, mosaic_size=20):
        """
        执行图像打码
        custom_boxes: 优先使用人工在界面上手动画出的框坐标 [[x1, y1, x2, y2], ...]
        """
        style = style or self.default_style
        mode = mode or self.default_mode
        img_bgr = cv2.imread(str(image_path))
        if img_bgr is None:
            raise FileNotFoundError(f"无法读取图片: {image_path}")

        h, w = img_bgr.shape[:2]
        
        # 如果用户手动指定了框，则直接使用用户指定的框；否则调用 AI 检测
        if custom_boxes and len(custom_boxes) > 0:
            boxes = custom_boxes
        else:
            boxes = self.detect_sensitive_regions(image_path, img_bgr)

        censored = img_bgr.copy()
        # 用户手调框已是最终形态：仅钳制越界，禁止重复应用覆盖模式换算
        # (否则每次预览刷新都会把框再收缩/移位一次，越调越小)
        processed_boxes = self.resolve_final_boxes(
            boxes, w, h, mode=mode, custom_ratio=custom_ratio,
            user_customized=bool(custom_boxes)
        )

        for x1, y1, x2, y2 in processed_boxes:
            roi = censored[y1:y2, x1:x2]

            if style == "black_bar":
                censored[y1:y2, x1:x2] = (0, 0, 0)
            elif style == "mosaic":
                rw, rh = max(1, x2 - x1), max(1, y2 - y1)
                cell = max(4, mosaic_size)
                small_w = max(1, rw // cell)
                small_h = max(1, rh // cell)
                small = cv2.resize(roi, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
                pixelated = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
                censored[y1:y2, x1:x2] = pixelated
            elif style == "blur":
                ksize = 45 if (45 % 2 == 1) else 47
                blurred = cv2.GaussianBlur(roi, (ksize, ksize), 30)
                censored[y1:y2, x1:x2] = blurred

        if output_path:
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out_p), censored)

        return censored, processed_boxes

    def apply_censor_gif(self, gif_path, output_path=None, style=None, mode=None, custom_ratio=None, custom_boxes=None, mosaic_size=20):
        """
        为多帧动态 GIF 图片执行敏感部位打码，并保持动态帧率
        """
        from PIL import Image, ImageSequence
        style = style or self.default_style
        mode = mode or self.default_mode
        im = Image.open(str(gif_path))
        duration = im.info.get("duration", 100)
        loop = im.info.get("loop", 0)

        # 确定打码框：优先使用手动微调框，否则以第一帧为基准运行 AI 检测
        if custom_boxes and len(custom_boxes) > 0:
            boxes = custom_boxes
        else:
            first_frame_bgr = cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2BGR)
            boxes = self.detect_sensitive_regions(None, img_bgr=first_frame_bgr)

        out_frames = []
        processed_boxes = []
        user_customized = bool(custom_boxes)
        for frame in ImageSequence.Iterator(im):
            frame_bgr = cv2.cvtColor(np.array(frame.convert("RGB")), cv2.COLOR_RGB2BGR)
            h, w = frame_bgr.shape[:2]
            censored = frame_bgr.copy()

            # 用户手调框已是最终形态：仅钳制越界，禁止重复应用覆盖模式换算
            frame_boxes = self.resolve_final_boxes(
                boxes, w, h, mode=mode, custom_ratio=custom_ratio,
                user_customized=user_customized
            )
            if not processed_boxes:
                processed_boxes.extend(frame_boxes)

            for x1, y1, x2, y2 in frame_boxes:
                roi = censored[y1:y2, x1:x2]
                if style == "black_bar":
                    censored[y1:y2, x1:x2] = (0, 0, 0)
                elif style == "mosaic":
                    rw, rh = max(1, x2 - x1), max(1, y2 - y1)
                    cell = max(4, mosaic_size)
                    small_w = max(1, rw // cell)
                    small_h = max(1, rh // cell)
                    small = cv2.resize(roi, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
                    censored[y1:y2, x1:x2] = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
                elif style == "blur":
                    ksize = 45 if (45 % 2 == 1) else 47
                    censored[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (ksize, ksize), 30)

            frame_rgb = cv2.cvtColor(censored, cv2.COLOR_BGR2RGB)
            out_frames.append(Image.fromarray(frame_rgb))

        out_p = Path(output_path) if output_path else Path(gif_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        if out_frames:
            out_frames[0].save(
                str(out_p),
                save_all=True,
                append_images=out_frames[1:],
                duration=duration,
                loop=loop,
                optimize=False
            )
        return out_p, processed_boxes
