"""
Database layer — Motor (async MongoDB)
Collections:
  users         — user profiles, settings, tier
  verified_groups — groups allowed to use bot
  stats         — aggregated daily/weekly/monthly stats
  broadcast_ids — users opted in for broadcast
"""

import motor.motor_asyncio
from datetime import datetime, timedelta
from config import MONGO_URI, DB_NAME

_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db      = _client[DB_NAME]

users_col    = db["users"]
groups_col   = db["verified_groups"]
stats_col    = db["stats"]
bcast_col    = db["broadcast_ids"]


# ─── USER ───────────────────────────────────────────────────────────────────────

async def get_user(user_id: int) -> dict:
    user = await users_col.find_one({"_id": user_id})
    if not user:
        user = {
            "_id": user_id,
            "tier": "normal",           # normal | promoted
            "settings": {
                "upload_mode": "tile",   # tile | individual
                "sample_duration": 30,   # seconds
                "scht_gen_mode": "equally_spaced",
                "watermark_video": False,
                "watermark_photo": False,
                "mediainfo_mode": "simple",  # simple | detailed
            },
            "joined": datetime.utcnow(),
            "last_active": datetime.utcnow(),
            "stats": {
                "screenshots": 0,
                "trims": 0,
                "samples": 0,
                "mediainfos": 0,
                "thumbs": 0,
            }
        }
        await users_col.insert_one(user)
    return user


async def update_user_setting(user_id: int, key: str, value):
    await users_col.update_one(
        {"_id": user_id},
        {"$set": {f"settings.{key}": value, "last_active": datetime.utcnow()}}
    )


async def promote_user(user_id: int):
    await users_col.update_one(
        {"_id": user_id},
        {"$set": {"tier": "promoted", "last_active": datetime.utcnow()}},
        upsert=True
    )


async def demote_user(user_id: int):
    await users_col.update_one(
        {"_id": user_id},
        {"$set": {"tier": "normal"}}
    )


async def is_promoted(user_id: int) -> bool:
    user = await users_col.find_one({"_id": user_id}, {"tier": 1})
    return bool(user and user.get("tier") == "promoted")


async def touch_user(user_id: int):
    await users_col.update_one(
        {"_id": user_id},
        {"$set": {"last_active": datetime.utcnow()}},
        upsert=True
    )


async def increment_stat(user_id: int, stat_key: str):
    """Increment a per-user stat counter and global daily stat."""
    await users_col.update_one(
        {"_id": user_id},
        {"$inc": {f"stats.{stat_key}": 1}}
    )
    today = datetime.utcnow().strftime("%Y-%m-%d")
    await stats_col.update_one(
        {"_id": today},
        {"$inc": {stat_key: 1, "total": 1}},
        upsert=True
    )


async def get_user_settings(user_id: int) -> dict:
    user = await get_user(user_id)
    return user["settings"]


# ─── GROUPS ─────────────────────────────────────────────────────────────────────

async def verify_group(group_id: int, added_by: int):
    await groups_col.update_one(
        {"_id": group_id},
        {"$set": {"added_by": added_by, "verified_at": datetime.utcnow()}},
        upsert=True
    )


async def unverify_group(group_id: int):
    await groups_col.delete_one({"_id": group_id})


async def is_group_verified(group_id: int) -> bool:
    return bool(await groups_col.find_one({"_id": group_id}))


# ─── BROADCAST ──────────────────────────────────────────────────────────────────

async def add_broadcast_user(user_id: int):
    await bcast_col.update_one({"_id": user_id}, {"$set": {"_id": user_id}}, upsert=True)


async def get_all_broadcast_ids() -> list:
    return [doc["_id"] async for doc in bcast_col.find({}, {"_id": 1})]


# ─── STATS ──────────────────────────────────────────────────────────────────────

async def get_stats_range(days: int) -> dict:
    cutoff = datetime.utcnow() - timedelta(days=days)
    dates  = [(cutoff + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)]
    totals: dict = {"screenshots": 0, "trims": 0, "samples": 0, "mediainfos": 0, "thumbs": 0, "total": 0}
    async for doc in stats_col.find({"_id": {"$in": dates}}):
        for k in totals:
            totals[k] += doc.get(k, 0)
    return totals


async def get_user_count() -> int:
    return await users_col.count_documents({})


async def get_active_users(days: int) -> int:
    cutoff = datetime.utcnow() - timedelta(days=days)
    return await users_col.count_documents({"last_active": {"$gte": cutoff}})
