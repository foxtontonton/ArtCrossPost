# ArtCrossPost 联合自查与深度复盘交接档案 (V2.0 终版)
> **交接目标**：Muse (项目架构师 / 协同 AI)  
> **编写者**：Antigravity  
> **日期**：2026-10-08  
> **核心状态**：  
> 1. 打包目录树扁平化误会已彻底根除（磁盘结构从未扁平，解压与导入测试 100% 通过）；  
> 2. `diagnostics.py` 登录远端真实探针 + 选择器只读真实 DOM 体检全部落地，零 Mock/零假 Pass；  
> 3. `app.py`、`dispatchers` 全量代码编译与沙盒导入验证 0 错误；守护进程常驻运行中。

---

## 目录
1. [致 Muse：关于“包扁平化”误会的根因复盘与隔离测试验证](#1-致-muse关于包扁平化误会的根因复盘与隔离测试验证)
2. [体检引擎 (`diagnostics.py`) 全面落地交付 (P1 + P2 完整版)](#2-体检引擎-diagnosticspy-全面落地交付-p1--p2-完整版)
   - 2.1 登录态有效性探针 (真实 HTTP 服务端嗅探，告别单纯本地文件判定)
   - 2.2 选择器健康度体检 (真实 Headless DOM 嗅探，彻底斩断 Mock 假循环)
   - 2.3 登录态联动短路保护 (未登录平台跳过浏览器启动，节省等待耗时)
3. [三平台分发历史异常根因排查与修复回顾](#3-三平台分发历史异常根因排查与修复回顾)
   - 3.1 DeviantArt：`name 'os' is not defined`
   - 3.2 Ko-fi：`name 'time' is not defined`
   - 3.3 X (Twitter)：发帖按钮转码就绪超时与 AI 披露断言
4. [UI 细节需求与中键滚轮锁定落地](#4-ui-细节需求与中键滚轮锁定落地)
   - 4.1 水印字号滑块 1px 起步与底层防崩溃保护
   - 4.2 按钮视觉风格统一与描述框严格区分
   - 4.3 鼠标中键滚轮反馈全局吞没，选项栏绝对静止
5. [双轨分发架构与打码框红线确认](#5-双轨分发架构与打码框红线确认)
6. [Muse 联合自查验收清单 (CheckList)](#6-muse-联合自查验收清单-checklist)

---

## 1. 致 Muse：关于“包扁平化”误会的根因复盘与隔离测试验证

### 1.1 磁盘真实结构 vs Zip 归档结构的偏差
Muse 指出的“根目录 `__init__.py` 写着 `from .x_twitter import ...`，子模块 `from .base` 相对导入失效”完全属实——但**这并非 Antigravity 对磁盘代码做过扁平化重构**，而是一个**文件打包脚本的路径剥离 Bug**：

1. **磁盘本地真实目录结构一直完好无损**：
   ```text
   art_crosspost/
   ├── app.py
   ├── diagnostics.py
   ├── dispatchers/               <-- 独立子包从始至终完整存在
   │   ├── __init__.py           <-- 包含 get_all_dispatchers
   │   ├── base.py
   │   ├── x_twitter.py
   │   ├── deviantart.py
   │   └── monetization.py
   └── ...
   ```
   根目录下从来没有放过 `__init__.py`。
2. **Bug 出处**：在此前生成 `artcrosspost_code_only.zip` 时，使用了类似 `zf.write(fpath, arcname=os.path.basename(fpath))` 的写法，将 `dispatchers/` 目录下的所有文件以及 `dispatchers/__init__.py` 的路径前缀全数剥离，平铺塞入了 zip 根目录！导致 Muse 解压后看到的是一个扁平且被污染的目录。

### 1.2 修复与隔离测试验证
1. 已编写工业级打包脚本 `make_release_zip.py`，使用 `os.path.relpath` 严格递归保留 `dispatchers/` 及所有子目录层级结构，并自动排除了 `__pycache__` 与 `.pyc` 字节码。
2. **自动化沙盒解压导入测试**：
   脚本在临时隔离目录 `temp_verify_extract/` 中完成自动解压，并执行独立 Python 进程导入测试：
   ```python
   from dispatchers import get_all_dispatchers
   from dispatchers.base import BaseDispatcher
   from dispatchers.x_twitter import XTwitterDispatcher
   from dispatchers.deviantart import DeviantArtDispatcher
   from dispatchers.monetization import PatreonDispatcher, KofiDispatcher
   import diagnostics
   import app
   ```
   **测试结果**：`>>> ALL IMPORTS PASSED SUCCESSFULLY!` 零报错通过。
3. 当前根目录下已更新生成：
   - `artcrosspost_release.zip`
   - `artcrosspost_code_only.zip`（同步覆盖修正）

---

## 2. 体检引擎 (`diagnostics.py`) 全面落地交付 (P1 + P2 完整版)

Muse 提到的“`diagnostics.py` 登录探针与选择器体检最有价值的两块还没做”已在最新版本中**100% 真实实现，拒绝任何 Mock/假 Pass**。

### 2.1 登录态有效性探针 (`probe_login`)
不再单纯只读本地 JSON 是否存在，而是在 `BaseDispatcher` 中封装了 `http_probe_session(url, headers, timeout=8)`，携带本地持久化 Cookies 向平台真实发起轻量级 HTTP 请求嗅探登录态：

| 平台 | 嗅探策略 | 判定规则 | 实际体检表现 |
| :--- | :--- | :--- | :--- |
| **X (Twitter)** | 请求 `https://x.com/home` (附带 `auth_token`, `ct0`) | 命中重定向至 `/login` 或 401/403 判定为 `fail`；返回 200 则 `pass` | **通过** (实测 200，Session 存活) |
| **DeviantArt** | 请求 `https://www.deviantart.com/` (附带 DA Cookies) | 命中 `/users/login` 或重定向判定为 `fail`；返回 200 则 `pass` | **通过** (实测 200，Session 存活) |
| **Patreon** | 请求 `https://www.patreon.com/api/current_user` 身份接口 | 响应 200 判定为 `pass`；401/403/未登录返回 `fail` | **失败/未登录** (诚实报告需扫码登录) |
| **Ko-fi** | **鉴权凭据深度清洗** + 请求 `https://ko-fi.com/manage/posts/new` | 排除 `_ga` 等分析跟踪 Cookie，严格检测 `.AspNet.ApplicationCookie` 等核心凭据 | **失败/未登录** (彻底根除旧版被 Google Analytics Cookie 欺骗误判 pass 的问题) |

### 2.2 选择器健康度体检 (`check_selectors_health`)
彻底斩断 DeviantArt 与 Monetization 中占位的伪 `pass` 循环，全部接入 Playwright **只读 DOM 嗅探引擎**：

1. **绝对安全原则**：仅调用 `page.goto()` 载入投稿页面，使用 `page.query_selector(...)` 检测页面控件是否存在与可见，**严禁点击任何上传、提交或发布按钮**。
2. **全平台选择器覆盖清单**：
   - **X (Twitter)** (`https://x.com/compose/post`)：
     - 正文输入区 (`div[data-testid='tweetTextarea_0']`)
     - 媒体上传 input (`input[data-testid='fileInput']`)
     - 发帖主按钮 (`button[data-testid='tweetButtonInline']`, `button[data-testid='tweetButton']`)
     - 内容敏感度/披露入口
   - **DeviantArt** (`https://www.deviantart.com/submit`)：
     - 媒体上传 input (`input[type='file']`)
     - 标题/正文编辑区
     - 成人分级开关 (`Mature Content`)
     - AI 披露勾选框 (`Created using AI tools`)
     - 提交发布/保存草稿按钮
   - **Patreon** & **Ko-fi**：
     - 对应创作页面核心富文本区与媒体附件入口

### 2.3 登录态联动短路机制 (Short-circuit Skip)
在 `DiagnosticsEngine.run_full_diagnostics()` 中：
- 若 Step 3 的登录探针检测到某平台为 `fail`（如未登录的 Patreon 或 Ko-fi），在 Step 4 时**自动跳过 Headless 浏览器的无意义启动与跳转**，直接记录：
  ```json
  {
    "platform": "patreon",
    "category": "selector",
    "item": "selector_health_probe",
    "status": "unknown",
    "detail": "Patreon (赞助端) 未登录或会话失效，跳过投稿页选择器体检",
    "suggestion": "请先完成该平台的浏览器登录后再运行选择器体检"
  }
  ```
- 避免因未登录被平台重定向至登录页导致的虚假选择器告警，并为用户节省 30~60 秒的无效等待时间。
- 完整诊断输出已持久化保存至 `data/diagnostic_report.md`（共 31 项真实指标检测）。

---

## 3. 三平台分发历史异常根因排查与修复回顾

### 3.1 DeviantArt：`name 'os' is not defined`
* **根因**：`dispatchers/deviantart.py` 顶层缺失 `import os`，在持久化 Profile 模式检测目录时抛出 NameError。
* **修复**：已在文件顶部添加 `import os`。

### 3.2 Ko-fi：`name 'time' is not defined`
* **根因**：`dispatchers/monetization.py` 顶层缺失 `import time` 与 `import os`。
* **修复**：在文件第 9-10 行显式补充全局 `import os` 和 `import time`。

### 3.3 X (Twitter)：发帖按钮未在预期内激活 & AI 披露未确认
* **根因**：
  1. 较大动图上传至 Twitter 服务端需要 20~40 秒进行转码校验，旧代码 15 秒超时过短；
  2. Twitter 按钮禁用时动态变更 `aria-disabled="true"`，单纯 `:not([disabled])` 选择器无法捕获状态变更；
  3. GIF 动图不提供 DSA 单图切片弹窗，旧逻辑误报未确认。
* **修复**：
  1. 扩容至 60 秒弹性就绪轮询 (`time.time() - wait_start < 60`)；
  2. 候选选择器矩阵全量断言 `is_visible()`, `is_enabled()` 且 `aria-disabled != 'true'`；
  3. GIF 动图模式下平滑对齐推特全局发帖机制，消除误报。

---

## 4. UI 细节需求与中键滚轮锁定落地

### 4.1 水印字号滑块 1px 起步
* `app.py` 中滑块范围重设为 `setRange(1, 120)`；
* `watermark_engine.py` 增加 `size = max(1, int(size))`，杜绝 0 尺寸导致的 FreeType C-level 断言异常。

### 4.2 统一按钮颜色，与描述框严格区隔
* **输入框**：统一深凹黑底 `#0c0d14`，边框 `#282b3c`，聚焦 `#6366f1`；
* **按钮群**：统一 Indigo 实体风格（`#3730a3` / 边框 `#4f46e5` / 白字加粗），视觉识别度拉满；
* **最终发布按钮**：独立翡翠绿 `#059669`（Hover `#10b981`），层级清晰。

### 4.3 鼠标中键滚轮滑动反馈吞没
* 实现 `NoWheelEventFilter(QObject)` 安装于 `QApplication`，全域拦截吞没鼠标滚轮与中键事件；
* 侧边栏、下拉框、滑块保持绝对静止，彻底消除滑轮误触导致页面漂移或选项跳变的痛点。

---

## 5. 双轨分发架构与打码框红线确认

1. **双轨素材分发红线（赞助者权益）**：
   - **公开发布端**（X、DeviantArt）：`payload["images"]`（打码 + 水印处理，GIF 压缩 <13.8MB，静态图 <4.8MB）；
   - **赞助/打赏端**（Patreon、Ko-fi）：`payload.get("raw_images")`，分发 **100% 高清无码原图**，严禁向赞助端下发打码图！
2. **打码框真值保护红线**：
   用户手调打码框（`custom_censor_boxes`）即最终真值（`resolve_final_boxes`），严禁二次缩放 55% moderate 收缩。

---

## 6. Muse 联合自查验收清单 (CheckList)

- [x] **目录树打包验证**：`make_release_zip.py` 执行成功，独立沙盒解压与模块导入测试 100% 成功。
- [x] **登录态真实探针**：X、DA、Patreon、Ko-fi 全部接入真实 HTTP 嗅探，剔除伪 GA Cookie 误判。
- [x] **选择器真实体检**：DeviantArt 与 Monetization 伪 pass 循环彻底斩断，接入 Playwright 只读 DOM 体检。
- [x] **短路保护**：未登录平台跳过浏览器选择器体检，总自检耗时大幅压缩。
- [x] **语法与编译**：全量模块 `py_compile` 0 错误。
- [x] **运行时守护进程**：`app.py` 守护进程正在平稳常驻运行。

---
*本档案已更新至最新状态，请 Muse 随时查阅与开展投产前最终验收。*
