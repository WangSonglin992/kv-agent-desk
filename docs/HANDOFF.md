# KV Agent Desk 工作区交接

这份文档是新工作区的入口。当前实现以公开仓库 `WangSonglin992/kv-agent-desk` 为准；历史聊天只作为背景，不代替源码、测试和发布记录。本交接按 2026-10-27 可见的代码整理。

## 目前状态

- 公共基线是 commit `21ca832`、应用版本 0.6.2。它修复了 Codex 打包环境与 Windows Explorer 访问不同 AppData 目录的问题，首次运行会将旧数据迁移到用户主目录下的 `.kv-agent-desk` 并保留备份。
- 用户现在要求把应用改为常规 Windows 安装器：默认安装到 `D:\APP\KV-Agent-Desk`，首次安装默认创建桌面快捷方式并启用自启动，复装更新使用注册的安装位置，同时保留应用外的本地历史、套餐资料和自定义样式。
- 本次安装器实现、构建、安装及交接验收尚在进行中。此版本中仍可见的旧 per-user 安装行为，不代表新安装器已经发布。完成后应在本节写入实际版本、提交、发布链接、安装目录和现场验收结果。
- 用户计划转到新工作区继续维护；后续功能修改在新工作区构建公开 release，安装程序负责更新目标安装目录。实际安装登记位置应从应用自己的 HKCU 登记读取，不能靠旧工作区或源码目录推断。
- 安装身份的正本为 `%USERPROFILE%\.kv-agent-desk\installation.ini`（UTF-16 BOM，`[Installation]` 节）。`InstallDir` 保存经校验的绝对程序目录；`DesktopShortcut` 与 `InstallerManaged` 保存 0/1 选项。HKCU `Software\KV-Agent-Desk` 是镜像/回退，因此更换开发工作区或打包宿主后安装身份仍保持一致。卸载可移除此登记文件和应用拥有的注册表镜像；必须保留同目录中的其他用户数据。

## 产品范围和运行边界

KV Agent Desk 是 Windows 本地运行的 KV / Trofeo Vision 9.16 USB LCD Agent 面板。控制页绑定 loopback；画面由同一渲染器输出到本机预览和屏幕。默认打包应用需要 Windows 10/11 x64、Python 3.12 x64 构建环境、当前用户的 WinUSB 设备接口和 WebView2 Evergreen Runtime。运行发布版不要求安装 Python；需使用 Python 的是本地源码构建流程。

屏幕接口 VID:PID 为 `0416:5408`。逻辑画布为 1920 × 480，9.16 常见传输高度为 462；硬件复用相同 USB ID 的其他屏幕不一定兼容。旋转、画面亮度和 USB 输出设置保存在本地；亮度通过像素调光，屏幕显示与 Thermalright TRCC 对同一 USB 接口互斥。

任务面板显示摘要，不显示任务标题或计划步骤。任务数、Token、额度无法可靠读取时遵循来源未知/最后可信值策略，不用未知额度冒充实际用量。Antigravity 的累计用量跨会话窗口，不能当作每日 Token；Gemini 5 小时/周额度共享，旧逐模型 Claude/GPT 额度不应硬套。套餐名称、规格、到期日期是与自动数据分开的手动资料。

## 来源适配器和刷新边界

| 适配器 | 读取来源 | 数据和注意点 |
| --- | --- | --- |
| Codex | 当前用户本机 Codex session JSONL | 按事件时间、会话及日界线汇总进行中父级任务、当日 Token 和本地已有的额度窗口；JSONL 增量读取并有限回看。读取错误时由保留层留住可信旧值。不会调用模型 API，也不读取认证凭据。 |
| ZCode | 本机 SQLite 会话库 | 只读打开，通过 session、turn/tool 生命周期和 session_input 汇总近期活跃任务及请求级 Token。数据库以最近 36 小时为观察范围，并限制扫描到最多 128 个近期会话；截断意味着不能把部分计数冒充精确总数。额度仅针对用户已选择的官方 Coding Plan，来源为允许清单中的官方 HTTPS API。 |
| Antigravity | 已运行的本机 language server RPC | 约每 2 秒查询排除子轨迹的父级活动摘要；约每 15 秒后台读取 `includeMessages=false` 的 Generator Metadata，只保留数值用量；约每 60 秒独立更新 Gemini shared quota 摘要。CSRF 和本地端口信息只短暂留在内存，请求不读消息正文。没有可靠来源时保留未知或最后可信结果。 |
| DeepSeek 账户 | `DeepSeek_public` / `DeepSeek_personal` 环境变量及 DeepSeek 官方余额接口 | 两个本机余额槽位独立刷新，至少 60 秒间隔、HTTPS GET；不请求模型推理。变量名是公开接口约定，实际值只能留在用户自己的 Windows 环境里。 |

适配器实现位于 `agent_dashboard/adapters/`，DeepSeek collector 位于 `agent_dashboard/accounts/`。当前字段契约在 `docs/EXTENDING.md`，状态的过期和合并由 `retention.py` 处理。新增来源应只用可复现生命周期/计数，未知值就保留未知；测试应 mock 文件、数据库和网络接口，不能触发真实账户请求。

## LCD 样式和编辑器

三套内置样式为 `classic`、`aurora` 和 `paper`。用户选择内置样式开始编辑时创建自定义副本。画布样式以版本 1 JSON profile 存储，顶层限定为布局、颜色、画布几何和可选 `elements`；当前画布 1920 × 480。模板允许移动元素和设置字体外观，但不能改文字内容、业务数据、绑定或资源路径。最多保存 32 个自定义 profile，单文件最大 64 KiB。

渲染图元的固定语义 ID 位于 `ui_elements.py`；选择框、父子卡片移动、对齐、分布、撤销和草稿在 `layout_editor.py` / `ui_scene.py`。字体 ID 是本机不透明标识，不随模板携带字体文件。修改模板 schema 时必须保持现有 JSON 导入/导出兼容性，并更新 `tests/test_ui_profiles.py`、`tests/test_editor_render.py` 及 `docs/UI-TEMPLATES.md`。

完整界面字段、JSON schema 和多选操作说明见 [UI-TEMPLATES.md](UI-TEMPLATES.md)。套餐资料保存在独立板块，不能在模板里伪造 Agent 状态或更改显示内容。

## 本地数据、迁移与隐私

Windows 发布版规范数据位置是 `%USERPROFILE%\.kv-agent-desk`，与安装程序位置分离。普通用户 AppData 在打包宿主中可能被重定向；0.6.2 已将状态路径移出该重定向范围，并固定让桌面和 Run 启动参数携带同一数据目录。

首次迁移读取固定白名单：设置、订阅资料、Agent 最后可信快照、余额快照、最后画面、画面元数据、更新元数据和合法 UI profile。迁移会先逐源备份，再原子且不覆盖地写入目标；旧源仍留在原位置。目标有成功 marker 后不重复迁移。profile/配置/缓存和迁移备份只在当前 Windows 用户本机，不进入 Git、Setup 文件、ZIP、日志、issue 或交接文档。不要查看或粘贴真实密钥、套餐资料、账户余额、任务文字或本地历史 JSON。

`docs/PRIVACY.md` 是网络请求与保留文件的公开说明。安装器卸载只能移除程序文件和应用拥有的快捷方式/自启动登记；必须保留用户历史和样式目录，不执行“清理本地数据”。

## 安装器工作线

本次安装器需求和阶段边界：

- 安装向导目标：默认 `D:\APP\KV-Agent-Desk`，存在路径选择页并创建产品子目录。
- 安装、更新、静默更新和卸载均以 Setup 登记的真实 `InstallDir` 为准，不能写死或恢复到旧 per-user 程序目录。
- 首次安装中“创建桌面快捷方式”“开机自启动”默认勾选；复装时沿用已安装路径，并在 Wizard 中尊重用户取消任务的选择。
- 编译器固定为 Inno Setup 7.1.0 x64（release tag `is-7_1_0`）；CI 必须先通过官方 release asset verification 与 Authenticode 签名校验再执行编译。
- 用户数据目录在程序目录之外；复装只替换产品文件，卸载只移除程序和应用登记，两个操作都保留设置、手动套餐、历史缓存、自定义 profile 和备份。
- 0.6.2 updater 下载经过 SHA-256 验证的 EXE。后续安装器发布必须保持此旧客户端更新协议兼容，或使用清晰、已验证的迁移/升级分支；未经验证不要改写线上 update manifest。
- Setup 脚本/CI资产名、CLI 与 silent 安装退出码、首次安装/复装/任务选项/卸载是否保留文件、以及 D 盘实际验收 commit/tag 均待安装器完成后补齐；本节此前提出的内容是验收边界，不是完成证明。

建议检查路径：`agent_dashboard/installation.py`、`app.py` 的入口和 `startup_command`、`tools/create_update_manifest.py`、`.github/workflows/windows-release.yml`、`Build-Dashboard.ps1`、`Start-Dashboard.cmd` 以及安装器脚本。源码运行没有受信任的安装器权限，不允许通过本机控制页传入任意安装路径或命令。

## 主要源码地图

| 路径 | 作用 |
| --- | --- |
| `agent_dashboard/app.py` | 生命周期、控制页服务器、状态合并、启动/停止和更新接入。 |
| `agent_dashboard/adapters/` | Codex、ZCode、Antigravity 只读采集。 |
| `agent_dashboard/accounts/` | DeepSeek 官方余额读取和限频。 |
| `agent_dashboard/retention.py` | 最后可信数据与 stale 状态。 |
| `agent_dashboard/ui_profiles.py`, `ui_elements.py`, `layout_editor.py`, `ui_scene.py` | 样式校验、固定图元、预览编辑及渲染 scene。 |
| `agent_dashboard/render.py`, `display.py` | 画面和 WinUSB 传输。 |
| `agent_dashboard/settings.py`, `subscriptions.py`, `paths.py`, `storage_migration.py` | 本地偏好、手动套餐、用户目录和白名单迁移。 |
| `agent_dashboard/installation.py`, `updates.py` | 程序部署、Run/快捷方式与在线更新。 |
| `tools/check_public_tree.py` | 只扫描 Git 暂存索引，白名单之外的文件不会发布。 |
| `tools/create_update_manifest.py`, `tools/package_release.py` | 生成稳定更新清单与只含公开资产的 ZIP。 |
| `.github/workflows/windows-release.yml` | 干净 Windows runner、测试和 release 打包。 |

## 已知发布和验证记录

| 版本 | 公开摘要 |
| --- | --- |
| 0.3.0 | WebView2 托盘应用、Run 自启动、启动时先恢复画面。 |
| 0.3.1–0.4.3 | Localhost 控制、安全刷新、DeepSeek/ZCode/Antigravity 适配与只读额度查询逐步稳定。具体历史见 `CHANGELOG.md`。 |
| 0.4.4 | Antigravity 父级任务状态和累计 Token 来源。 |
| 0.5.0 | 可视元素编辑、Ctrl 多选、对齐分布、本机字体和 JSON 样式副本。 |
| 0.6.0 | per-user 安装骨架、自动更新清单、版本切换。 |
| 0.6.1 | 自启动明确绑定数据路径、失败读取重试、不以内存默认设置覆盖磁盘配置。 |
| 0.6.2 | 修复打包宿主 AppData 重定向，统一用户数据路径并迁移旧副本。 |

既有开发验收基线为 `python -m unittest discover -s tests -q` 发现 256 项，其中本机有 1 项因 symlink 权限跳过；另有 Windows Release CI 执行全部测试、公开索引扫描、source/demo 和打包 EXE smoke。此数字是 0.6.2 的已知记录，不代表安装器 0.7.0 已验证。Windows 实际 USB 显示、安装向导和卸载必须分别记录现场证据；模拟 `Run` 启动不等同于重启整台电脑。

## 新工作区续接

1. 从最新公开 `main` clone 源码，不复制原 `.venv`；虚拟环境内的入口脚本记录绝对路径，移动目录后必须重建。
2. 开始修改前读本文件、`WORKSPACE-MIGRATION.md`、README、CHANGELOG、PRIVACY 和安装器文档，然后确认 `git status`、最新版本 tag、安装器路径登记规则。
3. 复制项目源码本身，不复制任何 `settings.json`、`subscriptions.json`、`ui-profiles/`、账户缓存、截图、余额、key、`.env` 或真实用户测试夹具。
4. 用仓库脚本从干净 `.venv` 构建。若要更新已安装版，生成干净 EXE/Setup，核对更新清单、SHA-256、Git 隐私扫描，再针对 Setup 的已登记目录进行复装。不要把旧 workspace 的 `dist/` 或 local runtime 二进制当作正式构建。
5. 完成新安装器后，回填“安装器工作线”中的实际文件、登记路径、构建版本、资产名、首次安装/复装/关闭任务/卸载/历史保留结果和 commit/tag。

新工作区的具体克隆、构建和更新命令见 [WORKSPACE-MIGRATION.md](WORKSPACE-MIGRATION.md)。本地历史自然存在于应用数据目录；源代码工作区不是它的备份位置。
