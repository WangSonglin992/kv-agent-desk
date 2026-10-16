import argparse
import hashlib
import json
from datetime import datetime
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from agent_dashboard.app import Dashboard, parser
from agent_dashboard.retention import LastGoodStore


class FakeDevice:
    def __init__(self):
        self.frames = []
    def status(self):
        return {"connected": True, "width": 1920, "height": 462}
    def send_jpeg(self, frame):
        self.frames.append(frame)
    def close(self):
        pass


class StartupRestoreTests(unittest.TestCase):
    def test_pre_editor_profile_hash_still_restores_the_last_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = Dashboard(parser().parse_args(["--no-browser", "--state-dir", folder]))
            old_profile = app.current_profile()
            old_profile.pop("elements", None)
            old_hash = hashlib.sha256(json.dumps(old_profile, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            (root / "last-frame-meta.json").write_text(json.dumps({"ui_id": old_profile["id"], "profile_hash": old_hash}))
            Image.new("RGB", (1920, 480), (220, 50, 20)).save(root / "last-frame.png")
            app.device = FakeDevice()
            app.display_status = app.device.status()
            app.adapters = [object()]
            app.restore_frame()
            self.assertTrue(app.restored_before_refresh)
            pixel = Image.open(BytesIO(app.device.frames[0])).getpixel((100, 100))
            self.assertGreater(pixel[0], 210)

    def test_restore_sends_saved_picture_before_any_provider_query(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new("RGB", (1920, 480), (220, 50, 20)).save(root / "last-frame.png")
            args = parser().parse_args(["--no-browser", "--state-dir", folder])
            app = Dashboard(args)
            app.device = FakeDevice()
            app.display_status = app.device.status()
            app.adapters = [object()]  # Any attempt to collect would fail.
            app.restore_frame()
            self.assertTrue(app.restored_before_refresh)
            self.assertEqual(len(app.device.frames), 1)
            pixel = Image.open(BytesIO(app.device.frames[0])).getpixel((100, 100))
            self.assertGreater(pixel[0], 210)

    def test_brightness_persists_and_dims_output_without_changing_cached_source(self):
        with tempfile.TemporaryDirectory() as folder:
            args = parser().parse_args(["--no-browser", "--state-dir", folder])
            app = Dashboard(args)
            app.update_settings({"brightness": 20})
            reloaded = Dashboard(args)
            self.assertEqual(reloaded.brightness, 20)
            reloaded.device = FakeDevice()
            reloaded.display_status = reloaded.device.status()
            original = Image.new("RGB", (1920, 462), (240, 0, 0))
            reloaded.send(original)
            pixel = Image.open(BytesIO(reloaded.device.frames[0])).getpixel((100, 100))
            self.assertTrue(44 <= pixel[0] <= 52)
            self.assertEqual(original.getpixel((100, 100)), (240, 0, 0))

    def test_refresh_updates_one_provider_without_erasing_other_cached_cards(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = LastGoodStore(root / "last-good.json")
            stamp = datetime.now().astimezone().isoformat()
            for provider in ("codex", "zcode"):
                store.merge({"provider": provider, "name": provider, "available": True, "state": "working",
                             "updated_at": stamp, "active_tasks": 3})
            store.save()
            app = Dashboard(parser().parse_args(["--no-browser", "--state-dir", folder]))
            class Adapter:
                def collect(self):
                    return {"provider": "codex", "name": "Codex", "available": True, "state": "idle",
                            "updated_at": stamp, "active_tasks": 0, "task": "private text must not persist"}
            app.adapters = [Adapter()]
            app.refresh_agent(0)
            self.assertEqual(app.latest_agents[0]["active_tasks"], 0)
            self.assertEqual(app.latest_agents[1]["active_tasks"], 3)
            self.assertNotIn("private text", (root / "last-good.json").read_text(encoding="utf-8"))
