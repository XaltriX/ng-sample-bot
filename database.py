"""
MongoDB database layer using motor (async).
"""
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

from config import MONGO_URI, MONGO_DB_NAME

IST = timezone(timedelta(hours=5, minutes=30))

# tz_aware=True → datetimes come back with tzinfo (otherwise they are naive)
client = AsyncIOMotorClient(MONGO_URI, tz_aware=True)
db = client[MONGO_DB_NAME]

channels_col = db["channels"]
scheduled_col = db["scheduled_posts"]
logs_col = db["posted_logs"]
samples_col = db["samples"]
counters_col = db["counters"]
deletions_col = db["sample_deletions"]


# ── Channels ──────────────────────────────────────────────────────────────────

async def add_channel(channel_id: int, title: str):
    existing = await channels_col.find_one({"channel_id": channel_id})
    if existing:
        return False
    await channels_col.insert_one({
        "channel_id": channel_id,
        "title": title,
        "added_at": datetime.now(timezone.utc),
    })
    return True


async def remove_channel(channel_id: int):
    await groups_col.update_many({}, {"$pull": {"channel_ids": channel_id}})  # also leave its group
    result = await channels_col.delete_one({"channel_id": channel_id})
    return result.deleted_count > 0


async def get_all_channels():
    return await channels_col.find({}).to_list(length=None)


# ── Post numbering ────────────────────────────────────────────────────────────

async def next_post_title(counter_id: str = "general") -> str:
    """Atomic counter → 'POST NO - 01', '02', ..."""
    doc = await counters_col.find_one_and_update(
        {"_id": counter_id},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"POST NO - {doc['seq']:02d}"


async def rollback_post_no(counter_id: str = "general"):
    """Give the number back when nothing was posted (called under the publish lock)."""
    await counters_col.update_one({"_id": counter_id}, {"$inc": {"seq": -1}})


# ── Scheduled Posts ───────────────────────────────────────────────────────────

async def save_scheduled_post(data: dict):
    data["created_at"] = datetime.now(timezone.utc)
    data["status"] = "pending"
    result = await scheduled_col.insert_one(data)
    return result.inserted_id


async def claim_due_post():
    """Atomically take ONE due post (pending → sending). Prevents double-sends."""
    return await scheduled_col.find_one_and_update(
        {"status": "pending", "schedule_time": {"$lte": datetime.now(timezone.utc)}},
        {"$set": {"status": "sending"}},
        sort=[("schedule_time", 1)],
        return_document=ReturnDocument.AFTER,
    )


async def finish_post(post_id, status: str, sent: int, failed: int, title: str | None = None):
    await scheduled_col.update_one(
        {"_id": post_id},
        {"$set": {
            "status": status,
            **({"title": title} if title else {}),
            "sent_count": sent,
            "failed_count": failed,
            "finished_at": datetime.now(timezone.utc),
        }},
    )


async def get_all_pending_posts():
    return await scheduled_col.find({"status": "pending"}).sort("schedule_time", 1).to_list(length=None)


async def cancel_post(post_id):
    # only pending posts can be cancelled (an already-sent post stays "sent")
    await scheduled_col.update_one(
        {"_id": ObjectId(str(post_id)), "status": "pending"},
        {"$set": {"status": "cancelled"}},
    )


# ── Posted Logs ───────────────────────────────────────────────────────────────

async def save_log(title: str, channels: list):
    await logs_col.insert_one({
        "title": title,
        "channels": channels,
        "posted_at": datetime.now(timezone.utc),
    })


async def get_today_sent_count():
    # "today" = IST day
    start = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    return await logs_col.count_documents({"posted_at": {"$gte": start.astimezone(timezone.utc)}})


async def get_last_5_logs():
    return await logs_col.find({}).sort("posted_at", -1).limit(5).to_list(length=None)


# ── Samples (file store) ──────────────────────────────────────────────────────

async def create_sample(key: str, admin_id: int, db_msg_id: int, ready_msg_id: int,
                        duration: float, size_bytes: int):
    await samples_col.insert_one({
        "key": key,
        "admin_id": admin_id,
        "db_msg_id": db_msg_id,        # message id inside the DB channel
        "ready_msg_id": ready_msg_id,  # bot message the admin replies to with the link
        "duration": duration,          # full video duration (seconds)
        "size_bytes": size_bytes,      # full video size
        "main_link": None,
        "status": "awaiting_link",
        "created_at": datetime.now(timezone.utc),
    })


async def get_sample(key: str):
    return await samples_col.find_one({"key": key})


async def get_sample_by_ready_msg(admin_id: int, ready_msg_id: int):
    return await samples_col.find_one({"admin_id": admin_id, "ready_msg_id": ready_msg_id})


async def get_latest_awaiting(admin_id: int):
    return await samples_col.find_one(
        {"admin_id": admin_id, "status": "awaiting_link"}, sort=[("created_at", -1)]
    )


async def set_sample_link(key: str, link: str):
    await samples_col.update_one({"key": key}, {"$set": {"main_link": link, "status": "linked"}})


# ── Sample auto-delete queue ──────────────────────────────────────────────────

async def add_deletion(chat_id: int, video_id: int, note_id: int, link: str, delete_at: datetime):
    await deletions_col.insert_one({
        "chat_id": chat_id,
        "video_id": video_id,
        "note_id": note_id,
        "main_link": link,
        "delete_at": delete_at,
        "done": False,
    })


async def get_due_deletions():
    return await deletions_col.find(
        {"done": False, "delete_at": {"$lte": datetime.now(timezone.utc)}}
    ).to_list(length=100)


async def mark_deletion_done(doc_id):
    await deletions_col.update_one({"_id": doc_id}, {"$set": {"done": True}})


# ── Channel groups ────────────────────────────────────────────────────────────

groups_col = db["groups"]
settings_col = db["settings"]


def _oid(gid):
    try:
        return ObjectId(str(gid))
    except Exception:
        return None


async def create_group(name: str, channel_ids: list, start_no: int = 1):
    res = await groups_col.insert_one({
        "name": name,
        "channel_ids": list(channel_ids),
        "created_at": datetime.now(timezone.utc),
    })
    # counter holds the LAST used number → next post gets start_no
    await counters_col.update_one({"_id": f"group:{res.inserted_id}"}, {"$set": {"seq": start_no - 1}}, upsert=True)
    return res.inserted_id


async def get_groups():
    return await groups_col.find({}).sort("created_at", 1).to_list(length=None)


async def get_group(gid):
    oid = _oid(gid)
    return await groups_col.find_one({"_id": oid}) if oid else None


async def set_group_channels(gid, channel_ids: list):
    await groups_col.update_one({"_id": _oid(gid)}, {"$set": {"channel_ids": list(channel_ids)}})


async def rename_group(gid, name: str):
    await groups_col.update_one({"_id": _oid(gid)}, {"$set": {"name": name}})


async def delete_group(gid):
    oid = _oid(gid)
    if oid:
        await groups_col.delete_one({"_id": oid})
        await counters_col.delete_one({"_id": f"group:{oid}"})


async def get_grouped_channel_ids(exclude_gid=None) -> set:
    """Channel ids that already belong to a group (optionally ignoring one group)."""
    taken = set()
    for g in await groups_col.find({}).to_list(length=None):
        if exclude_gid is not None and str(g["_id"]) == str(exclude_gid):
            continue
        taken.update(g.get("channel_ids", []))
    return taken


async def get_counter(counter_id: str) -> int:
    doc = await counters_col.find_one({"_id": counter_id})
    return int(doc["seq"]) if doc else 0


async def set_counter(counter_id: str, seq: int):
    await counters_col.update_one({"_id": counter_id}, {"$set": {"seq": seq}}, upsert=True)


# ── "Same as last time" ───────────────────────────────────────────────────────

async def set_last_target(admin_id: int, target: dict):
    await settings_col.update_one({"_id": f"last_target:{admin_id}"}, {"$set": {"target": target}}, upsert=True)


async def get_last_target(admin_id: int):
    doc = await settings_col.find_one({"_id": f"last_target:{admin_id}"})
    return doc["target"] if doc else None
