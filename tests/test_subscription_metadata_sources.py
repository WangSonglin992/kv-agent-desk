import unittest
from datetime import datetime, timedelta
from pathlib import Path

from agent_dashboard.adapters.codex import CodexAdapter, _rate_plan_type
from agent_dashboard.adapters.zcode_quota import ZCodeQuotaCollector


class FixedSourceQuotaCollector(ZCodeQuotaCollector):
    def __init__(self, quota_payload: dict, details_payload: dict):
        super().__init__(Path("unused-source-test"))
        self.quota_payload = quota_payload
        self.details_payload = details_payload

    def _resolve_target(self):
        return "test-key", "https://open.bigmodel.cn/api/monitor/usage/quota/limit", "test"

    def _fetch_json(self, url: str, api_key: str):
        if url.endswith("/api/biz/subscription/list"):
            return self.details_payload
        return self.quota_payload


def active_coding_plan(**overrides) -> dict:
    item = {
        "productId": "synthetic-example-coding-plan-2035",
        "productName": "Synthetic Example Coding Plan",
        "status": "VALID",
        "inCurrentPeriod": True,
        "billingCycle": "monthly",
        "autoRenew": 1,
        "nextRenewTime": "2035-02-07 10:00:00",
        "valid": "2035-01-07 10:00:00 - 2035-02-07 10:00:00",
    }
    item.update(overrides)
    return item


class SubscriptionMetadataSourceTests(unittest.TestCase):
    def test_codex_plan_type_provides_tier_but_not_expiration(self):
        now = datetime.now().astimezone()
        reset = now + timedelta(hours=3)
        adapter = CodexAdapter(Path("unused-codex-root"))

        plan_type = _rate_plan_type({"plan_type": "pro"}, {"plan_type": "pro"})
        subscription = adapter._subscription(
            [
                {
                    "limit_id": "codex",
                    "window_minutes": 7 * 24 * 60,
                    "used_percent": 35,
                    "resets_at": reset.timestamp(),
                    "timestamp": now,
                    "plan_type": plan_type,
                }
            ]
        )

        self.assertEqual(subscription["plan_name"], "ChatGPT Pro")
        self.assertIsNone(subscription["plan_spec"])
        self.assertIsNone(subscription["expires_at"])
        self.assertIn("plan_type", subscription["metadata_source"])
        self.assertIsNotNone(subscription["weekly"]["reset_at"])

    def test_codex_missing_plan_type_does_not_guess_a_plan(self):
        adapter = CodexAdapter(Path("unused-codex-root"))
        subscription = adapter._subscription(
            [
                {
                    "limit_id": "codex",
                    "window_minutes": 7 * 24 * 60,
                    "used_percent": 12,
                    "resets_at": datetime.now().timestamp() + 600,
                    "timestamp": datetime.now().astimezone(),
                    "plan_type": None,
                }
            ]
        )

        self.assertIsNone(subscription["plan_name"])
        self.assertIsNone(subscription["plan_spec"])
        self.assertIsNone(subscription["expires_at"])

    def test_zcode_uses_subscription_product_cycle_and_valid_period_end(self):
        collector = ZCodeQuotaCollector(Path("unused-zcode-root"))
        url = "https://open.bigmodel.cn/api/biz/subscription/list"
        result = collector._parse_subscription_details(
            {"code": 200, "success": True, "data": [active_coding_plan()]}, url
        )

        self.assertEqual(result["plan_name"], "Synthetic Example Coding Plan")
        self.assertEqual(result["plan_spec"], "月付")
        expiry = datetime.fromisoformat(result["expires_at"])
        self.assertEqual((expiry.year, expiry.month, expiry.day, expiry.hour), (2035, 2, 7, 10))
        self.assertIn("valid", result["metadata_source"])

    def test_zcode_collector_combines_quota_and_subscription_metadata(self):
        reset_ms = int((datetime.now().astimezone() + timedelta(hours=2)).timestamp() * 1000)
        collector = FixedSourceQuotaCollector(
            {
                "code": 200,
                "success": True,
                "data": {
                    "level": "pro",
                    "limits": [
                        {
                            "type": "TOKENS_LIMIT",
                            "unit": 3,
                            "number": 5,
                            "percentage": 7,
                            "nextResetTime": reset_ms,
                        }
                    ],
                },
            },
            {"code": 200, "success": True, "data": [active_coding_plan()]},
        )

        subscription = collector.collect()

        self.assertEqual(subscription["plan_name"], "Synthetic Example Coding Plan")
        self.assertEqual(subscription["plan_spec"], "月付")
        expiry = datetime.fromisoformat(subscription["expires_at"])
        self.assertEqual((expiry.year, expiry.month, expiry.day, expiry.hour), (2035, 2, 7, 10))
        self.assertTrue(subscription["five_hour"]["known"])
        self.assertNotIn("nextResetTime", subscription["metadata_source"])

    def test_zcode_auto_renew_does_not_use_next_renew_as_expiration(self):
        collector = ZCodeQuotaCollector(Path("unused-zcode-root"))
        result = collector._parse_subscription_details(
            {
                "code": 200,
                "success": True,
                "data": [active_coding_plan(valid=None)],
            },
            "https://open.bigmodel.cn/api/biz/subscription/list",
        )

        self.assertEqual(result["plan_name"], "Synthetic Example Coding Plan")
        self.assertEqual(result["plan_spec"], "月付")
        self.assertIsNone(result["expires_at"])

    def test_quota_window_reset_is_never_used_as_subscription_expiration(self):
        quota = {
            "code": 200,
            "success": True,
            "data": {
                "level": "pro",
                "limits": [
                    {
                        "type": "TOKENS_LIMIT",
                        "unit": 3,
                        "number": 5,
                        "percentage": 50,
                        "nextResetTime": int((datetime.now().timestamp() + 600) * 1000),
                    }
                ],
            },
        }
        collector = FixedSourceQuotaCollector(
            quota,
            {"code": 200, "success": True, "data": []},
        )

        subscription = collector.collect()

        self.assertIsNotNone(subscription["five_hour"]["reset_at"])
        self.assertEqual(subscription["plan_spec"], "Pro")
        self.assertIsNone(subscription["expires_at"])
        self.assertIn("data.level", subscription["metadata_source"])


if __name__ == "__main__":
    unittest.main()
