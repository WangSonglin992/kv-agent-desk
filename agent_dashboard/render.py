"""Fixed-size raster UI. The browser and the LCD use the same rendered frame."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
import os
import re

from PIL import Image, ImageDraw, ImageFont

SIZE = (1920, 480)
BG = "#090F16"
SURFACE = "#101A25"
EDGE = "#243344"
INK = "#F0F5FA"
MUTED = "#8FA3B7"
COLORS = {"codex": "#78E7C2", "zcode": "#F7BA71", "antigravity": "#9BB7FF"}
STATES = {
    "working": ("#78E7C2", "工作中"),
    "waiting": ("#F7BA71", "等待输入"),
    "idle": ("#A4B8CD", "空闲"),
    "offline": ("#718296", "未接入"),
    "unknown": ("#A4B8CD", "状态未知"),
}


@lru_cache(maxsize=40)
def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    choices = [fonts / ("consolab.ttf" if bold else "consola.ttf")] if mono else []
    choices += [fonts / ("msyhbd.ttc" if bold else "msyh.ttc"),
                fonts / ("segoeuib.ttf" if bold else "segoeui.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for path in choices:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def short(draw: ImageDraw.ImageDraw, text: object, ft: ImageFont.ImageFont, width: int) -> str:
    value = clean(text)
    if draw.textlength(value, font=ft) <= width:
        return value
    while value and draw.textlength(value + "…", font=ft) > width:
        value = value[:-1]
    return value + "…"


def number(value: int | None) -> str:
    if value is None:
        return "—"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f}亿"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def expiry_text(value: str | None) -> str:
    if not value:
        return "到期待填写"
    try:
        if len(value) == 10:
            return "到期 " + datetime.fromisoformat(value).strftime("%Y/%m/%d")
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        return "到期 " + expiry.strftime("%Y/%m/%d %H:%M")
    except (ValueError, TypeError, AttributeError):
        return "到期待填写"


def draw_agent(draw: ImageDraw.ImageDraw, data: dict, x: int, width: int) -> None:
    y, bottom = 76, 450
    accent = COLORS.get(data.get("provider"), "#9BB2CE")
    draw.rounded_rectangle((x, y, x + width, bottom), radius=19, fill=SURFACE, outline=EDGE, width=1)
    draw.rounded_rectangle((x, y + 25, x + 4, bottom - 25), radius=2, fill=accent)
    left, right = x + 28, x + width - 28
    heading_size = 30 if data.get("provider") == "antigravity" else 34
    draw.text((left, 94), data.get("name", "Agent"), fill=accent, font=font(heading_size, True))
    if data.get("provider") == "codex":
        draw.text((left + 147, 109), "ChatGPT 订阅", fill="#748CA3", font=font(16))
    state_color, default_label = STATES.get(data.get("state"), STATES["unknown"])
    state = short(draw, data.get("state_label") or default_label, font(21, True), 170)
    chip_width = int(draw.textlength(state, font=font(21, True))) + 43
    chip_left = right - chip_width
    draw.rounded_rectangle((chip_left, 101, right, 136), radius=16, fill="#1D2B39")
    draw.ellipse((chip_left + 13, 114, chip_left + 20, 121), fill=state_color)
    draw.text((chip_left + 28, 103), state, fill=state_color, font=font(21, True))

    subscription = data.get("subscription") or {}
    windows = sorted([item for item in (subscription.get("quota_windows") or []) if isinstance(item, dict)],
                     key=lambda item: not item.get("known", False))
    is_antigravity = data.get("provider") == "antigravity"
    observed_models = [item for item in windows if item.get("known")]
    draw.text((left, 151), "已读取额度的模型" if is_antigravity else "正在进行中的任务", fill=MUTED, font=font(21))
    count = len(observed_models) if is_antigravity and observed_models else data.get("active_tasks")
    count_text = str(count) if isinstance(count, int) else "—"
    draw.text((left, 177), count_text, fill=INK, font=font(58, True, mono=True))
    count_width = draw.textlength(count_text, font=font(58, True, mono=True))
    draw.text((left + count_width + 17, 204), "个模型" if is_antigravity else "个任务", fill=MUTED, font=font(23))
    metadata_x = left + 215
    metadata_width = right - metadata_x
    draw.text((metadata_x, 150), "订阅套餐", fill=MUTED, font=font(16))
    plan_name = subscription.get("plan_name") or "套餐待填写"
    name_size = 24
    while name_size > 20 and draw.textlength(clean(plan_name), font=font(name_size, True)) > metadata_width:
        name_size -= 1
    draw.text((metadata_x, 172), short(draw, plan_name, font(name_size, True), metadata_width),
              fill=INK if subscription.get("plan_name") else MUTED, font=font(name_size, True))
    draw.text((metadata_x, 203), short(draw, subscription.get("plan_spec") or "规格待填写", font(17), metadata_width),
              fill=MUTED, font=font(17))
    draw.text((metadata_x, 230), short(draw, expiry_text(subscription.get("expires_at")), font(16), metadata_width),
              fill="#748CA3", font=font(16))
    primary_window = windows[0] if windows else None
    weekly = primary_window or subscription.get("weekly") or {}
    known = bool(weekly.get("known")) and weekly.get("remaining_percent") is not None
    remaining = min(100.0, max(0.0, float(weekly["remaining_percent"]))) if known else None
    label = (primary_window.get("label") or primary_window.get("model") or "额度剩余") if primary_window else ("额度待更新" if data.get("provider") == "antigravity" else "周额度剩余" if known else "周额度待更新")
    label = short(draw, label, font(20), width - 120)
    draw.text((left, 255), label, fill=MUTED, font=font(20))
    if known:
        pct = f"{remaining:g}%"
        draw.text((right - draw.textlength(pct, font=font(20, mono=True)), 258), pct,
                  fill=accent, font=font(20, mono=True))
    draw.rounded_rectangle((left, 288, right, 294), radius=3, fill="#273746")
    if known and remaining:
        draw.rounded_rectangle((left, 288, left + max(5, (right - left) * remaining / 100), 294),
                               radius=3, fill=accent)

    tokens = data.get("tokens") or {}
    value = tokens.get("total") if tokens.get("known") else None
    token_date = tokens.get("date", "")
    date_label = "今日 TOKENS"
    if token_date and token_date != datetime.now().astimezone().date().isoformat():
        date_label = token_date[5:].replace("-", "/") + " TOKENS"
    if is_antigravity:
        second = observed_models[1] if len(observed_models) > 1 else {}
        date_label = second.get("label") or second.get("model") or "模型额度待更新"
        main_value = f"{float(second['remaining_percent']):g}%" if second.get("remaining_percent") is not None else "—"
        inout = "重置时间待更新"
        if second.get("reset_at"):
            try:
                reset = datetime.fromisoformat(second["reset_at"].replace("Z", "+00:00")).astimezone()
                inout = "重置 " + reset.strftime("%m/%d %H:%M")
            except (ValueError, AttributeError):
                pass
        cached = "服务端额度记录"
    else:
        main_value = number(value)
        inout = f"入 {number(tokens.get('input'))}  /  出 {number(tokens.get('output'))}"
        cached = f"缓存 {number(tokens.get('cached'))}" if tokens.get("known") else "本地记录暂无用量"
    draw.text((left, 309), short(draw, date_label, font(17), 196 if is_antigravity else width - 56), fill=MUTED, font=font(17))
    draw.text((left, 330), main_value, fill=INK, font=font(38, True))
    draw.text((left + 208, 315), short(draw, inout, font(18), width - 268), fill=MUTED, font=font(18))
    draw.text((left + 208, 347), short(draw, cached, font(17), width - 268), fill="#748CA3", font=font(17))
    draw.line((left, 384, right, 384), fill=EDGE)
    subscription = data.get("subscription") or {}
    footer_windows = windows[2:4] if is_antigravity and len(windows) > 2 else windows[:2]
    footer = [(item, item.get("label") or item.get("model") or "额度") for item in footer_windows] if windows else [
        (subscription.get("five_hour") or {}, "5小时"), (subscription.get("weekly") or {}, "周额度")]
    if data.get("provider") == "antigravity" and not windows:
        footer = [({}, "模型额度"), ({}, "重置时间")]
    for index, (item, title) in enumerate(footer):
        start = left + index * ((width - 56) // 2)
        percent = item.get("remaining_percent") if item.get("known") else None
        display_value = "—" if percent is None else f"{min(100, max(0, float(percent))):g}%"
        column_width = (width - 56) // 2
        value_width = draw.textlength(display_value, font=font(24, True, mono=True))
        draw.text((start, 393), short(draw, title, font(16), column_width - value_width - 26), fill=MUTED, font=font(16))
        draw.text((start + column_width - value_width - 22, 390), display_value, fill=accent, font=font(24, True, mono=True))
        reset_label = "重置时间待更新"
        if item.get("unlimited"):
            reset_label = "订阅无限额"
        elif item.get("reset_at"):
            try:
                reset = datetime.fromisoformat(str(item["reset_at"]).replace("Z", "+00:00")).astimezone()
                reset_label = "重置 " + reset.strftime("%m/%d %H:%M")
            except (ValueError, TypeError):
                pass
        draw.text((start, 425), reset_label, fill="#748CA3", font=font(15))
    if data.get("stale"):
        draw.text((left, 454), "数据待检查更新", fill="#888777", font=font(15))


@lru_cache(maxsize=6)
def logo(brand: str) -> Image.Image | None:
    if brand not in {"lab", "deepseek", "glm"}:
        return None
    path = Path(__file__).with_name("assets") / (brand + ".png")
    try:
        with Image.open(path) as source:
            result = source.convert("RGBA")
        bounds = result.getbbox()
        if bounds:
            result = result.crop(bounds)
        result.thumbnail((48, 45), Image.Resampling.LANCZOS)
        return result
    except (OSError, ValueError):
        return None


def draw_account(image: Image.Image, draw: ImageDraw.ImageDraw, data: dict, x: int, y: int, width: int = 382) -> None:
    height, padding = 178, 19
    right = x + width - padding
    draw.rounded_rectangle((x, y, x + width, y + height), radius=17, fill=SURFACE, outline=EDGE)
    icon = logo(data.get("brand", ""))
    if icon is not None:
        icon_x = right - 46
        if data.get("brand") == "lab":
            draw.rounded_rectangle((icon_x - 4, y + 13, right + 4, y + 64), radius=10, fill="#D8DEE5")
        image.paste(icon, (icon_x + (46 - icon.width) // 2, y + 15 + (45 - icon.height) // 2), icon)
    left = x + padding
    compact = width < 300
    title = ("课题组" if data.get("id") == "deepseek_lab" else "个人") if compact else data.get("name", "账户")
    draw.text((left, y + 16), short(draw, title, font(22 if compact else 20, True), width - 95), fill=INK, font=font(22 if compact else 20, True))
    weekly = data.get("kind") == "weekly"
    draw.text((left, y + 47), "周额度剩余" if weekly else "DeepSeek 余额" if compact else "账户余额", fill=MUTED, font=font(15))
    value, unit = "—", ""
    if data.get("known"):
        if weekly and data.get("remaining_percent") is not None:
            value = f"{min(100, max(0, float(data['remaining_percent']))):g}%"
        elif data.get("value") is not None:
            try:
                value = f"{Decimal(str(data['value'])):,.2f}"
                unit = {"CNY": "元", "USD": "USD"}.get(data.get("currency"), data.get("currency") or "")
            except (InvalidOperation, ValueError):
                pass
    draw.text((left, y + 74), short(draw, value, font(37, True, mono=True), width - 80), fill="#D3E3F4", font=font(37, True, mono=True))
    value_width = draw.textlength(value, font=font(37, True, mono=True))
    if unit:
        draw.text((min(right - 44, left + value_width + 12), y + 94), unit, fill=MUTED, font=font(18))
    line = ""
    if weekly:
        if data.get("limit") is not None and data.get("used") is not None:
            line = f"已用 {data['used']} / {data['limit']}"
        if data.get("reset_at"):
            try:
                reset = datetime.fromisoformat(data["reset_at"].replace("Z", "+00:00")).astimezone()
                line = "重置 " + reset.strftime("%m/%d %H:%M")
            except (ValueError, AttributeError):
                pass
    elif data.get("known"):
        line = "账户已用尽" if data.get("is_available") is False else "可用余额"
    if not data.get("known"):
        line = "数据待补充"
    draw.text((left, y + 132), short(draw, line, font(15), width - 38), fill=MUTED, font=font(15))
    if data.get("stale"):
        draw.text((left, y + 156), "数据待检查更新", fill="#888777", font=font(13))


def render(snapshot: dict, now: datetime | None = None) -> Image.Image:
    now = now or datetime.now().astimezone()
    image = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(image)
    draw.text((30, 17), "AGENT DESK", fill=INK, font=font(25, True, mono=True))
    draw.text((235, 22), "WINDOWS  /  KV 9.16", fill=MUTED, font=font(18, mono=True))
    draw.line((28, 59, 1892, 59), fill=EDGE, width=1)
    badge = "演示数据 / DEMO" if snapshot.get("demo") else "实时仪表盘 / LIVE"
    draw.ellipse((1465, 28, 1472, 35), fill="#F7BA71" if snapshot.get("demo") else "#78E7C2")
    draw.text((1485, 21), badge, fill=MUTED, font=font(17))
    draw.text((1736, 15), now.strftime("%H:%M:%S"), fill=INK, font=font(30, True, mono=True))
    weekdays = "一二三四五六日"
    draw.text((1100, 22), now.strftime("%m/%d") + f" 周{weekdays[now.weekday()]}", fill=MUTED, font=font(18))
    connected = snapshot.get("display", {}).get("connected", False)
    draw.text((1250, 22), "USB 已连接" if connected else "本机预览", fill="#78E7C2" if connected else MUTED, font=font(18))
    agents = snapshot.get("agents", [])
    for i, agent in enumerate(agents[:3]):
        draw_agent(draw, agent, 24 + i * 544, 528)
    accounts = snapshot.get("accounts", [])
    for index, account in enumerate(accounts[:2]):
        draw_account(image, draw, account, 1656, 76 + index * 196, width=240)
    return image
