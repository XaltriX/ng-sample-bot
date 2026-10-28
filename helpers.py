"""
Helpers: build caption, buttons, and send post to channels.
"""
import asyncio
import html

from pyrogram import Client, enums
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import database as db

# Set by bot.py after the file-store bot starts (its @username)
FS_USERNAME: str | None = None


def preview_link(key: str) -> str:
    """Deep link → file-store bot delivers the sample for this key."""
    return f"https://t.me/{FS_USERNAME}?start={key}"


def _a(url: str) -> str:
    return f'<i><a href="{html.escape(url, quote=True)}">Click Here</a></i>'


def build_caption(title: str, preview: str | None, main_link: str) -> str:
    """Final post caption (HTML). Preview / Download links shown twice for emphasis."""
    lines = [
        "┏━━━━━ 𝗡𝗘𝗪 𝗣𝗢𝗦𝗧 ━━━━━┓",
        "",
        f"🎬 <b>{html.escape(title)}</b>",
        "",
    ]
    if preview:
        lines += ["👀 <b>PREVIEW</b>", f"➥ {_a(preview)}", f"➥ {_a(preview)}", ""]
    lines += [
        "📥 <b>DOWNLOAD &amp; WATCH</b>",
        f"➥ {_a(main_link)}",
        f"➥ {_a(main_link)}",
        "",
        "┗━━━━ <b>@Linkz_wallah</b> ━━━━┛",
    ]
    return "\n".join(lines)


def build_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Buy Premium / Remove Ads", url="https://t.me/ForeignXHubBot?start=premium")],
    ])


async def send_post_to_channels(
    app: Client,
    title: str,
    main_link: str,
    preview: str | None,
    channel_ids: list[int],
) -> tuple[int, list[tuple[int, str]]]:
    """
    Sends a text-only post to the given channels.
    Returns (success_count, [(channel_id, error), ...]).
    """
    caption = build_caption(title, preview, main_link)
    buttons = build_buttons()
    ok: list[int] = []
    failed: list[tuple[int, str]] = []

    for cid in channel_ids:
        for attempt in range(2):
            try:
                await app.send_message(
                    chat_id=cid,
                    text=caption,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                ok.append(cid)
                break
            except FloodWait as e:
                if attempt == 0:
                    await asyncio.sleep(e.value + 1)
                    continue
                failed.append((cid, f"FloodWait {e.value}s"))
            except Exception as e:
                print(f"[ERROR] channel {cid}: {e}")
                failed.append((cid, str(e)))
                break

    if ok:
        await db.save_log(title, ok)
    return len(ok), failed
