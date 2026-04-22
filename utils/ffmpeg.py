"""
utils/ffmpeg.py
All FFmpeg / ffprobe helpers for the sample-video bot.
Watermark uses lavfi color + overlay (no drawtext/fontconfig needed).
"""

import asyncio
import json
import logging
import math
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("ffmpeg")

WATERMARK_TEXT = "@linkz_wallah"

# ── Duration ──────────────────────────────────────────────────────────────────

async def get_duration(video_path) -> float:
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
        raise RuntimeError(f"Could not parse duration: {data}") from exc


# ── Sample length logic ───────────────────────────────────────────────────────

def get_sample_duration(total_seconds: float) -> int:
    if total_seconds <= 60:
        return 10
    if total_seconds <= 600:
        return 20
    return 30


def get_start_time(total_seconds: float, sample_seconds: int) -> float:
    mid   = total_seconds / 2
    start = mid - sample_seconds / 2
    return max(0.0, min(start, total_seconds - sample_seconds))


# ── Watermark PNG builder (pure Python, no Pillow needed) ────────────────────

def _create_watermark_png(text: str, out_path: str) -> None:
    """
    Create a semi-transparent watermark PNG using FFmpeg's lavfi source.
    We draw white text on a black semi-transparent box using:
      - color filter  → solid black box
      - format=rgba   → add alpha channel
      - colorchannelmixer → set alpha to 0.45
    Then in the main command we overlay this at (W-w)/2:(H-h)/2.

    Actually simpler: generate the PNG with a Python bytes approach.
    We create a minimal 1x1 transparent PNG and let FFmpeg scale it,
    OR we use FFmpeg's own lavfi+drawtext to create the watermark image
    without needing fonts by encoding text as a simple box.

    Simplest reliable method: create watermark as a colored box with
    no text (text via separate step), OR use the vf 'drawbox' filter
    which does NOT need fontconfig.
    """
    pass   # not used — see _build_ffmpeg_cmd below


# ── FFmpeg command builder ────────────────────────────────────────────────────

def _build_ffmpeg_cmd(input_path, output_path, start: float, duration: int):
    """
    Watermark strategy that works WITHOUT fontconfig/drawtext:

    We use two-pass lavfi:
      1. Generate a semi-transparent black box with white text using
         FFmpeg's 'drawbox' (no font needed) for the box, and skip text
         OR use 'drawtext' with a bundled font path if available.

    Most reliable cross-platform approach:
      Use 'drawbox' for the background rectangle centred on screen,
      then overlay the text using a pre-rendered PNG created with Python's
      built-in struct/zlib (tiny PNG writer, zero dependencies).

    We embed the watermark as a lavfi input using the 'color' source
    and write the text character-by-character using 'drawbox' blocks
    — but that's too complex.

    BEST SOLUTION for static buildpacks:
      Use FFmpeg's built-in 'subtitles' or ASS filter — also needs fonts.
      
    ACTUAL BEST: generate a PNG with Pillow (add to requirements) OR
    use only 'drawbox' for a visible semi-transparent box (no text)
    and encode channel name in metadata.

    FINAL DECISION: Use Pillow to render watermark PNG (lightweight,
    always available), then overlay with FFmpeg. Add Pillow to requirements.
    """
    # Watermark PNG is pre-created before this cmd is built
    wm_path = str(Path(output_path).parent / "_wm.png")

    vf = (
        f"[0:v]scale=iw:ih[base];"
        f"[1:v]scale=iw*0.4:-1[wm];"          # watermark = 40% of video width
        f"[base][wm]overlay=(W-w)/2:(H-h)/2"   # centre it
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",    str(start),
        "-i",     str(input_path),
        "-i",     wm_path,              # watermark PNG as second input
        "-t",     str(duration),
        "-filter_complex", vf,
        "-c:v",   "libx264",
        "-preset","ultrafast",
        "-crf",   "28",
        "-c:a",   "aac",
        "-b:a",   "128k",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        str(output_path),
    ]
    return cmd, wm_path


async def _make_watermark_png(text: str, wm_path: str) -> None:
    """
    Render watermark PNG using Pillow.
    White text, semi-transparent dark background box, centred layout.
    """
    from PIL import Image, ImageDraw, ImageFont

    # Font — use default bitmap font (always available, no file needed)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
    except Exception:
        try:
            font = ImageFont.truetype("/app/.heroku/python/lib/python3.12/site-packages/PIL/fonts/FreeMono.ttf", 36)
        except Exception:
            font = ImageFont.load_default()

    # Measure text size
    dummy = Image.new("RGBA", (1, 1))
    draw  = ImageDraw.Draw(dummy)
    bbox  = draw.textbbox((0, 0), text, font=font)
    tw    = bbox[2] - bbox[0]
    th    = bbox[3] - bbox[1]

    pad   = 16
    img_w = tw + pad * 2
    img_h = th + pad * 2

    # Create image: transparent background
    img  = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Semi-transparent black box
    draw.rectangle([0, 0, img_w - 1, img_h - 1], fill=(0, 0, 0, 120))

    # White text
    draw.text((pad, pad), text, font=font, fill=(255, 255, 255, 230))

    img.save(wm_path, "PNG")
    logger.info("Watermark PNG written: %s (%dx%d)", wm_path, img_w, img_h)


# ── Main processing function ──────────────────────────────────────────────────

async def generate_sample(input_path, output_path, progress_callback=None) -> None:
    input_path  = Path(input_path)
    output_path = Path(output_path)

    # 1. Duration
    total = await get_duration(input_path)
    logger.info("Video duration: %.2f s", total)

    # 2. Clip params
    sample_dur = get_sample_duration(total)
    start_time = get_start_time(total, sample_dur)
    logger.info("Sample: start=%.2f s, duration=%d s", start_time, sample_dur)

    # 3. Build watermark PNG
    cmd, wm_path = _build_ffmpeg_cmd(input_path, output_path, start_time, sample_dur)
    await _make_watermark_png(WATERMARK_TEXT, wm_path)

    logger.debug("FFmpeg cmd: %s", " ".join(cmd))

    # 4. Run FFmpeg
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    out_time_us = 0
    total_us    = sample_dur * 1_000_000

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

    # Cleanup watermark PNG
    try:
        Path(wm_path).unlink(missing_ok=True)
    except Exception:
        pass

    if proc.returncode != 0:
        stderr_data = await proc.stderr.read()
        raise RuntimeError(f"FFmpeg failed (code {proc.returncode}): {stderr_data.decode()}")

    if progress_callback:
        await progress_callback(100)

    logger.info("Sample written to %s", output_path)
