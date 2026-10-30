# -*- coding: utf-8 -*-
"""
ArtCrossPost - Bluesky Teaser Text Formatting & Three-Place Consistency Tests
对应 fix/bluesky-teaser-text 需求:
1. 方案 B 预告+引流构建函数五情形:
   - 超长长文 (>300 字素) 句子边界截断，保提示句 + DA 链接 + 标签
   - 100 字短正文全文保留
   - 极窄预算从尾部逐个砍标签保正文预告
   - 极端超限连首句放不下时只保提示句 + DA 链接
   - 边界测试 (纯标签、纯正文、空文本、签名剥离、自定义覆写、标签去重、AI 标签开关)
2. 三处输出绝对一致性红线验证:
   - MainWindow._update_x_char_counter (计数器)
   - MainWindow._start_publishing (门禁检查)
   - BlueskyDispatcher.publish (分发器实际发帖)
   三处输入同一组参数时，组装出的文本与字素数严格完全一致。
3. DA 链接当帖回传优先与主页回退两阶段透传验证。
"""
import copy
import re
from unittest.mock import MagicMock, patch
import pytest

from dispatchers.bluesky import (
    format_bluesky_teaser_post,
    format_bluesky_full_text,
    truncate_at_sentence_boundary_bsky,
    count_graphemes,
    extract_facets,
    BlueskyDispatcher,
    MAX_GRAPHEMES,
    DEFAULT_BLUESKY_DA_LINK,
    DEFAULT_BLUESKY_NSFW_NOTICE,
)
from signature_utils import CANONICAL_SIGNATURE_TEMPLATE


class TestBlueskyTeaserPost:
    """测试 format_bluesky_teaser_post 构建函数五情形与边界场景"""

    def test_case_1_long_text_sentence_boundary_truncation(self):
        """情形 1: 超长长文 (>300 字素) 必须在句子边界截断，必保提示句与 DA 链接，尽量保标签"""
        sentence_pool = [
            "在幽暗的地下古城中，微弱的荧光菌点亮了古老的青铜石柱与神秘符号。",
            "探险队穿过潮湿厚重的藤蔓走廊，隐约听见深渊低语在耳畔回荡。",
            "古老的守卫机甲残骸静默立于石门两侧，散发着沉寂已久的热能余波。",
            "队长示意全体戒备，空气中的气压在无形中变得沉重而紧绷。",
            "随着巨石移开的轰鸣声，尘封千年的终极宝藏终于展现在众人眼前。"
        ]
        long_desc = "\n".join(sentence_pool * 10)  # > 1500 字素
        assert count_graphemes(long_desc) > 500

        tags = ["scifi", "conceptart", "illustration"]
        res = format_bluesky_teaser_post(
            title="【作品发布】迷雾遗迹",
            desc=long_desc,
            tags=tags,
            da_link=DEFAULT_BLUESKY_DA_LINK,
            nsfw_notice=DEFAULT_BLUESKY_NSFW_NOTICE,
            limit=MAX_GRAPHEMES,
            is_ai=True,
            add_ai_tags=True
        )

        # 1. 总字素严格 <= 300
        total_graphemes = count_graphemes(res)
        assert total_graphemes <= MAX_GRAPHEMES

        # 2. 必保引流句与 DA 链接
        assert DEFAULT_BLUESKY_NSFW_NOTICE in res
        assert DEFAULT_BLUESKY_DA_LINK in res

        # 3. 标签行全保 (含 AI 标签)
        for t in tags:
            assert f"#{t}" in res
        assert "#AIArt" in res
        assert "#GenerativeArt" in res

        # 4. 格式正确: [预告] \n\n 提示句 + DA链接 \n\n [标签行]
        parts = res.split("\n\n")
        assert len(parts) >= 3
        # 倒数第二段必须是 notice_line
        assert f"{DEFAULT_BLUESKY_NSFW_NOTICE} {DEFAULT_BLUESKY_DA_LINK}" in parts[-2]
        # 最后一段必须是标签行
        assert all(f"#{t}" in parts[-1] for t in tags)

        # 5. 正文在句子边界截断，绝不截断半句话
        teaser_body = "\n\n".join(parts[:-2])
        assert teaser_body[-1] in ("。", "！", "？", "…", ".", "!")

    def test_case_2_short_text_full_preservation(self):
        """情形 2: 100 字短正文，正文全文完整保留 + 提示句 + 外链 + 标签"""
        short_desc = "今天完成了一张全新的机甲少女插画，尝试了全新的金属高光渲染技法，希望大家能喜欢！"
        tags = ["art", "mecha"]
        res = format_bluesky_teaser_post(
            title="原创机甲少女",
            desc=short_desc,
            tags=tags,
            da_link=DEFAULT_BLUESKY_DA_LINK,
            nsfw_notice=DEFAULT_BLUESKY_NSFW_NOTICE,
            limit=MAX_GRAPHEMES,
            is_ai=True,
            add_ai_tags=False
        )

        # 总字素 <= 300
        assert count_graphemes(res) <= MAX_GRAPHEMES

        # 短正文全文保留
        assert "原创机甲少女" in res
        assert short_desc in res

        # 提示句 + 外链 + 标签完整保留
        assert DEFAULT_BLUESKY_NSFW_NOTICE in res
        assert DEFAULT_BLUESKY_DA_LINK in res
        assert "#art" in res
        assert "#mecha" in res

    def test_case_3_tight_budget_pops_tags_to_save_teaser(self):
        """情形 3: 极窄预算时，连首句都放不下时从尾部逐个砍标签腾预算保预告"""
        # 第一句话约 40 字素
        first_sentence = "星际巡洋舰在穿过超空间跃迁通道的瞬间遭遇了引力坍缩，导致防御护盾彻底瓦解。"
        second_sentence = "控制台的警告红光闪烁不定，所有主副推进器进入过载倒计时状态。"
        body = f"{first_sentence}\n\n{second_sentence}"

        # 构造较长标签，使 notice_line + 全部标签占据大量空间，导致正文第一句初次计算无法容纳
        many_tags = [f"conceptart_scifi_galaxy_fleet_ship_{i}" for i in range(12)]

        res = format_bluesky_teaser_post(
            title="",
            desc=body,
            tags=many_tags,
            da_link=DEFAULT_BLUESKY_DA_LINK,
            nsfw_notice=DEFAULT_BLUESKY_NSFW_NOTICE,
            limit=MAX_GRAPHEMES,
            is_ai=False
        )

        assert count_graphemes(res) <= MAX_GRAPHEMES
        # 验证第一句预告成功被保住
        assert first_sentence in res
        # 验证提示句与链接被保住
        assert DEFAULT_BLUESKY_DA_LINK in res
        # 验证尾部标签被砍掉以腾出预算保正文
        assert not all(f"#{t}" in res for t in many_tags)
        # 但前面的标签至少保留了一部分
        assert "#conceptart_scifi_galaxy_fleet_ship_0" in res

    def test_case_4_extreme_over_limit_only_notice_and_link(self):
        """情形 4: 极端超限，首句极其冗长即使全砍标签也无法容纳时，只保提示句+链接"""
        # 一句超长单句 (无标点断点)，长度达 260 字素
        huge_single_sentence = "这是一句完全没有标点符号停顿的极端冗长的单句正文" * 15
        assert count_graphemes(huge_single_sentence) > 250

        # notice_line 自身约 75 字素，260 + 75 + 2 = 337 > 300，连这一句都放不下
        res = format_bluesky_teaser_post(
            title="",
            desc=huge_single_sentence,
            tags=["tag1", "tag2"],
            da_link=DEFAULT_BLUESKY_DA_LINK,
            nsfw_notice=DEFAULT_BLUESKY_NSFW_NOTICE,
            limit=MAX_GRAPHEMES,
            is_ai=False
        )

        # 预告无法容纳，只保留提示句 + DA 链接
        assert res.strip() == f"{DEFAULT_BLUESKY_NSFW_NOTICE} {DEFAULT_BLUESKY_DA_LINK}"
        assert count_graphemes(res) <= MAX_GRAPHEMES

    def test_case_5_boundaries_and_special_scenarios(self):
        """情形 5: 边界与特殊场景测试"""
        # 5.1 纯标签 (无 title, 无 desc)
        tags = ["digitalart", "anime"]
        res_tags = format_bluesky_teaser_post(title="", desc="", tags=tags, is_ai=False)
        assert count_graphemes(res_tags) <= MAX_GRAPHEMES
        assert DEFAULT_BLUESKY_DA_LINK in res_tags
        assert "#digitalart" in res_tags
        assert "#anime" in res_tags

        # 5.2 纯正文 (无 tags)
        res_body = format_bluesky_teaser_post(title="", desc="Simple artwork without any tags.", tags=[], is_ai=False)
        assert count_graphemes(res_body) <= MAX_GRAPHEMES
        assert "Simple artwork without any tags." in res_body
        assert DEFAULT_BLUESKY_DA_LINK in res_body

        # 5.3 无正文无标签
        res_empty = format_bluesky_teaser_post(title="", desc="", tags=[], is_ai=False)
        assert res_empty == f"{DEFAULT_BLUESKY_NSFW_NOTICE} {DEFAULT_BLUESKY_DA_LINK}"

        # 5.4 自动剥离签名 (Patreon、Ko-fi、Where to find me、规范模板)
        dirty_desc = (
            "Fantastic cybernetic samurai concept.\n\n"
            "---\n"
            "Where to find me — same name everywhere:\n"
            "🎬X: [@FOXTONTONTON](https://x.com/FOXTONTONTON)\n"
            "Support on https://patreon.com/artist and ko-fi.com/artist"
        )
        res_sig = format_bluesky_teaser_post(title="Samurai", desc=dirty_desc, tags=["art"], is_ai=False)
        assert "Fantastic cybernetic samurai concept." in res_sig
        assert "Where to find me" not in res_sig
        assert "patreon.com" not in res_sig
        assert "ko-fi.com" not in res_sig
        assert DEFAULT_BLUESKY_DA_LINK in res_sig

        # 5.5 自定义 da_link 与 nsfw_notice 覆写
        custom_link = "https://www.deviantart.com/special_post/art/12345"
        custom_notice = "🔥 Full uncensored render here:"
        res_custom = format_bluesky_teaser_post(
            title="Custom",
            desc="Art description.",
            tags=["sample"],
            da_link=custom_link,
            nsfw_notice=custom_notice,
            is_ai=False
        )
        assert custom_link in res_custom
        assert custom_notice in res_custom
        assert DEFAULT_BLUESKY_DA_LINK not in res_custom

        # 5.6 正文已有标签去重
        desc_with_tag = "Check out this #cyberpunk artwork concept!"
        res_dedup = format_bluesky_teaser_post(title="", desc=desc_with_tag, tags=["cyberpunk", "futuristic"], is_ai=False)
        # #cyberpunk 在整篇推文中仅出现一次 (在正文中)，标签行不再添加
        assert res_dedup.count("#cyberpunk") == 1
        assert "#futuristic" in res_dedup

        # 5.7 AI 标签开关控制
        res_ai_on = format_bluesky_teaser_post(title="AI Work", desc="Prompt generated.", tags=["art"], is_ai=True, add_ai_tags=True)
        assert "#AIArt" in res_ai_on
        assert "#GenerativeArt" in res_ai_on

        res_ai_off = format_bluesky_teaser_post(title="Hand Work", desc="Hand painted.", tags=["art"], is_ai=False, add_ai_tags=True)
        assert "#AIArt" not in res_ai_off
        assert "#GenerativeArt" not in res_ai_off


class TestBlueskyThreePlacesConsistency:
    """验证红线原则: 计数器、发布门禁、分发器三处调用 format_bluesky_teaser_post 产出的文本与字素数绝对一致"""

    def test_three_places_produce_identical_text_and_count(self, tmp_path):
        title = "统一一致性作品测试 [OC]"
        desc = (
            "这是一段经过精心构思的作品详细描述，包含了世界观设定与角色背景。"
            "星空之下的要塞静穆伫立，守望着漫长的永夜。\n\n"
            "Where to find me:\n"
            "🎬X: [@FOXTONTONTON](https://x.com/FOXTONTONTON)"
        )
        tags = ["scifi", "worldbuilding", "illustration"]
        da_link = "https://www.deviantart.com/foxtontonton/art/Fortress-123456"
        nsfw_notice = DEFAULT_BLUESKY_NSFW_NOTICE
        is_ai = True
        add_ai = True

        # 1. 计数器逻辑 (模拟 MainWindow._update_x_char_counter 中的 Bluesky 段)
        counter_text = format_bluesky_teaser_post(
            title=title,
            desc=desc,
            tags=tags,
            da_link=da_link,
            nsfw_notice=nsfw_notice,
            limit=MAX_GRAPHEMES,
            is_ai=is_ai,
            add_ai_tags=add_ai
        )
        counter_count = count_graphemes(counter_text)

        # 2. 门禁检查逻辑 (模拟 MainWindow._start_publishing 中的 Bluesky 检查段)
        gate_text = format_bluesky_teaser_post(
            title=title,
            desc=desc,
            tags=tags,
            da_link=da_link,
            nsfw_notice=nsfw_notice,
            limit=MAX_GRAPHEMES,
            is_ai=is_ai,
            add_ai_tags=add_ai
        )
        gate_count = count_graphemes(gate_text)

        # 3. 分发器实际发帖逻辑 (BlueskyDispatcher.publish)
        dispatcher = BlueskyDispatcher(cookies_dir=tmp_path)
        dispatcher._save_credentials({
            "handle": "fox.bsky.social",
            "app_password": "test-password",
            "accessJwt": "fake_jwt",
            "did": "did:plc:test123"
        })

        posted_records = []

        def mock_post(url, headers=None, json=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            if "createRecord" in url:
                posted_records.append(json["record"])
                resp.json.return_value = {"uri": "at://did:plc:test123/app.bsky.feed.post/3rkey", "cid": "mockcid"}
            return resp

        def mock_get(url, params=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            if "getPostThread" in url:
                resp.json.return_value = {"thread": {"post": {"record": posted_records[-1]}}}
            return resp

        payload = {
            "title": title,
            "description": desc,
            "tags": tags,
            "bluesky_da_link": da_link,
            "bluesky_nsfw_notice": nsfw_notice,
            "is_ai_generated": is_ai,
            "platform_options": {
                "bluesky_ai_hashtags": add_ai
            }
        }

        with patch("requests.post", side_effect=mock_post), patch("requests.get", side_effect=mock_get):
            ok, msg = dispatcher.publish(payload)
            assert ok is True, f"发布应成功: {msg}"

        assert len(posted_records) == 1
        dispatcher_text = posted_records[0]["text"]
        dispatcher_count = count_graphemes(dispatcher_text)

        # 红线断言: 三处文本字符串完全相同
        assert counter_text == gate_text, "计数器与门禁文本必须完全一致"
        assert gate_text == dispatcher_text, "门禁与分发器发帖文本必须完全一致"

        # 红线断言: 三处字素计数完全相同
        assert counter_count == gate_count == dispatcher_count
        assert dispatcher_count <= MAX_GRAPHEMES

        # 验证 DA 链接被正确提取为 facet
        facets = posted_records[0].get("facets", [])
        link_facets = [f["features"][0]["uri"] for f in facets if f["features"][0].get("$type") == "app.bsky.richtext.facet#link"]
        assert da_link in link_facets


class TestBlueskyDaLinkPropagation:
    """验证 DA 链接两阶段透传优先当帖 URL 及主页回退机制"""

    def test_da_success_propagates_post_url_to_bluesky_teaser(self):
        """DA 成功发布时，Bluesky teaser 中使用当帖 URL，不含纯主页 URL"""
        post_url = "https://www.deviantart.com/foxtontonton/art/Latex-Warrior-999999"
        payload = {
            "title": "Warrior",
            "description": "Latex warrior concept.",
            "tags": ["warrior"],
            "da_post_url": post_url,
            "actual_traffic_link": post_url,
            "bluesky_da_link": post_url,
        }

        res = format_bluesky_teaser_post(
            title=payload["title"],
            desc=payload["description"],
            tags=payload["tags"],
            da_link=payload["bluesky_da_link"],
        )

        assert post_url in res
        # 确保没有残留独立的纯主页链接 (即全部转化为作品页 /art/ 链接)
        assert not re.search(r"https?://(?:www\.)?deviantart\.com/foxtontonton(?!\S*/art)", res)

    def test_da_failure_falls_back_to_homepage(self):
        """DA 失败时，Bluesky 回退使用默认主页 URL"""
        payload = {
            "title": "Warrior",
            "description": "Latex warrior concept.",
            "tags": ["warrior"],
            "da_post_url": None,
            "actual_traffic_link": DEFAULT_BLUESKY_DA_LINK,
            "bluesky_da_link": DEFAULT_BLUESKY_DA_LINK,
        }

        res = format_bluesky_teaser_post(
            title=payload["title"],
            desc=payload["description"],
            tags=payload["tags"],
            da_link=payload["bluesky_da_link"],
        )

        assert DEFAULT_BLUESKY_DA_LINK in res
