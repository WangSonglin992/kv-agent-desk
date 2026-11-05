# KV Agent Desk 工作区交接

新工作区请先读本文件和 [WORKSPACE-MIGRATION.md](WORKSPACE-MIGRATION.md)。此文只记录当前产品结构、可信验收状态与未完成项；历史失败 run 的完整裁定留在当前开发机的 ignored 本地 ledger，不纳入公开源码历史。

## 当前状态

- 截至 2026-11-05，正式版本为 [v0.8.0](https://github.com/WangSonglin992/kv-agent-desk/releases/tag/v0.8.0)，功能源码基线为 `912a5cd548bde1a0c9b3dc71c97e84b58bb59520`。本机已用官方 SHA-256 校验包更新到 `D:\APP\KV-Agent-Desk`。
- 手动 CI `41974753132` 和正式发布 CI `41975148889` 均通过；手动 CI 运行 314 项测试、跳过 1 项，并验证三张动画图集确实嵌入 EXE、源/打包程序 smoke、四阶段安装选项记忆、卸载与数据保留。
- 本机验收确认程序版本与官方 EXE 哈希一致、配置指纹未变、全部自定义样式和当前选择保留、历史缓存仍在、启动恢复上次画面、USB 已连接。桌面快捷方式和 Run 登记均指向安装目录与同一用户数据目录。没有执行实际 Windows 重启，也没有公开用户资料或画面。
- 安装器在 v0.7.5 已修复 PowerShell 快捷方式收尾超时、Inno 吞掉完成阶段错误和短/长路径别名比较问题。使用原生 Inno 处理受信任快捷方式，失败退出码为 73；旧失败 tag 不重写，v0.7.4 没有 Release。

## 产品与运行边界

KV Agent Desk 是 Windows 本地 Agent 状态屏和控制页，支持 Codex、ZCode、Antigravity、两个 DeepSeek 余额槽位、套餐资料和三套 UI。控制页仅监听 loopback。未知或过期来源沿用未知/最近可信状态，不从标题或消息正文猜任务进度。运行数据和配置位于源码仓库外。

KV/Trofeo Vision 9.16 使用 WinUSB VID:PID `0416:5408`。逻辑画布 1920×480，常见传输高度 462；方向、软件亮度和 USB 输出单独管理。USB 接口由本程序独占，不能同时交给 TRCC。

## 已接受的 Agent 面板与视觉规则

- 主画面固定为 Codex、ZCode、Antigravity 三张 Agent 卡；任务只显示数量，不显示标题或计划/计划进度。无可靠任务数或 Token 且没有可信历史时按用户要求显示 `0`；额度未知仍保持未知。
- 任务数超过 3 个时标红为忙碌。v0.8.0 状态指示器使用 AI 娘：Codex 白发龙娘、ZCode 黑发持书形象、Antigravity 蓝紫猫耳形象。空闲、工作、忙碌分别使用不同帧组；空闲周期 4 秒并按默认 1Hz 推进，工作周期 2 秒、忙碌周期 0.65 秒，沿用快速刷新。unknown/offline 不播放假工作动画，素材不可用时回退原指示器。官方 logo 和状态小字保留。
- Antigravity 展示 Gemini 共享的 5 小时和周额度；Claude/GPT 独立额度组在此面板忽略。无数据或陈旧数据时保留最后可信值/画面并给低调检查提示。
- DeepSeek 只显示右侧上下两张紧凑余额卡。无法可靠验证可用额度的 LiteLLM / 课题组 GLM 槽位不接入。ChatGPT 套餐规格/到期日保存在独立手填资料中；用户的月付套餐按月检查并手动更新，程序不会自动延长 `expires_at`。这与实时 5 小时/周额度读取无关。
- ChatGPT 确实没有 5 小时限额时，由控制页的 `codex_no_five_hour_limit` 个人设置将该窗口固定显示为 100%；不要用 API 余额推算它。OpenAI API 剩余额度源尚未接入：只读测试能认证 API Key，但费用统计与旧余额接口没有提供可用余额，因此没有添加“三个账户选两个”的配置，也不能用 Costs 费用冒充余额。不要读取网页登录会话凭据来绕过这一限制。
- 三内置主题为 `classic`、`aurora`、`paper`，内置主题编辑保存为自定义副本。所有注册视觉元素可调位置（XY/方向键）、字号、本机字体、颜色、粗体、斜体；Ctrl 多选支持同时移动、对齐和分布。JSON 导入/导出只改外观，不改可见文字、任务内容、额度/余额或数据绑定。
- 启动先恢复上次屏幕画面，再逐来源采集。屏幕默认旋转 180°；亮度是软件调光。Thermalright TRCC 与应用不能同时占有 USB 接口。

## 架构地图

| 路径 | 责任 |
| --- | --- |
| `agent_dashboard/app.py` | 应用启动、loopback API、数据刷新/合并、Setup lifecycle CLI 路由。 |
| `agent_dashboard/adapters/` | Codex JSONL、ZCode SQLite、Antigravity 本机 language server 读取。 |
| `agent_dashboard/accounts/` | DeepSeek 官方只读余额查询。 |
| `agent_dashboard/retention.py` | 最后可信值和 stale 标记。 |
| `agent_dashboard/ui_profiles.py`、`ui_elements.py`、`layout_editor.py`、`ui_scene.py`、`ui_fonts.py` | 三套内置 UI、元素编辑器、JSON profile 和本机字体映射。 |
| `agent_dashboard/render.py`、`display.py` | 固定 LCD 画布及 WinUSB 帧写入。 |
| `agent_dashboard/mascots.py`、`assets/mascot_*.png` | 固定映射、离线透明图集、三态循环与无素材回退；规范见 `docs/MASCOT-ANIMATIONS.md`。 |
| `agent_dashboard/paths.py`、`storage_migration.py` | canonical 用户数据目录和不覆盖式旧数据迁移。 |
| `agent_dashboard/install_location.py` | UTF-16 BOM `installation.ini` 和 HKCU install identity 解析。 |
| `agent_dashboard/installer_lifecycle.py` | Setup prepare/finish/uninstall 所属权检查和自有生命周期。 |
| `agent_dashboard/installation.py`、`updates.py` | 已登记目录的 EXE 安装/更新与 SHA-256 检查。 |
| `installer/KV-Agent-Desk.iss`、`Build-Installer.ps1`、`tools/stage_installer_payload.py` | Inno 向导、签名验证编译器和公开 Setup stage payload。 |
| `tests/test_install_location.py`、`test_installer_lifecycle.py`、`install_test_safety.py` | 安装登记、lifecycle、Windows 测试 fixture 路径保护。 |

## 安装登记与持久化数据

- 安装路径由 `%USERPROFILE%\.kv-agent-desk\installation.ini` 的 `[Installation]` / `InstallDir` 决定；HKCU `Software\KV-Agent-Desk` 是镜像/回退。安装、workspace 和用户数据分离。
- Setup 首装默认 `D:\APP\KV-Agent-Desk`，默认勾选桌面快捷方式和自启动。复装读取安装登记和上次安装任务选择；部署更新时尊重用户当前配置，不强制改成默认值。安装 AppId 固定为 `{8D8918EF-160F-4D1E-AE68-E168DB0AD2B9}`，不能随版本或工作区改变。
- 用户数据自 v0.6.2 起放在用户主目录，原因是打包宿主与 Explorer 可能访问不同的 AppData 重定向目录；不要改回 `%LOCALAPPDATA%`。UI 的 `status_indicator` 仍保留原有语义 ID 和 position-only 的 shape 描述符，即使它承载位图，也不能随意更名或改变导入契约。
- 卸载应仅删除本应用的程序文件、快捷方式及 Run/install 登记，保留 `.kv-agent-desk` 中的历史、配置和样式。快捷方式由 Inno `[Icons]` 原生清理，不应在卸载 hook 启动 PowerShell/COM。
- 迁移/安装验证限定读取 allowlist，使用数量、schema、metadata 或 hash 对账，不向输出写真实账户资料。当前 PC 保留性已实测；不要在 Setup stage、repo、日志或公开 handoff 复制任何运行数据。

## 构建、预览与发布

本机预览使用合成数据和独立端口：

```powershell
.\.venv\Scripts\python.exe -m agent_dashboard --demo --browser-only --port 8767 --state-dir (Join-Path $env:TEMP ('kv-agent-desk-demo-' + [guid]::NewGuid().ToString('N')))
```

不要把 source/debug EXE 或 workspace 路径登记为长期 Run/shortcut。只生成 EXE 时单独运行 `Build-Dashboard.ps1`；构建 Setup 只运行 `Build-Installer.ps1`，它默认会从当前源码 fresh-build EXE 并制作 Setup。`-SkipAppBuild` 仅供CI已stage好的payload内部使用。

```powershell
powershell -ExecutionPolicy Bypass -File .\Build-Installer.ps1
```

Setup 用 Inno Setup 7.1.0 x64（tag `is-7_1_0`），编译前验证官方资产 attestation 和 Authenticode。CI 从清洁源码构建并执行 tests、public-index guard、synthetic/package smoke、payload/Setup 和 hash 工作。更新协议保持已有 EXE SHA-256 manifest 兼容；不要用本机 `dist/` 或临时工具目录当发布来源。

完整发布必须从干净 CI 构建；本机脚本会安装锁定依赖并重新构建，但只在缺少 `.venv` 时创建环境，不等同于全新 runner。`Build-Dashboard.ps1` 还会请求停止本机默认端口的应用，单纯查看样式请使用独立 demo 端口。v0.8.0 的图集来自用户参考后生成，原参考图没有进入仓库；代码 MIT 与插画许可分开，详见 `THIRD_PARTY_NOTICES.md`。

无需恢复旧聊天即可继续维护：本轮状态动画已完成、发布并安装。后续若要接入 OpenAI 余额，需要先找到真实、可靠且授权的剩余额度来源；若要自动推进套餐日期，需要先明确续期语义，当前到期日是手填字段。其他适配器的本机格式可能随客户端升级变化，异常时保留最近可信数据，先以合成样本复现，不读取或公开真实会话正文。

## 新工作区启动

1. 从最新公开 `main` clone 到一个新的空目录；在新目录重新创建 `.venv`，不要复制旧 venv、`dist/` 或 `runtime/`。
2. 查看 README、PRIVACY、RELEASING 与本文件；查找当前 `__version__`、CI/release 状态和已登记安装位置。移动源码不会改变 `installation.ini`。
3. 新工作区先用 `--demo --browser-only --port 8767` 预览；不要让 debug EXE 注册自启或替换 D 盘已安装程序。
4. 新功能和版本更新在源码 workspace 实现并经 CI 验证，再通过已登记的 Setup 路径更新正式应用。

当前工作区 ignored 本地 ledger 记载具体 CI 失败诊断和验收过程，不属于公开交接包。禁止打开私有 PLAN/VERIFICATION 和记密文件；不要读或记录真实订阅、余额、key、任务内容、布局身份或截图。只使用本文件与公开源码接手。
