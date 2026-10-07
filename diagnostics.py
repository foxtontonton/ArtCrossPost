# -*- coding: utf-8 -*-
"""
ArtCrossPost - 诊断与自检系统 (Diagnostics Engine)
基于 Muse 架构师自查/诊断需求规范:
1. 环境自检 (Python 依赖、Edge 浏览器、ONNX 权重、磁盘与读写权限)
2. 网络连通性 (主流平台域名可达性与延迟)
3. 登录态有效性深度探针 (轻量级 Cookie/Session 存活检验)
4. 选择器健康检查 (只读 DOM，不触碰任何提交动作)
5. 结构化 Markdown 报告生成与导出
"""
import os
import sys
import time
import shutil
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional

try:
    from logger import get_logger
except ImportError:
    from .logger import get_logger

logger = get_logger("Diagnostics")


class DiagnosticsEngine:
    """系统诊断与自检引擎"""

    def __init__(self, data_dir: Path, dispatchers: list = None):
        self.data_dir = Path(data_dir)
        self.dispatchers = dispatchers or []
        self.report_file = self.data_dir / "diagnostic_report.md"

    def check_environment(self) -> List[Dict[str, Any]]:
        """3.1 环境自检"""
        results = []

        # 1. Python 依赖完整性
        deps = [
            ("PyQt5", "PyQt5 图形界面框架"),
            ("playwright", "Playwright 浏览器自动化引擎"),
            ("PIL", "Pillow 图像处理库"),
            ("cv2", "OpenCV 视觉处理库"),
            ("numpy", "NumPy 数值矩阵计算库"),
        ]
        for mod_name, desc in deps:
            try:
                __import__(mod_name)
                results.append({
                    "platform": "env",
                    "category": "env",
                    "item": f"dep_{mod_name}",
                    "status": "pass",
                    "detail": f"模块 {mod_name} ({desc}) 导入正常",
                    "suggestion": ""
                })
            except ImportError as e:
                results.append({
                    "platform": "env",
                    "category": "env",
                    "item": f"dep_{mod_name}",
                    "status": "fail",
                    "detail": f"模块 {mod_name} 缺失: {e}",
                    "suggestion": f"执行 pip install {mod_name} 进行安装"
                })

        # 2. onnxruntime (可选但推荐用于 AI 识别)
        try:
            import onnxruntime
            results.append({
                "platform": "env",
                "category": "env",
                "item": "dep_onnxruntime",
                "status": "pass",
                "detail": f"ONNX Runtime 就绪 (v{onnxruntime.__version__})",
                "suggestion": ""
            })
        except Exception as e:
            results.append({
                "platform": "env",
                "category": "env",
                "item": "dep_onnxruntime",
                "status": "unknown",
                "detail": f"ONNX Runtime 暂未就绪或加载报错: {e} (将降级使用启发式打码算法)",
                "suggestion": "可安装 Visual C++ 2015-2022 运行库以启用 AI 智能识别"
            })

        # 3. 磁盘剩余空间检查
        try:
            total, used, free = shutil.disk_usage(self.data_dir)
            free_gb = free / (1024 ** 3)
            if free_gb > 1.0:
                results.append({
                    "platform": "env",
                    "category": "env",
                    "item": "disk_space",
                    "status": "pass",
                    "detail": f"当前磁盘可用空间充足 ({free_gb:.2f} GB > 1.0 GB)",
                    "suggestion": ""
                })
            else:
                results.append({
                    "platform": "env",
                    "category": "env",
                    "item": "disk_space",
                    "status": "fail",
                    "detail": f"磁盘可用空间不足 ({free_gb:.2f} GB <= 1.0 GB)",
                    "suggestion": "请清理磁盘空间，以防动图或图片导出时写入失败"
                })
        except Exception as e:
            results.append({
                "platform": "env",
                "category": "env",
                "item": "disk_space",
                "status": "unknown",
                "detail": f"读取磁盘空间异常: {e}",
                "suggestion": ""
            })

        # 4. data 目录可写性检查
        test_file = self.data_dir / ".write_test.tmp"
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("ok")
            if test_file.exists():
                test_file.unlink()
            results.append({
                "platform": "env",
                "category": "env",
                "item": "data_writable",
                "status": "pass",
                "detail": f"数据目录 {self.data_dir} 可读写",
                "suggestion": ""
            })
        except Exception as e:
            results.append({
                "platform": "env",
                "category": "env",
                "item": "data_writable",
                "status": "fail",
                "detail": f"数据目录不可写: {e}",
                "suggestion": f"请检查目录 {self.data_dir} 写入权限"
            })

        # 5. Playwright 与 Edge Channel 可启动性探针 (只启动并立即关闭)
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                stealth_args = [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-first-run"
                ]
                browser = p.chromium.launch(
                    channel="msedge",
                    headless=True,
                    args=stealth_args
                )
                browser.close()
            results.append({
                "platform": "env",
                "category": "env",
                "item": "browser_edge",
                "status": "pass",
                "detail": "系统 Edge 浏览器通过 Playwright 启动验证正常",
                "suggestion": ""
            })
        except Exception as e:
            results.append({
                "platform": "env",
                "category": "env",
                "item": "browser_edge",
                "status": "fail",
                "detail": f"启动 Edge 浏览器失败: {e}",
                "suggestion": "请确认已安装 Microsoft Edge 浏览器，或执行 playwright install chromium"
            })

        return results

    def check_network(self) -> List[Dict[str, Any]]:
        """3.4 网络连通性 (针对 6 大平台根域名)"""
        import urllib.request
        results = []
        targets = [
            ("x_twitter", "https://x.com", "X (Twitter)"),
            ("deviantart", "https://www.deviantart.com", "DeviantArt"),
            ("patreon", "https://www.patreon.com", "Patreon"),
            ("kofi", "https://ko-fi.com", "Ko-fi")
        ]

        for p_name, url, d_name in targets:
            start_t = time.time()
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    elapsed = int((time.time() - start_t) * 1000)
                    results.append({
                        "platform": p_name,
                        "category": "network",
                        "item": "network_connectivity",
                        "status": "pass",
                        "detail": f"{d_name} ({url}) 连通正常 (延迟 {elapsed}ms, HTTP {resp.status})",
                        "suggestion": ""
                    })
            except Exception as e:
                elapsed = int((time.time() - start_t) * 1000)
                results.append({
                    "platform": p_name,
                    "category": "network",
                    "item": "network_connectivity",
                    "status": "unknown" if "403" in str(e) or "Cloudflare" in str(e) else "fail",
                    "detail": f"{d_name} ({url}) 连接异常: {e} (耗时 {elapsed}ms)",
                    "suggestion": "海外站点可能需要配置系统代理或加速器"
                })

        return results

    def run_full_diagnostics(self, progress_callback: Optional[Callable[[int, str], None]] = None) -> Dict[str, Any]:
        """
        执行完整自检流程 (串行执行，耗时控制在 3 分钟内)
        返回包含总览与明细的结构化报告字典
        """
        all_items: List[Dict[str, Any]] = []

        # Step 1: 环境自检 (0% ~ 20%)
        if progress_callback: progress_callback(5, "正在进行运行环境与依赖完整性自检...")
        env_items = self.check_environment()
        all_items.extend(env_items)

        # Step 2: 网络连通性 (20% ~ 40%)
        if progress_callback: progress_callback(25, "正在检测海外各平台网络连通性与延迟...")
        net_items = self.check_network()
        all_items.extend(net_items)

        # Step 3: 各平台登录态有效性探针 (40% ~ 65%)
        login_statuses: Dict[str, str] = {}
        total_disp = len(self.dispatchers)
        for idx, disp in enumerate(self.dispatchers):
            step = 40 + int((idx / max(1, total_disp)) * 25)
            if progress_callback: progress_callback(step, f"正在探测 [{disp.display_name}] 登录态有效性...")

            try:
                probe_res = disp.probe_login()
                all_items.append(probe_res)
                login_statuses[disp.name] = probe_res.get("status", "fail")
            except Exception as e:
                logger.warning(f"[{disp.display_name}] 登录探针异常: {e}")
                login_statuses[disp.name] = "fail"
                all_items.append({
                    "platform": disp.name,
                    "category": "login",
                    "item": "login_probe",
                    "status": "fail",
                    "detail": f"探针执行异常: {e}",
                    "suggestion": "点击卡片登录按钮尝试重新建立会话"
                })

        # Step 4: 选择器健康检查 (65% ~ 95%) - 只读 DOM，严禁点击提交
        for idx, disp in enumerate(self.dispatchers):
            step = 65 + int((idx / max(1, total_disp)) * 30)
            if progress_callback: progress_callback(step, f"正在体检 [{disp.display_name}] 投稿页选择器健康度...")

            # 若登录探针未通过，跳过投稿页浏览器体检，避免无意义的重定向与超时等待
            if login_statuses.get(disp.name) != "pass":
                all_items.append({
                    "platform": disp.name,
                    "category": "selector",
                    "item": "selector_health_probe",
                    "status": "unknown",
                    "detail": f"{disp.display_name} 未登录或会话失效，跳过投稿页选择器体检",
                    "suggestion": "请先完成该平台的浏览器登录后再运行选择器体检"
                })
                continue

            try:
                # 仅在具备有效 Cookie 且探针通过时检查投稿页选择器
                if hasattr(disp, "check_selectors_health"):
                    sel_items = disp.check_selectors_health()
                    all_items.extend(sel_items)
            except Exception as e:
                logger.warning(f"[{disp.display_name}] 选择器健康体检异常: {e}")
                all_items.append({
                    "platform": disp.name,
                    "category": "selector",
                    "item": "selector_health_probe",
                    "status": "unknown",
                    "detail": f"选择器体检中断: {e}",
                    "suggestion": "可稍后重试或检查该平台是否要求人机验证"
                })

        if progress_callback: progress_callback(98, "正在生成诊断报告汇总...")

        # 统计数据
        pass_count = sum(1 for it in all_items if it.get("status") == "pass")
        fail_count = sum(1 for it in all_items if it.get("status") == "fail")
        warn_count = sum(1 for it in all_items if it.get("status") == "unknown")
        total_count = len(all_items)

        report = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "total": total_count,
                "pass": pass_count,
                "fail": fail_count,
                "unknown": warn_count,
                "health_score": int((pass_count / max(1, total_count)) * 100)
            },
            "items": all_items
        }

        # 导出为 Markdown 文件
        self._export_to_markdown(report)

        if progress_callback: progress_callback(100, f"诊断完成: {pass_count} 项通过, {warn_count} 项可疑, {fail_count} 项失败")
        return report

    def _export_to_markdown(self, report: Dict[str, Any]):
        """生成符合需求规范的 Markdown 诊断报告"""
        lines = [
            "# ArtCrossPost 系统诊断与健康自检报告",
            f"> 生成时间: {report['timestamp']}",
            "",
            "## 1. 诊断总览",
            f"- **健康评分**: `{report['summary']['health_score']}%`",
            f"- **测试总项**: `{report['summary']['total']}`",
            f"- **✅ 正常项 (Pass)**: `{report['summary']['pass']}`",
            f"- **⚠️ 待确认/警告 (Unknown)**: `{report['summary']['unknown']}`",
            f"- **❌ 异常/失败项 (Fail)**: `{report['summary']['fail']}`",
            "",
            "---",
            "",
            "## 2. 诊断明细表",
            "| 平台 | 模块分类 | 检查项 | 状态 | 详细诊断信息 | 建议操作 |",
            "|---|---|---|---|---|---|"
        ]

        status_emoji = {
            "pass": "✅ 通过",
            "fail": "❌ 失败",
            "unknown": "⚠️ 待核验"
        }

        for it in report["items"]:
            plat = it.get("platform", "global")
            cat = it.get("category", "")
            item_name = it.get("item", "")
            st = it.get("status", "unknown")
            st_text = status_emoji.get(st, st)
            detail = str(it.get("detail", "")).replace("|", "\\|").replace("\n", " ")
            sugg = str(it.get("suggestion", "")).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| `{plat}` | `{cat}` | `{item_name}` | {st_text} | {detail} | {sugg} |")

        lines.extend([
            "",
            "---",
            "",
            "## 3. 失败与异常项专项建议",
        ])

        failed_items = [it for it in report["items"] if it.get("status") in ["fail", "unknown"]]
        if not failed_items:
            lines.append("🎉 恭喜！全系统未发现任何异常或失效项，所有发布通道运行就绪。")
        else:
            for it in failed_items:
                plat = it.get("platform", "global")
                lines.append(f"- **[{plat}] {it.get('item', '')}** ({status_emoji.get(it.get('status'))}):")
                lines.append(f"  - 原因: {it.get('detail')}")
                if it.get("suggestion"):
                    lines.append(f"  - 建议: `{it.get('suggestion')}`")

        lines.append("")
        try:
            with open(self.report_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            logger.info(f"[Diagnostics] 诊断报告已导出至: {self.report_file}")
        except Exception as e:
            logger.error(f"[Diagnostics] 写入报告文件异常: {e}")
