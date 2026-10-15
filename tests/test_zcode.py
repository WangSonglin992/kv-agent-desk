import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_dashboard.adapters.zcode import ZCodeAdapter


SCHEMA = """
CREATE TABLE session (
    id TEXT, directory TEXT, title TEXT, time_updated INTEGER
);
CREATE TABLE todo (
    session_id TEXT, content TEXT, status TEXT, position INTEGER,
    time_created INTEGER, time_updated INTEGER
);
CREATE TABLE tool_usage (
    session_id TEXT, turn_id TEXT, tool_name TEXT, status TEXT, approval_status TEXT,
    started_at INTEGER, first_output_at INTEGER, completed_at INTEGER
);
CREATE TABLE turn_usage (
    session_id TEXT, turn_id TEXT, user_message_id TEXT, status TEXT, started_at INTEGER,
    first_token_at INTEGER, completed_at INTEGER
);
CREATE TABLE session_input (
    session_id TEXT, promoted_message_id TEXT, status TEXT, time_created INTEGER, time_updated INTEGER
);
CREATE TABLE model_usage (
    id TEXT, logical_request_id TEXT, attempt_index INTEGER, session_id TEXT, turn_id TEXT,
    parent_user_message_id TEXT, status TEXT, started_at INTEGER, first_token_at INTEGER,
    completed_at INTEGER, input_tokens INTEGER, output_tokens INTEGER,
    reasoning_tokens INTEGER, cache_creation_input_tokens INTEGER,
    cache_read_input_tokens INTEGER, computed_total_tokens INTEGER
);
"""


def make_database(root: Path, stamp_ms: int) -> Path:
    path = root / "cli" / "db" / "db.sqlite"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT INTO session VALUES (?, ?, ?, ?)",
        ("s1", r"D:\workspace\demo", "Build the dashboard", stamp_ms),
    )
    connection.executemany(
        "INSERT INTO todo VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("s1", "Inspect local source", "completed", 0, stamp_ms, stamp_ms),
            ("s1", "Implement adapter", "pending", 1, stamp_ms, stamp_ms),
        ],
    )
    connection.execute(
        "INSERT INTO tool_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("s1", "t1", "Bash", "running", "none", stamp_ms, stamp_ms, None),
    )
    connection.executemany(
        "INSERT INTO model_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("m1", "req1", 0, "s1", "t1", None, "completed", stamp_ms, stamp_ms, stamp_ms, 10, 4, 1, 2, 3, 14),
            ("m2", "req1", 1, "s1", "t1", None, "completed", stamp_ms + 1, stamp_ms + 1, stamp_ms + 1, 20, 5, 2, 1, 4, 25),
            ("m3", "req2", 0, "s1", "t1", None, "error", stamp_ms + 2, stamp_ms + 2, stamp_ms + 2, 100, 30, 0, 0, 0, 130),
        ],
    )
    connection.commit()
    connection.close()
    return path


class ZCodeAdapterTests(unittest.TestCase):
    def test_missing_database_is_explicitly_offline_and_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            result = ZCodeAdapter(temp).collect()

        self.assertFalse(result["available"])
        self.assertEqual(result["state"], "offline")
        self.assertEqual(result["observed_state"], "unknown")
        self.assertTrue(result["stale"])
        self.assertFalse(result["tokens"]["known"])
        self.assertFalse(result["progress"]["known"])
        self.assertFalse(result["subscription"]["five_hour"]["known"])
        self.assertFalse(result["subscription"]["weekly"]["known"])

    def test_reads_progress_and_deduplicated_request_tokens_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_database(root, int(time.time() * 1000))
            adapter = ZCodeAdapter(root)
            with patch.object(adapter, "_detect_zcode_process", return_value=True):
                result = adapter.collect()
            connection = adapter._open_readonly(Path(result["source"]))
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            connection.close()

        self.assertTrue(result["available"])
        self.assertEqual(result["state"], "working")
        self.assertEqual(result["observed_state"], "working")
        self.assertFalse(result["stale"])
        self.assertEqual(result["session_id"], "s1")
        self.assertEqual(result["project"], r"D:\workspace\demo")
        self.assertEqual(result["progress"]["completed"], 1)
        self.assertEqual(result["progress"]["total"], 2)
        self.assertTrue(result["progress"]["known"])
        self.assertEqual(result["tokens"]["input"], 20)
        self.assertEqual(result["tokens"]["output"], 5)
        self.assertEqual(result["tokens"]["reasoning"], 2)
        self.assertEqual(result["tokens"]["cached"], 5)
        self.assertEqual(result["tokens"]["total"], 25)
        self.assertEqual(result["tokens"]["scope"], "today")
        self.assertEqual(result["tokens"]["date"], time.strftime("%Y-%m-%d"))
        self.assertTrue(result["tokens"]["known"])
        self.assertEqual(result["active_tasks"], 1)
        self.assertEqual(result["subscription"]["name"], "ZCode")
        self.assertIsNone(result["subscription"]["five_hour"]["remaining_percent"])
        self.assertIsNone(result["subscription"]["five_hour"]["reset_at"])
        self.assertFalse(result["subscription"]["five_hour"]["known"])
        self.assertFalse(result["subscription"]["weekly"]["known"])
        self.assertIn("setting.json", result["subscription"]["source"])

    def test_old_running_record_preserves_observation_but_marks_state_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = int((time.time() - 60 * 60) * 1000)
            make_database(root, old)
            adapter = ZCodeAdapter(root)
            with patch.object(adapter, "_detect_zcode_process", return_value=True):
                result = adapter.collect()

        self.assertTrue(result["available"])
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["observed_state"], "working")
        self.assertTrue(result["stale"])

    def test_offline_process_keeps_last_observed_local_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_database(root, int(time.time() * 1000))
            adapter = ZCodeAdapter(root)
            with patch.object(adapter, "_detect_zcode_process", return_value=False):
                result = adapter.collect()

        self.assertTrue(result["available"])
        self.assertEqual(result["state"], "offline")
        self.assertEqual(result["observed_state"], "working")
        self.assertTrue(result["stale"])

    def test_newer_completed_turn_overrides_old_running_and_waiting_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stamp = int(time.time() * 1000)
            db = make_database(root, stamp)
            connection = sqlite3.connect(db)
            connection.execute(
                "UPDATE tool_usage SET started_at=?, turn_id=? WHERE status='running'",
                (stamp - 2_000, "t-old"),
            )
            connection.execute(
                "INSERT INTO tool_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("s1", "t-wait", "Bash", "waiting", "none", stamp - 3_000, None, None),
            )
            connection.execute(
                "INSERT INTO turn_usage VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("s1", "t-new", "msg-new", "completed", stamp - 1_000, stamp - 500, stamp),
            )
            connection.commit()
            connection.close()

            adapter = ZCodeAdapter(root)
            with patch.object(adapter, "_detect_zcode_process", return_value=True):
                result = adapter.collect()

        self.assertEqual(result["observed_state"], "idle")
        self.assertEqual(result["state"], "idle")
        self.assertFalse(result["stale"])
        self.assertEqual(result["active_tasks"], 0)

    def test_active_task_count_uses_fresh_session_lifecycle_not_pending_todos(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now_ms = int(time.time() * 1000)
            db = make_database(root, now_ms)
            connection = sqlite3.connect(db)
            connection.executemany(
                "INSERT INTO session VALUES (?, ?, ?, ?)",
                [
                    ("s2", r"D:\workspace\finished", "Finished", now_ms - 10),
                    ("s3", r"D:\workspace\old", "Old run", now_ms - 20 * 60 * 1000),
                    ("s4", r"D:\workspace\pending", "Pending plan", now_ms - 20),
                ],
            )
            connection.executemany(
                "INSERT INTO tool_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("s2", "t2", "Bash", "running", "none", now_ms - 1_000, now_ms - 500, None),
                    ("s3", "t3", "Bash", "running", "none", now_ms - 20 * 60 * 1000, None, None),
                ],
            )
            connection.execute(
                "INSERT INTO turn_usage VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("s2", "t2", "msg-2", "completed", now_ms - 2_000, now_ms - 1_000, now_ms),
            )
            connection.execute(
                "INSERT INTO todo VALUES (?, ?, ?, ?, ?, ?)",
                ("s4", "Pending but not active", "pending", 0, now_ms, now_ms),
            )
            connection.commit()
            connection.close()

            adapter = ZCodeAdapter(root)
            with patch.object(adapter, "_detect_zcode_process", return_value=True):
                result = adapter.collect()

        self.assertEqual(result["active_tasks"], 1)

    def test_long_running_turn_uses_recent_linked_model_activity_for_freshness(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now_ms = int(time.time() * 1000)
            old_ms = now_ms - 45 * 60 * 1000
            db = make_database(root, old_ms)
            connection = sqlite3.connect(db)
            connection.execute(
                "UPDATE tool_usage SET turn_id=?, started_at=?, first_output_at=NULL, completed_at=NULL",
                ("t-long", old_ms),
            )
            connection.execute(
                "INSERT INTO turn_usage VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("s1", "t-long", "msg-long", "running", old_ms, old_ms + 1_000, None),
            )
            connection.execute(
                "INSERT INTO session_input VALUES (?, ?, ?, ?, ?)",
                ("s1", "msg-long", "promoted", old_ms, old_ms),
            )
            connection.execute(
                "UPDATE model_usage SET parent_user_message_id=?, started_at=?, first_token_at=?, completed_at=? "
                "WHERE id='m2'",
                ("msg-long", now_ms - 20_000, now_ms - 10_000, now_ms - 5_000),
            )
            connection.commit()
            connection.close()

            adapter = ZCodeAdapter(root)
            with patch.object(adapter, "_detect_zcode_process", return_value=True):
                result = adapter.collect()

        self.assertEqual(result["state"], "working")
        self.assertEqual(result["observed_state"], "working")
        self.assertFalse(result["stale"])
        self.assertEqual(result["active_tasks"], 1)
        self.assertGreaterEqual(time.time() - __import__("datetime").datetime.fromisoformat(result["updated_at"]).timestamp(), 0)

    def test_completed_tool_does_not_end_an_explicitly_running_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now_ms = int(time.time() * 1000)
            db = make_database(root, now_ms)
            connection = sqlite3.connect(db)
            connection.execute(
                "UPDATE tool_usage SET status='completed', started_at=?, first_output_at=?, completed_at=?",
                (now_ms - 5_000, now_ms - 1_000, now_ms),
            )
            connection.execute(
                "INSERT INTO turn_usage VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("s1", "t1", "msg-still-running", "running", now_ms - 8 * 60 * 1000, now_ms - 7 * 60 * 1000, None),
            )
            connection.commit()
            connection.close()

            adapter = ZCodeAdapter(root)
            with patch.object(adapter, "_detect_zcode_process", return_value=True):
                result = adapter.collect()

        self.assertEqual(result["observed_state"], "working")
        self.assertEqual(result["state"], "working")
        self.assertFalse(result["stale"])
        self.assertEqual(result["active_tasks"], 1)

    def test_promoted_input_and_linked_recent_model_bridge_completed_call_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now_ms = int(time.time() * 1000)
            db = make_database(root, now_ms)
            connection = sqlite3.connect(db)
            connection.execute(
                "UPDATE tool_usage SET status='completed', started_at=?, first_output_at=?, completed_at=?",
                (now_ms - 5_000, now_ms - 1_000, now_ms),
            )
            connection.execute(
                "UPDATE model_usage SET parent_user_message_id=?, started_at=?, first_token_at=?, completed_at=? "
                "WHERE id='m2'",
                ("msg-gap", now_ms - 10_000, now_ms - 8_000, now_ms - 2_000),
            )
            connection.execute(
                "INSERT INTO session_input VALUES (?, ?, ?, ?, ?)",
                ("s1", "msg-gap", "promoted", now_ms - 20_000, now_ms - 20_000),
            )
            connection.commit()
            connection.close()

            adapter = ZCodeAdapter(root)
            with patch.object(adapter, "_detect_zcode_process", return_value=True):
                result = adapter.collect()

        self.assertEqual(result["observed_state"], "working")
        self.assertEqual(result["state"], "working")
        self.assertFalse(result["stale"])
        self.assertEqual(result["active_tasks"], 1)

    def test_multi_session_selection_finds_fresh_work_with_old_session_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now_ms = int(time.time() * 1000)
            db = make_database(root, now_ms)
            connection = sqlite3.connect(db)
            connection.execute(
                "UPDATE tool_usage SET status='completed', first_output_at=?, completed_at=?",
                (now_ms - 500, now_ms),
            )
            connection.execute(
                "INSERT INTO turn_usage VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("s1", "t1", "msg-old", "completed", now_ms - 2_000, now_ms - 1_000, now_ms),
            )
            connection.execute(
                "INSERT INTO session VALUES (?, ?, ?, ?)",
                ("s2", r"D:\workspace\background", "Background", now_ms - 37 * 60 * 60 * 1000),
            )
            connection.execute(
                "INSERT INTO tool_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("s2", "t2", "Bash", "running", "none", now_ms - 5_000, now_ms - 1_000, None),
            )
            connection.execute(
                "INSERT INTO turn_usage VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("s2", "t2", "msg-new", "running", now_ms - 7_000, now_ms - 6_000, None),
            )
            connection.commit()
            connection.close()

            adapter = ZCodeAdapter(root)
            with patch.object(adapter, "_detect_zcode_process", return_value=True):
                result = adapter.collect()

        self.assertEqual(result["session_id"], "s2")
        self.assertEqual(result["state"], "working")
        self.assertEqual(result["observed_state"], "working")
        self.assertEqual(result["active_tasks"], 1)

    def test_promoted_work_becomes_unknown_and_exits_count_after_activity_goes_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now_ms = int(time.time() * 1000)
            old_ms = now_ms - 20 * 60 * 1000
            db = make_database(root, old_ms)
            connection = sqlite3.connect(db)
            connection.execute(
                "UPDATE tool_usage SET status='completed', started_at=?, first_output_at=?, completed_at=?",
                (old_ms - 5_000, old_ms - 1_000, old_ms),
            )
            connection.execute(
                "UPDATE model_usage SET parent_user_message_id=?, started_at=?, first_token_at=?, completed_at=? "
                "WHERE id='m2'",
                ("msg-old", old_ms - 10_000, old_ms - 8_000, old_ms - 2_000),
            )
            connection.execute(
                "INSERT INTO session_input VALUES (?, ?, ?, ?, ?)",
                ("s1", "msg-old", "promoted", old_ms - 20_000, old_ms - 20_000),
            )
            connection.commit()
            connection.close()

            adapter = ZCodeAdapter(root)
            with patch.object(adapter, "_detect_zcode_process", return_value=True):
                result = adapter.collect()

        self.assertEqual(result["observed_state"], "working")
        self.assertEqual(result["state"], "unknown")
        self.assertTrue(result["stale"])
        self.assertEqual(result["active_tasks"], 0)

    def test_unrecognized_schema_returns_unknown_without_guessing(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "cli" / "db" / "db.sqlite"
            db.parent.mkdir(parents=True)
            sqlite3.connect(db).close()
            result = ZCodeAdapter(temp).collect()

        self.assertFalse(result["available"])
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["observed_state"], "unknown")
        self.assertFalse(result["tokens"]["known"])


if __name__ == "__main__":
    unittest.main()
