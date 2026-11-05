# 在新工作区继续维护 KV Agent Desk

此文用于工作目录迁移或历史对话不可恢复时快速接手。源码 workspace、已安装程序和用户运行数据是三份分离内容。不要复制运行资料来恢复开发上下文。

## 当前状态

- 截至 2026-11-05，正式版本为 `v0.8.0`，已安装到 `D:\APP\KV-Agent-Desk`。功能源码基线为 `912a5cd548bde1a0c9b3dc71c97e84b58bb59520`；之后的交接文档修订以最新 main 为准。
- 正式 CI `41975148889` 通过；手动 CI `41974753132` 运行 314 项测试、跳过 1 项，并通过资源嵌入、源/EXE smoke、安装选项四阶段和卸载保留测试。本机也验证了版本/EXE 哈希、配置与样式留存、所选样式、缓存画面、USB 连接和桌面/Run 登记。
- v0.8.0 新增三张 AI 娘三态动画图集，三个主题共用。原始参考图不进入仓库；插画许可与代码许可分开。旧失败 tag 保留，v0.7.4 无 Release；安装生命周期问题已在 v0.7.5 修复，不再是待办。

## Windows 设置和本地资料

- 已安装程序默认位置是 `D:\APP\KV-Agent-Desk`，当前版本记录在产品的每用户安装登记中。
- 安装身份的本地正本为 `%USERPROFILE%\.kv-agent-desk\installation.ini`，UTF-16 BOM、`[Installation]` 节，字段 `InstallDir`、`DesktopShortcut`、`InstallerManaged`。HKCU `Software\KV-Agent-Desk` 为镜像/回退。移动源码仓库或新建 workspace 不改变已安装路径。
- 数据库、设置、手动套餐、缓存、布局和 last frame 留在 `%USERPROFILE%\.kv-agent-desk`。首次迁移有 `migration-backups/`；Setup升级只替换程序，卸载只移除自身程序、快捷方式、自启及身份登记，不删其它用户数据。
- Setup 首次默认勾选桌面快捷方式和自启；复装保留上次安装任务选择。更新部署还应尊重用户在应用内保存的当前选项，不强制恢复默认值。
- 不把真实 settings、subscriptions、ui-profiles、余额/任务缓存、key、截图或runtime二进制复制到workspace、Setup payload、公开ZIP、日志或handoff。只用合成fixtures验证数据保留。

## 从新目录接手

先从公开 main 克隆到新的空目录。下面的路径是例子，可替换为自己的workspace路径：

```powershell
$UserProfilePath = [Environment]::GetFolderPath('UserProfile')
$WorkspacePath = Join-Path $UserProfilePath 'Documents\kv-agent-desk'
New-Item -ItemType Directory -Path (Split-Path -Parent $WorkspacePath) -Force | Out-Null
git clone https://github.com/WangSonglin992/kv-agent-desk.git $WorkspacePath
Set-Location -LiteralPath $WorkspacePath
git status --short --branch
git log -1 --oneline
```

打开 `docs/HANDOFF.md`、本文件、README、PRIVACY 与 RELEASING，核对最新 `__version__`、main commit 和 Release 标签。旧 `.venv` 内含原绝对workspace路径，不能拷入新工作区；也不要复制旧 `dist/`、Setup exe、`runtime/`、私有便笺或用户数据。

## 预览和构建

源码浏览器预览采用合成数据和唯一的临时 state directory；不会更新正式用户资料：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$DemoState = Join-Path $env:TEMP ('kv-agent-desk-preview-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m agent_dashboard --demo --browser-only --port 8767 --state-dir $DemoState
```

如果只需要 EXE，运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Build-Dashboard.ps1
```

要创建完整 Setup，只运行 `Build-Installer.ps1`。缺少 `.venv` 时创建 Python 3.12 环境，随后安装锁定依赖、运行完整单元测试、重建 EXE、stage public payload、再编译 Setup；本机现有 venv 不等于全新 CI 环境。不要先单独运行 Build-Dashboard 再运行 Build-Installer，否则会重复构建。`-SkipAppBuild` 仅是 CI 已有干净 exe/payload 时用的内部选项，不用于本机 Setup 构建。

Inno Setup 编译器固定7.1.0 x64，构建前校验官方 release asset attestation和Authenticode。CI 还检查 Git index 白名单、源程序/Setup smoke、SHA-256 和更新manifest。正式 release 使用 clean runner；本机 dist/setup 不能代替CI release。

## 安装源码地图

| 路径 | 作用 |
| --- | --- |
| `installer/KV-Agent-Desk.iss` | Inno wizard、默认 DAPP 目录、任务checkbox与安装/卸载入口。 |
| `Build-Installer.ps1` | 验证并运行 Inno Setup 安装器，默认重建完整EXE/payload。 |
| `tools/stage_installer_payload.py` | 只stage EXE、license和public runtime assets。 |
| `agent_dashboard/install_location.py` | 读取/验证 canonical INI 与 HKCU 镜像安装记录。 |
| `agent_dashboard/installer_lifecycle.py` | Setup prepare/finish/uninstall及ownership/error propagation。 |
| `agent_dashboard/installation.py` | 兼容旧EXE install、现有安装路径更新、shortcut维护。 |
| `tests/test_installer_lifecycle.py`, `test_install_location.py`, `test_install_test_safety.py` | 生命周期、登记值和TEMP/8.3测试路径保护。 |

finish失败不得被Setup吞成exit0。Shortcut只可删本产品owned target；uninstall不能递归删除DAPP根目录或canonical用户数据。若测试临时目录内有unknown file，应验证它被保留。

## 已接受的仪表盘规则

- 主屏三张 Agent 卡只展示任务数量，不展示任务标题和计划进度。没有可靠任务数/Token且没有可信历史时按要求显示0；额度未知仍表示未知。
- 任务数超过 3 时为红色忙碌状态。状态区使用三态 AI 娘，素材不可用时回退 spinner；官方 logo 与标题居中对齐。空闲用常规 1Hz 更新，工作/忙碌用快速刷新，不额外查询模型或账户接口。
- Antigravity只用Gemini共享5小时与周额度，不展示Claude/GPT额度组。额度/状态过期时保留最后可信画面并显示低调提示。
- DeepSeek占右侧紧凑双余额卡，上下排列。LiteLLM与课题组GLM没有可验证可用额度时不启用。ChatGPT套餐是独立手动资料；月付套餐需按月人工维护，代码不能自动滚动`expires_at`。
- 三套内置theme `classic`/`aurora`/`paper`；内置样式可存自定义copy、导入导出。所有注册的视觉element支持XY/方向键/多选/对齐/分布/字号/本机字体/颜色/bold/italic；JSON仅改外观，不改业务内容或数据binding。
- 启动先恢复last frame，再逐个刷新来源；默认LCD旋转180°、亮度软件调光。TRCC和本程序不能同时占有屏幕USB接口。

来源接口、刷新/限频和隐私字段见 `docs/EXTENDING.md`、`docs/UI-TEMPLATES.md`、`docs/PRIVACY.md`。只有源码可证明的间隔才应写进文档，数值不可从用户本机日志反推公开。

## 继续更新

在新 workspace 修改、用合成数据验证、更新三处版本号（`pyproject.toml`、`agent_dashboard/__init__.py`、Inno 默认值），再从干净 CI 发布新的 tag；不要重写已有 tag。随后运行正式 SHA-256 校验的 Setup，沿用登记目录与当前设置，核对缓存画面、所选样式和 USB。应用只替换程序，本地配置与样式留在用户主目录，不需要从旧 workspace 导入。

角色图集、映射和打包入口见 `docs/MASCOT-ANIMATIONS.md`；新增资源必须同步 wheel、PyInstaller 显式列表和公开文件白名单。现有 `status_indicator` ID 仍是 position-only 描述符，旧布局的偏移需要保持。OpenAI 剩余额度没有可靠来源时不接入，不能把费用统计当余额；ChatGPT 手填到期日不会自动续期。

当前验收和模块地图见 `docs/HANDOFF.md`。本轮没有执行 Windows 重启；不得把登记检查写成真实重启验证。真实凭据、套餐值、余额、task 内容、layout ID 和截图不读、不引用、不上传。
