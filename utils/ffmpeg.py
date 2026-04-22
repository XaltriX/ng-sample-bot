"""
utils/ffmpeg.py
All FFmpeg / ffprobe helpers for the sample-video bot.
"""

import asyncio
import json
import logging
import math
from pathlib import Path

logger = logging.getLogger("ffmpeg")

WATERMARK_TEXT = "@linkz_wallah"

# ── Duration ──────────────────────────────────────────────────────────────────

async def get_duration(video_path: str | Path) -> float:
    """Return video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_entries", "format=duration",
        str(video_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode()}")

    data = json.loads(stdout)
    try:
        return float(data["format"]["duration"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"Could not parse duration from ffprobe output: {data}") from exc


# ── Sample length logic ───────────────────────────────────────────────────────

def get_sample_duration(total_seconds: float) -> int:
    """
    Return the sample clip length (seconds) based on total video duration.

    ≤  1 min  → 10 sec
    ≤ 10 min  → 20 sec
    > 10 min  → 30 sec
    """
    if total_seconds <= 60:
        return 10
    if total_seconds <= 600:
        return 20
    return 30


def get_start_time(total_seconds: float, sample_seconds: int) -> float:
    """Return the start time so the sample is taken from the middle of the video."""
    mid = total_seconds / 2
    start = mid - sample_seconds / 2
    # Clamp so we never go negative or past the end
    start = max(0.0, min(start, total_seconds - sample_seconds))
    return start


# ── FFmpeg command builder ─────────────────────────────────────────────────────

def _build_ffmpeg_cmd(
    input_path: str | Path,
    output_path: str | Path,
    start: float,
    duration: int,
) -> list[str]:
    """
    Build an FFmpeg command that:
    • Uses fast seek (-ss before -i)
    • Cuts the requested segment
    • Burns in a centred, semi-transparent watermark with a background box
    • Encodes with ultrafast preset for speed
    """
    # Escape special FFmpeg drawtext characters
    safe_text = WATERMARK_TEXT.replace("@", r"\@").replace(":", r"\:")

    # drawtext filter — centred, white text, semi-transparent black box
    drawtext = (
        f"drawtext="
        f"text='{safe_text}':"
        f"fontsize=36:"
        f"fontcolor=white@0.9:"
        f"x=(w-text_w)/2:"
        f"y=(h-text_h)/2:"
        f"box=1:"
        f"boxcolor=black@0.45:"
        f"boxborderw=10:"
        f"font=Sans"
    )

    cmd = [
        "ffmpeg",
        "-y",                        # overwrite output
        "-ss", str(start),           # fast seek (before -i)
        "-i", str(input_path),
        "-t", str(duration),         # clip duration
        "-vf", drawtext,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",                # acceptable quality at ultrafast
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",   # web-friendly
        "-progress", "pipe:1",       # progress output to stdout
        "-nostats",
        str(output_path),
    ]
    return cmd


# ── Main processing function ──────────────────────────────────────────────────

async def generate_sample(
    input_path: str | Path,
    output_path: str | Path,
    progress_callback=None,
) -> None:
    """
    Generate a watermarked sample clip.

    progress_callback(percent: int) is called periodically with 0–100.
    """
    input_path  = Path(input_path)
    output_path = Path(output_path)

    # 1. Get duration
    total = await get_duration(input_path)
    logger.info("Video duration: %.2f s", total)

    # 2. Decide clip parameters
    sample_dur = get_sample_duration(total)
    start_time = get_start_time(total, sample_dur)
    logger.info("Sample: start=%.2f s, duration=%d s", start_time, sample_dur)

    # 3. Build command
    cmd = _build_ffmpeg_cmd(input_path, output_path, start_time, sample_dur)
    logger.debug("FFmpeg cmd: %s", " ".join(cmd))

    # 4. Run FFmpeg with progress tracking
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    out_time_us = 0
    total_us = sample_dur * 1_000_000  # microseconds

    async def read_progress() -> None:
        nonlocal out_time_us
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="ignore").strip()
            if text.startswith("out_time_us="):
                try:
                    out_time_us = int(text.split("=")[1])
                except ValueError:
                    pass
                if progress_callback and total_us > 0:
                    pct = min(100, math.floor(out_time_us / total_us * 100))
                    await progress_callback(pct)

    await asyncio.gather(read_progress(), proc.wait())

    if proc.returncode != 0:
        stderr_data = await proc.stderr.read()
        raise RuntimeError(f"FFmpeg failed (code {proc.returncode}): {stderr_data.decode()}")

    if progress_callback:
        await progress_callback(100)

    logger.info("Sample written to %s", output_path)