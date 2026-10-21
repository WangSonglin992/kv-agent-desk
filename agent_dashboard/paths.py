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


__all__ = ["local_app_data"]
