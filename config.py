# -*- coding: utf-8 -*-
"""
ArtCrossPost - Configuration & Presets Manager
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
COOKIES_DIR = DATA_DIR / "cookies"
PRESETS_FILE = DATA_DIR / "presets.json"
WINDOW_STATE_FILE = DATA_DIR / "window_state.json"
CACHE_DIR = DATA_DIR / "cache"

# Ensure directories exist
for d in [DATA_DIR, COOKIES_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

import copy

DEFAULT_CONFIG = {
    # 1. 界面外观配置
    "ui_theme": "pure_dark",         # "pure_dark"(极黑), "deep_navy"(深蓝), "charcoal"(炭灰)
    "ui_font_size": 13,              # 界面字体大小 (默认 13px, 支持 10~60px 平滑无档位调节)

    # 2. 常用内容与固定标签偏好
    "default_tags": "original, oc, anime, illustration",  # 常驻固定标签
    "auto_fill_default_tags": True,                       # 新建作品/清空时是否自动预填
    "signature_template": "---\nSupport my work:\nPatreon: https://patreon.com/yourname\nKo-fi: https://ko-fi.com/yourname",

    # 3. 打码与水印保护设置
    "censor": {
        "enabled": True,
        "style": "black_bar",  # "black_bar", "mosaic", "blur"
        "coverage_mode": "moderate", # "slender", "moderate", "full"
        "coverage_scale": 0.55, # 55% moderate scale
        "padding_ratio": 0.15, # 15% safety expansion for full mode
        "mosaic_size": 20,
        "blur_radius": 35
    },
    "watermark": {
        "enabled": False,
        "apply_to_publish": True,
        "text": "@FOXTONTONTON",
        "font_family": "微软雅黑 (Microsoft YaHei)",
        "size_pct": 3.0,
        "opacity": 0.65,
        "position": "bottom_right",
        "color": "#ffffff",
        "stroke": True
    },
    "selected_platforms": ["x_twitter", "deviantart"],  # 记录上次选中的发布平台
    "platform_kofi_enabled": False,                      # 是否在平台发布列表中启用/显示 Ko-fi (默认 False)
    "comfyui_output_dir": "",                            # ComfyUI output 目录 (用于 GIF 自动配对同名/计数视频变体)
    "log_drawer_expanded": True,                          # 底部日志栏默认展开状态
    "settings_dialog_size": [760, 680],                   # 系统设置窗口记忆尺寸 (宽, 高)
    "settings_dialog_tab": 0,                             # 系统设置窗口记忆激活选项卡
    "civitai_max_images_per_post": 20,                   # Civitai 单帖图片上限 (平台硬顶 20，配置只许调小)
    "civitai_resources": [],                             # Civitai 关联模型资源 (支持 ID / URL / {"id": ..., "version": ...})
    "civitai_resources_scope": "all",                    # 关联作用域: "all"(全图) | "cover"(仅封面首图)
    "use_da_post_link_for_traffic": True,                # DA 帖子 URL 透传引流到 X/Patreon (默认开启，失败回退主页)
    "civitai_tool": "ComfyUI",                           # 图片工具标注 (默认 "ComfyUI")
    "deviantart_keep_preset_description": True,           # DA 发布保留表单预设描述 (默认 True，关闭则直接替换)
    "deviantart_known_galleries": [
        "Featured",
        "NSFW - Ai Training Clip",
        "Cast of Characters",
        "Cage - A muscle growth story",
        "Film set"
    ],
    # 4. 压缩优化与并发控制策略
    "compression": {
        "compress_only_when_over_limit": True, # 默认只在超平台门禁时压缩，未超限保持原始画质
        "jpeg_quality": 88,                    # 压缩起始质量 (可调)
        "x_max_limit_mb": 4.8,                 # X 平台单图体积上限
        "kofi_max_limit_mb": 9.5,              # Ko-fi 平台单图体积上限
        "deviantart_max_limit_mb": 30.0,       # DeviantArt 单图体积上限
        "patreon_max_limit_mb": 50.0           # Patreon 单图体积上限
    },
    "publish_concurrency": 2,                 # 并发发布数 (默认 2，各平台独立持久化浏览器实例)
    "defaults": {
        "is_ai_generated": True,
        "x_sensitive": True,
        "x_premium": False,
        "x_over_limit_policy": "ask",     # "ask", "truncate", "thread"
        "x_censor": True,                 # X (Twitter) 专属打码保护开关
        "deviantart_mature": "strict",
        "deviantart_default_gallery": "", # 默认发布相册 (留空使用默认)
        "deviantart_censor": True,        # DeviantArt 专属打码保护开关
        "deviantart_keep_preset_description": True, # DA 发布保留表单预设描述 (默认 True，关闭则直接替换)
        "deviantart_max_images_per_deviation": 10, # 单作品图片上限 (默认10张，Core会员可调至100)
        "civitai_max_images_per_post": 20, # Civitai 单帖图片上限 (平台硬顶 20，配置只许调小)
        "civitai_resources": [],          # Civitai 关联模型资源 (支持 ID / URL / {"id": ..., "version": ...})
        "civitai_resources_scope": "all", # 关联作用域: "all"(全图) | "cover"(仅封面首图)
        "civitai_tool": "ComfyUI",        # 图片工具标注 (默认 "ComfyUI")
        "patreon_visibility": "patrons",  # "public", "patrons", "tiers"
        "patreon_censor": False,          # Patreon 专属打码保护开关 (默认原图，可勾选启用打码)
        "kofi_visibility": "supporters",  # "everyone", "supporters", "tier"
        "kofi_censor": False,             # Ko-fi 专属打码保护开关 (默认原图，可勾选启用打码)
        "kofi_min_duration": 20,          # Ko-fi 发布耗时安全护栏阈值 (秒，低于此值直接判异常以防假阳性)
        "x_wait_button_timeout": 120,     # X (Twitter) 发帖按钮等待就绪超时 (秒，应对服务端转码延迟)
        "x_da_link": "https://www.deviantart.com/foxtontonton", # X 推文预告引流 DA 链接
        "x_nsfw_notice": "🔞 NSFW full set on DeviantArt 👇",     # X 推文 NSFW 引流提示文案
        "use_da_post_link_for_traffic": True,             # DA 帖子 URL 透传引流到 X/Patreon (默认开启，失败回退主页)
        "bluesky_label": "porn",                          # Bluesky 成人分级 self-labels ("porn", "sexual", "nudity")
        "bluesky_ai_hashtags": True,                      # Bluesky AI 话题标签 (#AIArt #GenerativeArt)
        "bluesky_censor": True,                           # Bluesky 专属打码保护开关
        "bluesky_over_limit_policy": "ask"                # Bluesky 超限策略 ("ask", "truncate", "thread")
    }
}

def load_config():
    if PRESETS_FILE.exists():
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                user_conf = json.load(f)
                merged = copy.deepcopy(DEFAULT_CONFIG)
                for k, v in user_conf.items():
                    if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
                        merged[k] = {**merged[k], **v}
                    else:
                        merged[k] = v
                if isinstance(merged.get("watermark"), dict):
                    merged["watermark"].pop("font_size", None)
                # 平台硬顶约束：civitai_max_images_per_post 强制限制在 [1, 20]
                if "civitai_max_images_per_post" in merged:
                    merged["civitai_max_images_per_post"] = min(20, max(1, int(merged["civitai_max_images_per_post"])))
                if isinstance(merged.get("defaults"), dict) and "civitai_max_images_per_post" in merged["defaults"]:
                    merged["defaults"]["civitai_max_images_per_post"] = min(20, max(1, int(merged["defaults"]["civitai_max_images_per_post"])))

                # Civitai 资源配置规范化与非法元素剔除
                try:
                    from dispatchers.civitai import parse_civitai_resources
                    if "civitai_resources" in merged:
                        merged["civitai_resources"] = parse_civitai_resources(merged["civitai_resources"])
                    if isinstance(merged.get("defaults"), dict) and "civitai_resources" in merged["defaults"]:
                        merged["defaults"]["civitai_resources"] = parse_civitai_resources(merged["defaults"]["civitai_resources"])
                except Exception:
                    pass

                # Civitai 资源 scope 校验
                if "civitai_resources_scope" in merged:
                    merged["civitai_resources_scope"] = "cover" if str(merged["civitai_resources_scope"]).lower().strip() == "cover" else "all"
                if isinstance(merged.get("defaults"), dict) and "civitai_resources_scope" in merged["defaults"]:
                    merged["defaults"]["civitai_resources_scope"] = "cover" if str(merged["defaults"]["civitai_resources_scope"]).lower().strip() == "cover" else "all"

                # Civitai 工具标注校验
                if "civitai_tool" in merged and merged["civitai_tool"] is not None:
                    merged["civitai_tool"] = str(merged["civitai_tool"]).strip()
                if isinstance(merged.get("defaults"), dict) and "civitai_tool" in merged["defaults"] and merged["defaults"]["civitai_tool"] is not None:
                    merged["defaults"]["civitai_tool"] = str(merged["defaults"]["civitai_tool"]).strip()

                return merged
        except Exception:
            return copy.deepcopy(DEFAULT_CONFIG)
    return copy.deepcopy(DEFAULT_CONFIG)

def save_config(config_data):
    try:
        with open(PRESETS_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to save config: {e}")

def load_window_state():
    if WINDOW_STATE_FILE.exists():
        try:
            with open(WINDOW_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Failed to load window state: {e}")
    return None

def save_window_state(state_data):
    try:
        with open(WINDOW_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to save window state: {e}")
