"""Small, token-free settings storage and Windows Run-key integration.

The JSON file contains only display and startup preferences.  A missing or
malformed file is treated as defaults; updates are validated before a
same-directory atomic replace so a failed write cannot truncate the prior
settings file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Any

try:  # pragma: no cover - availability depends on the host OS
    import winreg as _winreg
except ImportError:  # pragma: no cover - exercised by the non-Windows path
    _winreg = None


DEFAULT_SETTINGS: dict[str, Any] = {
    "brightness": 100,
    "rotation": 180,
    "usb_enabled": True,
    "auto_start": True,
    "auto_update": True,
    "lcd_height": 462,
    "codex_no_five_hour_limit": False,
    "ui_id": "classic",
}

_ALLOWED_FIELDS = frozenset(DEFAULT_SETTINGS)
_UI_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}\Z")
_RUN_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAME = "KVAgentDesk"


def _validate_setting(name: str, value: Any) -> Any:
    if name in {"brightness", "rotation", "lcd_height"}:
        if type(value) is not int:
            raise ValueError(f"{name} must be an integer")
        if name == "brightness" and not 0 <= value <= 100:
            raise ValueError("brightness must be between 0 and 100")
        if name == "rotation" and value not in (0, 180):
            raise ValueError("rotation must be 0 or 180")
        if name == "lcd_height" and value not in (462, 480):
            raise ValueError("lcd_height must be 462 or 480")
        return value
    if name in {"usb_enabled", "auto_start", "auto_update", "codex_no_five_hour_limit"}:
        if type(value) is not bool:
            raise ValueError(f"{name} must be a boolean")
        return value
    if name == "ui_id":
        if not isinstance(value, str) or not _UI_ID_PATTERN.fullmatch(value):
            raise ValueError("ui_id must be a lowercase profile slug")
        return value
    raise ValueError(f"Unknown setting: {name}")


def _settings_from_json(value: Any) -> dict[str, Any]:
    settings = DEFAULT_SETTINGS.copy()
    if not isinstance(value, dict):
        return settings
    for name in _ALLOWED_FIELDS:
        if name not in value:
            continue
        try:
            settings[name] = _validate_setting(name, value[name])
        except ValueError:
            # Older or partially corrupted files should remain readable.
            settings[name] = DEFAULT_SETTINGS[name]
    return settings


class AppSettings:
    """Validated JSON settings; ``path=None`` keeps values in memory only."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else None
        self._memory = DEFAULT_SETTINGS.copy()
        self._has_loaded = False
        self.last_read_status = "ready" if self.path is None else "unread"
        self._last_failure = None

    def get(self) -> dict[str, Any]:
        if self.path is None:
            return self._memory.copy()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self.last_read_status = ("missing" if isinstance(exc, FileNotFoundError) else
                                     "unavailable" if isinstance(exc, OSError) else "invalid")
            failure = (type(exc).__name__, getattr(exc, "errno", None))
            if failure != self._last_failure and not isinstance(exc, FileNotFoundError):
                logging.getLogger(__name__).warning("Saved settings unavailable (%s, errno=%s); retaining last values",
                                                    *failure)
            self._last_failure = failure
            return self._memory.copy()
        if not isinstance(value, dict):
            self.last_read_status = "invalid"
            return self._memory.copy()
        self._memory = _settings_from_json(value)
        self._has_loaded = True
        self.last_read_status = "ready"
        self._last_failure = None
        return self._memory.copy()

    def update(self, fields: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(fields, dict):
            raise ValueError("fields must be a dictionary")
        validated: dict[str, Any] = {}
        for name, value in fields.items():
            if name not in _ALLOWED_FIELDS:
                raise ValueError(f"Unknown setting: {name}")
            validated[name] = _validate_setting(name, value)

        current = self.get()
        if self.path is not None and self.last_read_status != "ready":
            if self.last_read_status != "missing" or self._has_loaded:
                raise OSError("Saved settings are not readable; retry before saving changes")
        current.update(validated)
        if self.path is None:
            self._memory = current
        else:
            self._atomic_write(current)
            self._memory = current.copy()
            self._has_loaded = True
            self.last_read_status = "ready"
        return current.copy()

    def _atomic_write(self, value: dict[str, Any]) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: str | None = None
        try:
            fd, temp_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass


def _is_windows() -> bool:
    return sys.platform == "win32"


def _registry_module():
    if not _is_windows() or _winreg is None:
        raise NotImplementedError("KV Agent Desk Run-key autostart is Windows-only")
    return _winreg


def _validated_autostart_command(command: list[str]) -> list[str]:
    if not isinstance(command, list) or len(command) not in (2, 4):
        raise ValueError("command must contain a fixed executable, background flag and optional state directory")
    exe, flag = command[:2]
    if not isinstance(exe, str) or any(c in exe for c in "\0\r\n"):
        raise ValueError("command executable path is invalid")
    win_path = PureWindowsPath(exe)
    if not win_path.is_absolute() or win_path.suffix.lower() != ".exe":
        raise ValueError("command must start with an absolute .exe path")
    if flag != "--background":
        raise ValueError("the only supported startup argument is --background")
    if len(command) == 4:
        option, state_dir = command[2:]
        if (option != "--state-dir" or not isinstance(state_dir, str)
                or any(c in state_dir for c in "\0\r\n") or not PureWindowsPath(state_dir).is_absolute()):
            raise ValueError("startup state directory must be an absolute local path")
    return command.copy()


def set_autostart(enabled: bool, command: list[str]) -> None:
    """Set the current user's KVAgentDesk Run value.

    For safety, enabling accepts only the frozen executable path and the
    fixed ``--background`` flag and optional absolute ``--state-dir``; requests
    cannot provide arbitrary command text. Registry access is isolated for tests.
    """
    if type(enabled) is not bool:
        raise ValueError("enabled must be a boolean")
    winreg = _registry_module()
    if enabled:
        checked_command = _validated_autostart_command(command)
        command_line = subprocess.list2cmdline(checked_command)
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, _RUN_SUBKEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _RUN_VALUE_NAME, 0, winreg.REG_SZ, command_line)
        return

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_SUBKEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            try:
                winreg.DeleteValue(key, _RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def get_autostart() -> bool:
    """Return whether the KVAgentDesk Run value is present and usable."""
    if not _is_windows() or _winreg is None:
        return False
    winreg = _winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_SUBKEY, 0, winreg.KEY_QUERY_VALUE
        ) as key:
            value, value_type = winreg.QueryValueEx(key, _RUN_VALUE_NAME)
    except FileNotFoundError:
        return False
    return value_type == winreg.REG_SZ and isinstance(value, str) and bool(value.strip())
