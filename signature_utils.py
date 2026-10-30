# -*- coding: utf-8 -*-
"""
ArtCrossPost - Signature & Markdown Link Utilities
严格覆盖需求: 全平台统一程序签名，带真超链接 (feature/signature-with-links)

1. 规范签名模版与真实链接:
   - 🎬X: [@FOXTONTONTON](https://x.com/FOXTONTONTON)
   - 🖼️DeviantArt: [FOXTONTONTON](https://www.deviantart.com/foxtontonton)
   - 💜Patreon：[FOXTONTONTON](https://patreon.com/foxtontonton)
   - ☕Ko-fi: [FOXTONTONTON](https://ko-fi.com/foxtontonton)
   - 💎Civitai: [FOXTONTONTON](https://civitai.red/user/FOXTONTONTON)
   末句定版: "...support my work, you can buy me a coffee on Ko-fi..."
2. 富文本平台 (DA / Patreon / Civitai) 渲染为真链接:
   正文纯文本填写，<、>、& 严格转义；正文与签名纯文本段中的方括号不得被误解析为链接；
   链接解析只认签名模板来源。
3. 提交前回读断言:
   回读编辑器 HTML，断言 6 个签名链接的 <a href="..."> 全部存在；缺任一报错阻断发布。
4. 纯文本回退:
   纯文本平台或回退上下文提取显示文字: [Display](URL) -> Display。
"""
import re
import html
from urllib.parse import urlparse
from typing import List, Tuple, Optional, Dict, Any


CANONICAL_SIGNATURE_TEMPLATE = (
    "Where to find me — same name everywhere:\n\n"
    "🎬X: [@FOXTONTONTON](https://x.com/FOXTONTONTON)\n\n"
    "🖼️DeviantArt: [FOXTONTONTON](https://www.deviantart.com/foxtontonton)\n\n"
    "💜Patreon：[FOXTONTONTON](https://patreon.com/foxtontonton)\n\n"
    "☕Ko-fi: [FOXTONTONTON](https://ko-fi.com/foxtontonton)\n\n"
    "💎Civitai: [FOXTONTONTON](https://civitai.red/user/FOXTONTONTON)\n\n"
    "🦋Bluesky: [FOXTONTONTON](https://bsky.app/profile/foxtontonton.bsky.social)\n\n"
    "You can find me on any of these platforms. Follow wherever you already hang out, "
    "and you won't miss the next chapter, illustration, or animation.\n\n"
    "If you like what you see, follow, like, and watch me — and if you'd like to support my work, "
    "you can buy me a coffee on Ko-fi. Thank you so much!"
)

# 6 个规范引流链接 (平台标识, 目标 URL)
CANONICAL_SIGNATURE_LINKS: List[Tuple[str, str]] = [
    ("X", "https://x.com/FOXTONTONTON"),
    ("DeviantArt", "https://www.deviantart.com/foxtontonton"),
    ("Patreon", "https://patreon.com/foxtontonton"),
    ("Ko-fi", "https://ko-fi.com/foxtontonton"),
    ("Civitai", "https://civitai.red/user/FOXTONTONTON"),
    ("Bluesky", "https://bsky.app/profile/foxtontonton.bsky.social"),
]

# Markdown 链接匹配正则: [显示文本](URL)
# URL 限定为 http:// 或 https:// 协议，排除未闭合或含空格的伪链接，且显示文本内不包含中括号
MARKDOWN_LINK_REGEX = re.compile(r"\[([^\[\]]+)\]\((https?://[^\s\)]+)\)")

# 签名起始标记
SIGNATURE_HEADER_MARKERS = [
    "Where to find me — same name everywhere:",
    "Where to find me -- same name everywhere:",
    "Where to find me",
]


def normalize_url(url: str) -> str:
    """
    归一化 URL 用于比对:
    1. 小写协议与域名
    2. 移除尾随斜杠 (根路径除外)
    3. 保留路径大小写与查询参数
    """
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
        scheme = p.scheme.lower()
        netloc = p.netloc.lower()
        path = p.path.rstrip("/") if p.path != "/" else "/"
        query = f"?{p.query}" if p.query else ""
        return f"{scheme}://{netloc}{path}{query}"
    except Exception:
        return url.strip().rstrip("/").lower()


def extract_markdown_links(text: str) -> List[Tuple[str, str]]:
    """提取文本中的所有 Markdown 链接 [显示文本](URL)"""
    if not text:
        return []
    return [(m.group(1), m.group(2)) for m in MARKDOWN_LINK_REGEX.finditer(text)]


def split_body_and_signature(desc: str, sig_template: Optional[str] = None) -> Tuple[str, str]:
    """
    将描述文本严格拆分为 (正文纯文本段, 签名段):
    正文中的方括号 (如 [OC], [1/3], [Artist]) 绝对不得被作为链接解析。
    仅签名段才解析 Markdown 链接。
    """
    if not desc:
        return "", ""

    # 1. 尝试以传入或配置的 sig_template 精确匹配
    if sig_template:
        st = sig_template.strip()
        if st and st in desc:
            idx = desc.rfind(st)
            return desc[:idx].rstrip(), desc[idx:].strip()

    # 2. 尝试以规范签名起始标头匹配
    for marker in SIGNATURE_HEADER_MARKERS:
        if marker in desc:
            idx = desc.rfind(marker)
            return desc[:idx].rstrip(), desc[idx:].strip()

    # 3. 尝试如果 sig_template 第一行非空在 desc 中
    if sig_template:
        first_line = next((line.strip() for line in sig_template.splitlines() if line.strip()), None)
        if first_line and first_line in desc:
            idx = desc.rfind(first_line)
            return desc[:idx].rstrip(), desc[idx:].strip()

    # 4. 未找到签名标记，全量视为正文
    return desc.strip(), ""


def has_signature(desc: str, sig_template: Optional[str] = None) -> bool:
    """判断描述文本中是否包含签名段"""
    if not desc:
        return False
    _, sig = split_body_and_signature(desc, sig_template)
    if sig:
        return True
    # 额外检查是否有任一规范链接
    for _, target_url in CANONICAL_SIGNATURE_LINKS:
        if target_url in desc:
            return True
    return False


def render_markdown_signature_to_html(sig_text: str) -> str:
    """
    将签名段的 Markdown 式 [显示文字](URL) 转为真超链接 HTML。
    - 其余纯文本严格经过 html.escape (<, >, & 转义)
    - 纯文本段中的孤立方括号或未闭合中括号保持字面义，绝不误解析
    - <a> 标签带 target="_blank" rel="noopener noreferrer nofollow"
    """
    if not sig_text:
        return ""

    # 使用正则逐步替换签名中的 markdown 链接，非链接部分进行 html.escape
    parts = []
    last_idx = 0
    for match in MARKDOWN_LINK_REGEX.finditer(sig_text):
        start, end = match.span()
        # 链接前的纯文本段进行 HTML 转义
        plain_segment = sig_text[last_idx:start]
        parts.append(html.escape(plain_segment))

        # 链接转为 <a>
        display_text = html.escape(match.group(1))
        link_url = html.escape(match.group(2), quote=True)
        parts.append(f'<a href="{link_url}" target="_blank" rel="noopener noreferrer nofollow">{display_text}</a>')
        last_idx = end

    # 剩余末尾纯文本段
    remaining = sig_text[last_idx:]
    parts.append(html.escape(remaining))

    return "".join(parts)


def render_description_to_html(desc: str, sig_template: Optional[str] = None) -> str:
    """
    将作品描述渲染为富文本编辑器 (Tiptap / ProseMirror / Remirror) 所需的 HTML:
    1. 正文段纯文本填写，<、>、& 严格转义，方括号不被误解析为链接
    2. 签名段中的 [显示文字](URL) 解析为真超链接 <a href="...">
    3. 段落按双换行切分为 <p>...</p> 块，单换行转为 <br>
    """
    if not desc:
        return "<p></p>"

    body_text, sig_text = split_body_and_signature(desc, sig_template)

    html_blocks: List[str] = []

    # 1. 渲染正文段 (纯文本，方括号原样保留)
    if body_text:
        # 按双换行分段
        body_paras = re.split(r"\n\s*\n", body_text)
        for para in body_paras:
            para = para.strip()
            if para:
                # 转义 HTML 实体，单换行转 <br>
                escaped = html.escape(para)
                escaped = escaped.replace("\r", "").replace("\n", "<br>")
                html_blocks.append(f"<p>{escaped}</p>")

    # 2. 渲染签名段 (带真超链接)
    if sig_text:
        sig_paras = re.split(r"\n\s*\n", sig_text)
        for para in sig_paras:
            para = para.strip()
            if para:
                rendered_sig = render_markdown_signature_to_html(para)
                rendered_sig = rendered_sig.replace("\r", "").replace("\n", "<br>")
                html_blocks.append(f"<p>{rendered_sig}</p>")

    if not html_blocks:
        return "<p></p>"

    return "".join(html_blocks)


def render_description_to_plain_text(desc: str) -> str:
    """
    将描述转换为纯文本 (针对 X/Twitter 等纯文本平台或回退上下文):
    去除 Markdown 链接语法，仅保留显示文字: [Display](URL) -> Display。
    """
    if not desc:
        return ""
    return MARKDOWN_LINK_REGEX.sub(r"\1", desc)


def verify_signature_links_in_html(
    html_content: str,
    expected_links: Optional[List[Tuple[str, str]]] = None,
    da_post_url: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """
    回读富文本编辑器 HTML，严格断言签名中的 6 个真实超链接 <a href="目标URL"> 全部存在。
    缺任一返回 (False, [未生效链接清单])。
    禁止纯文本填了就算成功。

    :param html_content: 编辑器回读的 innerHTML
    :param expected_links: 期望的 (平台名, URL) 清单，默认 CANONICAL_SIGNATURE_LINKS
    :param da_post_url: 当 DA 透传帖子链接存在时，DeviantArt 链接允许匹配当帖 URL
    :return: (is_all_present, missing_links_info)
    """
    if not html_content:
        return False, ["编辑器 HTML 内容为空"]

    targets = expected_links if expected_links is not None else list(CANONICAL_SIGNATURE_LINKS)

    # 提取所有 <a ... href="..." ...> 中的 href 属性
    # 支持单双引号及转义
    raw_hrefs = re.findall(r'<a\s+[^>]*?href=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
    normalized_dom_urls = [normalize_url(h) for h in raw_hrefs]

    missing = []

    for name, expected_url in targets:
        norm_expected = normalize_url(expected_url)

        matched = norm_expected in normalized_dom_urls

        # 若是 DeviantArt 平台且存在 da_post_url 透传，允许匹配当帖链接
        if not matched and name.lower() in ("deviantart", "da") and da_post_url:
            norm_da_post = normalize_url(da_post_url)
            if norm_da_post in normalized_dom_urls:
                matched = True

        if not matched:
            missing.append(f"{name} ({expected_url})")

    if missing:
        return False, missing
    return True, []


def convert_signature_links_for_bluesky(sig_text: str) -> str:
    """
    将签名段中的 [显示文字](URL) 转换为纯文本形式，保证 URL 以裸文本出现：
    [Display](URL) -> Display URL (若 Display 为空或与 URL 相同则仅输出 URL)
    帖子中不得出现 [ ]( ) 标记残留。
    """
    if not sig_text:
        return ""

    def _replace_link(m: re.Match) -> str:
        disp = m.group(1).strip()
        url = m.group(2).strip()
        if not disp or disp == url:
            return url
        return f"{disp} {url}"

    return MARKDOWN_LINK_REGEX.sub(_replace_link, sig_text)


def render_description_for_bluesky(desc: str, sig_template: Optional[str] = None) -> str:
    """
    为 Bluesky 组装描述文本：
    若含签名段，将签名段中的 [显示文字](URL) 转换为纯文本形式并保证 URL 以裸文本出现；
    帖子中签名部分不得出现 [ ]( ) 标记残留；正文部分的普通文本与方括号严格不受影响。
    """
    if not desc:
        return ""

    body_text, sig_text = split_body_and_signature(desc, sig_template)

    if not sig_text:
        # 兜底防御：若未匹配到签名头部标记，但检测到包含规范引流链接的 Markdown 链接，切分签名段
        for m in MARKDOWN_LINK_REGEX.finditer(desc):
            if any(target_url in m.group(2) for _, target_url in CANONICAL_SIGNATURE_LINKS):
                idx = m.start()
                line_start = desc.rfind("\n", 0, idx)
                split_pos = 0 if line_start == -1 else line_start + 1
                body_text = desc[:split_pos].rstrip()
                sig_text = desc[split_pos:].strip()
                break

    if not sig_text:
        return desc

    converted_sig = convert_signature_links_for_bluesky(sig_text)
    if body_text:
        return f"{body_text}\n\n{converted_sig}".strip()
    return converted_sig

