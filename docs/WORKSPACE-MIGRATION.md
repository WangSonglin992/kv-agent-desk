# 在新工作区继续维护 KV Agent Desk

这份文档面向工作目录改变或历史对话无法恢复时的开发者。程序的用户资料不随源码工作区迁移；同一台电脑上的新版从独立数据目录载入已有记录。

## 源码与用户数据是两回事

- Git 源码是公开仓库 `https://github.com/WangSonglin992/kv-agent-desk`，当前公开交接基线是 commit `21ca832`、release `v0.6.2`。新安装器将是用户本次任务后的新增交付；克隆之后先看最新 `main`、tag、`CHANGELOG.md` 和 [HANDOFF.md](HANDOFF.md)，再决定后续基线。
- 应用运行资料在 `%USERPROFILE%\.kv-agent-desk`：设置、手动套餐资料、UI profile、状态/额度缓存和截图。Windows 下其父路径有意放在用户主目录，因为打包宿主的 AppData 重定向曾让打包启动和 Explorer 启动读取到不同文件。
- 迁移代码会在该数据目录内保留 `migration-backups/`，旧 LocalAppData 和当前用户 Codex Windows 隔离目录的原始文件也保留在旧位置。不要从这些目录复制任何文件进 Git。
- 已安装程序与源码目录分离。用户要求的安装目标为 `D:\APP\KV-Agent-Desk`。安装登记正本是 `%USERPROFILE%\.kv-agent-desk\installation.ini`（UTF-16 BOM，`[Installation]` 中的 `InstallDir`、`DesktopShortcut`、`InstallerManaged`）；HKCU `Software\KV-Agent-Desk` 是镜像/回退。换 workspace 后不能从当前源码路径猜安装位置。Setup 编译器固定为 Inno Setup 7.1.0 x64、release tag `is-7_1_0`；CI 须验证官方 attestation 和 Authenticode 后再编译。

## 创建新工作区

在 PowerShell 选一个新的空目录。以下用当前用户 Documents 下的工作区作例子；可换成本机其它目录：

```powershell
$UserProfilePath = [Environment]::GetFolderPath('UserProfile')
$NewWorkspacePath = Join-Path $UserProfilePath 'Documents\kv-agent-desk'
New-Item -ItemType Directory -Path (Split-Path -Parent $NewWorkspacePath) -Force | Out-Null
git clone https://github.com/WangSonglin992/kv-agent-desk.git $NewWorkspacePath
Set-Location -LiteralPath $NewWorkspacePath
git status --short --branch
git log -1 --oneline
```

如果仓库需要继续处理尚未发布的提交，应先确认该提交已经推送到公开仓库，再在新工作区获取对应分支或提交。不要覆盖新工作区中已有的个人修改；clone 应使用空目标路径。

打开 [HANDOFF.md](HANDOFF.md)，然后从 `README.md` 的本地构建步骤开始。新目录必须新建 `.venv`：虚拟环境的 Python、PyInstaller 和 Windows 路径常带原工作区绝对路径，直接复制旧 `.venv` 容易启动失败。项目构建脚本会在新工作区创建 Python 3.12 环境，安装锁定的 build 依赖、运行单元测试并生成 `dist\KV-Agent-Desk.exe`。

当前公开版本的源码打包检查为：

```powershell
powershell -ExecutionPolicy Bypass -File .\Build-Dashboard.ps1
```

如需本机便携运行可从构建产物显式使用 `--portable`；发布版安装和用户日常启动使用安装器或系统中的 KV Agent Desk 快捷方式。请先阅读 `docs/RELEASING.md` 与最终的安装器 README 说明。此文档写于安装器变更前，若新 release 添加构建脚本、命令或登记名，应以该版本发布说明更新本节。

## 修改和检查

先确定代码要改在哪一层：

- 来源解析和只读限制：`agent_dashboard/adapters/` 或 `agent_dashboard/accounts/`。
- 最后可信值、过期提示：`agent_dashboard/retention.py`、`app.py`。
- LCD 样式、图元注册和编辑器：`render.py`、`ui_elements.py`、`ui_profiles.py`、`ui_scene.py`、`layout_editor.py`、`ui_fonts.py`。
- 屏幕写入：`agent_dashboard/display.py`。不要在普通样式修改中改变设备协议。
- 安装路径、INI/HKCU 安装登记、桌面快捷方式和 Run key：只从安装器/应用登记读取；不要用 workspace 绝对路径作为安装身份或硬编码某位用户目录。
- 稳定更新校验与 release manifest：`agent_dashboard/updates.py`、`tools/create_update_manifest.py` 及 GitHub Actions 工作流。

提交前至少确认 `git status`，阅读修改差异，留意测试跳过原因与公共文件边界。当前仓库可在 Windows Python 3.12 环境运行：

```powershell
python -m unittest discover -s tests -q
python tools/check_public_tree.py
```

公开源码保护检查读取 Git index，所以先 stage 要提交的源文件后再运行它。禁止 stage `.env`、本地 `settings.json`、`subscriptions.json`、`ui-profiles/`、账户缓存、截屏、实际日志、runtime 下载目录和私人工作记录。使用 `gitleaks` 或等效凭据扫描时只扫描暂存的公开代码，不读、导出或复制应用资料目录。

用本机合成配置验证迁移与安装。测试源/目标目录必须是临时目录且只放合成文件；不要在测试中对 `%USERPROFILE%\.kv-agent-desk` 实施覆盖安装、清空目录或卸载清理。

## 发布和更新

发布 Windows release 时，采用仓库 GitHub Actions 的干净 Windows 构建，不将本机 `dist\` 和 `.venv` 作为发布内容。CI 会运行测试、源码公共清单检查和 demo/package smoke，再生成 EXE、Setup、portable ZIP、SHA-256 侧车和更新 manifest。Setup 编译前验证官方 Inno Setup 7.1.0 x64 release 资产与签名。公开 release 的发布步骤见 `docs/RELEASING.md`。

本次安装器工作的验收要求是：

1. Setup 第一次默认显示 `D:\APP\KV-Agent-Desk`，自动创建产品子目录和开始菜单项。
2. “桌面快捷方式”和“开机自启动”默认勾选；取消后安装/重装也尊重选择。
3. 下一次 Setup 升级默认读取登记的安装路径，向该目录覆盖程序文件并刷新应用版本/卸载记录，不恢复到旧 per-user AppData 路径。
4. 使用 Explorer 分别启动桌面快捷方式、自启动命令和 Setup 后的 EXE，确认它们读同一份 `%USERPROFILE%\.kv-agent-desk`。
5. 在安装、同路径复装及卸载后，核对原始设置语义、订阅资料、全部 UI profile 与本机历史仍保留。卸载不删除用户资料目录及迁移备份。
6. 验证程序与安装器均由干净 CI 构建，公开清单只允许源代码、合成样例、公开文档与明确列出的五个应用资产。

安装器实施仍在本次工作中；上面的第 1–6 项是验收线，不是已完成状态。详见 [HANDOFF.md](HANDOFF.md) 的“安装器工作线”，最终验收证据需更新到交接文件和 README。登记 INI 移动 workspace 时不迁移或删除，旧用户数据不能混入源码 archive。

## 真实用户资料的边界

真实 Key 只应存在于用户自己的 Windows 环境变量或官方客户端安全存储里。真实套餐、余额、最近 Token/任务状态和截图不会被移入新源码目录、hand-off 文档或 GitHub release。新工作区的开发测试只使用 synthetic fixture，不需要查看 `.codex`、`.zcode`、Antigravity 客户端数据库、`%USERPROFILE%\.kv-agent-desk` 或安装目录中的真实内容。
