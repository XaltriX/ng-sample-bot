"""
Sample pipeline: download full video → cut watermarked clip (ffmpeg) →
upload to the private DB channel via the file-store bot → save in Mongo.
"""
import asyncio
import html
import json
import logging
import math
import secrets
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pyrogram import enums

import database as db
from config import DB_CHANNEL_ID, SAMPLE_WORKERS, WATERMARK_TEXT

log = logging.getLogger("sampler")

TEMP = Path("temp")
TEMP.mkdir(exist_ok=True)

_queue: asyncio.Queue | None = None
_tasks: list[asyncio.Task] = []
_fs = None  # file-store client (uploads the sample)


# ── ffmpeg helpers ────────────────────────────────────────────────────────────

async def probe(path: Path) -> tuple[float, int, int]:
    """Returns (duration_seconds, width, height)."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_entries", "format=duration:stream=codec_type,width,height",
        str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {err.decode()[:300]}")
    data = json.loads(out)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("No video stream found in this file.")
    return float(data["format"]["duration"]), int(video["width"]), int(video["height"])


def sample_length(total: float) -> int:
    if total <= 60:
        return 10
    if total <= 600:
        return 20
    return 30


def start_time(total: float, length: int) -> float:
    """Sample from the middle of the video."""
    return max(0.0, min(total / 2 - length / 2, total - length))


def make_watermark(text: str, video_w: int, out_path: Path) -> None:
    """Semi-transparent box + white text, sized relative to the video width."""
    size = max(16, int(video_w * 0.045))
    font = None
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",):
        try:
            font = ImageFont.truetype(p, size)
            break
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default(size=size)  # scalable default (Pillow ≥ 10.1)

    bbox = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font)
    pad = size // 2
    w, h = bbox[2] - bbox[0] + pad * 2, bbox[3] - bbox[1] + pad * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 120))
    ImageDraw.Draw(img).text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=(255, 255, 255, 230))
    img.save(out_path, "PNG")


async def make_sample(src: Path, dst: Path, progress=None) -> tuple[int, int, int, float]:
    """Cut + watermark. Returns (sample_seconds, width, height, full_duration)."""
    total, w, h = await probe(src)
    length = sample_length(total)
    length = min(length, max(1, int(total)))
    ss = start_time(total, length)
    log.info("duration=%.1fs → sample %ds from %.1fs", total, length, ss)

    wm = dst.with_name(dst.stem + "_wm.png")
    await asyncio.to_thread(make_watermark, WATERMARK_TEXT, w, wm)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(ss), "-i", str(src), "-i", str(wm), "-t", str(length),
        # even dimensions (libx264 needs them) + centred watermark
        "-filter_complex",
        "[0:v]scale=trunc(iw/2)*2:trunc(ih/2)*2[b];[b][1:v]overlay=(W-w)/2:(H-h)/2[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(dst),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    async def read_progress():
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            txt = line.decode(errors="ignore").strip()
            if txt.startswith("out_time_us=") and progress:
                try:
                    pct = min(100, math.floor(int(txt.split("=")[1]) / (length * 1_000_000) * 100))
                    await progress(max(0, pct))
                except ValueError:
                    pass

    try:
        _, err, _ = await asyncio.gather(read_progress(), proc.stderr.read(), proc.wait())
    finally:
        wm.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode()[-300:]}")
    return length, int(w // 2 * 2), int(h // 2 * 2), total


# ── Progress bar in the status message ────────────────────────────────────────

class Progress:
    def __init__(self, msg):
        self.msg, self.last = msg, 0.0

    async def show(self, label: str, pct: int, force: bool = False):
        now = time.monotonic()
        if not force and now - self.last < 3:
            return
        self.last = now
        pct = max(0, min(100, pct))
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        try:
            await self.msg.edit(f"{label}\n`{bar}` {pct}%")
        except Exception:
            pass

    def cb(self, label: str):
        async def _cb(cur: int, total: int):
            await self.show(label, int(cur * 100 / total) if total else 0)
        return _cb


# ── Queue / workers ───────────────────────────────────────────────────────────

async def start(fs_client, workers: int = SAMPLE_WORKERS):
    global _queue, _fs
    _fs, _queue = fs_client, asyncio.Queue()
    for i in range(workers):
        _tasks.append(asyncio.create_task(_worker(i)))
    log.info("Sample workers: %d", workers)


async def enqueue(client, message, status):
    await _queue.put((client, message, status))
    n = _queue.qsize()
    if n > 1:
        try:
            await status.edit(f"⏳ You are #{n} in the queue. Please wait…")
        except Exception:
            pass


async def _worker(i: int):
    while True:
        client, message, status = await _queue.get()
        try:
            await _process(client, message, status)
        except Exception as e:
            log.exception("sample job failed")
            try:
                await status.edit(f"❌ Sample failed.\n\n`{str(e)[:300]}`")
            except Exception:
                pass
        finally:
            _queue.task_done()


async def _process(client, message, status):
    key = secrets.token_hex(6)  # short id used in the t.me deep link
    src, dst = TEMP / f"{key}_in", TEMP / f"{key}_sample.mp4"
    prog = Progress(status)
    try:
        await prog.show("📥 Downloading…", 0, force=True)
        await client.download_media(message, file_name=str(src), progress=prog.cb("📥 Downloading…"))
        size_bytes = src.stat().st_size  # exact size of the full video

        await prog.show("🎞 Creating sample…", 0, force=True)
        length, w, h, total = await make_sample(src, dst, lambda p: prog.show("🎞 Creating sample…", p))

        await prog.show("📤 Uploading…", 0, force=True)
        sent = await _fs.send_video(
            chat_id=DB_CHANNEL_ID,
            video=str(dst),
            caption=f"🎬 <b>Sample Preview</b>\n\n<blockquote>{html.escape(WATERMARK_TEXT)}</blockquote>",
            parse_mode=enums.ParseMode.HTML,
            duration=length, width=w, height=h, supports_streaming=True,
            progress=prog.cb("📤 Uploading…"),
        )

        # status message becomes the "reply to me with the link" prompt
        await db.create_sample(key, message.from_user.id, sent.id, status.id, total, size_bytes)
        await status.edit(
            f"✅ **Sample ready** (`{key}`)\n\n"
            "📥 Now send the **download link**.\n"
            "__(If you sent several videos at once, reply to this message.)__"
        )
    finally:
        src.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)
