"""
Channel groups + "where to post" selection UI.

A target = {"groups": [group_id, ...], "all_groups": bool, "channels": [channel_id, ...]}
Each group has its own POST NO counter; manual channels use the "general" counter.
"""
from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup

import database as db
import state

GROUP_STEPS = ("grp_name", "grp_start", "grp_rename", "grp_setnum")


def _reset_group_state(uid: int):
    """Clear only the group-editing keys, so a pending post (video + link) is kept."""
    s = state.get(uid)
    for k in ("g_name", "g_start", "g_mode", "g_sel", "g_target"):
        s.pop(k, None)
    s["step"] = None


def _back(data: str, text: str = "🔙 Back") -> list:
    return [Btn(text, callback_data=data)]


async def _send(message, text: str, rows: list, reply: bool = False):
    try:
        if reply:
            await message.reply(text, reply_markup=Markup(rows))
        else:
            await message.edit_text(text, reply_markup=Markup(rows))
    except Exception as e:  # e.g. MessageNotModified
        print(f"[UI] {e}")


def describe(target: dict, groups: list) -> str:
    if target.get("all_groups"):
        return f"All groups ({len(groups)})"
    wanted = set(target.get("groups", []))
    parts = []
    names = [g["name"] for g in groups if str(g["_id"]) in wanted]
    if names:
        parts.append(", ".join(names))
    if target.get("channels"):
        parts.append(f"{len(target['channels'])} manual channel(s)")
    return " + ".join(parts) or "—"


# ── Target selection (used by Post Now and Schedule) ─────────────────────────

async def show_target_menu(message, uid: int, mode: str | None = None, reply: bool = False):
    if mode:
        state.set_key(uid, "target_mode", mode)
        state.set_key(uid, "tg_groups", [])
    groups = await db.get_groups()
    chosen = state.get_key(uid, "tg_groups", [])

    rows = []
    for g in groups:
        gid = str(g["_id"])
        mark = "☑" if gid in chosen else "☐"
        rows.append([Btn(f"{mark} {g['name']} ({len(g.get('channel_ids', []))})", callback_data=f"tg_g_{gid}")])

    last = await db.get_last_target(uid)
    if last:
        rows.append([Btn(f"⚡ Same as last time: {describe(last, groups)[:35]}", callback_data="tg_last")])
    if groups:
        rows.append([Btn("✅ Confirm", callback_data="tg_ok"), Btn("🌐 All Groups", callback_data="tg_all")])
    rows.append([Btn("🎯 Pick channels manually", callback_data="tg_manual")])
    rows.append(_back("main_menu", "❌ Cancel"))

    text = (
        "📢 **Choose where to post:**\n\nTap groups to select, then **Confirm**."
        if groups else
        "📢 **No groups yet.**\n\nCreate groups in Channel Manager → Groups, or pick channels manually."
    )
    await _send(message, text, rows, reply)


async def show_manual(message, uid: int):
    channels = await db.get_all_channels()
    sel = state.get_key(uid, "tg_manual_sel", [])
    rows = [
        [Btn(("☑" if c["channel_id"] in sel else "☐") + f" {c['title']}", callback_data=f"tg_c_{c['channel_id']}")]
        for c in channels
    ]
    rows.append([Btn("✅ Confirm", callback_data="tg_mc")])
    rows.append(_back("tg_back"))
    await _send(message, "🎯 **Select channels:**", rows)


async def _finish(client, message, uid: int, target: dict, on_done, save: bool = True):
    state.set_key(uid, "target", target)
    if save:
        await db.set_last_target(uid, target)
    await on_done(client, message, uid)


# ── Group management ─────────────────────────────────────────────────────────

async def show_groups(message, reply: bool = False):
    groups = await db.get_groups()
    rows = []
    for g in groups:
        nxt = await db.get_counter(f"group:{g['_id']}") + 1
        rows.append([Btn(
            f"🗂 {g['name']} · {len(g.get('channel_ids', []))} ch · next #{nxt:02d}",
            callback_data=f"gr_v_{g['_id']}",
        )])
    rows.append([Btn("➕ New Group", callback_data="gr_new")])
    rows.append(_back("manage_channels"))
    await _send(message, "🗂 **Channel Groups**" + ("" if groups else "\n\nNo groups yet."), rows, reply)


async def show_group(message, gid: str, reply: bool = False):
    g = await db.get_group(gid)
    if not g:
        return await show_groups(message, reply)
    titles = {c["channel_id"]: c["title"] for c in await db.get_all_channels()}
    chans = "\n".join(f"• {titles.get(c, c)}" for c in g.get("channel_ids", [])) or "• (no channels)"
    nxt = await db.get_counter(f"group:{g['_id']}") + 1
    rows = [
        [Btn("📢 Edit channels", callback_data=f"gr_e_{gid}")],
        [Btn("✏️ Rename", callback_data=f"gr_r_{gid}"), Btn("🔢 Set next number", callback_data=f"gr_n_{gid}")],
        [Btn("🗑 Delete group", callback_data=f"gr_d_{gid}")],
        _back("grp_menu"),
    ]
    await _send(
        message,
        f"🗂 **{g['name']}**\n\n📢 Channels:\n{chans}\n\n🔢 Next POST NO: **{nxt:02d}**",
        rows, reply,
    )


async def show_group_picker(message, uid: int, reply: bool = False):
    """Channel checklist for a new group (mode 'new') or an existing one (mode = group id)."""
    mode = state.get_key(uid, "g_mode")
    sel = state.get_key(uid, "g_sel", [])
    taken = await db.get_grouped_channel_ids(exclude_gid=None if mode == "new" else mode)
    avail = [c for c in await db.get_all_channels() if c["channel_id"] not in taken]
    rows = [
        [Btn(("☑" if c["channel_id"] in sel else "☐") + f" {c['title']}", callback_data=f"gr_t_{c['channel_id']}")]
        for c in avail
    ]
    rows.append([Btn("✅ Save", callback_data="gr_create" if mode == "new" else f"gr_save_{mode}")])
    rows.append(_back("grp_menu", "🔙 Cancel"))
    text = (
        "📢 **Select the channels for this group:**\n(Channels already in another group are hidden.)"
        if avail else "⚠️ No free channels. Add channels first or free one from another group."
    )
    await _send(message, text, rows, reply)


def _toggle(uid: int, key: str, value):
    sel = list(state.get_key(uid, key, []))
    if value in sel:
        sel.remove(value)
    else:
        sel.append(value)
    state.set_key(uid, key, sel)


# ── Callback router (called from handlers.cb_router) ─────────────────────────

async def on_callback(client, cb, uid: int, data: str, on_done):
    msg = cb.message
    needs_check = data in ("tg_ok", "tg_mc", "gr_create") or data.startswith("gr_save_")
    if not needs_check:
        await cb.answer()

    # ----- target selection
    if data.startswith("tg_g_"):
        _toggle(uid, "tg_groups", data[5:])
        await show_target_menu(msg, uid)

    elif data == "tg_ok":
        sel = state.get_key(uid, "tg_groups", [])
        if not sel:
            return await cb.answer("⚠️ Select at least one group.", show_alert=True)
        await cb.answer()
        await _finish(client, msg, uid, {"groups": sel, "all_groups": False, "channels": []}, on_done)

    elif data == "tg_all":
        await _finish(client, msg, uid, {"groups": [], "all_groups": True, "channels": []}, on_done)

    elif data == "tg_last":
        last = await db.get_last_target(uid)
        if not last:
            return await show_target_menu(msg, uid)
        await _finish(client, msg, uid, last, on_done, save=False)

    elif data == "tg_manual":
        state.set_key(uid, "tg_manual_sel", [])
        await show_manual(msg, uid)

    elif data == "tg_back":
        await show_target_menu(msg, uid)

    elif data.startswith("tg_c_"):
        _toggle(uid, "tg_manual_sel", int(data[5:]))
        await show_manual(msg, uid)

    elif data == "tg_mc":
        sel = state.get_key(uid, "tg_manual_sel", [])
        if not sel:
            return await cb.answer("⚠️ Select at least one channel.", show_alert=True)
        await cb.answer()
        await _finish(client, msg, uid, {"groups": [], "all_groups": False, "channels": sel}, on_done)

    # ----- group management
    elif data == "grp_menu":
        await show_groups(msg)

    elif data == "gr_new":
        _reset_group_state(uid)
        state.set_key(uid, "step", "grp_name")
        await _send(msg, "🗂 Send the **group name** (e.g. Hindi):", [_back("grp_menu", "❌ Cancel")])

    elif data.startswith("gr_t_"):
        _toggle(uid, "g_sel", int(data[5:]))
        await show_group_picker(msg, uid)

    elif data == "gr_create":
        sel = state.get_key(uid, "g_sel", [])
        if not sel:
            return await cb.answer("⚠️ Select at least one channel.", show_alert=True)
        await cb.answer()
        gid = await db.create_group(state.get_key(uid, "g_name"), sel, state.get_key(uid, "g_start", 1))
        _reset_group_state(uid)
        await show_group(msg, str(gid))

    elif data.startswith("gr_v_"):
        await show_group(msg, data[5:])

    elif data.startswith("gr_e_"):
        gid = data[5:]
        g = await db.get_group(gid)
        if not g:
            return await show_groups(msg)
        state.set_key(uid, "g_mode", gid)
        state.set_key(uid, "g_sel", list(g.get("channel_ids", [])))
        await show_group_picker(msg, uid)

    elif data.startswith("gr_save_"):
        sel = state.get_key(uid, "g_sel", [])
        await cb.answer()
        gid = data[8:]
        await db.set_group_channels(gid, sel)
        await show_group(msg, gid)

    elif data.startswith("gr_r_"):
        state.set_key(uid, "step", "grp_rename")
        state.set_key(uid, "g_target", data[5:])
        await _send(msg, "✏️ Send the **new group name**:", [_back(f"gr_v_{data[5:]}", "❌ Cancel")])

    elif data.startswith("gr_n_"):
        state.set_key(uid, "step", "grp_setnum")
        state.set_key(uid, "g_target", data[5:])
        await _send(
            msg, "🔢 Send the **next POST NO** for this group (e.g. 18):",
            [_back(f"gr_v_{data[5:]}", "❌ Cancel")],
        )

    elif data.startswith("gr_d_"):
        gid = data[5:]
        await _send(msg, "🗑 Delete this group? Its channels stay, only the group (and its counter) is removed.", [
            [Btn("✅ Yes, delete", callback_data=f"gr_dd_{gid}"), Btn("❌ No", callback_data=f"gr_v_{gid}")],
        ])

    elif data.startswith("gr_dd_"):
        await db.delete_group(data[6:])
        await show_groups(msg)


# ── Text steps (called from handlers.msg_handler) ────────────────────────────

async def handle_text(msg, uid: int, step: str):
    text = (msg.text or "").strip()

    if step == "grp_name":
        if not text or text.startswith("/"):
            return await msg.reply("❌ Send a text name for the group.")
        state.set_key(uid, "g_name", text)
        state.set_key(uid, "step", "grp_start")
        last = await db.get_counter("post_no")
        hint = f"\n\n(Your last POST NO so far was **{last:02d}**, so send **{last + 1}** to continue it.)" if last else ""
        await msg.reply(f"🔢 Send the **starting POST number** (e.g. 18), or /skip for 01.{hint}")

    elif step == "grp_start":
        if text == "/skip":
            start = 1
        elif text.isdigit() and int(text) >= 1:
            start = int(text)
        else:
            return await msg.reply("❌ Send a number (1 or more), or /skip.")
        state.set_key(uid, "g_start", start)
        state.set_key(uid, "g_mode", "new")
        state.set_key(uid, "g_sel", [])
        state.set_key(uid, "step", None)
        await show_group_picker(msg, uid, reply=True)

    elif step == "grp_rename":
        if not text or text.startswith("/"):
            return await msg.reply("❌ Send a text name.")
        gid = state.get_key(uid, "g_target")
        await db.rename_group(gid, text)
        _reset_group_state(uid)
        await show_group(msg, gid, reply=True)

    elif step == "grp_setnum":
        if not (text.isdigit() and int(text) >= 1):
            return await msg.reply("❌ Send a number (1 or more).")
        gid = state.get_key(uid, "g_target")
        await db.set_counter(f"group:{gid}", int(text) - 1)
        _reset_group_state(uid)
        await show_group(msg, gid, reply=True)
