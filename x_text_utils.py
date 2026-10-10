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
    根据 X (Twitter) 官方规则计算字符数:
    - http(s):// 链接统一折算为 23 字符
    - 其余字符按标准长度计算
    """
    if not text:
        return 0
    url_pattern = re.compile(r'https?://\S+')
    adjusted = url_pattern.sub("x" * 23, text)
    return len(adjusted)

def truncate_x_text(full_text: str, limit: int = 280) -> tuple[str, int, bool]:
    """
    针对 X 平台进行智能截断 (方案 A - 优先保护末尾变现签名):
    返回: (truncated_text, truncated_chars, signature_preserved)
    - truncated_text: 截断后的最终文本 (保证 <= limit)
    - truncated_chars: 被截断/移除的字符数
    - signature_preserved: 是否成功保留末尾签名
    """
    orig_count = calculate_x_char_count(full_text)
    if orig_count <= limit:
        return full_text, 0, False

    sig_markers = ["patreon.com", "ko-fi.com", "fanbox.cc", "fantia.jp", "subscribestar", "dlsite", "gumroad", "---"]
    # 检查后 160 字符内是否有签名特征
    tail_sample = full_text[-160:].lower() if len(full_text) > 160 else full_text.lower()
    has_sig = any(m in tail_sample for m in sig_markers)

    sig_start = -1
    if has_sig:
        # 寻找 \n--- 或签名特征行
        divider_idx = full_text.rfind("\n---")
        if divider_idx != -1 and divider_idx >= len(full_text) - 200:
            sig_start = divider_idx
        else:
            lines = full_text.splitlines(keepends=True)
            accum = 0
            for line in lines:
                if any(m in line.lower() for m in sig_markers) and accum >= len(full_text) - 180:
                    sig_start = accum
                    break
                accum += len(line)

    if sig_start != -1:
        body = full_text[:sig_start].rstrip()
        sig = full_text[sig_start:].lstrip("\r\n")
        ellipsis = "\n...[正文略]...\n"
        budget = limit - calculate_x_char_count(sig) - calculate_x_char_count(ellipsis)
        if budget > 25:
            cut_body = body
            while cut_body and calculate_x_char_count(cut_body) > budget:
                cut_body = cut_body[:-5]
            while cut_body and calculate_x_char_count(cut_body) > budget:
                cut_body = cut_body[:-1]

            truncated = f"{cut_body.rstrip()}{ellipsis}{sig}"
            cut_chars = len(full_text) - len(truncated)
            return truncated, max(0, cut_chars), True

    # 若未识别签名或预算不足以保留签名，执行常规尾部截断
    ellipsis = "..."
    budget = limit - len(ellipsis)
    cut = full_text
    while cut and calculate_x_char_count(cut) > budget:
        cut = cut[:-5]
    while cut and calculate_x_char_count(cut) > budget:
        cut = cut[:-1]

    truncated = cut.rstrip() + ellipsis
    cut_chars = len(full_text) - len(truncated)
    return truncated, max(0, cut_chars), False

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
