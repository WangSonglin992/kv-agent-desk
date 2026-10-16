"""Stable, content-free semantic elements available to UI profiles."""

from __future__ import annotations

from typing import Any


def _group(target: dict[str, dict[str, Any]], element_id: str, label: str) -> None:
    target[element_id] = {"kind": "group", "label": label, "parent": None}


def _children(target: dict[str, dict[str, Any]], parent: str,
              definitions: tuple[tuple[str, str, str], ...]) -> None:
    for suffix, kind, label in definitions:
        element_id = f"{parent}.{suffix}"
        target[element_id] = {"kind": kind, "label": label, "parent": parent}


_elements: dict[str, dict[str, Any]] = {}

_group(_elements, "header", "顶部信息")
_children(_elements, "header", (
    ("panel", "shape", "顶部背景"),
    ("rule", "shape", "顶部横线"),
    ("title", "text", "仪表盘标题"),
    ("platform", "text", "设备型号"),
    ("date", "text", "日期"),
    ("connection", "text", "连接状态"),
    ("live_dot", "shape", "实时状态点"),
    ("live_label", "text", "运行模式"),
    ("clock", "text", "时钟"),
))

_AGENT_ELEMENTS = (
    ("panel", "shape", "卡片背景"),
    ("logo", "image", "品牌标志"),
    ("name", "text", "Agent名称"),
    ("brand", "text", "品牌标签"),
    ("status_badge", "shape", "状态徽章"),
    ("status_indicator", "shape", "状态指示器"),
    ("status_text", "text", "状态文字"),
    ("task_label", "text", "任务标签"),
    ("task_count", "text", "任务数量"),
    ("task_unit", "text", "任务单位"),
    ("plan_label", "text", "套餐标签"),
    ("plan_name", "text", "套餐名称"),
    ("plan_spec", "text", "套餐规格"),
    ("plan_expiry", "text", "套餐到期时间"),
    ("weekly_label", "text", "周额度标签"),
    ("weekly_percent", "text", "周额度百分比"),
    ("weekly_bar", "shape", "周额度进度条"),
    ("weekly_bar_reset", "text", "周额度重置时间"),
    ("tokens_label", "text", "Token范围"),
    ("tokens_total", "text", "Token总量"),
    ("tokens_input", "text", "输入Token"),
    ("tokens_output", "text", "输出Token"),
    ("tokens_cached", "text", "缓存Token"),
    ("tokens_panel", "shape", "Token区背景"),
    ("tokens_rule", "shape", "Token区分隔线"),
    ("quota_rule", "shape", "额度区分隔线"),
    ("five_hour_label", "text", "五小时额度标签"),
    ("five_hour_value", "text", "五小时额度百分比"),
    ("five_hour_reset", "text", "五小时额度重置时间"),
    ("week_label", "text", "周额度标签"),
    ("week_value", "text", "周额度百分比"),
    ("week_reset", "text", "周额度重置时间"),
    ("stale_note", "text", "数据更新提示"),
)

for _provider, _label in (("codex", "Codex"), ("zcode", "ZCode"), ("antigravity", "Antigravity")):
    _group(_elements, f"agent.{_provider}", f"{_label}卡片")
    _children(_elements, f"agent.{_provider}", _AGENT_ELEMENTS)

_ACCOUNT_ELEMENTS = (
    ("panel", "shape", "账户卡片背景"),
    ("logo", "image", "账户标志"),
    ("title", "text", "账户名称"),
    ("subtitle", "text", "账户类型"),
    ("value", "text", "账户余额"),
    ("unit", "text", "余额单位"),
    ("rule", "shape", "账户分隔线"),
    ("detail", "text", "账户说明"),
    ("stale_note", "text", "数据更新提示"),
)

for _account, _label in (("deepseek_lab", "课题组DeepSeek"), ("deepseek_personal", "个人DeepSeek")):
    _group(_elements, f"account.{_account}", f"{_label}卡片")
    _children(_elements, f"account.{_account}", _ACCOUNT_ELEMENTS)

_elements["page.stale_note"] = {"kind": "text", "label": "页面数据更新提示", "parent": None}

ELEMENT_DEFINITIONS: dict[str, dict[str, Any]] = _elements
