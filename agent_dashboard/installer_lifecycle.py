"""Frozen Windows Setup lifecycle entry points.

These commands are intentionally separate from the ordinary native-launch and
raw-EXE installation paths.  They accept no custom state directory and only
operate on the canonical per-user store.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path, PureWindowsPath
import sys

from . import install_location, installation
from .paths import user_state_directory
from .settings import AppSettings, set_autostart


_EXECUTABLE_NAME = "KV-Agent-Desk.exe"


def _lifecycle_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="KV-Agent-Desk installer lifecycle",
        add_help=False,
        allow_abbrev=False,
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--installer-prepare", dest="mode", action="store_const", const="prepare")
    modes.add_argument("--installer-finish", dest="mode", action="store_const", const="finish")
    modes.add_argument("--installer-uninstall", dest="mode", action="store_const", const="uninstall")
    parser.add_argument("--install-dir", type=Path)
    parser.add_argument("--desktop-shortcut", choices=("0", "1"))
    parser.add_argument("--auto-start", choices=("0", "1"))
    return parser


def _parse_lifecycle_args(argv: list[str]) -> argparse.Namespace:
    args = _lifecycle_parser().parse_args(argv)
    if args.mode == "finish":
        if args.install_dir is None or args.desktop_shortcut is None or args.auto_start is None:
            raise SystemExit("--installer-finish requires --install-dir, --desktop-shortcut and --auto-start")
    elif args.install_dir is not None or args.desktop_shortcut is not None or args.auto_start is not None:
        raise SystemExit("installer options are only valid with --installer-finish")
    return args


def _require_frozen_windows() -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise ValueError("installer lifecycle commands require the frozen Windows application")


def _same_path(left: Path, right: Path) -> bool:
    try:
        left_value = os.path.normcase(str(Path(left).resolve()))
        right_value = os.path.normcase(str(Path(right).resolve()))
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    return left_value == right_value


def _canonical_state_directory() -> Path:
    state_dir = Path(user_state_directory())
    if not state_dir.is_absolute():
        raise ValueError("canonical data directory is not absolute")
    return state_dir


def _validate_install_directory(value: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute() or not PureWindowsPath(str(candidate)).is_absolute():
        raise ValueError("installation directory must be an absolute Windows path")
    return candidate.resolve()


def _current_executable(install_dir: Path | None = None) -> Path:
    executable = Path(sys.executable)
    if (not executable.is_absolute() or executable.name.casefold() != _EXECUTABLE_NAME.casefold()
            or executable.is_symlink() or not executable.is_file()):
        raise ValueError("current executable is not KV-Agent-Desk.exe")
    executable = executable.resolve()
    if install_dir is not None and not _same_path(executable, install_dir / _EXECUTABLE_NAME):
        raise ValueError("current executable does not match the selected installation directory")
    return executable


def _prepare_state_directory() -> Path:
    """Run the existing allowlisted legacy migration before backing up state."""
    from . import app

    args = app.parser().parse_args([])
    expected = _canonical_state_directory()
    if not _same_path(Path(args.state_dir), expected):
        raise ValueError("installer lifecycle requires the canonical data directory")
    app._prepare_state_directory(args)
    prepared = Path(args.state_dir)
    if not _same_path(prepared, expected):
        raise ValueError("legacy migration selected a non-canonical data directory")
    return prepared


def _prepare_installer() -> Path:
    _require_frozen_windows()
    state_dir = _prepare_state_directory()
    # A failed backup must never stop the still-running old application.
    backup = installation._backup_user_data(state_dir)
    installation._handoff_existing_server()
    return backup


def _read_previous_installation() -> install_location.Installation:
    # Invalid metadata must fail closed instead of being replaced by Setup.
    return install_location.read_installation()


def _owned_shortcut_targets(executable: Path,
                            previous: install_location.Installation | None) -> list[Path]:
    targets = [executable]
    if previous is not None and previous.registered:
        targets.append(previous.directory / _EXECUTABLE_NAME)
    targets.append(installation.legacy_installation_executable())
    result: list[Path] = []
    known: set[str] = set()
    for target in targets:
        candidate = Path(target)
        if not candidate.is_absolute() or candidate.suffix.casefold() != ".exe":
            continue
        try:
            resolved = candidate.resolve()
            identity = os.path.normcase(str(resolved))
        except (OSError, RuntimeError, ValueError):
            continue
        if identity not in known:
            known.add(identity)
            result.append(resolved)
    return result


def _finish_installer(install_dir: Path, desktop_shortcut: bool, auto_start: bool) -> None:
    _require_frozen_windows()
    if type(desktop_shortcut) is not bool or type(auto_start) is not bool:
        raise ValueError("installer selections must be booleans")
    target_dir = _validate_install_directory(install_dir)
    executable = _current_executable(target_dir)
    state_dir = _canonical_state_directory()
    previous = _read_previous_installation()

    settings = AppSettings(state_dir / "settings.json")
    previous_settings = settings.get()
    if settings.last_read_status not in {"ready", "missing"}:
        raise OSError("saved settings could not be synchronized")
    update_setting = previous_settings["auto_start"] is not auto_start

    install_location.register_installer_installation(
        target_dir, desktop_shortcut=desktop_shortcut, state_dir=state_dir
    )
    if desktop_shortcut:
        installation.ensure_desktop_shortcut(executable)
    else:
        installation.remove_owned_desktop_shortcut(
            _owned_shortcut_targets(executable, previous)
        )

    command = [str(executable), "--background", "--state-dir", str(state_dir.resolve())]
    set_autostart(auto_start, command)
    if update_setting:
        try:
            settings.update({"auto_start": auto_start})
        except Exception:
            # Match the existing settings-update behavior: restore the prior
            # Run-key choice if the settings file could not be saved.
            try:
                set_autostart(previous_settings["auto_start"], command)
            except Exception:
                logging.error("Startup registration rollback could not complete")
            raise

    from . import __version__

    install_location.sync_owned_uninstall_display_version(target_dir, __version__)


def _owned_uninstall_entry_matches(directory: Path) -> bool:
    try:
        winreg = install_location._registry()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            install_location.OWNED_UNINSTALL_SUBKEY,
            0,
            winreg.KEY_READ,
        ) as key:
            raw_directory, value_type = winreg.QueryValueEx(
                key, install_location.UNINSTALL_LOCATION_VALUE
            )
    except (FileNotFoundError, OSError, NotImplementedError):
        return False
    if value_type != winreg.REG_SZ or not isinstance(raw_directory, str):
        return False
    try:
        return _same_path(Path(raw_directory), directory)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _validate_owned_installed_executable() -> tuple[Path, install_location.Installation]:
    record = _read_previous_installation()
    if not record.registered or not record.installer_managed:
        raise ValueError("no owned Setup installation is registered")
    executable = _current_executable(record.directory)
    if not _owned_uninstall_entry_matches(record.directory):
        raise ValueError("owned Setup AppId does not match the current installation")
    return executable, record


def _uninstall_installer() -> None:
    _require_frozen_windows()
    executable, record = _validate_owned_installed_executable()
    installation._handoff_existing_server()
    set_autostart(False, [])
    installation.remove_owned_desktop_shortcut(
        _owned_shortcut_targets(executable, record)
    )
    if not install_location.clear_installer_installation(record.directory):
        raise ValueError("owned Setup installation registration could not be cleared")


def handle_installer_lifecycle(argv: list[str] | None = None) -> int | None:
    """Handle a frozen Setup command, or return ``None`` for an ordinary launch."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not any(isinstance(value, str) and value.startswith("--installer-") for value in arguments):
        return None
    args = _parse_lifecycle_args(arguments)
    try:
        if args.mode == "prepare":
            _prepare_installer()
        elif args.mode == "finish":
            _finish_installer(
                args.install_dir,
                args.desktop_shortcut == "1",
                args.auto_start == "1",
            )
        else:
            _uninstall_installer()
    except Exception as exc:
        logging.error("Installer lifecycle action failed: %s", type(exc).__name__)
        return 1
    return 0


__all__ = ["handle_installer_lifecycle"]
