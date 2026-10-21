from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_dashboard.subscriptions import SubscriptionProfiles


class SubscriptionProfileTests(unittest.TestCase):
    def test_retry_loads_delayed_metadata_and_retains_it_during_read_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "subscriptions.json"
            profiles = SubscriptionProfiles(path)
            self.assertFalse(profiles.reload())
            SubscriptionProfiles(path).update("codex", {"plan_name": "Synthetic saved plan"})
            self.assertTrue(profiles.reload())
            self.assertEqual(profiles.get_all()["codex"]["plan_name"], "Synthetic saved plan")
            original = path.read_bytes()
            with patch.object(Path, "read_text", side_effect=PermissionError(13, "synthetic failure")):
                self.assertFalse(profiles.reload())
                self.assertEqual(profiles.get_all()["codex"]["plan_name"], "Synthetic saved plan")
                with self.assertRaises(OSError):
                    profiles.update("zcode", {})
            self.assertEqual(path.read_bytes(), original)

    def test_partial_manual_fields_override_only_those_fields_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "subscriptions.json"
            profiles = SubscriptionProfiles(path)
            profiles.update("codex", {"plan_spec": "月付 / 自订额度档位", "expires_at": "2026-11-15"})
            profiles = SubscriptionProfiles(path)
            original = {"provider": "codex", "subscription": {"plan_name": "ChatGPT Pro", "plan_spec": "auto",
                         "weekly": {"known": True, "remaining_percent": 52}}}
            merged = profiles.apply(original)
            self.assertEqual(merged["subscription"]["plan_name"], "ChatGPT Pro")
            self.assertEqual(merged["subscription"]["plan_spec"], "月付 / 自订额度档位")
            self.assertEqual(merged["subscription"]["expires_at"], "2026-11-15")
            self.assertEqual(merged["subscription"]["metadata_sources"]["expires_at"], "manual")
            self.assertEqual(merged["subscription"]["weekly"]["remaining_percent"], 52)
            self.assertEqual(original["subscription"]["plan_spec"], "auto")

    def test_clearing_manual_values_restores_automatic_values(self):
        profiles = SubscriptionProfiles()
        profiles.update("zcode", {"plan_name": "Manual Max"})
        profiles.update("zcode", {})
        result = profiles.apply({"provider": "zcode", "subscription": {"plan_name": "Official Pro"}})
        self.assertEqual(result["subscription"]["plan_name"], "Official Pro")
        self.assertEqual(result["subscription"]["metadata_sources"]["plan_name"], "automatic")

    def test_invalid_update_does_not_overwrite_saved_profile(self):
        profiles = SubscriptionProfiles()
        profiles.update("zcode", {"plan_name": "Pro"})
        for provider, value in (("zcode", {"expires_at": "2026-02-30"}), ("other", {}),
                                ("zcode", {"plan_name": "x" * 81}), ("zcode", {"api_key": "not accepted"})):
            with self.assertRaises(ValueError):
                profiles.update(provider, value)
        self.assertEqual(profiles.get_all()["zcode"]["plan_name"], "Pro")


if __name__ == "__main__":
    unittest.main()
