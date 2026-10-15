# 隐私与本地数据

KV Agent Desk 在本机读取 Agent 状态并生成屏幕画面。它没有遥测或上传功能；控制页面和 HTTP 控制接口只绑定本机回环地址，并用随机控制令牌保护写操作。下表列出应用会读写的主要数据与网络行为。

## 读取的数据

| 来源 | 读取内容 | 用途 |
| --- | --- | --- |
| Codex | 本地 session JSONL 中的生命周期、任务状态和 Token 统计事件 | 推导状态、进行中任务数、当日 Token 与已观察到的额度窗口。适配器不调用 Codex 账户 API，也不读取认证令牌。 |
| ZCode | 本地 SQLite 数据库中的会话、工具/轮次状态和请求级 Token 统计 | 生成工作状态、任务数和当日 Token。数据库以 SQLite 只读模式打开；适配器不读消息正文或工具参数。 |
| ZCode Coding Plan | 当前选中的套餐 provider 配置中的 API Key 与服务地址 | 仅在已识别的官方 HTTPS 来源上，以额度/订阅 GET 请求获取使用百分比、重置时间和套餐元数据；不向未识别来源发送 Key，不跟随重定向。 |
| Antigravity | 已运行的官方客户端进程信息、本机 language server 的端口/CSRF 值（仅内存中） | 向该进程拥有的本机接口请求模型额度快照。CSRF 值不会写入应用缓存或日志；应用不启动推理请求。 |
| DeepSeek | 仅指定的 `DeepSeek_public`、`DeepSeek_personal` 环境变量或对应 Windows Environment 注册表值 | 将账户 Key 放在 HTTPS 请求头中调用 DeepSeek 官方余额 GET 接口，读取余额与币种。Key 不放入 URL、命令行、日志或应用缓存，也不用于推理。 |
| 手动订阅资料 | 控制页填写的套餐名、规格和到期日期 | 在本机补充无法自动读取的套餐资料并显示在对应 Agent 卡片中。 |

DeepSeek 余额接口的官方说明见 [DeepSeek Get User Balance](https://api-docs.deepseek.com/api/get-user-balance/)。接口返回余额并不代表账户的周额度或订阅额度。

## 网络请求

- Codex 数据从本地文件读取，不联网。
- ZCode 额度查询只针对代码中允许的 Z.ai / BigModel 官方 HTTPS 主机；仅在本机当前选择 Coding Plan provider 时尝试。请求至少间隔 60 秒，并拒绝重定向。
- DeepSeek 余额请求只到官方 HTTPS API，至少间隔 60 秒；每次请求都是只读 GET。
- Antigravity 配额请求发往正在运行的本机 language server；该客户端可能依照其自身行为与 Google 服务通信。本应用不会为 Antigravity 登录账号或发送模型推理请求。
- 本机控制页通过回环接口提供服务，不向局域网监听，也不会将状态同步到云端。

请求失败、来源数据缺失或时间过久时，应用优先保留最后成功读取的值，并附加“数据待检查更新”提示。未知值不会填成 0% 或 100%。默认过期提示时间为 300 秒；额度接口自身有更长刷新间隔。

## 保存位置与文件

默认目录为 `%LOCALAPPDATA%\KV-Agent-Desk`。文件均为普通本地文件，不加密，也不会被上传：

| 文件 | 保存内容 |
| --- | --- |
| `settings.json` | 显示方向、软件亮度、USB 开关、自启和无 5 小时额度的手动显示偏好。 |
| `ui-profiles/*.json` | 用户导入且通过校验的界面样式，包含配色、布局、字号等外观参数。 |
| `last-frame-meta.json` | 最近画面对应的 UI 标识和样式校验摘要，避免恢复不匹配的外观。 |
| `last-good.json` | 最近可信 Agent 状态、项目标识、任务计数、Token 汇总、订阅额度及其时间戳。 |
| `accounts-last-good.json` | 两个 DeepSeek 槽位的最近余额、币种、更新时间和状态；不含 API Key。 |
| `subscriptions.json` | 控制页手动填写的套餐名称、规格与到期日期。 |
| `last-frame.png` | 最近显示的完整仪表盘画面，约每 30 秒更新一次并在退出时保存。 |
| `dashboard.log` | 轮转日志，用于本机故障诊断。 |

开机恢复功能会先显示 `last-frame.png`，再开始刷新数据。注意，截图本身可能包含账户余额和用量；分享截图、缓存或日志前请先检查其内容。删除这些文件即可清除对应的本地状态；删除 `settings.json` 会恢复默认设置。

## 用户可控制的选项

- 关闭控制页中的“开机自启并恢复上次画面”，会移除应用自己的当前用户启动项。
- 暂停 USB 输出或退出应用可释放屏幕，方便临时使用 Thermalright TRCC。
- 在控制页清除套餐资料，可移除手动填写的套餐名称、规格与到期日期。
- 取消“ChatGPT 无5小时限额”可恢复读取实际观测额度；默认不勾选。
- 不配置 DeepSeek 对应 Key 时，该账户槽位保持待更新状态，应用不会为其发出请求。
