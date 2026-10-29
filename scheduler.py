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
                    if post.get("target"):
                        results = await helpers.publish_targets(
                            app, post["target"], post["main_link"], post.get("preview"),
                            post.get("duration"), post.get("size_bytes"),
                        )
                    else:
                        # posts scheduled by an older version (plain channel list / "all" mode)
                        if post.get("channels_mode") == "all":
                            ids = [c["channel_id"] for c in await db.get_all_channels()]
                        else:
                            ids = post.get("channels", [])
                        title, sent, failed = await helpers.publish(
                            app, post["main_link"], post.get("preview"), ids,
                            post.get("duration"), post.get("size_bytes"),
                        )
                        results = [{"label": "Channels", "title": title, "sent": sent,
                                    "failed": failed, "total": len(ids)}]
                except Exception as e:
                    results = [{"label": "Error", "title": "-", "sent": 0,
                                "failed": [("-", str(e))], "total": 0}]

                sent = sum(r["sent"] for r in results)
                nfail = sum(len(r["failed"]) for r in results)
                titles = ", ".join(r["title"] for r in results if r["sent"])
                await db.finish_post(post["_id"], "sent" if sent else "failed", sent, nfail, titles or None)
                print(f"[SCHEDULER] {titles or 'nothing posted'}: sent={sent} failed={nfail}")

                if nfail or not sent:
                    await _notify_admins(app, "⚠️ Scheduled post had problems:\n" + helpers.format_results(results))
        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}")
        await asyncio.sleep(20)
