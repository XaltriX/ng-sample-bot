"""
All bot handlers — wired with Pyrogram filters.
"""
from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from datetime import datetime, timezone, timedelta
import re

IST = timezone(timedelta(hours=5, minutes=30))

def to_ist(dt: datetime) -> str:
    """Convert any datetime to IST string."""
    return dt.astimezone(IST).strftime("%d-%m-%Y %H:%M IST")

import database as db
import helpers
import state
import sampler
from config import ADMIN_IDS

# ── Admin guard ───────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ── /start ────────────────────────────────────────────────────────────────────

def register_handlers(app: Client):

    @app.on_message(filters.command("start") & filters.private)
    async def cmd_start(client: Client, msg: Message):
        if not is_admin(msg.from_user.id):
            return await msg.reply("⛔ Unauthorized.")
        state.clear(msg.from_user.id)
        await msg.reply(
            "Welcome to **Linkz_Wallah Bot** 🤖",
            reply_markup=main_menu(),
        )

    # ── /cancel — abort whatever multi-step flow is in progress ────────────────

    @app.on_message(filters.command("cancel") & filters.private)
    async def cmd_cancel(client: Client, msg: Message):
        if not is_admin(msg.from_user.id):
            return
        state.clear(msg.from_user.id)
        await msg.reply("❌ Cancelled.", reply_markup=main_menu())

    # ── Callback router ───────────────────────────────────────────────────────

    @app.on_callback_query()
    async def cb_router(client: Client, cb: CallbackQuery):
        uid = cb.from_user.id
        if not is_admin(uid):
            return await cb.answer("⛔ Unauthorized.", show_alert=True)
        data = cb.data
        # confirm-select buttons answer themselves (they may need an alert)
        if data not in ("pn_confirm_select", "ch_confirm_select"):
            await cb.answer()
        if data in ("post_now", "schedule_post") and not state.get_key(uid, "main_link"):
            return await cb.message.edit_text(
                "⚠️ Session expire ho gaya. Video dobara bhejo.", reply_markup=back_btn()
            )

        # ── Main menu ─────────────────────────────────────────────────────────
        if data == "main_menu":
            state.clear(uid)
            await cb.message.edit_text(
                "Welcome to **Linkz_Wallah Bot** 🤖",
                reply_markup=main_menu(),
            )

        elif data == "create_post":
            state.clear(uid)
            await cb.message.edit_text(
                "🎬 Send the **full video** (video ya file).\n\n"
                "Sample + preview link auto ban jayega, phir bas Download link dena.",
                reply_markup=cancel_btn(),
            )

        elif data == "cancel_create":
            state.clear(uid)
            await cb.message.edit_text("❌ Post creation cancelled.", reply_markup=back_btn())

        # ── Post Now / Schedule ───────────────────────────────────────────────
        elif data == "post_now":
            channels = await db.get_all_channels()
            if not channels:
                return await cb.message.edit_text(
                    "❌ No channels added yet.\n\nAdd channels first.",
                    reply_markup=back_btn(),
                )
            state.set_key(uid, "post_mode", "now")
            await cb.message.edit_text(
                "📢 **Choose channels to post:**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ All Channels", callback_data="pn_ch_all")],
                    [InlineKeyboardButton("🎯 Select Channels", callback_data="pn_ch_select")],
                ]),
            )

        elif data == "pn_ch_all":
            channels = await db.get_all_channels()
            cids = [c["channel_id"] for c in channels]
            state.set_key(uid, "pn_selected", cids)
            await do_post_now(client, cb.message, uid)

        elif data == "pn_ch_select":
            channels = await db.get_all_channels()
            state.set_key(uid, "pn_toggle", {c["channel_id"]: False for c in channels})
            state.set_key(uid, "pn_channel_list", channels)
            await show_pn_toggle_list(cb.message, uid)

        elif data.startswith("pn_toggle_"):
            cid = int(data.split("_", 2)[2])
            toggles = state.get_key(uid, "pn_toggle", {})
            toggles[cid] = not toggles.get(cid, False)
            state.set_key(uid, "pn_toggle", toggles)
            await show_pn_toggle_list(cb.message, uid)

        elif data == "pn_confirm_select":
            toggles = state.get_key(uid, "pn_toggle", {})
            selected = [cid for cid, chosen in toggles.items() if chosen]
            if not selected:
                return await cb.answer("⚠️ Select at least one channel.", show_alert=True)
            await cb.answer()
            state.set_key(uid, "pn_selected", selected)
            await do_post_now(client, cb.message, uid)

        elif data == "schedule_post":
            state.set_key(uid, "step", "sched_type")
            await cb.message.edit_text(
                "⏰ Choose schedule type:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚡ Quick Schedule", callback_data="quick_sched"),
                     InlineKeyboardButton("🕒 Custom Time", callback_data="custom_sched")],
                    [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
                ]),
            )

        # ── Quick Schedule ────────────────────────────────────────────────────
        elif data == "quick_sched":
            await cb.message.edit_text(
                "⚡ Choose mode:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⏱ Minutes", callback_data="qs_minutes"),
                     InlineKeyboardButton("🕐 Hours", callback_data="qs_hours")],
                    [InlineKeyboardButton("🎯 Exact Hour", callback_data="qs_exact")],
                    [InlineKeyboardButton("📅 Today", callback_data="qs_today"),
                     InlineKeyboardButton("🌅 Tomorrow", callback_data="qs_tomorrow")],
                    [InlineKeyboardButton("🔙 Back", callback_data="schedule_post")],
                ]),
            )

        elif data == "qs_minutes":
            await cb.message.edit_text(
                "⏱ Choose minutes:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("+5m", callback_data="qst_5m"),
                     InlineKeyboardButton("+10m", callback_data="qst_10m"),
                     InlineKeyboardButton("+15m", callback_data="qst_15m")],
                    [InlineKeyboardButton("+30m", callback_data="qst_30m"),
                     InlineKeyboardButton("+60m", callback_data="qst_60m")],
                    [InlineKeyboardButton("🔙 Back", callback_data="quick_sched")],
                ]),
            )

        elif data == "qs_hours":
            await cb.message.edit_text(
                "🕐 Choose hours:",
                reply_markup=build_hours_menu(),
            )

        elif data == "qs_exact":
            await cb.message.edit_text(
                "🎯 Choose exact hour (next 12h):",
                reply_markup=build_exact_hour_menu(),
            )

        elif data == "qs_today":
            await cb.message.edit_text(
                "📅 Choose time today (IST):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("6 PM", callback_data="qst_today_18"),
                     InlineKeyboardButton("7 PM", callback_data="qst_today_19"),
                     InlineKeyboardButton("8 PM", callback_data="qst_today_20")],
                    [InlineKeyboardButton("9 PM", callback_data="qst_today_21"),
                     InlineKeyboardButton("10 PM", callback_data="qst_today_22")],
                    [InlineKeyboardButton("🔙 Back", callback_data="quick_sched")],
                ]),
            )

        elif data == "qs_tomorrow":
            await cb.message.edit_text(
                "🌅 Choose time tomorrow (IST):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("9 AM", callback_data="qst_tmrw_9"),
                     InlineKeyboardButton("12 PM", callback_data="qst_tmrw_12")],
                    [InlineKeyboardButton("6 PM", callback_data="qst_tmrw_18"),
                     InlineKeyboardButton("9 PM", callback_data="qst_tmrw_21")],
                    [InlineKeyboardButton("🔙 Back", callback_data="quick_sched")],
                ]),
            )

        elif data == "custom_sched":
            state.set_key(uid, "step", "custom_time")
            await cb.message.edit_text(
                "🕒 Send date & time in format:\n`DD-MM HH:MM`\n\nExample: `25-07 21:00`"
            )

        # ── Quick schedule time resolution ────────────────────────────────────
        elif data.startswith("qst_"):
            sched_time = resolve_quick_time(data)
            if not sched_time:
                return await cb.message.edit_text("❌ Invalid option.")
            if sched_time <= datetime.now(timezone.utc):
                return await cb.message.edit_text(
                    "⚠️ Ye time nikal chuka hai. Doosra time chuno.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔙 Back", callback_data="quick_sched")]]
                    ),
                )
            state.set_key(uid, "schedule_time", sched_time)
            await show_channel_selection(cb.message, uid)

        # ── Channel selection ─────────────────────────────────────────────────
        elif data == "ch_all":
            channels = await db.get_all_channels()
            cids = [c["channel_id"] for c in channels]
            state.set_key(uid, "selected_channels", cids)
            state.set_key(uid, "channels_mode", "all")
            await show_schedule_confirm(cb.message, uid)

        elif data == "ch_select":
            channels = await db.get_all_channels()
            if not channels:
                return await cb.message.edit_text("❌ No channels found.", reply_markup=back_btn())
            state.set_key(uid, "toggle_channels", {c["channel_id"]: False for c in channels})
            state.set_key(uid, "channel_list", channels)
            await show_toggle_list(cb.message, uid)

        elif data.startswith("toggle_"):
            cid = int(data.split("_", 1)[1])
            toggles = state.get_key(uid, "toggle_channels", {})
            toggles[cid] = not toggles.get(cid, False)
            state.set_key(uid, "toggle_channels", toggles)
            await show_toggle_list(cb.message, uid)

        elif data == "ch_confirm_select":
            toggles = state.get_key(uid, "toggle_channels", {})
            selected = [cid for cid, chosen in toggles.items() if chosen]
            if not selected:
                return await cb.answer("⚠️ Select at least one channel.", show_alert=True)
            await cb.answer()
            state.set_key(uid, "selected_channels", selected)
            state.set_key(uid, "channels_mode", "select")
            await show_schedule_confirm(cb.message, uid)

        # ── Schedule confirm/cancel ───────────────────────────────────────────
        elif data == "sched_confirm":
            sess = dict(state.get(uid))
            state.clear(uid)  # clear first so a double-tap can't schedule twice
            if not sess.get("main_link") or not sess.get("schedule_time"):
                return await cb.message.edit_text(
                    "⚠️ Session expire ho gaya. Video dobara bhejo.", reply_markup=back_btn()
                )
            channels_mode = sess.get("channels_mode", "select")
            title = await db.next_post_title()
            await db.save_scheduled_post({
                "title": title,
                "main_link": sess["main_link"],
                "preview": sess.get("preview"),
                "channels_mode": channels_mode,
                # "all" mode → resolved LIVE by the scheduler at send time,
                # so we don't need to freeze the channel list here.
                "channels": sess["selected_channels"] if channels_mode == "select" else [],
                "schedule_time": sess["schedule_time"],
            })
            state.clear(uid)
            await cb.message.edit_text(f"✅ **{title}** scheduled!", reply_markup=back_btn())

        elif data == "sched_cancel":
            state.clear(uid)
            await cb.message.edit_text("❌ Scheduling cancelled.", reply_markup=back_btn())

        # ── Channel Manager ───────────────────────────────────────────────────
        elif data == "manage_channels":
            await cb.message.edit_text(
                "📢 **Channel Manager**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Add Channel", callback_data="add_channel")],
                    [InlineKeyboardButton("➖ Remove Channel", callback_data="remove_channel")],
                    [InlineKeyboardButton("📋 List Channels", callback_data="list_channels")],
                    [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
                ]),
            )

        elif data == "add_channel":
            state.set_key(uid, "step", "add_channel")
            await cb.message.edit_text(
                "➕ Send the **Channel ID** (e.g. `-1001234567890`)\nor forward any message from the channel:"
            )

        elif data == "remove_channel":
            channels = await db.get_all_channels()
            if not channels:
                return await cb.message.edit_text("No channels found.", reply_markup=back_btn())
            buttons = [
                [InlineKeyboardButton(f"❌ {c['title']} ({c['channel_id']})", callback_data=f"del_ch_{c['channel_id']}")]
                for c in channels
            ]
            buttons.append([InlineKeyboardButton("🔙 Back", callback_data="manage_channels")])
            await cb.message.edit_text("Select channel to remove:", reply_markup=InlineKeyboardMarkup(buttons))

        elif data.startswith("del_ch_"):
            cid = int(data.split("_", 2)[2])
            await db.remove_channel(cid)
            await cb.message.edit_text("✅ Channel removed.", reply_markup=back_btn())

        elif data == "list_channels":
            channels = await db.get_all_channels()
            if not channels:
                text = "No channels added yet."
            else:
                lines = [f"• **{c['title']}** — `{c['channel_id']}`" for c in channels]
                text = "📋 **Channels:**\n\n" + "\n".join(lines)
            await cb.message.edit_text(text, reply_markup=back_btn())

        # ── Dashboard ─────────────────────────────────────────────────────────
        elif data == "dashboard":
            await show_dashboard(cb.message)

        elif data == "view_scheduled":
            posts = await db.get_all_pending_posts()
            if not posts:
                return await cb.message.edit_text("No scheduled posts.", reply_markup=back_btn())
            lines = []
            for p in posts:
                t = to_ist(p["schedule_time"])
                lines.append(f"• {t} — {p['title'][:30]}")
            await cb.message.edit_text(
                "📅 **All Scheduled:**\n\n" + "\n".join(lines),
                reply_markup=back_btn(),
            )

        elif data == "cancel_post_menu":
            posts = await db.get_all_pending_posts()
            if not posts:
                return await cb.message.edit_text("No pending posts.", reply_markup=back_btn())
            buttons = [
                [InlineKeyboardButton(
                    f"❌ {p['title'][:25]} ({p['schedule_time'].astimezone(IST).strftime('%d-%m %H:%M IST')})",
                    callback_data=f"do_cancel_{p['_id']}"
                )]
                for p in posts
            ]
            buttons.append([InlineKeyboardButton("🔙 Back", callback_data="dashboard")])
            await cb.message.edit_text("Select post to cancel:", reply_markup=InlineKeyboardMarkup(buttons))

        elif data.startswith("do_cancel_"):
            post_id = data.split("_", 2)[2]
            await db.cancel_post(post_id)
            await cb.message.edit_text("✅ Post cancelled.", reply_markup=back_btn())

    # ── Message handler (multi-step text input) ────────────────────────────────

    @app.on_message(filters.private & ~filters.command(["start", "cancel"]))
    async def msg_handler(client: Client, msg: Message):
        uid = msg.from_user.id
        if not is_admin(uid):
            return

        step = state.get_key(uid, "step")

        # Video / file → sample pipeline (unless we're waiting for a channel forward)
        if step != "add_channel" and (msg.video or msg.document):
            if msg.document and not (msg.document.mime_type or "").startswith("video/"):
                return await msg.reply("⚠️ Sirf video file bhejo.")
            status = await msg.reply("⏳ Queue me add ho gaya…")
            return await sampler.enqueue(client, msg, status)

        if step == "add_channel":
            channel_id = None
            title = "Unknown"

            if msg.forward_from_chat:
                channel_id = msg.forward_from_chat.id
                title = msg.forward_from_chat.title or "Unknown"
            elif msg.text:
                text = msg.text.strip()
                # Invite link
                if "t.me/" in text:
                    try:
                        chat = await client.get_chat(text)
                        channel_id = chat.id
                        title = chat.title or "Unknown"
                    except Exception as e:
                        return await msg.reply(f"❌ Could not resolve link: {e}")
                else:
                    try:
                        channel_id = int(text)
                        chat = await client.get_chat(channel_id)
                        title = chat.title or str(channel_id)
                    except Exception as e:
                        return await msg.reply(f"❌ Invalid channel ID: {e}")

            if channel_id is None:
                return await msg.reply("❌ Could not extract channel ID. Try forwarding a message.")

            added = await db.add_channel(channel_id, title)
            state.set_key(uid, "step", None)
            if added:
                await msg.reply(f"✅ Channel **{title}** (`{channel_id}`) added.", reply_markup=back_btn())
            else:
                await msg.reply(f"⚠️ Channel already exists.", reply_markup=back_btn())

        elif step == "custom_time":
            text = msg.text.strip() if msg.text else ""
            try:
                # Parse DD-MM HH:MM as IST (UTC+5:30)
                dt = datetime.strptime(text, "%d-%m %H:%M")
                now = datetime.now(timezone.utc)
                ist_offset = timedelta(hours=5, minutes=30)
                # Use current year
                dt = dt.replace(year=now.year)
                # Convert IST to UTC
                sched_time = dt.replace(tzinfo=timezone(ist_offset)).astimezone(timezone.utc)
                if sched_time <= now:
                    return await msg.reply("⚠️ Time must be in the future.")
                state.set_key(uid, "schedule_time", sched_time)
                state.set_key(uid, "step", None)
                await show_channel_selection_msg(msg, uid)
            except ValueError:
                await msg.reply("❌ Wrong format. Use `DD-MM HH:MM` e.g. `25-07 21:00`")

        else:
            # Any other text = download link for a sample
            await handle_link(msg, uid, msg.text.strip() if msg.text else "")


# ── Helper UI functions ───────────────────────────────────────────────────────

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Create Post", callback_data="create_post")],
        [InlineKeyboardButton("📢 Manage Channels", callback_data="manage_channels")],
        [InlineKeyboardButton("📊 Dashboard", callback_data="dashboard")],
    ])


def cancel_btn() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_create")]])


def back_btn() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]])


def build_hours_menu() -> InlineKeyboardMarkup:
    """Relative +Nh quick options: 1..12, then 16, 20, 24."""
    hours = list(range(1, 13)) + [16, 20, 24]
    rows = []
    row = []
    for h in hours:
        row.append(InlineKeyboardButton(f"+{h}h", callback_data=f"qst_{h}h"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="quick_sched")])
    return InlineKeyboardMarkup(rows)


def build_exact_hour_menu() -> InlineKeyboardMarkup:
    """
    Next 12 exact hour marks (e.g. now=13:44 → 14:00, 15:00 ... 01:00),
    computed fresh every time this menu is opened.
    """
    now_ist = datetime.now(timezone.utc).astimezone(IST)
    rows = []
    row = []
    for i in range(1, 13):
        target = (now_ist + timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        day_offset = (target.date() - now_ist.date()).days
        label = target.strftime("%H:00")
        row.append(InlineKeyboardButton(label, callback_data=f"qst_eh_{target.hour}_{day_offset}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="quick_sched")])
    return InlineKeyboardMarkup(rows)


async def handle_link(msg: Message, uid: int, text: str):
    """Admin sent a download link → attach to the matching sample."""
    if not text:
        return
    if not text.lower().startswith(("http://", "https://")):
        return await msg.reply("❌ Valid download link bhejo (http:// ya https:// se shuru).")

    sample = None
    if msg.reply_to_message:
        sample = await db.get_sample_by_ready_msg(uid, msg.reply_to_message.id)
    if not sample:
        sample = await db.get_latest_awaiting(uid)
    if not sample:
        return await msg.reply("⚠️ Pehle video bhejo, sample ready hone ke baad link bhejna.")

    await db.set_sample_link(sample["key"], text)
    state.clear(uid)
    state.set_key(uid, "main_link", text)
    state.set_key(uid, "preview", helpers.preview_link(sample["key"]))
    await msg.reply(
        "✅ **Post Ready!**",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Post Now", callback_data="post_now")],
            [InlineKeyboardButton("⏰ Schedule Post", callback_data="schedule_post")],
        ]),
    )


async def do_post_now(client, message, uid: int):
    sess = dict(state.get(uid))
    state.clear(uid)  # clear first so a double-tap can't post twice
    cids = sess.get("pn_selected", [])
    if not sess.get("main_link") or not cids:
        return await message.edit_text(
            "⚠️ Session expire ho gaya. Video dobara bhejo.", reply_markup=back_btn()
        )
    title = await db.next_post_title()
    await message.edit_text(f"⏳ **{title}** post ho raha hai…")
    sent, failed = await helpers.send_post_to_channels(
        app=client,
        title=title,
        main_link=sess["main_link"],
        preview=sess.get("preview"),
        channel_ids=cids,
    )
    text = f"✅ **{title}** posted in **{sent}/{len(cids)}** channel(s)."
    if failed:
        text += "\n\n⚠️ Failed:\n" + "\n".join(f"• `{cid}` — {err[:60]}" for cid, err in failed)
    await message.edit_text(text, reply_markup=back_btn())


async def show_pn_toggle_list(message, uid: int):
    toggles: dict = state.get_key(uid, "pn_toggle", {})
    channel_list: list = state.get_key(uid, "pn_channel_list", [])
    buttons = []
    for c in channel_list:
        cid = c["channel_id"]
        mark = "☑" if toggles.get(cid) else "☐"
        buttons.append([InlineKeyboardButton(
            f"{mark} {c['title']}",
            callback_data=f"pn_toggle_{cid}"
        )])
    buttons.append([InlineKeyboardButton("✅ Confirm & Post", callback_data="pn_confirm_select")])
    try:
        await message.edit_text("Select channels to post:", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        pass


async def show_channel_selection(message, uid: int):
    channels = await db.get_all_channels()
    if not channels:
        await message.edit_text("❌ No channels found.", reply_markup=back_btn())
        return
    await message.edit_text(
        "📢 Choose channels:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ All Channels", callback_data="ch_all")],
            [InlineKeyboardButton("🎯 Select Channels", callback_data="ch_select")],
        ]),
    )


async def show_channel_selection_msg(message: Message, uid: int):
    channels = await db.get_all_channels()
    if not channels:
        await message.reply("❌ No channels found.", reply_markup=back_btn())
        return
    await message.reply(
        "📢 Choose channels:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ All Channels", callback_data="ch_all")],
            [InlineKeyboardButton("🎯 Select Channels", callback_data="ch_select")],
        ]),
    )


async def show_toggle_list(message, uid: int):
    toggles: dict = state.get_key(uid, "toggle_channels", {})
    channel_list: list = state.get_key(uid, "channel_list", [])
    buttons = []
    for c in channel_list:
        cid = c["channel_id"]
        mark = "☑" if toggles.get(cid) else "☐"
        buttons.append([InlineKeyboardButton(
            f"{mark} {c['title']}",
            callback_data=f"toggle_{cid}"
        )])
    buttons.append([InlineKeyboardButton("✅ Confirm Selection", callback_data="ch_confirm_select")])
    try:
        await message.edit_text("Select channels:", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        pass


async def show_schedule_confirm(message, uid: int):
    sess = state.get(uid)
    sched_time: datetime = sess.get("schedule_time")
    if not sched_time or not sess.get("main_link"):
        return await message.edit_text(
            "⚠️ Session expire ho gaya. Video dobara bhejo.", reply_markup=back_btn()
        )
    channels = sess.get("selected_channels", [])
    channels_mode = sess.get("channels_mode", "select")

    ist_offset = timedelta(hours=5, minutes=30)
    ist_time = sched_time.astimezone(timezone(ist_offset))
    time_str = ist_time.strftime("%d-%m-%Y %H:%M IST")

    if channels_mode == "all":
        channels_line = f"📢 Channels: **All ({len(channels)})** — new channels auto-included"
    else:
        channels_line = f"📢 Channels: {len(channels)}"

    await message.edit_text(
        f"📋 **Confirm Schedule**\n\n"
        f"🕒 Time: `{time_str}`\n"
        f"{channels_line}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm", callback_data="sched_confirm"),
             InlineKeyboardButton("❌ Cancel", callback_data="sched_cancel")],
        ]),
    )


async def show_dashboard(message):
    pending = await db.get_all_pending_posts()
    today_count = await db.get_today_sent_count()
    last5 = await db.get_last_5_logs()

    lines = [
        f"📊 **Dashboard**\n",
        f"⏳ Pending posts: **{len(pending)}**",
        f"✅ Sent today: **{today_count}**\n",
    ]

    upcoming = pending[:3]
    if upcoming:
        lines.append("🗓 **Upcoming (next 3):**")
        for p in upcoming:
            t = to_ist(p["schedule_time"])
            lines.append(f"  • {t} — {p['title'][:25]}")
        lines.append("")

    if last5:
        lines.append("📜 **Last 5 sent:**")
        for l in last5:
            lines.append(f"  • {l['title'][:30]}")

    await message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 View All Scheduled", callback_data="view_scheduled")],
            [InlineKeyboardButton("❌ Cancel a Post", callback_data="cancel_post_menu")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")],
        ]),
    )


def resolve_quick_time(data: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    ist_offset = timedelta(hours=5, minutes=30)

    # Minutes: qst_5m, qst_10m etc.
    m = re.match(r"qst_(\d+)m$", data)
    if m:
        return now + timedelta(minutes=int(m.group(1)))

    # Hours (relative): qst_1h, qst_2h ... qst_24h
    m = re.match(r"qst_(\d+)h$", data)
    if m:
        return now + timedelta(hours=int(m.group(1)))

    # Exact hour: qst_eh_<hour>_<day_offset>
    m = re.match(r"qst_eh_(\d+)_(\d+)$", data)
    if m:
        hour = int(m.group(1))
        day_offset = int(m.group(2))
        now_ist = now.astimezone(timezone(ist_offset))
        target_date = now_ist + timedelta(days=day_offset)
        target = target_date.replace(hour=hour, minute=0, second=0, microsecond=0)
        return target.astimezone(timezone.utc)

    # Today IST hour: qst_today_18
    m = re.match(r"qst_today_(\d+)$", data)
    if m:
        hour = int(m.group(1))
        today_ist = now.astimezone(timezone(ist_offset))
        target = today_ist.replace(hour=hour, minute=0, second=0, microsecond=0)
        return target.astimezone(timezone.utc)

    # Tomorrow IST hour: qst_tmrw_9
    m = re.match(r"qst_tmrw_(\d+)$", data)
    if m:
        hour = int(m.group(1))
        tmrw_ist = (now + timedelta(days=1)).astimezone(timezone(ist_offset))
        target = tmrw_ist.replace(hour=hour, minute=0, second=0, microsecond=0)
        return target.astimezone(timezone.utc)

    return None
