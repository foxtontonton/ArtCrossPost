# -*- coding: utf-8 -*-
"""
针对「X 视频版自动配对 + 处理 (feature/x-video-variant)」的完整单元测试：
涵盖规则 1–14：
- 配对测试 (规则 1–5)：
  a. 同目录同主名 -> 直接配对
  b. 同主名在其他子目录（如 gif/ 与 video/ 分开放） -> 配对
  c. 主名仅尾部计数不同（去掉 _00001 这类后缀后相同） -> 按计数差最小配对
  d. 一个视频被配对后不再参与其他 GIF 的自动配对（先到先得，按导入顺序）；孤儿视频直接忽略，不单独成帖
  e. 缩略图徽标区分来源（自动配对 / 手动指定），支持手动指定、改绑与解绑；自动分组审核弹窗显示配对视频并支持当场改绑
- 单 GIF 帖生效规则 (规则 6)：
  仅单 GIF 帖对 X 生效；多图帖忽略配对
- 视频处理与缓存测试 (规则 7–10, 14)：
  ffmpeg -f lavfi 生成 2s 测试视频 -> 跑 black_bar + 水印 ->
  断言产物存在、编码 h264/yuv420p、中间帧打码区域像素为黑、水印角区域非空、
  进度回调 (time=) 正常触发、相同源文件指纹+参数哈希命中缓存不重复编码，
  以及 mosaic / blur 滤镜链构建与执行正常
- X 端分发与发前自检门禁测试 (规则 11–13)：
  a. 有配对视频时传处理后的 mp4，独立分支等待「处理完成」态再等发帖按钮激活，180s 超时报明确原因
  b. 无配对视频时保留现行 GIF 路径不变
  c. 发前自检：配对视频存在、产物 <= 512MB、时长 <= 140s（用 ffmpeg -i 解析），不合规提前阻断
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from PIL import Image
from PyQt5.QtWidgets import QApplication

from config import DEFAULT_CONFIG
from video_engine import (
    VideoEngine,
    get_ffmpeg_path,
    is_ffmpeg_available,
    split_stem_and_counter,
    find_matching_video_for_gif,
    pair_gifs_with_videos,
    probe_video_info,
    probe_video_duration,
    validate_x_video_constraints,
)
from auto_grouper import scan_and_group_folder


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _create_dummy_gif(path: Path, size=(120, 90)):
    path.parent.mkdir(parents=True, exist_ok=True)
    f1 = Image.new("RGB", size, (200, 50, 50))
    f2 = Image.new("RGB", size, (50, 200, 50))
    f1.save(str(path), save_all=True, append_images=[f2], duration=100, loop=0)
    return path


def _create_dummy_video_file(path: Path, content: bytes = b"fake_mp4_content"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _generate_lavfi_test_video(out_path: Path, duration: float = 2.0, size: str = "400x300", rate: int = 15):
    """使用 ffmpeg -f lavfi 生成标准测试视频"""
    ffmpeg_exe = get_ffmpeg_path()
    assert ffmpeg_exe, "测试环境需要 imageio-ffmpeg 提供可用 ffmpeg"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe,
        "-y",
        "-hide_banner",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size={size}:rate={rate}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"生成测试视频失败: {res.stderr}"
    assert out_path.exists() and out_path.stat().st_size > 0
    return out_path


# ==============================================================================
# 1. 配对测试：构造 output 目录样本（同名配对、计数错位配对、跨子目录配对、孤儿视频、多候选竞争）
# ==============================================================================


def test_pairing_priority_and_competition_rules(tmp_path):
    """
    断言规则 2–4：
    - 2a: 同目录同主名 -> 直接配对
    - 2b: 同主名在其他子目录（如 gif/ 与 video/ 分开放） -> 配对
    - 2c: 主名仅尾部计数不同（去掉 _00001 这类后缀后相同） -> 按计数差最小配对
    - 4:  一个视频被配对后不再参与其他 GIF 的自动配对（先到先得，按导入顺序）；
          找不到 GIF 的孤儿视频直接忽略，不单独成帖
    """
    output_dir = tmp_path / "comfyui_output"
    gif_sub = output_dir / "gif"
    vid_sub = output_dir / "video"
    same_dir = output_dir / "mixed"

    # 样本 1: 同目录同主名 (2a 优先级最高，即使子目录也有同名视频也优先配同目录)
    gif_same = _create_dummy_gif(same_dir / "hero_pose_00001.gif")
    vid_same_local = _create_dummy_video_file(same_dir / "hero_pose_00001.mp4")
    _create_dummy_video_file(vid_sub / "hero_pose_00001.mp4")

    # 样本 2: 跨子目录同主名 (2b: gif/ 与 video/ 分开放)
    gif_cross = _create_dummy_gif(gif_sub / "cyber_girl_00005.gif")
    vid_cross = _create_dummy_video_file(vid_sub / "cyber_girl_00005.mp4")

    # 样本 3: 尾部计数错位 + 多候选竞争 (2c + 规则 4 先到先得)
    # boss_fight_00010.gif 面对 _00007.mp4 (差3)、_00011.mp4 (差1)、_00020.mp4 (差10)
    # 应选计数差最小的 _00011.mp4；
    # 随后导入的 boss_fight_00012.gif 因 _00011.mp4 已被占用，只能在剩余候选中选距离最近的 _00007.mp4 (差5 < 差8)
    gif_cnt_1 = _create_dummy_gif(gif_sub / "boss_fight_00010.gif")
    gif_cnt_2 = _create_dummy_gif(gif_sub / "boss_fight_00012.gif")
    vid_cnt_7 = _create_dummy_video_file(vid_sub / "boss_fight_00007.mp4")
    vid_cnt_11 = _create_dummy_video_file(vid_sub / "boss_fight_00011.mp4")
    _create_dummy_video_file(vid_sub / "boss_fight_00020.webm")

    # 样本 4: 孤儿视频 (无对应 GIF)
    orphan_vid_1 = _create_dummy_video_file(vid_sub / "orphan_clip_00001.mp4")
    orphan_vid_2 = _create_dummy_video_file(same_dir / "standalone_movie.mov")

    gif_order = [str(gif_same), str(gif_cross), str(gif_cnt_1), str(gif_cnt_2)]
    paired_map = pair_gifs_with_videos(gif_order, comfyui_output_dir=output_dir)

    # 断言 2a: 同目录同主名优先命中 same_dir 下的 mp4
    assert Path(paired_map[str(gif_same)]) == vid_same_local.resolve()

    # 断言 2b: 跨子目录同主名命中 video/ 下的 mp4
    assert Path(paired_map[str(gif_cross)]) == vid_cross.resolve()

    # 断言 2c + 4: 计数差最小配对 + 先到先得互斥
    assert Path(paired_map[str(gif_cnt_1)]) == vid_cnt_11.resolve()
    assert Path(paired_map[str(gif_cnt_2)]) == vid_cnt_7.resolve()

    # 断言 4: 孤儿视频不参与配对，且扫描文件夹自动分组时孤儿视频绝不单独成帖
    paired_Trigger_videos = {Path(v).resolve() for v in paired_map.values()}
    assert orphan_vid_1.resolve() not in paired_Trigger_videos
    assert orphan_vid_2.resolve() not in paired_Trigger_videos

    groups = scan_and_group_folder(output_dir)
    all_grouped_files = [Path(f).name for files in groups.values() for f in files]
    assert "orphan_clip_00001.mp4" not in all_grouped_files
    assert "standalone_movie.mov" not in all_grouped_files


def test_split_stem_and_counter_variants():
    """验证 ComfyUI 常见尾部计数后缀解析"""
    assert split_stem_and_counter("render_00001") == ("render", 1)
    assert split_stem_and_counter("scene_A-0012") == ("scene_A", 12)
    assert split_stem_and_counter("my_work_00003_") == ("my_work", 3)
    assert split_stem_and_counter("plain_title") == ("plain_title", None)


# ==============================================================================
# 2. 处理测试：ffmpeg -f lavfi 生成 2s 测试视频 → 跑 black_bar + 水印 → 断言产物与像素
# ==============================================================================


def test_video_engine_black_bar_and_watermark_processing(tmp_path):
    """
    断言规则 7–10, 14：
    - 输入 ffmpeg -f lavfi 生成的 2s 测试视频
    - 应用有效打码框 black_bar + 透明 PNG 水印 overlay
    - 断言产物存在、编码为 h264、像素格式为 yuv420p
    - 断言中间帧 (t=1.0s) 打码区域像素为黑
    - 断言水印角区域 (右下角) 非空且与无水印背景有显著差异
    - 断言 stderr time= 进度回调被触发，且相同参数二次调用直接命中缓存不重编码
    """
    assert is_ffmpeg_available(), "需要 imageio-ffmpeg"
    src_video = _generate_lavfi_test_video(tmp_path / "input_testsrc.mp4", duration=2.0, size="400x300", rate=15)
    cache_dir = tmp_path / "cache"
    engine = VideoEngine(cache_dir=cache_dir)

    # 打码框选在画面中央偏左 [80, 60, 220, 180]
    censor_box = [80, 60, 220, 180]
    censor_params = {
        "enabled": True,
        "style": "black_bar",
        "boxes": [censor_box],
    }
    wm_params = {
        "enabled": True,
        "text": "@TEST_WATERMARK",
        "font_name": "微软雅黑 (Microsoft YaHei)",
        "font_size_pct": 6.0,
        "opacity": 1.0,
        "position": "bottom_right",
        "color_hex": "#ffffff",
        "stroke": True,
    }

    progress_events = []

    def _on_prog(pct, msg):
        progress_events.append((pct, msg))

    out_mp4 = engine.process_video(
        src_video,
        censor_params=censor_params,
        wm_params=wm_params,
        progress_callback=_on_prog,
    )
    out_p = Path(out_mp4)
    assert out_p.exists() and out_p.stat().st_size > 0
    assert len(progress_events) >= 2
    assert progress_events[-1][0] == 100

    # 1. 断言编码 h264 / yuv420p / 分辨率保持 400x300
    info = probe_video_info(out_p)
    assert info["codec"] == "h264", f"期望编码 h264，实际: {info}"
    assert info["pix_fmt"] == "yuv420p", f"期望像素格式 yuv420p，实际: {info}"
    assert info["width"] == 400 and info["height"] == 300

    # 2. 提取中间帧 (约第 15 帧 / t=1.0s) 检查打码区域与水印角区域
    cap_src = cv2.VideoCapture(str(src_video))
    cap_out = cv2.VideoCapture(str(out_p))
    try:
        mid_frame_idx = 15
        cap_src.set(cv2.CAP_PROP_POS_FRAMES, mid_frame_idx)
        cap_out.set(cv2.CAP_PROP_POS_FRAMES, mid_frame_idx)
        ok_s, frame_src = cap_src.read()
        ok_o, frame_out = cap_out.read()
        assert ok_s and frame_src is not None
        assert ok_o and frame_out is not None

        # 2a. 断言中间帧打码区域内部像素为纯黑 (YUV420p 编码容差 <= 5)
        x1, y1, x2, y2 = censor_box
        roi_censor = frame_out[y1 + 6 : y2 - 6, x1 + 6 : x2 - 6]
        assert roi_censor.size > 0
        assert float(np.mean(roi_censor)) < 5.0, f"打码框区域应为纯黑，实际均值={np.mean(roi_censor):.2f}"
        assert int(np.max(roi_censor)) <= 12, f"打码框区域最大像素值异常: {np.max(roi_censor)}"

        # 2b. 断言右下角水印区域非空（透明 PNG 水印层非空，且合成帧右下角与原视频右下角存在显著像素差）
        wm_png = cache_dir / "check_wm_overlay.png"
        engine.render_watermark_overlay_png(400, 300, wm_params, wm_png)
        wm_rgba = np.array(Image.open(str(wm_png)).convert("RGBA"))
        corner_alpha = wm_rgba[200:300, 180:400, 3]
        assert int(np.count_nonzero(corner_alpha)) > 50, "右下角水印透明图层应包含非空文字像素"

        corner_src = frame_src[200:300, 180:400].astype(np.float32)
        corner_out = frame_out[200:300, 180:400].astype(np.float32)
        diff_corner = np.abs(corner_out - corner_src)
        assert float(np.max(diff_corner)) > 30.0, "右下角水印区域合成后应与原视频帧有显著像素差异"
    finally:
        cap_src.release()
        cap_out.release()

    # 3. 断言缓存复用：源文件与参数未变时不重跑 ffmpeg
    mtime_1 = out_p.stat().st_mtime_ns
    out_mp4_2 = engine.process_video(
        src_video,
        censor_params=censor_params,
        wm_params=wm_params,
    )
    assert out_mp4_2 == out_mp4
    assert out_p.stat().st_mtime_ns == mtime_1


def test_video_engine_mosaic_and_blur_filters(tmp_path):
    """验证 mosaic 与 blur 两种打码样式在 ffmpeg 滤镜链中均可正常生成合规 h264 视频"""
    src_video = _generate_lavfi_test_video(tmp_path / "src.mp4", duration=1.0, size="320x240", rate=10)
    engine = VideoEngine(cache_dir=tmp_path / "cache")

    for style in ("mosaic", "blur"):
        out_mp4 = engine.process_video(
            src_video,
            censor_params={"enabled": True, "style": style, "boxes": [[40, 40, 160, 140]]},
            wm_params=None,
        )
        assert Path(out_mp4).exists()
        info = probe_video_info(out_mp4)
        assert info["codec"] == "h264"
        assert info["pix_fmt"] == "yuv420p"


# ==============================================================================
# 3. 发前自检门禁、单 GIF vs 多图帖规则与 X 分发器转码等待测试 (规则 6, 11–13)
# ==============================================================================


def test_validate_x_video_constraints_gate(tmp_path):
    """
    断言规则 13：
    发前自检增加：配对视频存在、处理后产物 <= 512MB、时长 <= 140s（用 ffmpeg -i 解析），不合规提前阻断
    """
    # 1. 视频不存在 -> 阻断
    ok, err = validate_x_video_constraints(tmp_path / "missing.mp4")
    assert ok is False and "不存在" in err

    # 2. 正常 2s 视频 -> 通过
    valid_mp4 = _generate_lavfi_test_video(tmp_path / "valid_2s.mp4", duration=2.0, size="320x240", rate=10)
    dur = probe_video_duration(valid_mp4)
    assert 1.5 <= dur <= 2.5
    ok, msg = validate_x_video_constraints(valid_mp4, max_size_mb=512.0, max_duration_sec=140.0)
    assert ok is True and "校验通过" in msg

    # 3. 体积超限 -> 阻断
    ok_size, err_size = validate_x_video_constraints(valid_mp4, max_size_mb=0.0001, max_duration_sec=140.0)
    assert ok_size is False and "MB" in err_size

    # 4. 时长超限 (> 140s，此测试用 max_duration_sec=1.0 模拟阈值拦截) -> 阻断
    ok_dur, err_dur = validate_x_video_constraints(valid_mp4, max_size_mb=512.0, max_duration_sec=1.0)
    assert ok_dur is False and "时长" in err_dur


def test_x_dispatcher_video_transcode_wait_and_timeout(tmp_path, monkeypatch):
    """
    断言规则 11–12：
    - 有配对视频时，set_input_files 传处理后的 mp4；上传后等待 X 视频转码完成（「处理完成」态）再等发布按钮激活
    - 转码超时 (如 180s) 报明确原因并中止发布
    """
    from dispatchers.x_twitter import XTwitterDispatcher

    vid_path = _generate_lavfi_test_video(tmp_path / "processed_x.mp4", duration=1.0, size="320x240", rate=10)
    disp = XTwitterDispatcher(tmp_path / "cookies")
    monkeypatch.setattr(disp, "get_cookies", lambda: [{"name": "auth_token", "value": "xxx", "domain": ".x.com", "path": "/"}])

    uploaded_files = []

    class FakeElement:
        def __init__(self, text="", visible=True, enabled=True, aria_disabled="false"):
            self._text = text
            self._visible = visible
            self._enabled = enabled
            self._aria_disabled = aria_disabled

        def click(self, **kwargs):
            pass

        def fill(self, val):
            pass

        def inner_text(self):
            return self._text

        def text_content(self):
            return self._text

        def is_visible(self):
            return self._visible

        def is_enabled(self):
            return self._enabled

        def get_attribute(self, name):
            if name == "aria-disabled":
                return self._aria_disabled
            return None

        def set_input_files(self, files):
            uploaded_files.extend(files)

        def evaluate(self, script):
            pass

    class FakePage:
        def __init__(self, stick_in_processing=False):
            self.url = "https://x.com/home"
            self.stick_in_processing = stick_in_processing
            self.poll_count = 0

        def wait_for_timeout(self, ms):
            pass

        def wait_for_selector(self, sel, timeout=5000):
            return FakeElement()

        def query_selector(self, sel):
            if "tweetTextarea_0" in sel:
                return FakeElement("")
            if "fileInput" in sel or "input[type='file']" in sel:
                return FakeElement()
            if "progressbar" in sel:
                self.poll_count += 1
                if self.stick_in_processing:
                    return FakeElement(visible=True)
                # 前 2 次轮询处于转码中，第 3 次完成
                return FakeElement(visible=(self.poll_count < 3))
            if sel == "div[data-testid='attachments']":
                if self.stick_in_processing or self.poll_count < 3:
                    return FakeElement(text="正在处理视频 45%")
                return FakeElement(text="处理完成")
            if "video" in sel or "attachments" in sel:
                return FakeElement(text="处理完成")
            if "toast" in sel:
                return FakeElement(text="Your post was sent")
            return None

        def query_selector_all(self, sel):
            if "toast" in sel or "alert" in sel:
                return []
            if "tweetButton" in sel or "Post" in sel or "发帖" in sel:
                return [FakeElement(visible=True, enabled=True, aria_disabled="false")]
            return []

    class FakeContext:
        def __init__(self, page):
            self.pages = [page]

        def add_cookies(self, cookies):
            pass

        def new_page(self):
            return self.pages[0]

        def close(self):
            pass

    # Case A: 转码顺利进入「处理完成」态后激活发帖按钮并成功提交
    fake_page_ok = FakePage(stick_in_processing=False)
    monkeypatch.setattr(disp, "create_persistent_stealth_context", lambda p, d, headless=True: FakeContext(fake_page_ok))
    monkeypatch.setattr(disp, "create_stealth_browser", lambda p, headless=True: MagicMock(new_context=lambda **kw: FakeContext(fake_page_ok)))
    monkeypatch.setattr(disp, "apply_stealth", lambda page: None)
    monkeypatch.setattr(disp, "safe_goto", lambda page, url, timeout=30000: None)
    monkeypatch.setattr(disp, "assert_field_value", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(disp, "assert_attached", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(disp, "assert_submitted", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(disp, "close_login_session", lambda *a, **kw: None)

    ok, msg, _ = disp.publish({
        "title": "Video Post",
        "description": "Test desc",
        "tags": ["art"],
        "x_video": str(vid_path),
        "x_images": [str(vid_path)],
        "x_video_transcode_timeout": 5,
        "x_video_poll_interval": 0.05,
    })
    assert ok is True, f"期望视频转码完成后发布成功，实际: {msg}"
    assert uploaded_files and Path(uploaded_files[-1]).resolve() == vid_path.resolve()

    # Case B: 视频一直卡在转码进度条，超过超时阈值应报明确转码超时原因
    fake_page_timeout = FakePage(stick_in_processing=True)
    monkeypatch.setattr(disp, "create_persistent_stealth_context", lambda p, d, headless=True: FakeContext(fake_page_timeout))
    monkeypatch.setattr(disp, "create_stealth_browser", lambda p, headless=True: MagicMock(new_context=lambda **kw: FakeContext(fake_page_timeout)))

    ok_t, msg_t, _ = disp.publish({
        "title": "Video Timeout Post",
        "description": "Test desc",
        "tags": ["art"],
        "x_video": str(vid_path),
        "x_images": [str(vid_path)],
        "x_video_transcode_timeout": 0.25,
        "x_video_poll_interval": 0.05,
    })
    assert ok_t is False
    assert "视频转码超时" in msg_t


def test_x_dispatcher_video_ai_and_sensitive_compliance(tmp_path, monkeypatch):
    """
    验证 X (Twitter) 在视频模式下的 AI 内容披露与敏感警告 (Nudity + AI) 自动化打标与合规审计通过
    - 验证披露设置弹窗 (Made with AI switch) 正常开启
    - 验证视频编辑弹窗内部动态匹配内容警告标签页 (Tab 1) 并勾选「裸体」与「AI 生成」后保存
    """
    from dispatchers.x_twitter import XTwitterDispatcher

    vid_path = _generate_lavfi_test_video(tmp_path / "x_comp.mp4", duration=1.0, size="320x240", rate=10)
    disp = XTwitterDispatcher(tmp_path / "cookies")
    monkeypatch.setattr(disp, "get_cookies", lambda: [{"name": "auth_token", "value": "xxx", "domain": ".x.com", "path": "/"}])

    class MockCheckbox:
        def __init__(self, checked=False):
            self._checked = checked

        def is_checked(self):
            return self._checked

        def click(self, **kw):
            self._checked = True

        def evaluate(self, script):
            self._checked = True

    class MockDialog:
        def __init__(self, is_disc=False):
            self.is_disc = is_disc
            self.disc_cb = MockCheckbox(checked=False)
            self.nudity_cb = MockCheckbox(checked=False)
            self.ai_media_cb = MockCheckbox(checked=False)
            self.active_tab = 0

        def query_selector(self, sel):
            if "由 AI 生成" in sel or "Made with AI" in sel:
                target_cb = self.disc_cb if self.is_disc else self.ai_media_cb
                class FakeLabel:
                    def __init__(lbl_self, cb):
                        lbl_self._cb = cb
                    def query_selector(lbl_self, sub_sel):
                        return lbl_self._cb
                return FakeLabel(target_cb)
            if "裸体" in sel or "裸露" in sel or "Nudity" in sel:
                nudity_cb_ref = self.nudity_cb
                class FakeNudityLabel:
                    def query_selector(lbl_self, sub_sel):
                        return nudity_cb_ref
                return FakeNudityLabel()
            if "保存" in sel or "Save" in sel or "完成" in sel or "Done" in sel or "endEditingButton" in sel:
                class FakeBtn:
                    def click(self, **kw): pass
                    def evaluate(self, script): pass
                return FakeBtn()
            return None

        def query_selector_all(self, sel):
            if "[role='tab']" in sel:
                class FakeTab:
                    def __init__(tab_self, idx, text, label):
                        tab_self.idx = idx
                        tab_self.text = text
                        tab_self.label = label
                    def inner_text(tab_self): return tab_self.text
                    def get_attribute(tab_self, attr):
                        if attr == "aria-label": return tab_self.label
                        return None
                    def click(tab_self, **kw):
                        self.active_tab = tab_self.idx
                    def evaluate(tab_self, script):
                        self.active_tab = tab_self.idx
                return [FakeTab(0, "字幕", "字幕"), FakeTab(1, "内容警告", "内容警告")]
            return []

    curr_dialog = [None]

    class FakeCompliancePage:
        def __init__(self):
            self.url = "https://x.com/home"

        def wait_for_timeout(self, ms): pass
        def wait_for_selector(self, sel, timeout=5000):
            class FakeEl:
                def set_input_files(self, files): pass
            return FakeEl()

        def query_selector(self, sel):
            if "tweetTextarea_0" in sel:
                class FakeEditor:
                    def fill(self, txt): pass
                    def click(self): pass
                return FakeEditor()
            if "fileInput" in sel:
                class FakeEl:
                    def set_input_files(self, files): pass
                return FakeEl()
            if "编辑媒体" in sel or "Edit media" in sel or "编辑" in sel:
                class FakeEditBtn:
                    def click(self, **kw):
                        curr_dialog[0] = MockDialog(is_disc=False)
                    def evaluate(self, script):
                        curr_dialog[0] = MockDialog(is_disc=False)
                return FakeEditBtn()
            if "attachments" in sel:
                class FakeAtt:
                    def inner_text(self): return "处理完成"
                    def text_content(self): return "处理完成"
                    def evaluate(self, script): pass
                return FakeAtt()
            if "video" in sel or "videoPlayer" in sel:
                return MagicMock()
            if "toast" in sel:
                return MagicMock(text_content=lambda: "Your post was sent")
            return None

        def query_selector_all(self, sel):
            if "contentDisclosureButton" in sel or "披露" in sel:
                class FakeDiscBtn:
                    def click(self, **kw):
                        curr_dialog[0] = MockDialog(is_disc=True)
                    def evaluate(self, script):
                        curr_dialog[0] = MockDialog(is_disc=True)
                return [FakeDiscBtn()]
            if "role='dialog'" in sel:
                return [curr_dialog[0]] if curr_dialog[0] else []
            if "tweetButton" in sel or "Post" in sel:
                class FakeTweetBtn:
                    def is_visible(self): return True
                    def is_enabled(self): return True
                    def get_attribute(self, attr): return "false" if attr == "aria-disabled" else None
                    def click(self, **kw): pass
                return [FakeTweetBtn()]
            return []

    fake_page = FakeCompliancePage()
    monkeypatch.setattr(disp, "create_persistent_stealth_context", lambda p, d, headless=True: MagicMock(pages=[fake_page], close=lambda: None))
    monkeypatch.setattr(disp, "create_stealth_browser", lambda p, headless=True: MagicMock())
    monkeypatch.setattr(disp, "apply_stealth", lambda page: None)
    monkeypatch.setattr(disp, "safe_goto", lambda page, url, timeout=30000: None)
    monkeypatch.setattr(disp, "assert_field_value", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(disp, "assert_attached", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(disp, "assert_submitted", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(disp, "close_login_session", lambda *a, **kw: None)

    ok, msg, compliance = disp.publish({
        "title": "Video AI Compliance Test",
        "description": "Test desc",
        "tags": ["ai", "art"],
        "x_video": str(vid_path),
        "x_images": [str(vid_path)],
        "is_ai": True,
        "is_sensitive": True,
        "x_video_transcode_timeout": 5,
        "x_video_poll_interval": 0.05,
    })

    assert ok is True
    comp_map = {c["item"]: c for c in compliance}
    assert comp_map["ai_disclosure"]["verified"] is True
    assert comp_map["media_sensitive"]["verified"] is True


def test_x_dispatcher_ai_cascade_fallback_to_media_declaration(tmp_path, monkeypatch):
    """
    断言 X 披露级联兜底：
    - 当推文级披露弹窗未能打开/未响应 (ai_verified 未确认) 时
    - 若媒体编辑弹窗内成功勾选官方「AI 生成」
    - ai_disclosure 合规条目必须级联判定通过，且 detail 必须标注为 '通过（媒体级声明兜底）'
    """
    from dispatchers.x_twitter import XTwitterDispatcher

    vid_path = _generate_lavfi_test_video(tmp_path / "x_fb.mp4", duration=1.0, size="320x240", rate=10)
    disp = XTwitterDispatcher(tmp_path / "cookies")
    monkeypatch.setattr(disp, "get_cookies", lambda: [{"name": "auth_token", "value": "xxx", "domain": ".x.com", "path": "/"}])

    class MockCheckbox:
        def __init__(self, checked=False):
            self._checked = checked
        def is_checked(self): return self._checked
        def click(self, **kw): self._checked = True
        def evaluate(self, script): self._checked = True

    class MockMediaDialog:
        def __init__(self):
            self.nudity_cb = MockCheckbox(checked=False)
            self.ai_media_cb = MockCheckbox(checked=False)
            self.active_tab = 0

        def query_selector(self, sel):
            if "由 AI 生成" in sel or "Made with AI" in sel or "AI 生成" in sel:
                class FakeLabel:
                    def __init__(lbl_self, cb): lbl_self._cb = cb
                    def query_selector(lbl_self, sub_sel): return lbl_self._cb
                return FakeLabel(self.ai_media_cb)
            if "裸体" in sel or "裸露" in sel or "Nudity" in sel:
                nudity_cb_ref = self.nudity_cb
                class FakeNudityLabel:
                    def query_selector(lbl_self, sub_sel): return nudity_cb_ref
                return FakeNudityLabel()
            if "保存" in sel or "Save" in sel or "完成" in sel or "Done" in sel or "endEditingButton" in sel:
                class FakeBtn:
                    def click(self, **kw): pass
                    def evaluate(self, script): pass
                return FakeBtn()
            return None

        def query_selector_all(self, sel):
            if "[role='tab']" in sel:
                class FakeTab:
                    def __init__(tab_self, idx, text, label):
                        tab_self.idx = idx
                        tab_self.text = text
                        tab_self.label = label
                    def inner_text(tab_self): return tab_self.text
                    def get_attribute(tab_self, attr):
                        if attr == "aria-label": return tab_self.label
                        return None
                    def click(tab_self, **kw): self.active_tab = tab_self.idx
                    def evaluate(tab_self, script): self.active_tab = tab_self.idx
                return [FakeTab(0, "字幕", "字幕"), FakeTab(1, "内容警告", "内容警告")]
            return []

    curr_media_dialog = [None]

    class FakeFallbackPage:
        def __init__(self):
            self.url = "https://x.com/home"

        def wait_for_timeout(self, ms): pass
        def wait_for_selector(self, sel, timeout=5000):
            class FakeEl:
                def set_input_files(self, files): pass
            return FakeEl()

        def query_selector(self, sel):
            if "tweetTextarea_0" in sel:
                class FakeEditor:
                    def fill(self, txt): pass
                    def click(self): pass
                return FakeEditor()
            if "fileInput" in sel:
                class FakeEl:
                    def set_input_files(self, files): pass
                return FakeEl()
            if "编辑媒体" in sel or "Edit media" in sel or "编辑" in sel:
                class FakeEditBtn:
                    def scroll_into_view_if_needed(self, **kw): pass
                    def click(self, **kw):
                        curr_media_dialog[0] = MockMediaDialog()
                    def evaluate(self, script):
                        curr_media_dialog[0] = MockMediaDialog()
                return FakeEditBtn()
            if "attachments" in sel:
                class FakeAtt:
                    def inner_text(self): return "处理完成"
                    def text_content(self): return "处理完成"
                    def evaluate(self, script): pass
                return FakeAtt()
            if "video" in sel or "videoPlayer" in sel:
                return MagicMock()
            if "toast" in sel:
                return MagicMock(text_content=lambda: "Your post was sent")
            return None

        def query_selector_all(self, sel):
            # 披露按钮存在但点击后弹窗无法唤起 (模拟区域未上线/动画超时)
            if "contentDisclosureButton" in sel or "披露" in sel:
                class FakeDiscBtn:
                    def scroll_into_view_if_needed(self, **kw): pass
                    def click(self, **kw): pass
                    def evaluate(self, script): pass
                return [FakeDiscBtn()]
            if "role='dialog'" in sel:
                # 只有媒体编辑弹窗能唤起，披露弹窗返回空
                return [curr_media_dialog[0]] if curr_media_dialog[0] else []
            if "tweetButton" in sel or "Post" in sel:
                class FakeTweetBtn:
                    def is_visible(self): return True
                    def is_enabled(self): return True
                    def get_attribute(self, attr): return "false" if attr == "aria-disabled" else None
                    def click(self, **kw): pass
                return [FakeTweetBtn()]
            return []

    fake_page = FakeFallbackPage()
    monkeypatch.setattr(disp, "create_persistent_stealth_context", lambda p, d, headless=True: MagicMock(pages=[fake_page], close=lambda: None))
    monkeypatch.setattr(disp, "create_stealth_browser", lambda p, headless=True: MagicMock())
    monkeypatch.setattr(disp, "apply_stealth", lambda page: None)
    monkeypatch.setattr(disp, "safe_goto", lambda page, url, timeout=30000: None)
    monkeypatch.setattr(disp, "assert_field_value", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(disp, "assert_attached", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(disp, "assert_submitted", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(disp, "close_login_session", lambda *a, **kw: None)

    ok, msg, compliance = disp.publish({
        "title": "Fallback AI Compliance Test",
        "description": "Test desc",
        "tags": ["ai", "art"],
        "x_video": str(vid_path),
        "x_images": [str(vid_path)],
        "is_ai": True,
        "is_sensitive": True,
        "x_video_transcode_timeout": 5,
        "x_video_poll_interval": 0.05,
    })

    assert ok is True
    comp_map = {c["item"]: c for c in compliance}
    assert comp_map["ai_disclosure"]["verified"] is True
    assert comp_map["ai_disclosure"]["detail"] == "通过（媒体级声明兜底）"
    assert comp_map["media_sensitive"]["verified"] is True


def test_single_gif_vs_multi_image_and_ui_badge(tmp_path, qapp):
    """
    断言规则 1, 3, 5, 6：
    - 默认配置包含 comfyui_output_dir
    - 导入 GIF 自动配对后存为 x_video_path，缩略图卡片徽标显示「🎬 自动配对」；
      手动改绑后切换为「🎬 手动指定」；解绑后隐藏徽标
    - 自动分组审核弹窗显示配对到的视频文件名并可当场改绑
    """
    from app import MainWindow, ThumbnailCardWidget
    from auto_group_dialog import AutoGroupReviewDialog

    assert "comfyui_output_dir" in DEFAULT_CONFIG

    out_dir = tmp_path / "comfy_out"
    gif_file = _create_dummy_gif(out_dir / "dance_00001.gif")
    vid_auto = _create_dummy_video_file(out_dir / "dance_00001.mp4")
    vid_manual = _create_dummy_video_file(out_dir / "dance_custom.mp4")

    win = MainWindow()
    win.config["comfyui_output_dir"] = str(out_dir)
    win.image_paths = [str(gif_file)]
    win.auto_pair_imported_gifs([str(gif_file)])
    win._refresh_thumbnail_gallery()

    assert Path(win.get_x_video_path(str(gif_file))) == vid_auto.resolve()
    assert win.get_x_video_source(str(gif_file)) == "auto"

    card = ThumbnailCardWidget(win, 0, str(gif_file))
    assert card.x_video_path == str(vid_auto.resolve())
    assert card.video_badge_lbl.text() == "🎬 自动配对"
    assert not card.video_badge_lbl.isHidden()

    # 手动改绑 -> 徽标变为「🎬 手动指定」
    card.prompt_bind_video(chosen_path=str(vid_manual))
    card_manual = ThumbnailCardWidget(win, 0, str(gif_file))
    assert card_manual.x_video_path == str(vid_manual.resolve())
    assert card_manual.video_badge_lbl.text() == "🎬 手动指定"

    # 解绑 -> x_video_path 清空，徽标隐藏
    card_manual.unbind_video()
    card_unbound = ThumbnailCardWidget(win, 0, str(gif_file))
    assert card_unbound.x_video_path is None
    assert card_unbound.video_badge_lbl.isHidden()

    # 规则 5：自动分组审核弹窗显示配对视频文件名并支持当场改绑
    dlg = AutoGroupReviewDialog(
        {"dance": [str(gif_file)]},
        tmp_path / "data",
        parent=win,
        comfyui_output_dir=str(out_dir),
    )
    assert Path(dlg.video_pairs[str(gif_file)]["video_path"]) == vid_auto.resolve()
    grp_item = dlg.tree.topLevelItem(0)
    assert "dance_00001.mp4" in grp_item.text(1)

    dlg.rebind_gif_video(str(gif_file), str(vid_manual))
    assert Path(dlg.video_pairs[str(gif_file)]["video_path"]) == vid_manual.resolve()
    assert dlg.video_pairs[str(gif_file)]["source"] == "manual"
    assert "dance_custom.mp4" in dlg.tree.topLevelItem(0).text(1)


def test_single_gif_vs_multi_image_publish_payload(tmp_path, qapp, monkeypatch):
    """
    断言规则 6：
    - 仅单 GIF 帖对 X 生效（X 的发布副本为处理后的 .mp4 视频）
    - 多图帖（如 2 张图）忽略配对视频，X 仍走图片/GIF 路径
    """
    from app import MainWindow

    gif_1 = _create_dummy_gif(tmp_path / "single_00001.gif")
    gif_2 = _create_dummy_gif(tmp_path / "second_00002.gif")
    real_vid = _generate_lavfi_test_video(tmp_path / "single_00001.mp4", duration=1.0, size="160x120", rate=10)

    win = MainWindow()
    win.title_input.setText("Single vs Multi Test")
    win.desc_input.setPlainText("Testing rule 6")
    for name, ctrl in win.platform_controls.items():
        ctrl["checkbox"].setChecked(name == "x_twitter")

    captured_payloads = []

    class DummyPublishThread:
        def __init__(self, selected_dispatchers, payload):
            captured_payloads.append(payload)
            self.progress_signal = MagicMock()
            self.completed_signal = MagicMock()

        def start(self):
            pass

        def isRunning(self):
            return False

    monkeypatch.setattr("app.PublishThread", DummyPublishThread)

    # 1. 单 GIF 帖 + 已配对视频 -> X 产物应为处理后的 .mp4
    win.image_paths = [str(gif_1)]
    win.x_video_paths = {str(gif_1): str(real_vid)}
    win.x_video_sources = {str(gif_1): "auto"}
    win._start_publishing()

    assert len(captured_payloads) == 1
    p_single = captured_payloads[0]
    assert p_single["x_video"] is not None
    assert p_single["x_video"].endswith(".mp4")
    assert Path(p_single["x_video"]).exists()
    assert p_single["x_images"] == [p_single["x_video"]]

    # 2. 多图帖 (2 张 GIF) + 仍有配对记录 -> 忽略配对视频，X 走 GIF 分支
    win.image_paths = [str(gif_1), str(gif_2)]
    win._start_publishing()

    assert len(captured_payloads) == 2
    p_multi = captured_payloads[1]
    assert p_multi["x_video"] is None
    assert all(str(x).endswith(".gif") for x in p_multi["x_images"])

