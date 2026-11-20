# 迁移记录

初始 Project 路径为 `C:\Users\mmaso\Documents\ChatGPT\local_draw`，检查时为空。先审查旧对话下的 `comic.py`、Schema、提示词、下载器、BAT、人工示例和十二项测试，然后复制可复用部分进行项目化改造，没有把旧代码直接当成完成品。

保留了旧程序中候选确认、Schema、页格编号、状态边界和导出思路。重构了服务、持久化、后台任务、界面、进程管理、ComfyUI 与选图排版；旧 BAT 的相对目录假设已全部替换。

中途用户暂停，复制被中断；恢复后重新清点目录和磁盘。用户清理后 C 盘约 250.5 GiB、E 盘约 680.8 GiB 可用。发现旧模型目录已出现大小完整的 Qwen 文件，核对用户指定 SHA-256 后复制到本项目；没有自动重新启动 Qwen 下载。旧文件未删除，先前已复制的分块保留。

旧 `.venv` 没有整体迁移。本项目新建 Python 3.12 环境安装锁定依赖；llama.cpp 运行文件整体复制到 `runtime/`，已通过本机真实生成确认可运行。模型启动不依赖旧对话目录。ComfyUI 原有两套环境缺少 PyTorch，新建本项目 `.comfy-venv` 安装 CUDA 版 PyTorch 和对应源码依赖，软件源路径仍使用 `E:\AI\ComfyUI`。

用户后续授权直接下载绘图模型，并要求先读 `archive/notes/comic_models_handoff.txt`。读完交接并核对官方模型资料后，仅下载 Animagine XL 4.0 Opt，一份权重，未下载所有候选；未安装角色 LoRA 或做训练。

原样例 Schema 迁移增加 `aspect_ratio` 和 `gaze`。旧样例的视线值明确标注“旧样例迁移，待人工细化”，不冒充模型生成的详细设定。正式新作品和四镜测试使用新 Schema。

当前 `.gitignore` 排除模型、运行二进制、虚拟环境和用户作品。源码、配置、模板、测试、工作流、中文文档可纳入 Git 管理。备份作品应单独复制整个 `data/projects`。
