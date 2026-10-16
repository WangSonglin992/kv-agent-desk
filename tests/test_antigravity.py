import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_dashboard.adapters.antigravity import (
    AntigravityAdapter,
    _ACTIVITY_RPC_PATH,
    _RPC_PATH,
    _SUMMARY_RPC_PATH,
)
from agent_dashboard.retention import LastGoodStore


RESET = "2035-02-07T10:00:00Z"


def status_payload():
    return {
        "userStatus": {
            # Personal profile fields intentionally exist in the server response;
            # the adapter must not copy them into its public snapshot.
            "email": "private@example.invalid",
            "planStatus": {"planInfo": {"planName": "Pro"}},
            "cascadeModelConfigData": {
                "clientModelConfigs": [
                    {
                        "label": "Gemini 3.8 Flash (Medium)",
                        "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M319"},
                        "quotaInfo": {"remainingFraction": 1, "resetTime": RESET},
                    },
                    {
                        "label": "Gemini 4 Argon",
                        "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M421"},
                        "quotaInfo": {},
                    },
                    {
                        "label": "Gemini 3.7 Flash exhausted",
                        "modelOrAlias": {"model": "MODEL_GEMINI_EXHAUSTED"},
                        "quotaInfo": {"resetTime": RESET},
                    },
                    {
                        "label": "Gemini 3.1 Pro invalid reset",
                        "modelOrAlias": {"model": "MODEL_GEMINI_INVALID"},
                        "quotaInfo": {"resetTime": "not-an-iso-time"},
                    },
                    {
                        "label": "Claude Opus excluded",
                        "modelOrAlias": {"model": "MODEL_OPUS_EXCLUDED"},
                        "quotaInfo": {"remainingFraction": 1, "resetTime": RESET},
                    },
                    {
                        "label": "GPT-OSS excluded",
                        "modelOrAlias": {"model": "MODEL_GPT_EXCLUDED"},
                        "quotaInfo": {"remainingFraction": 1, "resetTime": RESET},
                    },
                ]
            },
        }
    }


def quota_summary_payload():
    return {
        "response": {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {"bucketId": "gemini-weekly", "displayName": "Weekly Limit", "window": "weekly",
                         "remainingFraction": 0.58, "resetTime": "2035-02-14T10:00:00Z"},
                        {"bucketId": "gemini-5h", "displayName": "Five Hour Limit", "window": "5h",
                         "remainingFraction": 0.82, "resetTime": RESET},
                    ],
                },
                {
                    "displayName": "Claude and GPT models",
                    "buckets": [
                        {"bucketId": "3p-weekly", "displayName": "Weekly Limit", "window": "weekly",
                         "remainingFraction": 1, "resetTime": "2035-02-14T10:00:00Z"},
                        {"bucketId": "3p-5h", "displayName": "Five Hour Limit", "window": "5h",
                         "remainingFraction": 1, "resetTime": RESET},
                    ],
                },
            ]
        }
    }


def activity_payload(rows):
    return {"trajectorySummaries": rows}


def activity_result(count):
    state = "working" if count else "idle"
    return {"state": state, "observed_state": state,
            "state_label": "工作中" if count else "空闲",
            "active_tasks": count, "active_tasks_known": True}


class AntigravityAdapterTests(unittest.TestCase):
    def test_activity_counts_running_canceling_and_busy_but_not_idle(self):
        statuses = ((1, 0), (2, 1), (3, 1), (4, 1), ("CASCADE_RUN_STATUS_BUSY", 1))
        for status, expected_count in statuses:
            with self.subTest(status=status), patch.object(
                    AntigravityAdapter, "_fetch_local_rpc",
                    return_value=activity_payload({"synthetic-id": {"status": status}})):
                result = AntigravityAdapter._fetch_activity([{"csrf": "synthetic", "ports": [61234]}])
            self.assertEqual(result["active_tasks"], expected_count)
            self.assertEqual(result["active_tasks_known"], True)
            self.assertEqual(result["state"], "working" if expected_count else "idle")

    def test_activity_deduplicates_same_id_across_servers_and_rejects_conflicts(self):
        servers = [{"csrf": "synthetic-a", "ports": [61234]},
                   {"csrf": "synthetic-b", "ports": [61235]}]
        running = activity_payload({"synthetic-trajectory": {"status": 2}})
        with patch.object(AntigravityAdapter, "_fetch_local_rpc", side_effect=[running, running]):
            result = AntigravityAdapter._fetch_activity(servers)
        self.assertEqual(result["active_tasks"], 1)

        conflicting = activity_payload({"synthetic-trajectory": {"status": 1}})
        with patch.object(AntigravityAdapter, "_fetch_local_rpc", side_effect=[running, conflicting]):
            with self.assertRaisesRegex(ValueError, "conflicting activity summaries"):
                AntigravityAdapter._fetch_activity(servers)

    def test_unknown_activity_status_is_not_reported_as_zero(self):
        payload = activity_payload({"synthetic-trajectory": {"status": 99}})
        with patch.object(AntigravityAdapter, "_fetch_local_rpc", return_value=payload):
            with self.assertRaisesRegex(ValueError, "unknown activity status"):
                AntigravityAdapter._fetch_activity([{"csrf": "synthetic", "ports": [61234]}])

    def test_activity_rpc_excludes_subtrajectories_and_discards_private_fields(self):
        payload = activity_payload({"synthetic-trajectory": {
            "status": 4, "title": "synthetic private title", "prompt": "synthetic private prompt",
            "workspace": "synthetic/private/path",
        }})
        with patch.object(AntigravityAdapter, "_fetch_local_rpc", return_value=payload) as rpc:
            result = AntigravityAdapter._fetch_activity([{"csrf": "synthetic", "ports": [61234]}])

        args, _kwargs = rpc.call_args
        self.assertEqual(args[1], _ACTIVITY_RPC_PATH)
        self.assertEqual(json.loads(args[2]), {"excludeSubtrajectories": True})
        self.assertEqual(set(result), {"state", "observed_state", "state_label", "active_tasks", "active_tasks_known"})
        self.assertEqual(result["active_tasks"], 1)
        self.assertNotIn("synthetic private", json.dumps(result))
        self.assertNotIn("synthetic/private", json.dumps(result))

    def test_maps_legacy_per_model_quota_without_guessing_five_hour_or_weekly(self):
        result = AntigravityAdapter._parse_user_status(status_payload())

        self.assertEqual(result["provider"], "antigravity")
        self.assertTrue(result["available"])
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["state_label"], "已连接")
        self.assertTrue(result["stale"])
        self.assertEqual(len(result["subscription"]["quota_windows"]), 4)
        known, empty_placeholder, exhausted, invalid_window = result["subscription"]["quota_windows"]
        self.assertEqual(known["label"], "Gemini 3.8 Flash (Medium)")
        self.assertEqual(known["model"], "MODEL_PLACEHOLDER_M319")
        self.assertEqual(known["remaining_percent"], 100.0)
        self.assertEqual(known["reset_at"], RESET)
        self.assertTrue(known["known"])
        self.assertFalse(empty_placeholder["known"])
        self.assertIsNone(empty_placeholder["remaining_percent"])
        self.assertIsNone(empty_placeholder["reset_at"])
        self.assertTrue(exhausted["known"])
        self.assertEqual(exhausted["remaining_percent"], 0.0)
        self.assertEqual(exhausted["reset_at"], RESET)
        self.assertFalse(invalid_window["known"])
        self.assertIsNone(invalid_window["remaining_percent"])
        self.assertIsNone(invalid_window["reset_at"])
        self.assertFalse(result["tokens"]["known"])
        self.assertIsNone(result["active_tasks"])
        self.assertIsNone(result["subscription"]["five_hour"]["remaining_percent"])
        self.assertIsNone(result["subscription"]["weekly"]["remaining_percent"])
        self.assertEqual(result["quota_group"], "gemini")
        self.assertEqual(result["subscription"]["quota_group"], "gemini")
        self.assertNotIn("plan_spec", result["subscription"])
        self.assertNotIn("expires_at", result["subscription"])
        self.assertNotIn("Claude", json.dumps(result))
        self.assertNotIn("GPT", json.dumps(result))
        self.assertNotIn("private@example.invalid", json.dumps(result))

    def test_summary_maps_shared_gemini_five_hour_and_weekly_windows_only(self):
        result = AntigravityAdapter._parse_quota_summary(quota_summary_payload())

        self.assertEqual(result["provider"], "antigravity")
        self.assertEqual(result["quota_group"], "gemini")
        self.assertEqual(result["subscription"]["quota_group"], "gemini")
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["state_label"], "已连接")
        self.assertFalse(result["stale"])
        self.assertEqual(result["subscription"]["five_hour"], {
            "label": "5小时", "model": "Gemini", "window": "five_hour",
            "remaining_percent": 82.0, "reset_at": RESET, "known": True,
        })
        self.assertEqual(result["subscription"]["weekly"]["remaining_percent"], 58.0)
        self.assertEqual(result["subscription"]["weekly"]["reset_at"], "2035-02-14T10:00:00Z")
        self.assertEqual(len(result["subscription"]["quota_windows"]), 2)
        self.assertTrue(all(window["model"] == "Gemini" for window in result["subscription"]["quota_windows"]))
        self.assertFalse(result["tokens"]["known"])
        self.assertIsNone(result["active_tasks"])
        self.assertNotIn("Claude", json.dumps(result))
        self.assertNotIn("GPT", json.dumps(result))

    def test_summary_keeps_one_explicit_window_and_does_not_guess_other_period(self):
        payload = {"groups": [{"displayName": "Gemini Models", "buckets": [
            {"bucketId": "gemini-weekly", "window": "weekly", "remaining_fraction": 0.41,
             "reset_time": "2035-02-14T10:00:00Z"},
            {"bucketId": "gemini-monthly", "window": "monthly", "remaining_fraction": 0.99,
             "reset_time": RESET},
            {"bucketId": "gemini-disabled-5h", "window": "5h", "disabled": True,
             "remaining_fraction": 1, "reset_time": RESET},
        ]}]}
        result = AntigravityAdapter._parse_quota_summary(payload)

        self.assertTrue(result["subscription"]["weekly"]["known"])
        self.assertEqual(result["subscription"]["weekly"]["remaining_percent"], 41.0)
        self.assertFalse(result["subscription"]["five_hour"]["known"])
        self.assertIsNone(result["subscription"]["five_hour"]["remaining_percent"])
        self.assertIn("5小时窗口本次未返回", result["detail"])

    def test_summary_does_not_infer_period_from_reset_time_or_merge_duplicate_buckets(self):
        no_period = {"groups": [{"displayName": "Gemini Models", "buckets": [
            {"bucketId": "gemini-limit", "remainingFraction": 0.2, "resetTime": RESET},
        ]}]}
        result = AntigravityAdapter._parse_quota_summary(no_period)
        self.assertFalse(result["subscription"]["five_hour"]["known"])
        self.assertFalse(result["subscription"]["weekly"]["known"])

        self.assertIsNone(AntigravityAdapter._bucket_window({"window": "15h", "bucketId": "gemini-5h"}))
        self.assertIsNone(AntigravityAdapter._bucket_window({"window": "25hour", "bucketId": "gemini-5h"}))
        self.assertIsNone(AntigravityAdapter._bucket_window({"window": "session", "bucketId": "gemini-5h"}))
        self.assertTrue(result["stale"])

        duplicate = {"groups": [{"displayName": "Gemini Models", "buckets": [
            {"bucketId": "gemini-5h-a", "window": "5h", "remainingFraction": 0.8, "resetTime": RESET},
            {"bucketId": "gemini-5h-b", "window": "5h", "remainingFraction": 0.2, "resetTime": RESET},
        ]}]}
        result = AntigravityAdapter._parse_quota_summary(duplicate)
        self.assertFalse(result["subscription"]["five_hour"]["known"])
        self.assertFalse(result["subscription"]["weekly"]["known"])

    def test_local_rpc_uses_only_loopback_and_process_token_header(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(status_payload()).encode("utf-8")
        connection = MagicMock()
        connection.getresponse.return_value = response

        with patch("agent_dashboard.adapters.antigravity.http.client.HTTPConnection", return_value=connection) as factory:
            result = AntigravityAdapter._fetch_user_status(
                [{"csrf": "local-only-test-token", "ports": [61234]}]
            )

        factory.assert_called_once_with("127.0.0.1", 61234, timeout=2.0)
        args, kwargs = connection.request.call_args
        self.assertEqual(args[:2], ("POST", _RPC_PATH))
        self.assertEqual(kwargs["headers"]["X-Codeium-Csrf-Token"], "local-only-test-token")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(result["userStatus"]["email"], "private@example.invalid")

    def test_summary_rpc_uses_same_process_owned_loopback_and_explicit_refresh(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(quota_summary_payload()).encode("utf-8")
        connection = MagicMock()
        connection.getresponse.return_value = response

        with patch("agent_dashboard.adapters.antigravity.http.client.HTTPConnection", return_value=connection) as factory:
            result = AntigravityAdapter._fetch_quota_summary(
                [{"csrf": "local-only-test-token", "ports": [61234]}]
            )

        factory.assert_called_once_with("127.0.0.1", 61234, timeout=2.0)
        args, kwargs = connection.request.call_args
        self.assertEqual(args[:2], ("POST", _SUMMARY_RPC_PATH))
        self.assertEqual(json.loads(kwargs["body"]), {"forceRefresh": True})
        self.assertEqual(kwargs["headers"]["X-Codeium-Csrf-Token"], "local-only-test-token")
        self.assertEqual(len(result["response"]["groups"]), 2)

    def test_summary_retry_preserves_original_request_body_after_bad_response(self):
        bad_response = MagicMock(status=200)
        bad_response.read.return_value = b"not-json"
        good_response = MagicMock(status=200)
        good_response.read.return_value = json.dumps(quota_summary_payload()).encode("utf-8")
        first_connection = MagicMock()
        first_connection.getresponse.return_value = bad_response
        second_connection = MagicMock()
        second_connection.getresponse.return_value = good_response

        with patch("agent_dashboard.adapters.antigravity.http.client.HTTPConnection",
                   side_effect=[first_connection, second_connection]) as factory:
            result = AntigravityAdapter._fetch_quota_summary(
                [{"csrf": "local-only-test-token", "ports": [61234, 61235]}]
            )

        self.assertEqual(factory.call_count, 2)
        expected_body = json.dumps({"forceRefresh": True}, separators=(",", ":")).encode("utf-8")
        self.assertEqual(first_connection.request.call_args.kwargs["body"], expected_body)
        self.assertEqual(second_connection.request.call_args.kwargs["body"], expected_body)
        self.assertEqual(len(result["response"]["groups"]), 2)

    def test_collect_prefers_shared_summary_before_legacy_model_rpc(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Antigravity.exe").write_bytes(b"installed")
            adapter = AntigravityAdapter(root)
            with (
                patch.object(adapter, "_discover_runtime", return_value={"app_running": True, "servers": []}),
                patch.object(adapter, "_fetch_quota_summary", return_value=quota_summary_payload()) as summary,
                patch.object(adapter, "_fetch_user_status", side_effect=AssertionError("legacy fallback called")) as legacy,
                patch.object(adapter, "_fetch_activity", return_value=activity_result(1)) as activity,
                patch("agent_dashboard.adapters.antigravity.time.monotonic", return_value=100.0),
            ):
                result = adapter.collect()

        summary.assert_called_once_with([])
        legacy.assert_not_called()
        activity.assert_called_once_with([])
        self.assertEqual(result["active_tasks"], 1)
        self.assertTrue(result["subscription"]["five_hour"]["known"])
        self.assertTrue(result["subscription"]["weekly"]["known"])

    def test_activity_refresh_after_two_seconds_does_not_refresh_quota(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Antigravity.exe").write_bytes(b"installed")
            adapter = AntigravityAdapter(root)
            with (
                patch.object(adapter, "_discover_runtime", return_value={"app_running": True, "servers": []}) as discover,
                patch.object(adapter, "_fetch_quota_summary", return_value=quota_summary_payload()) as quota,
                patch.object(adapter, "_fetch_user_status") as legacy,
                patch.object(adapter, "_fetch_activity", side_effect=[activity_result(2), activity_result(0)]) as activity,
                patch("agent_dashboard.adapters.antigravity.time.monotonic", side_effect=[100.0, 103.0]),
            ):
                first = adapter.collect()
                second = adapter.collect()

        discover.assert_called_once_with()
        quota.assert_called_once_with([])
        legacy.assert_not_called()
        self.assertEqual(activity.call_count, 2)
        self.assertEqual((first["active_tasks"], first["state"]), (2, "working"))
        self.assertEqual((second["active_tasks"], second["state"]), (0, "idle"))
        self.assertEqual(first["subscription"]["weekly"], second["subscription"]["weekly"])

    def test_quota_failure_does_not_block_fresh_activity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Antigravity.exe").write_bytes(b"installed")
            adapter = AntigravityAdapter(root)
            with (
                patch.object(adapter, "_discover_runtime", return_value={"app_running": True, "servers": []}),
                patch.object(adapter, "_fetch_quota_summary", side_effect=RuntimeError("synthetic quota failure")),
                patch.object(adapter, "_fetch_user_status", side_effect=RuntimeError("synthetic legacy failure")),
                patch.object(adapter, "_fetch_activity", side_effect=[activity_result(1), activity_result(0)]) as activity,
                patch("agent_dashboard.adapters.antigravity.time.monotonic", side_effect=[200.0, 203.0]),
            ):
                first = adapter.collect()
                second = adapter.collect()

        self.assertEqual(activity.call_count, 2)
        self.assertEqual((first["active_tasks"], first["state"]), (1, "working"))
        self.assertEqual((second["active_tasks"], second["state"]), (0, "idle"))
        self.assertTrue(second["stale"])  # Quota remains unavailable while activity is fresh.

    def test_activity_failure_retains_count_but_fresh_idle_zero_replaces_busy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Antigravity.exe").write_bytes(b"installed")
            adapter = AntigravityAdapter(root)
            with (
                patch.object(adapter, "_discover_runtime", return_value={"app_running": True, "servers": []}),
                patch.object(adapter, "_fetch_quota_summary", return_value=quota_summary_payload()) as quota,
                patch.object(adapter, "_fetch_activity",
                             side_effect=[activity_result(4), RuntimeError("synthetic activity failure"),
                                          activity_result(0)]) as activity,
                patch("agent_dashboard.adapters.antigravity.time.monotonic", side_effect=[100.0, 102.0, 104.0]),
            ):
                working = adapter.collect()
                failed = adapter.collect()
                idle = adapter.collect()

        quota.assert_called_once_with([])
        self.assertEqual(activity.call_count, 3)
        self.assertEqual((working["state"], working["active_tasks"]), ("working", 4))
        self.assertEqual((failed["state"], failed["active_tasks"]), ("unknown", None))
        self.assertTrue(failed["stale"])

        store = LastGoodStore()
        first_time = datetime.fromisoformat(working["updated_at"].replace("Z", "+00:00"))
        store.merge(working, first_time)
        failed_time = first_time + timedelta(seconds=2)
        retained = store.merge(failed, failed_time)
        self.assertEqual(retained["active_tasks"], 4)
        self.assertEqual(retained["state"], "working")
        self.assertIn("active_tasks", retained["retained_fields"])

        idle_time = datetime.fromisoformat(idle["updated_at"].replace("Z", "+00:00"))
        completed = store.merge(idle, idle_time)
        self.assertEqual(completed["active_tasks"], 0)
        self.assertEqual(completed["state"], "idle")
        self.assertNotIn("active_tasks", completed["retained_fields"])

    def test_offline_snapshot_retains_both_shared_gemini_windows_as_stale(self):
        adapter = AntigravityAdapter("unused")
        adapter._last_result = AntigravityAdapter._parse_quota_summary(quota_summary_payload())
        result = adapter._offline_snapshot()

        self.assertTrue(result["stale"])
        self.assertEqual(result["quota_group"], "gemini")
        self.assertEqual(result["subscription"]["five_hour"], adapter._last_result["subscription"]["five_hour"])
        self.assertEqual(result["subscription"]["weekly"], adapter._last_result["subscription"]["weekly"])

    def test_collects_quota_and_failure_retains_it_as_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Antigravity.exe").write_bytes(b"installed")
            adapter = AntigravityAdapter(root)
            with (
                patch.object(adapter, "_discover_runtime", return_value={"app_running": True, "servers": []}),
                patch.object(adapter, "_fetch_quota_summary", side_effect=RuntimeError("summary unavailable")),
                patch.object(adapter, "_fetch_user_status", side_effect=[status_payload(), RuntimeError("private error")]),
                patch("agent_dashboard.adapters.antigravity.time.monotonic", side_effect=[100.0, 161.0]),
            ):
                first = adapter.collect()
                failed = adapter.collect()

        self.assertEqual(first["subscription"]["quota_windows"], failed["subscription"]["quota_windows"])
        self.assertTrue(first["subscription"]["quota_windows"][0]["known"])
        self.assertTrue(failed["stale"])
        self.assertNotIn("private error", failed["detail"])

    def test_throttles_quota_for_sixty_seconds_but_refreshes_activity_after_two_seconds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Antigravity.exe").write_bytes(b"installed")
            adapter = AntigravityAdapter(root)
            with (
                patch.object(adapter, "_discover_runtime", return_value={"app_running": True, "servers": []}) as discover,
                patch.object(adapter, "_fetch_quota_summary", side_effect=RuntimeError("summary unavailable")) as summary,
                patch.object(adapter, "_fetch_user_status", return_value=status_payload()) as fetch,
                patch.object(adapter, "_fetch_activity", return_value=activity_result(0)) as activity,
                patch("agent_dashboard.adapters.antigravity.time.monotonic", side_effect=[100.0, 159.0, 161.0]),
            ):
                first = adapter.collect()
                first["subscription"]["quota_windows"].clear()
                second = adapter.collect()
                third = adapter.collect()

        self.assertEqual(discover.call_count, 2)
        self.assertEqual(summary.call_count, 2)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(activity.call_count, 3)
        self.assertEqual(len(second["subscription"]["quota_windows"]), 4)
        self.assertEqual(len(third["subscription"]["quota_windows"]), 4)

    def test_missing_install_reports_offline_without_process_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = AntigravityAdapter(temp)
            with patch.object(adapter, "_discover_runtime") as discover:
                result = adapter.collect()

        self.assertFalse(result["available"])
        self.assertEqual(result["state"], "offline")
        self.assertEqual(result["subscription"]["quota_windows"], [])
        discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
