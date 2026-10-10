# -*- coding: utf-8 -*-
"""
Tests for X (Twitter) Text & Thread Processing Utilities (x_text_utils.py)
Covering:
- calculate_x_char_count
- truncate_x_text
- split_into_x_threads
- format_x_full_text
"""
from x_text_utils import (
    calculate_x_char_count,
    truncate_x_text,
    split_into_x_threads,
    format_x_full_text
)


class TestCalculateXCharCount:
    """测试 X 平台字符数计算逻辑"""

    def test_empty_text(self):
        assert calculate_x_char_count("") == 0
        assert calculate_x_char_count(None) == 0

    def test_plain_english_text(self):
        text = "Hello world!"
        assert calculate_x_char_count(text) == len(text)

    def test_chinese_text(self):
        text = "你好世界，艺术创作同步发布助手"
        assert calculate_x_char_count(text) == len(text)

    def test_single_short_and_long_url(self):
        # 无论 URL 实际长度如何，X 均折算为 23 字符
        short_url = "https://t.co/xyz"
        long_url = "https://www.example.com/gallery/artworks/2026/detail?id=987654321&ref=twitter_share"
        assert calculate_x_char_count(short_url) == 23
        assert calculate_x_char_count(long_url) == 23

    def test_mixed_text_with_url(self):
        text = "Check out my new art: https://patreon.com/myname Thanks!"
        # "Check out my new art: " (22) + URL (23) + " Thanks!" (8) = 53
        expected = len("Check out my new art: ") + 23 + len(" Thanks!")
        assert calculate_x_char_count(text) == expected

    def test_multiple_urls(self):
        text = "Patreon: https://patreon.com/a | Ko-fi: https://ko-fi.com/b"
        # "Patreon: " (9) + 23 + " | Ko-fi: " (11) + 23 = 66
        expected = len("Patreon: ") + 23 + len(" | Ko-fi: ") + 23
        assert calculate_x_char_count(text) == expected

    def test_newlines_and_emojis(self):
        text = "🎨 Title\n\nLine 1\nLine 2"
        assert calculate_x_char_count(text) == len(text)


class TestTruncateXText:
    """测试智能截断逻辑 (含签名保护)"""

    def test_within_limit_not_truncated(self):
        text = "A short post under limit."
        trunc, cut_chars, sig_ok = truncate_x_text(text, limit=280)
        assert trunc == text
        assert cut_chars == 0
        assert sig_ok is False

    def test_exact_limit_boundary(self):
        text = "a" * 280
        trunc, cut_chars, sig_ok = truncate_x_text(text, limit=280)
        assert trunc == text
        assert cut_chars == 0
        assert sig_ok is False

    def test_no_signature_tail_truncation(self):
        text = "Normal post without signature. " * 15  # ~465 chars
        limit = 280
        trunc, cut_chars, sig_ok = truncate_x_text(text, limit=limit)
        assert calculate_x_char_count(trunc) <= limit
        assert trunc.endswith("...")
        assert cut_chars > 0
        assert sig_ok is False

    def test_signature_preserved_patreon(self):
        body = "This is a detailed artwork description. " * 10
        sig = "\n---\nSupport me on Patreon: https://patreon.com/artist"
        full_text = body + sig
        limit = 280
        assert calculate_x_char_count(full_text) > limit

        trunc, cut_chars, sig_ok = truncate_x_text(full_text, limit=limit)
        assert calculate_x_char_count(trunc) <= limit
        assert sig_ok is True
        assert cut_chars > 0
        # 验证正文中间插入了截断省略符，而尾部签名完好无损
        assert "...[正文略]..." in trunc
        assert "Support me on Patreon: https://patreon.com/artist" in trunc

    def test_signature_preserved_kofi(self):
        body = "Long descriptive text for social media post. " * 8
        sig = "\nSupport: https://ko-fi.com/creator"
        full_text = body + sig
        limit = 200

        trunc, cut_chars, sig_ok = truncate_x_text(full_text, limit=limit)
        assert calculate_x_char_count(trunc) <= limit
        assert sig_ok is True
        assert "ko-fi.com/creator" in trunc

    def test_excessive_signature_fallback(self):
        # 如果签名自身已几乎填满全部预算 (budget <= 25)，应安全回退至尾部截断
        long_sig = "\n---\n" + ("Patreon link: https://patreon.com/test " * 10)
        body = "Short body. "
        full_text = body + long_sig
        limit = 100

        trunc, cut_chars, sig_ok = truncate_x_text(full_text, limit=limit)
        assert calculate_x_char_count(trunc) <= limit
        assert trunc.endswith("...")
        assert sig_ok is False


class TestSplitIntoXThreads:
    """测试 Thread 串推拆分逻辑"""

    def test_within_limit_single_thread(self):
        text = "This is a short post that fits into one tweet."
        chunks = split_into_x_threads(text, limit=280)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_split_multiple_chunks_with_numbering(self):
        # 构造一段长文本
        sentences = [f"This is sentence #{i} of the thread." for i in range(20)]
        text = " ".join(sentences)
        limit = 140

        chunks = split_into_x_threads(text, limit=limit)
        assert len(chunks) > 1

        total = len(chunks)
        for i, c in enumerate(chunks):
            # 每条必须以 (i+1/total) 结尾
            suffix = f"({i+1}/{total})"
            assert suffix in c
            # 每条字符数必须严格不超过 limit
            assert calculate_x_char_count(c) <= limit

    def test_split_never_breaks_url(self):
        long_url = "https://www.example.com/very/long/destination/url/that/should/not/be/chopped/into/pieces"
        text = ("Introductory text line. " * 8) + "\n" + long_url + "\n" + ("Concluding text line. " * 8)
        limit = 200

        chunks = split_into_x_threads(text, limit=limit)
        assert len(chunks) > 1

        # 验证 long_url 完整存在于某一 chunk 中，没有被切成两半
        url_found = any(long_url in c for c in chunks)
        assert url_found is True
        for c in chunks:
            assert calculate_x_char_count(c) <= limit

    def test_split_prefers_newlines_and_punctuation(self):
        p1 = "First paragraph ends here。\n"
        p2 = "Second paragraph starts now and has more content。\n"
        p3 = "Third paragraph is the conclusion。"
        text = p1 * 3 + p2 * 3 + p3
        limit = 150

        chunks = split_into_x_threads(text, limit=limit)
        assert len(chunks) > 1
        for c in chunks:
            assert calculate_x_char_count(c) <= limit


class TestFormatXFullText:
    """测试 X 推文正文组合与 Hashtag 智能排版"""

    def test_title_and_desc_without_tags(self):
        result = format_x_full_text(title="My Art", desc="Cool drawing", tags=[])
        assert result == "My Art\n\nCool drawing"

    def test_tags_converted_to_hashtags(self):
        tags = ["scifi", "digital art", "original"]
        result = format_x_full_text(title="Warrior", desc="New oc", tags=tags)
        assert "#scifi" in result
        assert "#digital_art" in result
        assert "#original" in result

    def test_tags_placed_before_signature(self):
        title = "Title"
        desc = "Artwork description.\n\n---\nSupport: https://patreon.com/artist"
        tags = ["art", "anime"]
        result = format_x_full_text(title=title, desc=desc, tags=tags)
        # 标签必须在签名行之前
        assert result.index("#art #anime") < result.index("---")
        assert result.endswith("https://patreon.com/artist")

    def test_tags_deduplication_against_desc(self):
        desc = "Drawing of #anime character"
        tags = ["anime", "illustration"]
        result = format_x_full_text(title="", desc=desc, tags=tags)
        # 不重复增加 #anime，但必须追加 #illustration
        assert result.count("#anime") == 1
        assert "#illustration" in result

    def test_empty_title_and_desc_only_tags(self):
        tags = ["art", "concept"]
        result = format_x_full_text(title="", desc="", tags=tags)
        assert result == "#art #concept"
