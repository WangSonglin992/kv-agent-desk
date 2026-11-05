"""Load local mascot frames from the bundled 4-by-3 sprite sheets."""

from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path
from typing import Final

from PIL import Image


_ASSET_DIR = Path(__file__).with_name("assets")
_PROVIDER_ASSETS: Final[dict[str, str]] = {
    "codex": "mascot_chatgpt.png",
    "zcode": "mascot_glm.png",
    "antigravity": "mascot_gemini.png",
}
_STATES: Final[dict[str, tuple[int, float]]] = {
    "idle": (0, 4.0),
    "working": (1, 2.0),
    "busy": (2, 0.65),
}
_COLUMNS: Final = 4
_ROWS: Final = 3
_MAX_FILE_BYTES: Final = 8 * 1024 * 1024
_MAX_IMAGE_EDGE: Final = 2048
_MAX_IMAGE_PIXELS: Final = 4_194_304
_MAX_OUTPUT_SIZE: Final = 512


@lru_cache(maxsize=3)
def _load_atlas(provider: str) -> tuple[Image.Image, ...] | None:
    """Decode and cache one allowlisted atlas, or return ``None`` if invalid."""
    filename = _PROVIDER_ASSETS.get(provider)
    if filename is None:
        return None

    path = _ASSET_DIR / filename
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        with Image.open(path) as source:
            if source.format != "PNG":
                return None
            width, height = source.size
            if (
                width < _COLUMNS
                or height < _ROWS
                or width > _MAX_IMAGE_EDGE
                or height > _MAX_IMAGE_EDGE
                or width * height > _MAX_IMAGE_PIXELS
            ):
                return None

            source.load()
            rgba = source.convert("RGBA")

        frames: list[Image.Image] = []
        for row in range(_ROWS):
            top = row * height // _ROWS
            bottom = (row + 1) * height // _ROWS
            for column in range(_COLUMNS):
                left = column * width // _COLUMNS
                right = (column + 1) * width // _COLUMNS
                if right <= left or bottom <= top:
                    return None
                # Keep each entire cell; never trim transparent margins, which
                # would make the mascot jump as its animation frame changes.
                frames.append(rgba.crop((left, top, right, bottom)))
        rgba.close()
        return tuple(frames)
    except Exception:
        # Missing, unreadable, truncated, or otherwise invalid bundled images
        # should not prevent the dashboard from rendering its regular UI.
        return None


def has_mascot(provider: str) -> bool:
    """Return whether ``provider`` has a readable, valid bundled sprite sheet."""
    if not isinstance(provider, str):
        return False
    return _load_atlas(provider) is not None


def mascot_frame(
    provider: str,
    state: str,
    elapsed: float | None,
    size: int = 64,
) -> Image.Image | None:
    """Return a copied RGBA animation frame, normalized to a square viewport.

    ``elapsed`` is an animation clock value in seconds; a global monotonic
    clock works because each state's animation loops by its own duration.
    Invalid, negative, or non-finite elapsed values select the stable first frame.
    Animation loops every 4 seconds while idle, 2 seconds while working, and
    0.65 seconds while busy. The atlas rows are idle, working, then busy.
    """
    if not isinstance(provider, str) or not isinstance(state, str):
        return None
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= _MAX_OUTPUT_SIZE:
        return None

    state_info = _STATES.get(state)
    if state_info is None:
        return None
    atlas = _load_atlas(provider)
    if atlas is None:
        return None

    row, cycle_seconds = state_info
    try:
        seconds = float(elapsed) if elapsed is not None else 0.0
    except (TypeError, ValueError, OverflowError):
        seconds = 0.0
    if not math.isfinite(seconds) or seconds < 0.0:
        seconds = 0.0

    frame_index = min(int((seconds % cycle_seconds) * _COLUMNS / cycle_seconds), _COLUMNS - 1)
    frame = atlas[row * _COLUMNS + frame_index]
    # The square output box remains identical across frames and states. Resizing
    # whole cells (including alpha padding) avoids frame-dependent trimming.
    return frame.resize((size, size), Image.Resampling.LANCZOS).copy()
