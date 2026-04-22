"""
utils/progress.py
Live-progress helpers: Telegram message updater + download/upload progress callbacks.
Compatible with Pyrogram 2.x (tested on 2.3.x).
"""

import asyncio
import logging
import time
from typing import Callable

from pyrogram.types import Message

logger = logging.getLogger("progress")

# How often (seconds) to push an edit to Telegram (avoid flood limits)
UPDATE_INTERVAL = 3.0

# Progress bar width (characters)
BAR_WIDTH = 20


def _make_bar(percent: int) -> str:
    """Return a Unicode progress bar string for a given 0–100 value."""
    filled = round(BAR_WIDTH * percent / 100)
    bar = "█" * filled + "░" * (BAR_WIDTH - filled)
    return f"[{bar}] {percent}%"


class ProgressUpdater:
    """
    Throttled Telegram message editor.

    Usage:
        updater = ProgressUpdater(status_message, "🔄 Processing…")
        await updater.update(42)
        await updater.done("✅ Done!")
    """

    def __init__(self, message: Message, prefix: str = "🔄 Processing…") -> None:
        self._msg     = message
        self._prefix  = prefix
        self._last_ts = 0.0
        self._last_pct = None
        self._lock    = asyncio.Lock()

    async def update(self, percent: int, force: bool = False) -> None:
        """Edit the status message with the current progress bar."""
        now = time.monotonic()
        async with self._lock:
            if not force and (now - self._last_ts < UPDATE_INTERVAL):
                return
            if percent == self._last_pct and not force:
                return
            self._last_pct = percent
            self._last_ts  = now

        text = f"{self._prefix}\n\n{_make_bar(percent)}"
        try:
            await self._msg.edit(text)
        except Exception as exc:
            logger.debug("Progress edit skipped: %s", exc)

    async def done(self, text: str) -> None:
        """Replace the progress message with a final status string."""
        try:
            await self._msg.edit(text)
        except Exception as exc:
            logger.debug("Done-edit skipped: %s", exc)


# ── Download / Upload wrappers ────────────────────────────────────────────────

def make_download_callback(updater: "ProgressUpdater") -> Callable:
    """
    Return a Pyrogram-compatible download progress callback.
    Pyrogram 2.x calls it as: async callback(current, total)
    """
    updater._prefix = "📥 Downloading…"

    async def callback(current: int, total: int) -> None:
        if total:
            pct = min(100, round(current / total * 100))
            await updater.update(pct)

    return callback


def make_upload_callback(updater: "ProgressUpdater") -> Callable:
    """Return a Pyrogram-compatible upload progress callback."""
    updater._prefix = "📤 Uploading…"

    async def callback(current: int, total: int) -> None:
        if total:
            pct = min(100, round(current / total * 100))
            await updater.update(pct)

    return callback


def make_ffmpeg_callback(updater: "ProgressUpdater") -> Callable:
    """Return an async callable for use with ffmpeg.generate_sample()."""
    updater._prefix = "⚙️ Generating sample…"

    async def callback(percent: int) -> None:
        await updater.update(percent)

    return callback