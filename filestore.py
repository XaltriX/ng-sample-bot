"""
File-store bot: user opens the preview deep link → gets the sample (with a
green "Full Video" button) → the sample auto-deletes after SAMPLE_TTL_SECONDS;
the note stays and turns into an "expired" message with the Full Video button.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from pyrogram import Client, filters
from pyrogram.types import Message

import botapi
import database as db
from config import DB_CHANNEL_ID, FILESTORE_BOT_TOKEN, SAMPLE_TTL_SECONDS


def full_markup(link: str) -> dict:
    return botapi.markup([botapi.url_btn("🎬 Full Video", link, "success")])


def register(fs: Client):

    @fs.on_message(filters.command("start") & filters.private)
    async def start(client: Client, msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) < 2:
            return await msg.reply("👋 Use the **Preview** link from a channel post to watch a sample.")

        sample = await db.get_sample(parts[1].strip())
        if not sample or not sample.get("main_link"):
            return await msg.reply("❌ This link is invalid or not ready yet.")

        # Bot API copyMessage → keeps the caption and lets us add a colored button
        copied = await botapi.call_styled(FILESTORE_BOT_TOKEN, "copyMessage", {
            "chat_id": msg.chat.id,
            "from_chat_id": DB_CHANNEL_ID,
            "message_id": sample["db_msg_id"],
            "reply_markup": full_markup(sample["main_link"]),
        })
        mins = max(1, SAMPLE_TTL_SECONDS // 60)
        note = await msg.reply(f"⚠️ This sample will be deleted in **{mins} min**. Watch or save it now.")
        await db.add_deletion(
            msg.chat.id, copied["message_id"], note.id, sample["main_link"],
            datetime.now(timezone.utc) + timedelta(seconds=SAMPLE_TTL_SECONDS),
        )


async def cleanup_loop(fs: Client):
    """Deletes expired samples. Timers live in Mongo → survive restarts."""
    print("[FILESTORE] Cleanup loop started.")
    while True:
        try:
            for d in await db.get_due_deletions():
                try:
                    await fs.delete_messages(d["chat_id"], d["video_id"])
                except Exception as e:
                    print(f"[FILESTORE] delete failed: {e}")
                try:
                    await botapi.call_styled(FILESTORE_BOT_TOKEN, "editMessageText", {
                        "chat_id": d["chat_id"],
                        "message_id": d["note_id"],
                        "text": "⏰ Sample expired.\n\nLiked it? Watch the full video here 👇",
                        "reply_markup": full_markup(d["main_link"]),
                    })
                except Exception as e:
                    print(f"[FILESTORE] edit failed: {e}")
                await db.mark_deletion_done(d["_id"])
        except Exception as e:
            print(f"[FILESTORE] cleanup error: {e}")
        await asyncio.sleep(10)
