# LocalDraw 本地漫画工作室

完全在本地运行的 Windows x64 AI 二次元漫画创作与绘图桌面工作室。所有核心程序位于 [`LocalDraw/`](LocalDraw/)，共享模型权重、Python 虚拟环境、推理运行时与历史作品位于根目录。

> **维护约定（强制）**：今后凡涉及代码、配置、工作流、界面或目录结构的任何改动，**必须**同步写入本 `README.md` 或由本文件直接指引的文档（优先追加到 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)）。

## 快速启动

直接双击 [`LocalDraw/LocalDraw.exe`](LocalDraw/LocalDraw.exe) 启动（根目录的 `LocalDraw.lnk` 是指向它的快捷方式）。应用以独立 Windows 桌面窗口运行，不打开浏览器或 CMD；关闭窗口后，已提交的出图任务和夜间队列继续在后台执行。

> 注意：根目录的 `LocalDraw-RTX4080-Setup.exe` 与 `LocalDraw-Paste-Update.exe` 是打包安装包，不是日常启动入口。

## 核心主流程

经过 2026-11-25 深度精简后，工作室只保留日常高频核心流程：

1. **提示词出图与二次采样（二采）**：首页「提示词出图」直接输入正负提示词，选择角色外观、兼容架构的 LoRA、模型、张数（1–16）和可选种子（留空随机，填写后多张按序递增）即可生成，支持一键复制单张/全部原图。出图后点击右侧图库右下角「二采」可在保持原图位置不变的前提下切入左侧二采二级面板，支持同模型或跨模型（如 `Anima → Krea2`、`Krea2 → MOSS`）二采、兼容 LoRA 选择、步数/重绘幅度/种子/缩放倍率（`1.0×` / `1.25×` / `1.4×` / `1.5×` / `2.0×`）、「细节重绘（潜空间 `bicubic`，默认 `denoise 0.45–0.50`）/ 保真超分（`RealESRGAN` + 低 `denoise`）」双路线、实时分辨率与舒适区提示、「整图精修 / 分块高清（2K–4K 逐块原生甜点区重绘+羽化拼接）」自动/手动切换以及可选「二采后超分（`RealESRGAN` `1.5× / 2.0×`，采样完成后无损锐化放大）」，按 `Esc` 或左上角「← 返回」退出；1 张自动应用，多张手动选图应用，且随时可「取消应用」恢复一采原图。
2. **分镜与逐格导入出图**：
   - 「创建分镜作品」输入故事构想 → 本地 Qwen 生成分镜 → 确认并逐格出图；或「直接导入每格提示词」（粘贴 TXT 每行一格或逐格 JSON）设置角色、LoRA 与每格张数后直接批量生成。
   - 支持导出分镜 JSON 给外部工具填词后再导回核对，也支持本地 Qwen 自动逐格填词与问题格子统一重画。
3. **融合任务**：将多部已有分镜作品或队列任务按顺序拼接，逐格核对提示词后保存为独立新作品统一出图。
4. **夜间任务列表**：竖向时间轴队列（持久化于 `LocalDraw/data/night-queue.json`），支持多任务排队、预约 30 天内定时执行、暂停/继续/动态修改剩余每格张数与断点恢复。
5. **角色库、LoRA 与排版导出**：版本化角色卡（支持图文建卡）、按模型架构分目录隔离的 LoRA 绑定与权重控制、逐格选图、横排对白气泡排版及整页 PNG / 可编辑 SVG / 独立文字层导出。

## 默认创作与模型规则

- **默认创作视角**：后续创作、分镜和绘图提示词默认采用 **POV 第一人称视角**（不使用双人构图，除非明确要求）。
- **默认语言**：气泡对白、旁白与拟声默认**简体中文**；人物、场景与绘图提示词使用**英文**。
- **默认出图策略**：默认每格 **2 张**，按确认后的提示词原样出图，**不启用自动 AI 审核与自动补画**。
- **支持模型**：
  - 默认：**Anima Delta Mix v6**（35 步，CFG 4.5；支持 Anima Edit v2 与单张固定参考图 / 自动参考上一格）。
  - 其他：**Krea2 Turbo（BF16 / FP8）**、**MOSS-Image SFT（BF16）**、Anima 3.8B 与 Qwen 编辑模型；LoRA 按模型架构隔离于 `models/loras/` 对应子目录（`anime`、`krea2`、`moss_image`、`sdxl`）。
- **显存管理**：顶部可预加载/卸载绘图模型；暂停任务或语言/绘图阶段切换时自动释放显存。

- **性能优化（2026-11-25）**：桌面启动器已改为并行初始化 WebView2 与 Python 后台并移除同步更新阻塞；LoRA 文件头检测启用磁盘缓存（`LocalDraw/data/lora-inspection-cache.json`），后端支持透明 `gzip` 压缩，提示词页角色与 LoRA 改为按需懒渲染（详见 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)）。

## 目录结构

```text
local_draw/
├─ LocalDraw/               唯一主程序源码与桌面应用（LocalDraw.exe、src/、web/、config/、scripts/、tests/）
│  ├─ src/comic_workflow/   精简后的后端服务（63 个核心模块）
│  ├─ web/                  原生 HTML/CSS/JS 前端（无构建依赖，漫画矢量字体位于 web/fonts/manga-font.json.gz）
│  ├─ scripts/              桌面启动器源码（windows/）、构建打包与核心测试脚本
│  └─ tests/                保留的离线单元测试（61 个文件）
├─ models/                  本地 Qwen、绘图底模与 LoRA 权重（由 LocalDraw/config/installed-runtime.json 引用）
├─ runtime/                 llama.cpp、Strata 与 ComfyUI-KreaMoss 运行环境
├─ .venv/                   主程序 Python 3.12 虚拟环境
├─ data/                    历史作品与公共数据（近期桌面作品位于 LocalDraw/data/projects/）
├─ docs/                    架构、变更日志与参考文档
└─ archive/                 历史旧版本、过时根目录源码、旧脚本与旧测试归档
```

## 文档与变更索引

| 文档 | 内容说明 |
|---|---|
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | **结构化变更日志**（所有代码简化、功能增删、配置调整的权威记录） |
| [`LocalDraw/README.md`](LocalDraw/README.md) | 桌面端安装更新、界面操作与源码说明 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 数据模型、确认机制与模块设计原则 |
| [`docs/DESKTOP.md`](docs/DESKTOP.md) | Windows 桌面版构建与自检说明 |
| [`docs/MODELS.md`](docs/MODELS.md) | 本地语言与绘图模型参数说明 |
| [`docs/Krea2和MOSS使用说明.txt`](docs/Krea2和MOSS使用说明.txt) | Krea2 与 MOSS 高精度出图说明 |
| [`docs/MOSS-LoRA使用说明.txt`](docs/MOSS-LoRA使用说明.txt) | MOSS LoRA 使用说明 |
| [`docs/CREATIVE.md`](docs/CREATIVE.md) | 剧本、角色与创作流程边界参考 |
| [`docs/ART_DIRECTION.md`](docs/ART_DIRECTION.md) | 彩色叙事漫画画风方向参考 |
| [`archive/notes/README-legacy-2026-11-25.md`](archive/notes/README-legacy-2026-11-25.md) | 2026-11-25 精简前的完整旧版 README 历史流水备份 |

## 常用开发与验证命令

在 `LocalDraw/` 目录下执行（使用根目录共享 `.venv`）：

```powershell
$env:PYTHONPATH = "F:\ChatGPT\local_draw\LocalDraw\src"
F:\ChatGPT\local_draw\.venv\Scripts\python.exe -m unittest tests/test_http.py tests/test_comfyui.py tests/test_quick_sample.py
```

重新编译桌面启动器与验证见 [`docs/DESKTOP.md`](docs/DESKTOP.md)。
