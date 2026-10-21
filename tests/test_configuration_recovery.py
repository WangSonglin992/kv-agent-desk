import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_dashboard.app import Dashboard, parser, startup_command
from agent_dashboard.settings import AppSettings, set_autostart
from agent_dashboard.ui_profiles import UIProfiles


class StartupRestoreTests(unittest.TestCase):
    def test_background_does_not_change_run_key_from_unread_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "settings.json"
            AppSettings(path).update({"ui_id": "paper", "auto_start": False})
            original_open = Path.open

            def blocked_open(p, *args, **kwargs):
                if p == path:
                    raise PermissionError(13, "synthetic login-time read failure")
                return original_open(p, *args, **kwargs)

            with patch("agent_dashboard.app.set_autostart") as register, \
                    patch("agent_dashboard.app.startup_command", return_value=["synthetic.exe", "--background"]):
                with patch.object(Path, "open", blocked_open):
                    dashboard = Dashboard(parser().parse_args(["--background", "--state-dir", folder]))
                    dashboard.desktop_mode = True
                    self.assertFalse(dashboard.restore_startup_registration())
                    register.assert_not_called()
                self.assertTrue(dashboard.retry_saved_configuration(force=True))
                register.assert_called_once_with(False, ["synthetic.exe", "--background"])

    def test_missing_subscription_file_does_not_end_startup_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            AppSettings(root / "settings.json").update({"ui_id": "paper"})
            dashboard = Dashboard(parser().parse_args(["--background", "--state-dir", folder]))
            self.assertTrue(dashboard.retry_saved_configuration(force=True))
            self.assertTrue(dashboard._configuration_pending)
            (root / "subscriptions.json").write_text(json.dumps({"profiles": {"codex": {"plan_name": "Late plan"}}}), encoding="utf-8")
            self.assertTrue(dashboard.retry_saved_configuration(force=True))
            self.assertFalse(dashboard._configuration_pending)
            self.assertEqual(dashboard.profiles.get_all()["codex"]["plan_name"], "Late plan")

    def test_background_uses_saved_settings_and_all_style_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profiles = UIProfiles(root / "ui-profiles")
            first = profiles.get("aurora")
            first.update(id="saved_aurora", name="Saved Aurora")
            first["elements"] = {"agent.codex.name": {"dx": 17, "font_size": 31}}
            profiles.import_profile(first)
            second = profiles.get("paper")
            second.update(id="saved_paper", name="Saved Paper")
            profiles.import_profile(second)
            AppSettings(root / "settings.json").update({"ui_id": first["id"], "brightness": 42, "rotation": 0})
            args = parser().parse_args(["--background", "--state-dir", folder])
            dashboard = Dashboard(args)
            self.assertTrue(dashboard.retry_saved_configuration(force=True))
            self.assertEqual(dashboard.ui_id, "saved_aurora")
            self.assertEqual(dashboard.brightness, 42)
            self.assertEqual(dashboard.rotation, 0)
            self.assertEqual(dashboard.current_profile()["elements"], first["elements"])
            self.assertEqual(sum(not p["builtin"] for p in dashboard.ui_profiles.list()), 2)

    def test_missing_style_at_login_does_not_replace_saved_choice_and_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "settings.json"
            AppSettings(path).update({"ui_id": "late_style", "brightness": 63})
            original = path.read_bytes()
            dashboard = Dashboard(parser().parse_args(["--background", "--state-dir", folder]))
            self.assertEqual(dashboard.ui_id, "classic")
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(dashboard.retry_saved_configuration(force=True))
            profile = dashboard.ui_profiles.get("paper")
            profile.update(id="late_style", name="Late style")
            dashboard.ui_profiles.import_profile(profile)
            self.assertTrue(dashboard.retry_saved_configuration(force=True))
            self.assertEqual(dashboard.ui_id, "late_style")
            self.assertEqual(dashboard.brightness, 63)
            self.assertEqual(path.read_bytes(), original)

    def test_settings_and_metadata_unavailable_at_login_are_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "settings.json"
            AppSettings(path).update({"ui_id": "paper", "brightness": 57})
            metadata_path = root / "subscriptions.json"
            metadata_path.write_text(json.dumps({"profiles": {"codex": {"plan_name": "Synthetic plan"}}}), encoding="utf-8")
            original = {p: p.read_bytes() for p in (path, metadata_path)}
            original_open = Path.open

            def blocked_open(p, *args, **kwargs):
                if p in original:
                    raise PermissionError(13, "synthetic login-time read failure")
                return original_open(p, *args, **kwargs)

            with patch.object(Path, "open", blocked_open):
                dashboard = Dashboard(parser().parse_args(["--background", "--state-dir", folder]))
                self.assertEqual(dashboard.ui_id, "classic")
                self.assertFalse(dashboard.retry_saved_configuration(force=True))
            self.assertTrue(dashboard.retry_saved_configuration(force=True))
            self.assertEqual(dashboard.ui_id, "paper")
            self.assertEqual(dashboard.brightness, 57)
            self.assertEqual(dashboard.profiles.get_all()["codex"]["plan_name"], "Synthetic plan")
            for p, raw in original.items():
                self.assertEqual(p.read_bytes(), raw)

    def test_run_command_pins_absolute_saved_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "KV-Agent-Desk.exe"
            executable.write_bytes(b"synthetic executable")
            with patch("agent_dashboard.app.sys.executable", str(executable)), \
                    patch("agent_dashboard.app.sys.frozen", True, create=True):
                command = startup_command(root / "data with spaces")
            self.assertEqual(command, [str(executable.resolve()), "--background", "--state-dir",
                                       str((root / "data with spaces").resolve())])


if __name__ == "__main__":
    unittest.main()
