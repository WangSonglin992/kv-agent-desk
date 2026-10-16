"""Fixed-size, privacy-preserving raster UI for the KV 9.16 LCD."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import math
from pathlib import Path
import os
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .ui_elements import ELEMENT_DEFINITIONS
from .ui_scene import FittedText, SceneCanvas


SIZE = (1920, 480)

_PALETTES = {
    "classic": {
        "background": "#090F16", "surface": "#101A25", "surface_alt": "#182635",
        "edge": "#243344", "text": "#F0F5FA", "muted": "#8FA3B7", "subtle": "#748CA3",
        "codex": "#78E7C2", "zcode": "#F7BA71", "antigravity": "#9BB7FF",
        "working": "#78E7C2", "waiting": "#F7BA71", "idle": "#A4B8CD",
        "offline": "#718296", "unknown": "#A4B8CD", "busy": "#F06469",
    },
    "aurora": {
        "background": "#080D1C", "surface": "#111A30", "surface_alt": "#0C1428",
        "edge": "#283454", "text": "#F1F5FF", "muted": "#99AAC4", "subtle": "#7386A5",
        "codex": "#79E9C4", "zcode": "#FFBD8B", "antigravity": "#B0A1FF",
        "working": "#79E9C4", "waiting": "#FFBD8B", "idle": "#99AAC4",
        "offline": "#7386A5", "unknown": "#99AAC4", "busy": "#FF7186",
    },
    "paper": {
        "background": "#F4F1EB", "surface": "#FFFEFA", "surface_alt": "#F4F1EB",
        "edge": "#D8D4CB", "text": "#202B34", "muted": "#66727A", "subtle": "#7A837F",
        "codex": "#157B69", "zcode": "#A5602D", "antigravity": "#5964AD",
        "working": "#157B69", "waiting": "#A5602D", "idle": "#66727A",
        "offline": "#7A837F", "unknown": "#7A837F", "busy": "#B44949",
    },
}

_DEFAULT_GEOMETRY = {
    "classic": {"margin": 24, "gap": 16, "account_width": 240, "top": 76, "bottom": 450},
    "aurora": {"margin": 24, "gap": 16, "account_width": 240, "top": 76, "bottom": 450},
    "paper": {"margin": 24, "gap": 16, "account_width": 240, "top": 76, "bottom": 450},
}
_DEFAULT_RADIUS = {"classic": 18, "aurora": 24, "paper": 10}
_LOGO_FILES = {"chatgpt": "chatgpt.png", "glm": "glm.png", "google": "google.png", "deepseek": "deepseek.png"}
_PROVIDER_LOGOS = {"codex": "chatgpt", "zcode": "glm", "antigravity": "google"}
_PROVIDER_TAGS = {"codex": "ChatGPT", "zcode": "GLM", "antigravity": "Google"}

# Compatibility constants for callers that used the original classic palette.
BG = _PALETTES["classic"]["background"]
SURFACE = _PALETTES["classic"]["surface"]
EDGE = _PALETTES["classic"]["edge"]
INK = _PALETTES["classic"]["text"]
MUTED = _PALETTES["classic"]["muted"]
COLORS = {key: _PALETTES["classic"][key] for key in ("codex", "zcode", "antigravity")}
STATES = {
    "working": (_PALETTES["classic"]["working"], "工作中"),
    "waiting": (_PALETTES["classic"]["waiting"], "等待输入"),
    "idle": (_PALETTES["classic"]["idle"], "空闲"),
    "offline": (_PALETTES["classic"]["offline"], "未接入"),
    "unknown": (_PALETTES["classic"]["unknown"], "状态未知"),
}


@lru_cache(maxsize=96)
def font(size: int, bold: bool = False, mono: bool = False, scale: int = 100) -> ImageFont.FreeTypeFont:
    """Load an installed system font, optionally scaled by a profile percentage."""
    pixel_size = max(8, round(size * scale / 100))
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    choices = [fonts / ("consolab.ttf" if bold else "consola.ttf")] if mono else []
    choices += [fonts / ("msyhbd.ttc" if bold else "msyh.ttc"),
                fonts / ("segoeuib.ttf" if bold else "segoeui.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for path in choices:
        if path.is_file():
            return ImageFont.truetype(str(path), pixel_size)
    return ImageFont.load_default(size=pixel_size)


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def short(draw: ImageDraw.ImageDraw | SceneCanvas, text: object, ft: ImageFont.ImageFont, width: int) -> FittedText:
    value = clean(text)
    if width <= 0:
        fitted = "…" if value else ""
        return FittedText(fitted, value, width)
    if draw.textlength(value, font=ft) <= width:
        return FittedText(value, value, width)
    while value and draw.textlength(value + "…", font=ft) > width:
        value = value[:-1]
    fitted = value + "…" if value else "…"
    return FittedText(fitted, clean(text), width)


def number(value: int | None) -> str:
    """Format Token counts, using zero when no usage has been observed."""
    if value is None:
        return "0"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
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


def _resolve_design(profile: dict | None) -> dict:
    """Return an immutable-per-call style context; never mutate global tokens."""
    source = profile if isinstance(profile, dict) else {}
    layout = source.get("layout") or source.get("id") or "classic"
    if layout not in _PALETTES:
        layout = "classic"
    palette = dict(_PALETTES[layout])
    custom_colors = source.get("colors")
    if isinstance(custom_colors, dict):
        for role, value in custom_colors.items():
            if role in palette and isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                palette[role] = value.upper()

    geometry = dict(_DEFAULT_GEOMETRY[layout])
    custom_geometry = source.get("geometry")
    if isinstance(custom_geometry, dict):
        bounds = {
            "margin": (16, 64), "gap": (8, 32), "account_width": (200, 320),
            "top": (64, 104), "bottom": (420, 464),
        }
        for key, (minimum, maximum) in bounds.items():
            value = custom_geometry.get(key)
            if type(value) is int and minimum <= value <= maximum:
                geometry[key] = value

    agent_width = (SIZE[0] - 2 * geometry["margin"] - geometry["account_width"] - 3 * geometry["gap"]) // 3
    if agent_width < 400:
        geometry = dict(_DEFAULT_GEOMETRY[layout])
        agent_width = (SIZE[0] - 2 * geometry["margin"] - geometry["account_width"] - 3 * geometry["gap"]) // 3

    font_scale = source.get("font_scale", 100)
    if type(font_scale) is not int or not 90 <= font_scale <= 115:
        font_scale = 100
    radius = source.get("radius", _DEFAULT_RADIUS[layout])
    if type(radius) is not int or not 0 <= radius <= 28:
        radius = _DEFAULT_RADIUS[layout]
    return {"layout": layout, "colors": palette, "geometry": geometry,
            "elements": source.get("elements") if isinstance(source.get("elements"), dict) else {},
            "agent_width": agent_width, "font_scale": font_scale, "radius": radius}


def _element(draw: ImageDraw.ImageDraw | SceneCanvas, name: str) -> str | None:
    if not isinstance(draw, SceneCanvas):
        return None
    identity = draw._name(name)
    return identity if identity in ELEMENT_DEFINITIONS else None


def _text(draw: ImageDraw.ImageDraw | SceneCanvas, xy, value, element: str, **kwargs):
    identity = _element(draw, element)
    if identity is not None:
        return draw.text(xy, value, element=identity, **kwargs)
    return draw.text(xy, value, **kwargs)


def _shape(draw: ImageDraw.ImageDraw | SceneCanvas, primitive: str, xy, element: str, **kwargs):
    identity = _element(draw, element)
    method = getattr(draw, primitive)
    if identity is not None:
        return method(xy, element=identity, **kwargs)
    return method(xy, **kwargs)


def _paste(image: Image.Image, draw: ImageDraw.ImageDraw | SceneCanvas, icon: Image.Image,
           xy: tuple[int, int], element: str, mask: Image.Image | None = None) -> None:
    identity = _element(draw, element)
    if identity is not None:
        draw.paste(icon, xy, mask, element=identity)
    else:
        image.paste(icon, xy, mask)


def _active_task_count(data: dict) -> int | None:
    """Return only an explicit, reliable task count; never use model counts."""
    retained = "active_tasks" in (data.get("retained_fields") or [])
    if data.get("provider") == "antigravity" and data.get("active_tasks_known") is not True and not retained:
        return None
    count = data.get("active_tasks")
    if type(count) is not int or count < 0 or (data.get("active_tasks_known") is False and not retained):
        return None
    return count


def activity_state(data: dict) -> str:
    """Classify motion/status for the caller's frame cadence and card badge."""
    if _active_task_count(data) is not None and _active_task_count(data) > 3:
        return "busy"
    return "working" if data.get("state") == "working" else "idle"


def is_animated(data: dict) -> bool:
    """Whether this Agent card needs a changing spinner frame."""
    return activity_state(data) in {"working", "busy"}


def animation_period(data: dict) -> float | None:
    """Spinner cycle in seconds: ordinary work is calm, busy work is quicker."""
    state = activity_state(data)
    if state == "busy":
        return 0.65
    if state == "working":
        return 2.0
    return None


@lru_cache(maxsize=8)
def logo(brand: str) -> Image.Image | None:
    """Load only local bundled brand assets; remote URLs are never consulted."""
    filename = _LOGO_FILES.get(brand)
    if filename is None:
        return None
    path = Path(__file__).with_name("assets") / filename
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


def _hex_rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))


def _mix_color(base: str, accent: str, factor: float) -> str:
    first, second = _hex_rgb(base), _hex_rgb(accent)
    factor = min(1.0, max(0.0, factor))
    return "#" + "".join(f"{round(a + (b - a) * factor):02X}" for a, b in zip(first, second))




def _window_percent(window: dict | None) -> float | None:
    if not isinstance(window, dict) or not window.get("known") or window.get("remaining_percent") is None:
        return None
    try:
        value = float(window["remaining_percent"])
        return min(100.0, max(0.0, value)) if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _window_reset(window: dict | None) -> str:
    if not isinstance(window, dict) or not window.get("reset_at"):
        return "重置时间待更新"
    try:
        reset = datetime.fromisoformat(str(window["reset_at"]).replace("Z", "+00:00")).astimezone()
        return "重置 " + reset.strftime("%m/%d %H:%M")
    except (ValueError, TypeError, AttributeError):
        return "重置时间待更新"




def _agent_metrics(data: dict, today: str | None = None) -> dict:
    subscription = data.get("subscription") or {}
    tokens = data.get("tokens") or {}
    date_label = "今日 TOKENS"
    token_date = tokens.get("date") or ""
    if tokens.get("scope") == "cumulative":
        date_label = "累计 TOKENS"
    elif token_date and token_date != (today or datetime.now().astimezone().date().isoformat()):
        date_label = str(token_date)[5:].replace("-", "/") + " TOKENS"
    return {
        "provider": data.get("provider"),
        "subscription": subscription,
        "count": _active_task_count(data),
        "primary": subscription.get("weekly") or {},
        "windows": [subscription.get("five_hour") or {}, subscription.get("weekly") or {}],
        "tokens": tokens,
        "token_value": tokens.get("total") if tokens.get("known") else None,
        "date_label": date_label,
        "busy": activity_state(data) == "busy",
    }


def _draw_spinner(draw: ImageDraw.ImageDraw, center: tuple[int, int], color: str, background: str,
                  animation_time: float | None, period: float) -> None:
    elapsed = animation_time if isinstance(animation_time, (int, float)) and math.isfinite(animation_time) else 0.0
    phase = (elapsed % period) / period * 8
    head = int(phase) % 8
    cx, cy = center
    for index in range(8):
        angle = math.radians(index * 45 - 90)
        x = cx + math.cos(angle) * 7
        y = cy + math.sin(angle) * 7
        age = (index - head) % 8
        fill = _mix_color(background, color, max(0.18, 1.0 - age * 0.11))
        _shape(draw, "ellipse", (round(x - 2), round(y - 2), round(x + 2), round(y + 2)),
               "status_indicator", fill=fill)


def _card(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int,
          design: dict, provider: str = "", account: bool = False) -> None:
    colors, layout = design["colors"], design["layout"]
    radius = design["radius"]
    if account:
        radius = max(0, radius - (2 if layout != "paper" else 0))
    if layout == "aurora":
        accent = colors.get(provider, colors["edge"])
        glow = _mix_color(colors["background"], accent, 0.16)
        _shape(draw, "rounded_rectangle", (x - 2, y - 2, x + width + 2, y + height + 2),
               "panel", radius=radius + 3, fill=glow)
        _shape(draw, "rounded_rectangle", (x, y, x + width, y + height), "panel", radius=radius,
               fill=colors["surface"], outline=colors["edge"], width=2)
        _shape(draw, "rounded_rectangle", (x + 2, y + 2, x + width - 2, y + height - 2), "panel",
               radius=max(0, radius - 2), outline=_mix_color(colors["surface"], accent, 0.22), width=1)
    else:
        _shape(draw, "rounded_rectangle", (x, y, x + width, y + height), "panel", radius=radius,
               fill=colors["surface"], outline=colors["edge"], width=1)


def _draw_agent_header(image: Image.Image | None, draw: ImageDraw.ImageDraw, data: dict,
                       x: int, y: int, width: int, design: dict,
                       animation_time: float | None) -> tuple[int, int]:
    colors, scale = design["colors"], design["font_scale"]
    provider = data.get("provider", "")
    if not isinstance(draw, SceneCanvas):
        draw = SceneCanvas(image, design, draw=draw, prefix=f"agent.{provider}")
    accent = colors.get(provider, colors["subtle"])
    status = activity_state(data)
    state_key = "busy" if status == "busy" else data.get("state")
    state_color = colors.get(state_key, colors["unknown"])
    label = "忙碌" if status == "busy" else data.get("state_label") or {
        "working": "工作中", "waiting": "等待输入", "idle": "空闲", "offline": "未接入",
        "unknown": "状态未知",
    }.get(data.get("state"), "状态未知")

    left, right = x + 28, x + width - 28
    logo_brand = _PROVIDER_LOGOS.get(provider)
    icon = logo(logo_brand) if logo_brand else None
    title_x = left
    if image is not None and icon is not None:
        title_x += 42 if logo_brand == "chatgpt" else 36

    state_font = font(18, True, scale=scale)
    title_font = font(32 if provider != "antigravity" else 29, True, scale=scale)
    tag_font = font(18, False, scale=scale)
    chip_width = max(102, int(draw.textlength(label, font=state_font)) + 42)
    chip_left = right - chip_width
    _shape(draw, "rounded_rectangle", (chip_left, y + 24, right, y + 60), "status_badge", radius=18,
           fill=_mix_color(colors["surface"], state_color, 0.12))
    if is_animated(data):
        period = animation_period(data) or 2.0
        _draw_spinner(draw, (chip_left + 17, y + 42), state_color, colors["surface"], animation_time, period)
    else:
        _shape(draw, "ellipse", (chip_left + 13, y + 38, chip_left + 21, y + 46),
               "status_indicator", fill=state_color)
    _text(draw, (chip_left + 29, y + 27), short(draw, label, state_font, chip_width - 39),
          "status_text", fill=state_color, font=state_font)

    available = max(0, chip_left - title_x - 10)
    name = short(draw, data.get("name", "Agent"), title_font, available)
    title_y = y + 17
    _, ink_top, _, ink_bottom = draw.textbbox((title_x, title_y), name, font=title_font)
    center_y = (ink_top + ink_bottom) / 2
    if image is not None and icon is not None:
        icon = icon.copy()
        icon.thumbnail((27, 27), Image.Resampling.LANCZOS)
        if logo_brand == "chatgpt":
            badge_size = 34
            badge_y = round(center_y - badge_size / 2)
            _shape(draw, "rounded_rectangle", (left - 3, badge_y, left - 3 + badge_size, badge_y + badge_size),
                   "logo", radius=9, fill="#101010")
            icon_x = left - 3 + (badge_size - icon.width) // 2
        else:
            icon_x = left
        _paste(image, draw, icon, (icon_x, round(center_y - icon.height / 2)), "logo", icon)
    _text(draw, (title_x, title_y), name, "name", fill=accent, font=title_font)
    title_width = draw.textlength(name, font=title_font)
    tag = _PROVIDER_TAGS.get(provider)
    if tag:
        tag_x = title_x + title_width + 10
        remaining = chip_left - tag_x - 8
        if remaining >= 30:
            tag = short(draw, tag, tag_font, remaining)
            _, tag_top, _, tag_bottom = draw.textbbox((0, 0), tag, font=tag_font)
            tag_y = round(center_y - (tag_top + tag_bottom) / 2)
            _text(draw, (tag_x, tag_y), tag, "brand", fill=colors["subtle"], font=tag_font)
    return left, right


def _draw_progress(draw: ImageDraw.ImageDraw, left: int, right: int, y: int, data: dict,
                   design: dict, metrics: dict, compact: bool = False) -> None:
    colors, scale = design["colors"], design["font_scale"]
    provider = metrics["provider"]
    window = metrics["primary"]
    title = "周额度剩余"
    percent = _window_percent(window)
    title_font = font(18 if compact else 20, scale=scale)
    pct_font = font(20 if compact else 22, True, True, scale)
    usable = right - left
    if percent is None:
        text = "额度待更新"
        _text(draw, (left, y), short(draw, text, title_font, usable), "weekly_label",
              fill=colors["muted"], font=title_font)
    else:
        title_width = max(60, usable - int(draw.textlength(f"{percent:g}%", font=pct_font)) - 20)
        _text(draw, (left, y), short(draw, title, title_font, title_width), "weekly_label",
              fill=colors["muted"], font=title_font)
    pct_text = f"{percent:g}%" if percent is not None else "—"
    _text(draw, (right - draw.textlength(pct_text, font=pct_font), y - 1), pct_text, "weekly_percent",
          fill=colors.get(provider, colors["text"]), font=pct_font, visible=percent is not None)
    bar_y = y + (28 if compact else 32)
    _shape(draw, "rounded_rectangle", (left, bar_y, right, bar_y + 7), "weekly_bar",
           radius=4, fill=colors["surface_alt"])
    if percent is not None and percent > 0:
        _shape(draw, "rounded_rectangle",
               (left, bar_y, left + max(5, round((right - left) * percent / 100)), bar_y + 7),
               "weekly_bar", radius=4, fill=colors.get(provider, colors["working"]))


def _draw_plan(draw: ImageDraw.ImageDraw, data: dict, x: int, y: int, width: int,
               design: dict, compact: bool = False) -> None:
    colors, scale = design["colors"], design["font_scale"]
    subscription = data.get("subscription") or {}
    if compact:
        prefix_font, value_font, detail_font = font(18, scale=scale), font(22, True, scale=scale), font(18, scale=scale)
        line_gap = 24
    else:
        prefix_font, value_font, detail_font = font(16, scale=scale), font(22, True, scale=scale), font(16, scale=scale)
        line_gap = 27
    _text(draw, (x, y), "订阅套餐", "plan_label", fill=colors["muted"], font=prefix_font)
    _text(draw, (x, y + line_gap), short(draw, subscription.get("plan_name") or "套餐待填写", value_font, width),
          "plan_name", fill=colors["text"] if subscription.get("plan_name") else colors["subtle"], font=value_font)
    spec = subscription.get("plan_spec") or "规格待填写"
    _text(draw, (x, y + line_gap * 2), short(draw, spec, detail_font, width), "plan_spec",
          fill=colors["muted"], font=detail_font)
    expiry = expiry_text(subscription.get("expires_at"))
    _text(draw, (x, y + line_gap * 3), short(draw, expiry, detail_font, width), "plan_expiry",
          fill=colors["subtle"], font=detail_font)


def _draw_token_block(draw: ImageDraw.ImageDraw, data: dict, x: int, y: int, width: int,
                      design: dict, metrics: dict, size: int = 38) -> None:
    colors, scale = design["colors"], design["font_scale"]
    tokens = metrics["tokens"]
    value = metrics["token_value"]
    main = number(value if isinstance(value, int) and not isinstance(value, bool) else None)
    label = metrics["date_label"]
    input_text = f"入 {number(tokens.get('input'))}  /  "
    output_text = f"出 {number(tokens.get('output'))}"
    tail = f"缓存 {number(tokens.get('cached'))}"
    label_font, main_font = font(16, scale=scale), font(size, True, True, scale)
    detail_font, tail_font = font(16, scale=scale), font(14, scale=scale)
    _text(draw, (x, y), short(draw, label, label_font, width), "tokens_label", fill=colors["muted"], font=label_font)
    _text(draw, (x, y + 19), main, "tokens_total", fill=colors["text"], font=main_font)
    main_bottom = y + 19 + main_font.size + 4
    input_width = min(width, draw.textlength(clean(input_text), font=detail_font) + draw.textlength(" ", font=detail_font))
    _text(draw, (x, main_bottom), short(draw, input_text, detail_font, input_width), "tokens_input",
          fill=colors["muted"], font=detail_font)
    _text(draw, (x + input_width, main_bottom), short(draw, output_text, detail_font, width - input_width),
          "tokens_output", fill=colors["muted"], font=detail_font)
    _text(draw, (x, main_bottom + 20), short(draw, tail, tail_font, width), "tokens_cached",
          fill=colors["subtle"], font=tail_font)


def _draw_footer_windows(draw: ImageDraw.ImageDraw, data: dict, x: int, y: int, width: int,
                         design: dict, metrics: dict) -> None:
    colors, scale = design["colors"], design["font_scale"]

    subscription = metrics["subscription"]
    windows = [(subscription.get("five_hour") or {}, "5小时"), (subscription.get("weekly") or {}, "周额度")]
    col_width = (width - 14) // 2
    label_font, value_font, reset_font = font(20, scale=scale), font(25, True, True, scale), font(18, scale=scale)
    for index, (window, title) in enumerate(windows):
        start_x = x + index * (col_width + 14)
        percent = _window_percent(window)
        text_value = "100%" if window.get("unlimited") else "—" if percent is None else f"{percent:g}%"
        label_id, value_id, reset_id = (("five_hour_label", "five_hour_value", "five_hour_reset")
                                        if index == 0 else ("week_label", "week_value", "week_reset"))
        _text(draw, (start_x, y), title, label_id, fill=colors["muted"], font=label_font)
        _text(draw, (start_x + col_width - draw.textlength(text_value, font=value_font), y - 2), text_value,
              value_id, fill=colors.get(metrics["provider"], colors["text"]), font=value_font)
        reset = "订阅无限额" if window.get("unlimited") else _window_reset(window)
        _text(draw, (start_x, y + 31), short(draw, reset, reset_font, col_width), reset_id,
              fill=colors["subtle"], font=reset_font)


def _draw_agent_classic(image: Image.Image | None, draw: ImageDraw.ImageDraw, data: dict,
                        x: int, y: int, width: int, height: int, design: dict,
                        animation_time: float | None, now: datetime) -> None:
    colors, scale = design["colors"], design["font_scale"]
    provider = data.get("provider", "")
    accent = colors.get(provider, colors["subtle"])
    _card(draw, x, y, width, height, design, provider)
    left, right = _draw_agent_header(image, draw, data, x, y, width, design, animation_time)
    metrics = _agent_metrics(data, now.date().isoformat())
    small, large = font(20, scale=scale), font(56, True, True, scale)
    label = "正在进行中的任务"
    count = str(metrics["count"]) if isinstance(metrics["count"], int) else "0"
    _text(draw, (left, y + 75), label, "task_label", fill=colors["muted"], font=small)
    _text(draw, (left, y + 101), count, "task_count", fill=colors["text"], font=large)
    count_width = draw.textlength(count, font=large)
    _text(draw, (left + count_width + 16, y + 131), "个任务", "task_unit",
          fill=colors["muted"], font=font(21, scale=scale))
    metadata_x = left + 215
    _draw_plan(draw, data, metadata_x, y + 72, right - metadata_x, design, compact=True)
    _draw_progress(draw, left, right, y + 179, data, design, metrics)

    token_y = y + 230
    _text(draw, (left, token_y), short(draw, metrics["date_label"], font(16, scale=scale), 190),
          "tokens_label", fill=colors["muted"], font=font(16, scale=scale))
    token_value = number(metrics["token_value"] if isinstance(metrics["token_value"], int) else None)
    _text(draw, (left, token_y + 20), token_value, "tokens_total", fill=colors["text"],
          font=font(38, True, True, scale))
    input_text = f"入 {number((metrics['tokens'] or {}).get('input'))} / "
    output_text = f"出 {number((metrics['tokens'] or {}).get('output'))}"
    detail_font = font(17, scale=scale)
    detail_x, detail_y = left + 205, token_y + 10
    detail_width = right - left - 210
    input_width = min(detail_width, draw.textlength(clean(input_text), font=detail_font) +
                      draw.textlength(" ", font=detail_font))
    cached = f"缓存 {number((metrics['tokens'] or {}).get('cached'))}"
    _text(draw, (detail_x, detail_y), short(draw, input_text, detail_font, input_width),
          "tokens_input", fill=colors["muted"], font=detail_font)
    _text(draw, (detail_x + input_width, detail_y), short(draw, output_text, detail_font, detail_width - input_width),
          "tokens_output", fill=colors["muted"], font=detail_font)
    _text(draw, (left + 205, token_y + 39), short(draw, cached, font(15, scale=scale), right - left - 210),
          "tokens_cached", fill=colors["subtle"], font=font(15, scale=scale))
    _shape(draw, "line", (left, y + 293, right, y + 293), "quota_rule", fill=colors["edge"], width=1)
    _draw_footer_windows(draw, data, left, y + 301, right - left, design, metrics)
    stale = bool(data.get("stale"))
    _text(draw, (left, min(y + height + 3, SIZE[1] - 22)), "数据待检查更新" if stale else "",
          "stale_note", fill=colors["subtle"], font=font(14, scale=scale), visible=stale)


def _draw_agent_aurora(image: Image.Image | None, draw: ImageDraw.ImageDraw, data: dict,
                       x: int, y: int, width: int, height: int, design: dict,
                       animation_time: float | None, now: datetime) -> None:
    colors, scale = design["colors"], design["font_scale"]
    provider = data.get("provider", "")
    _card(draw, x, y, width, height, design, provider)
    left, right = _draw_agent_header(image, draw, data, x, y, width, design, animation_time)
    metrics = _agent_metrics(data, now.date().isoformat())
    count = str(metrics["count"]) if isinstance(metrics["count"], int) else "0"
    label = "正在进行中的任务"
    count_font = font(54, True, True, scale)
    _text(draw, (left, y + 75), label, "task_label", fill=colors["muted"], font=font(18, scale=scale))
    _text(draw, (left, y + 99), count, "task_count", fill=colors["text"], font=count_font)
    _text(draw, (left + draw.textlength(count, font=count_font) + 14, y + 128), "个任务", "task_unit",
          fill=colors["muted"], font=font(19, scale=scale))

    quota_left = x + width // 2 + 5
    _draw_progress(draw, quota_left, right, y + 85, data, design, metrics, compact=True)
    weekly = metrics["primary"]
    _text(draw, (quota_left, y + 123), short(draw, _window_reset(weekly), font(18, scale=scale), right - quota_left),
          "weekly_bar_reset", fill=colors["subtle"], font=font(18, scale=scale))

    plan_x = left
    plan_y = y + 157
    plan_width = right - left
    subscription = metrics["subscription"]
    line_font = font(18, scale=scale)
    plan_prefix = "订阅套餐 "
    plan_label_width = draw.textlength(plan_prefix, font=line_font)
    displayed_name = short(draw, subscription.get("plan_name") or "套餐待填写", line_font,
                           max(0, plan_width - plan_label_width))
    _text(draw, (left, plan_y), plan_prefix, "plan_label", fill=colors["subtle"], font=line_font)
    _text(draw, (left + plan_label_width, plan_y), displayed_name, "plan_name",
          fill=colors["subtle"], font=line_font)
    detail_font = font(18, scale=scale)
    expiry = expiry_text(subscription.get("expires_at"))
    expiry_width = draw.textlength(expiry, font=detail_font)
    spec_width = max(0, plan_width - expiry_width - 16)
    _text(draw, (left, plan_y + 24), short(draw, "规格 " + (subscription.get("plan_spec") or "待填写"),
                                          detail_font, spec_width), "plan_spec",
          fill=colors["muted"], font=detail_font)
    _text(draw, (right - expiry_width, plan_y + 24), expiry, "plan_expiry", fill=colors["subtle"], font=detail_font)

    band_y = y + 211
    band_bottom = y + height - 14
    _shape(draw, "rounded_rectangle", (x + 16, band_y, x + width - 16, band_bottom), "tokens_panel",
           radius=max(12, design["radius"] - 5), fill=colors["surface_alt"],
           outline=_mix_color(colors["edge"], colors["codex"], 0.24), width=1)
    tokens = metrics["tokens"] or {}
    token_total = number(metrics["token_value"] if isinstance(metrics["token_value"], int) else None)
    _text(draw, (left + 10, band_y + 12), short(draw, metrics["date_label"], font(15, scale=scale), 190),
          "tokens_label", fill=colors["muted"], font=font(15, scale=scale))
    _text(draw, (left + 10, band_y + 30), token_total, "tokens_total", fill=colors["text"],
          font=font(34, True, True, scale))
    input_text = f"入 {number(tokens.get('input'))} / "
    output_text = f"出 {number(tokens.get('output'))}"
    detail_font = font(16, scale=scale)
    detail_x = left + 198
    detail_width = right - detail_x - 8
    input_width = min(detail_width, draw.textlength(clean(input_text), font=detail_font) +
                      draw.textlength(" ", font=detail_font))
    cached = f"缓存 {number(tokens.get('cached'))}"
    _text(draw, (detail_x, band_y + 28), short(draw, input_text, detail_font, input_width),
          "tokens_input", fill=colors["muted"], font=detail_font)
    _text(draw, (detail_x + input_width, band_y + 28), short(draw, output_text, detail_font, detail_width - input_width),
          "tokens_output", fill=colors["muted"], font=detail_font)
    _text(draw, (detail_x, band_y + 54), short(draw, cached, font(14, scale=scale), right - detail_x - 8),
          "tokens_cached", fill=colors["subtle"], font=font(14, scale=scale))
    _draw_footer_windows(draw, data, left + 8, band_y + 82, right - left - 16, design, metrics)
    stale = bool(data.get("stale"))
    _text(draw, (left, min(y + height + 3, SIZE[1] - 22)), "数据待检查更新" if stale else "",
          "stale_note", fill=colors["subtle"], font=font(14, scale=scale), visible=stale)


def _draw_agent_paper(image: Image.Image | None, draw: ImageDraw.ImageDraw, data: dict,
                      x: int, y: int, width: int, height: int, design: dict,
                      animation_time: float | None, now: datetime) -> None:
    colors, scale = design["colors"], design["font_scale"]
    provider = data.get("provider", "")
    _card(draw, x, y, width, height, design, provider)
    left, right = _draw_agent_header(image, draw, data, x, y, width, design, animation_time)
    metrics = _agent_metrics(data, now.date().isoformat())
    label = "ACTIVE TASKS  ·  进行中任务"
    count = str(metrics["count"]) if isinstance(metrics["count"], int) else "0"
    count_font = font(54, True, True, scale)
    _text(draw, (left, y + 72), label, "task_label", fill=colors["muted"], font=font(17, scale=scale))
    _text(draw, (left, y + 92), count, "task_count", fill=colors["text"], font=count_font)
    _text(draw, (left + draw.textlength(count, font=count_font) + 13, y + 119), "个任务", "task_unit",
          fill=colors["muted"], font=font(17, scale=scale))

    plan_x = x + width // 2 + 7
    _draw_plan(draw, data, plan_x, y + 66, right - plan_x, design, compact=True)
    _shape(draw, "line", (left, y + 166, right, y + 166), "quota_rule", fill=colors["edge"], width=1)

    quota = metrics["primary"]
    title = "周额度剩余"
    percent = _window_percent(quota)
    pct_text = "—" if percent is None else f"{percent:g}%"
    _text(draw, (left, y + 175), short(draw, title, font(18, scale=scale), width - 140), "weekly_label",
          fill=colors["muted"], font=font(18, scale=scale))
    pct_font = font(23, True, True, scale)
    _text(draw, (right - draw.textlength(pct_text, font=pct_font), y + 171), pct_text, "weekly_percent",
          fill=colors.get(provider, colors["text"]), font=pct_font)
    bar_y = y + 202
    _shape(draw, "rectangle", (left, bar_y, right, bar_y + 6), "weekly_bar", fill=colors["surface_alt"])
    if percent is not None and percent > 0:
        _shape(draw, "rectangle", (left, bar_y, left + max(5, round((right - left) * percent / 100)), bar_y + 6),
               "weekly_bar", fill=colors.get(provider, colors["working"]))

    tokens = metrics["tokens"] or {}
    token_value = number(metrics["token_value"] if isinstance(metrics["token_value"], int) else None)
    _shape(draw, "line", (left, y + 219, right, y + 219), "tokens_rule", fill=colors["edge"], width=1)
    _text(draw, (left, y + 228), short(draw, metrics["date_label"], font(15, scale=scale), 185),
          "tokens_label", fill=colors["muted"], font=font(15, scale=scale))
    _text(draw, (left, y + 248), token_value, "tokens_total", fill=colors["text"],
          font=font(34, True, True, scale))
    detail_x = left + 200
    input_text = f"输入 {number(tokens.get('input'))}  ·  "
    output_text = f"输出 {number(tokens.get('output'))}"
    detail_font = font(15, scale=scale)
    detail_width = right - detail_x
    input_width = min(detail_width, draw.textlength(clean(input_text), font=detail_font) +
                      draw.textlength(" ", font=detail_font))
    cache_detail = f"缓存 {number(tokens.get('cached'))}"
    _text(draw, (detail_x, y + 243), short(draw, input_text, detail_font, input_width),
          "tokens_input", fill=colors["muted"], font=detail_font)
    _text(draw, (detail_x + input_width, y + 243), short(draw, output_text, detail_font, detail_width - input_width),
          "tokens_output", fill=colors["muted"], font=detail_font)
    _text(draw, (detail_x, y + 269), short(draw, cache_detail, font(14, scale=scale), right - detail_x),
          "tokens_cached", fill=colors["subtle"], font=font(14, scale=scale))
    _shape(draw, "line", (left, y + 299, right, y + 299), "quota_rule", fill=colors["edge"], width=1)
    _draw_footer_windows(draw, data, left, y + 307, right - left, design, metrics)
    stale = bool(data.get("stale"))
    _text(draw, (left, min(y + height + 3, SIZE[1] - 22)), "数据待检查更新" if stale else "",
          "stale_note", fill=colors["subtle"], font=font(14, scale=scale), visible=stale)


def draw_agent(draw: ImageDraw.ImageDraw, data: dict, x: int, width: int, profile: dict | None = None,
               animation_time: float | None = None, image: Image.Image | None = None,
               y: int = 76, height: int = 374, now: datetime | None = None) -> None:
    """Draw one Agent card; the optional parameters keep the legacy API usable."""
    design = _resolve_design(profile)
    provider = data.get("provider", "")
    if not isinstance(draw, SceneCanvas):
        draw = SceneCanvas(image, design, draw=draw, prefix=f"agent.{provider}")
    now = now or datetime.now().astimezone()
    renderer = {
        "classic": _draw_agent_classic,
        "aurora": _draw_agent_aurora,
        "paper": _draw_agent_paper,
    }[design["layout"]]
    group_id = f"agent.{provider}"
    if group_id in ELEMENT_DEFINITIONS:
        with draw.group(group_id, (x, y, x + width, y + height)):
            renderer(image, draw, data, x, y, width, height, design, animation_time, now)
    else:
        renderer(image, draw, data, x, y, width, height, design, animation_time, now)


def draw_account(image: Image.Image, draw: ImageDraw.ImageDraw, data: dict, x: int, y: int,
                 width: int = 382, profile: dict | None = None, height: int = 178) -> None:
    design = _resolve_design(profile)
    account_id = data.get("id", "")
    group_id = f"account.{account_id}"
    if not isinstance(draw, SceneCanvas):
        draw = SceneCanvas(image, design, draw=draw,
                           prefix=group_id if group_id in ELEMENT_DEFINITIONS else "")
    if group_id in ELEMENT_DEFINITIONS:
        with draw.group(group_id, (x, y, x + width, y + height)):
            _draw_account_elements(image, draw, data, x, y, width, height, design)
    else:
        _draw_account_elements(image, draw, data, x, y, width, height, design)


def _draw_account_elements(image: Image.Image, draw: ImageDraw.ImageDraw | SceneCanvas, data: dict,
                           x: int, y: int, width: int, height: int, design: dict) -> None:
    colors, scale, layout = design["colors"], design["font_scale"], design["layout"]
    _card(draw, x, y, width, height, design, data.get("brand", ""), account=True)
    icon = logo(data.get("brand", ""))
    if icon is not None:
        icon = icon.copy()
        icon.thumbnail((36 if layout != "paper" else 32, 36 if layout != "paper" else 32), Image.Resampling.LANCZOS)
        icon_x = x + width - 20 - icon.width
        _paste(image, draw, icon, (icon_x, y + 15), "logo", icon)
    left, right = x + 19, x + width - 19
    compact = width < 300
    title = ("课题组" if data.get("id") == "deepseek_lab" else "个人") if compact else data.get("name", "账户")
    title_font = font(19 if compact else 20, True, scale=scale)
    subtitle_font = font(18, scale=scale)
    _text(draw, (left, y + 14), short(draw, title, title_font, width - 76), "title",
          fill=colors["text"], font=title_font)
    weekly = data.get("kind") == "weekly"
    subtitle = "周额度剩余" if weekly else "DeepSeek 余额" if compact else "账户余额"
    _text(draw, (left, y + 42), subtitle, "subtitle", fill=colors["muted"], font=subtitle_font)
    value, unit = "—", ""
    if data.get("known"):
        if weekly and data.get("remaining_percent") is not None:
            try:
                value = f"{min(100.0, max(0.0, float(data['remaining_percent']))):g}%"
            except (TypeError, ValueError, OverflowError):
                pass
        elif data.get("value") is not None:
            try:
                value = f"{Decimal(str(data['value'])):,.2f}"
                unit = {"CNY": "元", "USD": "USD"}.get(data.get("currency"), data.get("currency") or "")
            except (InvalidOperation, ValueError):
                pass
    value_font = font(34 if layout == "paper" else 36, True, True, scale)
    amount_width = width - 38
    if unit:
        amount_width -= int(draw.textlength(unit, font=font(15, scale=scale))) + 10
    _text(draw, (left, y + 75), short(draw, value, value_font, amount_width), "value",
          fill=colors["text"], font=value_font)
    unit_font = font(15, scale=scale)
    if unit:
        unit_x = min(right - 30, left + draw.textlength(value, font=value_font) + 9)
    else:
        unit_x = left
    _text(draw, (unit_x, y + 94), unit, "unit", fill=colors["muted"], font=unit_font, visible=bool(unit))
    if layout == "paper":
        _shape(draw, "line", (left, y + 121, right, y + 121), "rule", fill=colors["edge"], width=1)
        line_y = y + 132
    else:
        line_y = y + 130
    if weekly:
        line = _window_reset(data) if data.get("reset_at") else (
            f"已用 {data['used']} / {data['limit']}" if data.get("used") is not None and data.get("limit") is not None else "周额度")
    elif data.get("known"):
        line = "账户已用尽" if data.get("is_available") is False else "可用余额"
    else:
        line = "数据待补充"
    _text(draw, (left, line_y), short(draw, line, font(18, scale=scale), width - 38), "detail",
          fill=colors["muted"], font=font(18, scale=scale))
    stale = bool(data.get("stale"))
    _text(draw, (left, y + height - 23), "数据待检查更新" if stale else "", "stale_note",
          fill=colors["subtle"], font=font(14, scale=scale), visible=stale)


@lru_cache(maxsize=4)
def _aurora_background(background: str, accent: str) -> Image.Image:
    """Cache the static wash so animated status arcs need no per-frame blur."""
    glow = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    rgb = _hex_rgb(accent)
    for radius, alpha in ((420, 6), (340, 7), (260, 8), (190, 8), (130, 7)):
        glow_draw.ellipse((960 - radius, 520 - radius, 960 + radius, 520 + radius),
                          fill=(*rgb, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(46))
    base = Image.new("RGBA", SIZE, background)
    base.alpha_composite(glow)
    return base.convert("RGB")


def _draw_background(image: Image.Image, design: dict) -> Image.Image:
    colors = design["colors"]
    if design["layout"] == "aurora":
        return _aurora_background(colors["background"], colors["antigravity"]).copy()
    image.paste(colors["background"], (0, 0, *SIZE))
    return image


def _draw_page_header(draw: ImageDraw.ImageDraw, snapshot: dict, now: datetime, design: dict) -> None:
    colors, scale, layout = design["colors"], design["font_scale"], design["layout"]
    with draw.group("header", (0, 0, SIZE[0], 62)):
        if layout == "paper":
            _shape(draw, "rectangle", (0, 0, SIZE[0], 62), "panel", fill=colors["surface"])
            _shape(draw, "rectangle", (0, 59, SIZE[0], 62), "rule", fill=colors["codex"])
        elif layout == "aurora":
            _shape(draw, "line", (28, 59, 1892, 59), "rule",
                   fill=_mix_color(colors["edge"], colors["antigravity"], 0.25), width=1)
        else:
            _shape(draw, "line", (28, 59, 1892, 59), "rule", fill=colors["edge"], width=1)
        _text(draw, (30, 17), "AGENT DESK", "title", fill=colors["text"], font=font(25, True, True, scale))
        _text(draw, (235, 22), "WINDOWS  /  KV 9.16", "platform", fill=colors["muted"],
              font=font(18, mono=True, scale=scale))
        weekdays = "一二三四五六日"
        _text(draw, (1100, 22), now.strftime("%m/%d") + f" 周{weekdays[now.weekday()]}", "date",
              fill=colors["muted"], font=font(18, scale=scale))
        connected = snapshot.get("display", {}).get("connected", False)
        connection = "USB 已连接" if connected else "本机预览"
        _text(draw, (1250, 22), connection, "connection",
              fill=colors["working"] if connected else colors["muted"], font=font(18, scale=scale))
        badge = "演示数据 / DEMO" if snapshot.get("demo") else "实时仪表盘 / LIVE"
        badge_color = colors["waiting"] if snapshot.get("demo") else colors["working"]
        _shape(draw, "ellipse", (1465, 28, 1472, 35), "live_dot", fill=badge_color)
        _text(draw, (1485, 21), badge, "live_label", fill=colors["muted"], font=font(17, scale=scale))
        _text(draw, (1736, 15), now.strftime("%H:%M:%S"), "clock", fill=colors["text"],
              font=font(30, True, True, scale))


def _render(snapshot: dict, now: datetime | None, profile: dict | None,
            animation_time: float | None, *, capture: bool) -> tuple[Image.Image, list[dict]]:
    now = now or datetime.now().astimezone()
    design = _resolve_design(profile)
    image = _draw_background(Image.new("RGB", SIZE), design)
    draw = SceneCanvas(image, design, capture=capture)
    colors, geometry = design["colors"], design["geometry"]
    _draw_page_header(draw, snapshot, now, design)
    margin, gap, account_width = geometry["margin"], geometry["gap"], geometry["account_width"]
    top, bottom = geometry["top"], geometry["bottom"]
    agent_width = design["agent_width"]
    card_height = bottom - top
    agents = snapshot.get("agents", [])
    for index, agent in enumerate(agents[:3]):
        x = margin + index * (agent_width + gap)
        draw_agent(draw, agent, x, agent_width, design, animation_time, image=image,
                   y=top, height=card_height, now=now)
    account_x = SIZE[0] - margin - account_width
    account_gap = max(12, gap)
    account_height = (card_height - account_gap) // 2
    accounts = snapshot.get("accounts", [])
    for index, account in enumerate(accounts[:2]):
        y = top + index * (account_height + account_gap)
        draw_account(image, draw, account, account_x, y, width=account_width,
                     profile=design, height=account_height)

    stale_account_indices = [index for index, account in enumerate(accounts[:2])
                             if isinstance(account, dict) and account.get("stale")
                             and f"account.{account.get('id', '')}" in ELEMENT_DEFINITIONS]
    page_stale = bool(stale_account_indices) and not any(agent.get("stale") for agent in agents[:3] if isinstance(agent, dict))
    _text(draw, (margin, SIZE[1] - 22), "数据待检查更新", "page.stale_note",
          fill=colors["subtle"], font=font(14, scale=design["font_scale"]), visible=page_stale)
    return image, draw.manifest() if capture else []


def render(snapshot: dict, now: datetime | None = None, profile: dict | None = None,
           animation_time: float | None = None) -> Image.Image:
    """Render the same 1920x480 image for preview and LCD output."""
    return _render(snapshot, now, profile, animation_time, capture=False)[0]


def render_scene(snapshot: dict, now: datetime | None = None, profile: dict | None = None,
                 animation_time: float | None = None) -> tuple[Image.Image, list[dict]]:
    """Render an image plus its data-free semantic element manifest."""
    return _render(snapshot, now, profile, animation_time, capture=True)
