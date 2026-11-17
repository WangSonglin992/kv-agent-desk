from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

from PIL import ImageChops

from agent_dashboard import render as render_module
from agent_dashboard.render import _cluster_memory, _cluster_number, _cluster_update_label, render, render_scene
from agent_dashboard.ui_elements import ELEMENT_DEFINITIONS


def sample_cluster() -> dict:
    return {
        "known": True,
        "nodes": [
            {"name": "node-01", "cpu_allocated": 8, "cpu_total": 64,
             "memory_allocated_mb": 1536, "memory_total_mb": 262144,
             "gpu_allocated": 0, "gpu_total": 0, "state": "idle"},
            {"name": "node-02", "cpu_allocated": 40, "cpu_total": 64,
             "memory_allocated_mb": 65536, "memory_total_mb": 262144,
             "gpu_allocated": 2, "gpu_total": 4, "state": "mixed"},
            {"name": "node-03", "cpu_allocated": 64, "cpu_total": 64,
             "memory_allocated_mb": 262144, "memory_total_mb": 262144,
             "gpu_allocated": 4, "gpu_total": 4, "state": "allocated"},
            {"name": "node-04", "cpu_allocated": 0, "cpu_total": 64,
             "memory_allocated_mb": 0, "memory_total_mb": 262144,
             "gpu_allocated": 0, "gpu_total": 0, "state": "down"},
        ],
        "updated_at": "2035-01-02T12:34:56+00:00",
        "last_attempt_at": "2035-01-02T12:39:56+00:00",
        "stale": True,
        "checking": True,
        "detail": "synthetic detail",
    }


def _overlaps(first: list[int], second: list[int]) -> bool:
    return (first[0] < second[2] and first[2] > second[0]
            and first[1] < second[3] and first[3] > second[1])


class ClusterRenderTests(unittest.TestCase):
    def record_cluster_text(self, snapshot: dict, profile: dict | None = None) -> tuple[list[dict], dict[str, str]]:
        text_values: dict[str, str] = {}
        original_text = render_module._text

        def capture(draw, xy, value, element, **kwargs):
            if getattr(draw, "_groups", []) and draw._groups[0] == "cluster":
                identity = draw._name(element)
                text_values[identity] = str(value)
            return original_text(draw, xy, value, element, **kwargs)

        with patch("agent_dashboard.render._text", side_effect=capture):
            _, manifest = render_scene(snapshot, datetime(2035, 1, 2, 12, 0).astimezone(), profile)
        return manifest, text_values

    def test_cluster_slots_are_registered_under_nested_node_groups(self):
        self.assertEqual(ELEMENT_DEFINITIONS["cluster"]["kind"], "group")
        self.assertIsNone(ELEMENT_DEFINITIONS["cluster"]["parent"])
        for index in range(1, 5):
            group_id = f"cluster.node_{index}"
            self.assertEqual(ELEMENT_DEFINITIONS[group_id]["kind"], "group")
            self.assertEqual(ELEMENT_DEFINITIONS[group_id]["parent"], "cluster")
            for suffix in ("name", "state", "cpu_label", "cpu_value", "memory_label",
                           "memory_value", "gpu_label", "gpu_value"):
                self.assertIn(f"{group_id}.{suffix}", ELEMENT_DEFINITIONS)
                self.assertEqual(ELEMENT_DEFINITIONS[f"{group_id}.{suffix}"]["parent"], group_id)
        for identity in ("cluster.panel", "cluster.title", "cluster.updated_at", "cluster.stale_note"):
            self.assertIn(identity, ELEMENT_DEFINITIONS)

    def test_known_stale_cache_keeps_values_and_uses_success_timestamp(self):
        cluster = sample_cluster()
        manifest, text = self.record_cluster_text({"right_panel_mode": "cluster", "cluster": cluster})
        self.assertEqual(text["cluster.node_1.name"], "node-01")
        self.assertEqual(text["cluster.node_1.cpu_label"], "CPU")
        self.assertEqual(text["cluster.node_1.cpu_value"], "8/64")
        self.assertEqual(text["cluster.node_1.memory_value"], "1.5/256 GB")
        self.assertEqual(text["cluster.node_1.gpu_value"], "0/0")
        self.assertEqual(text["cluster.node_2.gpu_value"], "2/4")
        self.assertEqual(text["cluster.node_4.state"], "离线")
        self.assertEqual(text["cluster.stale_note"], "待检查更新")
        self.assertEqual(text["cluster.updated_at"], _cluster_update_label(cluster["updated_at"]))
        self.assertNotIn("12:39", text["cluster.updated_at"])

        nodes = {item["id"]: item for item in manifest}
        self.assertTrue(nodes["cluster" ]["visible"])
        self.assertTrue(nodes["cluster.panel"]["visible"])
        self.assertTrue(nodes["cluster.stale_note"]["visible"])
        self.assertFalse(nodes["cluster.checking"]["visible"])
        for index in range(1, 5):
            self.assertTrue(nodes[f"cluster.node_{index}"]["visible"])
            self.assertTrue(nodes[f"cluster.node_{index}.name"]["visible"])
            self.assertTrue(nodes[f"cluster.node_{index}.state_dot"]["visible"])

    def test_unknown_without_cache_waits_instead_of_showing_zero_usage(self):
        cluster = {"known": False, "nodes": [], "updated_at": None,
                   "last_attempt_at": "2035-01-02T12:39:56+00:00", "stale": False,
                   "checking": True, "detail": "synthetic detail"}
        manifest, text = self.record_cluster_text({"right_panel_mode": "cluster", "cluster": cluster})
        self.assertEqual(text["cluster.checking"], "检查中")
        self.assertEqual(text["cluster.updated_at"], "成功更新时间待获取")
        self.assertNotIn("12:39", text["cluster.updated_at"])
        for index in range(1, 5):
            self.assertEqual(text[f"cluster.node_{index}.name"], f"node-{index:02d}")
            self.assertEqual(text[f"cluster.node_{index}.cpu_value"], "等待数据")
            self.assertEqual(text[f"cluster.node_{index}.memory_value"], "等待数据")
            self.assertEqual(text[f"cluster.node_{index}.gpu_value"], "等待数据")
            self.assertEqual(text[f"cluster.node_{index}.state"], "等待")
        visible = {item["id"]: item for item in manifest}
        self.assertFalse(visible["cluster.stale_note"]["visible"])
        self.assertTrue(visible["cluster.checking"]["visible"])

    def test_unknown_cached_numbers_remain_unknown_instead_of_becoming_zero(self):
        cluster = sample_cluster()
        cluster["known"] = False
        _, text = self.record_cluster_text({"right_panel_mode": "cluster", "cluster": cluster})
        self.assertEqual(text["cluster.node_1.cpu_value"], "等待数据")
        self.assertEqual(text["cluster.node_1.memory_value"], "等待数据")
        self.assertEqual(text["cluster.node_1.gpu_value"], "等待数据")

    def test_nested_node_group_position_and_text_style_overrides_apply(self):
        snapshot = {"right_panel_mode": "cluster", "cluster": sample_cluster()}
        stamp = datetime(2035, 1, 2, 12, 0).astimezone()
        _, original_manifest = render_scene(snapshot, stamp)
        _, moved_manifest = render_scene(snapshot, stamp, {"elements": {
            "cluster.node_1": {"dx": 10, "dy": 4},
            "cluster.node_1.name": {"dx": 2, "dy": 1, "font_size": 21},
        }})
        original = {item["id"]: item for item in original_manifest}
        moved = {item["id"]: item for item in moved_manifest}
        self.assertEqual(moved["cluster.node_1"]["bbox"], [
            original["cluster.node_1"]["bbox"][0] + 10,
            original["cluster.node_1"]["bbox"][1] + 4,
            original["cluster.node_1"]["bbox"][2] + 10,
            original["cluster.node_1"]["bbox"][3] + 4,
        ])
        self.assertEqual(moved["cluster.node_1.name"]["bbox"][:2], [
            original["cluster.node_1.name"]["bbox"][0] + 12,
            original["cluster.node_1.name"]["bbox"][1] + 5,
        ])
        self.assertEqual(moved["cluster.node_1.name"]["font_size"], 21)

    def test_resource_numbers_use_roomier_default_and_shrink_to_row_bounds(self):
        snapshot = {"right_panel_mode": "cluster", "cluster": sample_cluster()}
        stamp = datetime(2035, 1, 2, 12, 0).astimezone()
        cases = (
            (100, {"margin": 24, "gap": 16, "account_width": 240, "top": 76, "bottom": 450}, 15, 18),
            (115, {"margin": 24, "gap": 16, "account_width": 240, "top": 76, "bottom": 450}, 17, 18),
            (115, {"margin": 64, "gap": 32, "account_width": 200, "top": 104, "bottom": 420}, 14, 13),
        )
        for scale, geometry, expected_size, expected_step in cases:
            profile = {"layout": "classic", "font_scale": scale, "geometry": geometry}
            with self.subTest(scale=scale, geometry=geometry):
                _, manifest = render_scene(snapshot, stamp, profile)
                nodes = {item["id"]: item for item in manifest}
                row = nodes["cluster.node_4"]["bbox"]
                cpu = nodes["cluster.node_4.cpu_value"]
                memory = nodes["cluster.node_4.memory_value"]
                gpu = nodes["cluster.node_4.gpu_value"]
                self.assertEqual(cpu["default_font_size"], expected_size)
                self.assertEqual(memory["default_font_size"], expected_size)
                self.assertEqual(gpu["default_font_size"], expected_size)
                self.assertEqual(memory["bbox"][1] - cpu["bbox"][1], expected_step)
                self.assertEqual(gpu["bbox"][1] - memory["bbox"][1], expected_step)
                self.assertLessEqual(gpu["bbox"][3], row[1] + row[3] - row[1])

    def test_resource_formatting_handles_zero_and_gigabyte_precision(self):
        self.assertEqual(_cluster_number(0), "0")
        self.assertEqual(_cluster_number(8), "8")
        self.assertEqual(_cluster_number(None), "—")
        self.assertEqual(_cluster_memory(0), "0")
        self.assertEqual(_cluster_memory(102), "0.1")
        self.assertEqual(_cluster_memory(1536), "1.5")
        self.assertEqual(_cluster_memory(2048), "2")

    def test_cluster_panel_fits_all_themes_and_geometry_extremes(self):
        geometries = (
            {"margin": 64, "gap": 32, "account_width": 200, "top": 104, "bottom": 420},
            {"margin": 16, "gap": 8, "account_width": 320, "top": 64, "bottom": 464},
            {"margin": 24, "gap": 16, "account_width": 240, "top": 76, "bottom": 450},
        )
        snapshot = {"right_panel_mode": "cluster", "cluster": sample_cluster()}
        for layout in ("classic", "aurora", "paper"):
            for geometry in geometries:
                for font_scale in (90, 115):
                    profile = {"id": layout, "layout": layout, "font_scale": font_scale,
                               "geometry": geometry}
                    with self.subTest(layout=layout, geometry=geometry, font_scale=font_scale):
                        _, manifest = render_scene(snapshot, datetime(2035, 1, 2, 12, 0).astimezone(), profile)
                        nodes = {item["id"]: item for item in manifest}
                        panel = nodes["cluster"]["bbox"]
                        self.assertEqual(panel, [
                            1920 - geometry["margin"] - geometry["account_width"],
                            geometry["top"], 1920 - geometry["margin"], geometry["bottom"],
                        ])
                        self.assertTrue(nodes["cluster.title"]["visible"])
                        self.assertTrue(nodes["cluster.updated_at"]["visible"])
                        content_ids = ["cluster.title", "cluster.stale_note", "cluster.updated_at"]
                        for index in range(1, 5):
                            content_ids.extend(f"cluster.node_{index}.{suffix}" for suffix in (
                                "name", "state", "cpu_label", "cpu_value", "memory_label",
                                "memory_value", "gpu_label", "gpu_value"))
                        for identity in content_ids:
                            box = nodes[identity]["bbox"]
                            self.assertGreaterEqual(box[0], panel[0], identity)
                            self.assertGreaterEqual(box[1], panel[1], identity)
                            self.assertLessEqual(box[2], panel[2], identity)
                            self.assertLessEqual(box[3], panel[3], identity)
                        text_nodes = [item for item in manifest
                                      if item.get("kind") == "text" and item["id"].startswith("cluster.")
                                      and item["visible"]]
                        for first_index, first in enumerate(text_nodes):
                            for second in text_nodes[first_index + 1:]:
                                self.assertFalse(_overlaps(first["bbox"], second["bbox"]),
                                                 (layout, font_scale, first["id"], second["id"]))

    def test_accounts_remain_the_default_and_explicit_mode_is_pixel_identical(self):
        snapshot = {"accounts": [
            {"id": "deepseek_lab", "name": "Lab", "brand": "deepseek", "kind": "balance",
             "known": False, "stale": True},
            {"id": "deepseek_personal", "name": "Personal", "brand": "deepseek", "kind": "balance",
             "known": True, "value": "12.34", "currency": "CNY"},
        ]}
        stamp = datetime(2035, 1, 2, 12, 0).astimezone()
        default = render(snapshot, stamp)
        explicit = render({**snapshot, "right_panel_mode": "accounts"}, stamp)
        self.assertIsNone(ImageChops.difference(default, explicit).getbbox())


if __name__ == "__main__":
    unittest.main()
