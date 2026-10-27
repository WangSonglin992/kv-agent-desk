"""Safe per-user installation for the frozen Windows dashboard executable."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from .install_location import (
    install_dir_for_runtime,
    register_legacy_installation,
    sync_owned_uninstall_display_version,
)
from .paths import local_app_data, user_state_directory


_EXECUTABLE_NAME = "KV-Agent-Desk.exe"
_SHORTCUT_NAME = "KV Agent Desk.lnk"
_SHORTCUT_TARGET_ENV = "KV_AGENT_DESK_TARGET"
_SHORTCUT_STATE_ENV = "KV_AGENT_DESK_STATE"
_SHORTCUT_OWNED_TARGETS_ENV = "KV_AGENT_DESK_OWNED_TARGETS"
_SERVER_URL = "http://127.0.0.1:8765"
_SERVER_STOP_TIMEOUT_SECONDS = 15.0
_REPLACE_TIMEOUT_SECONDS = 30.0
_REPLACE_RETRY_SECONDS = 0.2
_BACKUP_FILE_LIMITS = {
    "settings.json": 1024 * 1024,
    "subscriptions.json": 1024 * 1024,
    "last-good.json": 16 * 1024 * 1024,
    "accounts-last-good.json": 4 * 1024 * 1024,
    "last-frame.png": 32 * 1024 * 1024,
    "last-frame-meta.json": 256 * 1024,
    "updates.json": 1024 * 1024,
}
_SHORTCUT_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$target = $env:KV_AGENT_DESK_TARGET
$dataDirectory = $env:KV_AGENT_DESK_STATE
if ([string]::IsNullOrWhiteSpace($target) -or !(Test-Path -LiteralPath $target -PathType Leaf)) {
    exit 2
}
if ([string]::IsNullOrWhiteSpace($dataDirectory) -or ![IO.Path]::IsPathRooted($dataDirectory)) {
    exit 4
}
$desktop = [Environment]::GetFolderPath('Desktop')
if ([string]::IsNullOrWhiteSpace($desktop)) {
    exit 3
}
$linkPath = Join-Path $desktop 'KV Agent Desk.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = [IO.Path]::GetDirectoryName($target)
$shortcut.Arguments = '--state-dir "' + $dataDirectory + '"'
$shortcut.IconLocation = "$target,0"
$shortcut.Save()
[Console]::Out.WriteLine($linkPath)
'''


def _is_windows() -> bool:
    return sys.platform == "win32"


def installed_executable() -> Path:
    """Return the registered program path or its first-install default."""
    return installation_directory() / _EXECUTABLE_NAME


def installation_directory() -> Path:
    return install_dir_for_runtime()


def _sync_display_version_for_canonical_install(target: Path, state_dir: Path) -> None:
    try:
        is_canonical_data = Path(state_dir).resolve() == user_state_directory().resolve()
    except OSError:
        is_canonical_data = False
    if is_canonical_data:
        sync_owned_uninstall_display_version(target.parent, _package_version())


def is_installed(exe: Path) -> bool:
    """Return whether ``exe`` is the fixed installed executable and exists."""
    try:
        candidate = Path(exe)
        target = installed_executable()
        return candidate.is_file() and target.is_file() and candidate.resolve() == target.resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def ensure_desktop_shortcut(exe: Path) -> Path:
    """Create or repair the current user's Desktop shortcut with PowerShell."""
    if not _is_windows():
        raise NotImplementedError("KV Agent Desk desktop shortcuts are Windows-only")
    target = Path(exe)
    if not target.is_absolute() or not target.is_file() or target.suffix.lower() != ".exe":
        raise ValueError("shortcut target must be an existing absolute .exe path")
    target = target.resolve()
    system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    child_env = {
        "SystemRoot": str(system_root),
        _SHORTCUT_TARGET_ENV: str(target),
        _SHORTCUT_STATE_ENV: str(user_state_directory().resolve()),
    }
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", _SHORTCUT_SCRIPT],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
        env=child_env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise OSError("Could not create the KV Agent Desk desktop shortcut")
    link_path = Path(result.stdout.strip())
    if not link_path.is_absolute() or link_path.name != _SHORTCUT_NAME:
        raise OSError("PowerShell did not return a valid desktop shortcut path")
    return link_path


def remove_owned_desktop_shortcut(targets: list[Path]) -> bool:
    """Remove this product's shortcut only when it points to a known app EXE."""
    if not _is_windows():
        raise NotImplementedError("KV Agent Desk desktop shortcuts are Windows-only")
    resolved: list[str] = []
    for value in targets:
        path = Path(value)
        if not path.is_absolute() or path.suffix.lower() != ".exe":
            raise ValueError("shortcut removal target must be an absolute .exe path")
        normalized = str(path.resolve())
        if normalized not in resolved:
            resolved.append(normalized)
    system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    script = r'''
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('Desktop')
if ([string]::IsNullOrWhiteSpace($desktop)) { exit 3 }
$linkPath = Join-Path $desktop 'KV Agent Desk.lnk'
if (!(Test-Path -LiteralPath $linkPath -PathType Leaf)) { exit 0 }
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($linkPath)
$expected = @($env:KV_AGENT_DESK_OWNED_TARGETS -split [Environment]::NewLine)
$actual = [IO.Path]::GetFullPath($link.TargetPath)
foreach ($candidate in $expected) {
    if (![string]::IsNullOrWhiteSpace($candidate) -and
        [string]::Equals($actual, [IO.Path]::GetFullPath($candidate), [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $linkPath -Force
        exit 0
    }
}
exit 0
'''
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        check=False,
        env={"SystemRoot": str(system_root), _SHORTCUT_OWNED_TARGETS_ENV: os.linesep.join(resolved)},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise OSError("Could not remove the owned KV Agent Desk desktop shortcut")
    return True


def legacy_installation_executable() -> Path:
    """Return the previous per-user EXE path for stale-shortcut cleanup."""
    return local_app_data() / "Programs" / "KV-Agent-Desk" / _EXECUTABLE_NAME


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file_or_hash(source: Path, target: Path) -> bool:
    if not target.is_file():
        return False
    try:
        if os.path.samefile(source, target):
            return True
    except OSError:
        pass
    return _sha256(source) == _sha256(target)


def _backup_user_data(state_dir: Path) -> Path:
    if state_dir.is_symlink():
        raise ValueError("state directory must not be a symlink")
    backup_root = state_dir / "backups"
    if backup_root.is_symlink():
        raise ValueError("backup directory must not be a symlink")
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = Path(tempfile.mkdtemp(prefix=f"install-{stamp}-", dir=backup_root))
    for filename, max_bytes in _BACKUP_FILE_LIMITS.items():
        source = state_dir / filename
        if source.is_file() and not source.is_symlink():
            if source.stat().st_size > max_bytes:
                raise OSError("Saved data backup exceeded a size limit")
            with source.open("rb") as original, (destination / filename).open("xb") as backup:
                shutil.copyfileobj(original, backup, length=256 * 1024)
                backup.flush()
                os.fsync(backup.fileno())
    profiles = state_dir / "ui-profiles"
    if profiles.is_dir() and not profiles.is_symlink():
        destination_profiles = destination / "ui-profiles"
        for source in profiles.glob("*.json"):
            if source.is_file() and not source.is_symlink():
                destination_profiles.mkdir(parents=True, exist_ok=True)
                (destination_profiles / source.name).write_bytes(source.read_bytes())
    return destination


def _stage_executable(source: Path, destination_dir: Path) -> Path:
    fd, temporary_name = tempfile.mkstemp(prefix=".KV-Agent-Desk.", suffix=".tmp", dir=destination_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as target, source.open("rb") as original:
            shutil.copyfileobj(original, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        if _sha256(temporary) != _sha256(source):
            raise OSError("Staged executable verification failed")
        return temporary
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _replace_with_retry(temporary: Path, target: Path) -> None:
    deadline = time.monotonic() + _REPLACE_TIMEOUT_SECONDS
    while True:
        try:
            os.replace(temporary, target)
            return
        except PermissionError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Installed executable is still in use") from None
            time.sleep(min(_REPLACE_RETRY_SECONDS, remaining))


def _request_existing_server(*, stop: bool = False, show: bool = False) -> bool:
    # Lazy import avoids a cycle when app.main imports this installation module.
    from .app import existing_server

    return existing_server(_SERVER_URL, stop=stop, show=show)


def _read_server_state() -> dict | None:
    try:
        with urllib.request.urlopen(_SERVER_URL + "/api/state", timeout=1) as response:
            state = json.load(response)
        return state if isinstance(state, dict) else None
    except (OSError, ValueError):
        return None


def _server_app_id() -> str:
    from .app import APP_ID

    return APP_ID


def _server_is_current_installed_version(state: dict | None) -> bool:
    if not isinstance(state, dict) or state.get("app") != _server_app_id():
        return False
    updates = state.get("updates")
    if not isinstance(updates, dict) or updates.get("supported") is not True:
        return False
    from . import __version__

    return state.get("version") == __version__


def _wait_for_server_exit() -> bool:
    deadline = time.monotonic() + _SERVER_STOP_TIMEOUT_SECONDS
    app_id = _server_app_id()
    while True:
        state = _read_server_state()
        if not isinstance(state, dict) or state.get("app") != app_id:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.25, remaining))


def _handoff_existing_server() -> bool:
    """Stop only our marked dashboard and wait briefly for its listener to exit."""
    state = _read_server_state()
    if not isinstance(state, dict) or state.get("app") != _server_app_id():
        return False
    if not _request_existing_server(stop=True):
        latest = _read_server_state()
        if isinstance(latest, dict) and latest.get("app") == _server_app_id():
            raise OSError("Could not gracefully stop the running KV Agent Desk")
    if not _wait_for_server_exit():
        raise TimeoutError("The running KV Agent Desk did not stop in time")
    return True


def _launch(executable: Path, background: bool, *, state_dir: Path | None = None) -> None:
    command = [str(executable)]
    data_directory = user_state_directory() if state_dir is None else Path(state_dir)
    command.extend(("--state-dir", str(data_directory.resolve())))
    if background:
        command.append("--background")
    subprocess.Popen(
        command,
        cwd=str(executable.parent),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def install_current(source: Path, *, state_dir: Path, background: bool = False) -> Path:
    """Install/update one frozen executable without modifying user state.

    A same-version executable only repairs its shortcut and asks an already
    running dashboard to show itself. Updating stages and verifies the new
    executable before gracefully stopping this app's fixed localhost server.
    """
    if not _is_windows():
        raise NotImplementedError("KV Agent Desk installation is Windows-only")
    if type(background) is not bool:
        raise ValueError("background must be a boolean")
    source = Path(source)
    if not source.is_file() or source.suffix.lower() != ".exe":
        raise ValueError("source must be an existing executable file")
    source = source.resolve()
    target = installed_executable()
    if target.is_symlink():
        raise ValueError("installed executable must not be a symlink")
    if _same_file_or_hash(source, target):
        state_dir = Path(state_dir)
        record = register_legacy_installation(target.parent, state_dir=state_dir)
        _sync_display_version_for_canonical_install(target, state_dir)
        if record.desktop_shortcut:
            ensure_desktop_shortcut(target)
        if _server_is_current_installed_version(_read_server_state()):
            if background or _request_existing_server(show=True):
                return target
            if _server_is_current_installed_version(_read_server_state()):
                return target
        _handoff_existing_server()
        _launch(target, background=background, state_dir=state_dir)
        return target

    state_dir = Path(state_dir)
    _backup_user_data(state_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise ValueError("installation directory must not be a symlink")
    staged = _stage_executable(source, target.parent)
    old_target_exists = target.is_file()
    server_stopped = _handoff_existing_server() if old_target_exists else False
    try:
        _replace_with_retry(staged, target)
        staged = None
    except Exception:
        if old_target_exists and server_stopped and target.is_file():
            try:
                _launch(target, background=True, state_dir=state_dir)
            except OSError:
                pass
        raise
    finally:
        if staged is not None:
            try:
                staged.unlink()
            except FileNotFoundError:
                pass

    if not server_stopped:
        _handoff_existing_server()
    record = register_legacy_installation(target.parent, state_dir=state_dir)
    _sync_display_version_for_canonical_install(target, state_dir)
    if record.desktop_shortcut:
        ensure_desktop_shortcut(target)
    _launch(target, background=background, state_dir=state_dir)
    return target


def _package_version() -> str:
    from . import __version__

    return __version__
