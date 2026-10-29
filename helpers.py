"""
Helpers: build caption, buttons, and send post to channels.
"""
import asyncio
import html

from pyrogram import Client

import botapi
import database as db
from config import BOT_TOKEN

# Set by bot.py after the file-store bot starts (its @username)
FS_USERNAME: str | None = None


def preview_link(key: str) -> str:
    """Deep link → file-store bot delivers the sample for this key."""
    return f"https://t.me/{FS_USERNAME}?start={key}"


def fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def fmt_size(size_bytes: int) -> str:
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / 1024 ** 3:.2f} GB"
    return f"{size_bytes / 1024 ** 2:.2f} MB"


def _a(label: str, url: str) -> str:
    return f'<i><a href="{html.escape(url, quote=True)}">{label}</a></i>'


def build_caption(
    title: str,
    preview: str | None,
    main_link: str,
    duration: float | None = None,
    size_bytes: int | None = None,
) -> str:
    """Final post caption (HTML) with quote boxes. Each link is shown twice."""
    lines = [
        "┏━━━━━ 𝗡𝗘𝗪 𝗣𝗢𝗦𝗧 ━━━━━┓",
        "",
        f"🎬 <b>{html.escape(title)}</b>",
        "",
    ]

    info = []
    if duration:
        info.append(f"⏱ <b>Duration:</b> {fmt_duration(duration)}")
    if size_bytes:
        info.append(f"💾 <b>Size:</b> {fmt_size(size_bytes)}")
    if info:
        lines += ["   ".join(info), ""]

    if preview:
        link = _a("Preview Link", preview)
        lines += [f"<blockquote>👀 <b>PREVIEW</b>\n➥ {link}\n➥ {link}</blockquote>", ""]

    link = _a("Full Video", main_link)
    lines += [
        f"<blockquote>📥 <b>DOWNLOAD &amp; WATCH</b>\n➥ {link}\n➥ {link}</blockquote>",
        "",
        "┗━━━━ <b>@Linkz_wallah</b> ━━━━┛",
    ]
    return "\n".join(lines)


def build_markup() -> dict:
    return botapi.markup([
        botapi.url_btn("💎 Buy Premium / Remove Ads", "https://t.me/ForeignXHubBot?start=premium", "primary"),
    ])


async def send_post_to_channels(
    app: Client,  # kept for compatibility with callers; sending goes through the Bot API
    title: str,
    main_link: str,
    preview: str | None,
    channel_ids: list[int],
    duration: float | None = None,
    size_bytes: int | None = None,
) -> tuple[int, list[tuple[int, str]]]:
    """
    Sends a text-only post to the given channels.
    Returns (success_count, [(channel_id, error), ...]).
    """
    caption = build_caption(title, preview, main_link, duration, size_bytes)
    reply_markup = build_markup()
    ok: list[int] = []
    failed: list[tuple[int, str]] = []

    for cid in channel_ids:
        try:
            await botapi.call_styled(BOT_TOKEN, "sendMessage", {
                "chat_id": cid,
                "text": caption,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
                "reply_markup": reply_markup,
            })
            ok.append(cid)
        except Exception as e:
            print(f"[ERROR] channel {cid}: {e}")
            failed.append((cid, str(e)))

    if ok:
        await db.save_log(title, ok)
    return len(ok), failed


_publish_lock = asyncio.Lock()


async def publish(
    app: Client,
    main_link: str,
    preview: str | None,
    channel_ids: list[int],
    duration: float | None = None,
    size_bytes: int | None = None,
) -> tuple[str, int, list[tuple[int, str]]]:
    """
    Assigns the next POST NO and sends the post, under one lock.
    Numbering therefore always follows the real publish order in the channel
    (a post scheduled for tomorrow gets its number tomorrow, not today).
    If nothing could be posted, the number is given back.
    Returns (title, success_count, failed).
    """
    async with _publish_lock:
        title = await db.next_post_title()
        try:
            sent, failed = await send_post_to_channels(
                app, title, main_link, preview, channel_ids, duration, size_bytes
            )
        except Exception:
            await db.rollback_post_no()
            raise
        if not sent:
            await db.rollback_post_no()
        return title, sent, failed
