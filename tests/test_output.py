import argparse
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw
from agent_dashboard.app import Dashboard


class FakeDisplay:
    def __init__(self):
        self.jpeg = None
    def status(self):
        return {"connected": True, "width": 1920, "height": 462, "state": "connected"}
    def send_jpeg(self, data):
        self.jpeg = data
    def close(self):
        pass


class OutputTests(unittest.TestCase):
    def test_physical_frame_rotates_180_without_changing_preview_canvas(self):
        with tempfile.TemporaryDirectory() as folder:
            args = argparse.Namespace(preview_only=False, demo=False, state_dir=Path(folder),
                                      stale_seconds=300, lcd_height=462, rotation=180)
            dashboard = Dashboard(args)
            dashboard.device = FakeDisplay()
            dashboard.display_status = dashboard.device.status()
            frame = Image.new("RGB", (1920, 462), (0, 0, 240))
            ImageDraw.Draw(frame).rectangle((0, 0, 900, 200), fill=(240, 0, 0))
            dashboard.send(frame)
            physical = Image.open(BytesIO(dashboard.device.jpeg))
            self.assertGreater(physical.getpixel((1900, 440))[0], 220)
            self.assertGreater(physical.getpixel((20, 20))[2], 220)
            self.assertEqual(frame.getpixel((20, 20)), (240, 0, 0))
            self.assertEqual(dashboard.display_status["rotation"], 180)


if __name__ == "__main__":
    unittest.main()
