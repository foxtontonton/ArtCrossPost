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
from typing import List, Tuple, Optional, Dict, Any, Union


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
    "Where to find me:",
    "You can find me on any of these platforms — same name everywhere:",
    "You can find me on any of these platforms -- same name everywhere:",
    "You can find me on any of these platforms",
    "You can find me on any of these platforms:",
    "Find me on:",
    "Find me at:",
]


def normalize_url(url: str) -> str:
    """
    归一化 URL 用于比对:
    1. 小写协议与域名 (去除 www. 前缀)
    2. 移除尾随斜杠 (根路径除外)
    3. 保留路径大小写与查询参数
    """
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
        scheme = p.scheme.lower()
        netloc = p.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
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

    # 4. 尝试从系统配置读取 signature_template 匹配 (外部未显式传递 sig_template 时的自动透传)
    if not sig_template:
        try:
            from config import load_config
            cfg = load_config()
            cfg_sig = cfg.get("signature_template")
            if cfg_sig:
                cfg_st = cfg_sig.strip()
                if cfg_st and cfg_st in desc:
                    idx = desc.rfind(cfg_st)
                    return desc[:idx].rstrip(), desc[idx:].strip()
                cfg_first_line = next((line.strip() for line in cfg_sig.splitlines() if line.strip()), None)
                if cfg_first_line and cfg_first_line in desc:
                    idx = desc.rfind(cfg_first_line)
                    return desc[:idx].rstrip(), desc[idx:].strip()
        except Exception:
            pass

    # 5. 兜底防御：若未匹配到签名头部标记与模板首行，但在文本中检测到规范引流链接或已知平台链接的 Markdown 链接，切分签名段
    for m in MARKDOWN_LINK_REGEX.finditer(desc):
        url_lower = m.group(2).lower()
        if (
            any(target_url in m.group(2) or _url_path_identity(target_url)[1] == _url_path_identity(m.group(2))[1] for _, target_url in CANONICAL_SIGNATURE_LINKS)
            or "patreon.com" in url_lower
            or "deviantart.com" in url_lower
            or "civitai.red" in url_lower
            or "ko-fi.com" in url_lower
            or "bsky.app" in url_lower
            or "x.com" in url_lower
        ):
            idx = m.start()
            line_start = desc.rfind("\n\n", 0, idx)
            if line_start != -1:
                split_pos = line_start + 2
            else:
                l_start = desc.rfind("\n", 0, idx)
                split_pos = 0 if l_start == -1 else l_start + 1
            return desc[:split_pos].rstrip(), desc[split_pos:].strip()

    # 6. 未找到签名标记，全量视为正文
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


def _url_path_identity(url: str) -> Tuple[str, str]:
    """返回 (规范化域名去掉www, 规范化小写路径去掉尾随斜杠)"""
    if not url:
        return ("", "")
    try:
        p = urlparse(url.strip())
        netloc = p.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = p.path.rstrip("/").lower()
        return (netloc, path)
    except Exception:
        return ("", "")


def extract_html_hrefs(html_content: str) -> List[str]:
    """提取 HTML 中所有的 <a href="..."> 属性值列表"""
    if not html_content:
        return []
    return re.findall(r'<a\s+[^>]*?href=["\']([^"\']+)["\']', html_content, re.IGNORECASE)


def verify_signature_links_in_html(
    html_content: str,
    expected_links: Optional[List[Tuple[str, str]]] = None,
    da_post_url: Optional[str] = None,
    sig_template: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """
    回读富文本编辑器 HTML，严格断言签名中的真实超链接 <a href="目标URL"> 全部存在。
    缺任一返回 (False, [未生效链接清单])。
    禁止纯文本填了就算成功。
    支持按路径比对与 DA 链接查询参数剥除。

    :param html_content: 编辑器回读的 innerHTML
    :param expected_links: 期望的 (平台名, URL) 清单，默认从 sig_template 提取或 CANONICAL_SIGNATURE_LINKS
    :param da_post_url: 当 DA 透传帖子链接存在时，DeviantArt 链接允许匹配当帖 URL (自动剥除查询串并按路径比对)
    :param sig_template: 签名模板字符串 (若未传 expected_links，优先从模板提取 Markdown 链接断言)
    :return: (is_all_present, missing_links_info)
    """
    if not html_content:
        return False, ["编辑器 HTML 内容为空"]

    if expected_links is not None:
        targets = list(expected_links)
    elif sig_template:
        extracted = extract_markdown_links(sig_template)
        if extracted:
            targets = [(name, url) for name, url in extracted]
        else:
            targets = list(CANONICAL_SIGNATURE_LINKS)
    else:
        targets = list(CANONICAL_SIGNATURE_LINKS)

    raw_hrefs = extract_html_hrefs(html_content)
    normalized_dom_urls = [normalize_url(h) for h in raw_hrefs]
    dom_identities = [_url_path_identity(h) for h in raw_hrefs]

    clean_da_post = str(da_post_url).split("?")[0].split("#")[0].rstrip("/") if da_post_url else None
    da_netloc, da_path = _url_path_identity(clean_da_post) if clean_da_post else ("", "")

    missing = []

    for name, expected_url in targets:
        clean_exp = expected_url.split("?")[0].split("#")[0].rstrip("/")
        norm_expected = normalize_url(expected_url)
        exp_netloc, exp_path = _url_path_identity(clean_exp)

        # 1. 尝试完全归一化 URL 精确匹配
        matched = norm_expected in normalized_dom_urls

        # 2. 路径比对匹配 (忽略末尾斜杠、查询参数、大小写以及 www. 前缀)
        if not matched and exp_path:
            for d_net, d_path in dom_identities:
                if d_path == exp_path and (not d_net or exp_netloc in d_net or d_net in exp_netloc):
                    matched = True
                    break

        # 3. 若是 DeviantArt 平台且存在 da_post_url 透传，允许匹配当帖链接 (支持路径比对与剥查询串)
        if not matched and (name.lower() in ("deviantart", "da") or "deviantart.com" in expected_url.lower()) and clean_da_post:
            norm_da_post = normalize_url(clean_da_post)
            if norm_da_post in normalized_dom_urls:
                matched = True
            elif da_path:
                for d_net, d_path in dom_identities:
                    if d_path == da_path and (not d_net or "deviantart.com" in d_net):
                        matched = True
                        break

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


def count_expected_paragraphs(desc: str, sig_template: Optional[str] = None) -> int:
    """
    计算描述文本期望的富文本段落 (<p>) 数量:
    按 \\n\\s*\\n 切分后的非空段数（正文+签名整体计算，与 render_description_to_html 的切分口径一致）。
    """
    if not desc or not str(desc).strip():
        return 0
    body_text, sig_text = split_body_and_signature(str(desc), sig_template)
    count = 0
    if body_text:
        count += sum(1 for p in re.split(r"\n\s*\n", body_text) if p.strip())
    if sig_text:
        count += sum(1 for p in re.split(r"\n\s*\n", sig_text) if p.strip())
    return count


def count_html_paragraphs(html_content: str) -> int:
    """统计回读 HTML 中的 <p> 段落标签总数"""
    if not html_content or not isinstance(html_content, str):
        return 0
    return len(re.findall(r"<p(?:\s[^>]*)?>", html_content, flags=re.IGNORECASE))


def verify_paragraphs_in_html(
    html_content: str,
    desc_or_expected: Union[str, int],
    sig_template: Optional[str] = None,
) -> Tuple[bool, int, int]:
    """
    段落回读断言:
    - 期望段落数 = 描述文本按 \\n\\s*\\n 切分后的非空段数（正文+签名整体计算，与 render 的切分口径一致）；
    - 回读编辑器 innerHTML，统计 <p> 数须 >= 期望段落数（编辑器尾随空 <p> 只会多不会少，故用 >=）。
    :return: (is_ok, actual_p_count, expected_p_count)
    """
    if isinstance(desc_or_expected, int):
        expected_p = max(0, int(desc_or_expected))
    else:
        expected_p = count_expected_paragraphs(str(desc_or_expected or ""), sig_template=sig_template)

    actual_p = count_html_paragraphs(html_content)
    if expected_p <= 0:
        return True, actual_p, 0
    return (actual_p >= expected_p, actual_p, expected_p)


def is_textarea_element(el: Any) -> bool:
    """
    textarea 守卫:
    判断目标元素是否为纯文本 <textarea>（而非 contenteditable 富文本编辑器）。
    若定位到的是 <textarea>，只走纯文本 fill，跳过 HTML 步与段落断言。
    """
    if el is None:
        return False
    tag_attr = getattr(el, "tag_name", None)
    if isinstance(tag_attr, str) and tag_attr.strip().upper() == "TEXTAREA":
        return True
    if callable(tag_attr) and not hasattr(tag_attr, "assert_called"):
        try:
            t_res = tag_attr()
            if isinstance(t_res, str) and t_res.strip().upper() == "TEXTAREA":
                return True
        except Exception:
            pass
    try:
        val = el.evaluate("el => (el && el.tagName ? String(el.tagName).toUpperCase() : '')")
        if isinstance(val, str) and val.strip().upper() == "TEXTAREA":
            return True
    except Exception:
        pass
    return False


