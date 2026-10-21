import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_dashboard.paths import local_app_data


class LocalAppDataTests(unittest.TestCase):
    def test_windows_known_folder_is_preferred_over_environment(self):
        known = Path("D:/profile/AppData/Local")
        with patch("agent_dashboard.paths._is_windows", return_value=True), \
             patch("agent_dashboard.paths._windows_known_folder_local_app_data", return_value=known), \
             patch.dict(os.environ, {"LOCALAPPDATA": "relative\\path"}, clear=True):
            self.assertEqual(local_app_data(), known)

    def test_windows_fallback_accepts_only_absolute_expanded_environment_path(self):
        home = Path("D:/profile")
        invalid_values = (None, "relative\\Local", "%USERPROFILE%\\AppData\\Local", "$HOME/AppData/Local", "${USERPROFILE}/Local")
        for value in invalid_values:
            environment = {} if value is None else {"LOCALAPPDATA": value}
            with self.subTest(value=value), \
                 patch("agent_dashboard.paths._is_windows", return_value=True), \
                 patch("agent_dashboard.paths._windows_known_folder_local_app_data", side_effect=OSError("known folder unavailable")), \
                 patch("agent_dashboard.paths.Path.home", return_value=home), \
                 patch.dict(os.environ, environment, clear=True):
                self.assertEqual(local_app_data(), home / "AppData" / "Local")

    def test_windows_environment_fallback_is_used_when_known_folder_fails(self):
        environment_path = Path("D:/account/local-data")
        with patch("agent_dashboard.paths._is_windows", return_value=True), \
             patch("agent_dashboard.paths._windows_known_folder_local_app_data", return_value=None), \
             patch.dict(os.environ, {"LOCALAPPDATA": str(environment_path)}, clear=True):
            self.assertEqual(local_app_data(), environment_path)

    def test_known_folder_result_is_independent_of_current_working_directory(self):
        known = Path("D:/profile/AppData/Local")
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second, \
             patch("agent_dashboard.paths._is_windows", return_value=True), \
             patch("agent_dashboard.paths._windows_known_folder_local_app_data", return_value=known):
            original = Path.cwd()
            try:
                os.chdir(first)
                result1 = local_app_data()
                os.chdir(second)
                result2 = local_app_data()
            finally:
                os.chdir(original)
        self.assertEqual(result1, known)
        self.assertEqual(result2, known)

    def test_non_windows_preserves_environment_or_home_behavior(self):
        with patch("agent_dashboard.paths._is_windows", return_value=False), \
             patch.dict(os.environ, {"LOCALAPPDATA": "relative-cache"}, clear=True):
            self.assertEqual(local_app_data(), Path("relative-cache"))
        home = Path("/home/example")
        with patch("agent_dashboard.paths._is_windows", return_value=False), \
             patch("agent_dashboard.paths.Path.home", return_value=home), \
             patch.dict(os.environ, {}, clear=True):
            self.assertEqual(local_app_data(), home)

    def test_lookup_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "not-created"
            with patch("agent_dashboard.paths._is_windows", return_value=True), \
                 patch("agent_dashboard.paths._windows_known_folder_local_app_data", return_value=missing):
                self.assertEqual(local_app_data(), missing)
            self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
