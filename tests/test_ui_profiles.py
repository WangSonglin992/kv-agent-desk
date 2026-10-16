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
from agent_dashboard.ui_elements import ELEMENT_DEFINITIONS


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
            self.assertEqual(profile["elements"], {})

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
        self.assertEqual(stored["elements"], {})

    def test_element_registry_has_stable_group_parent_and_kind_metadata(self):
        children = {
            "header": {
                "panel": "shape", "rule": "shape", "title": "text", "platform": "text",
                "date": "text", "connection": "text", "live_dot": "shape",
                "live_label": "text", "clock": "text",
            },
            **{
                f"agent.{provider}": {
                    "panel": "shape", "logo": "image", "name": "text", "brand": "text",
                    "status_badge": "shape", "status_indicator": "shape", "status_text": "text",
                    "task_label": "text", "task_count": "text", "task_unit": "text",
                    "plan_label": "text", "plan_name": "text", "plan_spec": "text", "plan_expiry": "text",
                    "weekly_label": "text", "weekly_percent": "text", "weekly_bar": "shape",
                    "weekly_bar_reset": "text", "tokens_label": "text", "tokens_total": "text",
                    "tokens_input": "text", "tokens_output": "text", "tokens_cached": "text",
                    "tokens_panel": "shape", "tokens_rule": "shape", "quota_rule": "shape",
                    "five_hour_label": "text", "five_hour_value": "text", "five_hour_reset": "text",
                    "week_label": "text", "week_value": "text", "week_reset": "text", "stale_note": "text",
                }
                for provider in ("codex", "zcode", "antigravity")
            },
            **{
                f"account.{account}": {
                    "panel": "shape", "logo": "image", "title": "text", "subtitle": "text",
                    "value": "text", "unit": "text", "rule": "shape", "detail": "text",
                    "stale_note": "text",
                }
                for account in ("deepseek_lab", "deepseek_personal")
            },
        }
        expected_groups = set(children)
        expected_ids = expected_groups | {
            f"{parent}.{suffix}" for parent, elements in children.items() for suffix in elements
        }
        self.assertEqual(set(ELEMENT_DEFINITIONS), expected_ids | {"page.stale_note"})
        self.assertEqual(ELEMENT_DEFINITIONS["page.stale_note"]["kind"], "text")
        self.assertIsNone(ELEMENT_DEFINITIONS["page.stale_note"]["parent"])
        for group in expected_groups:
            self.assertEqual(ELEMENT_DEFINITIONS[group]["kind"], "group")
            self.assertIsNone(ELEMENT_DEFINITIONS[group]["parent"])
        for parent, elements in children.items():
            for suffix, kind in elements.items():
                definition = ELEMENT_DEFINITIONS[f"{parent}.{suffix}"]
                self.assertEqual(definition["kind"], kind)
                self.assertEqual(definition["parent"], parent)

    def test_element_overrides_validate_and_round_trip_across_restart(self):
        elements = {
            "header": {"dx": 4, "dy": -3},
            "agent.codex": {"dx": 12, "dy": 2},
            "agent.codex.logo": {"dx": 3, "dy": -1},
            "agent.codex.name": {"dx": 2, "dy": 1, "font_size": 38, "font_family": "display-font_1",
                                 "color": "#abcdef", "bold": True, "italic": False},
            "account.deepseek_lab.value": {"font_size": 28},
        }
        draft = UIProfiles.validate(sample_profile("draft-theme", "aurora", elements=elements))
        normalized_elements = {**elements,
                               "agent.codex.name": {**elements["agent.codex.name"], "color": "#ABCDEF"}}
        self.assertEqual(draft["elements"], normalized_elements)

        # Draft validation permits built-in IDs, while import still protects them.
        built_in_draft = UIProfiles.validate(sample_profile("classic", "classic", elements=elements))
        self.assertEqual(built_in_draft["elements"], normalized_elements)
        with self.assertRaises(ValueError):
            UIProfiles(None).import_profile(sample_profile("classic", "classic", elements=elements))

        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "ui-profiles"
            store = UIProfiles(directory)
            imported = store.import_profile(sample_profile("element-theme", "paper", elements=elements))
            self.assertEqual(imported["elements"], normalized_elements)
            self.assertEqual(store.export("element-theme"), imported)
            self.assertEqual(UIProfiles(directory).get("element-theme"), imported)

    def test_invalid_element_ids_properties_types_and_ranges_are_rejected(self):
        cases = (
            {"not.registered": {"dx": 1}},
            {"agent.codex.name": {"text": "replace label"}},
            {"agent.codex.name": {"content": "task title"}},
            {"agent.codex.name": {"value": "account data"}},
            {"agent.codex.name": {"url": "https://example.invalid"}},
            {"agent.codex.logo": {"font_size": 24}},
            {"agent.codex.panel": {"font_size": 24}},
            {"header": {"font_size": 24}},
            {"header.title": {"dx": True}},
            {"header.title": {"dy": 1.5}},
            {"header.title": {"font_size": float("nan")}},
            {"header.title": {"dx": 1921}},
            {"header.title": {"dy": -481}},
            {"header.title": {"font_size": 7}},
            {"header.title": {"font_size": 97}},
            {"header.title": {"font_family": ""}},
            {"header.title": {"font_family": "Uppercase"}},
            {"header.title": {"font_family": "x" * 65}},
            {"header.title": {"color": "red"}},
            {"header.title": {"color": "#12ab"}},
            {"header.title": {"bold": 1}},
            {"header.title": {"italic": "yes"}},
            {"header.panel": {"color": "#123456"}},
            {"header.panel": {"bold": True}},
            None,
            [],
        )
        for elements in cases:
            with self.subTest(elements=elements), self.assertRaises(ValueError):
                UIProfiles.validate(sample_profile(elements=elements))

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
            original = store.import_profile(sample_profile(
                "atomic-theme", name="First Theme", elements={"agent.codex.name": {"dx": 6, "font_size": 30}}))
            target = Path(folder) / "ui-profiles" / "atomic-theme.json"
            before = target.read_bytes()
            replacement = sample_profile(
                "atomic-theme", name="Second Theme", elements={"agent.codex.name": {"dx": 20, "font_size": 40}})
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
