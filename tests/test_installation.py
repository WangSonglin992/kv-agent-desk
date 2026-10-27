import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_dashboard import installation
import agent_dashboard.install_location as install_location_module
from agent_dashboard.app import APP_ID
from agent_dashboard import __version__
from install_test_safety import allow_synthetic_root, isolate_real_user_state


class FakeRegistrationRegistry:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
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

    def OpenKey(self, _root, name, _reserved=0, _access=0):
        if name not in self.values:
            raise FileNotFoundError()
        return self.Handle(self, name)

    def CreateKeyEx(self, _root, name, _reserved, _access):
        self.values.setdefault(name, {})
        return self.Handle(self, name)

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

    def DeleteKey(self, _root, name):
        if name not in self.values:
            raise FileNotFoundError()
        if self.values[name]:
            raise OSError("synthetic non-empty registry key")
        del self.values[name]


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.fixture_folder = tempfile.TemporaryDirectory()
        allow_synthetic_root(self, Path(self.fixture_folder.name))
        isolate_real_user_state(self)
        self.fixture_state = Path(self.fixture_folder.name) / "profile" / ".kv-agent-desk-fixture"
        self.location_registry = FakeRegistrationRegistry()
        self.location_windows_patch = patch.object(install_location_module, "_is_windows", return_value=True)
        self.location_registry_patch = patch.object(install_location_module, "_winreg", self.location_registry)
        self.location_windows_patch.start()
        self.location_registry_patch.start()
        self.user_state_patch = patch.object(installation, "user_state_directory", return_value=self.fixture_state)
        self.location_state_patch = patch.object(install_location_module, "user_state_directory", return_value=self.fixture_state)
        self.user_state_patch.start()
        self.location_state_patch.start()
        self.addCleanup(self.location_state_patch.stop)
        self.addCleanup(self.user_state_patch.stop)
        self.addCleanup(self.location_registry_patch.stop)
        self.addCleanup(self.location_windows_patch.stop)
        self.addCleanup(self.fixture_folder.cleanup)

    def test_installed_executable_is_fixed_under_local_app_data(self):
        with tempfile.TemporaryDirectory() as folder:
            local = Path(folder)
            target = local / "Programs" / "KV-Agent-Desk" / "KV-Agent-Desk.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"installed executable")
            source = local / "dist" / "KV-Agent-Desk.exe"
            source.parent.mkdir()
            source.write_bytes(b"same name, not installed")
            with patch.object(installation, "installation_directory", return_value=local / "Programs" / "KV-Agent-Desk"):
                self.assertEqual(installation.installed_executable(), target)
                self.assertTrue(installation.is_installed(target))
                self.assertFalse(installation.is_installed(source))

    def test_legacy_shortcut_cleanup_candidate_uses_the_local_app_program_folder(self):
        legacy_base = Path(self.fixture_folder.name) / "legacy-local-data"
        with patch.object(installation, "local_app_data", return_value=legacy_base):
            self.assertEqual(
                installation.legacy_installation_executable(),
                legacy_base / "Programs" / "KV-Agent-Desk" / "KV-Agent-Desk.exe",
            )

    def test_first_install_preserves_raw_state_and_copies_only_the_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / "local app data"
            state_dir = local / "KV-Agent-Desk"
            state_dir.mkdir(parents=True)
            state_bytes = b'{"ui_id":"my_custom_layout","rotation":180}\r\n'
            subscriptions_bytes = b'{ "provider": "synthetic", "plan": "local-only" }\n'
            profile_bytes = b'{"schema_version":1,"id":"my_custom_layout","elements":{}}\r\n'
            (state_dir / "settings.json").write_bytes(state_bytes)
            (state_dir / "subscriptions.json").write_bytes(subscriptions_bytes)
            profiles = state_dir / "ui-profiles"
            profiles.mkdir()
            (profiles / "my_custom_layout.json").write_bytes(profile_bytes)
            (profiles / "ignored.tmp").write_bytes(b"not a profile")

            source = root / "build output" / "KV-Agent-Desk.exe"
            source.parent.mkdir()
            source.write_bytes(b"synthetic frozen executable v1")
            def handoff_after_copy():
                self.assertEqual((local / "Programs" / "KV-Agent-Desk" / "KV-Agent-Desk.exe").read_bytes(),
                                 source.read_bytes())
                return True

            with (
                patch.object(installation, "installation_directory", return_value=local / "Programs" / "KV-Agent-Desk"),
                patch.object(installation, "_is_windows", return_value=True),
                patch.object(installation, "_handoff_existing_server", side_effect=handoff_after_copy) as handoff,
                patch.object(installation, "ensure_desktop_shortcut") as shortcut,
                patch.object(installation, "_launch") as launch,
            ):
                result = installation.install_current(source, state_dir=state_dir)

            target = local / "Programs" / "KV-Agent-Desk" / "KV-Agent-Desk.exe"
            self.assertEqual(result, target)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            handoff.assert_called_once_with()  # First install hands off only after the target is complete.
            shortcut.assert_called_once_with(target)
            launch.assert_called_once_with(target, background=False, state_dir=state_dir)
            self.assertEqual((state_dir / "settings.json").read_bytes(), state_bytes)
            self.assertEqual((state_dir / "subscriptions.json").read_bytes(), subscriptions_bytes)
            self.assertEqual((profiles / "my_custom_layout.json").read_bytes(), profile_bytes)
            backup_roots = list((state_dir / "backups").glob("install-*"))
            self.assertEqual(len(backup_roots), 1)
            backup = backup_roots[0]
            self.assertEqual((backup / "settings.json").read_bytes(), state_bytes)
            self.assertEqual((backup / "subscriptions.json").read_bytes(), subscriptions_bytes)
            self.assertEqual((backup / "ui-profiles" / "my_custom_layout.json").read_bytes(), profile_bytes)
            self.assertFalse((backup / "ui-profiles" / "ignored.tmp").exists())
            self.assertEqual(sorted(path.name for path in target.parent.iterdir()), [target.name])

    def test_update_stops_only_marked_server_then_replaces_and_launches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / "local"
            state_dir = local / "KV-Agent-Desk"
            state_dir.mkdir(parents=True)
            target = local / "Programs" / "KV-Agent-Desk" / "KV-Agent-Desk.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"old installed exe")
            source = root / "candidate.exe"
            source.write_bytes(b"new candidate exe")

            def graceful_stop():
                self.assertTrue(any(target.parent.glob(".KV-Agent-Desk.*.tmp")))
                self.assertEqual(target.read_bytes(), b"old installed exe")
                return True

            with (
                patch.object(installation, "installation_directory", return_value=local / "Programs" / "KV-Agent-Desk"),
                patch.object(installation, "_is_windows", return_value=True),
                patch.object(installation, "_handoff_existing_server", side_effect=graceful_stop) as handoff,
                patch.object(installation, "ensure_desktop_shortcut") as shortcut,
                patch.object(installation, "_launch") as launch,
            ):
                result = installation.install_current(source, state_dir=state_dir, background=True)

            self.assertEqual(result, target)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            handoff.assert_called_once_with()
            shortcut.assert_called_once_with(target)
            launch.assert_called_once_with(target, background=True, state_dir=state_dir)

    def test_same_hash_repairs_shortcut_and_shows_existing_app_without_replacing_or_relaunching(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / "local"
            target = local / "Programs" / "KV-Agent-Desk" / "KV-Agent-Desk.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"same synthetic executable")
            source = root / "candidate" / "KV-Agent-Desk.exe"
            source.parent.mkdir()
            source.write_bytes(target.read_bytes())
            before_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            with (
                patch.object(installation, "installation_directory", return_value=local / "Programs" / "KV-Agent-Desk"),
                patch.object(installation, "_is_windows", return_value=True),
                patch.object(installation, "_read_server_state", return_value={
                    "app": APP_ID, "version": __version__, "updates": {"supported": True}}),
                patch.object(installation, "_request_existing_server", return_value=True) as request_server,
                patch.object(installation, "_handoff_existing_server") as handoff,
                patch.object(installation, "ensure_desktop_shortcut") as shortcut,
                patch.object(installation, "_launch") as launch,
                patch.object(installation, "_replace_with_retry", side_effect=AssertionError("must not replace the executable")),
            ):
                result = installation.install_current(source, state_dir=local / "KV-Agent-Desk", background=False)

            self.assertEqual(result, target)
            request_server.assert_called_once_with(show=True)
            handoff.assert_not_called()
            shortcut.assert_called_once_with(target)
            launch.assert_not_called()
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before_hash)
            self.assertFalse((local / "KV-Agent-Desk" / "backups").exists())

    def test_same_hash_with_no_server_starts_installed_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / "local"
            target = local / "Programs" / "KV-Agent-Desk" / "KV-Agent-Desk.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"same synthetic executable")
            source = root / "candidate.exe"
            source.write_bytes(target.read_bytes())
            state_dir = local / "KV-Agent-Desk"
            with (
                patch.object(installation, "installation_directory", return_value=local / "Programs" / "KV-Agent-Desk"),
                patch.object(installation, "_is_windows", return_value=True),
                patch.object(installation, "_read_server_state", return_value=None),
                patch.object(installation, "ensure_desktop_shortcut"),
                patch.object(installation, "_handoff_existing_server", return_value=False) as handoff,
                patch.object(installation, "_launch") as launch,
            ):
                result = installation.install_current(source, state_dir=state_dir, background=False)

            self.assertEqual(result, target)
            handoff.assert_called_once_with()
            launch.assert_called_once_with(target, background=False, state_dir=state_dir)

    def test_same_hash_with_legacy_server_hands_off_before_launching_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / "local"
            target = local / "Programs" / "KV-Agent-Desk" / "KV-Agent-Desk.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"same synthetic executable")
            source = root / "candidate.exe"
            source.write_bytes(target.read_bytes())
            state_dir = local / "KV-Agent-Desk"
            with (
                patch.object(installation, "installation_directory", return_value=local / "Programs" / "KV-Agent-Desk"),
                patch.object(installation, "_is_windows", return_value=True),
                patch.object(installation, "_read_server_state", return_value={"app": APP_ID, "version": "0.5.0"}),
                patch.object(installation, "ensure_desktop_shortcut"),
                patch.object(installation, "_handoff_existing_server", return_value=True) as handoff,
                patch.object(installation, "_launch") as launch,
            ):
                result = installation.install_current(source, state_dir=state_dir, background=True)

            self.assertEqual(result, target)
            handoff.assert_called_once_with()
            launch.assert_called_once_with(target, background=True, state_dir=state_dir)

    def test_update_overwrites_registered_non_default_path_and_keeps_local_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state_dir = self.fixture_state
            state_dir.mkdir(parents=True)
            history = state_dir / "last-good.json"
            history.write_bytes(b"synthetic history survives replacement")
            install_dir = root / "custom parent" / "KV-Agent-Desk"
            install_dir.mkdir(parents=True)
            target = install_dir / "KV-Agent-Desk.exe"
            target.write_bytes(b"old installed executable")
            source = root / "candidate.exe"
            source.write_bytes(b"new release executable")
            registry = FakeRegistrationRegistry()

            with (
                patch.object(install_location_module, "_winreg", registry),
                patch.object(install_location_module, "_is_windows", return_value=True),
                patch.object(installation, "_is_windows", return_value=True),
                patch.object(installation, "_handoff_existing_server", return_value=True),
                patch.object(installation, "ensure_desktop_shortcut") as shortcut,
                patch.object(installation, "_launch") as launch,
            ):
                install_location_module.register_installer_installation(
                    install_dir, desktop_shortcut=False, state_dir=state_dir)
                result = installation.install_current(source, state_dir=state_dir, background=True)

            self.assertEqual(result, target)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(installation.installed_executable(), target)
            self.assertEqual(history.read_bytes(), b"synthetic history survives replacement")
            self.assertFalse(shortcut.called)
            launch.assert_called_once_with(target, background=True, state_dir=state_dir)

    def test_same_hash_with_unsupported_portable_server_hands_off_before_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / "local"
            target = local / "Programs" / "KV-Agent-Desk" / "KV-Agent-Desk.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"same synthetic executable")
            source = root / "portable.exe"
            source.write_bytes(target.read_bytes())
            state_dir = local / "KV-Agent-Desk"
            state = {"app": APP_ID, "version": __version__, "updates": {"supported": False}}
            with (
                patch.object(installation, "installation_directory", return_value=local / "Programs" / "KV-Agent-Desk"),
                patch.object(installation, "_is_windows", return_value=True),
                patch.object(installation, "_read_server_state", return_value=state),
                patch.object(installation, "ensure_desktop_shortcut"),
                patch.object(installation, "_handoff_existing_server", return_value=True) as handoff,
                patch.object(installation, "_launch") as launch,
            ):
                installation.install_current(source, state_dir=state_dir, background=True)

            handoff.assert_called_once_with()
            launch.assert_called_once_with(target, background=True, state_dir=state_dir)

    def test_handoff_stops_only_our_marked_server_and_waits_for_its_exit(self):
        marked_state = {"app": APP_ID, "version": "0.5.0"}
        with (
            patch.object(installation, "_read_server_state", side_effect=[marked_state, None]),
            patch.object(installation, "_request_existing_server", return_value=True) as request,
            patch.object(installation, "_wait_for_server_exit", return_value=True) as wait,
        ):
            self.assertTrue(installation._handoff_existing_server())
        request.assert_called_once_with(stop=True)
        wait.assert_called_once_with()

        with (
            patch.object(installation, "_read_server_state", return_value={"app": "unrelated-app"}),
            patch.object(installation, "_request_existing_server") as request,
            patch.object(installation, "_wait_for_server_exit") as wait,
        ):
            self.assertFalse(installation._handoff_existing_server())
        request.assert_not_called()
        wait.assert_not_called()

    def test_replace_timeout_preserves_old_executable_and_restarts_it_in_background(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / "local"
            state_dir = local / "KV-Agent-Desk"
            state_dir.mkdir(parents=True)
            target = local / "Programs" / "KV-Agent-Desk" / "KV-Agent-Desk.exe"
            target.parent.mkdir(parents=True)
            old_bytes = b"old working exe"
            target.write_bytes(old_bytes)
            source = root / "new.exe"
            source.write_bytes(b"new candidate exe")
            with (
                patch.object(installation, "installation_directory", return_value=local / "Programs" / "KV-Agent-Desk"),
                patch.object(installation, "_is_windows", return_value=True),
                patch.object(installation, "_REPLACE_TIMEOUT_SECONDS", 0.0),
                patch.object(installation, "_handoff_existing_server", return_value=True) as handoff,
                patch.object(installation.os, "replace", side_effect=PermissionError("locked")),
                patch.object(installation, "ensure_desktop_shortcut") as shortcut,
                patch.object(installation, "_launch") as launch,
            ):
                with self.assertRaises(TimeoutError):
                    installation.install_current(source, state_dir=state_dir, background=False)

            handoff.assert_called_once_with()
            shortcut.assert_not_called()
            launch.assert_called_once_with(target, background=True, state_dir=state_dir)
            self.assertEqual(target.read_bytes(), old_bytes)
            self.assertEqual(list(target.parent.glob(".KV-Agent-Desk.*.tmp")), [])

    def test_invalid_source_and_non_windows_shortcut_fail_without_side_effects(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state_dir = root / "state"
            with patch.object(installation, "_is_windows", return_value=True):
                with self.assertRaises(ValueError):
                    installation.install_current(root / "missing.exe", state_dir=state_dir)
                text_file = root / "not-an-exe.txt"
                text_file.write_bytes(b"synthetic")
                with self.assertRaises(ValueError):
                    installation.install_current(text_file, state_dir=state_dir)

            with patch.object(installation, "_is_windows", return_value=False), \
                    patch.object(installation.subprocess, "run") as run:
                with self.assertRaises(NotImplementedError):
                    installation.ensure_desktop_shortcut(text_file)
                run.assert_not_called()

    def test_shortcut_uses_powershell_desktop_and_environment_target_for_paths_with_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "Program Files" / "KV Agent Desk" / "KV Agent Desk.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"synthetic exe")
            link = root / "OneDrive - Synthetic" / "Desktop" / "KV Agent Desk.lnk"
            completed = SimpleNamespace(returncode=0, stdout=str(link) + "\r\n")
            with (
                patch.object(installation, "_is_windows", return_value=True),
                patch.object(installation, "user_state_directory", return_value=root / "saved data"),
                patch.dict(os.environ, {"SystemRoot": str(root / "Windows")} ),
                patch.object(installation.subprocess, "run", return_value=completed) as run,
            ):
                result = installation.ensure_desktop_shortcut(target)

            self.assertEqual(result, link)
            args, kwargs = run.call_args
            script = args[0][-1]
            self.assertIn("$ErrorActionPreference = 'Stop'", script)
            self.assertIn("[Console]::OutputEncoding = $utf8", script)
            self.assertIn("$OutputEncoding = $utf8", script)
            self.assertIn("[Environment]::GetFolderPath('Desktop')", script)
            self.assertIn("CreateShortcut($linkPath)", script)
            self.assertIn("$shortcut.TargetPath = $target", script)
            self.assertIn("[IO.Path]::GetDirectoryName($target)", script)
            self.assertNotIn(str(target), script)
            self.assertEqual(kwargs["env"][installation._SHORTCUT_TARGET_ENV], str(target.resolve()))
            self.assertEqual(kwargs["env"][installation._SHORTCUT_STATE_ENV], str((root / "saved data").resolve()))
            self.assertIn("$shortcut.Arguments = '--state-dir", script)
            self.assertTrue(kwargs["capture_output"])
            self.assertEqual(kwargs["errors"], "replace")
            self.assertEqual(kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))


if __name__ == "__main__":
    unittest.main()
