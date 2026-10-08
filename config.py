# -*- coding: utf-8 -*-
"""
ArtCrossPost - Configuration & Presets Manager
"""
import os
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
    "ui_font_size": 13,              # 11, 12, 13, 14, 15, 16

    # 2. 常用内容与固定标签偏好
    "default_tags": "original, oc, anime, illustration",  # 常驻固定标签
    "auto_fill_default_tags": True,                       # 新建作品/清空时是否自动预填
    "signature_template": "\n\n---\nSupport my work:\nPatreon: https://patreon.com/yourname\nKo-fi: https://ko-fi.com/yourname",

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
        "font_size": 38,
        "opacity": 0.65,
        "position": "bottom_right",
        "color": "#ffffff",
        "stroke": True
    },
    "selected_platforms": ["x_twitter", "deviantart"],  # 记录上次选中的发布平台
    "defaults": {
        "is_ai_generated": True,
        "x_sensitive": True,
        "x_premium": False,
        "x_over_limit_policy": "ask",     # "ask", "truncate", "thread"
        "deviantart_mature": "strict",
        "patreon_visibility": "patrons",  # "public", "patrons", "tiers"
        "kofi_visibility": "supporters"   # "everyone", "supporters", "tier"
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
