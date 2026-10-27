"""Current-user installation identity for the Windows installer and updater."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import logging
import os
from pathlib import Path, PureWindowsPath
import sys
import tempfile

from .paths import user_state_directory

try:  # pragma: no cover - availability depends on host OS
    import winreg as _winreg
except ImportError:  # pragma: no cover
    _winreg = None


REGISTRY_SUBKEY = r"Software\KV-Agent-Desk"
INSTALL_DIR_VALUE = "InstallDir"
DESKTOP_SHORTCUT_VALUE = "DesktopShortcut"
INSTALLER_MANAGED_VALUE = "InstallerManaged"

INSTALLER_APP_ID = "{8D8918EF-160F-4D1E-AE68-E168DB0AD2B9}"
OWNED_UNINSTALL_SUBKEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{INSTALLER_APP_ID}_is1"
UNINSTALL_DISPLAY_VERSION_VALUE = "DisplayVersion"
UNINSTALL_LOCATION_VALUE = "InstallLocation"

_INSTALLATION_SECTION = "Installation"
_INSTALLATION_INI_NAME = "installation.ini"


@dataclass(frozen=True)
class Installation:
    directory: Path
    desktop_shortcut: bool = True
    installer_managed: bool = False
    registered: bool = False


def _is_windows() -> bool:
    return sys.platform == "win32"


def _registry():
    if not _is_windows() or _winreg is None:
        raise NotImplementedError("Windows current-user installation registry is unavailable")
    return _winreg


def _validate_install_dir(value: object) -> Path:
    if not isinstance(value, str) or any(char in value for char in "\0\r\n"):
        raise ValueError("installation directory is invalid")
    candidate = Path(value)
    windows_path = PureWindowsPath(value)
    if (chr(34) in value or not windows_path.is_absolute()
            or not len(windows_path.drive) == 2 or windows_path.drive[1] != ":"):
        raise ValueError("installation directory must be a local absolute path")
    if candidate == Path(candidate.anchor) or not candidate.name:
        raise ValueError("installation directory must not be a volume root")
    return candidate


def default_install_dir() -> Path:
    """Return the first-install folder selected by the product contract."""
    if _is_windows():
        return Path("D:/APP/KV-Agent-Desk")
    return Path.home() / "APP" / "KV-Agent-Desk"


def installation_ini_path(state_dir: Path | None = None) -> Path:
    root = user_state_directory() if state_dir is None else Path(state_dir)
    return root / _INSTALLATION_INI_NAME


def _parse_ini(path: Path) -> Installation | None:
    if path.is_symlink():
        raise ValueError("installation record is invalid")
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise OSError("installation record could not be read") from None
    # Windows profile-INI APIs require UTF-16LE; rejecting UTF-16BE avoids
    # runtime/Setup disagreement about the authoritative install path.
    if not content.startswith(b"\xff\xfe"):
        raise ValueError("installation record is invalid")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(content.decode("utf-16"))
        if parser.sections() != [_INSTALLATION_SECTION]:
            raise ValueError
        section = parser[_INSTALLATION_SECTION]
        if set(section) != {INSTALL_DIR_VALUE, DESKTOP_SHORTCUT_VALUE, INSTALLER_MANAGED_VALUE}:
            raise ValueError
        directory = _validate_install_dir(section[INSTALL_DIR_VALUE])
        desktop_value = section[DESKTOP_SHORTCUT_VALUE]
        managed_value = section[INSTALLER_MANAGED_VALUE]
        if desktop_value not in {"0", "1"} or managed_value not in {"0", "1"}:
            raise ValueError
    except (UnicodeError, configparser.Error, ValueError):
        raise ValueError("installation record is invalid") from None
    return Installation(directory, desktop_value == "1", managed_value == "1", True)


def _query_registry_installation() -> Installation:
    default = Installation(default_install_dir())
    winreg = _registry()
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_SUBKEY, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return default
    with key:
        try:
            raw_dir, dir_type = winreg.QueryValueEx(key, INSTALL_DIR_VALUE)
        except FileNotFoundError:
            raw_dir, dir_type = None, None
        if raw_dir is not None and dir_type != winreg.REG_SZ:
            raise ValueError("registered installation directory has an invalid type")
        directory = default.directory if raw_dir is None else _validate_install_dir(raw_dir)

        try:
            raw_desktop, desktop_type = winreg.QueryValueEx(key, DESKTOP_SHORTCUT_VALUE)
        except FileNotFoundError:
            raw_desktop, desktop_type = None, None
        try:
            raw_managed, managed_type = winreg.QueryValueEx(key, INSTALLER_MANAGED_VALUE)
        except FileNotFoundError:
            raw_managed, managed_type = None, None

    if raw_managed is not None and (
        managed_type != winreg.REG_DWORD or type(raw_managed) is not int or raw_managed not in (0, 1)
    ):
        raise ValueError("registered installer state has an invalid type")
    if raw_desktop is not None and (
        desktop_type != winreg.REG_DWORD or type(raw_desktop) is not int or raw_desktop not in (0, 1)
    ):
        raise ValueError("registered desktop preference has an invalid type")
    installer_managed = raw_managed == 1
    # A damaged managed entry fails closed so an explicit icon opt-out is
    # never reversed by the normal launch path.
    desktop_shortcut = raw_desktop == 1
    if raw_desktop is None and not installer_managed:
        desktop_shortcut = True
    registered = raw_dir is not None or raw_desktop is not None or raw_managed is not None
    return Installation(directory, desktop_shortcut, installer_managed, registered)


def read_installation() -> Installation:
    """Read canonical UTF-16 metadata first and use HKCU as compatibility fallback."""
    ini = _parse_ini(installation_ini_path())
    if ini is not None:
        return ini
    return _query_registry_installation()


def install_dir_for_runtime() -> Path:
    return read_installation().directory


def desktop_shortcut_enabled() -> bool:
    return read_installation().desktop_shortcut


def _encode_ini(directory: Path, desktop_shortcut: bool, installer_managed: bool) -> bytes:
    checked = _validate_install_dir(str(directory))
    if type(desktop_shortcut) is not bool or type(installer_managed) is not bool:
        raise ValueError("installer preferences must be booleans")
    body = (
        f"[{_INSTALLATION_SECTION}]\r\n"
        f"{INSTALL_DIR_VALUE}={checked}\r\n"
        f"{DESKTOP_SHORTCUT_VALUE}={int(desktop_shortcut)}\r\n"
        f"{INSTALLER_MANAGED_VALUE}={int(installer_managed)}\r\n"
    )
    return b"\xff\xfe" + body.encode("utf-16-le")


def _write_ini(directory: Path, desktop_shortcut: bool, installer_managed: bool,
               state_dir: Path | None = None) -> bytes | None:
    """Atomically publish the canonical installation record; return its prior bytes."""
    path = installation_ini_path(state_dir)
    parent = path.parent
    if parent.is_symlink():
        raise OSError("installation record directory is invalid")
    parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError("installation record target is invalid")
    try:
        previous = path.read_bytes() if path.exists() else None
    except OSError:
        raise OSError("installation record could not be read") from None
    fd, temporary_name = tempfile.mkstemp(prefix=".installation.", suffix=".tmp", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_encode_ini(directory, desktop_shortcut, installer_managed))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise OSError("installation record could not be saved") from None
    return previous


def _read_current_values(winreg) -> dict[str, object]:
    result: dict[str, object] = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_SUBKEY, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return result
    with key:
        for name in (INSTALL_DIR_VALUE, DESKTOP_SHORTCUT_VALUE, INSTALLER_MANAGED_VALUE):
            try:
                result[name] = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                pass
    return result


def _restore_current_values(winreg, values: dict[str, object]) -> None:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_SUBKEY, 0, winreg.KEY_SET_VALUE)
    except FileNotFoundError:
        if not values:
            return
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REGISTRY_SUBKEY, 0, winreg.KEY_SET_VALUE)
    with key:
        for name in (INSTALL_DIR_VALUE, DESKTOP_SHORTCUT_VALUE, INSTALLER_MANAGED_VALUE):
            if name in values:
                value, value_type = values[name]  # type: ignore[misc]
                winreg.SetValueEx(key, name, 0, value_type, value)
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass


def _write_registry_mirror(directory: Path, desktop_shortcut: bool, installer_managed: bool) -> None:
    winreg = _registry()
    previous = _read_current_values(winreg)
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, REGISTRY_SUBKEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, INSTALL_DIR_VALUE, 0, winreg.REG_SZ, str(directory))
            winreg.SetValueEx(key, DESKTOP_SHORTCUT_VALUE, 0, winreg.REG_DWORD, int(desktop_shortcut))
            winreg.SetValueEx(key, INSTALLER_MANAGED_VALUE, 0, winreg.REG_DWORD, int(installer_managed))
    except OSError:
        try:
            _restore_current_values(winreg, previous)
        except OSError:
            pass
        raise OSError("registry installation record could not be saved") from None


def _register_installation(directory: Path, desktop_shortcut: bool, installer_managed: bool,
                           state_dir: Path | None = None, *, mirror_registry: bool = True) -> None:
    target = _validate_install_dir(str(directory))
    previous_ini = _write_ini(target, desktop_shortcut, installer_managed, state_dir)
    # The canonical INI is the identity source of truth. HKCU is a mirrored
    # compatibility value and may be unavailable in packaged launch hosts.
    try:
        if mirror_registry and _is_windows() and _winreg is not None:
            _write_registry_mirror(target, desktop_shortcut, installer_managed)
    except (OSError, NotImplementedError):
        logging.warning("Registry installation mirror could not be updated")
    except Exception:
        path = installation_ini_path(state_dir)
        try:
            if previous_ini is None:
                path.unlink(missing_ok=True)
            else:
                _write_ini_bytes(path, previous_ini)
        except OSError:
            pass
        raise


def _write_ini_bytes(path: Path, content: bytes) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=".installation.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise OSError("installation record could not be restored") from None


def register_installer_installation(directory: Path, *, desktop_shortcut: bool,
                                    state_dir: Path | None = None) -> None:
    """Persist the Setup wizard's selected program folder and desktop choice."""
    _register_installation(Path(directory), desktop_shortcut, True, state_dir)


def register_legacy_installation(directory: Path, *, state_dir: Path | None = None) -> Installation:
    """Record a raw-EXE compatibility install without changing owned Setup options."""
    canonical = state_dir is None
    if state_dir is not None:
        try:
            canonical = Path(state_dir).resolve() == user_state_directory().resolve()
        except OSError:
            canonical = False
    if canonical:
        # A first launch may have only the registry mirror (for example when a
        # host previously virtualized AppData). Preserve its installer choice
        # and promote it to the canonical INI before subsequent updates.
        current = read_installation()
    else:
        current = _parse_ini(installation_ini_path(state_dir)) if state_dir is not None else None
        if current is None:
            current = Installation(default_install_dir())
    if current.installer_managed:
        ini = installation_ini_path(state_dir)
        if canonical and not ini.is_file():
            _register_installation(
                current.directory, current.desktop_shortcut, True, state_dir,
                mirror_registry=True,
            )
        return current
    _register_installation(Path(directory), current.desktop_shortcut, False, state_dir,
                           mirror_registry=canonical)
    if state_dir is None:
        return read_installation()
    return _parse_ini(installation_ini_path(state_dir)) or Installation(Path(directory), current.desktop_shortcut)


def clear_installer_installation(directory: Path) -> bool:
    """Remove only this product's matching install.ini and HKCU registration."""
    current = read_installation()
    if not current.registered or not current.installer_managed:
        return False
    if current.directory.resolve() != Path(directory).resolve():
        raise ValueError("uninstall directory does not match the registered installation")
    path = installation_ini_path()
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if path.is_symlink() or not path.is_file():
            raise OSError("installation record target is invalid")
    if _is_windows() and _winreg is not None:
        winreg = _registry()
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_SUBKEY, 0, winreg.KEY_SET_VALUE)
        except FileNotFoundError:
            key = None
        if key is not None:
            with key:
                for name in (INSTALL_DIR_VALUE, DESKTOP_SHORTCUT_VALUE, INSTALLER_MANAGED_VALUE):
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REGISTRY_SUBKEY)
            except FileNotFoundError:
                pass
    # Keep the canonical record if registry mirror cleanup fails so a later
    # startup cannot silently select a different program directory.
    if info is not None:
        path.unlink()
    return True


def sync_owned_uninstall_display_version(directory: Path, version: str) -> bool:
    """Update DisplayVersion only on this AppId's matching Inno Setup entry."""
    if not isinstance(version, str) or not version or any(char in version for char in "\0\r\n"):
        raise ValueError("product version is invalid")
    if not _is_windows() or _winreg is None:
        return False
    winreg = _registry()
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, OWNED_UNINSTALL_SUBKEY, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return False
    with key:
        try:
            raw_location, location_type = winreg.QueryValueEx(key, UNINSTALL_LOCATION_VALUE)
        except FileNotFoundError:
            return False
    if location_type != winreg.REG_SZ:
        return False
    try:
        if _validate_install_dir(raw_location).resolve() != Path(directory).resolve():
            return False
    except (OSError, ValueError):
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, OWNED_UNINSTALL_SUBKEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, UNINSTALL_DISPLAY_VERSION_VALUE, 0, winreg.REG_SZ, version)
    except FileNotFoundError:
        return False
    return True


__all__ = [
    "DESKTOP_SHORTCUT_VALUE", "INSTALLER_APP_ID", "INSTALLER_MANAGED_VALUE",
    "INSTALL_DIR_VALUE", "Installation", "OWNED_UNINSTALL_SUBKEY", "REGISTRY_SUBKEY",
    "clear_installer_installation", "default_install_dir", "desktop_shortcut_enabled",
    "install_dir_for_runtime", "installation_ini_path", "read_installation",
    "register_installer_installation", "register_legacy_installation",
    "sync_owned_uninstall_display_version",
]
