# -*- coding: utf-8 -*-
"""
ArtCrossPost - X (Twitter) Text & Thread Processing Utilities
2026-10-23 方案 B: 统一预告 + 引流格式
格式:
[正文预告]

🔞 NSFW full set on DeviantArt 👇 [DA链接]

[标签行]

优先级:
- 必保: NSFW 提示句 + DA 外链 (URL 按加权 23 字计)
- 次保: 标签，在剩余预算内尽量全保
- 可截: 正文只留开头预告，必须在句子边界截断，不许截半句
"""
import re

DEFAULT_X_DA_LINK = "https://www.deviantart.com/foxtontonton"
DEFAULT_X_NSFW_NOTICE = "🔞 NSFW full set on DeviantArt 👇"


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


def truncate_at_sentence_boundary(text: str, budget: int) -> str:
    """
    在句子边界进行严格截断，绝不截断半句话。
    句子终结符判定:
    - 中文标点: 。、！、？、…
    - 英文标点: .、!、? 后紧跟空白符、换行符或文本末尾
    - 换行符: \n+
    若整段文本都在预算内，返回全文；若最长可在预算内的前缀中找不到任何完整句子终结符，返回空字符串。
    """
    if not text or budget <= 0:
        return ""
    if calculate_x_char_count(text) <= budget:
        return text.strip()

    # 二分查找权重在 budget 内的最长字符切片
    low, high = 0, len(text)
    best_len = 0
    while low <= high:
        mid = (low + high) // 2
        cand = text[:mid]
        if calculate_x_char_count(cand) <= budget:
            best_len = mid
            low = mid + 1
        else:
            high = mid - 1

    prefix = text[:best_len]
    if not prefix:
        return ""

    # 在 prefix 内向前检索最后一个句子终结符
    pattern = r'[。！？\u2026]|\n+|[.!?]+(?=[\s\n]|$)'
    matches = list(re.finditer(pattern, prefix))
    if matches:
        last_match = matches[-1]
        cut_point = last_match.end()
        return prefix[:cut_point].strip()

    return ""


def format_x_teaser_post(
    title: str = "",
    desc: str = "",
    tags: list[str] = None,
    da_link: str = None,
    nsfw_notice: str = None,
    limit: int = 280
) -> str:
    """
    X (Twitter) 方案 B 预告+引流统一排版引擎:
    1. 格式:
       [正文预告]

       🔞 NSFW full set on DeviantArt 👇 [DA链接]

       [标签行]
    2. 优先级:
       - 必保: NSFW 提示句 + DA 外链 (URL 按加权 23 字计)
       - 次保: 标签，在剩余预算内从尾部逐个截取尽量全保
       - 可截: 正文只留开头预告，必须在句子边界截断，不许截半句
    3. 预算:
       总加权 <= 280
       预告可用长度 = 280 - 提示句 - 23 - 标签 - 格式余量
       正文短于可用长度 -> 全文保留
       可用长度 <= 0 -> 先砍标签尾部，仍不够 -> 只发提示句 + 链接
    """
    title = (title or "").strip()
    desc = (desc or "").strip()
    tags = tags or []

    # 规范化外部传入或默认的 DA 引流参数
    if da_link is None:
        try:
            from config import load_config
            cfg = load_config()
            da_link = cfg.get("defaults", {}).get("x_da_link", DEFAULT_X_DA_LINK)
        except Exception:
            da_link = DEFAULT_X_DA_LINK

    if nsfw_notice is None:
        try:
            from config import load_config
            cfg = load_config()
            nsfw_notice = cfg.get("defaults", {}).get("x_nsfw_notice", DEFAULT_X_NSFW_NOTICE)
        except Exception:
            nsfw_notice = DEFAULT_X_NSFW_NOTICE

    da_link = (da_link or "").strip()
    nsfw_notice = (nsfw_notice or "").strip()

    if nsfw_notice and da_link:
        notice_line = f"{nsfw_notice} {da_link}".strip()
    else:
        notice_line = (nsfw_notice or da_link or "").strip()

    # 清理正文并剔除旧的赞助签名 (如 Patreon/Ko-fi 引流模板)
    raw_body = f"{title}\n\n{desc}".strip() if title and desc else (title or desc)
    sig_markers = ["---", "patreon.com", "ko-fi.com", "fanbox.cc", "fantia.jp", "subscribestar", "dlsite", "gumroad"]
    cleaned_body_lines = []
    for line in raw_body.splitlines():
        if any(marker in line.lower() for marker in sig_markers):
            break
        cleaned_body_lines.append(line)
    body = "\n".join(cleaned_body_lines).strip()

    # 规范化标签为标准推特 Hashtags (#tag)
    hashtags = []
    seen = set()
    for t in tags:
        clean = re.sub(r"^[#＃]+", "", str(t).strip())
        clean = re.sub(r"\s+", "_", clean).strip()
        if not clean:
            continue
        tag_lower = clean.lower()
        if tag_lower in seen:
            continue
        if f"#{tag_lower}" in body.lower() or f"#{clean}" in body:
            continue
        seen.add(tag_lower)
        hashtags.append(f"#{clean}")

    # 步骤 1: 预算必保提示句 + 尽量全保标签
    current_tags = list(hashtags)
    while current_tags:
        tag_str = " ".join(current_tags)
        cand_base = f"{notice_line}\n\n{tag_str}" if notice_line else tag_str
        if calculate_x_char_count(cand_base) <= limit:
            break
        current_tags.pop()

    tag_str = " ".join(current_tags)
    base_without_body = f"{notice_line}\n\n{tag_str}" if (notice_line and tag_str) else (notice_line or tag_str)

    if not body:
        return base_without_body

    # 步骤 2: 检查正文短文是否可全文保留
    full_cand = f"{body}\n\n{base_without_body}" if base_without_body else body
    if calculate_x_char_count(full_cand) <= limit:
        return full_cand

    # 步骤 3: 正文超限，在可用预算内按句子边界截断
    sep_overhead = 2 if base_without_body else 0
    body_budget = limit - calculate_x_char_count(base_without_body) - sep_overhead
    teaser = truncate_at_sentence_boundary(body, body_budget)

    # 步骤 4: 可用长度 <= 0 或连第一句也放不下时，先砍标签尾部腾出预算
    while not teaser and current_tags:
        current_tags.pop()
        tag_str = " ".join(current_tags)
        base_without_body = f"{notice_line}\n\n{tag_str}" if (notice_line and tag_str) else (notice_line or tag_str)
        sep_overhead = 2 if base_without_body else 0
        body_budget = limit - calculate_x_char_count(base_without_body) - sep_overhead
        if body_budget > 0:
            teaser = truncate_at_sentence_boundary(body, body_budget)

    # 步骤 5: 组合最终结果，仍不够则只发提示句 + 链接
    if teaser:
        return f"{teaser}\n\n{base_without_body}" if base_without_body else teaser
    else:
        return base_without_body


def format_x_full_text(
    title: str = "",
    desc: str = "",
    tags: list[str] = None,
    da_link: str = None,
    nsfw_notice: str = None,
    limit: int = 280
) -> str:
    """
    组合 X (Twitter) 推文完整正文 (自动应用预告+引流策略方案 B)
    """
    return format_x_teaser_post(
        title=title,
        desc=desc,
        tags=tags,
        da_link=da_link,
        nsfw_notice=nsfw_notice,
        limit=limit
    )


def truncate_x_text(
    full_text: str,
    limit: int = 280,
    da_link: str = None,
    nsfw_notice: str = None
) -> tuple[str, int, bool]:
    """
    针对 X 平台进行智能截断 (执行方案 B 预告+引流策略，废弃旧版裁引流保正文逻辑)
    返回: (truncated_text, truncated_chars, da_link_preserved)
    """
    orig_count = calculate_x_char_count(full_text)
    if orig_count <= limit:
        da_ok = (da_link or DEFAULT_X_DA_LINK).lower() in full_text.lower()
        return full_text, 0, da_ok

    # 提取现有文本中的 Hashtags 与正文内容
    tag_matches = list(re.finditer(r'#[^\s#]+', full_text))
    extracted_tags = [m.group(0) for m in tag_matches]
    text_without_tags = re.sub(r'#[^\s#]+', '', full_text).strip()

    result = format_x_teaser_post(
        title="",
        desc=text_without_tags,
        tags=extracted_tags,
        da_link=da_link,
        nsfw_notice=nsfw_notice,
        limit=limit
    )

    cut_chars = max(0, len(full_text) - len(result))
    da_ok = (da_link or DEFAULT_X_DA_LINK).lower() in result.lower()
    return result, cut_chars, da_ok


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
