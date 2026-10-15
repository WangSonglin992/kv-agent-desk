import sys
import threading
import types
import unittest
from unittest.mock import patch

from agent_dashboard import desktop


class _FakeEvent:
    def __init__(self, name, owner):
        self.name = name
        self.owner = owner
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def emit(self):
        values = []
        for handler in self.handlers:
            if self.name in {"before_show", "closing", "shown"}:
                values.append(handler(self.owner))
            else:
                values.append(handler())
        return any(value is False for value in values)


class _FakeNativeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def emit(self, sender, args):
        for handler in self.handlers:
            handler(sender, args)


class _FakeWindow:
    def __init__(self):
        self.hide_count = 0
        self.show_count = 0
        self.destroy_count = 0
        self.events = types.SimpleNamespace()
        self.events.before_show = _FakeEvent("before_show", self)
        self.events.closing = _FakeEvent("closing", self)
        self.events.closed = _FakeEvent("closed", self)
        self.events.shown = _FakeEvent("shown", self)

    def hide(self):
        self.hide_count += 1

    def show(self):
        self.show_count += 1

    def destroy(self):
        self.destroy_count += 1
        self.events.closed.emit()


class _FakeIcon:
    def __init__(self, *_args, **_kwargs):
        self.stop_count = 0
        self.update_count = 0
        self.visible = False

    def run(self, setup=None):
        if setup:
            setup(self)

    def stop(self):
        self.stop_count += 1

    def update_menu(self):
        self.update_count += 1


class _FakeMenuItem:
    def __init__(self, text, action, **kwargs):
        self.text = text
        self.action = action
        self.kwargs = kwargs


class _FakeMenu:
    def __init__(self, *items):
        self.items = items


class DesktopTests(unittest.TestCase):
    def test_close_hides_window_and_cancels_native_close(self):
        window = _FakeWindow()
        stop_event = threading.Event()
        shell = desktop._DesktopShell(window, stop_event)

        cancelled = window.events.closing.emit()

        self.assertTrue(cancelled)
        self.assertEqual(window.hide_count, 1)
        self.assertEqual(window.destroy_count, 0)
        self.assertFalse(stop_event.is_set())

    def test_request_show_waits_until_window_is_ready_then_shows(self):
        window = _FakeWindow()
        shell = desktop._DesktopShell(window, threading.Event())

        self.assertTrue(shell.request_show())
        self.assertEqual(window.show_count, 0)
        window.events.shown.emit()
        self.assertEqual(window.show_count, 1)
        self.assertTrue(shell.request_show())
        self.assertEqual(window.show_count, 2)

    def test_exit_stops_tray_sets_stop_event_and_destroys_window(self):
        window = _FakeWindow()
        stop_event = threading.Event()
        shell = desktop._DesktopShell(window, stop_event)
        shell._window_ready.set()
        icon = _FakeIcon()

        shell._exit(icon)

        self.assertTrue(stop_event.is_set())
        self.assertEqual(icon.stop_count, 1)
        self.assertEqual(window.destroy_count, 1)
        self.assertEqual(window.hide_count, 0)

    def test_os_shutdown_close_reason_allows_native_close_and_stops_shell(self):
        window = _FakeWindow()
        window.native = types.SimpleNamespace(FormClosing=_FakeNativeEvent())
        stop_event = threading.Event()
        shell = desktop._DesktopShell(window, stop_event)
        icon = _FakeIcon()
        shell.icon = icon
        window.events.before_show.emit()
        args = types.SimpleNamespace(CloseReason="WindowsShutDown", Cancel=True)

        window.native.FormClosing.emit(window.native, args)

        self.assertTrue(stop_event.is_set())
        self.assertFalse(args.Cancel)
        self.assertEqual(icon.stop_count, 1)
        self.assertTrue(shell._exiting)

    def test_user_close_reason_remains_cancelled_to_tray(self):
        window = _FakeWindow()
        window.native = types.SimpleNamespace(FormClosing=_FakeNativeEvent())
        stop_event = threading.Event()
        shell = desktop._DesktopShell(window, stop_event)
        window.events.before_show.emit()
        args = types.SimpleNamespace(CloseReason="UserClosing", Cancel=True)

        window.native.FormClosing.emit(window.native, args)

        self.assertTrue(args.Cancel)
        self.assertFalse(stop_event.is_set())

    def test_usb_menu_callback_updates_pause_label(self):
        window = _FakeWindow()
        shell = desktop._DesktopShell(window, threading.Event(), on_usb_toggle=lambda: True)
        icon = _FakeIcon()

        self.assertEqual(shell._usb_label(None), "暂停 USB 输出")
        shell._toggle_usb(icon, None)

        self.assertEqual(shell._usb_label(None), "恢复 USB 输出")
        self.assertEqual(icon.update_count, 1)

    def test_tray_setup_explicitly_makes_icon_visible(self):
        shell = desktop._DesktopShell(_FakeWindow(), threading.Event())
        fake_pystray = types.SimpleNamespace(
            Icon=_FakeIcon,
            MenuItem=_FakeMenuItem,
            Menu=_FakeMenu,
        )

        shell.start_tray(fake_pystray, "tray-image")

        self.assertTrue(shell.icon.visible)
        shell._stop_tray()

    def test_background_start_creates_hidden_window_and_uses_edgechromium_on_main_thread(self):
        window = _FakeWindow()
        create_args = {}
        calls = {}

        class FakeWebView:
            def create_window(self, title, url, **kwargs):
                create_args.update(title=title, url=url, **kwargs)
                return window

            def start(self, **kwargs):
                calls["gui"] = kwargs.get("gui")
                calls["main_thread"] = threading.current_thread() is threading.main_thread()
                window.events.shown.emit()
                result = []
                requester = threading.Thread(target=lambda: result.append(desktop.request_show()))
                requester.start()
                requester.join(timeout=2.0)
                calls["request_show"] = result == [True]

        fake_pystray = types.SimpleNamespace(
            Icon=_FakeIcon,
            MenuItem=_FakeMenuItem,
            Menu=_FakeMenu,
        )
        fake_image = types.SimpleNamespace(new=lambda *_args, **_kwargs: object())
        fake_draw = types.SimpleNamespace()

        with (
            patch.object(desktop, "webview2_runtime_version", return_value="155.0"),
            patch.object(desktop, "_load_gui_dependencies", return_value=(FakeWebView(), fake_pystray, fake_image, fake_draw)),
            patch.object(desktop, "_tray_image", return_value="tray-image"),
        ):
            desktop.run_desktop("http://127.0.0.1:8765/", threading.Event(), background=True)

        self.assertTrue(create_args["hidden"])
        self.assertEqual(calls["gui"], "edgechromium")
        self.assertTrue(calls["main_thread"])
        self.assertTrue(calls["request_show"])
        self.assertEqual(window.show_count, 1)
        self.assertEqual(create_args["url"], "http://127.0.0.1:8765/")

    def test_missing_webview2_runtime_fails_before_loading_gui(self):
        with (
            patch.object(desktop.sys, "platform", "win32"),
            patch.object(desktop, "webview2_runtime_version", return_value=None),
            patch.object(desktop, "_load_gui_dependencies") as load_gui,
        ):
            with self.assertRaisesRegex(RuntimeError, "WebView2 Runtime"):
                desktop.run_desktop("http://127.0.0.1/", threading.Event())

        load_gui.assert_not_called()

    def test_webview2_registry_probe_reads_official_runtime_key_only(self):
        calls = []

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def open_key(hive, path, _reserved, access):
            calls.append((hive, path, access))
            if "WOW6432Node" in path:
                return Key()
            raise OSError("not installed here")

        def query_value(key, name):
            self.assertEqual(name, "pv")
            return "155.0.4283.45", 1

        fake_winreg = types.SimpleNamespace(
            HKEY_CURRENT_USER=1,
            HKEY_LOCAL_MACHINE=2,
            KEY_READ=0x20019,
            OpenKey=open_key,
            QueryValueEx=query_value,
        )
        with (
            patch.object(desktop.sys, "platform", "win32"),
            patch.dict(sys.modules, {"winreg": fake_winreg}),
        ):
            version = desktop.webview2_runtime_version()

        self.assertEqual(version, "155.0.4283.45")
        self.assertTrue(all(call[2] == fake_winreg.KEY_READ for call in calls))
        self.assertTrue(any("WOW6432Node" in call[1] for call in calls))


if __name__ == "__main__":
    unittest.main()
