"""Safe, local font catalogue and resolver for UI style profiles.

Only fonts enumerated in the Windows system/user font directories are
selectable. Public identifiers are opaque hashes of family names; paths stay
inside this module and are never returned by the catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import re
import unicodedata

from PIL import ImageFont


_FONT_ID = re.compile(r"[a-z0-9_-]{1,64}\Z")
_FONT_SUFFIXES = {".ttf", ".ttc", ".otf", ".otc"}
_BUILTIN_FAMILIES = {
    "ui": ("Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Arial"),
    "mono": ("Cascadia Mono", "Consolas", "Cascadia Code", "Courier New"),
}
_BUILTIN_NAMES = {"ui": "默认 UI 字体", "mono": "等宽字体"}


@dataclass(frozen=True)
class _Face:
    family: str
    key: str
    path: Path
    index: int
    style: str
    bold: bool
    italic: bool


def is_font_family_id(value: object) -> bool:
    """Return whether a value is a syntactically safe opaque family ID."""
    return isinstance(value, str) and _FONT_ID.fullmatch(value) is not None


def _family_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _safe_family_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > 128 or any(char in name for char in "\\/:"):
        return None
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in name):
        return None
    if re.search(r"\.(?:ttf|ttc|otf|otc)(?:\b|$)", name, re.IGNORECASE):
        return None
    return name


def _family_id(family: str) -> str:
    return "f-" + hashlib.sha256(_family_key(family).encode("utf-8")).hexdigest()[:24]


def _font_directories() -> tuple[Path, ...]:
    if os.name != "nt":
        return ()
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    directories = [windows / "Fonts"]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        directories.append(Path(local_app_data) / "Microsoft" / "Windows" / "Fonts")
    result = []
    seen = set()
    for directory in directories:
        try:
            resolved = directory.resolve(strict=True)
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key not in seen and resolved.is_dir():
            result.append(resolved)
            seen.add(key)
    return tuple(result)


def _style_flags(style: str) -> tuple[bool, bool]:
    compact = re.sub(r"[^a-z]", "", style.casefold())
    bold = any(part in compact for part in (
        "bold", "semibold", "demibold", "demi", "extrabold", "ultrabold", "heavy", "black",
    ))
    italic = "italic" in compact or "oblique" in compact
    return bold, italic


def _faces_in_file(path: Path) -> list[_Face]:
    suffix = path.suffix.casefold()
    maximum_faces = 64 if suffix in {".ttc", ".otc"} else 1
    faces: list[_Face] = []
    for index in range(maximum_faces):
        try:
            loaded = ImageFont.truetype(str(path), 12, index=index)
            family, style = loaded.getname()
        except (OSError, ValueError, AttributeError):
            if index == 0:
                break
            break
        safe_family = _safe_family_name(family)
        if safe_family is None:
            continue
        if not isinstance(style, str):
            style = "Regular"
        bold, italic = _style_flags(style)
        faces.append(_Face(safe_family, _family_key(safe_family), path, index, style, bold, italic))
        del loaded
    return faces


@lru_cache(maxsize=1)
def _font_faces() -> tuple[_Face, ...]:
    faces: list[_Face] = []
    visited: set[str] = set()
    for directory in _font_directories():
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue
        for path in entries:
            if path.suffix.casefold() not in _FONT_SUFFIXES:
                continue
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve(strict=True)
                if resolved.parent != directory:
                    continue
            except OSError:
                continue
            normalized_path = os.path.normcase(str(resolved))
            if normalized_path in visited:
                continue
            visited.add(normalized_path)
            faces.extend(_faces_in_file(resolved))
    return tuple(faces)


@lru_cache(maxsize=1)
def _family_index() -> tuple[dict[str, tuple[_Face, ...]], dict[str, str]]:
    grouped: dict[str, list[_Face]] = {}
    for face in _font_faces():
        grouped.setdefault(face.key, []).append(face)
    families = {key: tuple(items) for key, items in grouped.items()}
    ids = {_family_id(items[0].family): key for key, items in families.items()}
    return families, ids


def font_catalogue() -> list[dict[str, str]]:
    """Return safe UI/mono choices and installed family names, without paths."""
    families, _ = _family_index()
    catalogue = [{"id": key, "name": _BUILTIN_NAMES[key]} for key in ("ui", "mono")]
    for family_key, faces in sorted(families.items(), key=lambda item: item[1][0].family.casefold()):
        catalogue.append({"id": _family_id(faces[0].family), "name": faces[0].family})
    return catalogue


def _default_identity(default_font: ImageFont.ImageFont) -> tuple[str | None, bool, bool]:
    try:
        family, style = default_font.getname()
    except (AttributeError, OSError, ValueError):
        family, style = None, "Regular"
    if not isinstance(family, str) or not family.strip():
        family = None
    if not isinstance(style, str):
        style = "Regular"
    bold, italic = _style_flags(style)
    return (_family_key(family) if family else None), bold, italic


@lru_cache(maxsize=256)
def _load_face(path: str, index: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size, index=index)


def _resized_default(default_font: ImageFont.ImageFont, size: int) -> ImageFont.ImageFont:
    path = getattr(default_font, "path", None)
    index = getattr(default_font, "index", 0)
    if isinstance(path, (str, os.PathLike)):
        try:
            return _load_face(str(path), int(index), size)
        except (OSError, TypeError, ValueError):
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return default_font


def _preferred_builtin_family(name: str, families: dict[str, tuple[_Face, ...]]) -> str | None:
    for candidate in _BUILTIN_FAMILIES[name]:
        key = _family_key(candidate)
        if key in families:
            return key
    return None


def resolve_font(default_font: ImageFont.ImageFont, size: int, *, font_family: str | None = None,
                 bold: bool | None = None, italic: bool | None = None
                 ) -> tuple[ImageFont.ImageFont, bool, bool]:
    """Resolve a face and report synthetic bold/italic needed by the canvas.

    With no overrides this preserves the renderer's exact existing font
    object, so default typography incurs no enumeration or visual changes.
    """
    if font_family is None and bold is None and italic is None:
        if getattr(default_font, "size", None) == size:
            return default_font, False, False
        variant = getattr(default_font, "font_variant", None)
        if callable(variant):
            try:
                return variant(size=size), False, False
            except (OSError, TypeError, ValueError):
                pass
        return _resized_default(default_font, size), False, False
    if type(size) is not int or size < 1:
        raise ValueError("font size must be a positive integer")

    default_key, default_bold, default_italic = _default_identity(default_font)
    wanted_bold = bold if type(bold) is bool else default_bold
    wanted_italic = italic if type(italic) is bool else default_italic
    families, ids = _family_index()

    target_key = default_key
    if font_family == "ui":
        target_key = _preferred_builtin_family("ui", families) or default_key
    elif font_family == "mono":
        target_key = _preferred_builtin_family("mono", families) or default_key
    elif is_font_family_id(font_family):
        target_key = ids.get(font_family, default_key)

    candidates = families.get(target_key, ()) if target_key else ()
    if not candidates:
        fallback = _resized_default(default_font, size)
        fallback_bold, fallback_italic = default_bold, default_italic
        return fallback, bool(wanted_bold and not fallback_bold), bool(wanted_italic and not fallback_italic)

    ordered = sorted(candidates, key=lambda face: (
        (face.bold != wanted_bold) + (face.italic != wanted_italic),
        face.bold != wanted_bold,
        face.italic != wanted_italic,
        face.style.casefold(),
        str(face.path).casefold(),
        face.index,
    ))
    for face in ordered:
        try:
            resolved = _load_face(str(face.path), face.index, size)
        except (OSError, ValueError):
            continue
        return resolved, bool(wanted_bold and not face.bold), bool(wanted_italic and not face.italic)

    fallback = _resized_default(default_font, size)
    return fallback, bool(wanted_bold and not default_bold), bool(wanted_italic and not default_italic)
