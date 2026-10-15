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
    def test_defaults_and_memory_updates(self):
        settings = AppSettings()
        self.assertEqual(settings.get(), DEFAULT_SETTINGS)
        changed = settings.update({"brightness": 73, "lcd_height": 480})
        self.assertEqual(changed["brightness"], 73)
        self.assertEqual(changed["lcd_height"], 480)
        self.assertEqual(settings.get()["rotation"], 180)

    def test_json_round_trip_contains_only_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "settings.json"
            settings = AppSettings(path)
            saved = settings.update({"auto_start": False, "usb_enabled": False})
            self.assertEqual(AppSettings(path).get(), saved)
            self.assertEqual(set(json.loads(path.read_text(encoding="utf-8"))), set(DEFAULT_SETTINGS))

    def test_invalid_updates_raise_without_changing_saved_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings = AppSettings(path)
            settings.update({"brightness": 60})
            original = path.read_bytes()
            invalid = [
                {"brightness": -1}, {"brightness": 101}, {"brightness": True},
                {"rotation": 90}, {"usb_enabled": 1}, {"auto_start": "yes"},
                {"lcd_height": 463}, {"other": 1},
            ]
            for fields in invalid:
                with self.subTest(fields=fields), self.assertRaises(ValueError):
                    settings.update(fields)
                self.assertEqual(path.read_bytes(), original)

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
