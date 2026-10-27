import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent_dashboard.install_location as location
from install_test_safety import (
    _REAL_PROFILE,
    allow_synthetic_root,
    audit,
    isolate_real_user_state,
    short_windows_path,
)


class FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1
    REG_DWORD = 4

    def __init__(self):
        self.values = {}
        self.fail_open = set()

    class Handle:
        def __init__(self, registry, key):
            self.registry = registry
            self.key = key

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def OpenKey(self, _root, key, _reserved=0, _access=0):
        if key in self.fail_open:
            raise PermissionError(13, "synthetic registry failure")
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
            raise OSError("key is not empty")
        del self.values[key]


class InstallLocationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="KV-Agent-Desk-install-test-fixture-")
        allow_synthetic_root(self, Path(self.folder.name))
        isolate_real_user_state(self)
        self.home = Path(self.folder.name) / "user profile"
        self.home.mkdir()
        self.registry = FakeRegistry()
        self.state_dir = self.home / ".kv-agent-desk-test-fixture"
        self.windows = patch.object(location, "_is_windows", return_value=True)
        self.registry_patch = patch.object(location, "_winreg", self.registry)
        self.home_patch = patch.object(location, "user_state_directory", return_value=self.state_dir)
        self.windows.start()
        self.registry_patch.start()
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.registry_patch.stop()
        self.windows.stop()
        self.folder.cleanup()

    def write_ini(self, install_dir: Path, desktop=1, managed=1):
        path = location.installation_ini_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (f"[Installation]\r\nInstallDir={install_dir}\r\n"
                f"DesktopShortcut={desktop}\r\nInstallerManaged={managed}\r\n")
        path.write_bytes(b"\xff\xfe" + body.encode("utf-16-le"))
        return path

    def install_dir_fixture(self):
        return self.home / "synthetic program location" / "KV-Agent-Desk"

    def set_registry(self, install_dir: Path, desktop=True, managed=True):
        self.registry.values[location.REGISTRY_SUBKEY] = {
            location.INSTALL_DIR_VALUE: (str(install_dir), self.registry.REG_SZ),
            location.DESKTOP_SHORTCUT_VALUE: (int(desktop), self.registry.REG_DWORD),
            location.INSTALLER_MANAGED_VALUE: (int(managed), self.registry.REG_DWORD),
        }

    def test_first_install_defaults_to_d_app_subdirectory(self):
        self.assertEqual(location.default_install_dir(), Path("D:/APP/KV-Agent-Desk"))
        self.assertEqual(location.read_installation().directory, Path("D:/APP/KV-Agent-Desk"))
        self.assertTrue(location.read_installation().desktop_shortcut)
        self.assertFalse(location.read_installation().installer_managed)

    def test_registration_requires_local_absolute_non_root_directory(self):
        for invalid in (r"relative\KV-Agent-Desk", r"\\server\share\KV-Agent-Desk",
                        r"\\?\D:\APP\KV-Agent-Desk", "D:\\", 'D:\\APP\\My"App'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                location._validate_install_dir(invalid)
        custom_leaf = Path(r"E:\Apps\My Agent Desk")
        self.assertEqual(location._validate_install_dir(str(custom_leaf)), custom_leaf)

    def test_registry_is_fallback_when_canonical_ini_has_not_been_created(self):
        registered = self.install_dir_fixture()
        self.set_registry(registered, desktop=False, managed=True)
        result = location.read_installation()
        self.assertEqual(result, location.Installation(registered, False, True, True))
        self.assertEqual(location.install_dir_for_runtime(), registered)

    def test_raw_update_promotes_registry_only_managed_opt_out_to_canonical_ini(self):
        registered = self.install_dir_fixture()
        self.set_registry(registered, desktop=False, managed=True)

        result = location.register_legacy_installation(
            registered, state_dir=self.state_dir)

        self.assertEqual(result, location.Installation(registered, False, True, True))
        self.assertEqual(location.read_installation(), result)
        self.assertFalse(location.desktop_shortcut_enabled())

    def test_audit_guard_rejects_run_value_delete_before_registry_call(self):
        with self.assertRaisesRegex(AssertionError, "cannot write to the Windows registry"):
            audit("winreg.DeleteValue", (self.registry, "KVAgentDesk"))

    def test_audit_guard_blocks_real_state_writes_and_allows_temporary_fixtures(self):
        blocked = Path(_REAL_PROFILE) / ".kv-agent-desk" / "settings.json"
        with self.assertRaisesRegex(AssertionError, "real user state"):
            audit("open", (blocked, "wb", os.O_WRONLY | os.O_CREAT))
        fixture_write = Path(self.folder.name) / "synthetic-state.json"
        audit("open", (fixture_write, "wb", os.O_WRONLY | os.O_CREAT))

    def test_audit_guard_accepts_short_parent_with_long_temporary_basename(self):
        original = Path(self.folder.name)
        alias_parent = short_windows_path(original.parent)
        if alias_parent is None or Path(alias_parent).resolve() == original.parent.resolve():
            self.skipTest("this Windows volume does not provide 8.3 aliases")
        mixed_root = Path(alias_parent) / original.name
        self.assertEqual(mixed_root.resolve(strict=True), original.resolve(strict=True))
        self.assertNotEqual(mixed_root.absolute(), original.absolute())
        audit("open", (mixed_root / "synthetic-alias-check.json", "wb", os.O_WRONLY | os.O_CREAT))

    def test_utf16_install_ini_is_canonical_over_stale_registry_mirror(self):
        registered = self.install_dir_fixture()
        self.write_ini(registered, desktop=0, managed=1)
        self.set_registry(self.install_dir_fixture(), desktop=True, managed=True)

        result = location.read_installation()

        self.assertEqual(result, location.Installation(registered, False, True, True))
        self.assertEqual(location.install_dir_for_runtime(), registered)
        self.assertFalse(location.desktop_shortcut_enabled())

    def test_canonical_ini_remains_readable_when_registry_mirror_is_unavailable(self):
        registered = self.install_dir_fixture()
        self.write_ini(registered, desktop=0, managed=1)
        self.registry.fail_open.add(location.REGISTRY_SUBKEY)

        self.assertEqual(location.install_dir_for_runtime(), registered)
        self.assertFalse(location.desktop_shortcut_enabled())

    def test_invalid_or_unreadable_canonical_ini_does_not_fall_back_to_registry(self):
        self.set_registry(self.install_dir_fixture())
        path = location.installation_ini_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a UTF-16 INI")
        with self.assertRaisesRegex(ValueError, "installation record is invalid"):
            location.read_installation()
        with patch.object(Path, "read_bytes", side_effect=PermissionError(13, "synthetic denial")):
            with self.assertRaisesRegex(OSError, "installation record could not be read"):
                location.read_installation()

    def test_setup_writes_atomic_utf16_ini_and_registry_mirror(self):
        destination = self.install_dir_fixture()
        location.register_installer_installation(destination, desktop_shortcut=False)

        path = location.installation_ini_path()
        self.assertTrue(path.read_bytes().startswith(b"\xff\xfe"))
        self.assertEqual(location.read_installation(), location.Installation(destination, False, True, True))
        self.assertEqual(self.registry.values[location.REGISTRY_SUBKEY], {
            location.INSTALL_DIR_VALUE: (str(destination), self.registry.REG_SZ),
            location.DESKTOP_SHORTCUT_VALUE: (0, self.registry.REG_DWORD),
            location.INSTALLER_MANAGED_VALUE: (1, self.registry.REG_DWORD),
        })
        self.assertFalse(list(path.parent.glob(".installation.*.tmp")))

    def test_failed_atomic_ini_replace_preserves_previous_canonical_record(self):
        original = self.install_dir_fixture()
        self.write_ini(original, desktop=1, managed=1)
        path = location.installation_ini_path()
        original_bytes = path.read_bytes()
        with patch.object(location.os, "replace", side_effect=PermissionError(13, "synthetic failure")):
            with self.assertRaisesRegex(OSError, "installation record could not be saved"):
                location.register_installer_installation(self.home / "other synthetic location" / "KV-Agent-Desk", desktop_shortcut=False)
        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertFalse(list(path.parent.glob(".installation.*.tmp")))

    def test_raw_install_registration_does_not_clear_existing_setup_opt_out(self):
        custom_dir = self.install_dir_fixture()
        self.write_ini(custom_dir, desktop=0, managed=1)
        result = location.register_legacy_installation(custom_dir, state_dir=self.state_dir)
        self.assertEqual(result, location.Installation(custom_dir, False, True, True))
        self.assertFalse(location.desktop_shortcut_enabled())

    def test_unregister_removes_only_matching_ini_and_registration_not_userdata(self):
        registered = self.install_dir_fixture()
        self.write_ini(registered, desktop=1, managed=1)
        self.set_registry(registered)
        settings = location.installation_ini_path().parent / "settings.json"
        settings.write_bytes(b"synthetic user settings")

        self.assertTrue(location.clear_installer_installation(registered))
        self.assertFalse(location.installation_ini_path().exists())
        self.assertEqual(settings.read_bytes(), b"synthetic user settings")
        self.assertNotIn(location.REGISTRY_SUBKEY, self.registry.values)

    def test_registry_delete_failure_preserves_canonical_install_record(self):
        registered = self.install_dir_fixture()
        path = self.write_ini(registered, desktop=1, managed=1)
        before = path.read_bytes()
        self.set_registry(registered)
        with patch.object(self.registry, "DeleteValue", side_effect=PermissionError(13, "synthetic denial")):
            with self.assertRaises(PermissionError):
                location.clear_installer_installation(registered)
        self.assertEqual(path.read_bytes(), before)

    def test_uninstall_with_mismatched_path_preserves_install_registration(self):
        registered = self.install_dir_fixture()
        self.write_ini(registered, desktop=1, managed=1)
        with self.assertRaisesRegex(ValueError, "does not match"):
            location.clear_installer_installation(self.home / "other synthetic location" / "KV-Agent-Desk")
        self.assertTrue(location.installation_ini_path().is_file())

    def test_display_version_updates_only_matching_inno_appid_record(self):
        matching = self.install_dir_fixture()
        self.registry.values[location.OWNED_UNINSTALL_SUBKEY] = {
            location.UNINSTALL_LOCATION_VALUE: (str(matching), self.registry.REG_SZ),
            "DisplayVersion": ("0.6.2", self.registry.REG_SZ),
        }
        self.assertTrue(location.sync_owned_uninstall_display_version(matching, "0.7.0"))
        self.assertEqual(self.registry.values[location.OWNED_UNINSTALL_SUBKEY]["DisplayVersion"],
                         ("0.7.0", self.registry.REG_SZ))
        self.assertFalse(location.sync_owned_uninstall_display_version(self.home / "other synthetic location" / "KV-Agent-Desk", "0.7.1"))
        self.assertEqual(self.registry.values[location.OWNED_UNINSTALL_SUBKEY]["DisplayVersion"],
                         ("0.7.0", self.registry.REG_SZ))


if __name__ == "__main__":
    unittest.main()
