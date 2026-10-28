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

                try:
                    # "all" mode → resolve channels LIVE at send time
                    if post.get("channels_mode") == "all":
                        channel_ids = [c["channel_id"] for c in await db.get_all_channels()]
                    else:
                        channel_ids = post.get("channels", [])

                    sent, failed = await helpers.send_post_to_channels(
                        app=app,
                        title=post["title"],
                        main_link=post["main_link"],
                        preview=post.get("preview"),
                        channel_ids=channel_ids,
                    )
                except Exception as e:
                    sent, failed, channel_ids = 0, [(0, str(e))], []

                await db.finish_post(post["_id"], "sent" if sent else "failed", sent, len(failed))
                print(f"[SCHEDULER] {post['title']}: sent={sent} failed={len(failed)}")

                if failed or not sent:
                    lines = "\n".join(f"• {cid} — {err[:80]}" for cid, err in failed) or "• koi channel nahi mila"
                    await _notify_admins(
                        app, f"⚠️ Scheduled **{post['title']}**: {sent} sent, {len(failed)} failed.\n{lines}"
                    )
        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}")
        await asyncio.sleep(20)
