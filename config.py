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

DEFAULT_CONFIG = {
    "censor": {
        "style": "black_bar",  # "black_bar", "mosaic", "blur"
        "coverage_mode": "moderate", # "slender", "moderate", "full"
        "coverage_scale": 0.55, # 55% moderate scale
        "padding_ratio": 0.15, # 15% safety expansion for full mode
        "mosaic_size": 20,
        "blur_radius": 35
    },
    "watermark": {
        "enabled": False,
        "text": "@FOXTONTONTON",
        "font_family": "微软雅黑 (Microsoft YaHei)",
        "font_size": 38,
        "opacity": 0.65,
        "position": "bottom_right",
        "color": "#ffffff",
        "stroke": True,
        "apply_to_publish": True
    },
    "defaults": {
        "is_ai_generated": True,
        "x_sensitive": True,
        "x_premium": False,
        "x_over_limit_policy": "ask",     # "ask", "truncate", "thread"
        "deviantart_mature": "strict",
        "patreon_visibility": "patrons",  # "public", "patrons", "tiers"
        "kofi_visibility": "supporters"   # "everyone", "supporters", "tier"
    },
    "signature_template": "\n\n---\nSupport my work:\nPatreon: https://patreon.com/yourname\nKo-fi: https://ko-fi.com/yourname"
}

def load_config():
    if PRESETS_FILE.exists():
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                user_conf = json.load(f)
                merged = dict(DEFAULT_CONFIG)
                for k, v in user_conf.items():
                    if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
                        merged[k] = {**merged[k], **v}
                    else:
                        merged[k] = v
                return merged
        except Exception:
            return DEFAULT_CONFIG
    return DEFAULT_CONFIG

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
