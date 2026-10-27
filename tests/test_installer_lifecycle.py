import hashlib
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent_dashboard.app as app_module
import agent_dashboard.install_location as install_location
import agent_dashboard.installer_lifecycle as lifecycle
import agent_dashboard.installation as installation
import agent_dashboard.settings as settings_module
from install_test_safety import allow_synthetic_root, isolate_real_user_state


class FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1
    REG_DWORD = 4

    class Handle:
        def __init__(self, registry, key):
            self.registry = registry
            self.key = key

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def __init__(self):
        self.values = {}

    def OpenKey(self, _root, key, _reserved=0, _access=0):
        if key not in self.values:
            raise FileNotFoundError()
        return self.Handle(self, key)

    def CreateKeyEx(self, _root, key, _reserved, _access):
        self.values.setdefault(key, {})
        return self.Handle(self, key)

    def QueryValueEx(self, handle, name):
        try:
            return self.values[handle.key][name]
        except KeyError:
            raise FileNotFoundError() from None

    def SetValueEx(self, handle, name, _reserved, value_type, value):
        self.values.setdefault(handle.key, {})[name] = (value, value_type)

    def DeleteValue(self, handle, name):
        try:
            del self.values[handle.key][name]
        except KeyError:
            raise FileNotFoundError() from None

    def DeleteKey(self, _root, key):
        if key not in self.values:
            raise FileNotFoundError()
        if self.values[key]:
            raise OSError("synthetic registry key is not empty")
        del self.values[key]


class InstallerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        allow_synthetic_root(self, self.root)
        isolate_real_user_state(self)
        self.home = self.root / "synthetic profile"
        self.home.mkdir()
        self.state_dir = self.home / ".kv-agent-desk"
        self.state_dir.mkdir()
        self.registry = FakeRegistry()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(self.folder.cleanup)

        self.stack.enter_context(patch.object(lifecycle.sys, "platform", "win32"))
        self.stack.enter_context(patch.object(lifecycle.sys, "frozen", True, create=True))
        self.stack.enter_context(patch.object(install_location, "_winreg", self.registry))
        self.stack.enter_context(patch.object(settings_module, "_winreg", self.registry))
        self.stack.enter_context(patch.object(install_location, "user_state_directory", return_value=self.state_dir))
        self.stack.enter_context(patch.object(lifecycle, "user_state_directory", return_value=self.state_dir))
        self.stack.enter_context(patch.object(app_module, "state_directory", return_value=self.state_dir))
        self.stack.enter_context(patch.object(
            installation, "legacy_installation_executable",
            return_value=self.root / "legacy" / "KV-Agent-Desk.exe",
        ))

    def executable(self, install_dir: Path) -> Path:
        install_dir.mkdir(parents=True, exist_ok=True)
        path = install_dir / "KV-Agent-Desk.exe"
        path.write_bytes(b"synthetic installer executable")
        self.stack.enter_context(patch.object(lifecycle.sys, "executable", str(path)))
        return path

    def test_prepare_backs_up_before_graceful_handoff(self):
        calls = []
        backup = self.state_dir / "backups" / "synthetic"
        with patch.object(app_module, "_prepare_state_directory") as migrate, \
                patch.object(installation, "_backup_user_data",
                             side_effect=lambda state: calls.append(("backup", state)) or backup), \
                patch.object(installation, "_handoff_existing_server",
                             side_effect=lambda: calls.append(("handoff",)) or True):
            result = lifecycle._prepare_installer()

        self.assertEqual(result, backup)
        migrate.assert_called_once()
        self.assertEqual(calls, [("backup", self.state_dir), ("handoff",)])

    def test_prepare_backup_failure_does_not_handoff_old_app(self):
        with patch.object(app_module, "_prepare_state_directory"), \
                patch.object(installation, "_backup_user_data", side_effect=OSError("synthetic backup failure")), \
                patch.object(installation, "_handoff_existing_server") as handoff:
            with self.assertRaises(OSError):
                lifecycle._prepare_installer()
        handoff.assert_not_called()

    def test_lifecycle_mode_rejects_mixed_normal_launch_options(self):
        with patch.object(lifecycle, "_prepare_installer") as prepare:
            with self.assertRaises(SystemExit):
                lifecycle.handle_installer_lifecycle(["--installer-prepare", "--portable"])
        prepare.assert_not_called()

    def test_ci_diagnostic_contains_only_sanitized_failure_metadata(self):
        message = "owned Setup AppId does not match the current installation"
        diagnostic = self.root / lifecycle._DIAGNOSTIC_FILENAME
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_TEMP": str(self.root)}), \
                patch.object(lifecycle, "_uninstall_installer", side_effect=ValueError(message)):
            result = lifecycle.handle_installer_lifecycle(["--installer-uninstall"])

        self.assertEqual(result, 1)
        payload = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual(payload["mode"], "uninstall")
        self.assertEqual(payload["exception_type"], "ValueError")
        self.assertEqual(payload["reason"], "appid_mismatch")
        self.assertIn("handle_installer_lifecycle", payload["traceback_functions"])
        serialized = json.dumps(payload)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn(message, serialized)
        self.assertEqual(set(payload), {"mode", "exception_type", "traceback_functions", "reason"})

    def test_ci_diagnostic_is_not_written_outside_github_actions(self):
        diagnostic = self.root / lifecycle._DIAGNOSTIC_FILENAME
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "RUNNER_TEMP": str(self.root)}), \
                patch.object(lifecycle, "_uninstall_installer", side_effect=ValueError("synthetic failure")):
            result = lifecycle.handle_installer_lifecycle(["--installer-uninstall"])

        self.assertEqual(result, 1)
        self.assertFalse(diagnostic.exists())

    def test_shortcut_targets_are_resolved_and_deduplicated_for_synthetic_aliases(self):
        install_dir = self.root / "synthetic install"
        executable = self.executable(install_dir)
        alias_component = self.root / "synthetic alias"
        alias_component.mkdir()
        alias_executable = alias_component / ".." / install_dir.name / "KV-Agent-Desk.exe"
        previous = install_location.Installation(install_dir, registered=True)

        with patch.object(installation, "legacy_installation_executable", return_value=alias_executable):
            targets = lifecycle._owned_shortcut_targets(alias_executable, previous)

        self.assertEqual(targets, [executable.resolve()])
        self.assertTrue(targets[0].is_absolute())
        self.assertNotIn("..", targets[0].parts)

    def test_finish_preserves_settings_bytes_when_auto_start_already_matches(self):
        install_dir = self.root / "registered install"
        executable = self.executable(install_dir)
        settings_file = self.state_dir / "settings.json"
        original = b'{"auto_start": true, "brightness": 73}\r\n'
        settings_file.write_bytes(original)
        original_hash = hashlib.sha256(original).hexdigest()

        with patch.object(installation, "ensure_desktop_shortcut") as ensure, \
                patch.object(installation, "remove_owned_desktop_shortcut") as remove, \
                patch.object(lifecycle, "set_autostart") as set_run:
            lifecycle._finish_installer(install_dir, True, True)

        ensure.assert_called_once_with(executable.resolve())
        remove.assert_not_called()
        set_run.assert_called_once_with(
            True,
            [str(executable.resolve()), "--background", "--state-dir", str(self.state_dir.resolve())],
        )
        self.assertEqual(hashlib.sha256(settings_file.read_bytes()).hexdigest(), original_hash)
        record = install_location.read_installation()
        self.assertEqual(record.directory, install_dir.resolve())
        self.assertTrue(record.desktop_shortcut)
        self.assertTrue(record.installer_managed)

    def test_finish_shortcut_opt_out_removes_only_registered_old_and_current_targets(self):
        old_dir = self.root / "old registered install"
        new_dir = self.root / "new registered install"
        executable = self.executable(new_dir)
        install_location.register_installer_installation(
            old_dir, desktop_shortcut=True, state_dir=self.state_dir
        )
        settings_file = self.state_dir / "settings.json"
        settings_file.write_bytes(b'{"auto_start": false}\n')

        with patch.object(installation, "ensure_desktop_shortcut") as ensure, \
                patch.object(installation, "remove_owned_desktop_shortcut") as remove, \
                patch.object(lifecycle, "set_autostart") as set_run:
            lifecycle._finish_installer(new_dir, False, False)

        ensure.assert_not_called()
        targets = remove.call_args.args[0]
        resolved = {Path(path).resolve() for path in targets}
        self.assertIn(executable.resolve(), resolved)
        self.assertIn((old_dir / "KV-Agent-Desk.exe").resolve(), resolved)
        self.assertNotIn((self.root / "someone-else" / "KV-Agent-Desk.exe").resolve(), resolved)
        set_run.assert_called_once_with(
            False,
            [str(executable.resolve()), "--background", "--state-dir", str(self.state_dir.resolve())],
        )
        self.assertFalse(install_location.read_installation().desktop_shortcut)

    def test_finish_rejects_executable_outside_selected_directory_before_writes(self):
        selected = self.root / "selected install"
        elsewhere = self.root / "other install"
        self.executable(elsewhere)
        settings_file = self.state_dir / "settings.json"
        settings_file.write_bytes(b'{"auto_start": true}\n')
        before = settings_file.read_bytes()

        with patch.object(installation, "ensure_desktop_shortcut") as ensure, \
                patch.object(lifecycle, "set_autostart") as set_run:
            with self.assertRaisesRegex(ValueError, "does not match"):
                lifecycle._finish_installer(selected, False, True)

        ensure.assert_not_called()
        set_run.assert_not_called()
        self.assertEqual(settings_file.read_bytes(), before)
        self.assertFalse(install_location.installation_ini_path(self.state_dir).exists())

    def test_uninstall_clears_only_owned_registration_and_keeps_user_files(self):
        install_dir = self.root / "owned install"
        self.executable(install_dir)
        install_location.register_installer_installation(
            install_dir, desktop_shortcut=False, state_dir=self.state_dir
        )
        self.registry.values[install_location.OWNED_UNINSTALL_SUBKEY] = {
            install_location.UNINSTALL_LOCATION_VALUE: (str(install_dir), self.registry.REG_SZ),
            install_location.UNINSTALL_DISPLAY_VERSION_VALUE: ("0.7.0", self.registry.REG_SZ),
        }
        run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        self.registry.values[run_key] = {
            "KVAgentDesk": ("synthetic owned run command", self.registry.REG_SZ),
        }
        settings_file = self.state_dir / "settings.json"
        settings_bytes = b'{"auto_start": true, "rotation": 180}\n'
        settings_file.write_bytes(settings_bytes)
        retained = self.state_dir / "history-sentinel.json"
        retained.write_bytes(b"synthetic retained history")

        with patch.object(installation, "_handoff_existing_server") as handoff, \
                patch.object(installation, "remove_owned_desktop_shortcut") as remove, \
                patch.object(installation.subprocess, "run",
                             side_effect=AssertionError("uninstall must not launch a process")), \
                patch.object(installation.subprocess, "Popen",
                             side_effect=AssertionError("uninstall must not launch a process")):
            lifecycle._uninstall_installer()

        handoff.assert_called_once_with()
        remove.assert_not_called()
        self.assertNotIn("KVAgentDesk", self.registry.values[run_key])
        self.assertFalse(install_location.installation_ini_path(self.state_dir).exists())
        self.assertNotIn(install_location.REGISTRY_SUBKEY, self.registry.values)
        self.assertEqual(settings_file.read_bytes(), settings_bytes)
        self.assertEqual(retained.read_bytes(), b"synthetic retained history")
        self.assertTrue(install_dir.is_dir())

    def test_uninstall_refuses_mismatched_appid_path_before_stopping_or_cleaning(self):
        install_dir = self.root / "owned install"
        self.executable(install_dir)
        install_location.register_installer_installation(
            install_dir, desktop_shortcut=True, state_dir=self.state_dir
        )
        self.registry.values[install_location.OWNED_UNINSTALL_SUBKEY] = {
            install_location.UNINSTALL_LOCATION_VALUE: (str(self.root / "other install"), self.registry.REG_SZ),
        }
        with patch.object(installation, "_handoff_existing_server") as handoff, \
                patch.object(installation, "remove_owned_desktop_shortcut") as remove:
            with self.assertRaisesRegex(ValueError, "AppId"):
                lifecycle._uninstall_installer()
        handoff.assert_not_called()
        remove.assert_not_called()
        self.assertTrue(install_location.installation_ini_path(self.state_dir).is_file())


if __name__ == "__main__":
    unittest.main()
