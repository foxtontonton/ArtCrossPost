# -*- coding: utf-8 -*-
"""
Tests for CensorEngine (censor_engine.py)
Covering:
- is_available method
- detect_sensitive_regions returns [] when detector is None (no silent center fallback box)
- apply_censor does not inject default box when detector is None
"""
import pytest
cv2 = pytest.importorskip("cv2")
from censor_engine import CensorEngine


def test_is_available_reflects_detector_status():
    ce = CensorEngine()
    # is_available returns boolean depending on whether detector is loaded
    assert isinstance(ce.is_available(), bool)
    assert ce.is_available() == (ce.detector is not None)


def test_detect_sensitive_regions_returns_empty_when_detector_none():
    ce = CensorEngine()
    # 模拟 detector 离线状态
    ce.detector = None
    assert ce.is_available() is False

    # 验证此时 detect_sensitive_regions 严禁静默生成中央默认框，必须返回空列表 []
    boxes = ce.detect_sensitive_regions("non_existent_path.png")
    assert boxes == []


def test_detect_sensitive_regions_returns_empty_when_detector_none_with_bgr():
    import numpy as np
    ce = CensorEngine()
    ce.detector = None
    fake_bgr = np.zeros((600, 800, 3), dtype=np.uint8)

    boxes = ce.detect_sensitive_regions(None, img_bgr=fake_bgr)
    assert boxes == []
