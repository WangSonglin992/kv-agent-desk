from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from agent_dashboard.retention import LastGoodStore


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 15, 12, tzinfo=timezone(timedelta(hours=8)))
        self.good = {"provider": "codex", "name": "Codex", "available": True,
                     "active_tasks": 3,
                     "state": "working", "state_label": "工作中", "task": "实现适配器",
                     "project": "demo", "updated_at": self.now.isoformat(),
                     "progress": {"known": True, "completed": 2, "total": 4},
                     "tokens": {"known": True, "total": 3200, "input": 3000, "output": 200}}

    def test_failure_retains_fields_and_sets_quiet_hint(self):
        store = LastGoodStore()
        store.merge(self.good, self.now)
        failed = {"provider": "codex", "name": "Codex", "available": False,
                  "state": "offline", "progress": {"known": False}, "tokens": {"known": False}}
        merged = store.merge(failed, self.now + timedelta(minutes=20))
        self.assertEqual(merged["state"], "working")
        self.assertEqual(merged["tokens"]["total"], 3200)
        self.assertEqual(merged["progress"]["completed"], 2)
        self.assertEqual(merged["active_tasks"], 3)
        self.assertEqual(merged["freshness_note"], "数据待检查更新")

    def test_restart_and_day_boundary_preserve_original_token_date(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "last-good.json"
            store = LastGoodStore(path)
            store.merge(self.good, self.now)
            store.save()
            store = LastGoodStore(path)
            merged = store.merge({"provider": "codex", "state": "unknown"}, self.now + timedelta(days=1))
            self.assertEqual(merged["tokens"]["date"], "2026-10-15")
            self.assertEqual(merged["tokens"]["total"], 3200)

    def test_real_completion_replaces_stale_running_state(self):
        store = LastGoodStore()
        store.merge(self.good, self.now)
        new = dict(self.good, state="idle", state_label="空闲")
        self.assertEqual(store.merge(new, self.now)["state"], "idle")

    def test_old_event_is_shown_with_hint_on_first_start(self):
        store = LastGoodStore()
        data = dict(self.good, state="unknown", observed_state="working", stale=True)
        merged = store.merge(data, self.now + timedelta(hours=2))
        self.assertEqual(merged["state"], "working")
        self.assertTrue(merged["stale"])

    def test_unknown_quota_retains_observed_windows(self):
        store = LastGoodStore()
        a = dict(self.good, subscription={"name": "ChatGPT", "five_hour": {"known": True, "remaining_percent": 72}, "weekly": {"known": True, "remaining_percent": 53,
                                "reset_at": "2026-10-20T16:35:02+08:00"}})
        store.merge(a, self.now)
        result = store.merge(dict(self.good, subscription={"weekly": {"known": False}}), self.now)
        self.assertEqual(result["subscription"]["weekly"]["remaining_percent"], 53)
        self.assertEqual(result["subscription"]["five_hour"]["remaining_percent"], 72)
        self.assertFalse(result["subscription"]["five_hour"].get("unlimited", False))

    def test_older_known_snapshot_does_not_replace_newer_good_values(self):
        store = LastGoodStore()
        store.merge(self.good, self.now)
        old = dict(self.good, state="idle", state_label="空闲", task="旧任务",
                   updated_at=(self.now - timedelta(hours=1)).isoformat(),
                   tokens={"known": True, "total": 100, "date": "2026-10-15"},
                   progress={"known": True, "completed": 0, "total": 4})
        result = store.merge(old, self.now)
        self.assertEqual(result["state"], "working")
        self.assertEqual(result["task"], "实现适配器")
        self.assertEqual(result["tokens"]["total"], 3200)
        self.assertEqual(result["progress"]["completed"], 2)
        self.assertTrue(result["stale"])

    def test_last_automatic_subscription_metadata_survives_missing_fields(self):
        store = LastGoodStore()
        metadata = {"plan_name": "ChatGPT Pro", "plan_spec": "官方规格", "expires_at": "2026-11-15"}
        store.merge(dict(self.good, subscription=metadata), self.now)
        merged = store.merge(self.good, self.now)
        self.assertEqual(merged["subscription"]["plan_name"], "ChatGPT Pro")
        self.assertEqual(merged["subscription"]["expires_at"], "2026-11-15")
        self.assertIn("expires_at", merged["subscription"]["metadata_retained"])

    def test_antigravity_model_quotas_are_retained_without_inventing_a_week_window(self):
        store = LastGoodStore()
        sample = dict(self.good, provider="antigravity", name="Antigravity",
                      subscription={"quota_windows": [{"label": "Gemini Pro", "model": "gemini-pro", "known": True,
                                                       "remaining_percent": 60, "reset_at": "2026-10-15T16:00:00+08:00"}]})
        store.merge(sample, self.now)
        result = store.merge(dict(self.good, provider="antigravity"), self.now)
        self.assertEqual(result["subscription"]["quota_windows"][0]["remaining_percent"], 60)
        self.assertNotIn("weekly", result["subscription"])


if __name__ == "__main__":
    unittest.main()
