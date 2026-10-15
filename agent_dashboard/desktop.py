"""Windows desktop shell for the local agent dashboard."""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Callable


logger = logging.getLogger(__name__)
_active_shell: "_DesktopShell | None" = None
_active_shell_lock = threading.Lock()


def webview2_runtime_version() -> str | None:
    """Return the installed Evergreen WebView2 version without changing the registry."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None

    client = r"{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    locations = (
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{client}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{client}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{client}"),
    )
    for hive, subkey in locations:
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
            if isinstance(version, str) and version.strip() and version != "0.0.0.0":
                return version.strip()
        except OSError:
            continue
    return None


def request_show() -> bool:
    """Ask the active desktop shell to show its window; safe for HTTP threads."""
    with _active_shell_lock:
        shell = _active_shell
    return shell.request_show() if shell is not None else False


def _load_gui_dependencies():
    import pystray
    import webview
    from PIL import Image, ImageDraw

    return webview, pystray, Image, ImageDraw


def _tray_image(Image, ImageDraw):
    image = Image.new("RGBA", (64, 64), (17, 26, 38, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5, 5, 59, 59), radius=12, fill=(20, 38, 54, 255), outline=(48, 193, 190, 255), width=3)
    draw.rounded_rectangle((16, 17, 48, 47), radius=5, fill=(48, 193, 190, 255))
    draw.rectangle((22, 23, 42, 41), fill=(17, 26, 38, 255))
    draw.line((25, 32, 29, 36, 38, 27), fill=(104, 225, 196, 255), width=3)
    return image


class _DesktopShell:
    def __init__(
        self,
        window: Any,
        stop_event: threading.Event,
        *,
        on_usb_toggle: Callable[[], object] | None = None,
    ):
        self.window = window
        self.stop_event = stop_event
        self.on_usb_toggle = on_usb_toggle
        self.icon: Any = None
        self._tray_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._window_ready = threading.Event()
        self._tray_ready = threading.Event()
        self._exiting = False
        self._pending_show = False
        self._usb_paused = False
        self._tray_error: BaseException | None = None
        window.events.before_show += self._on_before_show
        window.events.closing += self._on_closing
        window.events.closed += self._on_closed
        window.events.shown += self._on_shown

    def request_show(self) -> bool:
        with self._lock:
            if self._exiting:
                return False
            if not self._window_ready.is_set():
                self._pending_show = True
                return True
        self.window.show()
        return True

    def _on_shown(self, window) -> None:
        with self._lock:
            self._window_ready.set()
            should_show = self._pending_show and not self._exiting
            self._pending_show = False
            should_destroy = self._exiting
        if should_show:
            window.show()
        if should_destroy:
            window.destroy()

    def _on_closing(self, window) -> bool | None:
        with self._lock:
            if self._exiting:
                return None
        window.hide()
        return False

    def _on_before_show(self, window) -> None:
        # pywebview's public Windows native handle is a WinForms Form. Its
        # FormClosing args expose CloseReason, which lets OS shutdown bypass
        # our regular close-to-tray cancellation without a global hook.
        native = getattr(window, "native", None)
        form_closing = getattr(native, "FormClosing", None)
        if form_closing is not None:
            form_closing += self._on_native_form_closing

    def _on_native_form_closing(self, _sender, args) -> None:
        reason = getattr(args, "CloseReason", None)
        to_string = getattr(reason, "ToString", None)
        if callable(to_string):
            reason = to_string()
        if str(reason).casefold() != "windowsshutdown":
            return

        with self._lock:
            self._exiting = True
        self.stop_event.set()
        # pywebview's own closing callback runs first and marks Cancel=True;
        # clear that flag only for the OS shutdown reason.
        args.Cancel = False
        self._stop_tray()

    def _on_closed(self, *_args) -> None:
        self.stop_event.set()
        with self._lock:
            self._exiting = True
        self._stop_tray()

    def _usb_label(self, _item) -> str:
        return "恢复 USB 输出" if self._usb_paused else "暂停 USB 输出"

    def _toggle_usb(self, icon, _item) -> None:
        if self.on_usb_toggle is None:
            return
        try:
            result = self.on_usb_toggle()
            if isinstance(result, bool):
                self._usb_paused = result
            else:
                self._usb_paused = not self._usb_paused
            icon.update_menu()
        except Exception:
            logger.exception("USB output toggle failed")

    def _open_window(self, _icon=None, _item=None) -> None:
        self.request_show()

    def _exit(self, icon=None, _item=None) -> None:
        with self._lock:
            if self._exiting:
                return
            self._exiting = True
            ready = self._window_ready.is_set()
        self.stop_event.set()
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                logger.debug("Tray loop was already stopped", exc_info=True)
        if ready:
            try:
                self.window.destroy()
            except Exception:
                logger.debug("WebView was already closed", exc_info=True)

    def _stop_tray(self) -> None:
        icon = self.icon
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                logger.debug("Tray loop was already stopped", exc_info=True)
        thread = self._tray_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _run_tray(self) -> None:
        try:
            self.icon.run(setup=self._setup_tray)
        except BaseException as exc:
            self._tray_error = exc
            self._tray_ready.set()

    def _setup_tray(self, icon) -> None:
        # pystray skips its default visibility when a custom setup callback is
        # supplied, so explicitly show the icon before reporting readiness.
        icon.visible = True
        self._tray_ready.set()

    def start_tray(self, pystray, image) -> None:
        menu_items = [
            pystray.MenuItem("打开控制台", self._open_window, default=True),
            pystray.MenuItem(
                self._usb_label,
                self._toggle_usb,
                enabled=lambda _item: self.on_usb_toggle is not None,
            ),
            pystray.MenuItem("退出", self._exit),
        ]
        self.icon = pystray.Icon(
            "KV Agent Desk",
            image,
            "KV Agent Desk",
            menu=pystray.Menu(*menu_items),
        )
        self._tray_thread = threading.Thread(
            target=self._run_tray,
            name="agent-dashboard-tray",
            daemon=True,
        )
        self._tray_thread.start()
        if not self._tray_ready.wait(5.0) or self._tray_error is not None:
            self._stop_tray()
            raise RuntimeError("Windows system tray could not be started") from self._tray_error

    def watch_stop_event(self) -> threading.Thread:
        def wait_for_exit() -> None:
            self.stop_event.wait()
            self._exit(self.icon)

        thread = threading.Thread(target=wait_for_exit, name="agent-dashboard-stop-watch", daemon=True)
        thread.start()
        return thread


def run_desktop(
    url: str,
    stop_event: threading.Event,
    *,
    background: bool = False,
    on_usb_toggle: Callable[[], object] | None = None,
) -> None:
    """Run the native WebView2 window on the caller's main thread and a tray loop.

    ``on_usb_toggle`` is a no-argument toggle callback. If it returns a bool,
    that value is treated as the new paused state for the tray label.
    """
    global _active_shell

    if sys.platform != "win32":
        raise RuntimeError("The native desktop shell is available on Windows only")
    if stop_event.is_set():
        return
    runtime_version = webview2_runtime_version()
    if runtime_version is None:
        raise RuntimeError("Microsoft Edge WebView2 Runtime is required")

    webview, pystray, Image, ImageDraw = _load_gui_dependencies()
    window = webview.create_window(
        "KV Agent Desk",
        url,
        width=1080,
        height=760,
        min_size=(640, 480),
        hidden=background,
    )
    shell = _DesktopShell(window, stop_event, on_usb_toggle=on_usb_toggle)
    with _active_shell_lock:
        if _active_shell is not None:
            raise RuntimeError("A desktop shell is already running")
        _active_shell = shell

    try:
        shell.start_tray(pystray, _tray_image(Image, ImageDraw))
        shell.watch_stop_event()
        # pywebview's WinForms/WebView2 loop must run on the main thread.
        webview.start(gui="edgechromium")
    finally:
        shell._exit(shell.icon)
        with _active_shell_lock:
            if _active_shell is shell:
                _active_shell = None
