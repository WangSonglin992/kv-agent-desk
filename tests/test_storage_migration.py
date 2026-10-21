import copy
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent_dashboard.storage_migration as migration
from agent_dashboard.storage_migration import MIGRATION_MARKER_NAME, migrate_legacy_state
from agent_dashboard.ui_profiles import BUILTIN_PROFILES


def write_json(path: Path, value: object) -> bytes:
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def make_profile(profile_id: str, name: str) -> dict:
    profile = copy.deepcopy(BUILTIN_PROFILES["classic"])
    profile["id"] = profile_id
    profile["name"] = name
    return profile


class StorageMigrationTests(unittest.TestCase):
    def test_custom_profile_bundle_beats_newer_default_bundle_and_profiles_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            destination = base / "canonical"
            default_root = base / "legacy-default"
            selected_root = base / "legacy-selected"
            default_settings = write_json(default_root / "settings.json", {"ui_id": "classic"})
            selected_settings = write_json(selected_root / "settings.json", {"ui_id": "chosen-theme"})
            os.utime(default_root / "settings.json", (2_000_000_000, 2_000_000_000))
            os.utime(selected_root / "settings.json", (1_000_000_000, 1_000_000_000))
            write_json(default_root / "subscriptions.json", {"version": 1, "profiles": {"codex": {"plan_name": "default-bundle"}}})
            selected_subscriptions = write_json(
                selected_root / "subscriptions.json",
                {"version": 1, "profiles": {"codex": {"plan_name": "selected-bundle"}}},
            )
            (default_root / "last-good.json").parent.mkdir(parents=True, exist_ok=True)
            (default_root / "last-good.json").write_bytes(b"default-last-good")
            (selected_root / "last-good.json").parent.mkdir(parents=True, exist_ok=True)
            (selected_root / "last-good.json").write_bytes(b"selected-last-good")
            (default_root / "last-frame.png").write_bytes(b"default-frame")
            (default_root / "last-frame-meta.json").write_bytes(b'{"bundle":"default"}')
            (selected_root / "last-frame.png").write_bytes(b"synthetic-frame")
            (selected_root / "last-frame-meta.json").write_bytes(b'{"bundle":"selected"}')
            (default_root / "updates.json").write_bytes(b'{"version":1,"state":"old"}')
            (selected_root / "updates.json").write_bytes(b'{"version":1,"state":"ready"}')
            default_shared = write_json(default_root / "ui-profiles" / "shared.json", make_profile("shared", "Default copy"))
            default_only = write_json(default_root / "ui-profiles" / "default-only.json", make_profile("default-only", "Default only"))
            selected_chosen = write_json(selected_root / "ui-profiles" / "chosen-theme.json", make_profile("chosen-theme", "Chosen theme"))
            selected_shared = write_json(selected_root / "ui-profiles" / "shared.json", make_profile("shared", "Selected copy"))

            self.assertTrue(migrate_legacy_state(destination, [default_root, selected_root]))

            self.assertEqual((destination / "settings.json").read_bytes(), selected_settings)
            self.assertEqual((destination / "subscriptions.json").read_bytes(), selected_subscriptions)
            self.assertEqual((destination / "last-good.json").read_bytes(), b"selected-last-good")
            self.assertEqual((destination / "last-frame.png").read_bytes(), b"synthetic-frame")
            self.assertEqual((destination / "last-frame-meta.json").read_bytes(), b'{"bundle":"selected"}')
            self.assertEqual((destination / "updates.json").read_bytes(), b'{"version":1,"state":"ready"}')
            self.assertEqual(json.loads((destination / "ui-profiles" / "shared.json").read_text())["name"], "Selected copy")
            self.assertTrue((destination / "ui-profiles" / "chosen-theme.json").is_file())
            self.assertTrue((destination / "ui-profiles" / "default-only.json").is_file())
            self.assertTrue((destination / MIGRATION_MARKER_NAME).is_file())

            # Sources are untouched and every source copy has a backup before installation.
            self.assertEqual((default_root / "settings.json").read_bytes(), default_settings)
            self.assertEqual((selected_root / "ui-profiles" / "shared.json").read_bytes(), selected_shared)
            backups = list((destination / "migration-backups").glob("*/source-*/settings.json"))
            self.assertEqual(len(backups), 2)
            self.assertTrue(any(path.read_bytes() == default_shared for path in
                                (destination / "migration-backups").glob("*/source-000/ui-profiles/shared.json")))

    def test_newest_valid_settings_wins_when_none_selects_a_custom_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old_root, new_root = base / "older", base / "newer"
            write_json(old_root / "settings.json", {"ui_id": "classic"})
            newest = write_json(new_root / "settings.json", {"ui_id": "paper"})
            os.utime(old_root / "settings.json", (1_000_000_000, 1_000_000_000))
            os.utime(new_root / "settings.json", (2_000_000_000, 2_000_000_000))
            (old_root / "last-good.json").write_bytes(b"old")
            (new_root / "last-good.json").write_bytes(b"new")

            self.assertTrue(migrate_legacy_state(base / "destination", [old_root, new_root]))
            self.assertEqual((base / "destination" / "settings.json").read_bytes(), newest)
            self.assertEqual((base / "destination" / "last-good.json").read_bytes(), b"new")

    def test_existing_destination_files_are_never_overwritten_and_conflicts_are_backed_up(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            destination = base / "destination"
            source = base / "legacy"
            destination.mkdir()
            (destination / "last-good.json").write_bytes(b"canonical")
            (destination / "ui-profiles").mkdir()
            (destination / "ui-profiles" / "same.json").write_bytes(b"canonical-profile")
            write_json(source / "settings.json", {"ui_id": "classic"})
            (source / "last-good.json").write_bytes(b"legacy-conflict")
            write_json(source / "ui-profiles" / "same.json", make_profile("same", "Legacy conflict"))
            write_json(source / "ui-profiles" / "new.json", make_profile("new", "New profile"))

            self.assertTrue(migrate_legacy_state(destination, [source]))
            self.assertEqual((destination / "last-good.json").read_bytes(), b"canonical")
            self.assertEqual((destination / "ui-profiles" / "same.json").read_bytes(), b"canonical-profile")
            self.assertTrue((destination / "ui-profiles" / "new.json").is_file())
            self.assertTrue(any(path.read_bytes() == b"legacy-conflict" for path in
                                (destination / "migration-backups").glob("*/source-000/last-good.json")))

    def test_success_marker_makes_later_calls_noop(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            destination, source = base / "destination", base / "legacy"
            write_json(source / "settings.json", {"ui_id": "classic"})
            self.assertTrue(migrate_legacy_state(destination, [source]))
            first_backups = sorted(str(path.relative_to(destination)) for path in
                                   (destination / "migration-backups").rglob("*"))
            (source / "settings.json").write_bytes(b"late replacement")
            (source / "updates.json").write_bytes(b"late file")

            self.assertTrue(migrate_legacy_state(destination, [source]))
            second_backups = sorted(str(path.relative_to(destination)) for path in
                                    (destination / "migration-backups").rglob("*"))
            self.assertEqual(second_backups, first_backups)
            self.assertNotEqual((destination / "settings.json").read_bytes(), b"late replacement")
            self.assertFalse((destination / "updates.json").exists())

    def test_no_available_source_leaves_migration_retryable(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            destination, late_source = base / "destination", base / "late-source"
            self.assertFalse(migrate_legacy_state(destination, [late_source]))
            self.assertFalse((destination / MIGRATION_MARKER_NAME).exists())
            write_json(late_source / "settings.json", {"ui_id": "paper"})
            self.assertTrue(migrate_legacy_state(destination, [late_source]))
            self.assertTrue((destination / "settings.json").is_file())

    def test_failed_attempt_keeps_sources_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            destination, source = base / "destination", base / "legacy"
            original = write_json(source / "settings.json", {"ui_id": "classic"})
            (source / "last-good.json").write_bytes(b"preserved source")
            with patch.object(migration, "_copy_selected", side_effect=OSError("synthetic copy failure")):
                with self.assertRaisesRegex(OSError, "legacy state migration failed"):
                    migrate_legacy_state(destination, [source])
            self.assertFalse((destination / MIGRATION_MARKER_NAME).exists())
            self.assertEqual((source / "settings.json").read_bytes(), original)
            self.assertEqual((source / "last-good.json").read_bytes(), b"preserved source")

            self.assertTrue(migrate_legacy_state(destination, [source]))
            self.assertTrue((destination / MIGRATION_MARKER_NAME).is_file())
            self.assertEqual((destination / "last-good.json").read_bytes(), b"preserved source")

    def test_oversized_source_is_rejected_without_marker_or_source_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            destination, source = base / "destination", base / "legacy"
            original = write_json(source / "settings.json", {"ui_id": "classic"})
            (source / "last-good.json").write_bytes(b"oversized synthetic")
            with patch.object(migration, "MAX_TOTAL_BYTES", 4):
                with self.assertRaisesRegex(OSError, "legacy state migration failed"):
                    migrate_legacy_state(destination, [source])
            self.assertFalse((destination / MIGRATION_MARKER_NAME).exists())
            self.assertEqual((source / "settings.json").read_bytes(), original)
            self.assertEqual((source / "last-good.json").read_bytes(), b"oversized synthetic")

    def test_non_directory_destination_subfolders_abort_without_following_them(self):
        for folder_name in ("migration-backups", "ui-profiles"):
            with self.subTest(folder=folder_name), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                destination, source = base / "destination", base / "legacy"
                destination.mkdir()
                (destination / folder_name).write_bytes(b"not a directory")
                original = write_json(source / "settings.json", {"ui_id": "theme"})
                write_json(source / "ui-profiles" / "theme.json", make_profile("theme", "Synthetic"))
                with self.assertRaisesRegex(OSError, "legacy state migration failed"):
                    migrate_legacy_state(destination, [source])
                self.assertFalse((destination / MIGRATION_MARKER_NAME).exists())
                self.assertEqual((source / "settings.json").read_bytes(), original)

    def test_destination_symlink_subfolders_are_rejected(self):
        for folder_name in ("migration-backups", "ui-profiles"):
            with self.subTest(folder=folder_name), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                destination, source = base / "destination", base / "legacy"
                destination.mkdir()
                original = write_json(source / "settings.json", {"ui_id": "theme"})
                write_json(source / "ui-profiles" / "theme.json", make_profile("theme", "Synthetic"))
                real_lstat = migration._safe_lstat

                def lstat_with_destination_link(path):
                    if Path(path) == destination / folder_name:
                        return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_size=0, st_mtime_ns=0)
                    return real_lstat(Path(path))

                with patch.object(migration, "_safe_lstat", side_effect=lstat_with_destination_link):
                    with self.assertRaisesRegex(OSError, "legacy state migration failed"):
                        migrate_legacy_state(destination, [source])
                self.assertFalse((destination / MIGRATION_MARKER_NAME).exists())
                self.assertEqual((source / "settings.json").read_bytes(), original)
                self.assertFalse((destination / folder_name / "theme.json").exists())


if __name__ == "__main__":
    unittest.main()
