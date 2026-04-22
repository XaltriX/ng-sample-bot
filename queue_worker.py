"""
queue_worker.py
Async job queue — processes one video per worker, supports N concurrent users.
Compatible with Pyrogram 2.x (tested on 2.3.x).
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pyrogram import Client
from pyrogram.types import Message

from utils.ffmpeg import generate_sample
from utils.progress import (
    ProgressUpdater,
    make_download_callback,
    make_ffmpeg_callback,
    make_upload_callback,
)

logger = logging.getLogger("queue_worker")

TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)


# ── Job dataclass ─────────────────────────────────────────────────────────────

@dataclass
class VideoJob:
    client: Client
    message: Message
    status_msg: Message
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


# ── Queue ──────────────────────────────────────────────────────────────────────

class VideoQueue:
    """
    Manages an asyncio.Queue of VideoJob items and a pool of worker coroutines.

    :param app:     Pyrogram Client (for sending files)
    :param workers: Number of concurrent processing workers
    """

    def __init__(self, app: Client, workers: int = 3) -> None:
        self._app     = app
        self._workers = workers
        self._queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list = []

    async def start(self) -> None:
        """Spawn worker tasks."""
        for i in range(self._workers):
            task = asyncio.create_task(self._worker(i), name=f"worker-{i}")
            self._tasks.append(task)
        logger.info("Started %d worker(s)", self._workers)

    async def stop(self) -> None:
        """Cancel all workers gracefully."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("All workers stopped")

    async def enqueue(
        self, client: Client, message: Message, status_msg: Message
    ) -> None:
        job = VideoJob(client=client, message=message, status_msg=status_msg)
        await self._queue.put(job)
        queue_depth = self._queue.qsize()
        logger.info("Job %s enqueued (queue depth: %d)", job.job_id, queue_depth)

        if queue_depth > 1:
            try:
                await status_msg.edit(
                    f"⏳ You are #{queue_depth} in the queue. Please wait…"
                )
            except Exception:
                pass

    # ── Internal worker ───────────────────────────────────────────────────────

    async def _worker(self, worker_id: int) -> None:
        logger.info("Worker %d ready", worker_id)
        while True:
            job: VideoJob = await self._queue.get()
            logger.info("Worker %d picked up job %s", worker_id, job.job_id)
            try:
                await self._process(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Job %s failed: %s", job.job_id, exc)
                try:
                    await job.status_msg.edit(
                        f"❌ Processing failed.\n\n`{exc}`"
                    )
                except Exception:
                    pass
            finally:
                self._queue.task_done()

    async def _process(self, job: VideoJob) -> None:
        """Full pipeline: download → generate sample → upload → cleanup."""
        jid = job.job_id
        updater = ProgressUpdater(job.status_msg, "📥 Downloading…")

        # ── Paths ────────────────────────────────────────────────────────────
        input_path  = TEMP_DIR / f"{jid}_input.mp4"
        output_path = TEMP_DIR / f"{jid}_sample.mp4"

        try:
            # ── 1. Download ──────────────────────────────────────────────────
            logger.info("[%s] Downloading…", jid)
            await updater.update(0, force=True)

            dl_cb = make_download_callback(updater)
            await job.client.download_media(
                job.message,
                file_name=str(input_path),
                progress=dl_cb,
            )
            await updater.update(100, force=True)
            logger.info("[%s] Download complete: %s", jid, input_path)

            # ── 2. Generate sample ───────────────────────────────────────────
            logger.info("[%s] Generating sample…", jid)
            ff_cb = make_ffmpeg_callback(updater)
            await generate_sample(input_path, output_path, progress_callback=ff_cb)
            logger.info("[%s] Sample ready: %s", jid, output_path)

            # ── 3. Upload ────────────────────────────────────────────────────
            logger.info("[%s] Uploading…", jid)
            up_cb = make_upload_callback(updater)
            await updater.update(0, force=True)

            await job.client.send_video(
                chat_id=job.message.chat.id,
                video=str(output_path),
                caption="✅ Here is your sample clip with watermark @linkz_wallah",
                reply_to_message_id=job.message.id,
                progress=up_cb,
            )

            await updater.done("✅ Done! Sample sent above.")
            logger.info("[%s] Upload complete", jid)

        finally:
            # ── 4. Cleanup ───────────────────────────────────────────────────
            for p in (input_path, output_path):
                try:
                    if p.exists():
                        p.unlink()
                        logger.debug("[%s] Deleted %s", jid, p)
                except OSError as exc:
                    logger.warning("[%s] Could not delete %s: %s", jid, p, exc)