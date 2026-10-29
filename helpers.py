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
        f"🔞 <b>{html.escape(title)}</b>",
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
        link = _a("Sample Video", preview)
        lines += [f"<blockquote>🍷 <b>WATCH SAMPLE</b>\n🫦 {link}\n🫦 {link}</blockquote>", ""]

    link = _a("Full Video", main_link)
    lines += [
        f"<blockquote>🥂 <b>GET FULL VIDEO</b>\n🌶 {link}\n🌶 {link}</blockquote>",
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
    counter_id: str = "general",
) -> tuple[str, int, list[tuple[int, str]]]:
    """
    Assigns the next POST NO and sends the post, under one lock.
    Numbering therefore always follows the real publish order in the channel
    (a post scheduled for tomorrow gets its number tomorrow, not today).
    If nothing could be posted, the number is given back.
    Returns (title, success_count, failed).
    """
    async with _publish_lock:
        title = await db.next_post_title(counter_id)
        try:
            sent, failed = await send_post_to_channels(
                app, title, main_link, preview, channel_ids, duration, size_bytes
            )
        except Exception:
            await db.rollback_post_no(counter_id)
            raise
        if not sent:
            await db.rollback_post_no(counter_id)
        return title, sent, failed


async def publish_targets(
    app: Client,
    target: dict,
    main_link: str,
    preview: str | None,
    duration: float | None = None,
    size_bytes: int | None = None,
) -> list[dict]:
    """
    Posts to every selected group (each group = its own message with its own POST NO)
    and to manually picked channels (general counter). Groups are resolved NOW,
    so channels added to a group after scheduling are included.
    Returns [{"label", "title", "sent", "failed", "total"}, ...].
    """
    results: list[dict] = []
    all_groups = await db.get_groups()
    if target.get("all_groups"):
        selected = all_groups
    else:
        wanted = set(target.get("groups", []))
        selected = [g for g in all_groups if str(g["_id"]) in wanted]
        if len(selected) < len(wanted):
            results.append({"label": "Deleted group", "title": "-", "sent": 0,
                            "failed": [("-", "group no longer exists")], "total": 0})

    for g in selected:
        ids = g.get("channel_ids", [])
        if not ids:
            results.append({"label": g["name"], "title": "-", "sent": 0,
                            "failed": [("-", "group has no channels")], "total": 0})
            continue
        title, sent, failed = await publish(
            app, main_link, preview, ids, duration, size_bytes, counter_id=f"group:{g['_id']}"
        )
        results.append({"label": g["name"], "title": title, "sent": sent, "failed": failed, "total": len(ids)})

    manual = target.get("channels") or []
    if manual:
        title, sent, failed = await publish(app, main_link, preview, manual, duration, size_bytes)
        results.append({"label": "Manual", "title": title, "sent": sent, "failed": failed, "total": len(manual)})
    return results


def format_results(results: list[dict]) -> str:
    if not results:
        return "❌ Nothing to post: no channels found."
    lines = []
    for r in results:
        if r["sent"]:
            lines.append(f"✅ **{r['title']}** → {r['label']}: {r['sent']}/{r['total']}")
        else:
            lines.append(f"❌ {r['label']}: nothing posted")
        for cid, err in r["failed"]:
            lines.append(f"   ⚠️ `{cid}` — {err[:60]}")
    return "\n".join(lines)
