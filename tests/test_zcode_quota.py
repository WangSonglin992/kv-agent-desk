import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from agent_dashboard.adapters.zcode_quota import ZCodeQuotaCollector, _NoRedirectHandler


def quota_payload(reset_ms: int) -> dict:
    return {
        "code": 200,
        "success": True,
        "data": {
            "limits": [
                {
                    "type": "TOKENS_LIMIT",
                    "unit": 3,
                    "number": 5,
                    "percentage": 32.5,
                    "nextResetTime": reset_ms,
                },
                {
                    "type": "TOKENS_LIMIT",
                    "unit": 6,
                    "number": 1,
                    "percentage": 80,
                    "nextResetTime": reset_ms + 7_200_000,
                },
                {"type": "TIME_LIMIT", "unit": 5, "number": 1, "percentage": 10},
            ]
        },
    }


class FakeQuotaCollector(ZCodeQuotaCollector):
    def __init__(self, quota_responses, detail_responses=None):
        super().__init__(Path("unused-test-root"))
        self.quota_responses = list(quota_responses)
        self.detail_responses = list(detail_responses or [])
        self.quota_calls = 0
        self.detail_calls = 0
        self.request_target = None

    def _resolve_target(self):
        return "unit-test-secret", "https://api.z.ai/api/monitor/usage/quota/limit", "test source"

    def _fetch_json(self, url, api_key):
        self.request_target = (url, api_key)
        is_detail = url.endswith("/api/biz/subscription/list")
        queue = self.detail_responses if is_detail else self.quota_responses
        if is_detail:
            self.detail_calls += 1
        else:
            self.quota_calls += 1
        response = queue.pop(0) if queue else {"code": 200, "success": True, "data": []}
        if isinstance(response, Exception):
            raise response
        return response


class ZCodeQuotaTests(unittest.TestCase):
    def test_maps_official_used_percentages_to_remaining_and_resets(self):
        reset_ms = int((datetime.now().astimezone() + timedelta(hours=2)).timestamp() * 1000)
        collector = FakeQuotaCollector([quota_payload(reset_ms)])

        result = collector.collect()

        self.assertEqual(result["name"], "ZCode")
        self.assertEqual(result["five_hour"]["remaining_percent"], 67.5)
        self.assertEqual(result["weekly"]["remaining_percent"], 20.0)
        self.assertTrue(result["five_hour"]["known"])
        self.assertTrue(result["weekly"]["known"])
        actual_reset = datetime.fromisoformat(result["five_hour"]["reset_at"])
        expected_reset = datetime.fromtimestamp(reset_ms / 1000).astimezone()
        self.assertLess(abs((actual_reset - expected_reset).total_seconds()), 1)
        self.assertIn("api.z.ai", result["source"])
        self.assertNotIn("unit-test-secret", json.dumps(result))

    def test_only_one_query_is_made_within_sixty_seconds_and_failure_keeps_last_value(self):
        reset_ms = int((datetime.now().astimezone() + timedelta(hours=1)).timestamp() * 1000)
        collector = FakeQuotaCollector(
            [quota_payload(reset_ms), RuntimeError("hidden response")],
            [RuntimeError("hidden detail response"), RuntimeError("hidden detail response")],
        )
        clock = [100.0]
        with patch("agent_dashboard.adapters.zcode_quota.time.monotonic", side_effect=lambda: clock[0]):
            first = collector.collect()
            clock[0] += 59
            second = collector.collect()
            self.assertEqual(collector.quota_calls, 1)
            clock[0] += 2
            third = collector.collect()

        self.assertEqual(collector.quota_calls, 2)
        self.assertEqual(collector.detail_calls, 2)
        self.assertEqual(second, first)
        self.assertEqual(third["five_hour"]["remaining_percent"], first["five_hour"]["remaining_percent"])
        self.assertEqual(third["updated_at"], first["updated_at"])
        self.assertNotIn("hidden response", json.dumps(third))

    def test_resolves_only_the_selected_provider_and_blocks_nonofficial_origin(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            v2 = root / "v2"
            v2.mkdir()
            (v2 / "setting.json").write_text(
                json.dumps(
                    {
                        "providerFamilyDomain": "bigmodel",
                        "modelProviderFamilySelectedKeys": {
                            "bigmodel": "coding-plan:builtin:bigmodel-coding-plan"
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "provider": {
                    "unselected-provider": {
                        "options": {"apiKey": "must-not-be-selected", "baseURL": "https://example.com"}
                    },
                    "builtin:bigmodel-coding-plan": {
                        "options": {
                            "apiKey": "selected-test-key",
                            "baseURL": "https://open.bigmodel.cn/api/anthropic",
                        }
                    },
                }
            }
            config_path = v2 / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            collector = ZCodeQuotaCollector(root)
            key, url, _ = collector._resolve_target()
            self.assertEqual(key, "selected-test-key")
            self.assertEqual(url, "https://open.bigmodel.cn/api/monitor/usage/quota/limit")

            config["provider"]["builtin:bigmodel-coding-plan"]["options"]["baseURL"] = (
                "https://attacker.example/api/anthropic"
            )
            config_path.write_text(json.dumps(config), encoding="utf-8")
            blocked = ZCodeQuotaCollector(root)
            result = blocked.collect()

        self.assertFalse(result["five_hour"]["known"])
        self.assertIn("为避免外发密钥", result["source"])

    def test_redirect_handler_never_returns_a_followup_request(self):
        handler = _NoRedirectHandler()
        request = Request("https://api.z.ai/api/monitor/usage/quota/limit")
        self.assertIsNone(handler.redirect_request(request, None, 302, "Found", {}, "https://other.example/"))

    def test_missing_weekly_window_is_unknown_with_reason(self):
        reset_ms = int((datetime.now().astimezone() + timedelta(hours=1)).timestamp() * 1000)
        payload = quota_payload(reset_ms)
        payload["data"]["limits"] = payload["data"]["limits"][:1]
        result = FakeQuotaCollector([payload]).collect()

        self.assertTrue(result["five_hour"]["known"])
        self.assertFalse(result["weekly"]["known"])
        self.assertIn("未提供该额度窗口", result["source"])


if __name__ == "__main__":
    unittest.main()
