"""
File-store bot: user opens the preview deep link → gets the sample (with a
"Full Video" button) → sample auto-deletes after SAMPLE_TTL_SECONDS; the note
stays and turns into an "expired" message with the Full Video button.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import database as db
from config import DB_CHANNEL_ID, SAMPLE_TTL_SECONDS


def full_btn(link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎬 Full Video", url=link)]])


def register(fs: Client):

    @fs.on_message(filters.command("start") & filters.private)
    async def start(client: Client, msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) < 2:
            return await msg.reply("👋 Sample dekhne ke liye channel post ka **Preview** link use karo.")

        sample = await db.get_sample(parts[1].strip())
        if not sample or not sample.get("main_link"):
            return await msg.reply("❌ Ye link invalid hai ya ready nahi hai.")

        video = await client.copy_message(
            chat_id=msg.chat.id,
            from_chat_id=DB_CHANNEL_ID,
            message_id=sample["db_msg_id"],
            reply_markup=full_btn(sample["main_link"]),
        )
        mins = max(1, SAMPLE_TTL_SECONDS // 60)
        note = await msg.reply(f"⚠️ Ye sample **{mins} min** me delete ho jayega. Abhi dekh lo / save kar lo.")
        await db.add_deletion(
            msg.chat.id, video.id, note.id, sample["main_link"],
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
                    await fs.edit_message_text(
                        d["chat_id"], d["note_id"],
                        "⏰ Sample expire ho gaya.\n\nPasand aaya? Full video yahan se dekho 👇",
                        reply_markup=full_btn(d["main_link"]),
                    )
                except Exception as e:
                    print(f"[FILESTORE] edit failed: {e}")
                await db.mark_deletion_done(d["_id"])
        except Exception as e:
            print(f"[FILESTORE] cleanup error: {e}")
        await asyncio.sleep(10)
