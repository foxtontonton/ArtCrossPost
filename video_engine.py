# -*- coding: utf-8 -*-
"""
ArtCrossPost - X 视频变体自动配对与打码/水印合成引擎 (video_engine.py)
职责:
1. 通过 imageio-ffmpeg 获取内置 ffmpeg 二进制，缺失时安全降级禁用并提示
2. 在 ComfyUI output 目录递归查找候选视频 (.mp4/.mov/.webm)，按 3 级优先级自动配对 GIF:
   a. 同目录同主名 -> 直接配对
   b. 同主名在其他子目录 -> 配对
   c. 主名仅尾部计数不同 (如去掉 _00001 后相同) -> 按计数差最小配对
   遵循先到先得 (已配对视频不重复配对)、孤儿视频直接忽略
3. 按有效打码框 (_get_effective_censor_boxes) 与样式生成 ffmpeg 滤镜:
   - black_bar -> drawbox 填充
   - mosaic    -> crop 缩放贴回 (flags=neighbor)
   - blur      -> crop 高斯模糊贴回 (gblur)
4. 水印通过 PIL / WatermarkEngine 按 size_pct 渲染透明 RGBA PNG，再经 ffmpeg overlay 合成
5. 输出编码: libx264 / yuv420p / crf 19 / +faststart，保持原分辨率与帧率
6. 产物按 (源文件指纹 + 参数哈希) 缓存至 data/cache，未变更不重算；实时解析 stderr time= 汇报进度
7. 发布前自查校验: 配对视频存在、产物 <= 512MB、时长 <= 140s (从 ffmpeg -i 解析)
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from PIL import Image, ImageDraw

try:
    from logger import get_logger
except ImportError:
    import logging
    def get_logger(name):
        return logging.getLogger(name)

from watermark_engine import WatermarkEngine

logger = get_logger("VideoEngine")

SUPPORTED_VIDEO_EXTS = {".mp4", ".mov", ".webm"}
_EXT_PRIORITY = {".mp4": 0, ".mov": 1, ".webm": 2}


def get_ffmpeg_path() -> Optional[str]:
    """
    通过 imageio_ffmpeg.get_ffmpeg_exe() 获取 ffmpeg 可执行文件路径。
    取不到或文件不存在时返回 None。
    """
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return str(exe)
    except Exception as e:
        logger.debug(f"[VideoEngine] 获取 imageio-ffmpeg 路径失败: {e}")
    return None


def is_ffmpeg_available() -> bool:
    """检查内置 ffmpeg 是否可用"""
    return get_ffmpeg_path() is not None


def split_stem_and_counter(stem: str) -> Tuple[str, Optional[int]]:
    """
    拆分主文件名与尾部计数器后缀（如 ComfyUI 的 _00001 或 _00001_ 或 -0012）:
    - 'Latex_Clip_00001'  -> ('Latex_Clip', 1)
    - 'Latex_Clip_00002_' -> ('Latex_Clip', 2)
    - 'Latex_Clip'        -> ('Latex_Clip', None)
    """
    s = stem.strip()
    m = re.match(r"^(.*?)[_\-\s]+(\d+)_?$", s)
    if m:
        base = m.group(1).strip(" _-")
        if base:
            return base, int(m.group(2))
    return s.rstrip("_"), None


def _norm_path_key(p: Union[str, Path]) -> str:
    """跨平台归一化路径键 (Windows 下不区分大小写)"""
    try:
        resolved = str(Path(p).resolve())
    except Exception:
        resolved = str(Path(p))
    return os.path.normcase(os.path.normpath(resolved))


def collect_candidate_videos(
    output_dir: Optional[Union[str, Path]] = None,
    extra_dirs: Optional[List[Union[str, Path]]] = None,
    recursive_extra: bool = False,
) -> List[Path]:
    """
    收集候选视频文件 (.mp4/.mov/.webm):
    - 若配置了 output_dir 且目录存在，在 output_dir 内递归查找所有候选视频
    - 同时扫描 extra_dirs (如导入 GIF 所在目录，保证同目录同主名在任何位置皆可命中)
    """
    seen: Set[str] = set()
    candidates: List[Path] = []

    def _add_file(fp: Path):
        if fp.suffix.lower() not in SUPPORTED_VIDEO_EXTS:
            return
        try:
            if not fp.is_file():
                return
        except Exception:
            return
        key = _norm_path_key(fp)
        if key not in seen:
            seen.add(key)
            candidates.append(fp.resolve())

    if output_dir:
        out_p = Path(output_dir)
        if out_p.exists() and out_p.is_dir():
            for root, _dirs, files in os.walk(str(out_p)):
                for fn in sorted(files):
                    fp = Path(root) / fn
                    if fp.suffix.lower() in SUPPORTED_VIDEO_EXTS:
                        _add_file(fp)

    if extra_dirs:
        for d in extra_dirs:
            if not d:
                continue
            dp = Path(d)
            if not dp.exists() or not dp.is_dir():
                continue
            if recursive_extra:
                for root, _dirs, files in os.walk(str(dp)):
                    for fn in sorted(files):
                        fp = Path(root) / fn
                        if fp.suffix.lower() in SUPPORTED_VIDEO_EXTS:
                            _add_file(fp)
            else:
                try:
                    for fp in sorted(dp.iterdir()):
                        if fp.suffix.lower() in SUPPORTED_VIDEO_EXTS:
                            _add_file(fp)
                except Exception:
                    pass

    return candidates


def find_matching_video_for_gif(
    gif_path: Union[str, Path],
    candidate_videos: List[Path],
    used_videos: Optional[Set[str]] = None,
) -> Optional[Path]:
    """
    为单张 GIF 在候选视频列表中按优先级查找最佳配对视频:
    a. 同目录同主名 -> 直接配对
    b. 同主名在其他子目录 -> 配对
    c. 主名仅尾部计数不同 (去掉 _00001 这类后缀后相同) -> 按计数差最小配对
    已被配对的候选视频 (在 used_videos 中) 不再参与配对。
    """
    gif_p = Path(gif_path)
    if gif_p.suffix.lower() != ".gif":
        return None

    used = used_videos if used_videos is not None else set()
    available = [c for c in candidate_videos if _norm_path_key(c) not in used]
    if not available:
        return None

    gif_dir_key = _norm_path_key(gif_p.parent)
    gif_stem_lower = gif_p.stem.strip().lower()
    gif_base, gif_cnt = split_stem_and_counter(gif_p.stem)
    gif_base_lower = gif_base.lower()

    # 优先级 a: 同目录 + 同主名
    tier_a: List[Tuple[int, str, Path]] = []
    # 优先级 b: 其他子目录 + 同主名
    tier_b: List[Tuple[int, str, Path]] = []
    # 优先级 c: 主名仅尾部计数不同 -> 按计数差最小配对
    tier_c: List[Tuple[int, int, int, int, str, Path]] = []

    for cand in available:
        cand_dir_key = _norm_path_key(cand.parent)
        cand_stem_lower = cand.stem.strip().lower()
        ext_prio = _EXT_PRIORITY.get(cand.suffix.lower(), 9)

        if cand_stem_lower == gif_stem_lower:
            if cand_dir_key == gif_dir_key:
                tier_a.append((ext_prio, str(cand).lower(), cand))
            else:
                tier_b.append((ext_prio, str(cand).lower(), cand))
            continue

        cand_base, cand_cnt = split_stem_and_counter(cand.stem)
        if cand_base.lower() == gif_base_lower and (gif_cnt is not None or cand_cnt is not None):
            g_num = gif_cnt if gif_cnt is not None else 0
            c_num = cand_cnt if cand_cnt is not None else 0
            diff = abs(g_num - c_num)
            same_dir_rank = 0 if cand_dir_key == gif_dir_key else 1
            tier_c.append((diff, same_dir_rank, ext_prio, c_num, str(cand).lower(), cand))

    if tier_a:
        tier_a.sort(key=lambda x: (x[0], x[1]))
        return tier_a[0][2]

    if tier_b:
        tier_b.sort(key=lambda x: (x[0], x[1]))
        return tier_b[0][2]

    if tier_c:
        tier_c.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
        return tier_c[0][5]

    return None


def pair_gifs_with_videos(
    media_paths: List[Union[str, Path]],
    output_dir: Optional[Union[str, Path]] = None,
    already_used_videos: Optional[Set[str]] = None,
    extra_search_dirs: Optional[List[Union[str, Path]]] = None,
    recursive_extra: bool = False,
    comfyui_output_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Optional[str]]:
    """
    批量按导入顺序为 GIF 配对视频 (规则 2~4):
    - 先到先得：一个视频被配对后不再参与其他 GIF 的自动配对
    - 找不到 GIF 的孤儿视频直接忽略
    - 非 GIF 素材不参与配对
    返回: {str(gif_path): str(video_path) | None}
    """
    effective_output_dir = output_dir if output_dir is not None else comfyui_output_dir
    used: Set[str] = set()
    if already_used_videos:
        for v in already_used_videos:
            if v:
                used.add(_norm_path_key(v))

    parent_dirs: List[Union[str, Path]] = []
    if extra_search_dirs:
        parent_dirs.extend(extra_search_dirs)
    for p in media_paths:
        pp = Path(p)
        if pp.suffix.lower() == ".gif":
            parent_dirs.append(pp.parent)

    candidates = collect_candidate_videos(
        output_dir=effective_output_dir,
        extra_dirs=parent_dirs if parent_dirs else None,
        recursive_extra=recursive_extra,
    )

    results: Dict[str, Optional[str]] = {}
    for p in media_paths:
        p_str = str(p)
        if not p_str.lower().endswith(".gif"):
            continue
        matched = find_matching_video_for_gif(p, candidates, used_videos=used)
        if matched is not None:
            used.add(_norm_path_key(matched))
            results[p_str] = str(matched)
        else:
            results[p_str] = None

    return results


def parse_ffmpeg_time_seconds(time_str: str) -> float:
    """将 HH:MM:SS.xx 时间字符串解析为秒数 (float)"""
    m = re.match(r"^(\d+):(\d+):(\d+(?:\.\d+)?)$", time_str.strip())
    if not m:
        return 0.0
    hours = int(m.group(1))
    minutes = int(m.group(2))
    seconds = float(m.group(3))
    return hours * 3600.0 + minutes * 60.0 + seconds


def probe_video_info(video_path: Union[str, Path], ffmpeg_exe: Optional[str] = None) -> Dict[str, Any]:
    """
    通过执行 `ffmpeg -i <video_path>` 解析视频基础元数据:
    - duration_sec: float (秒)
    - width: int
    - height: int
    - codec: str (如 'h264')
    - pix_fmt: str (如 'yuv420p')
    - fps: float
    - has_audio: bool
    """
    exe = ffmpeg_exe or get_ffmpeg_path()
    if not exe:
        raise RuntimeError("未检测到 imageio-ffmpeg / ffmpeg 二进制组件，无法解析视频信息")

    vp = Path(video_path)
    if not vp.is_file():
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    cmd = [exe, "-hide_banner", "-i", str(vp)]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=creationflags,
    )
    info_text = (proc.stderr or "") + "\n" + (proc.stdout or "")

    duration_sec: Optional[float] = None
    dur_match = re.search(r"Duration:\s*(\d+:\d+:\d+(?:\.\d+)?)", info_text)
    if dur_match:
        duration_sec = parse_ffmpeg_time_seconds(dur_match.group(1))

    width, height = 0, 0
    codec = ""
    pix_fmt = ""
    fps = 30.0

    for line in info_text.splitlines():
        if "Stream #" in line and "Video:" in line:
            v_part = line.split("Video:", 1)[1]
            c_m = re.match(r"\s*([a-zA-Z0-9_]+)", v_part)
            if c_m:
                codec = c_m.group(1).lower()

            res_m = re.search(r",\s*(\d{2,5})x(\d{2,5})(?:[\s,\[]|$)", v_part)
            if res_m:
                width = int(res_m.group(1))
                height = int(res_m.group(2))

            # 解析像素格式 (如 yuv420p(tv, bt709) 或 yuv420p)
            tokens = [t.strip() for t in re.split(r",(?![^(]*\))", v_part)]
            if len(tokens) >= 2:
                pf_raw = tokens[1].split("(")[0].strip().lower()
                if pf_raw and not re.match(r"^\d+x\d+$", pf_raw):
                    pix_fmt = pf_raw

            fps_m = re.search(r"(\d+(?:\.\d+)?)\s*fps", v_part)
            if fps_m:
                try:
                    fps = float(fps_m.group(1))
                except ValueError:
                    pass
            break

    has_audio = bool(re.search(r"Stream #.*Audio:", info_text))

    return {
        "duration_sec": duration_sec,
        "width": width,
        "height": height,
        "codec": codec,
        "pix_fmt": pix_fmt,
        "fps": fps,
        "has_audio": has_audio,
        "raw_output": info_text,
    }


def probe_video_duration(video_path: Union[str, Path], ffmpeg_exe: Optional[str] = None) -> Optional[float]:
    """从 ffmpeg -i 输出解析视频时长（秒）"""
    info = probe_video_info(video_path, ffmpeg_exe=ffmpeg_exe)
    return info.get("duration_sec")


def validate_x_video_constraints(
    video_path: Union[str, Path],
    max_size_mb: float = 512.0,
    max_duration_sec: float = 140.0,
    ffmpeg_exe: Optional[str] = None,
    label: str = "X 视频",
    check_size: bool = True,
) -> Tuple[bool, str]:
    """
    发布前自查门禁 (规则 13):
    - 视频文件存在且非空
    - 体积 <= 512MB
    - 时长 <= 140s (通过 ffmpeg -i 解析)
    """
    vp = Path(video_path) if video_path else None
    if not vp or not vp.exists() or not vp.is_file():
        name_hint = vp.name if vp else "未知路径"
        return False, f"{label}文件不存在: {name_hint}"

    f_size = vp.stat().st_size
    if f_size <= 0:
        return False, f"{label}文件大小为 0: {vp.name}"

    size_mb = f_size / (1024 * 1024)
    platform_name = "Bluesky" if "bluesky" in label.lower() else "X (Twitter)"
    if check_size and size_mb > max_size_mb:
        return False, f"{platform_name} 限制视频体积 ≤ {max_size_mb:.0f}MB (当前 {vp.name}: {size_mb:.2f}MB)"

    exe = ffmpeg_exe or get_ffmpeg_path()
    if not exe:
        return False, f"未检测到 imageio-ffmpeg / ffmpeg 组件，无法校验与处理 {label}"

    try:
        dur = probe_video_duration(vp, ffmpeg_exe=exe)
    except Exception as e:
        return False, f"解析 {label}时长失败 ({vp.name}): {e}"

    if dur is None:
        return False, f"无法从 ffmpeg -i 解析 {label}时长: {vp.name}"

    if dur > max_duration_sec:
        return False, f"{platform_name} 限制视频时长 ≤ {max_duration_sec:.0f}s (当前 {vp.name}: {dur:.1f}s)"

    return True, f"{label}校验通过 ({size_mb:.2f}MB, {dur:.1f}s)"


def validate_bluesky_video_constraints(
    video_path: Union[str, Path],
    max_size_mb: float = 300.0,
    max_duration_sec: float = 600.0,
    ffmpeg_exe: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Bluesky 视频门禁校验 (P0-1):
    - 视频文件存在且非空
    - 体积 ≤ 300MB
    - 时长 ≤ 600s (通过 ffmpeg -i 解析)
    """
    return validate_x_video_constraints(
        video_path=video_path,
        max_size_mb=max_size_mb,
        max_duration_sec=max_duration_sec,
        ffmpeg_exe=ffmpeg_exe,
        label="Bluesky 视频",
        check_size=True,
    )


def convert_gif_to_mp4(
    gif_path: Union[str, Path],
    output_mp4_path: Union[str, Path],
    ffmpeg_exe: Optional[str] = None,
) -> Path:
    """
    将渲染完成的 GIF 动图转换为 H.264 格式的 MP4 视频 (P0-2):
    - 编码: libx264
    - 像素格式: yuv420p
    - 偶数宽高对齐: scale=trunc(iw/2)*2:trunc(ih/2)*2
    - 流式播放支持: +faststart
    - 静音: -an
    """
    exe = ffmpeg_exe or get_ffmpeg_path()
    if not exe:
        raise RuntimeError("未检测到 imageio-ffmpeg / ffmpeg 二进制组件，无法执行 GIF 转视频")

    in_gif = Path(gif_path)
    if not in_gif.is_file():
        raise FileNotFoundError(f"待转码 GIF 文件不存在: {gif_path}")

    out_mp4 = Path(output_mp4_path)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_mp4.parent / f"{out_mp4.stem}.tmp_{os.getpid()}.mp4"

    cmd = [
        exe, "-y", "-hide_banner",
        "-i", str(in_gif),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
        "-c:v", "libx264",
        "-crf", "19",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-an",
        str(tmp_out)
    ]

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        creationflags=creationflags,
    )
    if proc.returncode != 0 or not tmp_out.exists() or tmp_out.stat().st_size == 0:
        err = proc.stderr or proc.stdout
        raise RuntimeError(f"GIF 转 MP4 视频失败 (exit={proc.returncode}): {err}")

    if out_mp4.exists():
        out_mp4.unlink(missing_ok=True)
    tmp_out.replace(out_mp4)
    logger.info(f"[VideoEngine] GIF 转码 MP4 成功: '{in_gif.name}' -> '{out_mp4.name}' ({out_mp4.stat().st_size / 1024:.1f} KB)")
    return out_mp4


class VideoEngine:
    """
    X 视频打码 + 水印合成与 H.264 转码缓存引擎
    """

    def __init__(self, cache_dir: Optional[Union[str, Path]] = None, watermark_engine: Optional[WatermarkEngine] = None):
        if cache_dir is None:
            from config import CACHE_DIR
            self.cache_dir = Path(CACHE_DIR)
        else:
            self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.watermark_engine = watermark_engine or WatermarkEngine()

    @staticmethod
    def compute_source_fingerprint(video_path: Union[str, Path]) -> str:
        """计算源视频文件快速指纹 (文件大小 + 修改时间纳秒 + 首尾 64KB 内容哈希)"""
        vp = Path(video_path)
        st = vp.stat()
        h = hashlib.sha256()
        h.update(f"{st.st_size}:{st.st_mtime_ns}:".encode("utf-8"))
        chunk_size = 65536
        with open(vp, "rb") as f:
            head = f.read(chunk_size)
            h.update(head)
            if st.st_size > chunk_size * 2:
                f.seek(max(0, st.st_size - chunk_size))
                tail = f.read(chunk_size)
                h.update(tail)
        return h.hexdigest()

    @classmethod
    def compute_cache_key(
        cls,
        video_path: Union[str, Path],
        censor_params: Optional[Dict[str, Any]] = None,
        wm_params: Optional[Dict[str, Any]] = None,
        ref_size: Optional[Tuple[int, int]] = None,
    ) -> str:
        """计算 (源文件指纹 + 打码/水印参数哈希) 的唯一缓存键"""
        src_fp = cls.compute_source_fingerprint(video_path)
        norm_censor = None
        if censor_params and censor_params.get("enabled") and censor_params.get("boxes"):
            norm_censor = {
                "enabled": True,
                "style": censor_params.get("style", "black_bar"),
                "boxes": [[int(v) for v in b] for b in censor_params.get("boxes", [])],
                "ref_size": list(ref_size) if ref_size else None,
            }
        norm_wm = None
        if wm_params and wm_params.get("enabled") and str(wm_params.get("text", "")).strip():
            norm_wm = {
                "enabled": True,
                "text": str(wm_params.get("text", "")).strip(),
                "font_name": wm_params.get("font_name", "微软雅黑 (Microsoft YaHei)"),
                "font_size_pct": round(float(wm_params.get("font_size_pct", 3.0)), 2),
                "opacity": round(float(wm_params.get("opacity", 0.65)), 3),
                "position": wm_params.get("position", "bottom_right"),
                "color_hex": str(wm_params.get("color_hex", "#ffffff")).lower(),
                "stroke": bool(wm_params.get("stroke", True)),
            }
        payload_str = json.dumps(
            {"src": src_fp, "censor": norm_censor, "wm": norm_wm},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

    def render_watermark_overlay_png(
        self,
        width: int,
        height: int,
        wm_params: Dict[str, Any],
        output_png_path: Union[str, Path],
    ) -> Path:
        """
        使用 PIL 按当前水印配置（size_pct 占视频长边、位置、透明度、颜色、描边）
        在 (width, height) 透明画布上渲染 RGBA PNG 供 ffmpeg overlay 合成。
        """
        w, h = max(2, int(width)), max(2, int(height))
        text = str(wm_params.get("text", "")).strip()
        font_name = wm_params.get("font_name", "微软雅黑 (Microsoft YaHei)")
        font_size_pct = float(wm_params.get("font_size_pct", 3.0))
        opacity = float(wm_params.get("opacity", 0.65))
        position = wm_params.get("position", "bottom_right")
        color_hex = str(wm_params.get("color_hex", "#ffffff"))
        enable_stroke = bool(wm_params.get("stroke", True))

        watermark_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if not text:
            out_p = Path(output_png_path)
            watermark_layer.save(str(out_p), format="PNG")
            return out_p

        alpha_val = int(max(0.05, min(1.0, opacity)) * 255)
        hex_str = color_hex.lstrip("#")
        if len(hex_str) == 6:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
        else:
            r, g, b = 255, 255, 255

        text_color = (r, g, b, alpha_val)
        stroke_alpha = int(alpha_val * 0.85)
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        stroke_color = (0, 0, 0, stroke_alpha) if luminance > 128 else (255, 255, 255, stroke_alpha)

        font_px = self.watermark_engine.compute_font_px(w, h, font_size_pct)
        stroke_width = max(1, font_px // 18) if enable_stroke else 0
        font = self.watermark_engine.resolve_font(font_name, font_px)

        draw = ImageDraw.Draw(watermark_layer)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        b_left, b_top, b_right, b_bottom = bbox
        tw = b_right - b_left
        th = b_bottom - b_top

        margin_x = max(24, int(w * 0.028))
        margin_y = max(24, int(h * 0.028))

        if position == "tiled":
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
                stroke_fill=stroke_color if enable_stroke else None,
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

            draw.text(
                (tx, ty),
                text,
                font=font,
                fill=text_color,
                stroke_width=stroke_width,
                stroke_fill=stroke_color if enable_stroke else None,
            )

        out_p = Path(output_png_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        watermark_layer.save(str(out_p), format="PNG")
        return out_p

    @staticmethod
    def build_filter_complex(
        boxes: List[List[int]],
        style: str,
        vid_w: int,
        vid_h: int,
        has_watermark_input: bool = False,
    ) -> Tuple[str, str]:
        """
        构建 ffmpeg filter_complex 字符串 (规则 7 & 8):
        - black_bar -> drawbox=x=...:y=...:w=...:h=...:color=black@1:t=fill
        - mosaic    -> crop -> scale 缩小 -> scale(flags=neighbor) 放大 -> overlay 贴回
        - blur      -> crop -> gblur -> overlay 贴回
        - 若 has_watermark_input=True，串联 [1:v] overlay=0:0:format=auto
        返回: (filter_complex_str, final_video_pad_label)
        """
        valid_boxes: List[Tuple[int, int, int, int]] = []
        for b in boxes or []:
            if len(b) < 4:
                continue
            x1 = max(0, min(vid_w - 2, int(round(b[0]))))
            y1 = max(0, min(vid_h - 2, int(round(b[1]))))
            x2 = max(x1 + 2, min(vid_w, int(round(b[2]))))
            y2 = max(y1 + 2, min(vid_h, int(round(b[3]))))
            bw = x2 - x1
            bh = y2 - y1
            if bw >= 2 and bh >= 2:
                valid_boxes.append((x1, y1, bw, bh))

        chains: List[str] = []
        cur_pad = "[0:v]"
        step_idx = 0

        if valid_boxes:
            if style == "black_bar":
                draw_filters = [
                    f"drawbox=x={x}:y={y}:w={bw}:h={bh}:color=black@1:t=fill"
                    for (x, y, bw, bh) in valid_boxes
                ]
                next_pad = "[vcens]"
                chains.append(f"{cur_pad}{','.join(draw_filters)}{next_pad}")
                cur_pad = next_pad
            elif style == "mosaic":
                for i, (x, y, bw, bh) in enumerate(valid_boxes):
                    cell = max(4, bw // 15)
                    sw = max(2, bw // cell)
                    sh = max(2, bh // cell)
                    split_base = f"[vbase_{i}]"
                    split_crop = f"[vcrop_{i}]"
                    mos_pad = f"[vmos_{i}]"
                    next_pad = f"[vcens_{i}]"
                    chains.append(f"{cur_pad}split=2{split_base}{split_crop}")
                    chains.append(
                        f"{split_crop}crop={bw}:{bh}:{x}:{y},"
                        f"scale={sw}:{sh}:flags=bilinear,"
                        f"scale={bw}:{bh}:flags=neighbor{mos_pad}"
                    )
                    chains.append(f"{split_base}{mos_pad}overlay={x}:{y}{next_pad}")
                    cur_pad = next_pad
            elif style == "blur":
                for i, (x, y, bw, bh) in enumerate(valid_boxes):
                    split_base = f"[vbase_{i}]"
                    split_crop = f"[vcrop_{i}]"
                    blur_pad = f"[vblur_{i}]"
                    next_pad = f"[vcens_{i}]"
                    chains.append(f"{cur_pad}split=2{split_base}{split_crop}")
                    chains.append(f"{split_crop}crop={bw}:{bh}:{x}:{y},gblur=sigma=20{blur_pad}")
                    chains.append(f"{split_base}{blur_pad}overlay={x}:{y}{next_pad}")
                    cur_pad = next_pad
            else:
                draw_filters = [
                    f"drawbox=x={x}:y={y}:w={bw}:h={bh}:color=black@1:t=fill"
                    for (x, y, bw, bh) in valid_boxes
                ]
                next_pad = "[vcens]"
                chains.append(f"{cur_pad}{','.join(draw_filters)}{next_pad}")
                cur_pad = next_pad

        if has_watermark_input:
            next_pad = "[vwm]"
            chains.append(f"{cur_pad}[1:v]overlay=0:0:format=auto{next_pad}")
            cur_pad = next_pad

        # 确保输出像素格式为 yuv420p (若宽高为奇数则自动向下对齐偶像素以满足 libx264 yuv420p 要求)
        final_pad = "[vout]"
        if vid_w % 2 != 0 or vid_h % 2 != 0:
            chains.append(f"{cur_pad}scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p{final_pad}")
        else:
            chains.append(f"{cur_pad}format=yuv420p{final_pad}")

        return ";".join(chains), final_pad

    def process_video(
        self,
        input_video_path: Union[str, Path],
        censor_params: Optional[Dict[str, Any]] = None,
        wm_params: Optional[Dict[str, Any]] = None,
        ref_size: Optional[Tuple[int, int]] = None,
        output_path: Optional[Union[str, Path]] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> Path:
        """
        对配对视频执行打码 + 水印合成并编码为 H.264 MP4 (规则 7~10):
        - 编码参数: libx264 / yuv420p / crf 19 / +faststart，保持原分辨率与帧率
        - 缓存机制: 以 (源文件指纹 + 参数哈希) 存入 data/cache，未变更直接复用
        - 进度汇报: 实时解析 ffmpeg stderr 的 `time=` 并回调 progress_callback(pct, msg)
        """
        exe = get_ffmpeg_path()
        if not exe:
            raise RuntimeError("未检测到 imageio-ffmpeg / ffmpeg 二进制组件，X 视频处理功能不可用")

        in_p = Path(input_video_path)
        if not in_p.is_file():
            raise FileNotFoundError(f"配对视频文件不存在: {in_p}")

        cache_key = self.compute_cache_key(in_p, censor_params=censor_params, wm_params=wm_params, ref_size=ref_size)
        target_p = Path(output_path) if output_path else (self.cache_dir / f"x_video_{cache_key[:16]}.mp4")
        target_p.parent.mkdir(parents=True, exist_ok=True)

        if target_p.exists() and target_p.stat().st_size > 0 and output_path is None:
            logger.info(f"[VideoEngine] 命中视频处理缓存，直接复用: {target_p.name}")
            if progress_callback:
                progress_callback(100, f"已复用缓存视频副本: {target_p.name}")
            return target_p

        v_info = probe_video_info(in_p, ffmpeg_exe=exe)
        vid_w = v_info["width"] or 800
        vid_h = v_info["height"] or 600
        total_dur = v_info.get("duration_sec") or 0.0
        has_audio = v_info.get("has_audio", False)

        # 提取并按分辨率比例映射有效打码框 (若 GIF 与视频分辨率不同，自动等比映射)
        boxes: List[List[int]] = []
        style = "black_bar"
        if censor_params and censor_params.get("enabled"):
            style = censor_params.get("style", "black_bar")
            raw_boxes = censor_params.get("boxes") or []
            if ref_size and ref_size[0] > 0 and ref_size[1] > 0 and (ref_size[0] != vid_w or ref_size[1] != vid_h):
                sx = vid_w / float(ref_size[0])
                sy = vid_h / float(ref_size[1])
                boxes = [
                    [int(round(b[0] * sx)), int(round(b[1] * sy)), int(round(b[2] * sx)), int(round(b[3] * sy))]
                    for b in raw_boxes if len(b) >= 4
                ]
            else:
                boxes = [[int(round(v)) for v in b[:4]] for b in raw_boxes if len(b) >= 4]

        wm_enabled = bool(wm_params and wm_params.get("enabled") and str(wm_params.get("text", "")).strip())
        tmp_wm_png: Optional[Path] = None
        tmp_out_mp4 = target_p.parent / f"{target_p.stem}.tmp_{os.getpid()}.mp4"

        try:
            if wm_enabled:
                fd, png_name = tempfile.mkstemp(prefix="acp_wm_", suffix=".png", dir=str(self.cache_dir))
                os.close(fd)
                tmp_wm_png = Path(png_name)
                self.render_watermark_overlay_png(vid_w, vid_h, wm_params, tmp_wm_png)

            filter_complex, final_pad = self.build_filter_complex(
                boxes=boxes,
                style=style,
                vid_w=vid_w,
                vid_h=vid_h,
                has_watermark_input=wm_enabled,
            )

            cmd = [exe, "-y", "-hide_banner", "-i", str(in_p)]
            if wm_enabled and tmp_wm_png:
                cmd.extend(["-i", str(tmp_wm_png)])

            cmd.extend([
                "-filter_complex", filter_complex,
                "-map", final_pad,
            ])
            if has_audio:
                cmd.extend(["-map", "0:a?", "-c:a", "aac", "-b:a", "128k"])
            else:
                cmd.extend(["-an"])

            cmd.extend([
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-crf", "19",
                "-movflags", "+faststart",
                str(tmp_out_mp4),
            ])

            logger.info(
                f"[VideoEngine] 开始编码 X 视频副本: 源='{in_p.name}' ({vid_w}x{vid_h}, {total_dur:.1f}s), "
                f"打码框={len(boxes)}处({style}), 水印={wm_enabled}"
            )
            if progress_callback:
                progress_callback(1, f"正在处理 X 视频副本: {in_p.name} (0%)...")

            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )

            stderr_tail: List[str] = []
            buf = ""
            while True:
                ch = proc.stderr.read(1) if proc.stderr else ""
                if not ch and proc.poll() is not None:
                    break
                if ch in ("\r", "\n"):
                    line = buf.strip()
                    buf = ""
                    if line:
                        stderr_tail.append(line)
                        if len(stderr_tail) > 30:
                            stderr_tail.pop(0)
                        t_m = re.search(r"time=\s*(\d+:\d+:\d+(?:\.\d+)?)", line)
                        if t_m and total_dur > 0 and progress_callback:
                            cur_sec = parse_ffmpeg_time_seconds(t_m.group(1))
                            pct = max(1, min(99, int(round((cur_sec / total_dur) * 100))))
                            progress_callback(pct, f"正在渲染 X 视频副本: {in_p.name} ({pct}%, {cur_sec:.1f}s/{total_dur:.1f}s)...")
                else:
                    buf += ch

            if buf.strip():
                stderr_tail.append(buf.strip())

            ret_code = proc.wait()
            if ret_code != 0 or not tmp_out_mp4.exists() or tmp_out_mp4.stat().st_size == 0:
                err_excerpt = "\n".join(stderr_tail[-8:])
                raise RuntimeError(f"ffmpeg 视频编码失败 (exit={ret_code}): {err_excerpt}")

            if target_p.exists():
                target_p.unlink(missing_ok=True)
            tmp_out_mp4.replace(target_p)

            if progress_callback:
                progress_callback(100, f"X 视频副本渲染完成: {target_p.name}")

            logger.info(
                f"[VideoEngine] X 视频副本渲染成功: '{target_p.name}' "
                f"({target_p.stat().st_size / (1024 * 1024):.2f}MB)"
            )
            return target_p
        finally:
            if tmp_wm_png and tmp_wm_png.exists():
                try:
                    tmp_wm_png.unlink(missing_ok=True)
                except Exception:
                    pass
            if tmp_out_mp4.exists():
                try:
                    tmp_out_mp4.unlink(missing_ok=True)
                except Exception:
                    pass
