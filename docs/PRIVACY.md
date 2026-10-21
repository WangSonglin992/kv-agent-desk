# 隐私与本地数据

KV Agent Desk 在本机读取 Agent 状态并生成屏幕画面。它没有遥测或上传功能；控制页面和 HTTP 控制接口只绑定本机回环地址，并用随机控制令牌保护写操作。下表列出应用会读写的主要数据与网络行为。

## 读取的数据

| 来源 | 读取内容 | 用途 |
| --- | --- | --- |
| Codex | 本地 session JSONL 中的生命周期、任务状态和 Token 统计事件 | 推导状态、进行中任务数、当日 Token 与已观察到的额度窗口。适配器不调用 Codex 账户 API，也不读取认证令牌。 |
| ZCode | 本地 SQLite 数据库中的会话、工具/轮次状态和请求级 Token 统计 | 生成工作状态、任务数和当日 Token。数据库以 SQLite 只读模式打开；适配器不读消息正文或工具参数。 |
| ZCode Coding Plan | 当前选中的套餐 provider 配置中的 API Key 与服务地址 | 仅在已识别的官方 HTTPS 来源上，以额度/订阅 GET 请求获取使用百分比、重置时间和套餐元数据；不向未识别来源发送 Key，不跟随重定向。 |
| Antigravity | 已运行的官方客户端进程信息、本机 language server 的端口/CSRF 值（仅内存中）；使用 `excludeSubtrajectories=true` 获取的父级任务状态摘要；使用 `includeMessages=false` 获取的 Generator Metadata | 约每 2 秒读取并跨窗口按父级任务去重，约每 15 秒读取本机会话的数值 Token 用量，约每 60 秒读取额度摘要。只保留状态、计数、时间戳及 input/output/cache 数值；不保存任务或 cascade ID、标题、提示词、消息正文或 CSRF。 |
| DeepSeek | 仅指定的 `DeepSeek_public`、`DeepSeek_personal` 环境变量或对应 Windows Environment 注册表值 | 将账户 Key 放在 HTTPS 请求头中调用 DeepSeek 官方余额 GET 接口，读取余额与币种。Key 不放入 URL、命令行、日志或应用缓存，也不用于推理。 |
| 手动订阅资料 | 控制页填写的套餐名、规格和到期日期 | 在本机补充无法自动读取的套餐资料并显示在对应 Agent 卡片中。 |

DeepSeek 余额接口的官方说明见 [DeepSeek Get User Balance](https://api-docs.deepseek.com/api/get-user-balance/)。接口返回余额并不代表账户的周额度或订阅额度。

## 网络请求

- Codex 数据从本地文件读取，不联网。
- ZCode 额度查询只针对代码中允许的 Z.ai / BigModel 官方 HTTPS 主机；仅在本机当前选择 Coding Plan provider 时尝试。请求至少间隔 60 秒，并拒绝重定向。
- DeepSeek 余额请求只到官方 HTTPS API，至少间隔 60 秒；每次请求都是只读 GET。
- Antigravity 的任务状态、`includeMessages=false` Generator Metadata 与额度请求发往正在运行的本机 language server，分别约每 2 秒、15 秒和 60 秒读取。本地 usage 只读取 input/output/cache 数值；显示总数为输入加输出，缓存计数单独呈现、不重复相加。客户端可能依照其自身行为与 Google 服务通信；本应用不会为 Antigravity 登录账号或发送模型推理请求。
- 本机控制页通过回环接口提供服务，不向局域网监听，也不会将状态同步到云端。
- 已安装版本默认后台读取本仓库的 GitHub 稳定 Release 公共更新清单；清单不存在时才查询 GitHub 公开 API。下载仅允许 GitHub 及指定官方资产域名，文件通过 SHA-256 校验后才安装。请求不使用 GitHub 登录或 API Key，不上传设置、订阅或布局；正常自动检查间隔为 24 小时，失败时退避。

请求失败、来源数据缺失或时间过久时，应用优先保留最后成功读取的值，并附加“数据待检查更新”提示。未知值不会填成 0% 或 100%。默认过期提示时间为 300 秒；额度接口自身有更长刷新间隔。

## 保存位置与文件

默认目录为 `%LOCALAPPDATA%\KV-Agent-Desk`。文件均为普通本地文件，不加密，也不会被上传：

| 文件 | 保存内容 |
| --- | --- |
| `settings.json` | 显示方向、软件亮度、USB 开关、自启和无 5 小时额度的手动显示偏好。 |
| `ui-profiles/*.json` | 用户保存或导入且通过校验的界面样式，包含配色、元素位置、字号、字体 ID、粗斜体等外观参数；不包含显示内容或数据绑定。 |
| `last-frame-meta.json` | 最近画面对应的 UI 标识和样式校验摘要，避免恢复不匹配的外观。 |
| `last-good.json` | 最近可信 Agent 状态、项目标识、任务计数、Token 汇总、订阅额度及其时间戳。Antigravity 只保存状态/计数和数值用量，不保存任务或 cascade ID、标题、提示词、消息正文或 CSRF。 |
| `accounts-last-good.json` | 两个 DeepSeek 槽位的最近余额、币种、更新时间和状态；不含 API Key。 |
| `subscriptions.json` | 控制页手动填写的套餐名称、规格与到期日期。 |
| `last-frame.png` | 最近显示的完整仪表盘画面，约每 30 秒更新一次并在退出时保存。 |
| `dashboard.log` | 轮转日志，用于本机故障诊断。 |
| `updates.json` | 程序版本、更新检查时间和状态；不包含下载重定向的临时签名 URL、账号或凭据。 |
| `updates/<版本>/KV-Agent-Desk.exe` | 校验通过的更新程序。 |
| `backups/install-*` | 安装或更新前原样备份的设置、订阅和自定义布局；仍只在本机保存。 |

程序安装目录为 `%LOCALAPPDATA%\Programs\KV-Agent-Desk`。更新只替换这个目录中的 EXE，并修复桌面快捷方式，不清空上述数据目录。

开机恢复功能会先显示 `last-frame.png`，再开始刷新数据。注意，截图本身可能包含账户余额和用量；分享截图、缓存或日志前请先检查其内容。退出应用后删除这些文件即可清除对应的本地状态；删除 `settings.json` 后重新启动会恢复默认设置。

自启动命令包含本机数据目录的绝对路径。读取配置失败时保留上次有效值并重试；新增诊断日志仅记录读取操作、异常类别和错误码，不记录目录、布局名称、套餐内容或凭据。

布局编辑器的草稿预览仅在本机内存中使用当前画面的副本，不触发额外账户查询、配置保存或 USB 输出。元素选框清单只含固定标识、标签、几何和样式属性，不回传真实显示内容。字体目录仅列出本机字体名称与不透明 ID；模板不含字体文件或本机文件路径，也不会下载字体。

## 用户可控制的选项

- 关闭控制页中的“开机自启并恢复上次画面”，会移除应用自己的当前用户启动项。
- 关闭“自动检查并安装更新”可停止后续的自动检查；“检查并更新”仍可由用户手动触发。退出应用会取消尚未完成的检查或下载。
- 暂停 USB 输出或退出应用可释放屏幕，方便临时使用 Thermalright TRCC。
- 在控制页清除套餐资料，可移除手动填写的套餐名称、规格与到期日期。
- 取消“ChatGPT 无5小时限额”可恢复读取实际观测额度；默认不勾选。
- 不配置 DeepSeek 对应 Key 时，该账户槽位保持待更新状态，应用不会为其发出请求。
