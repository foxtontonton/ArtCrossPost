# -*- coding: utf-8 -*-
"""
ArtCrossPost - Watermark Engine (作品文字水印与防盗印渲染引擎)
支持:
1. 系统字体智能匹配 (中英文字体、微软雅黑、黑体、Arial、Impact 等自动索引 Windows 字体库)
2. 多种水印位置 (右下角、左下角、居中、右上角、底部居中、全图倾斜平铺防盗印)
3. 动态透明度 (Alpha 0.05 ~ 1.0) 与双层高对比描边/阴影 (在任何暗色/亮色复杂背景上皆清晰可见)
4. 与 CensorEngine 无缝桥接 (接收路径、BGR numpy 数组或 PIL 图像，输出混合图像)
"""
import os
import platform
from pathlib import Path
from typing import Union, Tuple, Optional, List, Dict

# winreg is Windows-only; import safely
try:
    import winreg  # type: ignore
except Exception:
    winreg = None

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

try:
    from logger import get_logger
    logger = get_logger("WatermarkEngine")
except Exception:
    import logging
    logger = logging.getLogger("WatermarkEngine")

if platform.system() == "Windows":
    FONTS_DIR = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
else:
    FONTS_DIR = Path(os.environ.get("FONTS_DIR", "/usr/share/fonts"))

# 常用高质量艺术家中英文字体速查映射 (含 Qt Family 映射与粗体特征)
CURATED_FONTS_DETAIL = {
    "微软雅黑 (Microsoft YaHei)": {"family": "Microsoft YaHei", "file": "msyh.ttc"},
    "微软雅黑 粗体 (YaHei Bold)": {"family": "Microsoft YaHei", "file": "msyhbd.ttc", "bold": True},
    "黑体 (SimHei)": {"family": "SimHei", "file": "simhei.ttf"},
    "楷体 (KaiTi)": {"family": "KaiTi", "file": "simkai.ttf"},
    "宋体 (SimSun)": {"family": "SimSun", "file": "simsun.ttc"},
    "Arial (经典无衬线)": {"family": "Arial", "file": "arial.ttf"},
    "Arial Bold (粗体)": {"family": "Arial", "file": "arialbd.ttf", "bold": True},
    "Arial Black (极粗醒目)": {"family": "Arial Black", "file": "ariblk.ttf"},
    "Impact (硬朗张力)": {"family": "Impact", "file": "impact.ttf"},
    "Segoe UI (现代界面字体)": {"family": "Segoe UI", "file": "segoeui.ttf"},
    "Segoe UI Bold": {"family": "Segoe UI", "file": "segoeuib.ttf", "bold": True},
    "Georgia (优雅衬线)": {"family": "Georgia", "file": "georgia.ttf"},
    "Times New Roman (经典衬线)": {"family": "Times New Roman", "file": "times.ttf"},
    "Comic Sans MS (卡通动漫风格)": {"family": "Comic Sans MS", "file": "comic.ttf"},
    "Trebuchet MS": {"family": "Trebuchet MS", "file": "trebuc.ttf"},
    "Verdana": {"family": "Verdana", "file": "verdana.ttf"},
}

CURATED_FONTS = {k: v["file"] for k, v in CURATED_FONTS_DETAIL.items()}

class WatermarkEngine:
    def __init__(self):
        self._font_cache: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._system_fonts_cache: Optional[Dict[str, str]] = None

    def get_font_family(self, font_name: str) -> Tuple[str, bool]:
        """获取适用于 Qt QFont 的标准 Family 名称与是否粗体标志"""
        if font_name in CURATED_FONTS_DETAIL:
            info = CURATED_FONTS_DETAIL[font_name]
            return info["family"], info.get("bold", False)
        clean = font_name.split(" (")[0].strip()
        is_bold = "bold" in font_name.lower() or "粗" in font_name
        return clean, is_bold

    def get_available_font_names(self) -> List[str]:
        """获取所有可用字体名称列表（精选推荐字体在前，系统其余字体按字母排序）"""
        names = list(CURATED_FONTS.keys())
        sys_fonts = self._get_system_fonts_map()
        for k in sorted(sys_fonts.keys()):
            if k not in names and not any(k.lower() in c.lower() for c in names):
                names.append(k)
        return names

    def _get_system_fonts_map(self) -> Dict[str, str]:
        if self._system_fonts_cache is not None:
            return self._system_fonts_cache

        fonts_map = {}
        if winreg is not None:
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
                )
                count = winreg.QueryInfoKey(key)[1]
                for i in range(count):
                    val_name, val_data, _ = winreg.EnumValue(key, i)
                    clean_name = val_name.replace(" (TrueType)", "").replace(" (OpenType)", "").strip()
                    font_file = val_data if os.path.isabs(val_data) else str(FONTS_DIR / val_data)
                    if os.path.exists(font_file):
                        fonts_map[clean_name] = font_file
                winreg.CloseKey(key)
            except Exception:
                pass

        # If not Windows or registry gave nothing, scan FONTS_DIR if it exists
        if not fonts_map and FONTS_DIR.exists():
            try:
                for p in FONTS_DIR.rglob("*"):
                    if p.suffix.lower() in {".ttf", ".ttc", ".otf"}:
                        fonts_map[p.stem] = str(p)
            except Exception:
                pass

        self._system_fonts_cache = fonts_map
        return fonts_map

    def resolve_font(self, font_name: str, size: int = 36) -> ImageFont.FreeTypeFont:
        """根据字体名智能加载 PIL TrueType 字体"""
        size = max(1, int(size))
        cache_key = (font_name, size)
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        font_obj = None

        # 1. 尝试精选字体表
        if font_name in CURATED_FONTS_DETAIL:
            file_name = CURATED_FONTS_DETAIL[font_name]["file"]
            p = FONTS_DIR / file_name
            if p.exists():
                try:
                    font_obj = ImageFont.truetype(str(p), size)
                except Exception:
                    font_obj = None

        # 2. 尝试根据注册表匹配
        if font_obj is None:
            sys_fonts = self._get_system_fonts_map()
            target_path = None
            if font_name in sys_fonts:
                target_path = sys_fonts[font_name]
            else:
                for k, v in sys_fonts.items():
                    if font_name.lower() in k.lower() or k.lower() in font_name.lower():
                        target_path = v
                        break
            if target_path and os.path.exists(target_path):
                try:
                    font_obj = ImageFont.truetype(target_path, size)
                except Exception as e:
                    logger.debug(f"[Watermark] 加载系统字体 {target_path} 异常: {e}")
                    font_obj = None

        # 3. 尝试直接按文件名查找
        if font_obj is None:
            for ext in ["", ".ttf", ".ttc", ".otf"]:
                p = FONTS_DIR / f"{font_name}{ext}"
                if p.exists():
                    try:
                        font_obj = ImageFont.truetype(str(p), size)
                        break
                    except Exception as e:
                        logger.debug(f"[Watermark] 加载字体文件 {p} 异常: {e}")

        # 4. 回退保底字体 (微软雅黑 -> 黑体 -> Arial -> 默认)
        if font_obj is None:
            for fb in ["msyh.ttc", "simhei.ttf", "arial.ttf"]:
                p = FONTS_DIR / fb
                if p.exists():
                    try:
                        font_obj = ImageFont.truetype(str(p), size)
                        break
                    except Exception as e:
                        logger.debug(f"[Watermark] 加载保底字体 {p} 异常: {e}")

        if font_obj is None:
            font_obj = ImageFont.load_default()

        self._font_cache[cache_key] = font_obj
        return font_obj

    @staticmethod
    def compute_font_px(
        w: int,
        h: Optional[Union[int, float]] = None,
        font_size_pct: Optional[float] = None,
        **kwargs
    ) -> int:
        """
        单一换算点：长边制。按 max(w, h) * font_size_pct 计算水印像素字号，严格钳制在 [14, 120] 像素区间内。

        支持调用模式：
        1. compute_font_px(w, h, font_size_pct) -> 传入宽、高与百分比 (标准形式)
        2. compute_font_px(w, font_size_pct) -> 兼容单尺寸传入 (将 w 作为长边)
        3. compute_font_px(w=..., h=..., font_size_pct=...) -> 关键字传参
        """
        if font_size_pct is not None:
            pct = float(font_size_pct)
            actual_h = int(h) if h is not None else 0
        elif h is not None:
            if isinstance(h, float) or (isinstance(h, (int, float)) and h < 20):
                pct = float(h)
                actual_h = 0
            else:
                actual_h = int(h)
                pct = float(kwargs.get("font_size_pct", 3.0))
        else:
            pct = float(kwargs.get("font_size_pct", 3.0))
            actual_h = 0

        long_edge = max(int(w), actual_h)
        calc_px = round(long_edge * pct / 100.0)
        return max(14, min(120, calc_px))

    def apply_watermark(
        self,
        image_input: Union[str, Path, np.ndarray, Image.Image],
        text: str = "@FOXTONTONTON",
        font_name: str = "微软雅黑 (Microsoft YaHei)",
        font_size_pct: float = 3.0,
        opacity: float = 0.65,
        position: str = "bottom_right",
        color_hex: str = "#ffffff",
        enable_stroke: bool = True,
        output_path: Optional[Union[str, Path]] = None
    ) -> Tuple[Image.Image, np.ndarray]:
        """
        在图像上渲染高质感文字水印
        
        参数:
        - image_input: 图像路径、OpenCV BGR numpy 数组或 PIL Image
        - text: 水印文本
        - font_name: 字体族或注册表名称
        - font_size_pct: 字体占图长边百分比 (默认 3.0%)
        - opacity: 不透明度 0.05 ~ 1.0
        - position: "bottom_right" | "bottom_left" | "top_right" | "top_left" | "center" | "bottom_center" | "tiled"
        - color_hex: 十六进制颜色代码 (默认纯白 "#ffffff")
        - enable_stroke: 是否启用抗混色边缘阴影/描边 (确保亮暗背景皆清晰)
        - output_path: 可选保存路径
        
        返回:
        - (pil_image, bgr_numpy_array)
        """
        # 1. 统一转换为 PIL Image (RGBA)
        if isinstance(image_input, (str, Path)):
            pil_base = Image.open(str(image_input)).convert("RGBA")
        elif isinstance(image_input, np.ndarray):
            rgb = cv2.cvtColor(image_input, cv2.COLOR_BGR2RGB)
            pil_base = Image.fromarray(rgb).convert("RGBA")
        elif isinstance(image_input, Image.Image):
            pil_base = image_input.convert("RGBA")
        else:
            raise ValueError(f"不支持的输入图像类型: {type(image_input)}")

        w, h = pil_base.size

        if not text or not text.strip():
            # 没有文字时直接返回原图
            rgb_final = pil_base.convert("RGB")
            bgr_arr = cv2.cvtColor(np.array(rgb_final), cv2.COLOR_RGB2BGR)
            if output_path:
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                rgb_final.save(str(output_path), quality=95)
            return rgb_final, bgr_arr

        # 2. 计算颜色和 Alpha 通道
        alpha_val = int(max(0.05, min(1.0, float(opacity))) * 255)
        hex_str = color_hex.lstrip("#")
        if len(hex_str) == 6:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
        else:
            r, g, b = 255, 255, 255

        text_color = (r, g, b, alpha_val)
        
        # 自动描边颜色（与文字互补或柔和半透明暗黑）
        stroke_alpha = int(alpha_val * 0.85)
        # 如果文字本身偏亮，描边用黑色；若文字偏暗，描边用浅白
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        if luminance > 128:
            stroke_color = (0, 0, 0, stroke_alpha)
        else:
            stroke_color = (255, 255, 255, stroke_alpha)

        font_px = self.compute_font_px(w, h, font_size_pct)
        stroke_width = max(1, font_px // 18) if enable_stroke else 0

        # 加载字体
        font = self.resolve_font(font_name, font_px)

        # 3. 创建半透明水印图层
        watermark_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(watermark_layer)

        # 计算文字精准包围盒边界
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        b_left, b_top, b_right, b_bottom = bbox
        tw = b_right - b_left
        th = b_bottom - b_top

        margin_x = max(24, int(w * 0.028))
        margin_y = max(24, int(h * 0.028))

        if position == "tiled":
            # 倾斜平铺防盗印 (角度 28°，行列奇偶交错)
            step_w = max(tw + 120, 220)
            step_h = max(th + 80, 120)
            stamp = Image.new("RGBA", (step_w, step_h), (0, 0, 0, 0))
            sdraw = ImageDraw.Draw(stamp)
            sdraw.text(
                (step_w // 2 - tw // 2 - b_left, step_h // 2 - th // 2 - b_top),
                text,
                font=font,
                fill=text_color,
                stroke_width=stroke_width,
                stroke_fill=stroke_color if enable_stroke else None
            )
            rot_stamp = stamp.rotate(28, expand=True, resample=Image.BICUBIC)
            rw, rh = rot_stamp.size

            row_idx = 0
            for py in range(-rh, h + rh, rh):
                x_offset = (rw // 2) if (row_idx % 2 == 1) else 0
                for px in range(-rw - rw // 2, w + rw, rw):
                    watermark_layer.paste(rot_stamp, (px + x_offset, py), rot_stamp)
                row_idx += 1

        else:
            # 单一位置：精准扣除文字包围盒 offset，确保 margin 绝对精确，绝不溢出或偏移
            if position == "bottom_right":
                tx = w - margin_x - b_right
                ty = h - margin_y - b_bottom
            elif position == "bottom_left":
                tx = margin_x - b_left
                ty = h - margin_y - b_bottom
            elif position == "top_right":
                tx = w - margin_x - b_right
                ty = margin_y - b_top
            elif position == "top_left":
                tx = margin_x - b_left
                ty = margin_y - b_top
            elif position == "center":
                tx = (w - (b_right + b_left)) // 2
                ty = (h - (b_bottom + b_top)) // 2
            elif position == "bottom_center":
                tx = (w - (b_right + b_left)) // 2
                ty = h - margin_y - b_bottom
            else:
                tx = w - margin_x - b_right
                ty = h - margin_y - b_bottom

            # 绘制文本
            draw.text(
                (tx, ty),
                text,
                font=font,
                fill=text_color,
                stroke_width=stroke_width,
                stroke_fill=stroke_color if enable_stroke else None
            )

        # 4. 图层 Alpha 融合
        composite = Image.alpha_composite(pil_base, watermark_layer)
        final_rgb = composite.convert("RGB")
        bgr_out = cv2.cvtColor(np.array(final_rgb), cv2.COLOR_RGB2BGR)

        if output_path:
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            final_rgb.save(str(out_p), quality=95)

        return final_rgb, bgr_out

    def apply_watermark_gif(
        self,
        gif_path: Union[str, Path],
        text: str = "@FOXTONTONTON",
        font_name: str = "微软雅黑 (Microsoft YaHei)",
        font_size_pct: float = 3.0,
        opacity: float = 0.65,
        position: str = "bottom_right",
        color_hex: str = "#ffffff",
        enable_stroke: bool = True,
        output_path: Optional[Union[str, Path]] = None
    ) -> Path:
        """为多帧动态 GIF 图片中的每一帧添加水印，并保持原生帧率与循环配置"""
        from PIL import ImageSequence
        im = Image.open(str(gif_path))
        duration = im.info.get("duration", 100)
        loop = im.info.get("loop", 0)

        out_frames = []
        for frame in ImageSequence.Iterator(im):
            fr_rgb = frame.convert("RGB")
            wm_frame, _ = self.apply_watermark(
                fr_rgb,
                text=text,
                font_name=font_name,
                font_size_pct=font_size_pct,
                opacity=opacity,
                position=position,
                color_hex=color_hex,
                enable_stroke=enable_stroke
            )
            out_frames.append(wm_frame)

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
        return out_p
