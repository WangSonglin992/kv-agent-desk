# LCD 界面样式与模板

KV Agent Desk 提供三种内置 LCD 样式：

- `classic`：经典深色。
- `aurora`：Aurora 极光。
- `paper`：Paper 纸感。

在控制页选择样式后，应用会记住当前选择，并在下次启动时继续使用。样式只改变 LCD 仪表盘的视觉呈现；它不会更改 Agent 数据来源、采集范围或刷新策略。

三套样式使用一致的数据布局。Token 数字采用十进制单位：K 为千、M 为百万、B 为十亿；缺少可靠数据时显示 `—`。Antigravity 展示 Gemini 共享的 5 小时和周额度，不列出逐模型额度或 Claude/GPT 额度组。

## 导入和导出

导出内置样式后，先把 `id` 改成新的标识（例如 `my_ocean`），并修改 `name`，再调整外观参数并导入。已有自定义样式可使用相同 `id` 更新。导入成功后立即应用，无需重启。

最小示例（在导入入口选择保存后的 `.json` 文件）：

```json
{"schema_version":1,"id":"my_ocean","name":"海蓝","layout":"aurora","colors":{"codex":"#42C9D1"}}
```

控制页可以将界面样式导出为 JSON 文件，也可以导入之前保存的模板。导入文件上限为 64 KiB。应用会校验 JSON 格式及受支持的模板字段；不符合格式或超出大小限制的文件会被拒绝。

导出文件采用版本 1 的 profile manifest。顶层字段为 `schema_version`、`id`、`name`、`layout`、`canvas`、`colors`、`font_scale`、`radius` 和 `geometry`；当前画布固定为 `[1920, 480]`。`layout` 只能是 `classic`、`aurora` 或 `paper`，自定义 profile 的 `id` 不能与内置样式冲突。可选样式字段从对应布局继承默认值；不支持的额外字段会被拒绝。

可覆盖的颜色角色为 `background`、`surface`、`surface_alt`、`edge`、`text`、`muted`、`subtle`、`codex`、`zcode`、`antigravity`、`working`、`waiting`、`idle`、`offline`、`unknown` 和 `busy`，颜色值使用 `#RRGGBB` 格式。`font_scale` 范围为 90–115 的整数，`radius` 范围为 0–28 的整数。`geometry` 仅接受以下字段，单位为画布像素：

| 字段 | 默认值 | 范围 |
| --- | ---: | ---: |
| `margin` | 24 | 16–64 |
| `gap` | 16 | 8–32 |
| `account_width` | 240 | 200–320 |
| `top` | 76 | 64–104 |
| `bottom` | 450 | 420–464 |

除单文件不超过 64 KiB 外，本机最多保留 32 个自定义 profile；三个内置样式不计入此数量。profile 校验还会检查布局推导出的 Agent 卡片和账户卡片是否能在画布内摆放。

模板只包含界面样式数据。它不包含或导出账号标识、API Key、订阅资料、Agent 会话、任务内容、Token 用量、运行缓存、图片资源、本机路径、URL 或可执行代码。分享模板前仍建议打开文件检查其内容。

如需备份个人应用设置，请使用本机设置备份方式；模板导出仅用于分享和迁移界面样式，不是完整配置备份。有关本机数据位置和保留范围，请参阅 [隐私与本地数据说明](PRIVACY.md)。
