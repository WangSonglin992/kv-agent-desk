# 扩展 Agent 面板

新增 Agent 时，优先实现只读采集器，并复用现有状态保留、渲染和 UI 约定。不要从消息正文、工具参数或日志文本猜测进度；没有足够证据的字段保持未知。

## 目录与数据流

1. `agent_dashboard/adapters/<provider>.py` 实现数据采集。
2. `agent_dashboard/app.py` 的 `Dashboard.initialise()` 按固定 provider 顺序载入适配器；`PROVIDERS` 同时控制顺序。
3. `agent_dashboard/retention.py` 合并新结果与最后可信结果，并为过期数据设置 `stale`。
4. `agent_dashboard/render.py` 绘制 1920 × 480 画布。屏幕不显示任务名称或计划步骤；Agent 面板使用任务数、状态、Token 和周额度等摘要。
5. `agent_dashboard/web.py` 提供本机控制页与手动套餐资料入口。
6. `tests/test_<provider>.py` 覆盖解析、失败降级和刷新节流。

账户余额卡片与 Agent 适配器分开：余额采集器放在 `agent_dashboard/accounts/`，由 `accounts/monitor.py` 控制更新和最后状态保留。

## 适配器输出契约

`collect()` 返回一个字典，至少提供以下字段：

```python
{
    "provider": "example",       # 与注册名一致
    "name": "Example",
    "available": False,
    "state": "unknown",          # working / waiting / idle / offline / unknown
    "state_label": "状态未知",
    "observed_state": "unknown",
    "stale": True,
    "project": "",
    "updated_at": None,           # 有来源时间时使用 ISO 8601
    "active_tasks": None,         # 无可靠计数时为 None
    "tokens": {
        "known": False,
        "total": None,
        "scope": "today",
    },
    "subscription": {
        "five_hour": {"known": False, "remaining_percent": None},
        "weekly": {"known": False, "remaining_percent": None},
    },
    "source": "",
    "detail": "等待本机数据",
}
```

读取成功的字段应带有效时间戳；未读取到的字段使用 `None` 和 `known: false`。如果额度周期并非固定 5 小时或一周，使用 `subscription.quota_windows`，为每个窗口提供来源中的标签、百分比、重置时间、模型名和 `known`。不要将特定服务的周期硬套到其他服务。

适配器应满足：

- 收集只读本地来源或已核实的官方查询接口；不得为了显示状态触发模型推理。
- 仅读取完成功能所需的字段；例如避免消息正文和工具参数。
- API Key 只从对应的凭据来源读取，在内存中通过 HTTPS 请求头发送；不打印异常响应、请求头或密钥，不把密钥写入 URL、命令行或缓存。
- 网络调用设置短超时、响应体上限、精确允许的官方 HTTPS 主机，并拒绝重定向；不自动尝试其他账户或来源。
- 对失败结果保留最近可信字段并标为 `stale`；无历史值时返回未知状态。
- 对外部 API 设节流，避免每次画面刷新都请求账户额度。保留策略由来源适配器或已有 `retention.py` 完成。
- 任务状态只能依据明确生命周期、工具运行状态或其他可验证事件。不要通过任务标题、计划步骤或进程存在本身推断工作进度。

## 实现与验证步骤

1. 在 `agent_dashboard/adapters/` 新建模块，实现 `ExampleAdapter(root=None).collect()`；依赖的文件根目录应可注入，方便使用临时目录测试。
2. 在 `agent_dashboard/app.py` 同时更新 `PROVIDERS` 与 `Dashboard.initialise()` 内的适配器列表。当前类名按 `<显示名称>Adapter` 载入（如 `CodexAdapter`）。更新 `empty_agent()` 的订阅名称映射；新展示字段须经过 `display_data()` 白名单。
3. 在 `agent_dashboard/render.py` 中添加颜色和卡片信息。默认布局只绘制 `agents[:3]`，三列各宽 528，横向间隔 544；右侧保留 240 宽的双余额卡。替换现有来源可沿用布局，增加第四列必须调整截取数量、卡宽、坐标和字体，避免新卡片不可见或重叠。不要展示任务标题、项目路径、密钥或未经证实的百分比。
4. 若需要手动套餐资料，在 `agent_dashboard/subscriptions.py` 的 `PROVIDERS` 注册来源；在 `agent_dashboard/web.py` 增加状态区、表单和一致的元素 ID，并同步 `fillProfiles()` 及表单事件绑定中的 provider 列表。否则前端不会填入或保存新来源。
5. 编写 `tests/test_example.py`，至少测试：正常解析、字段缺失时保持 unknown、失败保留上次值并 stale、过期数据保留提示，以及请求节流/拒绝不可信目标（若使用网络接口）。网络调用使用 mock，不调用真实用户服务。
6. 如使用动态导入，更新 `Build-Dashboard.ps1` 与 `.github/workflows/windows-release.yml` 的 PyInstaller hidden import 列表。新增静态资源时只在 `pyproject.toml` 和打包显式白名单中包含确实需要的文件，不要打包整个 `assets/`、`runtime/` 或用户数据目录。
7. 使用当前 Python 3.12 环境运行 `python -m unittest discover -s tests -v`，然后运行 `python tools/check_public_tree.py`。若测试依赖系统特定功能，保持其可 mock，不在测试中启动副屏、桌面服务或真实账户请求。

添加额度卡片或新 Agent 前，请同步更新 `README.md` 与 `docs/PRIVACY.md`，说明可验证的数据来源、请求目的、凭据保存方式和未知/过期行为。

调整现有元素的位置和文字样式时，可直接使用应用内布局编辑器或 JSON 模板的 `elements`。新增可编辑元素时，先在 `ui_elements.py` 注册固定 ID、类型、标签和所属组，再通过 `render.py` 的 `SceneCanvas` 图元绘制；ID 不应取自内容、遍历顺序或代码行号。`render_scene()` 返回预览图和不含内容的元素清单，`render()` 使用相同绘制路径输出 LCD 画面。新增数据字段仍须从适配器和显示白名单接入，不能由样式模板指定数据绑定。

本机控制页位于 `web.py`，布局编辑工具栏位于 `layout_editor.py`，本机字体解析位于 `ui_fonts.py`。新增发布资源还需同步 `tools/check_public_tree.py` 的公共文件白名单；提交前暂存文件，再运行该检查器。
