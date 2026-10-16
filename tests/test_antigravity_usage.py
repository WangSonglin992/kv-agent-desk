import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import ImageChops

from agent_dashboard.adapters.antigravity import (
    AntigravityAdapter,
    _ACTIVITY_RPC_PATH,
    _USAGE_RPC_PATH,
)
from agent_dashboard.render import _agent_metrics, render
from agent_dashboard.retention import LastGoodStore


SERVER_A = {"csrf": "synthetic-csrf-a", "ports": [61234]}
SERVER_B = {"csrf": "synthetic-csrf-b", "ports": [61235]}
RESET = "2035-02-07T10:00:00Z"


def summary_payload():
    return {"groups": [{"displayName": "Gemini Models", "buckets": [
        {"bucketId": "gemini-5h", "window": "5h", "remainingFraction": .8, "resetTime": RESET},
        {"bucketId": "gemini-weekly", "window": "weekly", "remainingFraction": .6,
         "resetTime": "2035-02-14T10:00:00Z"},
    ]}]}


def metadata_entry(usage, **extra):
    return {"chatModel": {"usage": usage}, **extra}


def usage_call(path, body):
    return json.loads(body)


class AntigravityUsageTests(unittest.TestCase):
    def test_token_count_accepts_uint64_strings_and_rejects_invalid_values(self):
        maximum = 2**64 - 1
        self.assertEqual(AntigravityAdapter._token_count(str(maximum)), maximum)
        self.assertEqual(AntigravityAdapter._token_count("00042"), 42)
        for value in (-1, True, False, 1.0, None, "-1", "+1", "12x", str(maximum + 1), maximum + 1):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                AntigravityAdapter._token_count(value)

    def test_fetch_tokens_paginates_until_empty_page_and_excludes_cached_from_total(self):
        requests = []

        def rpc(servers, path, body):
            if path == _ACTIVITY_RPC_PATH:
                self.assertEqual(json.loads(body), {"excludeSubtrajectories": False})
                return {"trajectorySummaries": {"synthetic-cascade": {}}}
            request = usage_call(path, body)
            requests.append(request)
            self.assertEqual(path, _USAGE_RPC_PATH)
            self.assertFalse(request["includeMessages"])
            self.assertEqual(request["cascadeId"], "synthetic-cascade")
            if request["generatorMetadataOffset"] == 0:
                return {"generatorMetadata": [metadata_entry({
                    "messageId": "synthetic-message-1", "inputTokens": "10", "outputTokens": 2,
                    "cacheReadTokens": 8, "cacheWriteTokens": 3, "thinkingOutputTokens": 5,
                })]}
            if request["generatorMetadataOffset"] == 1:
                return {"generatorMetadata": [metadata_entry({
                    "messageId": "synthetic-message-2", "inputTokens": 20, "outputTokens": "3",
                    "cacheReadTokens": 12, "cacheWriteTokens": 4, "thinkingOutputTokens": 7,
                })]}
            return {"generatorMetadata": []}

        with patch.object(AntigravityAdapter, "_fetch_local_rpc", side_effect=rpc):
            result = AntigravityAdapter._fetch_tokens([SERVER_A])

        self.assertEqual([request["generatorMetadataOffset"] for request in requests], [0, 1, 2])
        self.assertEqual(result, {
            "known": True, "scope": "cumulative", "input": 30, "output": 5,
            "cached": 20, "cache_write": 7, "reasoning": 12, "total": 35,
        })

    def test_fetch_tokens_deduplicates_cascade_ids_across_servers_and_message_ids(self):
        requests = []
        shared_usage = {"messageId": "synthetic-shared-message", "inputTokens": 11,
                        "outputTokens": 4, "cacheReadTokens": 30}

        def rpc(servers, path, body):
            if path == _ACTIVITY_RPC_PATH:
                if servers[0] == SERVER_A:
                    return {"trajectorySummaries": {"synthetic-shared-cascade": {}}}
                return {"trajectorySummaries": {
                    "synthetic-shared-cascade": {}, "synthetic-second-cascade": {}}}
            request = usage_call(path, body)
            requests.append((servers[0], request))
            self.assertFalse(request["includeMessages"])
            if request["generatorMetadataOffset"]:
                return {"generatorMetadata": []}
            return {"generatorMetadata": [metadata_entry(shared_usage)]}

        with patch.object(AntigravityAdapter, "_fetch_local_rpc", side_effect=rpc):
            result = AntigravityAdapter._fetch_tokens([SERVER_A, SERVER_B])

        first_pages = [request for _, request in requests if request["generatorMetadataOffset"] == 0]
        self.assertEqual([request["cascadeId"] for request in first_pages],
                         ["synthetic-shared-cascade", "synthetic-second-cascade"])
        self.assertEqual(result["input"], 11)
        self.assertEqual(result["output"], 4)
        self.assertEqual(result["cached"], 30)
        self.assertEqual(result["total"], 15)

    def test_fetch_tokens_never_returns_partial_totals_if_a_later_request_fails(self):
        def rpc(servers, path, body):
            if path == _ACTIVITY_RPC_PATH:
                return {"trajectorySummaries": {"synthetic-first": {}, "synthetic-second": {}}}
            request = usage_call(path, body)
            if request["cascadeId"] == "synthetic-first" and request["generatorMetadataOffset"] == 0:
                return {"generatorMetadata": [metadata_entry({"inputTokens": 100, "outputTokens": 2})]}
            if request["cascadeId"] == "synthetic-first":
                return {"generatorMetadata": []}
            raise RuntimeError("synthetic later-page failure")

        with patch.object(AntigravityAdapter, "_fetch_local_rpc", side_effect=rpc):
            with self.assertRaisesRegex(RuntimeError, "synthetic later-page failure"):
                AntigravityAdapter._fetch_tokens([SERVER_A])

    def test_missing_usage_is_unknown_not_a_zero_total(self):
        def rpc(servers, path, body):
            if path == _ACTIVITY_RPC_PATH:
                return {"trajectorySummaries": {"synthetic-cascade": {}}}
            if usage_call(path, body)["generatorMetadataOffset"] == 0:
                return {"generatorMetadata": [metadata_entry({})]}
            return {"generatorMetadata": []}

        with patch.object(AntigravityAdapter, "_fetch_local_rpc", side_effect=rpc):
            with self.assertRaisesRegex(ValueError, "usage counts unavailable"):
                AntigravityAdapter._fetch_tokens([SERVER_A])

    def test_empty_trajectory_map_is_unknown_not_a_zero_total(self):
        with patch.object(AntigravityAdapter, "_fetch_local_rpc",
                          return_value={"trajectorySummaries": {}}):
            with self.assertRaisesRegex(ValueError, "usage trajectories unavailable"):
                AntigravityAdapter._fetch_tokens([SERVER_A])

    def test_usage_metadata_requests_exclude_messages_and_result_keeps_only_aggregates(self):
        private_values = ("synthetic private prompt", "synthetic private title", "synthetic/private/path")

        def rpc(servers, path, body):
            if path == _ACTIVITY_RPC_PATH:
                return {"trajectorySummaries": {"synthetic-cascade": {}}}
            request = usage_call(path, body)
            self.assertFalse(request["includeMessages"])
            if request["generatorMetadataOffset"]:
                return {"generatorMetadata": []}
            return {"generatorMetadata": [metadata_entry(
                {"messageId": "synthetic-message", "inputTokens": 7, "outputTokens": 3},
                prompt=private_values[0], title=private_values[1], workspace=private_values[2],
            )]}

        with patch.object(AntigravityAdapter, "_fetch_local_rpc", side_effect=rpc):
            result = AntigravityAdapter._fetch_tokens([SERVER_A])

        self.assertEqual(set(result), {"known", "scope", "input", "output", "cached", "cache_write", "reasoning", "total"})
        self.assertEqual(result["total"], 10)
        serialized = json.dumps(result)
        for private_value in private_values:
            self.assertNotIn(private_value, serialized)

    def test_slow_usage_query_does_not_block_collect_and_usage_refresh_is_limited_to_15_seconds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Antigravity.exe").write_bytes(b"installed")
            adapter = AntigravityAdapter(root)
            entered = threading.Event()
            release = threading.Event()
            usage = {"known": True, "scope": "cumulative", "input": 10, "output": 5,
                     "cached": 1, "cache_write": 0, "reasoning": 0, "total": 15}

            def slow_usage(servers):
                entered.set()
                release.wait(timeout=2)
                return usage

            with (
                patch.object(adapter, "_discover_runtime", return_value={"app_running": True, "servers": [SERVER_A]}),
                patch.object(adapter, "_fetch_quota_summary", return_value=summary_payload()) as quota,
                patch.object(adapter, "_fetch_activity", return_value={
                    "state": "working", "observed_state": "working", "state_label": "工作中",
                    "active_tasks": 1, "active_tasks_known": True,
                }) as activity,
                patch.object(adapter, "_fetch_tokens", side_effect=slow_usage) as fetch_tokens,
                patch("agent_dashboard.adapters.antigravity.time.monotonic", side_effect=[100.0, 102.0, 115.0]),
            ):
                started = time.perf_counter()
                first = adapter.collect()
                elapsed = time.perf_counter() - started
                self.assertTrue(entered.wait(timeout=1))
                self.assertLess(elapsed, .5)
                self.assertEqual(first["active_tasks"], 1)
                self.assertEqual(first["tokens"]["scope"], "cumulative")
                release.set()
                adapter._usage_thread.join(timeout=1)

                adapter.collect()  # 2 seconds: activity refreshes, usage remains throttled.
                self.assertEqual(fetch_tokens.call_count, 1)
                adapter.collect()  # 15 seconds: usage refresh becomes eligible again.
                adapter._usage_thread.join(timeout=1)

        self.assertEqual(fetch_tokens.call_count, 2)
        self.assertEqual(quota.call_count, 1)
        self.assertEqual(activity.call_count, 3)

    def test_failed_usage_refresh_keeps_last_good_cumulative_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Antigravity.exe").write_bytes(b"installed")
            adapter = AntigravityAdapter(root)
            good_usage = {"known": True, "scope": "cumulative", "input": 90, "output": 10,
                          "cached": 70, "cache_write": 8, "reasoning": 4, "total": 100}
            with (
                patch.object(adapter, "_discover_runtime", return_value={"app_running": True, "servers": [SERVER_A]}),
                patch.object(adapter, "_fetch_quota_summary", return_value=summary_payload()),
                patch.object(adapter, "_fetch_activity", return_value={
                    "state": "idle", "observed_state": "idle", "state_label": "空闲",
                    "active_tasks": 0, "active_tasks_known": True,
                }),
                patch.object(adapter, "_fetch_tokens", side_effect=[good_usage, RuntimeError("synthetic usage failure")]) as fetch_tokens,
                patch("agent_dashboard.adapters.antigravity.time.monotonic", side_effect=[200.0, 203.0, 216.0, 219.0]),
            ):
                adapter.collect()
                adapter._usage_thread.join(timeout=1)
                good_snapshot = adapter.collect()
                bad_refresh_snapshot = adapter.collect()
                adapter._usage_thread.join(timeout=1)
                failed_snapshot = adapter.collect()

        self.assertEqual(fetch_tokens.call_count, 2)
        self.assertTrue(good_snapshot["tokens"]["known"])
        self.assertFalse(failed_snapshot["tokens"]["known"])
        self.assertTrue(failed_snapshot["stale"])
        store = LastGoodStore()
        good_time = datetime.fromisoformat(good_snapshot["updated_at"].replace("Z", "+00:00"))
        store.merge(good_snapshot, good_time)
        failed_time = datetime.fromisoformat(failed_snapshot["updated_at"].replace("Z", "+00:00"))
        retained = store.merge(failed_snapshot, failed_time)
        self.assertEqual(retained["tokens"]["total"], 100)
        self.assertEqual(retained["tokens"]["scope"], "cumulative")
        self.assertIn("tokens", retained["retained_fields"])
        self.assertTrue(retained["stale"])

    def test_cumulative_scope_label_is_used_by_all_three_render_profiles(self):
        now = datetime(2035, 2, 7, 12, tzinfo=timezone.utc)
        agent = {
            "provider": "antigravity", "name": "Antigravity", "state": "idle", "state_label": "空闲",
            "active_tasks": 0, "active_tasks_known": True,
            "tokens": {"known": True, "scope": "cumulative", "input": 90, "output": 10,
                       "cached": 70, "total": 100},
            "subscription": {"five_hour": {"known": False}, "weekly": {"known": False}},
        }
        today_agent = {**agent, "tokens": {**agent["tokens"], "scope": "today", "date": "2035-02-07"}}
        self.assertEqual(_agent_metrics(agent, "2035-02-07")["date_label"], "累计 TOKENS")
        self.assertEqual(_agent_metrics(today_agent, "2035-02-07")["date_label"], "今日 TOKENS")

        for layout in ("classic", "aurora", "paper"):
            with self.subTest(layout=layout):
                profile = {"id": layout, "layout": layout}
                cumulative = render({"agents": [agent]}, now, profile, animation_time=0)
                today = render({"agents": [today_agent]}, now, profile, animation_time=0)
                region = (40, 280, 350, 340)
                self.assertIsNotNone(ImageChops.difference(cumulative.crop(region), today.crop(region)).getbbox())


if __name__ == "__main__":
    unittest.main()
