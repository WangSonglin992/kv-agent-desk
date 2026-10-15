import json
import unittest
from unittest.mock import patch

from agent_dashboard.accounts.deepseek import DeepSeekAccounts


def balance_payload(value: str = "110.00", currency: str = "CNY") -> dict:
    return {
        "is_available": True,
        "balance_infos": [
            {
                "currency": currency,
                "total_balance": value,
                "granted_balance": "10.00",
                "topped_up_balance": "100.00",
            }
        ],
    }


class DeepSeekAccountsTests(unittest.TestCase):
    def test_reads_only_two_configured_secrets_from_official_balance_endpoint(self):
        secrets = {
            "DeepSeek_public": "lab-test-key",
            "DeepSeek_personal": "personal-test-key",
        }
        with (
            patch("agent_dashboard.accounts.deepseek.read_secret_env", side_effect=secrets.get) as read_secret,
            patch("agent_dashboard.accounts.deepseek.get_json", side_effect=[balance_payload(), balance_payload("25.50", "USD")]) as get_json,
        ):
            result = DeepSeekAccounts().collect()

        self.assertEqual([item["id"] for item in result], ["deepseek_lab", "deepseek_personal"])
        self.assertEqual([item["name"] for item in result], ["课题组 DeepSeek", "个人 DeepSeek"])
        self.assertEqual([(item["value"], item["currency"]) for item in result], [("110.00", "CNY"), ("25.50", "USD")])
        self.assertTrue(all(item["known"] and not item["stale"] for item in result))
        self.assertTrue(all(item["remaining_percent"] is None and item["reset_at"] is None for item in result))
        self.assertTrue(all(item["source"] == "https://api.deepseek.com/user/balance" for item in result))
        self.assertEqual(read_secret.call_args_list[0].args, ("DeepSeek_public",))
        self.assertEqual(read_secret.call_args_list[1].args, ("DeepSeek_personal",))
        self.assertEqual(get_json.call_args_list[0].args, ("https://api.deepseek.com/user/balance", "lab-test-key"))
        self.assertEqual(get_json.call_args_list[0].kwargs, {"header": "Authorization", "bearer": True, "timeout": 5})
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("lab-test-key", serialized)
        self.assertNotIn("personal-test-key", serialized)

    def test_enforces_per_account_sixty_second_refresh_limit(self):
        with (
            patch("agent_dashboard.accounts.deepseek.read_secret_env", side_effect=lambda name: f"key-for-{name}"),
            patch("agent_dashboard.accounts.deepseek.get_json", return_value=balance_payload()) as get_json,
            patch("agent_dashboard.accounts.deepseek.time.monotonic", side_effect=[100.0, 159.0, 161.0]),
        ):
            collector = DeepSeekAccounts()
            first = collector.collect()
            second = collector.collect()
            third = collector.collect()

        self.assertEqual(get_json.call_count, 4)
        self.assertEqual(second, first)
        self.assertEqual(third[0]["value"], "110.00")
        self.assertFalse(third[0]["stale"])

    def test_failure_keeps_last_known_value_and_hides_exception_details(self):
        responses = [
            balance_payload("77.25"),
            balance_payload("18.00", "USD"),
            RuntimeError("raw headers contain personal-test-key"),
            RuntimeError("response body contains lab-test-key"),
        ]
        with (
            patch("agent_dashboard.accounts.deepseek.read_secret_env", side_effect=lambda name: f"key-for-{name}"),
            patch("agent_dashboard.accounts.deepseek.get_json", side_effect=responses),
            patch("agent_dashboard.accounts.deepseek.time.monotonic", side_effect=[100.0, 161.0]),
        ):
            collector = DeepSeekAccounts()
            initial = collector.collect()
            failed = collector.collect()

        self.assertEqual([item["value"] for item in failed], ["77.25", "18.00"])
        self.assertEqual([item["currency"] for item in failed], ["CNY", "USD"])
        self.assertTrue(all(item["known"] and item["stale"] for item in failed))
        self.assertEqual([item["updated_at"] for item in failed], [item["updated_at"] for item in initial])
        serialized = json.dumps(failed, ensure_ascii=False)
        self.assertNotIn("raw headers", serialized)
        self.assertNotIn("personal-test-key", serialized)
        self.assertNotIn("lab-test-key", serialized)

    def test_missing_key_is_unknown_and_does_not_call_upstream(self):
        with (
            patch("agent_dashboard.accounts.deepseek.read_secret_env", side_effect=lambda name: "lab-test-key" if name == "DeepSeek_public" else None),
            patch("agent_dashboard.accounts.deepseek.get_json", return_value=balance_payload()) as get_json,
        ):
            result = DeepSeekAccounts().collect()

        self.assertTrue(result[0]["known"])
        self.assertFalse(result[1]["known"])
        self.assertTrue(result[1]["stale"])
        self.assertIsNone(result[1]["value"])
        self.assertEqual(get_json.call_count, 1)
        self.assertNotIn("DeepSeek_personal", json.dumps(result, ensure_ascii=False))

    def test_prefers_cny_if_api_returns_multiple_supported_currencies(self):
        payload = {
            "is_available": False,
            "balance_infos": [
                {"currency": "USD", "total_balance": "3.14"},
                {"currency": "CNY", "total_balance": "20.00"},
            ],
        }
        with (
            patch("agent_dashboard.accounts.deepseek.read_secret_env", side_effect=lambda name: "key"),
            patch("agent_dashboard.accounts.deepseek.get_json", return_value=payload),
        ):
            result = DeepSeekAccounts().collect()

        self.assertEqual(result[0]["value"], "20.00")
        self.assertEqual(result[0]["currency"], "CNY")
        self.assertIn("余额不足", result[0]["detail"])


if __name__ == "__main__":
    unittest.main()
