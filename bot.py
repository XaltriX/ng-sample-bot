"""
Main entry point — runs the main bot, the file-store bot, the sample workers
and the post scheduler in ONE process.
"""
import asyncio
import logging

from pyrogram import Client

import filestore
import handlers
import helpers
import sampler
import scheduler
from config import API_ID, API_HASH, BOT_TOKEN, FILESTORE_BOT_TOKEN, DB_CHANNEL_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")


async def main():
    if "PUT_" in FILESTORE_BOT_TOKEN or not DB_CHANNEL_ID:
        raise SystemExit("config.py me FILESTORE_BOT_TOKEN aur DB_CHANNEL_ID bharo.")

    # in_memory → no .session files to leak / lose on Heroku restarts
    app = Client("main_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
    fs = Client("filestore_bot", api_id=API_ID, api_hash=API_HASH, bot_token=FILESTORE_BOT_TOKEN, in_memory=True)

    handlers.register_handlers(app)
    filestore.register(fs)

    async with app, fs:
        helpers.FS_USERNAME = (await fs.get_me()).username
        print(f"[BOT] Running. File-store bot: @{helpers.FS_USERNAME}")

        # sanity check: file-store bot must be admin in the DB channel
        try:
            await fs.get_chat(DB_CHANNEL_ID)
        except Exception as e:
            print(f"[WARN] DB channel not reachable by file-store bot: {e}")

        await sampler.start(fs)
        tasks = [  # keep references so they are never garbage-collected
            asyncio.create_task(scheduler.scheduler_loop(app)),
            asyncio.create_task(filestore.cleanup_loop(fs)),
        ]
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
