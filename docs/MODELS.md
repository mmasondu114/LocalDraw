# 模型选择与显存说明

已阅读项目根目录 `archive/notes/comic_models_handoff.txt`。第一版仅选一套主力写作模型和一套绘图模型，不同时下载 Anima、Illustrious、图像编辑模型等所有候选。

## 写作：Qwen3.6-27B Q4_K_M

来源：[Unsloth GGUF](https://huggingface.co/unsloth/Qwen3.6-27B-GGUF)。沿用用户初选，并进行了本机阶段生成测试；不是依据参数量宣称漫画质量最优。Qwen3-32B、LongWriter-Zero-32B 没有在此项目做比较测试。

- 文件：`models/Qwen3.6-27B-Q4_K_M.gguf`。
- 大小：16,817,244,384 字节 = 16.817 GB ≈ 15.662 GiB。
- SHA-256：`5ed60d0af4650a854b1755bd392f9aef4872643dc25a254bc68043fa638392a0`。
- llama.cpp：迁移自此前验证过的 b11026 CUDA 13.4 运行包。
- 配置：16,384 token、单并发、关闭思考、Q8_0 K/V 缓存、Flash Attention、GPU 层 99、batch 512、ubatch 128。
- 每次启动都会核对完整文件哈希；仅同名或大小正确不能绕过校验。

GB 是十进制十亿字节，GiB 是 2^30 字节，MiB 是 2^20 字节。显卡报告 24,455 MiB 约等于 23.88 GiB，而不是 24,455 MB。文件大小也不等于运行显存。

显存大致包括权重、注意力 KV 缓存、循环/线性注意力状态、计算缓冲、CUDA 上下文与桌面其他进程。Qwen3.6 的[官方配置](https://huggingface.co/Qwen/Qwen3.6-27B/blob/main/config.json)包含全注意力层和线性注意力层，不能按全部 64 层都是普通 KV 缓存估算。以 16 个全注意力层、4 个 KV head、256 head_dim、16K token、K/V 双份 Q8_0 为粗略估算，普通 KV 部分约 0.53 GiB；循环状态与检查点另计，实际以 llama.cpp 分配及显卡观测为准。Q8_0 的块缩放元数据也占空间。

这是估算公式，不是全部运行显存实测。完整实际速度、峰值和请求参数保存在 `data/test-results/`。官方上下文上限不是本机可用承诺；这里明确只配置和测试 16K，并在请求前用服务器 tokenizer 检查输入、输出预留与安全余量，超预算则拒绝提交。

## 绘图：Animagine XL 4.0 Opt

来源：[Cagliostro Labs 官方模型页](https://huggingface.co/cagliostrolab/animagine-xl-4.0)。选择 Opt 对应文件作为标准 SDXL 二次元基线，主要考虑标准节点、部署路径清晰，以及当前尚无指定角色 LoRA。它偏英文标签式输入，不能把中文中立描述原样当作最终适配；本项目由 Qwen 生成独立英文适配候选，确认后才交给 ComfyUI。模型可能混淆多人特征、手部和复杂空间关系，需要审图。

- 文件：`models/checkpoints/animagine-xl-4.0-opt.safetensors`。
- 大小：6,938,350,040 字节 = 6.938 GB ≈ 6.462 GiB。
- SHA-256：`6327eca98bfb6538dd7a4edce22484a1bbc57a8cff6b11d075d40da1afb847ac`，由官方仓库 LFS 元数据核对。
- 标准 CheckpointLoaderSimple 一体检查点，模型/CLIP/VAE 由检查点加载，未混入其他架构组件。
- 起始参数：Euler ancestral、28 步、CFG 5，按画格比例选择约 1MP 分辨率。每张单独生成，三张候选串行，避免三张 batch 同时占显存。
- 不在图中生成中文对白；文字单独排版。

Anima 官方当前有 Base/Aesthetic/Turbo，需要匹配的 Qwen 文本编码器与图像 VAE；这里未部署或做质量对比。Illustrious 在目标角色 LoRA 要求该底模时再评估。Qwen-Image-Edit-2511 量化、参考图控制和动漫超分尚未安装。角色/画风 LoRA 由用户下载，本项目提供标准 LoRA 加载入口，不做训练，未声明任何具体 LoRA 的兼容性或一致性已经验证。

许可证保留模型上游声明，权重不进 Git。主力模型选择可替换，不意味着这个组合对所有角色和画风最好。
