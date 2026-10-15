import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from agent_dashboard.app import Dashboard, LoopbackServer, handler, APP_ID, existing_server


class WebTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        args = argparse.Namespace(preview_only=True, demo=False, state_dir=Path(self.folder.name),
                                  stale_seconds=300, lcd_height=None)
        self.dashboard = Dashboard(args)
        self.server = LoopbackServer(("127.0.0.1", 0), handler(self.dashboard))
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()

    def post(self, path, data, token=None, origin=None):
        headers = {"Content-Type": "application/json", "X-Dashboard-Token": token or "wrong"}
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(self.url + path, json.dumps(data).encode(), headers, method="POST")
        return urllib.request.urlopen(req, timeout=2)

    def test_state_and_frame_are_available_before_collectors_finish(self):
        with urllib.request.urlopen(self.url + "/api/state") as response:
            self.assertEqual(json.load(response)["app"], APP_ID)
        with urllib.request.urlopen(self.url + "/frame.png") as response:
            self.assertTrue(response.read().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_commands_require_token_and_same_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/display", {"enabled": True})
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/display", {"enabled": True}, self.dashboard.token, "https://example.org")
        self.assertEqual(caught.exception.code, 403)
        self.post("/api/display", {"enabled": True}, self.dashboard.token).close()
        self.assertFalse(self.dashboard.usb_enabled)  # --preview-only remains a hard boundary.

    def test_dns_rebinding_host_is_rejected(self):
        req = urllib.request.Request(self.url + "/api/state", headers={"Host": "example.org"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req)
        self.assertEqual(caught.exception.code, 403)

    def test_stop_command_is_discoverable_and_only_sets_our_stop_flag(self):
        self.assertTrue(existing_server(self.url, True))
        self.assertTrue(self.dashboard.stop.wait(timeout=1))

    def test_demo_cannot_push_fake_data_to_usb(self):
        self.dashboard.args.demo = True
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/display", {"enabled": True}, self.dashboard.token)
        self.assertEqual(caught.exception.code, 400)

    def test_second_server_cannot_bind_our_port(self):
        with self.assertRaises(OSError):
            LoopbackServer(("127.0.0.1", self.server.server_port), handler(self.dashboard))

    def test_subscription_profile_save_and_clear_are_persistent_and_validated(self):
        with self.post("/api/subscriptions", {"provider": "codex", "profile": {"plan_spec": "月付", "expires_at": "2026-11-15"}}, self.dashboard.token) as response:
            self.assertTrue(json.load(response)["ok"])
        with urllib.request.urlopen(self.url + "/api/state") as response:
            self.assertEqual(json.load(response)["subscription_profiles"]["codex"]["expires_at"], "2026-11-15")
        self.assertTrue((Path(self.folder.name) / "subscriptions.json").is_file())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/subscriptions", {"provider": "codex", "profile": {"expires_at": "2026-02-31"}}, self.dashboard.token)
        self.assertEqual(caught.exception.code, 400)
        self.post("/api/subscriptions", {"provider": "codex", "profile": {}}, self.dashboard.token).close()
        self.assertEqual(self.dashboard.profiles.get_all()["codex"]["expires_at"], "")

    def test_brightness_settings_are_validated_and_persisted(self):
        self.post("/api/settings", {"brightness": 45}, self.dashboard.token).close()
        self.assertEqual(self.dashboard.brightness, 45)

        with urllib.request.urlopen(self.url + "/api/state") as response:
            self.assertEqual(json.load(response)["settings"]["brightness"], 45)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/settings", {"brightness": 101}, self.dashboard.token)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.dashboard.brightness, 45)

    def test_show_request_is_queued_while_native_window_initialises(self):
        self.dashboard.desktop_mode = True
        with patch("agent_dashboard.desktop.request_show", return_value=False):
            self.assertTrue(existing_server(self.url, show=True))
        self.assertTrue(self.dashboard.show_requested.is_set())

    def test_unlimited_five_hour_override_is_optional_and_does_not_change_raw_data(self):
        agent = {"provider": "codex", "subscription": {"five_hour": {"known": False}}}
        self.assertFalse(self.dashboard.present_agent(agent)["subscription"]["five_hour"]["known"])
        self.post("/api/settings", {"codex_no_five_hour_limit": True}, self.dashboard.token).close()
        self.assertTrue(self.dashboard.present_agent(agent)["subscription"]["five_hour"]["unlimited"])
        self.assertFalse(agent["subscription"]["five_hour"]["known"])
        self.post("/api/settings", {"codex_no_five_hour_limit": False}, self.dashboard.token).close()
        self.assertFalse(self.dashboard.present_agent(agent)["subscription"]["five_hour"]["known"])


if __name__ == "__main__":
    unittest.main()
