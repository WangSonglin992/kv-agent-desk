"""Platform-aware lookup for the current user's local application-data directory."""

from __future__ import annotations

import ctypes
import os
import re
import uuid
from pathlib import Path


_LOCAL_APP_DATA_FOLDER_ID = "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"
_UNEXPANDED_VARIABLE = re.compile(r"%[^%]+%|\$\{[^}]+\}|\$[A-Za-z_][A-Za-z0-9_]*")


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def local_app_data(cls) -> "_GUID":
        return cls.from_buffer_copy(uuid.UUID(_LOCAL_APP_DATA_FOLDER_ID).bytes_le)


def _is_windows() -> bool:
    return os.name == "nt"


def _windows_known_folder_local_app_data() -> Path | None:
    """Read FOLDERID_LocalAppData through SHGetKnownFolderPath without creating it."""
    if not _is_windows():
        return None
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    get_known_folder = shell32.SHGetKnownFolderPath
    get_known_folder.argtypes = [
        ctypes.POINTER(_GUID), ctypes.c_uint32, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    get_known_folder.restype = ctypes.c_long
    free = ole32.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None

    value = ctypes.c_wchar_p()
    result = get_known_folder(
        ctypes.byref(_GUID.local_app_data()), 0, None, ctypes.byref(value)
    )
    try:
        if result != 0 or not value.value:
            return None
        path = Path(value.value)
        return path if path.is_absolute() else None
    finally:
        if value:
            free(ctypes.cast(value, ctypes.c_void_p))


def _valid_windows_environment_path(value: str | None) -> Path | None:
    if not value or _UNEXPANDED_VARIABLE.search(value):
        return None
    path = Path(value)
    if not path.is_absolute():
        return None
    return path


def local_app_data() -> Path:
    """Return the LocalAppData base path without creating or migrating directories."""
    if not _is_windows():
        # Preserve the existing source-build behavior used by this application.
        value = os.environ.get("LOCALAPPDATA")
        return Path(value) if value else Path.home()

    try:
        known_folder = _windows_known_folder_local_app_data()
    except Exception:
        known_folder = None
    if known_folder is not None and known_folder.is_absolute():
        return known_folder

    environment_path = _valid_windows_environment_path(os.environ.get("LOCALAPPDATA"))
    if environment_path is not None:
        return environment_path
    return Path.home() / "AppData" / "Local"


def user_state_directory() -> Path:
    """Return the canonical per-user state directory without creating it.

    Windows uses the user's home directory so packaged-app LocalAppData
    virtualization cannot split the dashboard's settings from its data.
    Other platforms preserve the existing LocalAppData-based location.
    """
    if _is_windows():
        home = Path.home()
        if not home.is_absolute():
            raise OSError("user home directory is not absolute")
        return home / ".kv-agent-desk"
    return local_app_data() / "KV-Agent-Desk"


def legacy_state_directories() -> list[Path]:
    """List current-user legacy state roots, without reading or creating files.

    The ordinary LocalAppData root is kept first even when absent. Package
    LocalCache candidates are included only when that exact dashboard folder
    currently exists under a current-user ``OpenAI.Codex_*`` package folder.
    """
    base = local_app_data()
    candidates = [base / "KV-Agent-Desk"]
    packages = base / "Packages"
    try:
        package_directories = sorted(
            (path for path in packages.glob("OpenAI.Codex_*") if path.is_dir()),
            key=lambda path: path.name.casefold(),
        )
    except OSError:
        package_directories = []

    for package in package_directories:
        candidate = package / "LocalCache" / "Local" / "KV-Agent-Desk"
        try:
            if candidate.is_dir():
                candidates.append(candidate)
        except OSError:
            # Keep the stable ordinary location; callers may probe candidates
            # again if package-local access becomes available.
            continue

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = os.path.normcase(os.path.normpath(str(path)))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


__all__ = ["legacy_state_directories", "local_app_data", "user_state_directory"]
