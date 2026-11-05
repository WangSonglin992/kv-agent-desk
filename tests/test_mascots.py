from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from agent_dashboard import mascots


ASSET_NAMES = {
    "codex": "mascot_chatgpt.png",
    "zcode": "mascot_glm.png",
    "antigravity": "mascot_gemini.png",
}
STATE_ROWS = {"idle": 0, "working": 1, "busy": 2}
STATE_CYCLES = {"idle": 4.0, "working": 2.0, "busy": 0.65}


def cell_color(provider_index: int, row: int, column: int) -> tuple[int, int, int, int]:
    return (
        10 + provider_index * 70 + row * 15 + column * 3,
        row * 70 + column * 11,
        90 + row * 35 + column * 9,
        80 + row * 40 + column * 13,
    )


def write_atlas(path: Path, provider_index: int, width: int = 17, height: int = 10) -> None:
    atlas = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(atlas)
    for row in range(3):
        top, bottom = row * height // 3, (row + 1) * height // 3
        for column in range(4):
            left, right = column * width // 4, (column + 1) * width // 4
            draw.rectangle(
                (left, top, right - 1, bottom - 1),
                fill=cell_color(provider_index, row, column),
            )
    atlas.save(path, format="PNG")


class MascotFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.assets_dir = Path(tempdir.name)

        mascots._load_atlas.cache_clear()
        asset_patch = patch.object(mascots, "_ASSET_DIR", self.assets_dir)
        asset_patch.start()
        self.addCleanup(asset_patch.stop)
        self.addCleanup(mascots._load_atlas.cache_clear)

        for provider_index, filename in enumerate(ASSET_NAMES.values()):
            write_atlas(self.assets_dir / filename, provider_index)

    def assert_frame_color(self, frame: Image.Image, expected: tuple[int, int, int, int]) -> None:
        actual = frame.getpixel((32, 32))
        self.assertLessEqual(max(abs(actual[channel] - expected[channel]) for channel in range(3)), 1)
        self.assertEqual(actual[3], expected[3])

    def test_provider_rows_frames_and_nondivisible_grid(self) -> None:
        for provider_index, provider in enumerate(ASSET_NAMES):
            with self.subTest(provider=provider):
                self.assertTrue(mascots.has_mascot(provider))
                for state, row in STATE_ROWS.items():
                    cycle = STATE_CYCLES[state]
                    for column in range(4):
                        # Sample inside each frame interval, away from boundaries.
                        elapsed = (column + 0.25) * cycle / 4
                        frame = mascots.mascot_frame(provider, state, elapsed)
                        self.assertIsNotNone(frame)
                        assert frame is not None
                        self.assertEqual(frame.mode, "RGBA")
                        self.assertEqual(frame.size, (64, 64))
                        with self.subTest(state=state, column=column):
                            self.assert_frame_color(
                                frame, cell_color(provider_index, row, column)
                            )

    def test_alpha_is_preserved_and_callers_receive_independent_frames(self) -> None:
        first = mascots.mascot_frame("codex", "idle", 0.0)
        self.assertIsNotNone(first)
        assert first is not None
        expected = cell_color(0, 0, 0)
        self.assert_frame_color(first, expected)

        first.putpixel((32, 32), (0, 0, 0, 0))
        second = mascots.mascot_frame("codex", "idle", 0.0)
        self.assertIsNotNone(second)
        assert second is not None
        self.assert_frame_color(second, expected)

    def assert_invalid_elapsed_uses_first_frame(self, elapsed: object) -> None:
        expected = mascots.mascot_frame("codex", "busy", 0.0)
        actual = mascots.mascot_frame("codex", "busy", elapsed)  # type: ignore[arg-type]
        self.assertIsNotNone(expected)
        self.assertIsNotNone(actual)
        assert expected is not None and actual is not None
        self.assertEqual(actual.tobytes(), expected.tobytes())

    def test_none_elapsed_uses_first_frame(self) -> None:
        self.assert_invalid_elapsed_uses_first_frame(None)

    def test_negative_elapsed_uses_first_frame(self) -> None:
        self.assert_invalid_elapsed_uses_first_frame(-1.0)

    def test_nan_elapsed_uses_first_frame(self) -> None:
        self.assert_invalid_elapsed_uses_first_frame(float("nan"))

    def test_positive_infinite_elapsed_uses_first_frame(self) -> None:
        self.assert_invalid_elapsed_uses_first_frame(float("inf"))

    def test_negative_infinite_elapsed_uses_first_frame(self) -> None:
        self.assert_invalid_elapsed_uses_first_frame(float("-inf"))

    def test_unparseable_elapsed_uses_first_frame(self) -> None:
        self.assert_invalid_elapsed_uses_first_frame("bad")

    def test_unknown_inputs_and_corrupt_file_fall_back(self) -> None:
        self.assertIsNone(mascots.mascot_frame("../../outside", "idle", 0.0))
        self.assertFalse(mascots.has_mascot("../../outside"))
        self.assertIsNone(mascots.mascot_frame("codex", "sleeping", 0.0))
        self.assertIsNone(mascots.mascot_frame("codex", "idle", 0.0, size=0))
        self.assertIsNone(mascots.mascot_frame("codex", "idle", 0.0, size=513))

        (self.assets_dir / ASSET_NAMES["codex"]).write_bytes(b"not a png")
        mascots._load_atlas.cache_clear()
        self.assertFalse(mascots.has_mascot("codex"))
        self.assertIsNone(mascots.mascot_frame("codex", "idle", 0.0))

    def test_oversized_atlas_is_rejected_and_cache_is_bounded(self) -> None:
        for provider in ASSET_NAMES:
            self.assertTrue(mascots.has_mascot(provider))
        self.assertLessEqual(mascots._load_atlas.cache_info().currsize, 3)

        oversized = self.assets_dir / ASSET_NAMES["codex"]
        write_atlas(
            oversized,
            provider_index=0,
            width=mascots._MAX_IMAGE_EDGE + 1,
            height=3,
        )
        mascots._load_atlas.cache_clear()
        self.assertFalse(mascots.has_mascot("codex"))
        self.assertIsNone(mascots.mascot_frame("codex", "idle", 0.0))


if __name__ == "__main__":
    unittest.main()
