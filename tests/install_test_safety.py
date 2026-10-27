"""Block installer tests from touching this Windows account's real state."""

from __future__ import annotations

from contextlib import ExitStack
import builtins
import io
import ipaddress
import ntpath
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch
import winreg


def _known_folder(folder_id: str) -> str:
    import ctypes
    import uuid

    class Guid(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    pointer = ctypes.c_wchar_p()
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    query = shell.SHGetKnownFolderPath
    query.argtypes = [ctypes.POINTER(Guid), ctypes.c_uint32, ctypes.c_void_p,
                      ctypes.POINTER(ctypes.c_wchar_p)]
    query.restype = ctypes.c_long
    free = ole.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None
    guid = Guid.from_buffer_copy(uuid.UUID(folder_id).bytes_le)
    status = query(ctypes.byref(guid), 0, None, ctypes.byref(pointer))
    if status != 0 or not pointer.value:
        raise OSError("Could not set up the installer-test filesystem guard")
    try:
        return pointer.value
    finally:
        free(ctypes.cast(pointer, ctypes.c_void_p))


# Derive only known-folder paths, not environment values or file contents.
_REAL_PROFILE = ntpath.normcase(ntpath.normpath(_known_folder("5E6C858F-0E22-4760-9AFE-EA3317B67173")))
_REAL_LOCAL_APP_DATA = ntpath.normcase(ntpath.normpath(_known_folder("F1B32785-6FBA-4FCF-9D55-7B8E7F157091")))
_SANDBOX_ROOTS: set[str] = set()


def _normalize(path: object) -> str | None:
    if not isinstance(path, (str, bytes, os.PathLike)):
        return None
    try:
        value = os.fsdecode(os.fspath(path))
    except (TypeError, ValueError):
        return None
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normcase(ntpath.abspath(value)).rstrip("\\/")


def _short_windows_path(path: Path) -> str | None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_short_path = kernel.GetShortPathNameW
    get_short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_short_path.restype = wintypes.DWORD
    required = get_short_path(str(path), None, 0)
    if required == 0 or required > 32768:
        return None
    buffer = ctypes.create_unicode_buffer(required)
    written = get_short_path(str(path), buffer, required)
    if written == 0 or written >= required:
        return None
    return buffer.value


short_windows_path = _short_windows_path


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "\\")


def _is_private_path(path: object) -> bool:
    candidate = _normalize(path)
    if candidate is None:
        return False
    if any(_inside(candidate, root) for root in _SANDBOX_ROOTS):
        return False
    sensitive_roots = (
        ntpath.join(_REAL_PROFILE, ".kv-agent-desk"),
        ntpath.join(_REAL_PROFILE, "KV-Agent-Desk"),
        ntpath.join(_REAL_PROFILE, "Programs", "KV-Agent-Desk"),
        ntpath.join(_REAL_PROFILE, "AppData", "Local", "Programs", "KV-Agent-Desk"),
        ntpath.join(_REAL_PROFILE, "AppData", "Local", "KV-Agent-Desk"),
        ntpath.join(_REAL_PROFILE, "AppData", "Roaming", "KV-Agent-Desk"),
        ntpath.join(_REAL_PROFILE, ".codex"),
        ntpath.join(_REAL_PROFILE, ".zcode"),
        ntpath.join(_REAL_LOCAL_APP_DATA, "KV-Agent-Desk"),
        ntpath.join(_REAL_LOCAL_APP_DATA, "Programs", "KV-Agent-Desk"),
        ntpath.join(_REAL_LOCAL_APP_DATA, "Packages"),
        ntpath.normcase(ntpath.normpath(r"D:\APP\KV-Agent-Desk")),
    )
    if any(_inside(candidate, ntpath.normcase(ntpath.normpath(root))) for root in sensitive_roots):
        return True
    try:
        under_profile = ntpath.commonpath((candidate, _REAL_PROFILE)) == _REAL_PROFILE
    except ValueError:
        under_profile = False
    if under_profile:
        parts = ntpath.normcase(ntpath.normpath(candidate)).split("\\")
        for index in range(max(0, len(parts) - 4)):
            if (parts[index] == "packages" and parts[index + 1].startswith("openai.codex_")
                    and parts[index + 2:index + 5] == ["localcache", "local", "kv-agent-desk"]):
                return True
    return False


def allow_synthetic_root(test_case, path: Path) -> None:
    """Allow exactly one existing TemporaryDirectory below the active TEMP root."""
    candidate = Path(path)
    temp_root = Path(tempfile.gettempdir()).resolve()
    original_absolute = candidate.absolute()
    original_alias = _normalize(original_absolute)
    resolved = candidate.resolve(strict=True)
    if (candidate.is_symlink() or not candidate.is_dir() or resolved == temp_root
            or resolved.parent != temp_root or _is_private_path(resolved)):
        raise ValueError("synthetic state root must be a direct TemporaryDirectory")
    normalized = _normalize(resolved)
    if normalized is None:
        raise ValueError("synthetic state root must be absolute")
    aliases = {normalized}
    if original_alias is not None:
        resolved_original = _normalize(original_absolute.resolve(strict=True))
        if resolved_original != normalized:
            raise ValueError("temporary mixed short/long path changed directory identity")
        aliases.add(original_alias)
    short_path = _short_windows_path(resolved)
    if short_path:
        if _normalize(Path(short_path).resolve(strict=True)) != normalized:
            raise ValueError("temporary 8.3 path did not resolve to the same synthetic root")
        short_normalized = _normalize(Path(short_path))
        if short_normalized is not None:
            aliases.add(short_normalized)
    _SANDBOX_ROOTS.update(aliases)
    test_case.addCleanup(_SANDBOX_ROOTS.difference_update, aliases)


def register_bootstrap_sandbox(path: Path) -> Path:
    """Register only a fresh runtime/installer-test-sandboxes/<uuid> root."""
    runtime_root = Path(__file__).resolve().parents[1] / "runtime" / "installer-test-sandboxes"
    root = Path(path).resolve(strict=True)
    if (root.parent != runtime_root.resolve(strict=True) or len(root.name) != 32
            or any(char not in "0123456789abcdef" for char in root.name.casefold())
            or _is_private_path(root)):
        raise ValueError("installer runner root is not an approved synthetic sandbox")
    normalized = _normalize(root)
    if normalized is None:
        raise ValueError("installer runner root must be absolute")
    _SANDBOX_ROOTS.add(normalized)
    return root


def _check_path(path: object, *, writes_only: bool = False) -> None:
    if _is_private_path(path):
        raise AssertionError("installer test attempted access to real user state")
    if writes_only:
        candidate = _normalize(path)
        if candidate is not None and not any(_inside(candidate, root) for root in _SANDBOX_ROOTS):
            raise AssertionError("installer test attempted a write outside its synthetic sandbox")


def audit(event: str, args: tuple[object, ...]) -> None:
    if event == "open" and args:
        path = args[0]
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        writes = isinstance(mode, str) and any(char in mode for char in "wax+")
        writes = writes or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        _check_path(path, writes_only=writes)
    elif event.startswith("os.") and args:
        if event in {"os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.replace", "os.link",
                     "os.symlink", "os.chmod", "os.chown", "os.truncate", "os.utime", "os.unlink"}:
            _check_path(args[0], writes_only=True)
            if event in {"os.rename", "os.replace", "os.link"} and len(args) > 1:
                _check_path(args[1], writes_only=True)
    elif event in {"subprocess.Popen", "os.system", "os.startfile"} or event.startswith(("os.spawn", "os.exec")):
        raise AssertionError("installer tests must mock all process launches")
    elif event == "socket.getaddrinfo" and args:
        host = args[0]
        port = args[1] if len(args) > 1 else None
        is_loopback_host = host in {None, "localhost", "127.0.0.1", "::1"}
        if not is_loopback_host or (is_loopback_host and port == 8765):
            raise AssertionError("installer tests cannot resolve external hosts")
    elif event == "socket.connect" and len(args) > 1:
        address = args[1]
        host = address[0] if isinstance(address, tuple) and address else address
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except (TypeError, ValueError):
            loopback = host == "localhost"
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        if not loopback or port == 8765:
            raise AssertionError("installer tests cannot connect to external hosts")
    elif event in {"winreg.CreateKey", "winreg.CreateKeyEx", "winreg.SetValue",
                   "winreg.DeleteKey", "winreg.DeleteValue", "winreg.LoadKey", "winreg.SaveKey"}:
        raise AssertionError("installer tests cannot write to the Windows registry")


def isolate_real_user_state(test_case) -> None:
    """Guard real profile/AppData/App locations at the pathlib/os boundaries."""
    stack = ExitStack()
    test_case.addCleanup(stack.close)

    for module, name in ((builtins, "open"), (io, "open")):
        original = getattr(module, name)

        def guarded_open(file, *args, __original=original, **kwargs):
            _check_path(file)
            return __original(file, *args, **kwargs)

        stack.enter_context(patch.object(module, name, guarded_open))

    for name in ("open", "read_bytes", "read_text", "write_bytes", "write_text", "stat", "lstat",
                 "exists", "is_file", "is_dir", "is_symlink", "resolve", "absolute", "mkdir", "rmdir",
                 "unlink", "glob", "rglob", "iterdir", "rename", "replace", "chmod", "touch"):
        original = getattr(Path, name, None)
        if original is None:
            continue

        def guarded_path(path, *args, __original=original, __name=name, **kwargs):
            _check_path(path, writes_only=__name in {"write_bytes", "write_text", "mkdir", "rmdir",
                                                       "unlink", "rename", "replace", "chmod", "touch"})
            return __original(path, *args, **kwargs)

        stack.enter_context(patch.object(Path, name, guarded_path))

    for name in ("open", "stat", "lstat", "scandir", "listdir", "mkdir", "makedirs", "rmdir",
                 "unlink", "remove", "rename", "replace", "link", "symlink", "chmod", "chown", "utime"):
        original = getattr(os, name, None)
        if original is None:
            continue

        def guarded_os(path, *args, __original=original, __name=name, **kwargs):
            _check_path(path, writes_only=__name in {"mkdir", "makedirs", "rmdir", "unlink", "remove",
                                                       "rename", "replace", "link", "symlink", "chmod", "chown", "utime"})
            if __name in {"rename", "replace", "link"} and args:
                _check_path(args[0], writes_only=True)
            return __original(path, *args, **kwargs)

        stack.enter_context(patch.object(os, name, guarded_os))

    # Prove the path matcher rejects real-state targets without touching them.
    try:
        _check_path(Path(_REAL_PROFILE) / ".kv-agent-desk" / "settings.json")
    except AssertionError as exc:
        if "installer test attempted access" not in str(exc):
            raise
    else:
        raise AssertionError("real user-state sentinel did not activate")
