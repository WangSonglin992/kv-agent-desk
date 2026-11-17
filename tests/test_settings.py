import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import agent_dashboard.settings as settings_module
from agent_dashboard.settings import AppSettings, DEFAULT_SETTINGS, get_autostart, set_autostart


class _FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 2
    KEY_QUERY_VALUE = 1
    REG_SZ = 1

    def __init__(self):
        self.values = {}
        self.opened = []
        self.created = []
        self.deleted = []

    class _Handle:
        def __init__(self, registry, subkey):
            self.registry = registry
            self.subkey = subkey

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def CreateKeyEx(self, root, subkey, _reserved, access):
        self.created.append((root, subkey, access))
        return self._Handle(self, subkey)

    def OpenKey(self, root, subkey, _reserved, access):
        self.opened.append((root, subkey, access))
        if (root, subkey) not in self.values:
            raise FileNotFoundError()
        return self._Handle(self, subkey)

    def SetValueEx(self, key, name, _reserved, value_type, value):
        self.values[(self.HKEY_CURRENT_USER, key.subkey)] = (name, value_type, value)

    def QueryValueEx(self, key, name):
        stored = self.values.get((self.HKEY_CURRENT_USER, key.subkey))
        if stored is None or stored[0] != name:
            raise FileNotFoundError()
        return stored[2], stored[1]

    def DeleteValue(self, key, name):
        value_key = (self.HKEY_CURRENT_USER, key.subkey)
        stored = self.values.get(value_key)
        if stored is None or stored[0] != name:
            raise FileNotFoundError()
        del self.values[value_key]
        self.deleted.append(name)


class AppSettingsTests(unittest.TestCase):
    def test_temporary_read_failure_retains_values_and_cannot_overwrite_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings = AppSettings(path)
            settings.update({"brightness": 39, "ui_id": "saved_layout"})
            original = path.read_bytes()
            with patch.object(Path, "open", side_effect=PermissionError(13, "synthetic failure")):
                self.assertEqual(settings.get()["ui_id"], "saved_layout")
                self.assertEqual(settings.last_read_status, "unavailable")
                with self.assertRaises(OSError):
                    settings.update({"brightness": 40})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(settings.get()["brightness"], 39)
            self.assertEqual(settings.last_read_status, "ready")

    def test_invalid_file_is_not_overwritten_by_a_default_based_update(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_bytes(b'{ damaged configuration')
            settings = AppSettings(path)
            with self.assertRaises(OSError):
                settings.update({"ui_id": "classic"})
            self.assertEqual(path.read_bytes(), b'{ damaged configuration')

    def test_defaults_and_memory_updates(self):
        settings = AppSettings()
        self.assertEqual(settings.get(), DEFAULT_SETTINGS)
        self.assertEqual(settings.get()["right_panel_mode"], "accounts")
        self.assertEqual(settings.get()["cluster_ssh_host"], "")
        self.assertEqual(settings.get()["cluster_refresh_seconds"], 60)
        changed = settings.update({"brightness": 73, "lcd_height": 480})
        self.assertEqual(changed["brightness"], 73)
        self.assertEqual(changed["lcd_height"], 480)
        self.assertEqual(settings.get()["rotation"], 180)
        self.assertTrue(settings.get()["auto_update"])

    def test_json_round_trip_contains_only_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "settings.json"
            settings = AppSettings(path)
            saved = settings.update({"auto_start": False, "auto_update": False,
                                     "usb_enabled": False, "ui_id": "paper"})
            self.assertEqual(AppSettings(path).get(), saved)
            self.assertEqual(saved["ui_id"], "paper")
            self.assertFalse(saved["auto_update"])
            self.assertEqual(set(json.loads(path.read_text(encoding="utf-8"))), set(DEFAULT_SETTINGS))

    def test_legacy_settings_default_auto_update_on_and_preserve_existing_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"brightness": 47, "rotation": 0,
                                        "usb_enabled": False, "auto_start": False,
                                        "ui_id": "paper", "legacy_unknown": "ignored"}),
                            encoding="utf-8")
            settings = AppSettings(path)
            loaded = settings.get()
            self.assertTrue(loaded["auto_update"])
            self.assertEqual(loaded["brightness"], 47)
            self.assertEqual(loaded["rotation"], 0)
            self.assertFalse(loaded["usb_enabled"])
            self.assertFalse(loaded["auto_start"])
            self.assertEqual(loaded["ui_id"], "paper")
            self.assertNotIn("legacy_unknown", loaded)

            disabled = settings.update({"auto_update": False})
            self.assertFalse(disabled["auto_update"])
            for name in ("brightness", "rotation", "usb_enabled", "auto_start", "ui_id"):
                self.assertEqual(disabled[name], loaded[name])
            self.assertEqual(AppSettings(path).get(), disabled)

    def test_legacy_settings_receive_cluster_defaults_and_keep_existing_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"brightness": 47, "ui_id": "paper"}), encoding="utf-8")
            settings = AppSettings(path)
            loaded = settings.get()
            self.assertEqual(loaded["right_panel_mode"], "accounts")
            self.assertEqual(loaded["cluster_ssh_host"], "")
            self.assertEqual(loaded["cluster_refresh_seconds"], 60)
            saved = settings.update({"cluster_ssh_host": "hpc-login", "cluster_refresh_seconds": 300})
            self.assertEqual(saved["brightness"], 47)
            self.assertEqual(saved["ui_id"], "paper")
            self.assertEqual(saved["right_panel_mode"], "accounts")
            self.assertEqual(AppSettings(path).get(), saved)

    def test_invalid_updates_raise_without_changing_saved_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings = AppSettings(path)
            settings.update({"brightness": 60})
            original = path.read_bytes()
            invalid = [
                {"brightness": -1}, {"brightness": 101}, {"brightness": True},
                {"rotation": 90}, {"usb_enabled": 1}, {"auto_start": "yes"},
                {"auto_update": 1}, {"auto_update": "yes"},
                {"lcd_height": 463}, {"ui_id": "../theme"}, {"ui_id": 1}, {"other": 1},
                {"right_panel_mode": "other"}, {"right_panel_mode": 1}, {"right_panel_mode": []},
                {"cluster_ssh_host": "user@host"}, {"cluster_ssh_host": "host name"},
                {"cluster_ssh_host": "host/name"}, {"cluster_ssh_host": "-host"},
                {"cluster_ssh_host": "a" * 129}, {"cluster_ssh_host": 1},
                {"cluster_refresh_seconds": 14}, {"cluster_refresh_seconds": 3601},
                {"cluster_refresh_seconds": True}, {"cluster_refresh_seconds": "60"},
            ]
            for fields in invalid:
                with self.subTest(fields=fields), self.assertRaises(ValueError):
                    settings.update(fields)
                self.assertEqual(path.read_bytes(), original)

    def test_cluster_settings_accept_alias_mode_and_interval_boundaries(self):
        settings = AppSettings()
        saved = settings.update({"right_panel_mode": "cluster", "cluster_ssh_host": "HPC_01.example-2",
                                 "cluster_refresh_seconds": 15})
        self.assertEqual(saved["right_panel_mode"], "cluster")
        self.assertEqual(saved["cluster_ssh_host"], "HPC_01.example-2")
        self.assertEqual(saved["cluster_refresh_seconds"], 15)
        self.assertEqual(settings.update({"cluster_ssh_host": "", "cluster_refresh_seconds": 3600})[
            "cluster_refresh_seconds"], 3600)

    def test_failed_atomic_replace_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings = AppSettings(path)
            settings.update({"brightness": 58})
            original = path.read_bytes()
            with patch.object(settings_module.os, "replace", side_effect=PermissionError("busy")):
                with self.assertRaises(PermissionError):
                    settings.update({"brightness": 12})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_bad_json_and_invalid_stored_values_fall_back_safely(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text("{ broken", encoding="utf-8")
            self.assertEqual(AppSettings(path).get(), DEFAULT_SETTINGS)
            path.write_text('{"brightness":true,"rotation":0,"extra":"ignored"}', encoding="utf-8")
            loaded = AppSettings(path).get()
            self.assertEqual(loaded["brightness"], DEFAULT_SETTINGS["brightness"])
            self.assertEqual(loaded["rotation"], 0)
            self.assertNotIn("extra", loaded)


class AutoStartTests(unittest.TestCase):
    def test_pinned_state_directory_is_quoted_and_validated(self):
        command = [r"C:\Program Files\KV Agent Desk\app.exe", "--background", "--state-dir", r"C:\Private\data with spaces"]
        set_autostart(True, command)
        value = self.registry.values[(self.registry.HKEY_CURRENT_USER,
                                     r"Software\Microsoft\Windows\CurrentVersion\Run")]
        self.assertEqual(value[2], subprocess.list2cmdline(command))
        for invalid in (command[:3] + ["relative"], command[:2] + ["--another-flag", command[3]]):
            with self.assertRaises(ValueError):
                set_autostart(True, invalid)

    def setUp(self):
        self.registry = _FakeRegistry()
        self.platform = patch.object(settings_module, "_is_windows", return_value=True)
        self.platform.start()
        self.winreg = patch.object(settings_module, "_winreg", self.registry)
        self.winreg.start()
        self.addCleanup(self.winreg.stop)
        self.addCleanup(self.platform.stop)

    def test_writes_quoted_fixed_command_to_hkcu_run(self):
        command = [r"C:\Program Files\KV Agent Desk\KV Agent Desk.exe", "--background"]
        set_autostart(True, command)
        self.assertEqual(
            self.registry.created,
            [(self.registry.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", self.registry.KEY_SET_VALUE)],
        )
        stored_name, stored_type, stored_command = self.registry.values[(
            self.registry.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
        )]
        self.assertEqual(stored_name, "KVAgentDesk")
        self.assertEqual(stored_type, self.registry.REG_SZ)
        self.assertEqual(stored_command, subprocess.list2cmdline(command))
        self.assertTrue(get_autostart())

    def test_rejects_arbitrary_or_relative_commands(self):
        for command in (
            ["dashboard.exe", "--background"],
            [r"C:\app.exe", "--run-this"],
            [r"C:\app.exe", "--background", "extra"],
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                set_autostart(True, command)
        self.assertEqual(self.registry.values, {})

    def test_disable_removes_only_our_value_and_missing_is_safe(self):
        set_autostart(False, [])
        self.assertEqual(self.registry.deleted, [])
        set_autostart(True, [r"C:\KVAgentDesk.exe", "--background"])
        set_autostart(False, [])
        self.assertEqual(self.registry.deleted, ["KVAgentDesk"])
        self.assertFalse(get_autostart())

    def test_non_windows_get_is_false_and_set_is_explicitly_unsupported(self):
        with patch.object(settings_module, "_is_windows", return_value=False):
            self.assertFalse(get_autostart())
            with self.assertRaises(NotImplementedError):
                set_autostart(True, [r"C:\KVAgentDesk.exe", "--background"])


if __name__ == "__main__":
    unittest.main()
