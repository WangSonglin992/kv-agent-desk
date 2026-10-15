import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_dashboard.ui_profiles import (
    BUILTIN_PROFILES,
    CANVAS,
    COLOR_ROLES,
    GEOMETRY_DEFAULTS,
    MAX_CUSTOM_PROFILES,
    MAX_PROFILE_BYTES,
    UIProfiles,
)


def sample_profile(profile_id="test-theme", layout="aurora", **overrides):
    profile = {
        "schema_version": 1,
        "id": profile_id,
        "name": "Synthetic Theme",
        "layout": layout,
    }
    profile.update(overrides)
    return profile


class UIProfilesTests(unittest.TestCase):
    def test_builtin_profiles_are_complete_and_unknown_id_falls_back_to_classic(self):
        profiles = UIProfiles(None)
        listed = profiles.list()
        self.assertEqual([item["id"] for item in listed], ["classic", "aurora", "paper"])
        self.assertTrue(all(item["builtin"] for item in listed))
        self.assertEqual(profiles.get("unknown-theme"), profiles.get("classic"))
        for profile_id in ("classic", "aurora", "paper"):
            profile = profiles.get(profile_id)
            self.assertEqual(profile["schema_version"], 1)
            self.assertEqual(profile["canvas"], CANVAS)
            self.assertEqual(set(profile["colors"]), set(COLOR_ROLES))
            self.assertEqual(profile["geometry"], GEOMETRY_DEFAULTS)

    def test_missing_style_fields_use_layout_defaults_and_partial_overrides_merge(self):
        profiles = UIProfiles(None)
        stored = profiles.import_profile(
            sample_profile(
                colors={"background": "#102030"},
                geometry={"margin": 32},
            )
        )
        aurora = BUILTIN_PROFILES["aurora"]
        self.assertEqual(stored["canvas"], [1920, 480])
        self.assertEqual(stored["colors"]["background"], "#102030")
        self.assertEqual(stored["colors"]["codex"], aurora["colors"]["codex"])
        self.assertEqual(stored["font_scale"], aurora["font_scale"])
        self.assertEqual(stored["radius"], aurora["radius"])
        self.assertEqual(stored["geometry"]["margin"], 32)
        self.assertEqual(stored["geometry"]["gap"], aurora["geometry"]["gap"])

    def test_custom_profile_import_export_and_restart_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "ui-profiles"
            store = UIProfiles(directory)
            imported = store.import_profile(sample_profile("warm-paper", "paper"))

            self.assertTrue(store.contains("warm-paper"))
            self.assertEqual(store.export("warm-paper"), imported)
            summary = next(item for item in store.list() if item["id"] == "warm-paper")
            self.assertFalse(summary["builtin"])
            self.assertEqual(summary["layout"], "paper")
            self.assertIn("background", summary["colors"])

            restarted = UIProfiles(directory)
            self.assertEqual(restarted.get("warm-paper"), imported)
            self.assertEqual(restarted.export("classic"), BUILTIN_PROFILES["classic"])

    def test_memory_mode_stores_custom_profiles_without_files(self):
        store = UIProfiles(None)
        saved = store.import_profile(sample_profile("memory-theme"))
        self.assertTrue(store.contains("memory-theme"))
        self.assertEqual(store.get("memory-theme"), saved)
        self.assertEqual(store.export("memory-theme"), saved)
        self.assertEqual(len(store.list()), 4)

    def test_unknown_root_style_and_geometry_keys_are_rejected(self):
        cases = (
            sample_profile(script="alert(1)"),
            sample_profile(assets=["https://example.invalid/image.png"]),
            sample_profile(colors={"background": "#000000", "background_image": "x"}),
            sample_profile(geometry={"margin": 24, "path": "image.png"}),
        )
        for profile in cases:
            with self.subTest(keys=sorted(profile)), self.assertRaises(ValueError):
                UIProfiles(None).import_profile(profile)

    def test_markup_urls_and_pathlike_profile_names_are_rejected(self):
        for name in (
            "<script>alert(1)</script>",
            "https://example.invalid/theme",
            "file:theme",
            r"folder\theme",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                UIProfiles(None).import_profile(sample_profile(name=name))

    def test_invalid_ids_layout_canvas_and_versions_are_rejected(self):
        cases = (
            sample_profile("../escape"),
            sample_profile("Uppercase"),
            sample_profile(layout="untrusted-template"),
            sample_profile(canvas=[1280, 720]),
            sample_profile(schema_version=True),
        )
        for profile in cases:
            with self.subTest(profile_fields=sorted(profile)), self.assertRaises(ValueError):
                UIProfiles(None).import_profile(profile)

    def test_invalid_types_nonfinite_values_and_out_of_range_geometry_are_rejected(self):
        cases = (
            sample_profile(font_scale=float("nan")),
            sample_profile(font_scale=True),
            sample_profile(radius=29),
            sample_profile(colors={"background": "red"}),
            sample_profile(geometry={"gap": True}),
            sample_profile(geometry={"margin": 100}),
        )
        for profile in cases:
            with self.subTest(profile_fields=sorted(profile)), self.assertRaises(ValueError):
                UIProfiles(None).import_profile(profile)

    def test_boundary_geometry_fits_agent_and_account_slots(self):
        profile = UIProfiles(None).import_profile(
            sample_profile(
                geometry={
                    "margin": 64,
                    "gap": 32,
                    "account_width": 320,
                    "top": 104,
                    "bottom": 420,
                }
            )
        )
        geometry = profile["geometry"]
        agent_width = (1920 - 2 * geometry["margin"] - geometry["account_width"] - 3 * geometry["gap"]) // 3
        account_height = (geometry["bottom"] - geometry["top"] - geometry["gap"]) // 2
        self.assertGreaterEqual(agent_width, 400)
        self.assertGreaterEqual(account_height, 140)

    def test_builtin_id_collision_is_rejected(self):
        with self.assertRaises(ValueError):
            UIProfiles(None).import_profile(sample_profile("classic"))

    def test_custom_profile_count_is_bounded(self):
        store = UIProfiles(None)
        for index in range(MAX_CUSTOM_PROFILES):
            store.import_profile(sample_profile(f"theme-{index}"))
        with self.assertRaises(ValueError):
            store.import_profile(sample_profile("theme-over-limit"))

    def test_failed_atomic_replace_preserves_existing_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            store = UIProfiles(Path(folder) / "ui-profiles")
            original = store.import_profile(sample_profile("atomic-theme", name="First Theme"))
            target = Path(folder) / "ui-profiles" / "atomic-theme.json"
            before = target.read_bytes()
            replacement = sample_profile("atomic-theme", name="Second Theme")
            with patch("agent_dashboard.ui_profiles.os.replace", side_effect=PermissionError("busy")):
                with self.assertRaises(PermissionError):
                    store.import_profile(replacement)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(store.get("atomic-theme"), original)
            self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_duplicate_json_keys_and_corrupt_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "ui-profiles"
            directory.mkdir()
            path = directory / "bad-theme.json"
            path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            store = UIProfiles(directory)
            self.assertFalse(store.contains("bad-theme"))
            self.assertEqual(store.get("bad-theme"), BUILTIN_PROFILES["classic"])
            self.assertNotIn("bad-theme", [item["id"] for item in store.list()])

    def test_profile_file_over_64k_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "ui-profiles"
            directory.mkdir()
            path = directory / "large-theme.json"
            path.write_bytes(b"{}" + b" " * MAX_PROFILE_BYTES)
            store = UIProfiles(directory)
            self.assertFalse(store.contains("large-theme"))
            self.assertEqual(store.get("large-theme"), BUILTIN_PROFILES["classic"])


if __name__ == "__main__":
    unittest.main()
