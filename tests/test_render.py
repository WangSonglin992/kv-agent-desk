import unittest
from copy import deepcopy
from datetime import datetime
from PIL import Image, ImageChops, ImageDraw
from agent_dashboard.render import (
    activity_state,
    _agent_metrics,
    _draw_agent_header,
    _resolve_design,
    font,
    animation_period,
    expiry_text,
    is_animated,
    logo,
    number,
    render,
)


class RenderTests(unittest.TestCase):
    def test_unknown_and_long_texts_render_without_size_change(self):
        agents = [{"provider": key, "name": name, "state": "unknown",
                   "project": "D:/项目/当前任务", "task": "非常长的中文任务" * 100,
                   "progress": {"known": False}, "tokens": {"known": False}}
                  for key, name in [("codex", "Codex"), ("zcode", "ZCode")]]
        self.assertEqual(render({"agents": agents}).size, (1920, 480))

    def test_cached_is_never_added_to_total_by_renderer(self):
        self.assertEqual(number(None), "—")
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
        busy_red = (0xF0, 0x64, 0x69)
        normal_badge = normal_card.crop((400, 95, 530, 140))
        busy_badge = busy_card.crop((400, 95, 530, 140))
        self.assertNotIn(busy_red, normal_badge.get_flattened_data())
        self.assertIn(busy_red, busy_badge.get_flattened_data())

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
        original = render(snapshot, stamp, profile, animation_time=0)
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
                _, top, _, bottom = draw.textbbox((0, 93), name, font=title_font)
                region = (49, 82, 84, 146)
                pixels = image.crop(region)
                bbox = ImageChops.difference(pixels, Image.new("RGB", pixels.size, surface)).getbbox()
                self.assertIsNotNone(bbox)
                actual = region[1] + (bbox[1] + bbox[3]) / 2
                self.assertLessEqual(abs(actual - (top + bottom) / 2), 1.5, (provider, layout))

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
