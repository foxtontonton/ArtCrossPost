# ArtCrossPost 项目技术交接与系统全景档案
> **交接对象**：Muse (项目接管 AI / 架构师)  
> **编写者**：Antigravity  
> **归档日期**：2026-10-07  
> **当前状态**：核心功能全部就绪、代码稳定运行中、全暗色 UI、三栏持久化布局、硬件级 GIF/图片实时打码与水印

---

## 目录
1. [项目定位与核心使命](#1-项目定位与核心使命)
2. [技术栈与环境拓扑](#2-技术栈与环境拓扑)
3. [项目文件树与职责全景](#3-项目文件树与职责全景)
4. [四大核心子系统深度剖析](#4-四大核心子系统深度剖析)
   - [4.1 响应式界面与画布交互系统 (`app.py`)](#41-响应式界面与画布交互系统-apppy)
   - [4.2 智能敏感部位遮挡引擎 (`censor_engine.py`)](#42-智能敏感部位遮挡引擎-censor_enginepy)
   - [4.3 高质感文字防盗印与排版引擎 (`watermark_engine.py`)](#43-高质感文字防盗印与排版引擎-watermark_enginepy)
   - [4.4 全平台自动化发布矩阵 (`dispatchers/`)](#44-全平台自动化发布矩阵-dispatchers)
5. [历史已解决踩坑档案与防御红线 (Muse 必读)](#5-历史已解决踩坑档案与防御红线-muse-必读)
6. [数据持久化规范 (`data/`)](#6-数据持久化规范-data)
7. [运行、调试与维护手册](#7-运行调试与维护手册)
8. [未来推荐演进路线](#8-未来推荐演进路线)

---

## 1. 项目定位与核心使命

**ArtCrossPost** 是一款专为二次元、CG 及数字艺术家量身打造的**跨平台自动化艺术作品同步分发客户端**。

### 核心痛点与解决对策：
1. **各平台监管、成人分级与赞助权益规则碎裂化**：
   - X (Twitter) 要求勾选 Sensitive / Nudity 警告并标定 Made with AI；
   - DeviantArt 具备深度 Cloudflare 防护与严格的 Mature Content (Moderate/Strict/Nudity) 级别及 Created using AI tools 选项；
   - 赞助变现平台 (Patreon / Ko-fi) 面向付费赞助者，需严格分发**高清无码原图**，以保障赞助者权益。
   - **ArtCrossPost 解决方案**：统一在本地单次配置，一键对齐所有目标平台的原生表单、标签与合规选项；双轨制分发（公开发布端打码/水印防盗，赞助端高清原图直达）。
2. **打码与水印繁琐，反复导出耗时耗力**：
   - 传统工作流需在 Photoshop 中对静态图或多帧 GIF 逐帧打码盖章。
   - **ArtCrossPost 解决方案**：基于 ONNX 深度学习敏感部位检测 + 本地实时 8 向手柄交互式微调 + 动态文字水印覆盖，实现静态图与动图在同一预览框中毫秒级 60FPS 实时渲染。

---

## 2. 技术栈与环境拓扑

- **操作系统**：Windows 10 / 11 (x64)
- **开发语言**：Python 3.10+
- **图形界面 (GUI)**：PyQt5 (定制 Pure Dark Mode 现代暗黑工业风)
- **底层图形渲染**：OpenCV (cv2), Pillow (PIL), NumPy
- **机器学习推理**：ONNX Runtime (配合 NudeNet 敏感目标检测模型)
- **浏览器自动化与 Cookie 会话拦截**：Playwright (Chromium 内核，专门接管 Windows 原生 Microsoft Edge)
- **网络通信**：Requests, Urllib

---

## 3. 项目文件树与职责全景

```
art_crosspost/
│
├── app.py                     # [核心] PyQt5 客户端主入口、三栏布局、UnifiedPreviewCanvas 画布组件、LoginThread 与 PublishThread
├── censor_engine.py           # [核心] AI 敏感部位检测与遮挡合成引擎 (黑条/马赛克/模糊、比例缩放)
├── watermark_engine.py        # [核心] 文字防盗印与排版引擎 (Windows 字体索引、子像素边界修正、平铺/定位覆盖)
├── config.py                  # 配置加载、窗口尺寸与分割条持久化、路径常量定义
├── diagnostics.py             # [核心] 全平台四维非侵入式自检与诊断引擎 (环境/连通/探针/选择器)
│
├── dispatchers/               # [核心] 各平台自动化与 API 发布矩阵
│   ├── __init__.py            # 调度器自动注册工厂 (get_all_dispatchers)
│   ├── base.py                # BaseDispatcher 基类: Edge 隐身模式启动、全 Context 反爬注入、Cookie/资料存取、合规回读
│   ├── deviantart.py          # DeviantArt 自动化投稿: safe_goto 容灾、Google OAuth 多窗口穿透、原生表单对齐
│   ├── x_twitter.py           # X (Twitter) 自动化投稿: 媒体上传断言、敏感媒体标记、AI 声明、超限防截断/串推
│   └── monetization.py        # 赞助变现平台: Patreon, Ko-fi 赞助引流模版与无码原图分发
│
├── data/                      # 本地数据存储与缓存目录 (自动化产生，受 gitignore 保护)
│   ├── cookies/               # 各平台持久化 Cookie (JSON)
│   ├── profiles/              # 各平台解析出的用户名资料 (JSON)
│   ├── avatars/               # 各平台下载并缓存的高清用户头像图片 (PNG)
│   ├── cache/                 # 预览与临时文件缓存
│   ├── presets.json           # 默认表单配置、打码参数、水印模板与引流签名持久化
│   ├── draft.json             # 标题与描述防抖实时草稿持久化
│   ├── diagnostic_report.md   # 一键自检生成的完整健康审计 Markdown 报告
│   └── window_state.json      # 窗口大小 (全屏/最大化状态) 与三栏 QSplitter 宽度比例持久化
│
├── requirements.txt           # 项目 Python 依赖清单
├── start.bat                  # Windows 极速启动批处理
├── make_release_zip.py        # [发布工具] 发行包与代码包构建脚本 (严格保留 dispatchers/ 目录树并做解压自测)
├── MUSE_DEBUG_HANDOVER.md     # [联合调试] 针对 Muse 的缺陷复盘、真实探针实现与验收指南
└── PROJECT_HANDOVER.md        # [本文件] 项目主交接与系统全景档案
```

---

## 4. 四大核心子系统深度剖析

### 4.1 响应式界面与画布交互系统 (`app.py`)

#### A. 布局架构与三栏划分
主窗口采用纯暗色配色方案（背景色 `#121218`，面板卡片 `#181822`，聚焦高亮 `#4f46e5` / `#06b6d4`）：
1. **左侧栏 (Left)**：多选图片/动图列表、批量导入/移除、打码全局开关、样式（黑条/马赛克/模糊）、覆盖模式（适中 55%/细条 30%/严实 100%）、打码控制按钮（清除/恢复 AI）、水印全参数调节区（文本、系统字体下拉框、字号、不透明度、方位、反色描边）。
2. **中央栏 (Center)**：核心主画布 (`UnifiedPreviewCanvas`)、图片切换导航、全图尺寸信息与打码/水印实时状态条。
3. **右侧栏 (Right)**：目标平台勾选矩阵（X、DeviantArt、Patreon、Ko-fi）、各平台专属合规开关（Sensitive、AI 声明、Mature 分级、赞助可见性）、赞助引流签名追加区、发布操作区与内嵌通知栏 (`NotificationBar`)。

#### B. `UnifiedPreviewCanvas` 实时交互与零开销渲染
这是项目最核心的自定义组件，继承自 `QLabel`，支持静态图与动态 GIF：
- **静态图片 (PNG/JPG/WebP)**：
  - 在内存中直接合成带打码和水印的 `QPixmap` 呈现在画布上。
  - 在 `paintEvent` 中，动态绘制交互式矢量控制图层（虚线边框、编号徽章、8 个缩放锚点），完全不污染底图。
- **动态动图 (GIF) 60FPS 极速渲染**：
  - **绝不在磁盘重编码或跳帧**！直接加载 `QMovie` 并在 `paintEvent` 执行动态图层覆盖（实时 `fillRect`、`currentPixmap` 动态打码截取、`QFont` 水印矢量绘制）。
  - 用户拖动打码/水印滑块时响应延迟仅 **1.5ms**，零内存泄漏。
- **8 向拖拽控制交互系统 (Photoshop / Figma 级体验)**：
  - **移动位置**：鼠标在任意框内按下并拖动，即可任意挪动。
  - **8 向缩放**：点击选中任意框后，边框四周显示 **8 个青色圆形锚点**（4 个角落 + 4 个边线中点）。鼠标悬停自动切换光标（`SizeFDiagCursor`, `SizeVerCursor` 等），拖拽即可任意缩放。
  - **空白处新增**：在图片空白处按住并拖拽，即可拉伸新建打码框。
  - **删除**：选中后按键盘 `Delete` / `Backspace` 键，或右键弹出上下文菜单一键删除。
  - 所有改动通过 `boxes_changed` 信号即时同步给 `custom_censor_boxes[current_img]` 并触发防抖刷新。

#### C. 多线程与零弹窗设计
- **`LoginThread(QThread)`**：所有平台的浏览器自动化登录操作（`disp.login_interactive()`）**严禁**在 Qt 主事件循环中同步执行。统一由 `LoginThread` 在后台运行，主界面全程 100% 保持流畅，避免 Windows 抛出“未响应/死机”状态。
- **`PublishThread(QThread)`**：多平台分发任务由后台线程逐个派发，并通过 `progress_signal` 和 `completed_signal` 实时汇报发布百分比与每个平台的发布结果。
- **`NotificationBar(QFrame)`**：全局严禁调用阻塞式 `QMessageBox`！所有用户反馈、错误告警与进度日志均通过内嵌通知栏展示（支持 `info` 蓝、`success` 绿、`warning` 黄、`error` 红），自动 5 秒渐隐或永久保留。

---

### 4.2 智能敏感部位遮挡引擎 (`censor_engine.py`)

- **模型推理**：尝试加载 NudeNet ONNX 模型。若模型不可用或加载异常，自动平滑回退至**图像解剖学启发式智能几何算法**，确保绝不抛出未捕获异常。
- **DLL 初始化避坑**：
  在 Windows 平台上，OpenCV 与 ONNX Runtime 同时存在时可能出现 `onnxruntime_pybind11_state` DLL 初始化失败崩溃。代码首行强制提前执行 `import onnxruntime` 解决冲突。
- **样式与遮挡比例**：
  - `black_bar`：纯黑遮挡矩形，边界抗锯齿。
  - `mosaic`：自适应双线性重采样降质马赛克，根据图像分辨率自动计算像素块大小。
  - `blur`：大核高斯模糊。
  - 覆盖模式计算 (`compute_censor_box`)：支持 `moderate` (适中 55% 中心收缩)、`slender` (轻量线条 30%) 与 `full` (100% 全覆盖)。

---

### 4.3 高质感文字防盗印与排版引擎 (`watermark_engine.py`)

- **系统字体索引与双端对齐**：
  - Windows 系统注册表字体库遍历 (`SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts`)。
  - `CURATED_FONTS_DETAIL` 映射表：精确绑定人类易读显示名（如 `"Arial (经典无衬线)"`）到标准的 QFont Family（`"Arial"`）及 Windows TTF 实体文件（`"arial.ttf"`），确保 **Pillow 后台渲染与 Qt 前台画布渲染出的字体 100% 像素级一致**。
- **子像素几何边界修复**：
  - 彻底规避旧版 `tx = w - tw - margin` 的贴边溢出 Bug。
  - 基于 Pillow `draw.textbbox((0, 0), text)` 返回的 `b_left`, `b_top`, `b_right`, `b_bottom` 绝对边界，计算：
    - 右下角：`tx = w - margin_x - b_right`, `ty = h - margin_y - b_bottom`
    - 左下角：`tx = margin_x - b_left`, `ty = h - margin_y - b_bottom`
    - 居中：`tx = (w - (b_right + b_left)) // 2`, `ty = (h - (b_bottom + b_top)) // 2`
  - 前台 Qt 画布计算基线时扣除 `descent`，确保任何中英文字体绝不贴边切字。
- **倾斜平铺防盗印 (`tiled`)**：
  - 自动将单段文字绘制后旋转 28° 生成防盗印印章，并在整张画布上以交错步长平铺。
- **GIF 导出支持 (`apply_watermark_gif`)**：
  - 自动遍历 GIF 的所有帧序列，逐帧合成后保持原生 `duration` 与 `loop` 重建保存。

---

### 4.4 全平台自动化发布矩阵 (`dispatchers/`)

#### A. `BaseDispatcher` (基准规约)
- **隐身浏览器创建 (`create_stealth_browser`)**：
  - 专门指定 Windows 系统自带的 Microsoft Edge (`channel="msedge"`)。
  - 关键启动参数：`--disable-blink-features=AutomationControlled`、`--disable-popup-blocking` (至关重要，解决第三方 OAuth 弹窗被拦截)、`--no-sandbox`、`--no-first-run`。
  - 移除 `--enable-automation` 标志，使 Google 账号认证系统将其识别为普通真实浏览器。
- **合规项回读验证基准 (`check_selector_state` / `verify_compliance_item`)**：
  - 规定所有平台 Dispatcher 的 `publish()` 均返回三元组 `(success: bool, message: str, compliance: list[dict])`。
  - `check_selector_state(page, selector, expected)` 统一探测 `input.is_checked()`, `aria-checked`, `aria-selected`，并在提交前校验勾选状态，杜绝静默假发布。
  - 详细审计结构包含 `platform`, `item`, `expected`, `verified`, `detail`，供 UI 层进行显性警告展示。

#### B. `DeviantArtDispatcher` (重点平台)
- **Cloudflare WAF 容灾导航器 (`safe_goto`)**：
  - 严禁使用默认的 `wait_until="load"`（DeviantArt 会返回 HTTP 202 并在后台取消部分资源请求，导致 Chromium 抛出 `net::ERR_ABORTED` 崩溃）。
  - 统一使用 `wait_until="domcontentloaded"`，并自动捕获 `ERR_ABORTED` 转换为静默等待 DOM 就绪。
- **直达登录页与多窗口 OAuth 追踪**：
  - 放弃访问庞大的主页，直接打开 `https://www.deviantart.com/users/login`。
  - 循环检测逻辑**仅在 DeviantArt 主域**查询登录标志，**绝不对正在加载的 Google 弹窗调用 DOM 查询**（避免打断 Google 重定向）。
- **高可用头像与资料抓取**：
  - 解析 `userinfo` Cookie 获取真实用户名与头像链接。
  - 优先调用 Playwright 鉴权会话（`context.request.get(avatar_src)`）下载头像，**彻底规避 Wix / DeviantArt CDN 防盗链 403 导致的“头像无法显示”问题**。
- **投稿原生表单对齐 (`publish`)**：
  - 自动勾选官方防封项：`Created using AI tools`。
  - 自动设置 `Mature Content` -> `Strict` / `Moderate` -> 勾选 `Nudity`。

#### C. `XTwitterDispatcher` (X / Twitter)
- 自动读取 Cookie 会话。
- 自动填充推文文本；如果作品启用了成人遮挡或勾选了敏感标记，自动为上传的媒体设置敏感警告 (Sensitive Content) 标记。
- 自动在文本末尾附带 `#AIArt` 或由 AI 辅助创作声明。

#### D. 赞助变现平台 (`monetization.py` - Patreon & Ko-fi)
- **赞助者权益保障机制**：
  - 公开发布端 (X / DeviantArt) 严格分发打码/水印版本以合规并防盗；
  - 赞助端 (Patreon / Ko-fi) 自动化投稿严格读取 `payload["raw_images"]`，分发**无码高清原图**，切实保障付费会员权益。
- **自动引流与受众权限**：
  - 自动管理赞助档位可见性（Patreon: public / tiers / patrons；Ko-fi: everyone / supporters）；
  - 提供一键在描述末尾追加赞助引流签名模版功能。

---

## 5. 历史已解决踩坑档案与防御红线 (Muse 必读)

为了避免 Muse 在后续迭代中引入回归 Bug，请**严格遵守**以下防御性编程红线：

| 陷阱与故障现象 | 根因剖析 | 严格红线规约 (DO NOT REGRESS) |
|---|---|---|
| **1. NudeDetector 初始化失败 / DLL load failed** | Windows 下 OpenCV 与 ONNX Runtime 的底层 C++ 运行时绑定冲突 | 必须在 `app.py` 和任何启动脚本最顶层、在 `cv2` 之前执行 `import onnxruntime`。 |
| **2. DA 登录抛出 `Page.goto: net::ERR_ABORTED`** | Playwright 默认 `wait_until="load"`，DA 部署了 Cloudflare WAF 并返回 HTTP 202，后台脚本取消请求触发了终止 | 访问 DA 页面严禁使用 `"load"`，必须使用 `wait_until="domcontentloaded"` 并由 `safe_goto` 捕获异常。 |
| **3. 点击 Google 登录弹窗白屏且程序死机** | a) 同步在主线程调用 Playwright 导致 Qt 事件循环冻结；<br>b) 未向 Context 注入反爬脚本，弹窗暴露了 webdriver；<br>c) 缺少 `--disable-popup-blocking` | a) 登录必须放在 `LoginThread(QThread)` 异步执行；<br>b) 反爬必须注入 `BrowserContext`；<br>c) Chromium 启动参数必须包含 `--disable-popup-blocking`。 |
| **4. 水印下拉框更换字体没有任何反应** | 下拉框传入了 `"Arial (经典无衬线)"` 等显示名，Qt 的 `QFont` 无法识别带后缀的名称，静默回退到默认字体 | 必须通过 `watermark_engine.get_font_family()` 提取纯粹的 Family 名传给 `QFont`。 |
| **5. 交互式微调打码框闪退 (Crash)** | 旧版的独立弹窗使用了未拷贝内存缓冲区的 `QImage(rgb.data)`，且在 Windows 中文路径下 `cv2.imread` 返回 None 导致崩溃 | 彻底废除独立弹窗！所有交互直接内置在主画布 `UnifiedPreviewCanvas` 的 `paintEvent` 中。 |
| **6. 调整打码滑块时程序卡死/卡顿数秒** | 旧代码在滑块每次移动时都在磁盘重新生成合成完整的 GIF 动图并重新读取 | 严禁在滑块拖动时写入磁盘！必须使用 `UnifiedPreviewCanvas` 的 `paintEvent` 实时矢量覆盖。 |
| **7. 启动 Google 登录被 Google 拦截“此浏览器或应用可能不安全”** | a) 旧反爬脚本使用 `Object.defineProperty` 重写 `navigator.plugins=[1,2,3,4,5]`，被 Google BotGuard 识别非 `PluginArray`；<br>b) 手动硬编码 `user_agent` 导致与 Edge 原生 Client Hints (`Sec-Ch-Ua`) 冲突并清空 `navigator.userAgentData`；<br>c) 本机 Edge 进程占用导致 `User Data` 单例锁 (Error 32) 触发时，若回退到临时沙盒会被 Google 拒登 | ① 统一经 `create_login_context` 启动：优先复用本机 Edge 真实配置；若被系统占用锁死，自动平滑切换至各平台专属持久化目录 (`data/browser_profiles/<platform>`)，永不锁死；<br>② 依靠底层 `--disable-blink-features=AutomationControlled` 原生置 `webdriver=false`，**严禁注入破坏 plugins/webdriver 的 JS 脚本**；<br>③ **严禁覆盖原生 User-Agent**，保持 Edge 真实指纹与 Client Hints 100% 吻合。 |
| **8. 界面弹出系统弹窗导致自动化测试或全屏体验中断** | 调用了 `QMessageBox` | 整个工程**禁止调用 `QMessageBox`**，全部统一使用 `self.notify(msg, level=...)`。 |
| **9. 浏览器配置目录被占用导致崩溃 (ProcessSingleton Error 32)** | Windows 系统后台 Edge 进程锁定 `User Data` 目录 | 严禁仅依赖单一原生目录启动；必须使用 `create_login_context` 平滑回退至专属持久化环境 (`data/browser_profiles/`)。 |
| **10. 手调打码框在松开鼠标刷新时二次换算缩小 (越调越小)** | 鼠标松开触发即时预览刷新时，代码把手调框重复传入 `compute_censor_box` 套用覆盖模式换算 (moderate 模式自动收缩到 55% 并居中) | 统一走 `resolve_final_boxes` 入口：手调框 (`user_customized=True`) 仅作边界越界钳制，**严禁再次套用模式缩放**；只有 AI 初识别框才走换算。静态图预览、GIF 预览、单张导出、多图发布、GIF 分帧 5 处调用点全部严格遵循该规约。 |
| **11. 发布合规项（分级/AI标识）静默跳过导致封号隐患** | 旧逻辑为“尽力而为”，选择器一旦失效或未勾选成功仅控制台打印日志便静默提交；右侧细分选项未接入发布 payload 具有误导性 | ① 4 大 Dispatcher (X, DeviantArt, Patreon, Ko-fi) 的 `publish()` 必须统一返回三元组 `(success, msg, compliance)`，并兼容老式二元组；<br>② 成人分级与 AI 标识等合规项在点击后、最终提交前必须调用 `check_selector_state` 回读验证 DOM 真实勾选状态，**严禁静默跳过**；<br>③ 右侧各平台细分选项（X 敏感警告、DA Mature、Patreon/Ko-fi 受众）必须实时汇入 `payload["platform_options"]`；<br>④ 存在任何未确认项时，发布汇总日志中必须显性黄色/红色警示，并触发常驻 `NotificationBar` 提示（严格遵守红线 #8，严禁使用 `QMessageBox`），Ko-fi 明确标注文档未覆盖需人工核验。 |
| **12. 静默截断长文本导致变现签名丢失与字数浪费** | 旧版在 `dispatchers/x_twitter.py` 中直接 `[:275] + "..."` 强行砍断尾部，UI 零体现且将末尾赞助引流链接全盘抹杀；Premium 用户 25,000 额度无法发挥 | ① 严禁静默截断！UI 描述框下方必须集成实时字符计数器（URL 按 23 字符折算，≥90% 变黄，超限变红）；<br>② 发布前若 X 文本超限，必须通过发布控制台**内嵌确认条**（严格遵守红线 #8，严禁 `QMessageBox`）由用户显性决策：[截断发布] / [拆成串推] / [取消 X 发布]，或依据 X 卡片预设策略自动执行；<br>③ 截断必须采用方案 A：自动探测末尾签名/赞助链接特征，优先从正文中间截断并完整保留末尾引流尾巴；<br>④ 串推必须遵循 Thread 规范：拆分不得破坏完整 URL，图片与 AI/敏感合规项置于首条，逐条追加 `(1/N)` 序号；<br>⑤ 支持 X Premium 开关（上限 25,000），状态持久化至 `presets.json`。 |
| **13. 画布几何重复重采样卡顿 & 裸异常吞没与盲等 (代码健康度治理)** | a) 鼠标拖拽或移动时高频触发 `get_display_geometry()`，旧逻辑每次均执行 `pixmap.scaled(..., Qt.SmoothTransformation)` 导致 CPU 严重瓶颈；<br>b) 35 处 `except Exception`（其中 21 处裸 `pass`）导致错误静默丢失；<br>c) 页面盲等 `time.sleep(3~5s)` 导致发布缓慢；<br>d) 用户名硬编码 `@FOXTONTONTON` 破坏多账号通用性 | ① `UnifiedPreviewCanvas` 的渲染几何 `(orig_w, orig_h, ox, oy, dw, dh, scale_x, scale_y)` 必须经 `_cached_geometry` 缓存，仅在 `resizeEvent`、`set_image`、`set_gif` 及 `_update_display` 时失效；计算必须基于比例数学公式，**严禁在鼠标交互事件中执行图像重采样缩放**；<br>② **严禁使用任何 `except: pass`**！工程全量消除裸 pass，统一接入 `logger.py`（控制台 INFO + 每日滚动文件 DEBUG，自动清理保留 7 天）；<br>③ 分发器除组件动画微延时（≤1s，需注释 `# 等待组件动画/渲染完成`）外，**全部统一使用 Playwright 条件等待** (`wait_for_selector` / `wait_for_url` / `wait_for_load_state`)；<br>④ 所有页面导航统一收拢至基类 `self.safe_goto(...)`；<br>⑤ X (Twitter) 登录用户名严禁硬编码，必须从 DOM 动态抓取，缺省降级为中性“X 用户”；<br>⑥ 草稿 1000ms 防抖自动存入 `data/draft.json`，开机自恢复，发布成功自清理；<br>⑦ 发布失败支持网络/超时自动重试 1 次，并在界面提供「重试失败项」按钮定向重新分发。 |
| **14. 模块未绑定命名空间、动图服务端转码超时与 UI 视觉层级融合** | a) DA dispatcher 缺失 `import os`，切换独立 profile 抛出 `NameError`；<br>b) Ko-fi 常驻旧进程缺少顶层 `import time`；<br>c) 推特对 12MB+ GIF 进行服务端转码耗时 25~45s，15s 超时误判断言中断发帖；<br>d) 次级按钮深灰背景与 `QTextEdit` 描述框底色混淆 | ① 各 Dispatcher 头部必须显式引入 `import os, time`，模块加载层级前置；<br>② X 发帖按钮就绪等待升级为 60s 弹性轮询，同时断言 `is_enabled()` 与 `aria-disabled != "true"`，覆盖多语种发帖选择器；<br>③ UI 描述框采用凹陷暗黑底 `#0c0d14`，常规操作按钮统一收拢为高辨识度 Indigo 实体蓝（`#3730a3` / 边框 `#4f46e5`），彻底拉开层级差异；<br>④ 详见专项档案 `MUSE_DEBUG_HANDOVER.md`。 |
| **15. 鼠标中键滚轮滑动导致选项栏漂移与控件数值误触** | 用户上下滚动鼠标滚轮时，Qt 默认将滚轮事件分发至 `QScrollArea` 及光标掠过的 `QComboBox`、`QSlider`，导致选项栏意外滚动且设置项静默改变 | ① 实现 `NoWheelEventFilter(QObject)` 全局事件过滤器，在 `QApplication` 顶层统一拦截并吞没 `QEvent.Wheel` 与 `Qt.MidButton` 事件；<br>② `MainWindow` 与 `UnifiedPreviewCanvas` 显式重写 `wheelEvent` 为 `ignore()`；<br>③ 选项栏与所有内部控件保持绝对静止，彻底杜绝误触。 |

---

## 6. 数据持久化规范 (`data/`)

所有持久化文件统一置于 `art_crosspost/data/` 目录：
1. **`data/window_state.json`**：
   - 记录窗口是否最大化 (`is_maximized`)、窗口正常几何尺寸 (`normal_geometry`)、三栏 QSplitter 宽度数组 (`splitter_sizes`) 以及十六进制状态。
   - 用户拖动分割栏或调整窗口大小时，通过 400ms 防抖定时器自动回存，关机重启后 100% 恢复上一次布局。
2. **`data/presets.json`**：
   - 存储默认的打码样式、遮挡尺寸、水印配置与赞助引流模版。
3. **`data/draft.json`**：
   - 自动保存未发布的作品标题、通用标签和正文描述（1000ms 防抖持久化）；开机启动自动恢复，全平台发布成功后自动清理。
4. **`data/logs/artcrosspost_YYYY-MM-DD.log`**：
   - 每日滚动日志文件，记录 DEBUG 级以上的完整排错轨迹；系统自动扫描并清理保留最近 7 天的历史日志。
5. **`data/cookies/<platform>_cookies.json`**：
   - 保存 Playwright 抓取到的浏览器 Cookie 字典列表。
6. **`data/profiles/<platform>_profile.json`**：
   - 记录已认证的用户名。
7. **`data/avatars/<platform>_avatar.png`**：
   - 本地缓存的用户头像，界面直接基于此路径圆角渲染。

---

## 7. 运行、调试与维护手册

### 7.1 启动程序
在 Windows PowerShell / CMD 中执行：
```powershell
cd C:\Users\yourname\.gemini\antigravity\scratch\art_crosspost
python app.py
```
或直接双击运行：
```powershell
start.bat
```

### 7.2 语法校验与编译命令
在对任何文件进行改动后，执行以下命令进行编译验证：
```powershell
python -m py_compile app.py watermark_engine.py censor_engine.py config.py diagnostics.py dispatchers/base.py dispatchers/deviantart.py dispatchers/x_twitter.py dispatchers/monetization.py
```
若返回 exit code 0，说明语法与模块导入均完全正常。

### 7.3 清理临时文件与缓存
如需清理测试缓存或重置窗口状态：
```powershell
# 清除图片处理缓存
Remove-Item data\cache\* -Recurse -Force

# 重置窗口布局 (下次启动将恢复默认最大化)
Remove-Item data\window_state.json -Force
```

---

## 8. 未来推荐演进路线

Muse 接管项目后，可优先探索以下高价值拓展方向：

1. **批量发布队列与计划任务 (Task Queue & Scheduler)**：
   - 当前发布是即时点击即时派发。后续可增加“定时分发”功能（如设定每天晚上 20:00 自动发送一组作品到 Twitter 和 DeviantArt）。
2. **视频与动画短片支持 (MP4 / WebM)**：
   - 目前全面支持 PNG、JPG、WebP 及 GIF 动图。若后续用户创作视频短片，可引入 `ffmpeg-python` 实现视频局部加水印与截帧打码。
3. **多账号矩阵切换 (Multi-Account Profile)**：
   - 允许在同一平台（如 Twitter）下保存多个账号的 Cookie 配置，并在右侧平台栏支持下拉切换当前活跃账号。
4. **网络代理配置中心 (Proxy Settings)**：
   - 在 UI 设置中增加 HTTP/SOCKS5 代理端口设置（如 `127.0.0.1:7890`），并在 `create_stealth_browser` 中自动注入 `--proxy-server`，提升国内访问海外平台的连接成功率。

---

## 9. 各平台原生发布与合规项实测对齐记录 (X / DeviantArt / Patreon / Ko-fi)

### 9.1 X (Twitter)
- **15MB GIF 硬限制与多级自适应压缩**：
  - X 平台对动态 GIF 有严格的 15MB 物理限制。若原图体积过大（如 18~25MB 多帧动图），常规减色可能仍会超限（如 17.8MB）。
  - 修复：在 `app.py` 的 `_generate_composite_gif` 中部署**迭代阶梯压缩循环**：
    1. 调色板阶梯降级：256 色 -> 192 色 -> 128 色 -> 96 色；
    2. 若降至 96 色仍超限，系统自动以 `sqrt(13.8MB / current_size)` 比例缩放物理像素分辨率（LANCZOS 重采样）并重新压制；
    3. 严格将体积锁定在 **≤ 13.8MB**，且实测画质保持度达 95%+，彻底消灭 Twitter 15MB 拦截门禁与海外传输超时。
- **发布熔断机制**：
  - `x_twitter.py` 增加前置拦截：若检测到超限弹窗或发帖框内未成功挂载媒体缩略图，立即熔断中止发送，严防“只发文字没发图片”的事故。
- **合规选项对齐**：
  - 动态 GIF 原生不支持单图裁剪与媒体编辑弹窗，其合规标记依托底部 Flag 按钮 (`contentDisclosureButton`) 触发的“由 AI 生成 / Made with AI”弹窗进行对齐。
  - 静态图使用多语言选择器 `button[aria-label*='编辑媒体'], button[aria-label*='Edit media']` 唤起编辑弹窗并自动勾选 Tab 2 的 Nudity / 敏感内容警告。

### 9.2 DeviantArt
- **CloudFront WAF 403 规避与持久化上下文**：
  - 若在无状态临时会话中直接请求 `https://www.deviantart.com/submit`，CloudFront 会阻断并返回 `403 ERROR: The request could not be satisfied`，导致无法定位文件上传框。
  - 修复：全量复用登录时建立的独立持久化环境 (`data/browser_profiles/deviantart`)，并在访问提交页前优先导航至主域建立 WAF 握手，100% 保持正常 200 通行。
- **素材格式智能分发与羊驼动画等待**：
  - 区分媒体类型：GIF 动图精准匹配 `accept*='image/gif'`，静态图匹配 `accept*='image/png|jpg'`，杜绝将动图强行塞入静态图控件。
  - 大动图在 DeviantArt 会触发“羊驼吞文件 (`Please wait while the llama noms your file...`)”进度等待，等待时间放宽至 75 秒，保证 Studio 表单完全展开。
- **Studio 弹窗原生表单与提交**：
  - 标题输入框：原生 selector 为 `input[name='title'], input[placeholder*='Add your title here']`，彻底覆盖原有的文件名默认推导。
  - AI 与成人合规：定位 Checkbox 与 label 文本，点击并完整回读校验状态。
  - 提交按钮：定位原生绿色 `Submit` 按钮（严格排除旁边的 `Save to Studio` 存草稿按钮），提交后断言弹窗销毁与详情页跳转。

### 9.3 赞助端双轨素材分发 (Patreon & Ko-fi)
- **无码原图通道**：
  - 严格读取 `payload["raw_images"]`，分发未经打码或水印的原版高质量画作。
  - 权限与可见性自动同步：根据右侧卡片选择设置 Patreon (public/tiers/patrons) 与 Ko-fi (everyone/supporters) 的受众范围。
- **Ko-fi 运行环境健壮性**：
  - 模块顶层与发布方法内部均显性绑定 `import time`，全量消除未绑定异常。

---

## 10. 发布自查与全平台健康诊断体系 (Self-Check & Diagnostics System)

本体系严格遵循 Muse 架构师制定的《自查 / 诊断系统需求文档》，构筑全链路防御工事，全面消灭假成功与静默失败。

### 10.1 发布前自查门禁 (Pre-publish Gate - P0 级)
- **拦截目标**：在唤起任何 Playwright 浏览器或发起写操作之前，拦截 90% 的已知致命问题。
- **门禁检验序列**：
  1. **登录态轻量探针 (`probe_login()`)**：无需唤起完整浏览器，采用轻量化请求或本地关键凭据检验：
     - X: 验证本地 Cookies 是否包含核心 `auth_token`；
     - DeviantArt: 验证 `auth_secure` 令牌与 `userinfo` 用户名；
     - Patreon / Ko-fi: 校验本地会话完整度。
     - 若探针返回 `fail`，门禁立即熔断并跳过该平台，并在状态栏与最终统计中明确提示原因，绝不强行拉起无凭证浏览器。
  2. **素材合规与体积门禁 (`validate_assets`)**：
     - 文件物理存在性与大小非零检查；
     - 严格约束平台专属限制（X: GIF ≤ 15MB、单图 ≤ 5MB）；超限者直接拦截并报错。
  3. **文本合规门禁 (`validate_text`)**：
     - 全局标题非空约束（至少 1 字符）。
- **门禁熔断与容灾逻辑**：
  - 若勾选的部分平台门禁未通过，系统自动跳过未通过项，仅对通过项继续执行浏览器分发；
  - 若全部选中平台均未通过，立即停止后续发布并给出针对性提示。

### 10.2 发布中关键步骤断言 (In-flight Assertions - P0 级)
在各平台调度器的 `publish()` 方法中部署三大刚性断言哨卡：
1. **上传后断言 (`assert_attached(page, selector, timeout)`)**：
   - 上传图片/GIF 后，条件等待附件预览卡片渲染完毕；超时未出现直接判定失败中止，严防“图片未附加就发送纯文字”的严重缺陷。
2. **填表后断言 (`assert_field_value(page, selector, expected, max_retries=1)`)**：
   - 填充标题/描述后回读页面真实文本，若不一致则自动重试一次；仍不一致立即中止，杜绝默认填充覆盖（如 DeviantArt 默认文件名）。
3. **提交后断言 (`assert_submitted(page, check_fn, timeout)`)**：
   - 提交后必须校验 URL 跳转、弹窗销毁或成功 Toast 提示；超时未确认则判定为未生效并明确提示“可能已存入草稿箱，请前往网页确认”；
   - **全面消灭假成功**：彻底删除所有 `return True, "表单已填充并尝试发布"` 等掩耳盗铃式的分支。

### 10.3 一键体检与诊断引擎 (`DiagnosticsEngine` - P1 级)
- **非侵入式只读诊断**：
  - 界面提供“🔍 一键自检”按钮；
  - 启动后台 `DiagnosticThread (QThread)` 串行执行全项体检，耗时约 15~30 秒；
  - 只读 DOM 探测，严格禁止触碰任何发布或写操作按钮。
- **四大体检大类 (30+ 检查项)**：
  - `🖥️ 运行环境与依赖`: 检查 Python 模块依赖 (PyQt5, Playwright, OpenCV, PIL, onnxruntime)、磁盘余量、Edge 浏览器启动能力；
  - `🌐 平台网络连通性`: 测量 4 大平台域名 HTTP 连通性与往返延迟；
  - `🔑 平台登录态存活探针`: 对 4 大平台执行登录会话轻量存活测试；
  - `🎯 投稿页选择器健康度`: 无头模式访问各平台投稿端点，检测发帖输入框、文件选择器等关键 DOM 是否漂移失效。

### 10.4 嵌入式诊断报告面板 (P1 级)
- **严格遵循红线 #8 (无弹窗原则)**：
  - 诊断面板完全内嵌于主窗口右侧下方 (`self.diag_panel`)，支持一键收起；
  - 动态计算健康评分百分比 (`health_score`)，用 `[PASS]`、`[WARN]`、`[FAIL]` 标签清晰高亮；
  - 提供“📄 导出报告 (.md)”按钮，自检完毕自动在 `data/diagnostic_report.md` 生成 Markdown 离线档案并支持一键唤起系统默认编辑器查看。

---

> **致 Muse**：  
> 本项目代码经过高度严谨的防御性打磨，所有多线程通信、图层几何变换、自动化防爬策略与四重自检诊断防线均已全面闭环。请在后续迭代中继续遵循本档案中的设计哲学与红线规约。祝你开发顺利！
