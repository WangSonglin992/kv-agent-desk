import unittest
from copy import deepcopy
from datetime import datetime
from agent_dashboard.render import render, number, expiry_text


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

    def test_task_names_and_plan_do_not_appear_on_screen(self):
        stamp = datetime(2026, 10, 15, 12, 0).astimezone()
        data = {"agents": [{"provider": "codex", "name": "Codex", "state": "working", "active_tasks": 2,
                            "tokens": {"known": False}, "task": "private task A",
                            "progress": {"known": True, "completed": 1, "total": 10}}]}
        changed = deepcopy(data)
        changed["agents"][0]["task"] = "different private task B"
        changed["agents"][0]["progress"]["completed"] = 9
        self.assertEqual(render(data, stamp).tobytes(), render(changed, stamp).tobytes())

    def test_expiry_preserves_date_precision_and_rejects_invalid_dates(self):
        self.assertEqual(expiry_text("2026-11-15"), "到期 2026/11/15")
        self.assertEqual(expiry_text(None), "到期待填写")
        self.assertEqual(expiry_text("invalid-date"), "到期待填写")


if __name__ == "__main__":
    unittest.main()
