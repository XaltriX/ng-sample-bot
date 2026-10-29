"""
Background async worker — checks pending posts every 20 seconds.
"""
import asyncio

from pyrogram import Client

import database as db
import helpers
from config import ADMIN_IDS


async def _notify_admins(app: Client, text: str):
    for aid in ADMIN_IDS:
        try:
            await app.send_message(aid, text)
        except Exception:
            pass


async def scheduler_loop(app: Client):
    print("[SCHEDULER] Started.")
    while True:
        try:
            while True:
                # Atomic claim → even with 2 instances a post is sent only once.
                post = await db.claim_due_post()
                if not post:
                    break

                title = post.get("title") or "scheduled post"
                try:
                    # "all" mode → resolve channels LIVE at send time
                    if post.get("channels_mode") == "all":
                        channel_ids = [c["channel_id"] for c in await db.get_all_channels()]
                    else:
                        channel_ids = post.get("channels", [])

                    title, sent, failed = await helpers.publish(
                        app=app,
                        main_link=post["main_link"],
                        preview=post.get("preview"),
                        duration=post.get("duration"),
                        size_bytes=post.get("size_bytes"),
                        channel_ids=channel_ids,
                    )
                except Exception as e:
                    sent, failed = 0, [(0, str(e))]

                await db.finish_post(post["_id"], "sent" if sent else "failed", sent, len(failed), title if sent else None)
                print(f"[SCHEDULER] {title}: sent={sent} failed={len(failed)}")

                if failed or not sent:
                    lines = "\n".join(f"• {cid} — {err[:80]}" for cid, err in failed) or "• no channels found"
                    await _notify_admins(
                        app, f"⚠️ Scheduled **{title}**: {sent} sent, {len(failed)} failed.\n{lines}"
                    )
        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}")
        await asyncio.sleep(20)
