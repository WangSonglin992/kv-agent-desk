"""Read local agent telemetry, render once, send the same picture to browser/USB."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from PIL import Image, ImageEnhance
from . import __version__

from .render import render
from .retention import LastGoodStore
from .subscriptions import SubscriptionProfiles
from .accounts.monitor import AccountMonitor
from .settings import AppSettings, get_autostart, set_autostart
from .web import PAGE

APP_ID = "kv-agent-desk"
PROVIDERS = (("codex", "Codex"), ("zcode", "ZCode"), ("antigravity", "Antigravity"))


def display_data(agent: dict) -> dict:
    """Keep only visible telemetry, never private task titles or tool arguments."""
    allowed = {"provider", "name", "available", "state", "state_label", "observed_state", "stale",
               "project", "updated_at", "session_id", "active_tasks", "active_tasks_known", "tokens",
               "subscription", "source", "detail", "retained_fields", "freshness_note", "last_good_at"}
    return {key: deepcopy(value) for key, value in agent.items() if key in allowed}


def startup_command() -> list[str]:
    executable = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1] / "dist" / "KV-Agent-Desk.exe"
    if not executable.is_file():
        raise ValueError("请先构建 Windows 应用，再启用开机自启")
    return [str(executable.resolve()), "--background"]


def state_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KV-Agent-Desk"


def empty_agent(provider: str, name: str, detail: str = "正在读取本机会话") -> dict:
    return {"provider": provider, "name": name, "available": False, "state": "unknown",
            "state_label": "状态未知", "project": "", "task": "", "updated_at": None,
            "active_tasks": None,
            "progress": {"known": False, "completed": 0, "total": 0, "steps": []},
            "tokens": {"known": False, "total": None, "scope": "today"},
            "subscription": {"name": {"codex": "ChatGPT", "zcode": "ZCode", "antigravity": "Antigravity"}.get(provider, name),
                             "five_hour": {"known": False, "remaining_percent": None},
                             "weekly": {"known": False, "remaining_percent": None}},
            "source": "", "detail": detail}


def demo_agents() -> list[dict]:
    """Synthetic examples only; never derived from local account snapshots."""
    result = []
    for provider, name, tasks, usage in [
        ("codex", "Codex", 2, 123_456),
        ("zcode", "ZCode", 1, 65_432),
        ("antigravity", "Antigravity", None, None),
    ]:
        agent = empty_agent(provider, name)
        agent.update(available=True, state="working" if tasks else "unknown",
                     state_label="工作中" if tasks else "状态未知",
                     active_tasks=tasks, updated_at=datetime.now().astimezone().isoformat(),
                     tokens={"known": usage is not None, "total": usage, "scope": "today"},
                     source="DEMO", detail="Synthetic example")
        result.append(agent)
    return result


class Dashboard:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.lock = threading.Lock()
        self.settings_lock = threading.RLock()
        self.stop = threading.Event()
        self.show_requested = threading.Event()
        self.desktop_mode = False
        self.settings = AppSettings(None if args.demo else args.state_dir / "settings.json")
        prefs = self.settings.get()
        overrides = {key: getattr(args, key) for key in ("rotation", "lcd_height") if getattr(args, key, None) is not None}
        if overrides:
            prefs = self.settings.update(overrides)
        self.usb_enabled = prefs["usb_enabled"] and not args.preview_only and not args.demo
        self.rotation = getattr(args, "rotation", None)
        self.rotation = prefs["rotation"] if self.rotation is None else self.rotation
        self.lcd_height = getattr(args, "lcd_height", None) or prefs["lcd_height"]
        self.brightness = prefs["brightness"]
        self.token = secrets.token_urlsafe(32)
        self.store = LastGoodStore(None if args.demo else args.state_dir / "last-good.json", args.stale_seconds)
        self.store.values = {key: display_data(value) for key, value in self.store.values.items()}
        self.profiles = SubscriptionProfiles(None if args.demo else args.state_dir / "subscriptions.json")
        self.accounts = AccountMonitor(None if args.demo else args.state_dir / "accounts-last-good.json", self.stop, args.demo)
        self.adapters = []
        self.device = None
        self.last_connect = 0.0
        self._last_frame_write = 0.0
        self._last_raw_frame: Image.Image | None = None
        self.restored_before_refresh = False
        self._error_logged = set()
        self.display_status = {"connected": False, "state": "paused", "detail": "USB 输出尚未启动",
                               "width": 1920, "height": self.lcd_height, "device": "0416:5408"}
        self.latest_agents = [display_data(self.store.merge(display_data(empty_agent(key, name)))) for key, name in PROVIDERS]
        initial_agents = [self.present_agent(agent) for agent in self.latest_agents]
        self.snapshot = {"app": APP_ID, "version": __version__, "demo": args.demo,
                         "agents": initial_agents,
                         "accounts": self.accounts.snapshot(),
                         "display": self.display_status, "usb_enabled": self.usb_enabled,
                         "updated_at": datetime.now().astimezone().isoformat()}
        self.png = self.encode(self.dim(render(self.snapshot)), "PNG")

    @staticmethod
    def encode(image: Image.Image, kind: str, **options) -> bytes:
        output = BytesIO()
        image.save(output, format=kind, **options)
        return output.getvalue()

    def initialise(self) -> None:
        from importlib import import_module
        class UnavailableAdapter:
            def __init__(self, provider, name):
                self.provider, self.name = provider, name
            def collect(self):
                return empty_agent(self.provider, self.name, "接入模块暂不可用；保留最近记录")
        self.adapters = []
        for provider, name, root in (("codex", "Codex", self.args.codex_root), ("zcode", "ZCode", self.args.zcode_root),
                                     ("antigravity", "Antigravity", None)):
            try:
                module = import_module(".adapters." + provider, __package__)
                adapter = getattr(module, name + "Adapter")(root)
            except Exception:
                logging.exception("Could not initialise %s adapter", provider)
                adapter = UnavailableAdapter(provider, name)
            self.adapters.append(adapter)
        from .display import USBDisplay
        self.device = USBDisplay()

    def update_settings(self, fields: dict) -> dict:
        if not isinstance(fields, dict):
            raise ValueError("设置格式不正确")
        with self.settings_lock:
            previous = self.settings.get()
            if "auto_start" in fields:
                if type(fields["auto_start"]) is not bool:
                    raise ValueError("开机自启应为开关值")
                set_autostart(fields["auto_start"], startup_command())
            try:
                prefs = self.settings.update(fields)
            except Exception:
                if "auto_start" in fields:
                    set_autostart(previous["auto_start"], startup_command())
                raise
            with self.lock:
                self.usb_enabled = prefs["usb_enabled"] and not self.args.demo and not self.args.preview_only
                self.rotation = prefs["rotation"]
                self.lcd_height = prefs["lcd_height"]
                self.brightness = prefs["brightness"]
                self.snapshot["usb_enabled"] = self.usb_enabled
            return prefs

    def toggle_usb(self) -> bool:
        self.update_settings({"usb_enabled": not self.usb_enabled})
        return not self.usb_enabled

    def dim(self, image: Image.Image) -> Image.Image:
        return image if self.brightness == 100 else ImageEnhance.Brightness(image).enhance(self.brightness / 100)

    def present_agent(self, agent: dict) -> dict:
        result = self.profiles.apply(agent)
        if result.get("provider") == "codex" and self.settings.get()["codex_no_five_hour_limit"]:
            result.setdefault("subscription", {})["five_hour"] = {
                "known": True, "remaining_percent": 100.0, "reset_at": None, "unlimited": True}
        return result

    def refresh_agent(self, index: int) -> dict:
        adapter = self.adapters[index]
        provider, name = PROVIDERS[index]
        try:
            current = display_data(adapter.collect())
            self._error_logged.discard(provider)
        except Exception as exc:
            current = display_data(empty_agent(provider, name, f"读取失败：{type(exc).__name__}；保留最近记录"))
            if provider not in self._error_logged:
                logging.exception("%s adapter error", provider)
                self._error_logged.add(provider)
        result = display_data(self.store.merge(current))
        self.store.values[provider] = display_data(self.store.values[provider])
        with self.lock:
            self.latest_agents[index] = result
        try:
            self.store.save()
        except OSError:
            logging.warning("Could not save last-good display cache", exc_info=True)
        return result

    def collect(self) -> list[dict]:
        if self.args.demo:
            return demo_agents()
        return [self.refresh_agent(index) for index in range(len(self.adapters))]

    def send(self, image: Image.Image) -> None:
        with self.lock:
            enabled = self.usb_enabled
        if not enabled:
            if self.device:
                self.device.close()
            self.display_status.update(connected=False, state="paused", detail="USB 输出已暂停，可使用官方软件")
            return
        if not self.device:
            return
        try:
            if not self.device.status().get("connected"):
                if time.monotonic() - self.last_connect < 10:
                    return
                self.last_connect = time.monotonic()
                self.display_status = self.device.connect()
            if self.display_status.get("connected"):
                width = self.display_status.get("width", 1920)
                height = self.lcd_height
                frame = image if image.size == (width, height) else image.resize((width, height), Image.Resampling.LANCZOS)
                frame = self.dim(frame)
                if self.rotation == 180:
                    frame = frame.transpose(Image.Transpose.ROTATE_180)
                jpeg = self.encode(frame, "JPEG", quality=90, subsampling=0)
                self.device.send_jpeg(jpeg)
                self.display_status = self.device.status()
                self.display_status["height"] = height
                self.display_status["rotation"] = self.rotation
                self.display_status["brightness"] = self.brightness
                self.display_status["brightness_mode"] = "software"
        except Exception as exc:
            self.device.close()
            self.display_status = {"connected": False, "state": "error", "detail": str(exc),
                                   "width": 1920, "height": self.lcd_height, "device": "0416:5408"}
            self.last_connect = time.monotonic()
            logging.warning("USB output error: %s", exc)

    def tick(self, agents: list[dict] | None = None) -> None:
        agents = self.collect() if agents is None else agents
        agents = [self.present_agent(agent) for agent in agents]
        state = {"app": APP_ID, "version": __version__, "agents": agents, "demo": self.args.demo,
                 "accounts": self.accounts.snapshot(),
                 "display": deepcopy(self.display_status), "usb_enabled": self.usb_enabled,
                 "updated_at": datetime.now().astimezone().isoformat()}
        image = render(state)
        self.send(image)
        state["display"] = deepcopy(self.display_status)
        image = render(state)
        self._last_raw_frame = image
        with self.lock:
            self.snapshot, self.png = state, self.encode(self.dim(image), "PNG")
        self.persist_frame()

    def persist_frame(self, force: bool = False) -> None:
        if self.args.demo or self._last_raw_frame is None or (not force and time.monotonic() - self._last_frame_write < 30):
            return
        try:
            self.args.state_dir.mkdir(parents=True, exist_ok=True)
            path = self.args.state_dir / "last-frame.png"
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(self.encode(self._last_raw_frame, "PNG"))
            os.replace(temporary, path)
            self._last_frame_write = time.monotonic()
        except OSError:
            logging.warning("Could not persist display frame", exc_info=True)

    def restore_frame(self) -> None:
        image = None
        path = self.args.state_dir / "last-frame.png"
        if not self.args.demo and path.is_file():
            try:
                with Image.open(path) as saved:
                    if saved.size == (1920, 480):
                        image = saved.convert("RGB")
            except (OSError, ValueError):
                pass
        if image is None:
            image = render(self.snapshot)
        self._last_raw_frame = image
        self.send(image)
        with self.lock:
            self.png = self.encode(self.dim(image), "PNG")
        self.restored_before_refresh = True

    def update_loop(self) -> None:
        first_round = True
        while not self.stop.is_set():
            started = time.monotonic()
            if self.args.demo:
                with self.lock:
                    self.latest_agents = demo_agents()
            else:
                for index in range(len(self.adapters)):
                    if self.stop.is_set():
                        return
                    self.refresh_agent(index)
            if first_round:
                self.accounts.start()
                first_round = False
            self.stop.wait(max(.1, self.args.interval - (time.monotonic() - started)))

    def run(self) -> None:
        try:
            self.restore_frame()
            updater = threading.Thread(target=self.update_loop, daemon=True, name="agent-data-updates")
            updater.start()
            while not self.stop.is_set():
                started = time.monotonic()
                with self.lock:
                    agents = deepcopy(self.latest_agents)
                self.tick(agents)
                self.stop.wait(max(.05, 1.0 - (time.monotonic() - started)))
        except Exception:
            logging.exception("Dashboard worker failed")
            self.stop.set()
        finally:
            self.persist_frame(force=True)
            if self.device:
                self.device.close()


class LoopbackServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def handler(dashboard: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send_data(self, status: int, kind: str, content: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def permitted_host(self) -> bool:
            return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def do_GET(self):
            if not self.permitted_host():
                self.send_data(403, "text/plain", b"Forbidden host")
                return
            path = self.path.split("?", 1)[0]
            if path == "/":
                body = PAGE.replace("__TOKEN__", json.dumps(dashboard.token)).encode("utf-8")
                self.send_data(200, "text/html; charset=utf-8", body)
            elif path == "/api/state":
                with dashboard.lock:
                    state = deepcopy(dashboard.snapshot)
                state["subscription_profiles"] = dashboard.profiles.get_all()
                state["settings"] = dashboard.settings.get()
                state["auto_start_registered"] = get_autostart()
                state["restored_before_refresh"] = dashboard.restored_before_refresh
                data = json.dumps(state, ensure_ascii=False).encode("utf-8")
                self.send_data(200, "application/json; charset=utf-8", data)
            elif path == "/frame.png":
                with dashboard.lock:
                    data = dashboard.png
                self.send_data(200, "image/png", data)
            else:
                self.send_data(404, "text/plain", b"Not found")

        def do_POST(self):
            origin = self.headers.get("Origin")
            origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            if (not self.permitted_host() or (origin and origin not in origins)
                    or not secrets.compare_digest(self.headers.get("X-Dashboard-Token", ""), dashboard.token)):
                self.send_data(403, "text/plain", b"Forbidden control request")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 4096:
                    raise ValueError("Body too large")
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, TypeError):
                self.send_data(400, "text/plain", b"Invalid JSON")
                return
            if self.path == "/api/display" and isinstance(data, dict) and isinstance(data.get("enabled"), bool):
                if dashboard.args.demo and data["enabled"]:
                    self.send_data(400, "text/plain", b"Demo mode does not output to USB")
                    return
                try:
                    dashboard.update_settings({"usb_enabled": data["enabled"]})
                except (ValueError, OSError):
                    self.send_data(500, "text/plain", b"Could not save USB setting")
                    return
                self.send_data(200, "application/json", b'{"ok":true}')
            elif self.path == "/api/settings" and isinstance(data, dict):
                try:
                    prefs = dashboard.update_settings(data)
                except ValueError as exc:
                    self.send_data(400, "application/json; charset=utf-8", json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"))
                    return
                except (OSError, NotImplementedError):
                    self.send_data(500, "application/json; charset=utf-8", json.dumps({"error": "设置保存失败，请检查当前用户权限"}, ensure_ascii=False).encode("utf-8"))
                    return
                self.send_data(200, "application/json", json.dumps({"ok": True, "settings": prefs}).encode("utf-8"))
            elif self.path == "/api/show":
                from .desktop import request_show
                shown = request_show()
                queued = not shown and dashboard.desktop_mode
                if queued:
                    dashboard.show_requested.set()
                self.send_data(200, "application/json", json.dumps({"shown": shown, "queued": queued}).encode("utf-8"))
            elif self.path == "/api/subscriptions" and isinstance(data, dict):
                try:
                    profiles = dashboard.profiles.update(data.get("provider"), data.get("profile"))
                except ValueError as exc:
                    self.send_data(400, "application/json; charset=utf-8",
                                   json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"))
                    return
                except OSError:
                    self.send_data(500, "application/json; charset=utf-8",
                                   json.dumps({"error": "套餐信息保存失败，请检查本机配置目录"}, ensure_ascii=False).encode("utf-8"))
                    return
                self.send_data(200, "application/json; charset=utf-8",
                               json.dumps({"ok": True, "profiles": profiles}, ensure_ascii=False).encode("utf-8"))
            elif self.path == "/api/stop":
                self.send_data(200, "application/json", b'{"ok":true}')
                dashboard.stop.set()
            else:
                self.send_data(400, "text/plain", b"Unknown control")
    return Handler


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="KV 9.16 Windows agent dashboard")
    p.add_argument("--preview-only", action="store_true", help="Do not open/write the USB display")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--background", action="store_true", help="Run the desktop app in the system tray")
    p.add_argument("--browser-only", action="store_true", help="Use an external browser instead of the native window")
    p.add_argument("--demo", action="store_true", help="Explicitly labelled demo; USB output is disabled")
    p.add_argument("--once", action="store_true", help="Collect/render once, save frame.png/state.json and exit")
    p.add_argument("--stop", action="store_true", help="Stop an existing local dashboard")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--interval", type=float, default=2)
    p.add_argument("--stale-seconds", type=float, default=300)
    p.add_argument("--lcd-height", type=int, choices=(462, 480), default=None)
    p.add_argument("--rotation", type=int, choices=(0, 180), default=None, help="USB frame rotation (otherwise restores saved rotation)")
    p.add_argument("--codex-root", type=Path, default=None)
    p.add_argument("--zcode-root", type=Path, default=None)
    p.add_argument("--state-dir", type=Path, default=state_directory())
    return p


def existing_server(url: str, stop: bool = False, show: bool = False) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/state", timeout=1) as response:
            state = json.load(response)
        if state.get("app") != APP_ID:
            return False
        if stop or show:
            with urllib.request.urlopen(url + "/", timeout=1) as response:
                page = response.read().decode("utf-8")
            import re
            match = re.search(r'const token=("[^"]+");', page)
            if not match:
                return False
            token = json.loads(match.group(1))
            req = urllib.request.Request(url + ("/api/stop" if stop else "/api/show"), data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json", "X-Dashboard-Token": token})
            with urllib.request.urlopen(req, timeout=2) as response:
                if show:
                    result = json.load(response)
                    return bool(result.get("shown") or result.get("queued"))
        return True
    except (OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not 1 <= args.port <= 65535 or args.interval < .5 or args.stale_seconds < 0:
        raise SystemExit("Invalid port/interval/stale timeout")
    url = f"http://127.0.0.1:{args.port}"
    if args.stop:
        return 0 if existing_server(url, True) else 1
    args.state_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[RotatingFileHandler(args.state_dir / "dashboard.log", maxBytes=1_000_000,
                                                      backupCount=2, encoding="utf-8")])
    if not args.once and existing_server(url):
        if args.browser_only and not args.no_browser:
            webbrowser.open(url)
        elif not args.no_browser and not args.background:
            if not existing_server(url, show=True):
                webbrowser.open(url)
        return 0
    dashboard = Dashboard(args)
    if args.once:
        dashboard.initialise()
        dashboard.tick()
        (args.state_dir / "frame.png").write_bytes(dashboard.png)
        (args.state_dir / "state.json").write_text(json.dumps(dashboard.snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        if dashboard.device:
            dashboard.device.close()
        return 0
    try:
        server = LoopbackServer(("127.0.0.1", args.port), handler(dashboard))
    except OSError as exc:
        logging.error("Could not bind local dashboard: %s", exc)
        if os.name == "nt" and not args.no_browser:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f"本机端口 {args.port} 已被占用。请使用 --port 指定其他端口。", "KV Agent Desk", 0x10)
        return 1
    server.daemon_threads = True
    server.timeout = .5
    # Publish the control page first so expensive first-day indexing doesn't block preview.
    def worker():
        try:
            dashboard.initialise()
            dashboard.run()
        except Exception:
            logging.exception("Could not initialise dashboard")
            dashboard.stop.set()
    thread = threading.Thread(target=worker, name="dashboard", daemon=True)
    thread.start()
    signal.signal(signal.SIGINT, lambda *_: dashboard.stop.set())
    def serve():
        try:
            while not dashboard.stop.is_set():
                server.handle_request()
        except (OSError, ValueError):
            if not dashboard.stop.is_set():
                raise
    native = (not args.no_browser or args.background) and not args.browser_only
    dashboard.desktop_mode = native
    http_thread = None
    try:
        if native:
            from .desktop import run_desktop, request_show
            def show_pending():
                while not dashboard.stop.is_set():
                    if dashboard.show_requested.wait(.2) and request_show():
                        dashboard.show_requested.clear()
                    dashboard.stop.wait(.1)
            threading.Thread(target=show_pending, daemon=True, name="desktop-show-requests").start()
            if not args.demo:
                try:
                    set_autostart(dashboard.settings.get()["auto_start"], startup_command())
                except (OSError, NotImplementedError, ValueError):
                    logging.exception("Could not configure current-user startup")
            http_thread = threading.Thread(target=serve, daemon=True, name="dashboard-http")
            http_thread.start()
            run_desktop(url, dashboard.stop, background=args.background, on_usb_toggle=dashboard.toggle_usb)
        else:
            if args.browser_only and not args.no_browser:
                webbrowser.open(url)
            serve()
    finally:
        dashboard.stop.set()
        server.server_close()
        thread.join(timeout=8)
        if http_thread:
            http_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
