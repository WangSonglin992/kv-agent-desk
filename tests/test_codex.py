import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_dashboard.adapters.codex import CodexAdapter


SHANGHAI = timezone(timedelta(hours=8))


def stamp(value):
    return value.isoformat()


def event(at, kind, payload):
    return {"timestamp": stamp(at), "type": kind, "payload": payload}


def meta(at, session_id="session-1", **extra):
    payload = {
        "session_id": session_id,
        "id": session_id,
        "timestamp": stamp(at),
        "cwd": r"D:\projects\demo",
    }
    payload.update(extra)
    return event(at, "session_meta", payload)


def user_message(at, text):
    return event(
        at,
        "response_item",
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": f"## My request for Codex:\n{text}"}],
        },
    )


def usage_record(at, response_id, values, session_id="session-1", turn_id="turn-1"):
    usage = {
        "input_tokens": values[0],
        "output_tokens": values[1],
        "cached_input_tokens": values[2],
        "reasoning_output_tokens": values[3],
        "total_tokens": values[4],
        "cache_write_input_tokens": 0,
    }
    return event(
        at,
        "token_usage_record",
        {
            "response_id": response_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "root_turn_id": turn_id,
            "thread_id": session_id,
            "usage": usage,
            "turn_token_usage": usage,
            "thread_token_usage": usage,
        },
    )


def token_count(at, total, rate_limits=None, rate_limits_by_limit_id=None):
    payload = {
        "type": "token_count",
        "info": {
            "total_token_usage": {
                "input_tokens": total[0],
                "output_tokens": total[1],
                "cached_input_tokens": total[2],
                "reasoning_output_tokens": total[3],
                "total_tokens": total[4],
            },
            "last_token_usage": {},
            "model_context_window": 200000,
        },
        "rate_limits": rate_limits,
    }
    if rate_limits_by_limit_id is not None:
        payload["rate_limits_by_limit_id"] = rate_limits_by_limit_id
    return event(at, "event_msg", payload)


def write_rollout(root, now, name, records, day=None):
    day = day or now.date()
    folder = root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-{name}.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    os.utime(path, (now.timestamp(), now.timestamp()))
    return path


class CodexAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 15, 12, 0, tzinfo=SHANGHAI)

    def adapter_result(self, records, name="one", day=None):
        write_rollout(self.root, self.now, name, records, day)
        return CodexAdapter(self.root)._collect_at(self.now)

    def test_reads_current_turn_plan_and_usage_record_schema(self):
        records = [
            meta(self.now - timedelta(minutes=2)),
            event(self.now - timedelta(minutes=1), "event_msg", {"type": "task_started", "turn_id": "turn-1"}),
            user_message(self.now - timedelta(seconds=59), "修复仪表盘刷新"),
            event(
                self.now - timedelta(seconds=40),
                "event_msg",
                {
                    "type": "plan_update",
                    "explanation": None,
                    "plan": [
                        {"step": "检查日志", "status": "completed"},
                        {"step": "接入状态", "status": "in_progress"},
                        {"step": "验证显示", "status": "pending"},
                    ],
                },
            ),
            usage_record(self.now - timedelta(seconds=20), "response-a", (120, 30, 40, 10, 150)),
        ]
        result = self.adapter_result(records)
        self.assertEqual(result["provider"], "codex")
        self.assertEqual(result["observed_state"], "working")
        self.assertEqual(result["project"], "demo")
        self.assertEqual(result["task"], "修复仪表盘刷新")
        self.assertEqual(result["progress"]["completed"], 1)
        self.assertEqual(result["progress"]["total"], 3)
        self.assertTrue(result["progress"]["known"])
        self.assertEqual(result["tokens"]["input"], 120)
        self.assertEqual(result["tokens"]["cached"], 40)
        self.assertEqual(result["tokens"]["reasoning"], 10)
        self.assertEqual(result["tokens"]["total"], 150)
        self.assertTrue(result["tokens"]["known"])

    def test_duplicate_branch_response_ids_are_counted_once(self):
        rec = usage_record(self.now - timedelta(seconds=10), "shared-response", (100, 20, 30, 5, 120))
        first = [meta(self.now - timedelta(minutes=1), "root"), rec]
        second = [meta(self.now - timedelta(minutes=1), "fork", forked_from_id="root"), rec]
        write_rollout(self.root, self.now, "root", first)
        write_rollout(self.root, self.now, "fork", second)
        result = CodexAdapter(self.root)._collect_at(self.now)
        self.assertEqual(result["tokens"]["input"], 100)
        self.assertEqual(result["tokens"]["total"], 120)

    def test_legacy_cumulative_token_count_uses_cross_day_delta(self):
        yesterday = self.now - timedelta(days=1)
        records = [
            meta(yesterday.replace(hour=23), "legacy-session"),
            token_count(yesterday.replace(hour=23, minute=50), (900, 200, 300, 40, 1100)),
            token_count(self.now.replace(hour=11), (1000, 260, 330, 50, 1260)),
        ]
        result = self.adapter_result(records, day=yesterday.date())
        self.assertEqual(result["tokens"]["input"], 100)
        self.assertEqual(result["tokens"]["output"], 60)
        self.assertEqual(result["tokens"]["cached"], 30)
        self.assertEqual(result["tokens"]["reasoning"], 10)
        self.assertEqual(result["tokens"]["total"], 160)
        self.assertTrue(result["tokens"]["known"])

    def test_no_baseline_on_old_session_does_not_make_daily_zero(self):
        yesterday = self.now - timedelta(days=1)
        records = [
            meta(yesterday.replace(hour=10), "old-session"),
            token_count(self.now - timedelta(minutes=2), (900, 200, 300, 40, 1100)),
        ]
        result = self.adapter_result(records, day=yesterday.date())
        self.assertFalse(result["tokens"]["known"])
        self.assertIsNone(result["tokens"]["input"])

    def test_incomplete_final_line_is_ignored(self):
        path = write_rollout(
            self.root,
            self.now,
            "partial",
            [
                meta(self.now - timedelta(minutes=1)),
                event(self.now - timedelta(seconds=20), "event_msg", {"type": "task_started", "turn_id": "turn-1"}),
            ],
        )
        with path.open("ab") as stream:
            stream.write(b'{"timestamp":"2026-10-15T11:59:59+08:00","type":"event_msg","payload":')
        os.utime(path, (self.now.timestamp(), self.now.timestamp()))
        result = CodexAdapter(self.root)._collect_at(self.now)
        self.assertEqual(result["observed_state"], "working")
        self.assertEqual(result["session_id"], "session-1")

    def test_old_plan_is_cleared_for_new_task_and_stale_start_is_not_reported_fresh(self):
        old = self.now - timedelta(minutes=30)
        current = self.now - timedelta(minutes=20)
        records = [
            meta(old),
            event(old, "event_msg", {"type": "task_started", "turn_id": "old-turn"}),
            user_message(old + timedelta(seconds=1), "旧任务"),
            event(
                old + timedelta(seconds=2),
                "event_msg",
                {"type": "plan_update", "plan": [{"step": "旧步骤", "status": "completed"}]},
            ),
            event(old + timedelta(seconds=3), "event_msg", {"type": "task_complete", "turn_id": "old-turn"}),
            event(current, "event_msg", {"type": "task_started", "turn_id": "current-turn"}),
            user_message(current + timedelta(seconds=1), "新任务"),
        ]
        result = self.adapter_result(records)
        self.assertEqual(result["task"], "新任务")
        self.assertFalse(result["progress"]["known"])
        self.assertEqual(result["observed_state"], "working")
        self.assertTrue(result["stale"])
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["active_tasks"])
        self.assertFalse(result["active_tasks_known"])

    def test_active_task_count_uses_fresh_root_sessions_and_ignores_children(self):
        at = self.now - timedelta(seconds=5)
        root_one = [
            meta(at, "root-one"),
            event(at, "event_msg", {"type": "task_started", "turn_id": "turn-one"}),
        ]
        root_two = [
            meta(at, "root-two"),
            event(at, "event_msg", {"type": "task_started", "turn_id": "turn-two"}),
        ]
        duplicate_root = [
            meta(at, "root-one"),
            event(at, "event_msg", {"type": "task_started", "turn_id": "turn-one"}),
        ]
        child = [
            meta(at, "child", parent_thread_id="root-one", thread_source="subagent"),
            event(at, "event_msg", {"type": "task_started", "turn_id": "child-turn"}),
        ]
        write_rollout(self.root, self.now, "root-one", root_one)
        write_rollout(self.root, self.now, "root-two", root_two)
        write_rollout(self.root, self.now, "duplicate", duplicate_root)
        write_rollout(self.root, self.now, "child", child)
        result = CodexAdapter(self.root)._collect_at(self.now)
        self.assertEqual(result["active_tasks"], 2)
        self.assertTrue(result["active_tasks_known"])

    def test_tail_scan_preserves_child_metadata_from_bounded_header(self):
        at = self.now - timedelta(seconds=5)
        records = [
            meta(self.now - timedelta(seconds=20), "child-thread", parent_thread_id="parent-thread"),
            event(at - timedelta(seconds=2), "event_msg", {"type": "warning", "message": "x" * 500}),
            event(at - timedelta(seconds=1), "event_msg", {"type": "warning", "message": "y" * 500}),
            event(at, "event_msg", {"type": "task_started", "turn_id": "child-turn"}),
        ]
        path = write_rollout(self.root, self.now, "large-child", records)
        adapter = CodexAdapter(self.root)
        adapter.MAX_INITIAL_BYTES = 256
        parsed = adapter._scan_file(path, path.stat(), self.now.replace(hour=0, minute=0, second=0, microsecond=0), self.now)
        self.assertIsNotNone(parsed)
        summary = adapter._summarize_file(parsed)
        self.assertEqual(summary["thread_id"], "child-thread")
        self.assertTrue(summary["is_child"])
        self.assertEqual(summary["observed_state"], "working")

    def test_stale_explicit_idle_session_does_not_hide_fresh_working_count(self):
        old = self.now - timedelta(hours=1)
        fresh = self.now - timedelta(seconds=10)
        old_session = [
            meta(old, "old-thread"),
            event(old + timedelta(seconds=1), "event_msg", {"type": "task_started", "turn_id": "old-turn"}),
            event(old + timedelta(seconds=2), "event_msg", {"type": "task_complete", "turn_id": "old-turn"}),
        ]
        current_session = [
            meta(fresh, "fresh-thread"),
            event(self.now - timedelta(seconds=5), "event_msg", {"type": "task_started", "turn_id": "fresh-turn"}),
        ]
        write_rollout(self.root, self.now, "old-idle", old_session)
        write_rollout(self.root, self.now, "fresh-working", current_session)
        result = CodexAdapter(self.root)._collect_at(self.now)
        self.assertEqual(result["active_tasks"], 1)
        self.assertTrue(result["active_tasks_known"])

    def test_only_codex_buckets_are_used_for_weekly_and_five_hour(self):
        weekly_reset = int((self.now + timedelta(days=4)).timestamp())
        rate_limits = {
            "limit_id": "codex",
            "plan_type": "pro",
            "primary": {"window_minutes": 10080, "used_percent": 32.0, "resets_at": weekly_reset},
            "secondary": {"window_minutes": 300, "used_percent": 90.0, "resets_at": weekly_reset},
        }
        by_id = {
            "other_provider": {
                "limit_id": "other_provider",
                "primary": {"window_minutes": 10080, "used_percent": 99.0, "resets_at": weekly_reset},
            }
        }
        newest_codex_by_id = {
            "codex": {
                "primary": {"window_minutes": 10080, "used_percent": 25.0, "resets_at": weekly_reset},
            }
        }
        newer_other_bucket = {
            "limit_id": "api",
            "primary": {"window_minutes": 10080, "used_percent": 0.0, "resets_at": weekly_reset},
        }
        records = [
            meta(self.now - timedelta(minutes=1)),
            token_count(self.now - timedelta(seconds=30), (0, 0, 0, 0, 0), rate_limits, by_id),
            token_count(
                self.now - timedelta(seconds=10),
                (0, 0, 0, 0, 0),
                newer_other_bucket,
                newest_codex_by_id,
            ),
        ]
        result = self.adapter_result(records)
        subscription = result["subscription"]
        self.assertEqual(subscription["five_hour"]["remaining_percent"], 10.0)
        self.assertTrue(subscription["five_hour"]["known"])
        self.assertFalse(subscription["five_hour"].get("unlimited", False))
        self.assertEqual(subscription["weekly"]["remaining_percent"], 75.0)
        self.assertEqual(subscription["weekly"]["reset_at"], (self.now + timedelta(days=4)).isoformat())
        self.assertTrue(subscription["weekly"]["known"])


if __name__ == "__main__":
    unittest.main()
