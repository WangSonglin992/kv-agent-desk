import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_dashboard.adapters.antigravity import AntigravityAdapter, _RPC_PATH


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
                        "label": "Claude Opus exhausted",
                        "modelOrAlias": {"model": "MODEL_OPUS_EXHAUSTED"},
                        "quotaInfo": {"resetTime": RESET},
                    },
                    {
                        "label": "Claude Sonnet invalid window",
                        "modelOrAlias": {"model": "MODEL_SONNET_INVALID"},
                        "quotaInfo": {"resetTime": "not-an-iso-time"},
                    },
                ]
            },
        }
    }


class AntigravityAdapterTests(unittest.TestCase):
    def test_maps_actual_per_model_quota_without_guessing_window_type(self):
        result = AntigravityAdapter._parse_user_status(status_payload())

        self.assertEqual(result["provider"], "antigravity")
        self.assertTrue(result["available"])
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["state_label"], "已连接")
        self.assertFalse(result["stale"])
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
        self.assertNotIn("plan_spec", result["subscription"])
        self.assertNotIn("expires_at", result["subscription"])
        self.assertNotIn("private@example.invalid", json.dumps(result))

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

    def test_collects_quota_and_failure_retains_it_as_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Antigravity.exe").write_bytes(b"installed")
            adapter = AntigravityAdapter(root)
            with (
                patch.object(adapter, "_discover_runtime", return_value={"app_running": True, "servers": []}),
                patch.object(adapter, "_fetch_user_status", side_effect=[status_payload(), RuntimeError("private error")]),
                patch("agent_dashboard.adapters.antigravity.time.monotonic", side_effect=[100.0, 161.0]),
            ):
                first = adapter.collect()
                failed = adapter.collect()

        self.assertEqual(first["subscription"]["quota_windows"], failed["subscription"]["quota_windows"])
        self.assertTrue(first["subscription"]["quota_windows"][0]["known"])
        self.assertTrue(failed["stale"])
        self.assertNotIn("private error", failed["detail"])

    def test_throttles_requests_for_sixty_seconds_and_returns_a_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Antigravity.exe").write_bytes(b"installed")
            adapter = AntigravityAdapter(root)
            with (
                patch.object(adapter, "_discover_runtime", return_value={"app_running": True, "servers": []}) as discover,
                patch.object(adapter, "_fetch_user_status", return_value=status_payload()) as fetch,
                patch("agent_dashboard.adapters.antigravity.time.monotonic", side_effect=[100.0, 159.0, 161.0]),
            ):
                first = adapter.collect()
                first["subscription"]["quota_windows"].clear()
                second = adapter.collect()
                third = adapter.collect()

        self.assertEqual(discover.call_count, 2)
        self.assertEqual(fetch.call_count, 2)
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
