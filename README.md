# LocalDraw 本地漫画工作室

LocalDraw 是 Windows x64 桌面应用，支持创作漫画、编辑分镜，以及从每格提示词直接出图。

## 下载和安装

登录有仓库访问权限的 GitHub 账号，打开[最新发行版](https://github.com/mmasondu114/LocalDraw/releases/latest)。

- **首次安装**　把四个以 `.001` 至 `.004` 结尾的文件和 `Join-LocalDraw-Setup.cmd` 下载到同一个文件夹。双击命令文件，等它确认安装程序校验通过，再运行合并得到的 EXE，选择空目录安装。此包面向 Windows x64、RTX 4080 环境。首次出图需要在应用中点「下载模型」，或使用已安装的模型。
- **更新已有安装**　等当前任务结束后关闭工作室，将对应版本的 `Update.zip` 解压到含有 `LocalDraw.exe` 的目录并覆盖程序文件。重启电脑后打开工作室，以加载新的后台程序。此更新包适用于已安装 RTX 4080 完整版的电脑。

## 检测更新

点击窗口顶部的「检测更新」。工作室将当前版本与私有仓库的最新正式发行版比较。本机已登录的 GitHub CLI（在 PATH 中）或 `GH_TOKEN` 可用于查询。也可以在弹窗里填写此仓库具有 Contents 只读权限的 GitHub 令牌，令牌仅用于本次查询。「打开 GitHub 下载页」使用系统浏览器里的 GitHub 登录状态。

## 源码与运行环境

`src/comic_workflow` 是本地服务，`web` 是界面，`scripts/windows` 包含桌面启动器源代码。安装包提供配套的 Python、绘图运行时和桌面组件。可在已安装的运行环境中替换源码进行开发。开发环境使用 Python 3.12，通过 `PYTHONPATH=src` 运行对应测试。
