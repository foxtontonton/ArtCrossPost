# -*- coding: utf-8 -*-
"""
ArtCrossPost - Publish History & Image Fingerprint Manager (P1-2)
"""
import json
import hashlib
from datetime import datetime
from pathlib import Path
from PIL import Image

PLATFORM_DISPLAY_SHORT = {
    "deviantart": "DA",
    "x_twitter": "X",
    "patreon": "Patreon",
    "kofi": "Ko-fi",
    "reddit": "Reddit",
    "civitai": "Civitai"
}


def compute_image_fingerprints(image_path: Path | str) -> dict:
    """
    计算图片指纹:
    - sha256: 文件内容精准哈希
    - dhash: 64-bit 差分感知哈希 (抗轻微转码/元数据变更)
    """
    p = Path(image_path)
    if not p.exists() or not p.is_file():
        return {}

    # 1. SHA-256
    sha256_hasher = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256_hasher.update(chunk)
        sha256_val = sha256_hasher.hexdigest()
    except Exception:
        sha256_val = ""

    # 2. dHash (8x8 difference hash)
    dhash_val = ""
    try:
        with Image.open(p) as img:
            gray = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(gray.getdata())
            diff = []
            for row in range(8):
                for col in range(8):
                    diff.append(pixels[row * 9 + col] > pixels[row * 9 + col + 1])
            dhash_val = hex(int("".join(["1" if b else "0" for b in diff]), 2))[2:].zfill(16)
    except Exception:
        dhash_val = ""

    return {
        "sha256": sha256_val,
        "dhash": dhash_val
    }


class PublishHistoryManager:
    """管理发布历史持久化与指纹检索 (data/publish_history.json)"""

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.history_file = self.data_dir / "publish_history.json"
        self._ensure_file()

    def _ensure_file(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.history_file.exists():
            try:
                self.history_file.write_text("[]", encoding="utf-8")
            except Exception:
                pass

    def load_history(self) -> list[dict]:
        """按时间倒序返回所有历史记录 (最新在前)"""
        if not self.history_file.exists():
            return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    # 倒序排列 (最新在前)
                    return list(reversed(data))
        except Exception:
            return []
        return []

    def append_record(
        self,
        title: str,
        image_paths: list[str],
        publish_results: dict,
        payload: dict = None
    ) -> dict:
        """
        每次发布结束后追加一条历史记录:
        - 时间戳
        - 各图指纹 (sha256 & dhash)
        - 各平台成败状态及直链 URL
        """
        now = datetime.now()
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # 收集图片指纹
        fingerprints = []
        for p in image_paths or []:
            fp = compute_image_fingerprints(p)
            if fp.get("sha256"):
                fingerprints.append({
                    "path": Path(p).name,
                    "sha256": fp["sha256"],
                    "dhash": fp.get("dhash", "")
                })

        # 整理平台发布结果
        platforms_data = {}
        for plat_name, res in (publish_results or {}).items():
            success = res[0] if isinstance(res, (tuple, list)) else bool(res)
            msg = res[1] if isinstance(res, (tuple, list)) and len(res) > 1 else str(res)

            # 提取 URL (若 msg 中包含 URL)
            url = ""
            import re
            m = re.search(r'https?://\S+', msg)
            if m:
                url = m.group(0).rstrip(").,;")

            platforms_data[plat_name] = {
                "success": bool(success),
                "url": url,
                "message": msg
            }

        record = {
            "id": now.strftime("%Y%m%d_%H%M%S_%f"),
            "timestamp": timestamp_str,
            "title": title or "未命名作品",
            "image_fingerprints": fingerprints,
            "platforms": platforms_data
        }

        # 读取并追加写入
        all_records = []
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    all_records = json.load(f)
                    if not isinstance(all_records, list):
                        all_records = []
            except Exception:
                all_records = []

        all_records.append(record)

        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(all_records, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return record

    def check_duplicate_image(self, image_path: Path | str) -> dict | None:
        """
        导入图片时查历史：
        若命中已发布过的图片指纹，返回匹配历史记录信息:
        {
            "date": "YYYY-MM-DD",
            "platforms": ["DA", "X"],
            "title": "作品标题",
            "raw_record": {...}
        }
        未命中返回 None
        """
        fp = compute_image_fingerprints(image_path)
        sha = fp.get("sha256")
        dh = fp.get("dhash")
        if not sha and not dh:
            return None

        # 遍历历史记录 (倒序优先匹配最近的记录)
        history = self.load_history()
        for record in history:
            fps = record.get("image_fingerprints", [])
            hit = False
            for old_fp in fps:
                if sha and old_fp.get("sha256") == sha:
                    hit = True
                    break
                if dh and old_fp.get("dhash") and old_fp.get("dhash") == dh:
                    hit = True
                    break

            if hit:
                # 提取历史发布的成功平台
                succ_platforms = []
                for p_name, p_info in record.get("platforms", {}).items():
                    if p_info.get("success"):
                        short_name = PLATFORM_DISPLAY_SHORT.get(p_name, p_name)
                        if short_name not in succ_platforms:
                            succ_platforms.append(short_name)

                ts = record.get("timestamp", "")
                date_str = ts[:10] if len(ts) >= 10 else ts

                return {
                    "date": date_str,
                    "platforms": succ_platforms,
                    "title": record.get("title", ""),
                    "raw_record": record
                }

        return None

    def find_published_da_url(self, image_paths: list[str | Path]) -> str | None:
        """
        按图集顺序逐张查历史记录，检索首个成功的 DeviantArt 帖子直链:
        - 仅在 DeviantArt 平台发布成功且 url 非空时命中
        - 清洗: 剥除 ? 查询参数、# 锚点及尾随斜杠
        - 正则校验: 必须符合 deviantart.com/.../art/... 形状 (阶段1 同款正则，不区分大小写)
        - 历史记录缺失/损坏/无命中一律安全返回 None，严禁抛错阻断发布
        """
        try:
            if not image_paths:
                return None

            import re
            da_pattern = re.compile(
                r"^https?://(?:www\.)?deviantart\.com/[^/\s]+/art/[^/\s]+$",
                re.IGNORECASE
            )

            for p in image_paths:
                dup = self.check_duplicate_image(p)
                if not dup:
                    continue
                raw_record = dup.get("raw_record") or {}
                da_info = raw_record.get("platforms", {}).get("deviantart", {})
                if not da_info.get("success"):
                    continue
                raw_url = str(da_info.get("url") or "").strip()
                if not raw_url:
                    continue

                cleaned_url = raw_url.split("?")[0].split("#")[0].rstrip("/")
                if da_pattern.match(cleaned_url):
                    return cleaned_url
                return None
        except Exception:
            return None
        return None

