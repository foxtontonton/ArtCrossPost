# -*- coding: utf-8 -*-
"""
Tests for X (Twitter) Text & Thread Processing Utilities (x_text_utils.py)
Covering 2026-10-23 Plan B:
- calculate_x_char_count
- truncate_at_sentence_boundary
- format_x_teaser_post
- format_x_full_text
- truncate_x_text
- split_into_x_threads
"""
from x_text_utils import (
    calculate_x_char_count,
    truncate_at_sentence_boundary,
    format_x_teaser_post,
    format_x_full_text,
    truncate_x_text,
    split_into_x_threads,
    DEFAULT_X_DA_LINK,
    DEFAULT_X_NSFW_NOTICE
)


class TestCalculateXCharCount:
    """测试 X 平台字符数加权计算逻辑 (Twitter-Text Spec)"""

    def test_empty_text(self):
        assert calculate_x_char_count("") == 0
        assert calculate_x_char_count(None) == 0

    def test_plain_english_text(self):
        text = "Hello world!"
        assert calculate_x_char_count(text) == len(text)

    def test_chinese_text(self):
        text = "你好世界，艺术创作同步发布助手"
        # 15 个汉字及中文全角标点，Twitter 加权为 15 * 2 = 30
        assert calculate_x_char_count(text) == 30

    def test_single_short_and_long_url(self):
        # 无论 URL 实际长度如何，X 均统一折算为 23 字符权重
        short_url = "https://t.co/xyz"
        long_url = "https://www.example.com/gallery/artworks/2026/detail?id=987654321&ref=twitter_share"
        assert calculate_x_char_count(short_url) == 23
        assert calculate_x_char_count(long_url) == 23

    def test_mixed_text_with_url(self):
        text = "Check out my new art: https://deviantart.com/myname Thanks!"
        expected = len("Check out my new art: ") + 23 + len(" Thanks!")
        assert calculate_x_char_count(text) == expected

    def test_multiple_urls(self):
        text = "DA: https://deviantart.com/a | Patreon: https://patreon.com/b"
        expected = len("DA: ") + 23 + len(" | Patreon: ") + 23
        assert calculate_x_char_count(text) == expected

    def test_newlines_and_emojis(self):
        text = "🎨 Title\n\nLine 1\nLine 2"
        # 🎨 为 Emoji 权重为 2，其余 ASCII/换行字符 21 个，合计 23
        assert calculate_x_char_count(text) == 23


class TestTruncateAtSentenceBoundary:
    """测试在句子边界严格截断，绝不截断半句"""

    def test_empty_and_zero_budget(self):
        assert truncate_at_sentence_boundary("", 100) == ""
        assert truncate_at_sentence_boundary("Hello", 0) == ""
        assert truncate_at_sentence_boundary("Hello", -10) == ""

    def test_within_budget_returns_full(self):
        text = "This is a full sentence within budget."
        assert truncate_at_sentence_boundary(text, 100) == text

    def test_english_sentence_boundary(self):
        text = "First sentence is great. Second sentence is fantastic! Third sentence goes on and on and should be cut."
        # 预算只够前两句
        budget = len("First sentence is great. Second sentence is fantastic!") + 5
        res = truncate_at_sentence_boundary(text, budget)
        assert res == "First sentence is great. Second sentence is fantastic!"
        assert "Third" not in res

    def test_chinese_sentence_boundary(self):
        text = "这是作品第一句。这是作品第二句！这是作品第三句？后面的内容极其冗长不应出现在预告中……"
        budget = calculate_x_char_count("这是作品第一句。这是作品第二句！") + 4
        res = truncate_at_sentence_boundary(text, budget)
        assert res == "这是作品第一句。这是作品第二句！"
        assert "第三句" not in res

    def test_no_sentence_boundary_in_budget_returns_empty(self):
        text = "ThisIsAnExtremelyLongWordOrSentenceWithoutAnyPunctuationMarkAtAll"
        res = truncate_at_sentence_boundary(text, 20)
        assert res == ""


class TestFormatXTeaserPost:
    """测试 X (Twitter) 方案 B 统一预告 + 引流格式"""

    def test_2000_char_long_text_teaser(self):
        """
        单测要求：2000 字长正文
        - 断言格式正确：[正文预告]\n\n🔞 NSFW full set on DeviantArt 👇 [DA链接]\n\n[标签行]
        - 加权 ≤ 280
        - 标签与外链完整
        - 正文在句子边界截断无半句
        """
        # 构建超过 2000 字符的长篇正文，由多句完整中英文组成
        sentence_pool = [
            "这是该系列插画设定的详尽世界观背景介绍第一卷。",
            "女主角在迷雾森林深处发现了被封印千年的古老遗迹与魔法水晶！",
            "随着符文光芒亮起，四周的重力场发生剧烈扭曲？",
            "这一幕彻底改变了整个王国大陆未来的历史命运……",
            "The protagonist bravely stepped forward into the mysterious glowing sanctuary.",
            "Dark shadows gathered around the ancient altar, threatening to consume all light!"
        ]
        long_desc = "\n".join(sentence_pool * 25)  # 远远超过 2000 字
        assert len(long_desc) > 2000

        tags = ["scifi", "anime", "illustration", "conceptart"]
        res = format_x_teaser_post(
            title="【作品发布】迷雾遗迹",
            desc=long_desc,
            tags=tags,
            da_link=DEFAULT_X_DA_LINK,
            nsfw_notice=DEFAULT_X_NSFW_NOTICE,
            limit=280
        )

        # 1. 加权 ≤ 280
        count = calculate_x_char_count(res)
        assert count <= 280

        # 2. 标签与外链完整
        assert DEFAULT_X_DA_LINK in res
        assert DEFAULT_X_NSFW_NOTICE in res
        assert "#scifi" in res
        assert "#anime" in res
        assert "#illustration" in res
        assert "#conceptart" in res

        # 3. 格式正确: [正文预告] \n\n 提示句 + 外链 \n\n [标签行]
        parts = res.split("\n\n")
        assert len(parts) >= 3
        # 倒数第二段必须是 notice_line
        assert f"{DEFAULT_X_NSFW_NOTICE} {DEFAULT_X_DA_LINK}" in parts[-2]
        # 最后一段必须是标签行
        assert all(f"#{t}" in parts[-1] for t in tags)

        # 4. 正文在句子边界截断无半句
        teaser_body = "\n\n".join(parts[:-2])
        # 预告末尾必须以合法的标点终结，不能截在词语半当中
        assert teaser_body[-1] in ("。", "！", "？", "…", ".", "!")

    def test_100_char_short_text_full_preservation(self):
        """
        单测要求：100 字短正文
        - 断言正文全文保留 + 提示句 + 外链 + 标签
        """
        short_desc = "今天完成了一张全新的机甲少女插画，尝试了全新的金属高光渲染技法，希望大家能喜欢！"
        tags = ["art", "mecha"]
        res = format_x_teaser_post(
            title="原创机甲少女",
            desc=short_desc,
            tags=tags,
            da_link=DEFAULT_X_DA_LINK,
            nsfw_notice=DEFAULT_X_NSFW_NOTICE,
            limit=280
        )

        # 加权 ≤ 280
        assert calculate_x_char_count(res) <= 280

        # 短正文全文保留
        assert "原创机甲少女" in res
        assert short_desc in res

        # 提示句 + 外链 + 标签完整保留
        assert DEFAULT_X_NSFW_NOTICE in res
        assert DEFAULT_X_DA_LINK in res
        assert "#art" in res
        assert "#mecha" in res

    def test_boundary_pure_tags(self):
        """边界测试：纯标签 (无 title, 无 desc)"""
        tags = ["character_design", "digital_painting"]
        res = format_x_teaser_post(title="", desc="", tags=tags)
        assert calculate_x_char_count(res) <= 280
        assert DEFAULT_X_DA_LINK in res
        assert "#character_design" in res
        assert "#digital_painting" in res

    def test_boundary_pure_body_no_tags(self):
        """边界测试：纯正文 (无 tags)"""
        res = format_x_teaser_post(title="", desc="Simple artwork without any tags.", tags=[])
        assert calculate_x_char_count(res) <= 280
        assert "Simple artwork without any tags." in res
        assert DEFAULT_X_DA_LINK in res

    def test_boundary_no_body_no_tags(self):
        """边界测试：无正文且无标签"""
        res = format_x_teaser_post(title="", desc="", tags=[])
        assert DEFAULT_X_DA_LINK in res
        assert DEFAULT_X_NSFW_NOTICE in res

    def test_custom_da_link_and_notice_override(self):
        """测试可覆写 DA 链接与提示句"""
        custom_link = "https://www.deviantart.com/custom_artist"
        custom_notice = "🔥 Full uncensored gallery here:"
        res = format_x_teaser_post(
            title="Title",
            desc="Description text.",
            tags=["sample"],
            da_link=custom_link,
            nsfw_notice=custom_notice
        )
        assert custom_link in res
        assert custom_notice in res
        assert DEFAULT_X_DA_LINK not in res

    def test_tag_deduplication_against_body(self):
        """测试标签去重：若正文中已显式包含该 #tag，标签行不重复添加"""
        desc = "Check out this #cyberpunk artwork concept!"
        tags = ["cyberpunk", "futuristic"]
        res = format_x_teaser_post(title="", desc=desc, tags=tags)
        # #cyberpunk 在整篇推文中仅出现一次 (在正文中)
        assert res.count("#cyberpunk") == 1
        assert "#futuristic" in res

    def test_strips_old_patronage_signatures(self):
        """测试自动剥离正文中旧的赞助引流模板 (Patreon/Ko-fi 等)"""
        body = "Exciting new piece finished today!\n\n---\nSupport me on https://patreon.com/artist\nKo-fi: https://ko-fi.com/artist"
        res = format_x_teaser_post(title="", desc=body, tags=["art"])
        assert "Exciting new piece finished today!" in res
        assert "patreon.com" not in res
        assert "ko-fi.com" not in res
        assert DEFAULT_X_DA_LINK in res


class TestTruncateXText:
    """测试 truncate_x_text 遵循方案 B 逻辑"""

    def test_within_limit_no_truncation(self):
        text = "Short text under limit."
        trunc, cut_chars, da_ok = truncate_x_text(text, limit=280)
        assert trunc == text
        assert cut_chars == 0

    def test_over_limit_applies_plan_b(self):
        long_body = "This is a very long story. " * 30
        trunc, cut_chars, da_ok = truncate_x_text(long_body, limit=280)
        assert calculate_x_char_count(trunc) <= 280
        assert cut_chars > 0
        assert da_ok is True
        assert DEFAULT_X_DA_LINK in trunc


class TestSplitIntoXThreads:
    """测试 Thread 串推拆分逻辑"""

    def test_within_limit_single_thread(self):
        text = "This is a short post that fits into one tweet."
        chunks = split_into_x_threads(text, limit=280)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_split_multiple_chunks_with_numbering(self):
        sentences = [f"This is sentence #{i} of the thread." for i in range(20)]
        text = " ".join(sentences)
        limit = 140

        chunks = split_into_x_threads(text, limit=limit)
        assert len(chunks) > 1

        total = len(chunks)
        for i, c in enumerate(chunks):
            suffix = f"({i+1}/{total})"
            assert suffix in c
            assert calculate_x_char_count(c) <= limit

    def test_split_never_breaks_url(self):
        long_url = "https://www.example.com/very/long/destination/url/that/should/not/be/chopped/into/pieces"
        text = ("Introductory text line. " * 8) + "\n" + long_url + "\n" + ("Concluding text line. " * 8)
        limit = 200

        chunks = split_into_x_threads(text, limit=limit)
        assert len(chunks) > 1
        url_found = any(long_url in c for c in chunks)
        assert url_found is True
        for c in chunks:
            assert calculate_x_char_count(c) <= limit
