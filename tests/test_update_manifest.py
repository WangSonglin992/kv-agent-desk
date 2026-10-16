import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.create_update_manifest import write_manifest


class UpdateManifestTests(unittest.TestCase):
    def test_manifest_contains_only_fixed_public_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "KV-Agent-Desk.exe"
            exe.write_bytes(b"synthetic public executable")
            digest = hashlib.sha256(exe.read_bytes()).hexdigest()
            exe.with_name(exe.name + ".sha256").write_text(f"{digest}  {exe.name}\n", encoding="ascii")
            target = Path(folder) / "update.json"
            payload = write_manifest(exe, target, "1.2.3")
            self.assertEqual(payload["tag_name"], "v1.2.3")
            self.assertEqual({a["name"] for a in payload["assets"]}, {exe.name, exe.name + ".sha256"})
            self.assertNotIn(folder, target.read_text())
            self.assertEqual(json.loads(target.read_text()), payload)
            self.assertTrue(all(a["browser_download_url"].startswith(
                "https://github.com/WangSonglin992/kv-agent-desk/releases/download/v1.2.3/") for a in payload["assets"]))

    def test_bad_checksum_or_version_never_produces_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "KV-Agent-Desk.exe"
            exe.write_bytes(b"synthetic")
            exe.with_name(exe.name + ".sha256").write_text("0" * 64 + "  " + exe.name)
            target = Path(folder) / "update.json"
            for version in ("1.0.0", "../latest", "v1.2.3", "1.0.0-beta"):
                with self.subTest(version=version), self.assertRaises(ValueError):
                    write_manifest(exe, target, version)
                self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
