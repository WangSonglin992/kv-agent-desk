from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent_dashboard.app as app_module
from agent_dashboard.app import Dashboard, parser


class InstallationEntryTests(unittest.TestCase):
    def test_legacy_shortcut_and_run_directories_migrate_to_shared_user_store(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = root / "appdata" / "KV-Agent-Desk"
            destination = root / "profile" / ".kv-agent-desk"
            args = parser().parse_args(["--background", "--state-dir", str(legacy)])
            with patch.object(app_module.sys, "platform", "win32"), \
                    patch.object(app_module, "state_directory", return_value=destination), \
                    patch.object(app_module, "legacy_state_directories", return_value=[legacy]), \
                    patch("agent_dashboard.storage_migration.migrate_legacy_state") as migrate:
                app_module._prepare_state_directory(args)
            migrate.assert_called_once_with(destination, [legacy])
            self.assertEqual(args.state_dir, destination)

    def test_custom_and_demo_directories_are_not_migrated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for flags in ([], ["--demo"], ["--portable"]):
                args = parser().parse_args([*flags, "--state-dir", str(root / "custom")])
                with patch.object(app_module.sys, "platform", "win32"), \
                        patch.object(app_module, "state_directory", return_value=root / "canonical"), \
                        patch.object(app_module, "legacy_state_directories", return_value=[root / "legacy"]), \
                        patch("agent_dashboard.storage_migration.migrate_legacy_state") as migrate:
                    app_module._prepare_state_directory(args)
                migrate.assert_not_called()
                self.assertEqual(args.state_dir, root / "custom")

    def test_explicit_install_cannot_silently_switch_a_custom_data_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            args = parser().parse_args(["--install", "--state-dir", folder])
            with patch.object(app_module.sys, "platform", "win32"), \
                    patch.object(app_module.sys, "frozen", True, create=True), \
                    patch("agent_dashboard.installation.install_current") as install:
                with self.assertRaises(ValueError):
                    app_module._prepare_installed_launch(args)
            install.assert_not_called()

    def test_source_and_demo_modes_do_not_install_or_enable_program_updates(self):
        for arguments in (["--demo", "--no-browser"], ["--portable", "--no-browser"]):
            with tempfile.TemporaryDirectory() as folder:
                args = parser().parse_args([*arguments, "--state-dir", folder])
                with patch.object(app_module.sys, "frozen", False, create=True), \
                        patch("agent_dashboard.installation.install_current") as install:
                    self.assertFalse(app_module._prepare_installed_launch(args))
                    app = Dashboard(args)
                    app.configure_updates()
                self.assertFalse(app.update_status()["supported"])
                install.assert_not_called()

    def test_downloaded_build_hands_off_before_single_instance_check(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app_module, "_default_native_install_mode", return_value=True), \
                    patch("agent_dashboard.installation.is_installed", return_value=False), \
                    patch("agent_dashboard.installation.install_current") as install, \
                    patch.object(app_module, "existing_server") as existing:
                self.assertEqual(app_module.main(["--state-dir", folder, "--background"]), 0)
            install.assert_called_once_with(Path(app_module.sys.executable), state_dir=Path(folder), background=True)
            existing.assert_not_called()

    def test_installed_launch_repairs_shortcut_without_reinstallation(self):
        with tempfile.TemporaryDirectory() as folder:
            args = parser().parse_args(["--state-dir", folder])
            with patch.object(app_module, "_default_native_install_mode", return_value=True), \
                    patch("agent_dashboard.installation.is_installed", return_value=True), \
                    patch("agent_dashboard.installation.install_current") as install, \
                    patch("agent_dashboard.installation.ensure_desktop_shortcut") as shortcut:
                self.assertFalse(app_module._prepare_installed_launch(args))
            install.assert_not_called()
            shortcut.assert_called_once_with(Path(app_module.sys.executable))

    def test_failed_update_launch_does_not_stop_the_current_dashboard(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Dashboard(parser().parse_args(["--demo", "--state-dir", folder]))
            candidate = Path(folder) / "KV-Agent-Desk.exe"
            candidate.write_bytes(b"synthetic executable")
            with patch.object(app, "persist_frame") as persist, \
                    patch.object(app_module.subprocess, "Popen", side_effect=OSError("synthetic start failure")):
                with self.assertRaises(OSError):
                    app._apply_update(candidate)
            persist.assert_called_once_with(force=True)
            self.assertFalse(app.stop.is_set())

    def test_update_handoff_keeps_running_until_the_installer_requests_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Dashboard(parser().parse_args(["--demo", "--state-dir", folder]))
            candidate = Path(folder) / "KV-Agent-Desk.exe"
            candidate.write_bytes(b"synthetic executable")
            with patch.object(app, "persist_frame") as persist, \
                    patch.object(app_module.subprocess, "Popen") as launch:
                app._apply_update(candidate)
            persist.assert_called_once_with(force=True)
            self.assertEqual(launch.call_args.args[0], [str(candidate), "--install", "--background"])
            self.assertFalse(app.stop.is_set())


if __name__ == "__main__":
    unittest.main()
