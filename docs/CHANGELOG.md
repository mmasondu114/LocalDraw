# 变更日志（Changelog）

本文件由根目录 [`README.md`](../README.md) 直接指引。后续所有代码、配置、流程与目录变更均按日期倒序追加到本文件。

---

## 2026-11-26：二采面板新增「二采后超分」（后置 `RealESRGAN` `1.5× / 2.0×`）

- **后端（`LocalDraw/src/comic_workflow/second_pass.py`）**：
  - 新增 `post_upscale` 参数（`1.0` 关闭 / `1.5` / `2.0`，输出上限 `6144px`）。在 `VAEDecode`（或分块拼接）完成之后直接串联 `RealESRGAN_x4plus_anime_6B.pth` + `lanczos` 缩放到目标尺寸，不再次送入 `KSampler`，因此可在保持模型整图舒适区采样（如 Anima `1.0×` `2048×1152`）的前提下快速锐化线条并输出 3K/4K，零分身、零脸型漂移。
- **前端（`LocalDraw/web/prompt.js`）**：
  - 二采面板新增「二采后超分」下拉，实时尺寸条同步显示采样尺寸与后置超分最终尺寸（如 `2048×1152 → 2048×1152 → 超分 4096×2304`），图库详情同步记录后置超分倍率。
- **验证（`LocalDraw/tests/test_second_pass.py`）**：
  - 新增 `post_upscale=2.0` 节点连线与非法倍率单测（6/6 通过），Edge 无头浏览器端到端实测选择后置超分后实时尺寸提示正确且无报错。

---

## 2026-11-25（七）：全局记忆每个 LoRA 的上次使用强度（不再重置为默认 0.7）

- **前端（`LocalDraw/web/loras.js`、`prompt.js`、`drawing.js`、`imported-setup.js`）**：
  - 在 `loras.js` 新增基于 `localStorage`（`local-draw-lora-weights-v1`）的 `savedLoraWeights()` / `rememberLoraWeight()` / `defaultLoraWeight()` 工具函数，按 LoRA 文件路径持久化保存用户调过的强度值。
  - 在「提示词出图」、二次采样面板、分镜出图与逐格导入设置四个入口统一接入：改过任意 LoRA 权重后（如 `Krea2Rella = 0.5`、`v67Lighting = 0.35`），之后清空重选、进二采新加或重启应用都会自动沿用上次调好的权重，不再恢复默认 `0.7`。

---

## 2026-11-25（六）：二采新增「潜空间 `bicubic` 细节重绘」模式（对齐白垩光工作流）

- **背景与动因**：参考 Krea2 白垩光标准工作流（`LatentUpscaleBy('bicubic', 1.4)` + 高 `denoise=0.50`），此前默认先走 `RealESRGAN` 再用低 `denoise=0.30` 重绘时，对 Krea2 / MOSS 这类 Flow-Matching 模型过于保守，肉眼难以看出笔触与细节增益。
- **后端（`LocalDraw/src/comic_workflow/second_pass.py`）**：
  - 新增 `method` 参数（`latent` 默认 / `upscale`）与 `1.4×` 缩放选项：
    - **`latent`（细节重绘）**：走 `LoadImage → VAEEncode → LatentUpscaleBy(bicubic, scale_by) → KSampler`（适配 8× 与 16× VAE，含 MOSS `flux2-vae`），默认重绘幅度提至 Krea2 `0.50`（8 步）/ MOSS `0.45`（20 步）/ Anima `0.45`（24 步）；分块模式下用 `BICUBIC` 准备画布不提前锁死边缘。
    - **`upscale`（保真超分）**：保留 `RealESRGAN_x4plus_anime_6B` + 低 `denoise`（`0.30–0.35`）路线，适合已满意画面纯拉 4K。
- **前端（`LocalDraw/web/prompt.js`）**：
  - 二采面板新增「二采方式」下拉（`细节重绘（潜空间 bicubic，推荐）` / `保真超分（RealESRGAN，保留原画）`）与 `1.4×（潜空间推荐）` 倍率；1K 原图默认 `1.4×`，2K 原图默认 `1.0×`；随方式与分块状态自动联动推荐 `denoise`（潜空间整图 `0.45–0.50`、潜空间分块 `0.38`、保真超分 `0.30`）。
- **Krea2 整图舒适区与分块防分身护栏（`second_pass.py`、`prompt.js`）**：将 Krea2 整图舒适区从 `2.6MP` 提至 `4.8MP`、`VAEDecodeTiled` 阈值提至 `5.0MP`（`2048×1152 × 1.4× = 2864×1616` 自动走零分身整图原生解码）；进入分块模式时默认建议 `denoise=0.30`，用户调高至 `>0.35` 时界面主动弹出防背景人物分身预警。
- **Krea2 LoRA 结构识别补全（`LocalDraw/src/comic_workflow/lora_inspection.py`）**：扩展 `_is_krea2_adapter()` 与 `base_model` 元数据检测，支持 ModelScope/DiffSynth 导出的 `transformer.img_in` / `transformer.final_layer`（`base_model: krea/Krea-2-*`，如 `Krea2Rella_c1-st8000`、`wlop_c1-st4000`）与局部层滑块（如 `Detailer-KREA2` 从 `blocks.14` 起）正确识别为 `Krea2` 兼容，磁盘缓存版本升至 `v3` 自动刷新旧缓存。
- **验证（`LocalDraw/tests/test_second_pass.py`）**：
  - 覆盖 `LatentUpscaleBy(bicubic, 1.4)` 节点构图、`upscale` 路线回归与非法 `method` 校验，6/6 全部通过；Edge 无头浏览器端到端实测 `method` 切换、`denoise` 自动联动（Krea2 `0.5`、分块 `0.38` / `0.3`），全程 0 报错。

---

## 2026-11-25（五）：二采管线升级（修复 4K 整图崩坏、内置分块高清与 `qwen_image_vae` 优化）

- **问题修复根因**：一采为 2K（如 `2048×1152`）时再选 `2.0×` 会将 `4096×2304`（9.4MP）整图直接喂给原生 1K 级模型（如 Anima Delta Mix v6），触发 OOD 噪点马赛克崩坏；原 `VAEDecodeTiled` 阈值过低（`1.8MP`、`tile_size=512 / overlap=64`）易在 `qwen_image_vae` 下产生小块伪影；且 `comfyui.json` 的 `delta_matte_skin: true` 会在二采时暗中向提示词注入雾面皮肤前缀。
- **后端升级（`LocalDraw/src/comic_workflow/second_pass.py`）**：
  - 新增 `mode` 参数（`auto` / `full` / `tiled`）与分模型整图舒适区配置（Anima `2.4MP` / `1024px` 块、Krea2/MOSS `2.6MP` / `1280px` 块、SDXL `2.0MP` / `1024px` 块）；Anima 默认二采步数由 `18` 提至 `24`，Krea2 由 `6` 提至 `8`。
  - 内置无第三方插件依赖的**分块高清二采（`tiled`）**：先生成目标分辨率底图（`≥1.5×` 走 `RealESRGAN_x4plus_anime_6B` + `lanczos`，其余 `lanczos`），经 `compute_tiles()` 切分为带 `192px` 重叠的原生甜点区区块，逐块在 ComfyUI 原生 `VAEDecode` 下采样，最后经 `blend_tiles()` 双向余弦羽化无缝拼接。
  - 将整图模式 `VAEDecodeTiled` 阈值从 `1.8MP` 提高至 **`4.5MP`** 且改为大块 `tile_size=1024, overlap=256`，彻底避免 2K–2.5K 整图触发小块解码伪影；`1.25×` 改用纯 `lanczos` 避免 GAN 锐化振铃；二采默认关闭隐藏的 `delta_matte_skin` 注入（`False`）。
- **前端升级（`LocalDraw/web/prompt.js`、`style.css`）**：
  - 新增「二采模式」下拉与实时目标尺寸/舒适区提示条（`.sp-size-hint`）：原图 `≥2MP` 时默认缩放自动选 `1.0×`，选到 `2.0×`（如 `4096×2304`）时实时显示「分块高清（约 15 块 × 1024px）」并自动将未手动编辑的 `denoise` 建议为 `0.3`；强行在超舒适区选「整图精修」时给出红色预警。
- **验证（`LocalDraw/tests/test_second_pass.py`）**：
  - 扩充至 6 组单元测试（覆盖 `4.5MP` 解码阈值、`1.25×` 跳过 GAN、`resolve_mode`、`compute_tiles`、`blend_tiles`、分块 `run()` 实跑 `2048×1152 @ 1.5× → 3072×1728`、禁用隐藏 `delta_matte_skin`），6/6 全部通过；Edge 无头浏览器按 `2048×1152` 真尺寸实测自动默认 `1.0×`、切 `2.0×` 显示 15 块分块高清与 `denoise=0.3`、强选整图触发预警，全程 0 报错。

---

## 2026-11-25（四）：新增二次采样（二采）二级面板与跨模型精绘管线

- **后端（`LocalDraw/src/comic_workflow/second_pass.py`、`server.py`）**：
  - 新增像素空间通用二采工作流 `second_pass.build_graph()`：以 `LoadImage` 读入一采原图，缩放 `> 1.0×` 时使用已安装的 `RealESRGAN_x4plus_anime_6B.pth` 超分并 `lanczos` 精准缩放到目标对齐尺寸（MOSS 16px 对齐，其余 8px 对齐），经目标模型专属 `VAEEncode` 送入 `KSampler` 按指定 `denoise` 和 `steps` 重绘；目标像素 ≥ 1.8MP 时自动切换 `VAEDecodeTiled`（`tile_size=512`）避免显存溢出。
  - 天然支持**同模型精绘与跨模型二采**（如 `Anima → Krea2`、`Krea2 → Anima`、`Anima → MOSS-Image`），禁止 Qwen 编辑模型作为二采底模；LoRA 按目标二采模型架构严格隔离，默认只继承兼容的底图 LoRA。
  - 新增动作接口 `second_pass`、`apply_second_pass`、`unapply_second_pass`：应用状态持久化在 `images/index.json["second_pass_applied"]` 中（分镜板模式同步更新该格 `selections`）。生成 **1 张**时自动应用，生成 **多张（2–8 张）**时等待用户手动选图应用；「取消应用」恢复一采原图且保留所有二采图。
- **前端（`LocalDraw/web/prompt.js`、`style.css`）**：
  - 右侧预览区右下角在有图时显示「二采」入口按钮（使用 `div.gallery-footer` 避免与侧栏 `nav` 样式冲突）。
  - 点击「二采」后右侧图库保持原位不动，左侧切入简洁的二采二级面板（`.prompt-second-pass`），包含：左上角「← 返回」按钮、模型、缩放大小、重绘幅度、采样步数、种子（留空随机）、张数、兼容 LoRA 搜索与权重控制及可折叠提示词微调；按 `Esc` 优先关闭 LoRA 搜索下拉，再次 `Esc` 返回主界面。
  - 正常图库中已应用的二采图直接原位替换显示，附带「已应用二采」标记、「取消应用」按钮和「查看一采原图」链接，复制与下载均作用于当前展示图。
- **测试与验证（`LocalDraw/tests/test_second_pass.py`）**：
  - 新增 4 组离线单元测试，覆盖跨模型构图（`Anima → Krea2`、16px MOSS 对齐、`1.0×` 跳过超分、`≥1.8MP` 自动 `VAEDecodeTiled`）、LoRA 架构隔离与非法参数校验、单张自动应用与取消应用、多张等待手选与递增种子；`test_second_pass.py`（4/4）、`test_quick_sample.py` 与 `test_http.py` 全部通过。
  - 使用 Edge 无头浏览器连接真实 `LocalDraw.exe --no-browser` 端到端实测：右下角「二采」位置、进入/退出二级面板时右侧预览区坐标零偏移（`768, 86, 648×792`）、切换 Krea2 自动更新默认步数/重绘幅度、LoRA 搜索 `Esc` 分层关闭、手选候选图应用、`Esc` 与「← 返回」退出及「取消应用」恢复原图，全程 0 报错。

---

## 2026-11-25（三）：提示词出图增加可选 Seed 输入框

- **前端（`LocalDraw/web/prompt.js`、`studio-home.js`）**：在「提示词出图」与分镜出图板参数区新增「种子（留空随机）」数字输入框（范围 `0`–`9007199254740991`），支持草稿自动保存、底部提交摘要实时回显、非法值拦截，以及夜间队列设置预览显示已保存种子。
- **后端（`LocalDraw/src/comic_workflow/quick_sample.py`、`night_queue.py`）**：`prepare_sample()` 校验并持久化可选 `seed`；留空时不传入 `seed`（沿用 `comfyui.run_batch` 每次随机生成）；填写整数后，多张生成按 `seed, seed+1, ...` 顺序递增，并同步支持夜间任务列表。
- **测试（`LocalDraw/tests/test_quick_sample.py`）**：新增 `test_optional_seed_defaults_to_random_and_increments_when_set`，覆盖留空随机、多张递增与非法输入校验；Edge 无头浏览器实测输入框交互与摘要校验无报错。

---

## 2026-11-25（二）：冷启动与「提示词出图」加载性能优化

### 根因定位
- **冷启动慢**：`desktop_update.py` 启动前同步阻塞等待 GitHub 更新检查（最多 4 秒）；`stop_idle()`、`startup.ready()` 与 `model.status()` / `strata_model.status()` 对未监听的本机端口直接发 HTTP 请求，在 Windows 上每次触发 1–2 秒 TCP SYN 重传；`StudioLauncher.cs` 串行先初始化 WebView2 再启动 Python 后台。
- **进「提示词出图」慢**：`lora_inspection.py` 无磁盘缓存，冷启动首次扫描全部 2,642 个 `.safetensors` 文件头耗时 **4.74 秒**；`/api/characters`（1,761 角色 = 10.45 MB）与 `/api/drawing-options`（2,642 LoRA = 7.5 MB）单次未压缩传输高达 **18 MB**；前端 `prompt.js` 与 `loras.js` 同步构建 **5 万+ 个 DOM 节点**，并在 `populateOwner` 中产生 2,642 × 1,761 ≈ **465 万次**主线程循环。

### 优化改动
- **冷启动并行与快速端口探测**：
  - `LocalDraw/scripts/desktop_update.py`：移除 `check.wait(timeout=4)` 同步等待，改为纯后台检查更新；`stop_idle()` 先用 `80 ms` TCP 探测本机端口，未监听立即跳过。
  - `LocalDraw/src/comic_workflow/startup.py`：`ready()` 增加 `80 ms` 端口快检并将启动轮询间隔从 `250 ms` 缩短至 `60 ms`。
  - `LocalDraw/src/comic_workflow/model.py`、`strata_model.py`：`status()` 探测未启动模型端口前增加 `60 ms` TCP 快检。
  - `LocalDraw/scripts/windows/StudioLauncher.cs`：将 `StudioLauncher.StartServer()` 提前至 `CoreWebView2Environment.CreateAsync()` 之前并行执行，并已用 `build_launcher.ps1` 重新编译 `LocalDraw/LocalDraw.exe`。
- **后端持久缓存、接口瘦身与 Gzip**：
  - `LocalDraw/src/comic_workflow/lora_inspection.py`：新增持久磁盘缓存 `LocalDraw/data/lora-inspection-cache.json`（按文件大小与 mtime/ctime 及底模自动失效，避免冷启动重复扫盘）。
  - `LocalDraw/src/comic_workflow/characters.py`：给 `CharacterLibrary.listing()` 与 `searchable_listing()` 增加基于卡片文件签名的内存缓存，保存时自动失效。
  - `LocalDraw/src/comic_workflow/server.py`：`Handler.send()` 对大于 4 KB 且客户端支持 `gzip` 的文本/JSON 响应启用快速压缩（level 1）；`/api/drawing-options` 精简 `lora_labels` 冗余大字段；`/api/characters?compact=1` 精简非外观大块 `source` 元数据。
- **前端懒渲染（消除 5 万无用 DOM 与 O(N×M) 循环）**：
  - `LocalDraw/web/loras.js`：用 `Map` 将 `populateOwner` 改为 O(1) 查询，`bulkOwner` 改为懒加载；全部 LoRA 行改为轻量状态代理，仅在被勾选或进入当前分页时 `mount()` 创建 DOM。
  - `LocalDraw/web/prompt.js`：改为请求 `/api/characters?compact=1`，角色列表改为按需 `mount()`（顺带修复刷新角色列表时残留的旧字段引用），`defaultPrompt` 改为按需计算。

### 实测前后对比
| 指标 | 优化前 | 优化后 |
|---|---|---|
| `LocalDraw.exe --no-browser` 冷启动 | 4,734 ms（另加串行等待 WebView2） | **728–767 ms**（且与 WebView2 并行） |
| `/api/status`（语言模型未启动） | 1,026 ms | **105 ms** |
| `/api/drawing-options` 传输体积 | 7,485 KB | **714 KB**（gzip） |
| `/api/characters` 传输体积 | 10,451 KB | **570 KB**（compact + gzip） |
| Edge 打开首页至可交互 | — | **203 ms** |
| 冷启动后点击「开始画一张图」→ 提示词页就绪 | 十余秒卡顿（58,000+ DOM 节点） | **1,375 ms**（仅 **420** 个 DOM 节点） |

- 回归验证：`LocalDraw.exe --self-test` 通过；`test_http.py`、`test_quick_sample.py`、`test_lora_binding.py`、`test_moss_lora_inspection.py`、`test_ui_performance.py`、`test_desktop_release.py` 全部 55 个用例 100% 通过；Edge 实测搜索并勾选角色“一之濑帆波”即时懒挂载。

---

## 2026-11-25：代码库深度精简与单一主源码归整

### 1. 目录与源码归整（消除双份源码）
- 确认 `LocalDraw.lnk` 指向的 [`LocalDraw/`](../LocalDraw/) 为唯一主源码与桌面运行目录。
- 将根目录过时的旧副本 `src/`、`scripts/`（253 个文件）、`tests/`（157 个文件）、`schemas/`、`workflows/` 整体归档至 `archive/root-legacy-2026-11-25/`。
- 根目录共享资产 `models/`、`data/`、`runtime/`、`.venv/`、`.comfy-venv/`、`config/`、`docs/` 原样保留，确保 `LocalDraw/config/installed-runtime.json`、`settings.json`、`comfyui.json` 的外部路径引用完全不受影响。

### 2. 后端深度精简（`LocalDraw/src/comic_workflow/`：85 → 62 个模块）
只保留核心主流程（提示词出图、分镜/逐格导入出图、融合任务、夜间队列、角色/LoRA/排版导出与更新下载），移除 **23 个**死代码、已停用功能及旧 SDXL 历史实验模块：
- **彻底死代码 / 已停用功能**：`phased_drawing.py`、`sudowrite.py`、`project_translation.py`、`automatic.py`、`batch_drawing.py`、`reviewed_drawing.py`、`review_statistics.py`。
- **旧 SDXL 试画/局部修图/画风对照**：`drawing_tools.py`、`repair_masks.py`、`art_presets.py`、`comparison_models.py`。
- **旧 SDXL 多人区域/手部实验/IP-Adapter/Hires**：`bv_regional.py`、`native_regional.py`、`regional_lora.py`、`regional_prompt.py`、`interaction_masks.py`、`interactions.py`、`guided_hand.py`、`fooocus_guided_hand.py`、`hand_detail.py`、`hires.py`、`person_mask.py`、`reference_images.py`。
- **源码内非代码数据清理**：删除混在源码中的研究数据目录 `LocalDraw/src/comic_workflow/data/`（含 259 KB `hf-gap-search.json`）。
- **核心模块瘦身与迁移**：
  - 将 6 行公共函数 `story_fingerprint()` 从 `batch_drawing.py` 迁至 `engine.py`，更新 6 处调用方。
  - 将 `preserve_source_lettering()` 从 `automatic.py` 内联至唯一调用方 `storyboard_tools.py`。
  - `comfyui.py`（1077 → 882 行）：移除 SDXL 实验自定义节点加载与旧区域/手部细化/参考图/局部修复/Hires 管线，保留 Anima Delta v6、Anima Edit v2 一致性参考、Krea2、MOSS、Qwen Edit 与标准 LoRA 出图。
  - `server.py`（1245 → 1162 行）：移除已废弃路由（`sudowrite_*`、`automatic`、`small_batch`、`drawing_pilot`、`local_repair`、`preview_art_preset`、`translate_project_english`、`/api/art-comparison`、`/api/art-image`、`/api/hand-guide`、`/api/review-statistics`），新增静态字体路由 `/fonts/manga-font.json.gz`。
  - `lora_binding.py`（225 → 64 行）：仅保留角色 LoRA 绑定与按格路由逻辑。
  - `quick_sample.py`（449 → 382 行）：移除 SDXL 分区/参考图/手部增强与旧三模型对照校验。
  - `creative.py`（537 → 389 行）：移除已废弃的 `review_image()` 自动审图逻辑。
  - `framing.py`（188 → 67 行）：仅保留分镜校验与镜头词拼接（`FRAMING`、`source_text`、`validate_framing`、`apply_camera`）。
  - `drawing_settings.py`：移除旧 `art_presets` 强校验（历史 `drawing-settings.json` 仍可兼容加载）。

### 3. 前端与脚本瘦身
- **`LocalDraw/web/drawing.js`（3,007 KB → 80 KB，缩小 97.3%）**：将原本硬编码在 JS 中的 2.98 MB Base64 压缩矢量字库拆分为独立静态资源 `LocalDraw/web/fonts/manga-font.json.gz`（2.18 MB，含 15,368 个字形），改为排版预览/导出时按需 `fetch` 加载，功能保持 100% 一致。
- **清理前端废弃控件**：移除 `drawing.js` 中的框选局部修复弹窗、画风预设下拉、审核统计折叠区、少量试画与画风对照入口；移除 `prompt.js` 中的 SDXL 分区绘制、角色参考图强度、自动手部增强开关及框选修复按钮；移除 `studio-home.js` 队列预览中的手部增强字段。
- **脚本与测试归档**：
  - `LocalDraw/scripts/` 从 198 个顶层文件精简至 16 个核心构建/运行/自检脚本 + `windows/` 启动器源码，其余 183 个一次性实验/GPU 验证脚本及 `vendor/` 移至 `archive/legacy-localdraw-scripts-2026-11-25/`。
  - `LocalDraw/tests/` 从 85 个测试文件精简至 60 个核心测试文件，25 个已裁减模块/脚本的旧测试移至 `archive/legacy-localdraw-tests-2026-11-25/`，并清理了 6 个保留测试文件中针对已裁减功能的旧断言；在 `test_http.py` 补充了 `/fonts/manga-font.json.gz` 静态资源与解压校验测试。

### 4. 文档体系重构
- 将原 51 KB 倒序流水账式根目录 `README.md` 备份至 `archive/notes/README-legacy-2026-11-25.md`。
- 重写根目录 [`README.md`](../README.md) 为简洁总览与文档索引入口，建立本 `docs/CHANGELOG.md` 并与 [`LocalDraw/README.md`](../LocalDraw/README.md) 互链。

### 5. 验证结果与 EXE 启动链修复
- **EXE 启动链补全与实测**：核查 `LocalDraw/LocalDraw.exe` → `start_studio.py` 启动链后，恢复了 `LocalDraw/scripts/desktop_update.py`、`desktop_release.py` 及对应单测 `LocalDraw/tests/test_desktop_release.py`（`LocalDraw/scripts/` 保留 16 个顶层核心脚本 + `windows/`，保留单测 60 个）。
- **实测 `LocalDraw/LocalDraw.exe`**：
  - `LocalDraw.exe --self-test` 退出码 `0`。
  - `LocalDraw.exe --no-browser` 退出码 `0`，成功拉起 `F:\ChatGPT\local_draw\LocalDraw` 后端服务（端口 `18765`），`GET /`、`/drawing.js`（80 KB）、`/prompt.js`、`/fonts/manga-font.json.gz`（200，可解压 15,368 字形）、`/api/bootstrap`、`/api/drawing-options` 均返回 `200`，并通过 `desktop_idle_shutdown` 正常释放。
  - WebView2 首页加载渲染零 JS 报错（`"errors": []`；`DesktopUiTests.cs` 旧断言要求 3 个首页入口而当前界面有 4 个，属历史脚本断言未更新）。
- **模块与语法**：全部 61 个后端业务模块 `import` 无报错；19 个前端 `.js` 文件 `node --check` 零语法错误。
- **离线单测**：`LocalDraw/tests/` 60 个保留离线测试文件中 **56 个通过**（含 `test_desktop_release.py`、`test_wardrobe_refresh.py` 与新增字体路由校验的 `test_http.py`），4 个为与本次精简无关的历史/环境用例失败（`test_startup.py` 期望 `.venv` 位于 `LocalDraw/` 内、`test_output_language.py` 需连接本机运行中的语言模型端口、`test_draft_lifecycle.py` 断言旧版立即卸载行为、`test_prompt_sessions.py` 遍历 `MODELS` 时未给 `qwen_edit` 传入固定参考图）。

---

## 历史里程碑摘要（2026-09 ~ 2026-11）

详细历史记录见 [`archive/notes/README-legacy-2026-11-25.md`](../archive/notes/README-legacy-2026-11-25.md)：

- **2026-11-20**：桌面版整合进 `LocalDraw/`，支持直接导入每格提示词、分镜出图板、融合任务、绘图模型预加载与自动更新检测；旧桌面版与 22 个 `.bat` 入口归档至 `archive/`。
- **2026-11-05**：确立默认创作视角为 **POV 第一人称视角**（不使用双人构图）。
- **2026-10-24**：接入 **Krea2 Turbo（BF16/FP8）** 与 **MOSS-Image SFT（BF16）** 高精度出图，切换至 `runtime/ComfyUI-KreaMoss`。
- **2026-10-22 ~ 10-23**：上线开始页与夜间任务列表时间轴、暂停任务动态改每格数量、草稿自动保存。
- **2026-10-18**：默认绘图模型切换为 **Anima Delta Mix v6**，支持 Anima Edit v2 固定参考图、第三阶段本地 Qwen 填词、完整剧本 JSON 导入；确立默认气泡语言为简体中文、绘图提示词为英文。
- **2026-10-16**：默认每格候选改为 2 张，正式停用自动 AI 审核与补画，上线外部提示词 JSON 导出/导入与任务暂停/取消控制。
