# -*- coding: utf-8 -*-
"""
ArtCrossPost - X (Twitter) Text & Thread Processing Utilities
规范:
- URL 正则提取与统一折算为 23 字符
- 智能截断 (方案 A): 保护末尾 Patreon/Ko-fi 等签名与变现链接
- 智能串推切分 (Thread): 保护 URL 完整性，自然换行/句号断句，追加 (1/N) 序号
"""
import re

def calculate_x_char_count(text: str) -> int:
    """
    根据 X (Twitter) 官方规则计算加权字符数 (Twitter-Text Spec):
    - http(s):// 链接统一折算为 23 字符权重
    - 标准 Latin / ASCII / 常见半角字符 (codepoints 0~4351, 8192~8205, 8208~8223, 8242~8247) 权重为 1
    - CJK 汉字、日韩文字、中文全角标点符号 (如 ：、，、。)、Emoji 表情等权重为 2
    """
    if not text:
        return 0
    url_pattern = re.compile(r'https?://\S+')
    # 将所有 URL 替换为 23 个占位单宽字符
    text_without_urls = url_pattern.sub(" " * 23, text)
    count = 0
    for ch in text_without_urls:
        code = ord(ch)
        if (0 <= code <= 4351) or (8192 <= code <= 8205) or (8208 <= code <= 8223) or (8242 <= code <= 8247):
            count += 1
        else:
            count += 2
    return count


def truncate_x_text(full_text: str, limit: int = 280) -> tuple[str, int, bool]:
    """
    针对 X 平台进行智能截断 (优先级反转: 优先保留正文，签名最先被裁):
    裁剪顺序:
    ① 赞助引流文案 (先删 Patreon/Ko-fi 等链接行，保留 DeviantArt 链接)
    ② tags (若删完引流依然超限，从后往前逐个移除 hashtags)
    ③ 正文 (正文优先保留，最后才动；末尾附加 '...'，杜绝中文标记)
    返回: (truncated_text, truncated_chars, signature_preserved)
    """
    orig_count = calculate_x_char_count(full_text)
    if orig_count <= limit:
        return full_text, 0, False

    cur_text = full_text

    # ① 裁剪赞助引流文案 (先删 Patreon/Ko-fi 等链接行，保留 DeviantArt 链接)
    sponsorship_domains = [
        "patreon.com", "ko-fi.com", "fanbox.cc", "fantia.jp",
        "subscribestar", "gumroad", "dlsite"
    ]
    lines = cur_text.splitlines()
    filtered_lines = []
    removed_sponsorship = False

    for line in lines:
        line_lower = line.lower()
        if "deviantart.com" in line_lower:
            filtered_lines.append(line)
        elif any(domain in line_lower for domain in sponsorship_domains):
            removed_sponsorship = True
            continue
        else:
            filtered_lines.append(line)

    if removed_sponsorship:
        cleaned_lines = []
        for idx, l in enumerate(filtered_lines):
            # 若分割线后已无任何链接或有效内容，清理孤立分割线
            if l.strip() == "---":
                remaining = "\n".join(filtered_lines[idx+1:]).strip()
                if not remaining or not any(m in remaining.lower() for m in ["http://", "https://", "deviantart.com"]):
                    continue
            cleaned_lines.append(l)
        candidate = "\n".join(cleaned_lines).strip()
        candidate = re.sub(r'\n{3,}', '\n\n', candidate)
        if calculate_x_char_count(candidate) <= limit:
            da_preserved = "deviantart.com" in candidate.lower()
            return candidate, max(0, len(full_text) - len(candidate)), da_preserved
        cur_text = candidate

    # ② 裁剪 tags (若依然超限，从后往前逐个移除 hashtags)
    tag_matches = list(re.finditer(r'#[^\s#]+', cur_text))
    if tag_matches:
        for m in reversed(tag_matches):
            start = m.start()
            end = m.end()
            if start > 0 and cur_text[start - 1] == ' ':
                start -= 1
            cand = cur_text[:start] + cur_text[end:]
            cand = re.sub(r'[ \t]+\n', '\n', cand)
            cand = re.sub(r'\n{3,}', '\n\n', cand).strip()
            cur_text = cand
            if calculate_x_char_count(cur_text) <= limit:
                da_preserved = "deviantart.com" in cur_text.lower()
                return cur_text, max(0, len(full_text) - len(cur_text)), da_preserved

    # ③ 裁剪正文 (正文优先保留，最后才动；末尾附加 '...')
    has_da = "deviantart.com" in cur_text.lower()
    if has_da:
        lines = cur_text.splitlines()
        da_lines = [l for l in lines if "deviantart.com" in l.lower()]
        non_da_lines = [l for l in lines if "deviantart.com" not in l.lower()]
        da_block = "\n".join(da_lines).strip()
        body_part = "\n".join(non_da_lines).strip()

        needed_da_budget = calculate_x_char_count(da_block) + 4
        if limit - needed_da_budget >= 25:
            body_budget = limit - needed_da_budget - calculate_x_char_count("...")
            cut_b = body_part
            while cut_b and calculate_x_char_count(cut_b) > body_budget:
                cut_b = cut_b[:-5]
            while cut_b and calculate_x_char_count(cut_b) > body_budget:
                cut_b = cut_b[:-1]
            final_text = f"{cut_b.rstrip()}...\n\n{da_block}".strip()
            if calculate_x_char_count(final_text) <= limit:
                return final_text, max(0, len(full_text) - len(final_text)), True

    ellipsis = "..."
    budget = limit - calculate_x_char_count(ellipsis)
    cut = cur_text
    while cut and calculate_x_char_count(cut) > budget:
        cut = cut[:-5]
    while cut and calculate_x_char_count(cut) > budget:
        cut = cut[:-1]

    final_text = cut.rstrip() + ellipsis
    return final_text, max(0, len(full_text) - len(final_text)), False

def split_into_x_threads(full_text: str, limit: int = 280) -> list[str]:
    """
    按 X 规则将长推文智能切分为串推列表 (Thread):
    - 绝不断开完整 URL
    - 优先在换行、中文句号/感叹号/分号、英文句号处断开
    - 末尾追加 (1/N) 标识
    """
    orig_count = calculate_x_char_count(full_text)
    if orig_count <= limit:
        return [full_text]

    # 将文本切分为词素，完整保护 URL
    url_pattern = re.compile(r'(https?://\S+)')
    parts = url_pattern.split(full_text)

    tokens = []
    for part in parts:
        if not part:
            continue
        if url_pattern.match(part):
            tokens.append((part, True))
        else:
            # 按换行和标点分割非 URL 文本
            sub_chunks = re.split(r'(\n+|[。！？；]+|[.!?]\s+)', part)
            for sc in sub_chunks:
                if sc:
                    tokens.append((sc, False))

    def pack_tokens(tokens_list, chunk_budget):
        chunks = []
        current = ""
        for t_text, is_url in tokens_list:
            cand = current + t_text
            if not current:
                if calculate_x_char_count(t_text) > chunk_budget and not is_url:
                    piece = t_text
                    while piece:
                        idx = len(piece)
                        while idx > 0 and calculate_x_char_count(piece[:idx]) > chunk_budget:
                            idx -= 1
                        if idx == 0:
                            idx = 1
                        chunks.append(piece[:idx])
                        piece = piece[idx:]
                    current = ""
                else:
                    current = t_text
            elif calculate_x_char_count(cand) <= chunk_budget:
                current = cand
            else:
                chunks.append(current)
                if calculate_x_char_count(t_text) > chunk_budget and not is_url:
                    piece = t_text
                    while piece:
                        idx = len(piece)
                        while idx > 0 and calculate_x_char_count(piece[:idx]) > chunk_budget:
                            idx -= 1
                        if idx == 0:
                            idx = 1
                        chunks.append(piece[:idx])
                        piece = piece[idx:]
                    current = ""
                else:
                    current = t_text
        if current:
            chunks.append(current)
        return chunks

    # 预留 (1/X) 序号空间 (10 字符)
    raw_chunks = pack_tokens(tokens, limit - 10)
    total = len(raw_chunks)
    if total <= 1:
        return [full_text]

    final_chunks = []
    for i, c in enumerate(raw_chunks):
        suffix = f" ({i+1}/{total})"
        full_c = c.rstrip() + suffix
        if calculate_x_char_count(full_c) > limit:
            base = c.rstrip()
            while base and calculate_x_char_count(base + suffix) > limit:
                base = base[:-1]
            full_c = base + suffix
        final_chunks.append(full_c)

    return final_chunks


def format_x_full_text(title: str = "", desc: str = "", tags: list[str] = None) -> str:
    """
    组合 X (Twitter) 推文完整正文:
    - 规范化 tags 列表为标准推特 Hashtags (#tag1 #tag2)
    - 自动滤除正文中已包含的重复标签
    - 优先将 Hashtag 区域置于正文末尾、引流签名 (--- / Patreon / Ko-fi) 之前
    """
    title = (title or "").strip()
    desc = (desc or "").strip()
    tags = tags or []

    hashtags = []
    seen = set()
    desc_lower = desc.lower()

    for t in tags:
        clean = re.sub(r"^[#＃]+", "", str(t).strip())
        # 替换内部空格为下划线，符合推特 Hashtag 语法
        clean = re.sub(r"\s+", "_", clean).strip()
        if not clean:
            continue
        tag_lower = clean.lower()
        if tag_lower in seen:
            continue
        # 如果正文中已明确包含此 Hashtag，则不重复追加
        if f"#{tag_lower}" in desc_lower or f"#{clean}" in desc:
            continue
        seen.add(tag_lower)
        hashtags.append(f"#{clean}")

    tag_line = " ".join(hashtags)

    if not tag_line:
        return f"{title}\n\n{desc}".strip() if title else desc

    if not desc:
        return f"{title}\n\n{tag_line}".strip() if title else tag_line

    # 检查 desc 末尾是否存在签名
    sig_markers = ["patreon.com", "ko-fi.com", "fanbox.cc", "fantia.jp", "subscribestar", "dlsite", "gumroad", "---"]
    tail_sample = desc[-200:].lower() if len(desc) > 200 else desc.lower()
    has_sig = any(m in tail_sample for m in sig_markers)

    sig_start = -1
    if has_sig:
        divider_idx = desc.rfind("\n---")
        if divider_idx != -1 and divider_idx >= len(desc) - 220:
            sig_start = divider_idx
        else:
            lines = desc.splitlines(keepends=True)
            accum = 0
            for line in lines:
                if any(m in line.lower() for m in sig_markers) and accum >= len(desc) - 200:
                    sig_start = accum
                    break
                accum += len(line)

    if sig_start != -1:
        body = desc[:sig_start].rstrip()
        sig = desc[sig_start:].lstrip("\r\n")
        combined_desc = f"{body}\n\n{tag_line}\n\n{sig}".strip() if body else f"{tag_line}\n\n{sig}".strip()
    else:
        combined_desc = f"{desc}\n\n{tag_line}".strip()

    return f"{title}\n\n{combined_desc}".strip() if title else combined_desc
