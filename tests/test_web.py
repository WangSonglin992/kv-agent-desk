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
            self.assertIn("blob:", response.headers["Content-Security-Policy"])

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

    def test_ui_catalogue_and_synthetic_preview_are_available(self):
        with urllib.request.urlopen(self.url + "/api/ui") as response:
            ui = json.load(response)
        self.assertEqual(ui["active_id"], "classic")
        self.assertEqual({p["id"] for p in ui["profiles"]}, {"classic", "aurora", "paper"})
        with urllib.request.urlopen(self.url + "/api/ui/preview?id=paper") as response:
            self.assertTrue(response.read().startswith(b"\x89PNG\r\n\x1a\n"))
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.url + "/api/ui/export?id=missing")

    def test_ui_switch_renders_immediately_without_provider_collection_and_survives_restart(self):
        before = self.dashboard.png
        with patch.object(self.dashboard, "collect", side_effect=AssertionError("must not poll")):
            self.post("/api/settings", {"ui_id": "paper"}, self.dashboard.token).close()
        self.assertEqual(self.dashboard.ui_id, "paper")
        self.assertNotEqual(before, self.dashboard.png)
        restored = Dashboard(self.dashboard.args)
        self.assertEqual(restored.ui_id, "paper")
        self.assertTrue((Path(self.folder.name) / "last-frame-meta.json").is_file())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/settings", {"ui_id": "not_installed"}, self.dashboard.token)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.dashboard.ui_id, "paper")

    def test_ui_import_requires_existing_control_security(self):
        profile = self.dashboard.ui_profiles.export("paper")
        profile.update(id="custom_paper", name="Synthetic custom")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/ui/import", {"profile": profile})
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/ui/import", {"profile": profile}, self.dashboard.token, "https://example.invalid")
        self.assertEqual(caught.exception.code, 403)
        self.assertFalse(self.dashboard.ui_profiles.contains("custom_paper"))

    def test_custom_ui_import_export_and_restart_never_include_account_data(self):
        profile = self.dashboard.ui_profiles.export("paper")
        profile.update(id="custom_paper", name="Synthetic custom")
        profile["colors"]["codex"] = "#176D9A"
        self.dashboard.profiles.update("codex", {"plan_name": "private account label"})
        with patch.object(self.dashboard, "collect", side_effect=AssertionError("must not poll")):
            with self.post("/api/ui/import", {"profile": profile}, self.dashboard.token) as response:
                self.assertEqual(json.load(response)["ui"]["active_id"], "custom_paper")
        with urllib.request.urlopen(self.url + "/api/ui/export") as response:
            self.assertIn("custom_paper.kvui.json", response.headers["Content-Disposition"])
            exported = response.read()
        self.assertNotIn(b"private account label", exported)
        self.assertNotIn(b"tokens", exported)
        self.assertNotIn(b"api_key", exported)
        self.assertEqual(json.loads(exported)["id"], "custom_paper")
        self.assertEqual(Dashboard(self.dashboard.args).ui_id, "custom_paper")

    def test_invalid_ui_import_preserves_current_selection_and_rejects_large_payload(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/ui/import", {"profile": {"schema_version": 1, "id": "bad", "name": "bad",
                                                     "layout": "paper", "script": "forbidden"}}, self.dashboard.token)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.dashboard.ui_id, "classic")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/ui/import", {"profile": {"name": "x" * 75000}}, self.dashboard.token)
        self.assertEqual(caught.exception.code, 400)

    def test_asset_route_is_an_exact_allowlist(self):
        for name in ("chatgpt.png", "glm.png", "google.png"):
            with urllib.request.urlopen(self.url + "/assets/" + name) as response:
                self.assertEqual(response.headers["Content-Type"], "image/png")
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.url + "/assets/../settings.py")


if __name__ == "__main__":
    unittest.main()
