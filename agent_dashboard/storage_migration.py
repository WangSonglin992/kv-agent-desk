"""One-time, non-destructive migration of allowlisted local dashboard state."""

from __future__ import annotations

import errno
import json
import logging
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass
from typing import Iterable

from .settings import DEFAULT_SETTINGS, _validate_setting
from .ui_profiles import BUILTIN_PROFILES, MAX_PROFILE_BYTES, UIProfiles


MIGRATION_MARKER_NAME = ".legacy-state-migration-v1.json"
MAX_LEGACY_ROOTS = 16
MAX_FILES_PER_ROOT = 256
MAX_TOTAL_BYTES = 128 * 1024 * 1024

_ROOT_FILE_LIMITS = {
    "settings.json": 1024 * 1024,
    "subscriptions.json": 1024 * 1024,
    "last-good.json": 16 * 1024 * 1024,
    "accounts-last-good.json": 4 * 1024 * 1024,
    "last-frame.png": 32 * 1024 * 1024,
    "last-frame-meta.json": 256 * 1024,
    "updates.json": 1024 * 1024,
}
_PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}\Z")


class _MigrationFailure(Exception):
    def __init__(self, category: str):
        self.category = category


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


@dataclass(frozen=True)
class _Entry:
    source_index: int
    relative: Path
    backup_path: Path
    size: int
    mtime_ns: int


def _safe_lstat(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ENOTDIR}:
            return None
        raise _MigrationFailure("source_read") from None


def _read_regular_source(path: Path, limit: int) -> tuple[bytes, int] | None:
    info = _safe_lstat(path)
    if info is None or not stat.S_ISREG(info.st_mode):
        return None
    if info.st_size < 0 or info.st_size > limit:
        raise _MigrationFailure("size_limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
            return None
        raise _MigrationFailure("source_read") from None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            return None
        if opened.st_size < 0 or opened.st_size > limit:
            raise _MigrationFailure("size_limit")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise _MigrationFailure("size_limit")
        return data, opened.st_mtime_ns
    finally:
        os.close(descriptor)


def _ensure_destination_parent(destination: Path, parent: Path) -> None:
    try:
        relative = parent.relative_to(destination)
    except ValueError:
        raise _MigrationFailure("destination_invalid") from None
    current = destination
    for part in relative.parts:
        if part in {".", ".."}:
            raise _MigrationFailure("destination_invalid")
        current = current / part
        info = _safe_lstat(current)
        if info is None:
            try:
                current.mkdir()
            except FileExistsError:
                pass
            except OSError:
                raise _MigrationFailure("destination_invalid") from None
            info = _safe_lstat(current)
        if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise _MigrationFailure("destination_invalid")


def _write_exclusive(path: Path, data: bytes, destination: Path) -> bool:
    """Atomically publish a complete same-directory temporary file without replacing."""
    _ensure_destination_parent(destination, path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".migration-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        except OSError:
            raise _MigrationFailure("destination_write") from None
        return True
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _root_entries(root: Path, source_index: int, backup_root: Path,
                  total_bytes: list[int]) -> list[_Entry]:
    info = _safe_lstat(root)
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return []
    entries: list[_Entry] = []

    def snapshot(path: Path, relative: Path, limit: int) -> None:
        result = _read_regular_source(path, limit)
        if result is None:
            return
        data, mtime_ns = result
        total_bytes[0] += len(data)
        if total_bytes[0] > MAX_TOTAL_BYTES:
            raise _MigrationFailure("size_limit")
        backup_path = backup_root / f"source-{source_index:03d}" / relative
        try:
            if not _write_exclusive(backup_path, data, backup_root.parents[1]):
                raise _MigrationFailure("backup_collision")
        except OSError:
            raise _MigrationFailure("backup_write") from None
        entries.append(_Entry(source_index, relative, backup_path, len(data), mtime_ns))
        if len(entries) > MAX_FILES_PER_ROOT:
            raise _MigrationFailure("file_limit")

    for name, limit in _ROOT_FILE_LIMITS.items():
        snapshot(root / name, Path(name), limit)

    profile_directory = root / "ui-profiles"
    profile_info = _safe_lstat(profile_directory)
    if profile_info is not None and stat.S_ISDIR(profile_info.st_mode) and not stat.S_ISLNK(profile_info.st_mode):
        try:
            with os.scandir(profile_directory) as iterator:
                names = sorted((entry.name for entry in iterator if entry.name.endswith(".json")), key=str.casefold)
        except OSError as exc:
            if exc.errno not in {errno.ENOENT, errno.ENOTDIR}:
                raise _MigrationFailure("source_read") from None
            names = []
        for name in names:
            snapshot(profile_directory / name, Path("ui-profiles") / name, MAX_PROFILE_BYTES)
    return entries


def _valid_settings(entry: _Entry) -> dict | None:
    try:
        value = json.loads(entry.backup_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    normalized = DEFAULT_SETTINGS.copy()
    for name, field_value in value.items():
        if name not in DEFAULT_SETTINGS:
            continue
        try:
            normalized[name] = _validate_setting(name, field_value)
        except ValueError:
            return None
    return normalized


def _valid_custom_profiles(entries: Iterable[_Entry]) -> dict[tuple[int, str], _Entry]:
    result: dict[tuple[int, str], _Entry] = {}
    for entry in entries:
        if len(entry.relative.parts) != 2 or entry.relative.parts[0] != "ui-profiles":
            continue
        filename = entry.relative.name
        profile_id = filename[:-5] if filename.endswith(".json") else ""
        if not _PROFILE_ID.fullmatch(profile_id) or profile_id in BUILTIN_PROFILES:
            continue
        try:
            payload = json.loads(entry.backup_path.read_text(encoding="utf-8"),
                                 object_pairs_hook=_unique_json_object)
            normalized = UIProfiles.validate(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
            continue
        if normalized.get("id") == profile_id:
            result[(entry.source_index, profile_id)] = entry
    return result


def _choose_bundle(entries: list[_Entry], destination: Path) -> int | None:
    settings_entries = [
        entry for entry in entries
        if entry.relative == Path("settings.json") and _valid_settings(entry) is not None
    ]
    if not settings_entries:
        return min((entry.source_index for entry in entries), default=None)
    profiles = _valid_custom_profiles(entries)
    candidates = []
    for entry in settings_entries:
        settings = _valid_settings(entry)
        assert settings is not None
        selected_id = settings["ui_id"]
        selected_custom = selected_id not in BUILTIN_PROFILES and (entry.source_index, selected_id) in profiles
        candidates.append((not selected_custom, -entry.mtime_ns, entry.source_index))
    return min(candidates)[2]


def _valid_marker(path: Path) -> bool:
    info = _safe_lstat(path)
    if info is None:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size > 1024:
        raise _MigrationFailure("marker_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise _MigrationFailure("marker_invalid") from None
    if (not isinstance(value, dict) or type(value.get("version")) is not int
            or value.get("version") != 1):
        raise _MigrationFailure("marker_invalid")
    return True


def _copy_selected(entries: list[_Entry], destination: Path, bundle_source: int,
                   valid_profiles: dict[tuple[int, str], _Entry]) -> None:
    by_source_and_name: dict[tuple[int, Path], _Entry] = {
        (entry.source_index, entry.relative): entry for entry in entries
    }
    source_order = [bundle_source] + sorted({entry.source_index for entry in entries} - {bundle_source})

    settings_candidates = [
        entry for entry in entries
        if entry.relative == Path("settings.json") and _valid_settings(entry) is not None
    ]
    settings_entry = next((entry for index in source_order for entry in settings_candidates
                           if entry.source_index == index), None)

    for name in _ROOT_FILE_LIMITS:
        if name == "settings.json":
            if settings_entry is None:
                continue
            source_entries = [settings_entry]
        else:
            source_entries = [by_source_and_name[(index, Path(name))]
                              for index in source_order
                              if (index, Path(name)) in by_source_and_name]
        target = destination / name
        if os.path.lexists(target):
            continue
        if source_entries:
            data = source_entries[0].backup_path.read_bytes()
            _write_exclusive(target, data, destination)

    if not valid_profiles:
        return
    profile_directory = destination / "ui-profiles"
    _ensure_destination_parent(destination, profile_directory)
    for profile_id in sorted({profile_id for _index, profile_id in valid_profiles}):
        target = profile_directory / f"{profile_id}.json"
        if os.path.lexists(target):
            continue
        chosen = next((valid_profiles[(index, profile_id)] for index in source_order
                       if (index, profile_id) in valid_profiles), None)
        if chosen is not None:
            _write_exclusive(target, chosen.backup_path.read_bytes(), destination)


def _write_success_marker(destination: Path) -> None:
    marker = destination / MIGRATION_MARKER_NAME
    data = b'{"version":1}\n'
    try:
        created = _write_exclusive(marker, data, destination)
    except OSError:
        raise _MigrationFailure("marker_write") from None
    if not created and not _valid_marker(marker):
        raise _MigrationFailure("marker_invalid")


def migrate_legacy_state(destination: Path, legacy_roots: list[Path]) -> bool:
    """Back up and copy allowlisted old state once, without replacing destination files.

    Returns True when migration is complete or an earlier success marker exists.
    Returns False when no source data is available yet. A failed migration
    raises a fixed-message OSError, leaves no success marker, and can be retried.
    """
    destination = Path(destination)
    if not isinstance(legacy_roots, list) or len(legacy_roots) > MAX_LEGACY_ROOTS:
        logging.warning("Legacy state migration skipped (input)")
        raise OSError("legacy state migration failed")
    try:
        destination_info = _safe_lstat(destination)
        if destination_info is not None and (
            stat.S_ISLNK(destination_info.st_mode) or not stat.S_ISDIR(destination_info.st_mode)
        ):
            raise _MigrationFailure("destination_invalid")
        destination.mkdir(parents=True, exist_ok=True)
        marker = destination / MIGRATION_MARKER_NAME
        if _valid_marker(marker):
            return True

        destination_resolved = destination.resolve(strict=False)
        roots: list[tuple[int, Path]] = []
        seen: set[str] = set()
        for index, value in enumerate(legacy_roots):
            root = Path(value)
            root_key = os.path.normcase(str(root.resolve(strict=False)))
            if root_key == os.path.normcase(str(destination_resolved)) or root_key in seen:
                continue
            seen.add(root_key)
            roots.append((index, root))

        migration_id = uuid.uuid4().hex
        backup_root = destination / "migration-backups" / migration_id
        all_entries: list[_Entry] = []
        total_bytes = [0]
        for source_index, root in roots:
            info = _safe_lstat(root)
            if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                continue
            all_entries.extend(_root_entries(root, source_index, backup_root, total_bytes))
        if not all_entries:
            return False

        bundle_source = _choose_bundle(all_entries, destination)
        if bundle_source is None:
            return False
        valid_profiles = _valid_custom_profiles(all_entries)
        _copy_selected(all_entries, destination, bundle_source, valid_profiles)
        _write_success_marker(destination)
        return True
    except _MigrationFailure as exc:
        logging.warning("Legacy state migration failed (%s)", exc.category)
        raise OSError("legacy state migration failed") from None
    except OSError:
        logging.warning("Legacy state migration failed (io)")
        raise OSError("legacy state migration failed") from None
    except Exception:
        logging.warning("Legacy state migration failed (unexpected)")
        raise OSError("legacy state migration failed") from None


__all__ = ["MIGRATION_MARKER_NAME", "migrate_legacy_state"]
