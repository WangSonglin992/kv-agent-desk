import unittest
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch
from PIL import Image, ImageChops, ImageDraw
from agent_dashboard.render import (
    activity_state,
    _agent_metrics,
    _draw_agent_header,
    _resolve_design,
    _status_label,
    font,
    animation_period,
    expiry_text,
    is_animated,
    logo,
    number,
    render,
    render_scene,
)


class RenderTests(unittest.TestCase):
    def test_unknown_and_long_texts_render_without_size_change(self):
        agents = [{"provider": key, "name": name, "state": "unknown",
                   "project": "D:/项目/当前任务", "task": "非常长的中文任务" * 100,
                   "progress": {"known": False}, "tokens": {"known": False}}
                  for key, name in [("codex", "Codex"), ("zcode", "ZCode")]]
        self.assertEqual(render({"agents": agents}).size, (1920, 480))

    def test_cached_is_never_added_to_total_by_renderer(self):
        self.assertEqual(number(None), "0")
        self.assertEqual(number(1000), "1.0K")
        self.assertEqual(number(1234567), "1.23M")
        self.assertEqual(number(100_000_000), "100.00M")
        self.assertEqual(number(123_000_000), "123.00M")
        self.assertEqual(number(1_000_000_000), "1.00B")
        self.assertTrue(all(number(value).isascii() for value in (10, 1000, 1234567, 123000000, 1000000000)))

    def test_task_names_and_plan_do_not_appear_on_screen(self):
        stamp = datetime(2026, 10, 15, 12, 0).astimezone()
        data = {"agents": [{"provider": "codex", "name": "Codex", "state": "working", "active_tasks": 2,
                            "tokens": {"known": False}, "task": "private task A",
                            "progress": {"known": True, "completed": 1, "total": 10}}]}
        changed = deepcopy(data)
        changed["agents"][0]["task"] = "different private task B"
        changed["agents"][0]["progress"]["completed"] = 9
        first = render(data, stamp)
        second = render(changed, stamp)
        self.assertIsNone(ImageChops.difference(first, second).getbbox())

    def test_expiry_preserves_date_precision_and_rejects_invalid_dates(self):
        self.assertEqual(expiry_text("2026-11-15"), "到期 2026/11/15")
        self.assertEqual(expiry_text(None), "到期待填写")
        self.assertEqual(expiry_text("invalid-date"), "到期待填写")

    @staticmethod
    def sample_snapshot():
        return {
            "demo": True,
            "display": {"connected": False},
            "agents": [
                {
                    "provider": "codex", "name": "Codex", "state": "working", "state_label": "工作中",
                    "active_tasks": 2, "active_tasks_known": True, "stale": True,
                    "project": "private/project", "task": "private task title", "progress": {"known": True},
                    "tokens": {"known": True, "total": 123456, "input": 100000, "output": 23456,
                               "cached": 80000, "date": "2026-10-15"},
                    "subscription": {
                        "plan_name": "Synthetic Plan", "plan_spec": "Synthetic monthly plan",
                        "expires_at": "2035-01-02", "five_hour": {"known": True, "remaining_percent": 85,
                                                                       "reset_at": "2035-01-02T12:00:00Z"},
                        "weekly": {"known": True, "remaining_percent": 72,
                                   "reset_at": "2035-01-04T12:00:00Z"},
                    },
                },
                {
                    "provider": "zcode", "name": "ZCode", "state": "waiting", "state_label": "等待输入",
                    "active_tasks": 1, "active_tasks_known": True, "stale": False,
                    "tokens": {"known": True, "total": 65432, "input": 54000, "output": 11432,
                               "cached": 31000, "date": "2026-10-15"},
                    "subscription": {
                        "plan_name": "Synthetic Coding Plan", "plan_spec": "Synthetic tier",
                        "expires_at": "2035-02-03", "five_hour": {"known": False},
                        "weekly": {"known": True, "remaining_percent": 58,
                                   "reset_at": "2035-01-05T12:00:00Z"},
                    },
                },
                {
                    "provider": "antigravity", "name": "Antigravity", "state": "unknown",
                    "state_label": "状态未知", "active_tasks": None, "active_tasks_known": False,
                    "stale": False, "tokens": {"known": False},
                    "subscription": {
                        "plan_name": "Synthetic Google Plan", "plan_spec": "Synthetic tier",
                        "expires_at": "2035-03-04", "quota_group": "gemini",
                        "five_hour": {"known": True, "remaining_percent": 80, "reset_at": "2035-01-02T17:00:00Z"},
                        "weekly": {"known": True, "remaining_percent": 64, "reset_at": "2035-01-06T12:00:00Z"},
                        "quota_windows": [
                            {"known": True, "label": f"Synthetic Model {index}", "model": f"model-{index}",
                             "remaining_percent": 90 - index, "reset_at": "2035-01-06T12:00:00Z"}
                            for index in range(6)
                        ],
                    },
                },
            ],
            "accounts": [
                {"id": "deepseek_lab", "name": "课题组 DeepSeek", "brand": "deepseek", "kind": "balance",
                 "known": True, "value": "123.45", "currency": "CNY", "stale": False},
                {"id": "deepseek_personal", "name": "个人 DeepSeek", "brand": "deepseek", "kind": "balance",
                 "known": True, "value": "67.89", "currency": "CNY", "stale": True},
            ],
        }

    def test_three_profiles_render_distinct_fixed_canvas(self):
        stamp = datetime(2035, 1, 2, 12, 0).astimezone()
        snapshot = self.sample_snapshot()
        images = {
            layout: render(snapshot, stamp, {"id": layout, "layout": layout}, animation_time=0.0)
            for layout in ("classic", "aurora", "paper")
        }
        self.assertEqual({image.size for image in images.values()}, {(1920, 480)})
        self.assertEqual(len({hash(image.tobytes()) for image in images.values()}), 3)

    def test_provider_and_account_logos_are_local_and_chatgpt_mark_has_neutral_badge(self):
        for brand in ("chatgpt", "glm", "google", "deepseek"):
            with self.subTest(brand=brand):
                self.assertIsNotNone(logo(brand))
        paper = render(self.sample_snapshot(), datetime(2035, 1, 2, 12, 0).astimezone(),
                       {"id": "paper", "layout": "paper"}, animation_time=0.0)
        self.assertEqual(paper.getpixel((50, 111)), (16, 16, 16))

    def test_render_preserves_snapshot_and_profile_and_hides_private_fields(self):
        stamp = datetime(2035, 1, 2, 12, 0).astimezone()
        snapshot = self.sample_snapshot()
        original = deepcopy(snapshot)
        profile = {
            "schema_version": 1, "id": "paper", "name": "Synthetic Paper",
            "layout": "paper", "canvas": [1920, 480], "font_scale": 100, "radius": 8,
            "colors": {"background": "#F4F1EB", "busy": "#B44949"},
            "geometry": {"margin": 24, "gap": 16, "account_width": 240, "top": 76, "bottom": 450},
        }
        profile_original = deepcopy(profile)
        changed_private = deepcopy(snapshot)
        changed_private["agents"][0]["task"] = "another private task title"
        changed_private["agents"][0]["project"] = "another/private/path"
        for layout in ("classic", "aurora", "paper"):
            current_profile = {**profile, "layout": layout, "id": layout}
            image = render(snapshot, stamp, current_profile, animation_time=0.0)
            changed_image = render(changed_private, stamp, current_profile, animation_time=0.0)
            self.assertIsNone(ImageChops.difference(image, changed_image).getbbox())
        self.assertEqual(snapshot, original)
        self.assertEqual(profile, profile_original)

    def test_working_spinner_changes_frame_but_not_snapshot(self):
        stamp = datetime(2035, 1, 2, 12, 0).astimezone()
        agent = self.sample_snapshot()["agents"][0]
        original = deepcopy(agent)
        snapshot = {"agents": [agent]}
        first = render(snapshot, stamp, animation_time=0.0)
        second = render(snapshot, stamp, animation_time=0.5)
        self.assertIsNotNone(ImageChops.difference(first, second).getbbox())
        self.assertEqual(snapshot["agents"][0], original)
        self.assertTrue(is_animated(agent))
        self.assertEqual(animation_period(agent), 2.0)

    def test_busy_threshold_is_strict_and_uses_fast_spinner(self):
        waiting = {"provider": "codex", "state": "waiting", "active_tasks": 3,
                   "active_tasks_known": True}
        busy = {**waiting, "active_tasks": 4}
        self.assertEqual(activity_state(waiting), "idle")
        self.assertEqual(animation_period(waiting), None)
        self.assertEqual(activity_state(busy), "busy")
        self.assertTrue(is_animated(busy))
        self.assertEqual(animation_period(busy), 0.65)
        stamp = datetime(2035, 1, 2, 12, 0).astimezone()
        snapshot = {"agents": [busy]}
        first = render(snapshot, stamp, animation_time=0.0)
        second = render(snapshot, stamp, animation_time=0.17)
        self.assertIsNotNone(ImageChops.difference(first, second).getbbox())

        normal_card = render({"agents": [waiting]}, stamp, animation_time=0.0)
        busy_card = render(snapshot, stamp, animation_time=0.0)
        normal_badge = normal_card.crop((52, 130, 165, 160)).convert("RGB").get_flattened_data()
        busy_badge = busy_card.crop((52, 130, 165, 160)).convert("RGB").get_flattened_data()
        is_busy_accent = lambda rgb: rgb[0] > 150 and rgb[0] > rgb[1] * 1.35 and rgb[0] > rgb[2] * 1.35
        self.assertFalse(any(is_busy_accent(rgb) for rgb in normal_badge))
        self.assertTrue(any(is_busy_accent(rgb) for rgb in busy_badge))

    def test_unknown_antigravity_model_count_never_means_busy(self):
        agent = {
            "provider": "antigravity", "state": "unknown", "active_tasks": None,
            "active_tasks_known": False,
            "subscription": {"quota_windows": [
                {"known": True, "remaining_percent": 80, "reset_at": "2035-01-06T12:00:00Z"}
                for _ in range(8)
            ]},
        }
        self.assertEqual(activity_state(agent), "idle")
        self.assertFalse(is_animated(agent))
        self.assertIsNone(animation_period(agent))

    def test_status_caption_uses_state_and_rejects_misleading_unknown_labels(self):
        self.assertEqual(_status_label({"state": "working", "state_label": "工作中"}, "working"), "工作中")
        self.assertEqual(_status_label({"state": "waiting", "state_label": "等待输入"}, "idle"), "等待输入")
        self.assertEqual(_status_label({"state": "offline", "state_label": "未运行"}, "idle"), "未运行")
        self.assertEqual(_status_label({"state": "unknown", "state_label": "已连接"}, "idle"), "状态未知")
        self.assertEqual(_status_label({"state": "unknown", "state_label": "数据待更新", "stale": True}, "idle"),
                         "数据待更新")
        self.assertEqual(_status_label({"state": "waiting"}, "busy"), "忙碌")

    def test_idle_and_waiting_mascots_use_regular_refresh_without_fast_cadence(self):
        with patch("agent_dashboard.render.has_mascot", return_value=True):
            for state in ("idle", "waiting", "unknown", "offline"):
                self.assertFalse(is_animated({"provider": "codex", "state": state}))

    def test_idle_mascot_advances_with_regular_render_time(self):
        snapshot = {"agents": [{"provider": "codex", "name": "Codex", "state": "idle"}]}

        def fake_frame(provider, state, elapsed, size=144):
            shade = 40 if elapsed < 1 else 200
            return Image.new("RGBA", (size, size), (shade, 80, 160, 255))

        with patch("agent_dashboard.render.has_mascot", return_value=True), \
                patch("agent_dashboard.render.mascot_frame", side_effect=fake_frame):
            first = render(snapshot, datetime(2035, 1, 2, 12, 0).astimezone(), animation_time=0.0)
            next_second = render(snapshot, datetime(2035, 1, 2, 12, 0).astimezone(), animation_time=1.0)

        self.assertFalse(is_animated(snapshot["agents"][0]))
        self.assertIsNotNone(ImageChops.difference(first, next_second).getbbox())

    def test_mascot_header_states_fit_above_content_in_all_themes(self):
        snapshot = {
            "agents": [
                {"provider": "codex", "name": "Codex", "state": "working", "state_label": "工作中",
                 "active_tasks": 2, "active_tasks_known": True},
                {"provider": "zcode", "name": "ZCode", "state": "waiting", "state_label": "等待输入",
                 "active_tasks": 1, "active_tasks_known": True},
                {"provider": "antigravity", "name": "Antigravity", "state": "unknown",
                 "state_label": "已连接", "active_tasks_known": False, "stale": True},
            ]
        }
        requests = []

        def fake_frame(provider, state, elapsed, size=144):
            requests.append((provider, state, elapsed, size))
            tint = int((elapsed % 1.0) * 200) + 20
            return Image.new("RGBA", (size, size), (tint, 80, 160, 255))

        overlaps = lambda a, b: (a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1])
        with patch("agent_dashboard.render.has_mascot", return_value=True), \
                patch("agent_dashboard.render.mascot_frame", side_effect=fake_frame):
            for layout in ("classic", "aurora", "paper"):
                for font_scale in (90, 100, 115):
                    profile = {"id": layout, "layout": layout, "font_scale": font_scale}
                    first, manifest = render_scene(
                        snapshot, datetime(2035, 1, 2, 12, 0).astimezone(), profile, 0.0)
                    second = render(snapshot, datetime(2035, 1, 2, 12, 0).astimezone(), profile,
                                    animation_time=0.5)
                    self.assertIsNotNone(ImageChops.difference(first, second).getbbox(), (layout, font_scale))
                    nodes = {item["id"]: item for item in manifest}
                    for provider in ("codex", "zcode", "antigravity"):
                        indicator = nodes[f"agent.{provider}.status_indicator"]
                        badge = nodes[f"agent.{provider}.status_badge"]
                        caption = nodes[f"agent.{provider}.status_text"]
                        self.assertTrue(nodes[f"agent.{provider}.name"]["visible"])
                        self.assertTrue(nodes[f"agent.{provider}.brand"]["visible"])
                        self.assertEqual((indicator["bbox"][2] - indicator["bbox"][0],
                                          indicator["bbox"][3] - indicator["bbox"][1]), (144, 144))
                        self.assertEqual(indicator["bbox"][1], 76 + 7)
                        self.assertLessEqual(indicator["bbox"][3], 76 + 151)
                        self.assertEqual(badge["bbox"][1], 76 + 54)
                        self.assertLessEqual(badge["bbox"][3], 76 + 80)
                        self.assertTrue(caption["visible"])
                        self.assertGreaterEqual(caption["bbox"][1], badge["bbox"][1])
                        self.assertLessEqual(caption["bbox"][3], badge["bbox"][3])
                        header_ids = {"panel", "logo", "name", "brand", "status_badge",
                                      "status_indicator", "status_text", "stale_note"}
                        body_ids = {identity for identity in nodes
                                    if identity.startswith(f"agent.{provider}.")
                                    and identity[len(f"agent.{provider}."):] not in header_ids}
                        for identity in body_ids:
                            body_box = nodes[identity]["bbox"]
                            self.assertFalse(overlaps(body_box, indicator["bbox"]),
                                             (identity, layout, font_scale))
                        for header_suffix in ("logo", "name", "brand", "status_indicator", "status_text"):
                            header_box = nodes[f"agent.{provider}.{header_suffix}"]["bbox"]
                            for identity in body_ids:
                                self.assertFalse(overlaps(header_box, nodes[identity]["bbox"]),
                                                 (header_suffix, identity, layout, font_scale))
                        self.assertFalse(overlaps(nodes[f"agent.{provider}.task_label"]["bbox"],
                                                  nodes[f"agent.{provider}.task_count"]["bbox"]))
                        self.assertFalse(overlaps(nodes[f"agent.{provider}.plan_name"]["bbox"],
                                                  nodes[f"agent.{provider}.plan_spec"]["bbox"]))
                        self.assertFalse(overlaps(nodes[f"agent.{provider}.tokens_input"]["bbox"],
                                                  nodes[f"agent.{provider}.tokens_cached"]["bbox"]))
                        self.assertLessEqual(nodes[f"agent.{provider}.week_reset"]["bbox"][3], 450)
        self.assertIn(("codex", "working", 0.0, 144), requests)
        self.assertIn(("zcode", "idle", 0.0, 144), requests)
        self.assertIn(("antigravity", "idle", 0.0, 144), requests)

    def test_antigravity_explicit_known_task_count_can_be_busy(self):
        agent = {"provider": "antigravity", "state": "waiting", "active_tasks": 4,
                 "active_tasks_known": True, "subscription": {"quota_windows": [
                     {"known": True, "remaining_percent": 90 - index,
                      "reset_at": "2035-01-06T12:00:00Z"}
                     for index in range(8)
                 ]}}
        self.assertEqual(activity_state(agent), "busy")
        self.assertEqual(animation_period(agent), 0.65)

    def test_antigravity_uses_shared_windows_without_inventing_tasks_or_tokens(self):
        agent = self.sample_snapshot()["agents"][2]
        metrics = _agent_metrics(agent, "2035-01-02")
        self.assertIsNone(metrics["count"])
        self.assertIsNone(metrics["token_value"])
        self.assertEqual(metrics["date_label"], "今日 TOKENS")
        self.assertEqual(metrics["primary"]["remaining_percent"], 64)
        self.assertEqual([w["remaining_percent"] for w in metrics["windows"]], [80, 64])

    def test_model_rows_including_claude_and_gpt_never_change_shared_card(self):
        stamp = datetime(2035, 1, 2, 12, 0).astimezone()
        agent = self.sample_snapshot()["agents"][2]
        changed = deepcopy(agent)
        changed["subscription"]["quota_windows"] = [
            {"known": True, "model": name, "label": name, "remaining_percent": pct}
            for name, pct in (("Claude Demo", 1), ("GPT Demo", 0), ("Gemini Demo", 99))]
        for layout in ("classic", "aurora", "paper"):
            profile = {"id": layout, "layout": layout}
            first = render({"agents": [agent]}, stamp, profile, animation_time=0)
            second = render({"agents": [changed]}, stamp, profile, animation_time=0)
            self.assertIsNone(ImageChops.difference(first, second).getbbox(), layout)

    def test_aurora_all_plan_fields_remain_visible_in_common_layout(self):
        stamp = datetime(2035, 1, 2, 12, 0).astimezone()
        snapshot = self.sample_snapshot()
        profile = {"id": "aurora", "layout": "aurora"}
        original, manifest = render_scene(snapshot, stamp, profile, animation_time=0)
        nodes = {item["id"]: item for item in manifest}
        self.assertTrue(nodes["agent.antigravity.weekly_bar_reset"]["visible"])
        reset = nodes["agent.antigravity.weekly_bar_reset"]["bbox"]
        label = nodes["agent.antigravity.weekly_label"]["bbox"]
        percent = nodes["agent.antigravity.weekly_percent"]["bbox"]
        self.assertFalse(reset[0] < label[2] and reset[2] > label[0]
                         and reset[1] < label[3] and reset[3] > label[1])
        self.assertFalse(reset[0] < percent[2] and reset[2] > percent[0]
                         and reset[1] < percent[3] and reset[3] > percent[1])
        region = (1140, 220, 1630, 285)
        for field, value in (("plan_name", "Different synthetic plan"),
                             ("plan_spec", "Different synthetic tier"),
                             ("expires_at", "2036-04-05")):
            changed = deepcopy(snapshot)
            changed["agents"][2]["subscription"][field] = value
            modified = render(changed, stamp, profile, animation_time=0)
            self.assertIsNotNone(ImageChops.difference(original.crop(region), modified.crop(region)).getbbox(), field)

    def test_provider_logos_are_centered_on_name_ink(self):
        for provider, name in (("codex", "Codex"), ("zcode", "ZCode"), ("antigravity", "Antigravity")):
            for layout in ("classic", "aurora", "paper"):
                design = _resolve_design({"layout": layout})
                surface = design["colors"]["surface"]
                image = Image.new("RGB", (1920, 480), surface)
                draw = ImageDraw.Draw(image)
                data = {"provider": provider, "name": name, "state": "idle"}
                _draw_agent_header(image, draw, data, 24, 76, 528, design, 0)
                title_font = font(29 if provider == "antigravity" else 32, True)
                _, top, _, bottom = draw.textbbox((0, 86), name, font=title_font)
                region = (49, 82, 84, 129)
                pixels = image.crop(region)
                bbox = ImageChops.difference(pixels, Image.new("RGB", pixels.size, surface)).getbbox()
                self.assertIsNotNone(bbox)
                actual = region[1] + (bbox[1] + bbox[3]) / 2
                self.assertLessEqual(abs(actual - (top + bottom) / 2), 1.5, (provider, layout))

    def test_short_geometry_uses_smaller_portrait_and_keeps_metadata_and_footer_inside_card(self):
        snapshot = self.sample_snapshot()
        requests = []

        def fake_frame(provider, state, elapsed, size=144):
            requests.append((provider, size))
            return Image.new("RGBA", (size, size), (30, 120, 200, 255))

        profile = {"id": "short-card", "layout": "classic", "font_scale": 115,
                   "geometry": {"margin": 64, "gap": 32, "account_width": 320,
                                "top": 104, "bottom": 420}}
        with patch("agent_dashboard.render.has_mascot", return_value=True), \
                patch("agent_dashboard.render.mascot_frame", side_effect=fake_frame):
            _, manifest = render_scene(snapshot, datetime(2035, 1, 2, 12, 0).astimezone(), profile, 0)

        nodes = {item["id"]: item for item in manifest}
        card = nodes["agent.antigravity"]
        indicator = nodes["agent.antigravity.status_indicator"]
        self.assertEqual(card["bbox"], [1044, 104, 1502, 420])
        self.assertEqual((indicator["bbox"][2] - indicator["bbox"][0],
                          indicator["bbox"][3] - indicator["bbox"][1]), (112, 112))
        self.assertTrue(nodes["agent.antigravity.name"]["visible"])
        self.assertTrue(nodes["agent.antigravity.brand"]["visible"])
        for suffix in ("plan_label", "plan_name", "plan_spec", "plan_expiry", "weekly_label",
                       "weekly_bar", "tokens_label", "tokens_total", "tokens_input", "tokens_output",
                       "tokens_cached", "five_hour_label", "five_hour_reset", "week_label", "week_reset"):
            self.assertTrue(nodes[f"agent.antigravity.{suffix}"]["visible"], suffix)
        self.assertLessEqual(nodes["agent.antigravity.week_reset"]["bbox"][3], card["bbox"][3])
        self.assertGreaterEqual(nodes["agent.antigravity.week_reset"]["default_font_size"], 12)
        self.assertIn(("antigravity", 112), requests)

        body = nodes["agent.antigravity.task_label"]["bbox"]
        count = nodes["agent.antigravity.task_count"]["bbox"]
        self.assertFalse(body[0] < count[2] and body[2] > count[0]
                         and body[1] < count[3] and body[3] > count[1])
        for suffix in ("task_label", "task_count", "plan_label", "plan_name", "plan_spec", "plan_expiry",
                       "weekly_label", "weekly_bar", "tokens_label", "tokens_total", "tokens_input",
                       "tokens_output", "tokens_cached", "five_hour_label", "five_hour_reset",
                       "week_label", "week_reset"):
            box = nodes[f"agent.antigravity.{suffix}"]["bbox"]
            overlaps = (box[0] < indicator["bbox"][2] and box[2] > indicator["bbox"][0]
                        and box[1] < indicator["bbox"][3] and box[3] > indicator["bbox"][1])
            self.assertFalse(overlaps, suffix)

    def test_missing_mascot_frame_falls_back_to_animated_status_indicator(self):
        snapshot = {"agents": [{"provider": "codex", "name": "Codex", "state": "working"}]}
        with patch("agent_dashboard.render.has_mascot", return_value=True), \
                patch("agent_dashboard.render.mascot_frame", return_value=None):
            first, manifest = render_scene(snapshot, datetime(2035, 1, 2, 12, 0).astimezone(), animation_time=0)
            second = render(snapshot, datetime(2035, 1, 2, 12, 0).astimezone(), animation_time=0.5)
        nodes = {item["id"]: item for item in manifest}
        indicator = nodes["agent.codex.status_indicator"]
        self.assertEqual(indicator["kind"], "shape")
        self.assertTrue(nodes["agent.codex.status_text"]["visible"])
        self.assertEqual(nodes["agent.codex.status_badge"]["bbox"][1], 76 + 54)
        self.assertIsNotNone(ImageChops.difference(first, second).getbbox())

    def test_unreliable_count_does_not_trigger_busy(self):
        agent = {"provider": "zcode", "state": "working", "active_tasks": 8,
                 "active_tasks_known": False}
        self.assertEqual(activity_state(agent), "working")
        self.assertEqual(animation_period(agent), 2.0)

    def test_retained_last_known_busy_count_keeps_its_status_until_updated(self):
        agent = {"provider": "zcode", "state": "working", "active_tasks": 4,
                 "active_tasks_known": False, "retained_fields": ["active_tasks"], "stale": True}
        self.assertEqual(activity_state(agent), "busy")
        self.assertEqual(animation_period(agent), .65)
        self.assertTrue(agent["stale"])


if __name__ == "__main__":
    unittest.main()
