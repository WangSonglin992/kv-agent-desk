import json
import unittest
from copy import deepcopy
from datetime import datetime, timezone

from PIL import ImageChops

from agent_dashboard.render import render, render_scene


NOW = datetime(2035, 6, 12, 14, 30, tzinfo=timezone.utc)


def scene_snapshot():
    agents = []
    for provider, name in (("codex", "Synthetic Codex Agent"),
                           ("zcode", "Synthetic ZCode Agent"),
                           ("antigravity", "Synthetic Gemini Agent")):
        agents.append({
            "provider": provider, "name": name, "state": "idle", "state_label": "空闲",
            "active_tasks": 0, "active_tasks_known": True, "stale": False,
            "tokens": {"known": True, "scope": "cumulative", "total": 12345,
                       "input": 10000, "output": 2345, "cached": 4321},
            "subscription": {
                "plan_name": f"Synthetic {provider} plan", "plan_spec": "Synthetic tier",
                "expires_at": "2035-12-31", "five_hour": {"known": True, "remaining_percent": 83,
                                                               "reset_at": "2035-06-12T17:00:00Z"},
                "weekly": {"known": True, "remaining_percent": 62,
                           "reset_at": "2035-06-19T10:00:00Z"},
            },
        })
    accounts = [
        {"id": "deepseek_lab", "name": "PRIVATE_SAMPLE_LAB_ACCOUNT", "brand": "deepseek",
         "kind": "balance", "known": True, "value": "123.45", "currency": "CNY", "stale": False},
        {"id": "deepseek_personal", "name": "PRIVATE_SAMPLE_PERSONAL_ACCOUNT", "brand": "deepseek",
         "kind": "balance", "known": True, "value": "67.89", "currency": "USD", "stale": False},
    ]
    return {"demo": True, "display": {"connected": False}, "agents": agents, "accounts": accounts}


class EditorRenderTests(unittest.TestCase):
    def test_page_and_account_freshness_hints_remain_separate_elements(self):
        snapshot = scene_snapshot()
        snapshot["accounts"][0]["stale"] = True
        _, manifest = render_scene(snapshot, NOW)
        before = {item["id"]: item for item in manifest}
        profile = {"elements": {"account.deepseek_lab.stale_note": {"dx": 9}}}
        _, changed = render_scene(snapshot, NOW, profile)
        after = {item["id"]: item for item in changed}
        self.assertEqual(before["page.stale_note"], after["page.stale_note"])
        self.assertEqual(after["account.deepseek_lab.stale_note"]["bbox"][0],
                         before["account.deepseek_lab.stale_note"]["bbox"][0] + 9)
        self.assertLess(before["account.deepseek_lab.stale_note"]["bbox"][2] -
                        before["account.deepseek_lab.stale_note"]["bbox"][0], 250)

    def test_each_layout_has_unique_stable_scene_ids_without_rendered_content(self):
        snapshot = scene_snapshot()
        original = deepcopy(snapshot)
        expected_shared = {
            "header", "header.title", "header.clock", "header.connection",
            "agent.codex", "agent.codex.name", "agent.codex.logo", "agent.codex.panel",
            "agent.codex.task_count", "agent.codex.tokens_total", "agent.codex.tokens_input",
            "agent.codex.tokens_output", "agent.codex.week_value",
            "agent.zcode.logo", "agent.antigravity.logo",
            "account.deepseek_lab", "account.deepseek_lab.logo", "account.deepseek_lab.value",
            "account.deepseek_personal", "account.deepseek_personal.unit",
        }
        for layout in ("classic", "aurora", "paper"):
            with self.subTest(layout=layout):
                profile = {"id": layout, "layout": layout}
                legacy_image = render(snapshot, NOW, profile, animation_time=0)
                image, manifest = render_scene(snapshot, NOW, profile, animation_time=0)
                ids = [node["id"] for node in manifest]
                self.assertEqual(len(ids), len(set(ids)))
                by_id = {node["id"]: node for node in manifest}
                self.assertTrue(expected_shared.issubset(by_id))
                self.assertIsNone(ImageChops.difference(legacy_image, image).getbbox())
                payload = json.dumps(manifest, ensure_ascii=False)
                self.assertNotIn("PRIVATE_SAMPLE", payload)
                self.assertNotIn("Synthetic codex plan", payload)
        self.assertEqual(snapshot, original)

    def test_single_text_and_group_offsets_affect_only_registered_scene_bounds(self):
        snapshot = scene_snapshot()
        original_snapshot = deepcopy(snapshot)
        profile = {"id": "editor-test", "layout": "classic", "elements": {
            "agent.codex.tokens_total": {"dx": 17, "dy": -4},
        }}
        original_profile = deepcopy(profile)
        base_image, base_manifest = render_scene(snapshot, NOW, {"id": "classic", "layout": "classic"}, 0)
        moved_image, moved_manifest = render_scene(snapshot, NOW, profile, 0)
        before = {node["id"]: node for node in base_manifest}
        after = {node["id"]: node for node in moved_manifest}

        self.assertEqual(after["agent.codex.tokens_total"]["bbox"], [
            before["agent.codex.tokens_total"]["bbox"][0] + 17,
            before["agent.codex.tokens_total"]["bbox"][1] - 4,
            before["agent.codex.tokens_total"]["bbox"][2] + 17,
            before["agent.codex.tokens_total"]["bbox"][3] - 4,
        ])
        self.assertEqual(after["agent.codex.task_count"], before["agent.codex.task_count"])
        self.assertIsNotNone(ImageChops.difference(base_image, moved_image).getbbox())

        grouped = {"id": "editor-group", "layout": "classic", "elements": {
            "agent.zcode": {"dx": 11, "dy": 7},
            "agent.zcode.panel": {"dx": 2, "dy": 3},
            "agent.zcode.logo": {"dx": -4, "dy": 1},
        }}
        grouped_image, grouped_manifest = render_scene(snapshot, NOW, grouped, 0)
        moved = {node["id"]: node for node in grouped_manifest}
        for identity, delta in (("agent.zcode", (11, 7)), ("agent.zcode.status_text", (11, 7)),
                                ("agent.zcode.panel", (13, 10)), ("agent.zcode.logo", (7, 8))):
            before_box = before[identity]["bbox"]
            dx, dy = delta
            self.assertEqual(moved[identity]["bbox"],
                             [before_box[0] + dx, before_box[1] + dy,
                              before_box[2] + dx, before_box[3] + dy])
        zcode_header = (560, 76, 1100, 140)
        self.assertIsNotNone(ImageChops.difference(base_image.crop(zcode_header),
                                                   grouped_image.crop(zcode_header)).getbbox())
        self.assertEqual(snapshot, original_snapshot)
        self.assertEqual(profile, original_profile)


if __name__ == "__main__":
    unittest.main()
