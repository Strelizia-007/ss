"""
╔══════════════════════════════════════════════════════════════╗
║            SCREENSHOT GENERATOR BOT — bot.py                ║
║  Telethon (MTProto) for ALL updates + file downloads         ║
║  PTB Bot object used ONLY for sending replies                ║
╚══════════════════════════════════════════════════════════════╝

Why this architecture:
  Bot API (PTB polling) and Telethon MTProto cannot both receive
  updates for the same bot token simultaneously — one starves the
  other.  Solution: Telethon receives ALL updates (including media,
  callbacks, commands) and downloads files natively over MTProto
  with no 20 MB limit.  PTB's Bot() is kept only as a convenient
  HTTP sender for formatted messages, keyboards, etc.
"""

import os, asyncio, tempfile, logging
from typing import Optional

from telethon import TelegramClient, events
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl import types as tl_types
from telethon.sessions import StringSession

from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.constants import ParseMode

import config
import database as db
from media_utils import (
    is_adult_content, detect_link_type, download_direct_link, download_gdrive_file,
    generate_screenshots, make_tile_image, trim_video, parse_time_range,
    generate_sample_video, extract_thumbnail,
    parse_simple_mediainfo, parse_detailed_mediainfo, upload_to_telegraph,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Globals ─────────────────────────────────────────────────────────────────
tele: Optional[TelegramClient] = None   # Telethon — receives updates + downloads
bot:  Optional[Bot]            = None   # PTB Bot  — sends replies only

# per-user state  {user_id: {"step": str, "tele_msg": ..., "link_text": ..., ...}}
user_state: dict = {}


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


async def check_fsub(uid: int) -> bool:
    try:
        member = await bot.get_chat_member(config.FSUB_CHANNEL_ID, uid)
        return member.status not in ("left", "kicked", "banned")
    except Exception:
        return False


async def check_group_access(chat_id: int) -> bool:
    if chat_id > 0:
        return True
    return await db.is_group_verified(chat_id)


def get_limits(tier: str) -> dict:
    if tier == "promoted":
        return dict(max_screenshots=config.PROMOTED_MAX_SCREENSHOTS,
                    max_trim_min=config.PROMOTED_MAX_TRIM_MIN,
                    max_sample_sec=config.PROMOTED_MAX_SAMPLE_SEC)
    return dict(max_screenshots=config.NORMAL_MAX_SCREENSHOTS,
                max_trim_min=config.NORMAL_MAX_TRIM_MIN,
                max_sample_sec=config.NORMAL_MAX_SAMPLE_SEC)


# ─── Keyboards ───────────────────────────────────────────────────────────────

def main_keyboard():
    rows = [
        [InlineKeyboardButton(str(n), callback_data=f"scht:{n}") for n in pair]
        for pair in [(2,3),(4,5),(6,7),(8,9)]
    ]
    rows.append([InlineKeyboardButton("10", callback_data="scht:10")])
    rows += [
        [InlineKeyboardButton("📸 Manual Screenshots!", callback_data="action:manual_scht")],
        [InlineKeyboardButton("✂️ Trim Video!",          callback_data="action:trim")],
        [InlineKeyboardButton("🎞 Generate Sample Video!", callback_data="action:sample")],
        [InlineKeyboardButton("📋 Get Media Information",  callback_data="action:mediainfo")],
        [InlineKeyboardButton("🖼 Get Thumbs", callback_data="action:thumbs"),
         InlineKeyboardButton("🎨 Get Covers", callback_data="action:covers")],
    ]
    return InlineKeyboardMarkup(rows)


def settings_keyboard(s: dict):
    upload_lbl = "🖼 Tile Image" if s["upload_mode"]    == "tile"   else "📤 Individual"
    mi_lbl     = "📋 Simple"    if s["mediainfo_mode"] == "simple" else "📊 Detailed"
    wm_vid     = "✅ On" if s["watermark_video"] else "❌ Off"
    wm_photo   = "✅ On" if s["watermark_photo"] else "❌ Off"
    sample_lbl = f"⏱ {s['sample_duration']}s"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload Mode",     callback_data="s:noop"),
         InlineKeyboardButton(upload_lbl,           callback_data="s:toggle_upload")],
        [InlineKeyboardButton("🎞 Sample Duration", callback_data="s:noop"),
         InlineKeyboardButton(sample_lbl,           callback_data="s:cycle_sample")],
        [InlineKeyboardButton("📋 MediaInfo Mode",  callback_data="s:noop"),
         InlineKeyboardButton(mi_lbl,               callback_data="s:toggle_mi")],
        [InlineKeyboardButton("🎬 Watermark Video", callback_data="s:noop"),
         InlineKeyboardButton(wm_vid,               callback_data="s:toggle_wm_vid")],
        [InlineKeyboardButton("📸 Watermark Photos",callback_data="s:noop"),
         InlineKeyboardButton(wm_photo,             callback_data="s:toggle_wm_photo")],
        [InlineKeyboardButton("❌ Close",            callback_data="s:close")],
    ])


# ══════════════════════════════════════════════════════════════════════════════
#  FILE DOWNLOAD — Telethon download_media (no size limit)
# ══════════════════════════════════════════════════════════════════════════════

async def resolve_file(uid: int, tmpdir: str) -> Optional[str]:
    state     = user_state.get(uid, {})
    tele_msg  = state.get("tele_msg")
    link_text = state.get("link_text")
    file_name = state.get("file_name", "video.mp4")
    dest      = os.path.join(tmpdir, file_name)

    if tele_msg is not None:
        try:
            # download_media on the Message object — Telethon already has the
            # full InputDocument reference, no peer resolution needed at all.
            result = await tele.download_media(tele_msg, file=dest)
            if result and os.path.exists(dest) and os.path.getsize(dest) > 0:
                logger.info(f"Downloaded via Telethon: {dest} ({os.path.getsize(dest)} bytes)")
                return dest
            logger.error("Telethon download_media returned empty")
        except Exception as e:
            logger.error(f"Telethon download_media error: {e}")
        return None

    if link_text:
        ltype, identifier = detect_link_type(link_text)
        if ltype == "direct":
            ok = await download_direct_link(identifier, dest)
            return dest if ok else None
        elif ltype == "gdrive_file":
            ok = await download_gdrive_file(identifier, dest)
            return dest if ok else None

    return None


# ══════════════════════════════════════════════════════════════════════════════
#  PROCESSING
# ══════════════════════════════════════════════════════════════════════════════

async def process_screenshots(uid: int, chat_id: int, count: int):
    user_rec = await db.get_user(uid)
    settings = user_rec["settings"]
    limits   = get_limits(user_rec["tier"])
    count    = min(count, limits["max_screenshots"])
    prog     = await bot.send_message(chat_id, f"⚙️ Generating {count} screenshots...")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = await resolve_file(uid, tmpdir)
        if not path:
            await prog.edit_text("❌ Could not download the file."); return
        images = await generate_screenshots(path, count, tmpdir, settings["scht_gen_mode"])
        if not images:
            await prog.edit_text("❌ Screenshot generation failed."); return
        await db.increment_stat(uid, "screenshots")
        if settings["upload_mode"] == "tile":
            tile = os.path.join(tmpdir, "tile.jpg")
            if await make_tile_image(images, tile):
                await bot.send_photo(chat_id, open(tile,"rb"), caption=f"📸 {count} screenshots")
            else:
                await prog.edit_text("❌ Tile generation failed."); return
        else:
            media = [InputMediaPhoto(open(p,"rb").read()) for p in images]
            await bot.send_media_group(chat_id, media)
    await prog.delete()


async def process_sample(uid: int, chat_id: int):
    user_rec = await db.get_user(uid)
    settings = user_rec["settings"]
    limits   = get_limits(user_rec["tier"])
    dur      = min(settings["sample_duration"], limits["max_sample_sec"])
    prog     = await bot.send_message(chat_id, f"⚙️ Generating {dur}s sample...")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = await resolve_file(uid, tmpdir)
        if not path:
            await prog.edit_text("❌ Could not download the file."); return
        out = os.path.join(tmpdir, "sample.mp4")
        if await generate_sample_video(path, dur, out):
            await bot.send_video(chat_id, open(out,"rb"), caption=f"🎞 Sample ({dur}s)")
            await db.increment_stat(uid, "samples")
        else:
            await prog.edit_text("❌ Sample generation failed."); return
    await prog.delete()


async def process_trim(uid: int, chat_id: int, start: str, end: str):
    prog = await bot.send_message(chat_id, "✂️ Trimming...")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = await resolve_file(uid, tmpdir)
        if not path:
            await prog.edit_text("❌ Could not download the file."); return
        out = os.path.join(tmpdir, "trimmed.mp4")
        if await trim_video(path, start, end, out):
            await bot.send_video(chat_id, open(out,"rb"), caption=f"✂️ {start} → {end}")
            await db.increment_stat(uid, "trims")
        else:
            await prog.edit_text("❌ Trim failed. Check time values."); return
    await prog.delete()


async def process_mediainfo(uid: int, chat_id: int):
    user_rec = await db.get_user(uid)
    settings = user_rec["settings"]
    prog     = await bot.send_message(chat_id, "📋 Reading media info...")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = await resolve_file(uid, tmpdir)
        if not path:
            await prog.edit_text("❌ Could not download the file."); return
        if settings["mediainfo_mode"] == "simple":
            info = parse_simple_mediainfo(path)
            await bot.send_message(chat_id, info, parse_mode=ParseMode.MARKDOWN)
        else:
            full = parse_detailed_mediainfo(path)
            url  = await upload_to_telegraph("Media Info", full, config.TELEGRAPH_TOKEN)
            if url:
                await bot.send_message(chat_id,
                    f"📊 **Detailed Media Info**\n\n[View on Telegraph]({url})",
                    parse_mode=ParseMode.MARKDOWN)
            else:
                await bot.send_message(chat_id,
                    f"```\n{full[:3900]}\n```", parse_mode=ParseMode.MARKDOWN)
        await db.increment_stat(uid, "mediainfos")
    await prog.delete()


async def process_thumb(uid: int, chat_id: int):
    prog = await bot.send_message(chat_id, "🖼 Extracting thumbnail...")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = await resolve_file(uid, tmpdir)
        if not path:
            await prog.edit_text("❌ Could not download the file."); return
        out = os.path.join(tmpdir, "thumb.jpg")
        if await extract_thumbnail(path, out):
            await bot.send_photo(chat_id, open(out,"rb"), caption="🖼 Thumbnail")
            await db.increment_stat(uid, "thumbs")
        else:
            await prog.edit_text("❌ Thumbnail extraction failed."); return
    await prog.delete()


# ══════════════════════════════════════════════════════════════════════════════
#  TELETHON HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def register_handlers(client: TelegramClient):

    @client.on(events.NewMessage(pattern=r"^/start"))
    async def on_start(event):
        uid     = event.sender_id
        chat_id = event.chat_id
        if not await check_group_access(chat_id):
            await bot.send_message(chat_id, "⛔ Bot not enabled here. Admin must run /verify.")
            return
        if event.is_private and not await check_fsub(uid):
            ch = await bot.get_chat(config.FSUB_CHANNEL_ID)
            uname = getattr(ch, "username", "") or ""
            url   = f"https://t.me/{uname}" if uname else f"https://t.me/c/{str(config.FSUB_CHANNEL_ID)[4:]}/1"
            await bot.send_message(uid, "🔒 *Access Required!*\n\nPlease join our channel first.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=url)]]))
            return
        await db.get_user(uid)
        await db.add_broadcast_user(uid)
        await db.touch_user(uid)
        sender = await event.get_sender()
        name   = getattr(sender, "first_name", "there")
        await bot.send_message(chat_id,
            config.START_TEXT.format(name=name), parse_mode=ParseMode.MARKDOWN)

    @client.on(events.NewMessage(pattern=r"^/help"))
    async def on_help(event):
        await bot.send_message(event.chat_id, config.HELP_TEXT, parse_mode=ParseMode.MARKDOWN)

    @client.on(events.NewMessage(pattern=r"^/privacy"))
    async def on_privacy(event):
        await bot.send_message(event.chat_id, config.PRIVACY_TEXT, parse_mode=ParseMode.MARKDOWN)

    @client.on(events.NewMessage(pattern=r"^/donate"))
    async def on_donate(event):
        await bot.send_message(event.chat_id, config.DONATE_TEXT,
            parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

    @client.on(events.NewMessage(pattern=r"^/settings"))
    async def on_settings(event):
        s = await db.get_user_settings(event.sender_id)
        await bot.send_message(event.chat_id,
            "⚙️ *Settings* — tap a button to toggle.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=settings_keyboard(s))

    @client.on(events.CallbackQuery(pattern=b"^s:"))
    async def on_settings_cb(event):
        uid    = event.sender_id
        action = event.data.decode().split(":")[1]
        await event.answer()
        if action == "close":
            await event.delete(); return
        if action == "noop": return
        s = await db.get_user_settings(uid)
        if   action == "toggle_upload":  await db.update_user_setting(uid, "upload_mode",    "individual" if s["upload_mode"]    == "tile"   else "tile")
        elif action == "cycle_sample":
            opts = config.SAMPLE_DURATION_OPTIONS
            idx  = opts.index(s["sample_duration"]) if s["sample_duration"] in opts else 0
            await db.update_user_setting(uid, "sample_duration", opts[(idx+1) % len(opts)])
        elif action == "toggle_mi":      await db.update_user_setting(uid, "mediainfo_mode", "detailed" if s["mediainfo_mode"] == "simple" else "simple")
        elif action == "toggle_wm_vid":  await db.update_user_setting(uid, "watermark_video",  not s["watermark_video"])
        elif action == "toggle_wm_photo":await db.update_user_setting(uid, "watermark_photo",  not s["watermark_photo"])
        s = await db.get_user_settings(uid)
        try:
            await bot.send_message(event.chat_id,
                "⚙️ *Settings* — tap a button to toggle.",
                parse_mode=ParseMode.MARKDOWN, reply_markup=settings_keyboard(s))
        except Exception: pass

    # Media received — store Telethon Message object directly
    @client.on(events.NewMessage(func=lambda e: bool(e.media) and not e.text.startswith("/")))
    async def on_media(event):
        uid     = event.sender_id
        chat_id = event.chat_id
        if not await check_group_access(chat_id): return
        msg       = event.message
        file_name = "video.mp4"
        if msg.document:
            for attr in msg.document.attributes:
                fn = getattr(attr, "file_name", None)
                if fn: file_name = fn; break
        caption = msg.message or ""
        if is_adult_content(file_name, caption):
            await bot.send_message(chat_id,
                "🚫 *Adult content detected!* Processing prohibited. Incident reported. 🔴",
                parse_mode=ParseMode.MARKDOWN)
            try:
                sender  = await event.get_sender()
                mention = f"[{getattr(sender,'first_name','User')}](tg://user?id={uid})"
                await bot.send_message(config.SUPPORT_GROUP_ID,
                    f"🔞 *18+ Alert!*\n👤 {mention}\n🆔 `{uid}`\n📁 `{file_name}`",
                    parse_mode=ParseMode.MARKDOWN)
            except Exception: pass
            return
        # KEY: store the Telethon Message object — download_media needs this exact object
        user_state[uid] = {"tele_msg": msg, "file_name": file_name, "link_text": None, "step": None}
        await db.touch_user(uid)
        await bot.send_message(chat_id,
            f"✅ *File received!*\n`{file_name}`\n\nChoose what to do:",
            parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

    # Text / link / awaiting input
    @client.on(events.NewMessage(func=lambda e: not e.media and not e.text.startswith("/")))
    async def on_text(event):
        uid     = event.sender_id
        chat_id = event.chat_id
        text    = event.raw_text.strip()
        state   = user_state.get(uid, {})

        if state.get("step") == "trim":
            start, end = parse_time_range(text)
            if not start or not end:
                await bot.send_message(chat_id,
                    "❌ Invalid format. Use `HH:MM:SS - HH:MM:SS`", parse_mode=ParseMode.MARKDOWN)
                return
            user_state[uid]["step"] = None
            asyncio.create_task(process_trim(uid, chat_id, start, end))
            return

        if text.startswith("http"):
            ltype, identifier = detect_link_type(text)
            if ltype == "unsupported":
                await bot.send_message(chat_id,
                    "🚫 *Unsupported link*\n\n✅ Direct video links & Google Drive only.",
                    parse_mode=ParseMode.MARKDOWN)
                return
            if ltype == "none": return
            file_name = text.split("/")[-1].split("?")[0] or "video"
            if is_adult_content(file_name, text):
                await bot.send_message(chat_id, "🚫 *Adult content detected!*", parse_mode=ParseMode.MARKDOWN)
                return
            user_state[uid] = {"tele_msg": None, "link_text": text, "file_name": file_name, "step": None}
            await bot.send_message(chat_id,
                f"✅ *Link received!*\n`{file_name}`\n\nChoose what to do:",
                parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

    # Action callbacks
    @client.on(events.CallbackQuery(pattern=b"^(scht:|action:)"))
    async def on_action_cb(event):
        uid     = event.sender_id
        chat_id = event.chat_id
        data    = event.data.decode()
        await event.answer()
        state = user_state.get(uid, {})
        if not state.get("tele_msg") and not state.get("link_text"):
            await bot.send_message(chat_id, "⚠️ No file loaded. Please send a file or link first.")
            return
        if   data.startswith("scht:"):          asyncio.create_task(process_screenshots(uid, chat_id, int(data.split(":")[1])))
        elif data == "action:trim":
            user_state[uid]["step"] = "trim"
            await bot.send_message(chat_id,
                "✂️ *Trim Video*\n\nSend time range:\n`HH:MM:SS - HH:MM:SS`",
                parse_mode=ParseMode.MARKDOWN)
        elif data == "action:sample":           asyncio.create_task(process_sample(uid, chat_id))
        elif data == "action:mediainfo":        asyncio.create_task(process_mediainfo(uid, chat_id))
        elif data in ("action:thumbs","action:covers"): asyncio.create_task(process_thumb(uid, chat_id))
        elif data == "action:manual_scht":
            user_state[uid]["step"] = "manual_scht"
            await bot.send_message(chat_id,
                "📸 *Manual Screenshots*\n\nSend timestamps:\n`HH:MM:SS, HH:MM:SS, ...`",
                parse_mode=ParseMode.MARKDOWN)

    # Admin commands
    @client.on(events.NewMessage(pattern=r"^/promote"))
    async def on_promote(event):
        if not is_admin(event.sender_id): return
        parts = event.raw_text.split()
        if len(parts) < 2:
            await bot.send_message(event.chat_id, "Usage: `/promote {user_id}`", parse_mode=ParseMode.MARKDOWN); return
        uid = int(parts[1])
        await db.promote_user(uid)
        try:
            await bot.send_message(uid,
                "🎉 *Promoted to Donor tier!*\n\n🔓 Screenshots: 50 | Sample: 5min | Trim: 20min\n\nThank you! ❤️",
                parse_mode=ParseMode.MARKDOWN)
        except Exception: pass
        await bot.send_message(event.chat_id, f"✅ User `{uid}` promoted.", parse_mode=ParseMode.MARKDOWN)

    @client.on(events.NewMessage(pattern=r"^/demote"))
    async def on_demote(event):
        if not is_admin(event.sender_id): return
        parts = event.raw_text.split()
        if len(parts) < 2: return
        await db.demote_user(int(parts[1]))
        await bot.send_message(event.chat_id, f"✅ User `{parts[1]}` demoted.", parse_mode=ParseMode.MARKDOWN)

    @client.on(events.NewMessage(pattern=r"^/verify"))
    async def on_verify(event):
        if not is_admin(event.sender_id): return
        if event.is_private:
            await bot.send_message(event.chat_id, "❌ Run this inside a group."); return
        chat = await event.get_chat()
        await db.verify_group(event.chat_id, event.sender_id)
        await bot.send_message(event.chat_id, f"✅ *{chat.title}* verified.", parse_mode=ParseMode.MARKDOWN)

    @client.on(events.NewMessage(pattern=r"^/unverify"))
    async def on_unverify(event):
        if not is_admin(event.sender_id): return
        await db.unverify_group(event.chat_id)
        await bot.send_message(event.chat_id, "✅ Group unverified.")

    @client.on(events.NewMessage(pattern=r"^/broadcast"))
    async def on_broadcast(event):
        if not is_admin(event.sender_id): return
        if not event.message.reply_to_msg_id:
            await bot.send_message(event.chat_id, "❌ Reply to a message to broadcast it."); return
        replied = await event.get_reply_message()
        ids     = await db.get_all_broadcast_ids()
        prog    = await bot.send_message(event.chat_id, f"📡 Broadcasting to {len(ids)} users...")
        sent = failed = 0
        for uid in ids:
            try:
                await tele.forward_messages(uid, replied)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        await prog.edit_text(f"✅ Done! Sent: {sent} | Failed: {failed}")

    # FSub join requests via Telethon Raw updates
    @client.on(events.Raw(tl_types.UpdateBotChatInviteRequester))
    async def on_join_request(update):
        try:
            user_id    = update.user_id
            channel_id = update.peer.channel_id if hasattr(update.peer, "channel_id") else None
            fsub_id    = int(str(config.FSUB_CHANNEL_ID).replace("-100", ""))
            if channel_id != fsub_id:
                return
            await tele(tl_types.channels.HideInviteRequest(
                channel=await tele.get_input_entity(config.FSUB_CHANNEL_ID),
                user_id=await tele.get_input_entity(user_id),
                approved=True,
            ))
            sender = await tele.get_entity(user_id)
            name   = getattr(sender, "first_name", "there")
            await bot.send_message(user_id,
                config.START_TEXT.format(name=name), parse_mode=ParseMode.MARKDOWN)
            logger.info(f"FSub approved: {user_id}")
        except Exception as e:
            logger.error(f"join_request handler error: {e}")

    @client.on(events.NewMessage(pattern=r"^/stats"))
    async def on_stats(event):
        if not is_admin(event.sender_id): return
        daily  = await db.get_stats_range(1)
        weekly = await db.get_stats_range(7)
        monthly= await db.get_stats_range(30)
        total  = await db.get_user_count()
        ad,aw,am = await db.get_active_users(1), await db.get_active_users(7), await db.get_active_users(30)
        def row(d): return f"  📸 {d['screenshots']}  ✂️ {d['trims']}  🎞 {d['samples']}  📋 {d['mediainfos']}  🖼 {d['thumbs']}  📦 {d['total']}"
        await bot.send_message(event.chat_id,
            f"📊 *Stats*\n\n👥 Total: {total} | Today: {ad} | Week: {aw} | Month: {am}\n\n"
            f"📅 Daily\n{row(daily)}\n\n📅 Weekly\n{row(weekly)}\n\n📅 Monthly\n{row(monthly)}",
            parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    global tele, bot

    bot = Bot(token=config.BOT_TOKEN)
    await bot.initialize()
    logger.info("PTB Bot initialized (send-only) ✅")

    tele = TelegramClient(
        StringSession(config.TELETHON_SESSION),
        config.API_ID,
        config.API_HASH
    )
    register_handlers(tele)
    await tele.start(bot_token=config.BOT_TOKEN)
    logger.info("Telethon receiving ALL updates ✅")

    # Confirm bot is alive by messaging the first admin
    try:
        await bot.send_message(config.ADMIN_IDS[0], "🟢 Bot started & receiving updates via Telethon MTProto.")
    except Exception as e:
        logger.warning(f"Startup ping failed: {e}")

    logger.info("Bot fully started ✅")
    await tele.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
