"""
Telegram Sample Video Bot — bot.py
Accepts video files, generates watermarked samples, supports multiple users via async queue.
Compatible with Pyrogram 2.3.x / Python 3.12.
"""

import os
import asyncio
import logging
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("bot")

# ── Config ────────────────────────────────────────────────────────────────────
API_ID    = int(os.environ.get("API_ID",    "24955235"))
API_HASH  = os.environ.get("API_HASH",      "f317b3f7bbe390346d8b46868cff0de8")
BOT_TOKEN = os.environ.get("BOT_TOKEN",     "8746844152:AAF1cfkvhzWGdvU_t_2mCSMbrXzzab6lH3k")

TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

# ── Pyrogram client ───────────────────────────────────────────────────────────
app = Client(
    "sample_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# Imported here (after app) to avoid circular issues at module load
from queue_worker import VideoQueue  # noqa: E402

video_queue = None   # assigned once bot starts


# ── Handlers ──────────────────────────────────────────────────────────────────
@app.on_message(filters.command("start"))
async def cmd_start(client: Client, message: Message) -> None:
    logger.info("CMD /start  user=%s", message.from_user.id)
    await message.reply(
        "👋 **Sample Video Bot**\n\n"
        "Send me any video and I'll generate a watermarked sample clip:\n"
        "• ≤ 1 min  → 10 sec sample\n"
        "• ≤ 10 min → 20 sec sample\n"
        "• > 10 min → 30 sec sample\n\n"
        "Watermark `@linkz_wallah` is centred on the frame."
    )


@app.on_message(filters.command("help"))
async def cmd_help(client: Client, message: Message) -> None:
    logger.info("CMD /help  user=%s", message.from_user.id)
    await message.reply(
        "📖 **How to use:**\n"
        "1. Send a video file (as video or document)\n"
        "2. Wait while I process it\n"
        "3. Receive your sample!\n\n"
        "⚙️ FFmpeg ultrafast preset for speed."
    )


@app.on_message(filters.video | filters.document)
async def handle_video(client: Client, message: Message) -> None:
    logger.info(
        "Incoming media  user=%s  type=%s",
        message.from_user.id,
        "video" if message.video else "document",
    )

    # Gate documents on MIME type
    if message.document:
        mime = message.document.mime_type or ""
        if not mime.startswith("video/"):
            await message.reply("⚠️ Please send a valid video file.")
            return

    media = message.video or message.document
    file_size_mb = (media.file_size or 0) / (1024 * 1024)
    logger.info("File size: %.1f MB", file_size_mb)

    status_msg = await message.reply("⏳ Added to queue, please wait…")
    await video_queue.enqueue(client, message, status_msg)


# ── Entry point ───────────────────────────────────────────────────────────────
async def main() -> None:
    global video_queue

    async with app:
        video_queue = VideoQueue(app, workers=3)
        await video_queue.start()
        logger.info("Bot is running…")
        await asyncio.Future()   # block forever (cancelled on Ctrl+C)


if __name__ == "__main__":
    app.run(main())