# ArtCrossPost (多平台艺术创作同步分发工具)

专为画师与漫画创作者定制的极简、轻量级、免 API 月费的多平台一键发布桌面客户端。

## 核心特性

1. **精简聚焦 6 大核心平台**：
   - 社交与社区：**X (Twitter)**、**DeviantArt**、**Bluesky**
   - 二次元枢纽：**Pixiv (P站)**
   - 创作者赞助：**Patreon**、**Ko-fi**
2. **Pixiv 专属 AI 自动打码分流**：
   - 导入一张无码原图，后台自动通过智能检测生成 Pixiv 合规版本（支持纯黑条、经典马赛克、高斯模糊）。
   - **分流机制**：无码原图分发至 Patreon / Ko-fi / X / Bluesky；合规打码图单独分发至 Pixiv。
3. **免 API 月费与防风控**：
   - 基于本地安全会话与 Playwright 自动化，免去 X 每月 100 美元的高昂 API 费用与 Patreon 发帖接口限制。
   - 复用创作者本机 Chrome 浏览器登录凭证，抗风控能力极强。
4. **一键赞助引流与预设模板**：
   - 支持一键追加 Patreon/Ko-fi 引流尾巴、记住各平台常用标签与分级状态。

## 快速启动

双击根目录下的 `start.bat`，或者在终端运行：

```bash
python app.py
```

## 项目目录结构

```
art_crosspost/
├── app.py                 # PyQt5 桌面主程序
├── config.py              # 全局配置与预设管理
├── censor_engine.py       # Pixiv 自动打码合规引擎
├── start.bat              # Windows 双击一键启动脚本
├── requirements.txt       # 依赖清单
├── dispatchers/           # 6 大平台专属分发器
│   ├── base.py            # 分发器基类
│   ├── x_twitter.py       # X (Twitter)
│   ├── pixiv.py           # Pixiv (专属打码分流)
│   ├── bluesky.py         # Bluesky (分级标签)
│   ├── deviantart.py      # DeviantArt (Mature 选项)
│   └── monetization.py    # Patreon & Ko-fi (可见档位)
└── data/
    ├── cookies/           # 各平台持久化本地会话凭证
    └── cache/             # 自动打码缓存文件
```
