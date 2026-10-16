"""Validated, data-only UI profile packages for the raster renderer.

Imported profiles contain only colors and bounded geometry. They cannot add
assets, file paths, URLs, templates, markup, or executable code.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any

from .ui_elements import ELEMENT_DEFINITIONS


SCHEMA_VERSION = 1
CANVAS = [1920, 480]
MAX_PROFILE_BYTES = 64 * 1024
MAX_CUSTOM_PROFILES = 32

LAYOUTS = ("classic", "aurora", "paper")
COLOR_ROLES = (
    "background",
    "surface",
    "surface_alt",
    "edge",
    "text",
    "muted",
    "subtle",
    "codex",
    "zcode",
    "antigravity",
    "working",
    "waiting",
    "idle",
    "offline",
    "unknown",
    "busy",
)
GEOMETRY_DEFAULTS = {
    "margin": 24,
    "gap": 16,
    "account_width": 240,
    "top": 76,
    "bottom": 450,
}

_PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}\Z")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
_FONT_FAMILY_ID = re.compile(r"[a-z0-9_-]{1,64}\Z")
_ROOT_KEYS = frozenset(
    {"schema_version", "id", "name", "layout", "canvas", "colors", "font_scale", "radius", "geometry", "elements"}
)
_GEOMETRY_RANGES = {
    "margin": (16, 64),
    "gap": (8, 32),
    "account_width": (200, 320),
    "top": (64, 104),
    "bottom": (420, 464),
}

_PALETTES: dict[str, dict[str, str]] = {
    "classic": {
        "background": "#090F16",
        "surface": "#101A25",
        "surface_alt": "#182635",
        "edge": "#243344",
        "text": "#F0F5FA",
        "muted": "#8FA3B7",
        "subtle": "#748CA3",
        "codex": "#78E7C2",
        "zcode": "#F7BA71",
        "antigravity": "#9BB7FF",
        "working": "#78E7C2",
        "waiting": "#F7BA71",
        "idle": "#A4B8CD",
        "offline": "#718296",
        "unknown": "#A4B8CD",
        "busy": "#F16D72",
    },
    "aurora": {
        "background": "#080D1C",
        "surface": "#111A30",
        "surface_alt": "#0C1428",
        "edge": "#283454",
        "text": "#F1F5FF",
        "muted": "#99AAC4",
        "subtle": "#7386A5",
        "codex": "#79E9C4",
        "zcode": "#FFBD8B",
        "antigravity": "#B0A1FF",
        "working": "#79E9C4",
        "waiting": "#FFC078",
        "idle": "#A6B8C7",
        "offline": "#71899E",
        "unknown": "#A6B8C7",
        "busy": "#FF6679",
    },
    "paper": {
        "background": "#F4F1EB",
        "surface": "#FFFEFA",
        "surface_alt": "#F4F1EB",
        "edge": "#D8D4CB",
        "text": "#202B34",
        "muted": "#66727A",
        "subtle": "#7A837F",
        "codex": "#157B69",
        "zcode": "#A5602D",
        "antigravity": "#5964AD",
        "working": "#1C8C69",
        "waiting": "#AD6906",
        "idle": "#6B7882",
        "offline": "#88929A",
        "unknown": "#6B7882",
        "busy": "#C82631",
    },
}

_NAMES = {
    "classic": "经典深色",
    "aurora": "Aurora 极光",
    "paper": "Paper 纸感",
}

BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    profile_id: {
        "schema_version": SCHEMA_VERSION,
        "id": profile_id,
        "name": _NAMES[profile_id],
        "layout": profile_id,
        "canvas": CANVAS.copy(),
        "colors": deepcopy(_PALETTES[profile_id]),
        "font_scale": 100,
        "radius": {"classic": 18, "aurora": 24, "paper": 10}[profile_id],
        "geometry": GEOMETRY_DEFAULTS.copy(),
        "elements": {},
    }
    for profile_id in LAYOUTS
}


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON numeric constant: {value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


class UIProfiles:
    """Built-in and imported data-only profiles.

    ``directory=None`` stores imported profiles in memory, suitable for demo
    mode and tests. Persistent profiles use generated ``<id>.json`` names.
    """

    def __init__(self, directory: Path | str | None = None):
        self.directory = Path(directory) if directory is not None else None
        self._memory: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            summaries = [self._summary(profile, True) for profile in BUILTIN_PROFILES.values()]
            custom = self._custom_profiles()
            summaries.extend(self._summary(profile, False) for profile in custom.values())
            return summaries

    def get(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            builtin = BUILTIN_PROFILES.get(profile_id) if isinstance(profile_id, str) else None
            if builtin is not None:
                return deepcopy(builtin)
            if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
                return deepcopy(BUILTIN_PROFILES["classic"])
            custom = self._load_custom(profile_id)
            return custom if custom is not None else deepcopy(BUILTIN_PROFILES["classic"])

    def contains(self, profile_id: str) -> bool:
        with self._lock:
            if not isinstance(profile_id, str):
                return False
            if profile_id in BUILTIN_PROFILES:
                return True
            if not _PROFILE_ID.fullmatch(profile_id):
                return False
            return self._load_custom(profile_id) is not None

    def import_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        normalized = self.validate(profile)
        profile_id = normalized["id"]
        if profile_id in BUILTIN_PROFILES:
            raise ValueError("custom profile id conflicts with a built-in profile")

        with self._lock:
            if self.directory is None:
                if profile_id not in self._memory and len(self._memory) >= MAX_CUSTOM_PROFILES:
                    raise ValueError("too many custom UI profiles")
                self._memory[profile_id] = deepcopy(normalized)
            else:
                self._write_custom(normalized)
            return deepcopy(normalized)

    def export(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
                raise ValueError("invalid UI profile id")
            builtin = BUILTIN_PROFILES.get(profile_id)
            if builtin is not None:
                return deepcopy(builtin)
            custom = self._load_custom(profile_id)
            if custom is None:
                raise ValueError("unknown UI profile")
            return custom

    @staticmethod
    def validate(profile: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize a draft without saving it.

        Built-in IDs are valid here so an editor can validate a built-in style
        draft. ``import_profile`` separately prevents replacing built-ins.
        """
        return UIProfiles._normalize(profile)

    @staticmethod
    def _summary(profile: dict[str, Any], builtin: bool) -> dict[str, Any]:
        return {
            "id": profile["id"],
            "name": profile["name"],
            "layout": profile["layout"],
            "builtin": builtin,
            "colors": deepcopy(profile["colors"]),
        }

    @staticmethod
    def _normalize(profile: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(profile, dict) or any(not isinstance(key, str) for key in profile):
            raise ValueError("UI profile must be a JSON object with string keys")
        unknown = set(profile) - _ROOT_KEYS
        if unknown:
            raise ValueError("unknown UI profile fields")

        version = profile.get("schema_version")
        if type(version) is not int or version != SCHEMA_VERSION:
            raise ValueError("unsupported UI profile schema_version")

        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise ValueError("id must be a lowercase profile slug")

        name = profile.get("name")
        if not isinstance(name, str):
            raise ValueError("name must be a string")
        name = name.strip()
        if not 1 <= len(name) <= 48 or _has_forbidden_text(name):
            raise ValueError("name must be a short plain-text label")

        layout = profile.get("layout")
        if not isinstance(layout, str) or layout not in LAYOUTS:
            raise ValueError("layout must be a supported built-in layout")

        canvas = profile.get("canvas", CANVAS)
        if (
            not isinstance(canvas, list)
            or len(canvas) != 2
            or any(type(value) is not int for value in canvas)
            or canvas != CANVAS
        ):
            raise ValueError("canvas must be [1920, 480]")

        base = BUILTIN_PROFILES[layout]
        colors = deepcopy(base["colors"])
        provided_colors = profile.get("colors", {})
        if not isinstance(provided_colors, dict) or any(not isinstance(key, str) for key in provided_colors):
            raise ValueError("colors must be an object")
        if set(provided_colors) - set(COLOR_ROLES):
            raise ValueError("unknown color role")
        for role, color in provided_colors.items():
            if not isinstance(color, str) or not _COLOR.fullmatch(color):
                raise ValueError(f"{role} must be a #RRGGBB color")
            colors[role] = color.upper()

        font_scale = profile.get("font_scale", base["font_scale"])
        if type(font_scale) is not int or not 90 <= font_scale <= 115:
            raise ValueError("font_scale must be an integer from 90 to 115")

        radius = profile.get("radius", base["radius"])
        if type(radius) is not int or not 0 <= radius <= 28:
            raise ValueError("radius must be an integer from 0 to 28")

        geometry = deepcopy(base["geometry"])
        provided_geometry = profile.get("geometry", {})
        if not isinstance(provided_geometry, dict) or any(not isinstance(key, str) for key in provided_geometry):
            raise ValueError("geometry must be an object")
        if set(provided_geometry) - set(GEOMETRY_DEFAULTS):
            raise ValueError("unknown geometry field")
        for key, value in provided_geometry.items():
            minimum, maximum = _GEOMETRY_RANGES[key]
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"geometry.{key} is outside its allowed range")
            geometry[key] = value
        _validate_geometry(geometry)

        elements = _normalize_elements(profile.get("elements", {}))

        normalized = {
            "schema_version": SCHEMA_VERSION,
            "id": profile_id,
            "name": name,
            "layout": layout,
            "canvas": CANVAS.copy(),
            "colors": colors,
            "font_scale": font_scale,
            "radius": radius,
            "geometry": geometry,
            "elements": elements,
        }
        _check_profile_size(normalized)
        return normalized

    def _load_custom(self, profile_id: str) -> dict[str, Any] | None:
        if self.directory is None:
            profile = self._memory.get(profile_id)
            return deepcopy(profile) if profile is not None else None
        path = self.directory / f"{profile_id}.json"
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PROFILE_BYTES:
                return None
            raw = path.read_bytes()
            if len(raw) > MAX_PROFILE_BYTES:
                return None
            decoded = raw.decode("utf-8", errors="strict")
            payload = json.loads(
                decoded,
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_constant,
            )
            normalized = self._normalize(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
            return None
        if normalized["id"] != profile_id or profile_id in BUILTIN_PROFILES:
            return None
        return normalized

    def _custom_profiles(self) -> dict[str, dict[str, Any]]:
        if self.directory is None:
            candidates = self._memory.items()
        else:
            try:
                if self.directory.is_symlink() or not self.directory.is_dir():
                    return {}
                filenames = sorted(self.directory.glob("*.json"), key=lambda item: item.name)
            except OSError:
                return {}
            candidates = ((path.stem, None) for path in filenames)

        profiles: dict[str, dict[str, Any]] = {}
        for profile_id, memory_profile in candidates:
            if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
                continue
            if profile_id in BUILTIN_PROFILES:
                continue
            profile = deepcopy(memory_profile) if self.directory is None else self._load_custom(profile_id)
            if profile is not None:
                profiles[profile_id] = profile
            if len(profiles) >= MAX_CUSTOM_PROFILES:
                break
        return dict(sorted(profiles.items(), key=lambda item: (item[1]["name"].casefold(), item[0])))

    def _write_custom(self, profile: dict[str, Any]) -> None:
        assert self.directory is not None
        profile_id = profile["id"]
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise ValueError("UI profile directory must be a regular directory")
        existing = self._custom_profiles()
        if profile_id not in existing and len(existing) >= MAX_CUSTOM_PROFILES:
            raise ValueError("too many custom UI profiles")
        path = self.directory / f"{profile_id}.json"
        if path.is_symlink():
            raise ValueError("UI profile path must not be a symlink")
        payload = json.dumps(profile, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n"
        if len(payload) > MAX_PROFILE_BYTES:
            raise ValueError("UI profile exceeds the 64 KiB limit")

        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=f".{profile_id}.", suffix=".tmp", dir=self.directory)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass


def _has_forbidden_text(value: str) -> bool:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return True
    lowered = value.casefold()
    return any(marker in lowered for marker in ("<", ">", "://", "javascript:", "file:")) or "/" in value or "\\" in value


def _normalize_elements(elements: object) -> dict[str, dict[str, Any]]:
    if not isinstance(elements, dict) or any(not isinstance(key, str) for key in elements):
        raise ValueError("elements must be an object")
    normalized: dict[str, dict[str, int]] = {}
    for element_id, override in elements.items():
        definition = ELEMENT_DEFINITIONS.get(element_id)
        if definition is None:
            raise ValueError("unknown UI element id")
        if not isinstance(override, dict) or any(not isinstance(key, str) for key in override):
            raise ValueError("element override must be an object")
        kind = definition["kind"]
        allowed = {"dx", "dy", "font_size", "font_family", "color", "bold", "italic"} if kind == "text" else {"dx", "dy"}
        if set(override) - allowed:
            raise ValueError("unsupported UI element override field")
        values: dict[str, Any] = {}
        for key, value in override.items():
            if key == "font_family":
                if not isinstance(value, str) or not _FONT_FAMILY_ID.fullmatch(value):
                    raise ValueError(f"elements.{element_id}.font_family must be an opaque lowercase id")
                values[key] = value
                continue
            if key == "color":
                if not isinstance(value, str) or not _COLOR.fullmatch(value):
                    raise ValueError(f"elements.{element_id}.color must be a #RRGGBB color")
                values[key] = value.upper()
                continue
            if key in {"bold", "italic"}:
                if type(value) is not bool:
                    raise ValueError(f"elements.{element_id}.{key} must be a boolean")
                values[key] = value
                continue
            if type(value) is not int:
                raise ValueError(f"elements.{element_id}.{key} must be an integer")
            minimum, maximum = {
                "dx": (-1920, 1920),
                "dy": (-480, 480),
                "font_size": (8, 96),
            }[key]
            if not minimum <= value <= maximum:
                raise ValueError(f"elements.{element_id}.{key} is outside its allowed range")
            values[key] = value
        if values:
            normalized[element_id] = values
    return normalized


def _validate_geometry(geometry: dict[str, int]) -> None:
    margin = geometry["margin"]
    gap = geometry["gap"]
    account_width = geometry["account_width"]
    top = geometry["top"]
    bottom = geometry["bottom"]
    agent_width = (CANVAS[0] - 2 * margin - account_width - 3 * gap) // 3
    account_height = (bottom - top - gap) // 2
    if agent_width < 400 or account_height < 140:
        raise ValueError("geometry does not fit the 1920 x 480 canvas")


def _check_profile_size(profile: dict[str, Any]) -> None:
    try:
        encoded = json.dumps(profile, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("UI profile must be JSON-safe") from exc
    if len(encoded) > MAX_PROFILE_BYTES:
        raise ValueError("UI profile exceeds the 64 KiB limit")
