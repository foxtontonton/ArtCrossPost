import logging
import time
from unittest.mock import MagicMock, patch
import pytest

from dispatchers.civitai import CivitaiDispatcher


class FakeFastTime:
    """快速推进时间模拟器，避免在徽章轮询时真实 sleep 2.5s"""
    def __init__(self, start: float = 1000.0):
        self.current = start

    def time(self) -> float:
        return self.current

    def sleep(self, secs: float):
        self.current += secs


def test_civitai_tag_circuit_breaker_all_badge_missing(tmp_path, caplog):
    """
    假页面断言无徽章时，在连续第 3 个标签失败后立即触发熔断:
    - 仅尝试前 3 个标签，剩余 7 个标签直接跳过
    - failed_count == 10 (3 个尝试失败 + 7 个跳过)
    - success_count == 0
    - 告警日志记录连续失败 3 次并跳过剩余 7 个标签
    """
    disp = CivitaiDispatcher(tmp_path)
    mock_page = MagicMock()

    mock_input = MagicMock()
    mock_input.is_visible.return_value = True

    # 模拟输入框出现，但徽章始终为 None
    def fake_query_selector(selector: str):
        if "input" in selector or "Autocomplete" in selector:
            return mock_input
        if "Badge" in selector or "badge" in selector or "post:tag" in selector:
            return None
        return None

    mock_page.query_selector.side_effect = fake_query_selector

    tags = [f"tag_{i}" for i in range(1, 11)]  # 10 个标签
    timer = FakeFastTime()

    with patch("dispatchers.civitai.time.time", side_effect=timer.time), \
         patch("dispatchers.civitai.time.sleep", side_effect=timer.sleep), \
         caplog.at_level(logging.WARNING):

        ok_cnt, fail_cnt = disp.inject_tags(mock_page, tags, per_tag_timeout_sec=1.0)

    # 1. 断言返回值
    assert ok_cnt == 0
    assert fail_cnt == 10

    # 2. 断言输入填充只调用了 3 次 (第 3 次失败后立即熔断，未执行后续 7 个)
    assert mock_input.fill.call_count == 3
    called_tags = [call_args[0][0] for call_args in mock_input.fill.call_args_list]
    assert called_tags == ["tag_1", "tag_2", "tag_3"]

    # 3. 断言熔断日志
    expected_msg = "[Civitai] 标签注入连续失败 3 次触发熔断，跳过剩余 7 个标签"
    assert any(expected_msg in r.message for r in caplog.records)


def test_civitai_tag_circuit_breaker_mixed_sequence(tmp_path, caplog):
    """
    混合序列（假假真假假假）:
    - 前两次失败，第三次成功 -> 连续失败计数清零
    - 随后再连续失败 3 次 -> 在第 6 个标签处触发熔断
    - 剩余 4 个标签直接跳过
    - 结果: 成功 1，失败 9 (5 尝试失败 + 4 跳过)
    """
    disp = CivitaiDispatcher(tmp_path)
    mock_page = MagicMock()

    mock_input = MagicMock()
    mock_input.is_visible.return_value = True
    mock_badge = MagicMock()

    # tag_3 成功，其余标签徽章不出现
    def fake_query_selector(selector: str):
        if "input" in selector or "Autocomplete" in selector:
            return mock_input
        if "tag_3" in selector:
            return mock_badge
        if "Badge" in selector or "badge" in selector:
            return None
        return None

    mock_page.query_selector.side_effect = fake_query_selector

    tags = [f"tag_{i}" for i in range(1, 11)]  # 10 个标签: tag_1 .. tag_10
    timer = FakeFastTime()

    with patch("dispatchers.civitai.time.time", side_effect=timer.time), \
         patch("dispatchers.civitai.time.sleep", side_effect=timer.sleep), \
         caplog.at_level(logging.WARNING):

        ok_cnt, fail_cnt = disp.inject_tags(mock_page, tags, per_tag_timeout_sec=1.0)

    # 1. 结果断言: tag_3 成功，共 1 成功，9 失败
    assert ok_cnt == 1
    assert fail_cnt == 9

    # 2. fill 调用次数断言: tag_1, tag_2, tag_3, tag_4, tag_5, tag_6 共 6 次，7..10 未执行
    assert mock_input.fill.call_count == 6
    called_tags = [call_args[0][0] for call_args in mock_input.fill.call_args_list]
    assert called_tags == ["tag_1", "tag_2", "tag_3", "tag_4", "tag_5", "tag_6"]

    # 3. 熔断日志断言
    expected_msg = "[Civitai] 标签注入连续失败 3 次触发熔断，跳过剩余 4 个标签"
    assert any(expected_msg in r.message for r in caplog.records)


def test_civitai_tag_circuit_breaker_resets_on_success_no_trip(tmp_path, caplog):
    """
    交替序列（假假真假真）:
    - 连续失败未达 3 次，熔断器不触发
    - 全部 5 个标签均被尝试执行
    """
    disp = CivitaiDispatcher(tmp_path)
    mock_page = MagicMock()

    mock_input = MagicMock()
    mock_input.is_visible.return_value = True
    mock_badge = MagicMock()

    def fake_query_selector(selector: str):
        if "input" in selector or "Autocomplete" in selector:
            return mock_input
        # tag_3 与 tag_5 成功
        if "tag_3" in selector or "tag_5" in selector:
            return mock_badge
        return None

    mock_page.query_selector.side_effect = fake_query_selector

    tags = ["tag_1", "tag_2", "tag_3", "tag_4", "tag_5"]
    timer = FakeFastTime()

    with patch("dispatchers.civitai.time.time", side_effect=timer.time), \
         patch("dispatchers.civitai.time.sleep", side_effect=timer.sleep), \
         caplog.at_level(logging.WARNING):

        ok_cnt, fail_cnt = disp.inject_tags(mock_page, tags, per_tag_timeout_sec=1.0)

    assert ok_cnt == 2
    assert fail_cnt == 3
    assert mock_input.fill.call_count == 5
    assert not any("触发熔断" in r.message for r in caplog.records)


def test_civitai_tag_entry_missing_idx0_preserves_initial_behavior(tmp_path, caplog):
    """
    测试保留首标签入口未找到直接放弃全部标签的既有行为:
    - idx == 0 时找不到 tag_btn，立即退出返回 (0, total)
    """
    disp = CivitaiDispatcher(tmp_path)
    mock_page = MagicMock()
    mock_page.query_selector.return_value = None  # 找不到输入框与入口按钮

    tags = [f"tag_{i}" for i in range(1, 6)]

    with caplog.at_level(logging.WARNING):
        ok_cnt, fail_cnt = disp.inject_tags(mock_page, tags, per_tag_timeout_sec=0.5)

    assert ok_cnt == 0
    assert fail_cnt == 5
    assert any("标签入口未找到，已跳过全部标签" in r.message for r in caplog.records)
    assert not any("触发熔断" in r.message for r in caplog.records)


def test_civitai_tag_circuit_breaker_subsequent_button_click_failures(tmp_path, caplog):
    """
    测试后续标签（idx > 0）点击 '+ Tag' 按钮连续异常时同样触发熔断:
    - tag_1 成功
    - tag_2, tag_3, tag_4 点击按钮抛出异常 -> 连续 3 次失败触发熔断
    - tag_5, tag_6 直接跳过
    """
    disp = CivitaiDispatcher(tmp_path)
    mock_page = MagicMock()

    mock_input = MagicMock()
    mock_badge = MagicMock()
    mock_btn = MagicMock()

    current_idx = [0]

    def fake_query_selector(selector: str):
        if current_idx[0] == 0:
            # 标签 1: 输入框直接可见，徽章可查
            if "input" in selector:
                return mock_input
            if "tag_1" in selector:
                return mock_badge
            return None
        else:
            # 标签 2 以后: 输入框不可见，需要点按钮
            if "TAG" in selector or "+ Tag" in selector:
                return mock_btn
            return None

    def fake_fill(text, **kwargs):
        pass

    mock_input.fill.side_effect = fake_fill
    mock_page.query_selector.side_effect = fake_query_selector

    # 按钮点击抛出异常
    mock_btn.click.side_effect = RuntimeError("按钮被模态浮层遮挡")

    tags = [f"tag_{i}" for i in range(1, 7)]  # 6 个标签
    timer = FakeFastTime()

    # 跟踪当前标签索引
    orig_qs = fake_query_selector
    def tracking_qs(selector):
        return orig_qs(selector)
    mock_page.query_selector.side_effect = tracking_qs

    # 由于 inject_tags 对每个 tag 执行一个循环，我们在循环中更新 current_idx
    with patch("dispatchers.civitai.time.time", side_effect=timer.time), \
         patch("dispatchers.civitai.time.sleep", side_effect=timer.sleep), \
         caplog.at_level(logging.WARNING):

        # 让 input_visible 在 idx==0 为 True，之后为 False
        def input_visible():
            return current_idx[0] == 0
        mock_input.is_visible.side_effect = input_visible

        # 挂钩循环：在 tag_input.fill 调用时递增 current_idx
        def on_fill(tag_val, **kwargs):
            current_idx[0] += 1
        mock_input.fill.side_effect = on_fill

        # 在 mock_btn.click 时也递增
        def on_btn_click(**kwargs):
            current_idx[0] += 1
            raise RuntimeError("按钮被遮挡")
        mock_btn.click.side_effect = on_btn_click

        ok_cnt, fail_cnt = disp.inject_tags(mock_page, tags, per_tag_timeout_sec=0.5)

    assert ok_cnt == 1
    assert fail_cnt == 5
    assert any("[Civitai] 标签注入连续失败 3 次触发熔断，跳过剩余 2 个标签" in r.message for r in caplog.records)
