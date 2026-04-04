"""
╔══════════════════════════════════════════════════════════════╗
║            SCREENSHOT GENERATOR BOT — bot.py                ║
║  python-telegram-bot 20+  |  async  |  Motor MongoDB        ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, asyncio, tempfile, logging
from typing import Optional
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, ChatJoinRequest
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ChatJoinRequestHandler, ConversationHandler, filters, ContextTypes
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

import config
import database as db
from media_utils import (
    is_adult_content, detect_link_type, download_direct_link, download_gdrive_file,
    generate_screenshots, make_tile_image, trim_video, parse_time_range,
    generate_sample_video, extract_thumbnail,
    parse_simple_mediainfo, parse_detailed_mediainfo, upload_to_telegraph
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Conversation states ─────────────────────────────────────────────────────
AWAITING_TRIM_TIME   = 1
AWAITING_SCHT_COUNT  = 2

# ─── Helpers ─────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def check_fsub(bot, user_id: int) -> bool:
    """Returns True if user is a member of the force-sub channel."""
    try:
        member = await bot.get_chat_member(config.FSUB_CHANNEL_ID, user_id)
        return member.status not in ("left", "kicked", "banned")
    except Exception:
        return False


async def check_group_access(chat_id: int) -> bool:
    """Private chats always allowed. Groups need verification."""
    if chat_id > 0:
        return True
    return await db.is_group_verified(chat_id)


def get_user_limits(tier: str) -> dict:
    if tier == "promoted":
        return {
            "max_screenshots": config.PROMOTED_MAX_SCREENSHOTS,
            "max_trim_min":    config.PROMOTED_MAX_TRIM_MIN,
            "max_sample_sec":  config.PROMOTED_MAX_SAMPLE_SEC,
        }
    return {
        "max_screenshots": config.NORMAL_MAX_SCREENSHOTS,
        "max_trim_min":    config.NORMAL_MAX_TRIM_MIN,
        "max_sample_sec":  config.NORMAL_MAX_SAMPLE_SEC,
    }


def settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    upload_label = "🖼 As Tile Image" if settings["upload_mode"] == "tile" else "📤 Individual Photos"
    mediainfo_label = "📋 Simple" if settings["mediainfo_mode"] == "simple" else "📊 Detailed"
    wm_video = "✅ On" if settings["watermark_video"] else "❌ Off"
    wm_photo = "✅ On" if settings["watermark_photo"] else "❌ Off"
    sample   = f"⏱ {settings['sample_duration']}s"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload Mode", callback_data="s:noop"),
         InlineKeyboardButton(upload_label, callback_data="s:toggle_upload_mode")],
        [InlineKeyboardButton("🎞 Sample Duration", callback_data="s:noop"),
         InlineKeyboardButton(sample, callback_data="s:cycle_sample_dur")],
        [InlineKeyboardButton("📋 MediaInfo Mode", callback_data="s:noop"),
         InlineKeyboardButton(mediainfo_label, callback_data="s:toggle_mediainfo")],
        [InlineKeyboardButton("🎬 Watermark on Video", callback_data="s:noop"),
         InlineKeyboardButton(wm_video, callback_data="s:toggle_wm_video")],
        [InlineKeyboardButton("📸 Watermark on Photos", callback_data="s:noop"),
         InlineKeyboardButton(wm_photo, callback_data="s:toggle_wm_photo")],
        [InlineKeyboardButton("❌ Close", callback_data="s:close")],
    ])


def main_action_keyboard() -> InlineKeyboardMarkup:
    nums = [[InlineKeyboardButton(str(n), callback_data=f"scht:{n}") for n in row]
            for row in [[2,3],[4,5],[6,7],[8,9],[10]]]
    nums.append([InlineKeyboardButton("➡️ More", callback_data="scht:more")])
    extra = [
        [InlineKeyboardButton("📸 Manual Screenshots!", callback_data="action:manual_scht")],
        [InlineKeyboardButton("✂️ Trim Video!", callback_data="action:trim")],
        [InlineKeyboardButton("🎞 Generate Sample Video!", callback_data="action:sample")],
        [InlineKeyboardButton("📋 Get Media Information", callback_data="action:mediainfo")],
        [InlineKeyboardButton("🖼 Get Thumbs", callback_data="action:thumbs"),
         InlineKeyboardButton("🎨 Get Covers", callback_data="action:covers")],
    ]
    return InlineKeyboardMarkup(nums + extra)


# ══════════════════════════════════════════════════════════════════════════════
#  FSUB — ChatJoinRequest handler
# ══════════════════════════════════════════════════════════════════════════════

async def handle_join_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Auto-approve join requests to the fsub channel, delete the join-req message, send start."""
    req = update.chat_join_request
    if req.chat.id != config.FSUB_CHANNEL_ID:
        return
    try:
        await ctx.bot.approve_chat_join_request(req.chat.id, req.from_user.id)
    except TelegramError:
        pass
    # Send welcome to user in PM
    user = req.from_user
    await db.get_user(user.id)  # ensure record exists
    await db.add_broadcast_user(user.id)
    try:
        await ctx.bot.send_message(
            user.id,
            config.START_TEXT.format(name=user.first_name),
            parse_mode=ParseMode.MARKDOWN
        )
    except TelegramError:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    # Group access check
    if not await check_group_access(chat.id):
        await update.message.reply_text(
            "⛔ This bot is not enabled in this group.\n"
            "An admin must run `/verify` to activate it here.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # FSub check (only in private)
    if chat.type == "private":
        if not await check_fsub(ctx.bot, user.id):
            await update.message.reply_text(
                "🔒 **Access Required!**\n\n"
                "Please join our channel to use this bot.\n"
                "After requesting to join, the bot will auto-approve & send you the welcome message! 🎉",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{(await ctx.bot.get_chat(config.FSUB_CHANNEL_ID)).username or 'channel'}")
                ]])
            )
            return

    await db.get_user(user.id)
    await db.add_broadcast_user(user.id)
    await db.touch_user(user.id)

    await update.message.reply_text(
        config.START_TEXT.format(name=user.first_name),
        parse_mode=ParseMode.MARKDOWN
    )


# ══════════════════════════════════════════════════════════════════════════════
#  /help  /privacy  /donate
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(config.HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def cmd_privacy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(config.PRIVACY_TEXT, parse_mode=ParseMode.MARKDOWN)


async def cmd_donate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(config.DONATE_TEXT, parse_mode=ParseMode.MARKDOWN,
                                    disable_web_page_preview=True)


# ══════════════════════════════════════════════════════════════════════════════
#  /settings
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    settings = await db.get_user_settings(user.id)
    await update.message.reply_text(
        "⚙️ **Settings** — Press a button to toggle.\n\n"
        "_Changes apply immediately._",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=settings_keyboard(settings)
    )


async def settings_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    action  = query.data.split(":")[1]

    if action == "close":
        await query.message.delete()
        return
    if action == "noop":
        return

    settings = await db.get_user_settings(user_id)

    if action == "toggle_upload_mode":
        new = "individual" if settings["upload_mode"] == "tile" else "tile"
        await db.update_user_setting(user_id, "upload_mode", new)

    elif action == "cycle_sample_dur":
        opts = config.SAMPLE_DURATION_OPTIONS
        cur  = settings["sample_duration"]
        idx  = opts.index(cur) if cur in opts else 0
        new  = opts[(idx + 1) % len(opts)]
        await db.update_user_setting(user_id, "sample_duration", new)

    elif action == "toggle_mediainfo":
        new = "detailed" if settings["mediainfo_mode"] == "simple" else "simple"
        await db.update_user_setting(user_id, "mediainfo_mode", new)

    elif action == "toggle_wm_video":
        await db.update_user_setting(user_id, "watermark_video", not settings["watermark_video"])

    elif action == "toggle_wm_photo":
        await db.update_user_setting(user_id, "watermark_photo", not settings["watermark_photo"])

    settings = await db.get_user_settings(user_id)
    try:
        await query.message.edit_reply_markup(reply_markup=settings_keyboard(settings))
    except TelegramError:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  FILE / LINK HANDLER — entry point for all media processing
# ══════════════════════════════════════════════════════════════════════════════

async def handle_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    message = update.message
    chat    = update.effective_chat

    if not await check_group_access(chat.id):
        return

    # Determine source: file or link
    file_id    = None
    file_name  = "unknown"
    link_text  = None

    if message.video or message.document:
        obj       = message.video or message.document
        file_id   = obj.file_id
        file_name = getattr(obj, "file_name", None) or "video.mp4"
    elif message.text:
        link_text = message.text.strip()
        file_name = link_text.split("/")[-1].split("?")[0]
    else:
        return

    caption = message.caption or ""

    # 18+ detection
    if is_adult_content(file_name, caption + " " + (link_text or "")):
        await message.reply_text(
            "🚫 **Adult content detected!**\n\n"
            "Processing of 18+ content is strictly prohibited.\n"
            "This incident has been reported. 🔴",
            parse_mode=ParseMode.MARKDOWN
        )
        try:
            await ctx.bot.send_message(
                config.SUPPORT_GROUP_ID,
                f"🔞 **18+ Content Alert!**\n\n"
                f"👤 User: {user.mention_markdown_v2()}\n"
                f"🆔 ID: `{user.id}`\n"
                f"📁 Filename: `{file_name}`\n"
                f"🔗 Link: `{link_text or 'N/A'}`",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except TelegramError:
            pass
        return

    # Validate link if provided
    if link_text:
        link_type, identifier = detect_link_type(link_text)
        if link_type == "unsupported":
            await message.reply_text(
                "🚫 **Unsupported Link Type**\n\n"
                "I can only process:\n"
                "✅ Direct video links (.mp4, .mkv, etc.)\n"
                "✅ Google Drive file/folder links\n\n"
                "YouTube, streaming sites and DRM-protected links are **not supported**. 😔",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        if link_type == "none":
            return

    # Store source info in user_data for callbacks
    ctx.user_data["file_id"]   = file_id
    ctx.user_data["link_text"] = link_text
    ctx.user_data["file_name"] = file_name

    await message.reply_text(
        f"✅ **File received!**\n`{file_name}`\n\n"
        "Choose what you'd like to do:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_action_keyboard()
    )


# ══════════════════════════════════════════════════════════════════════════════
#  ACTION CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

async def resolve_file(ctx: ContextTypes.DEFAULT_TYPE, tmpdir: str) -> Optional[str]:
    """Download/get the file into tmpdir. Returns path or None."""
    file_id   = ctx.user_data.get("file_id")
    link_text = ctx.user_data.get("link_text")
    file_name = ctx.user_data.get("file_name", "video.mp4")

    dest = os.path.join(tmpdir, file_name)

    if file_id:
        # get_file raises BadRequest for files >20 MB — use the file_path URL directly
        try:
            tg_file = await ctx.bot.get_file(file_id)
            file_url = tg_file.file_path  # full HTTPS URL, no size limit for download
            ok = await download_direct_link(file_url, dest)
            return dest if ok else None
        except Exception as e:
            logger.error(f"resolve_file telegram error: {e}")
            return None

    if link_text:
        link_type, identifier = detect_link_type(link_text)
        if link_type == "direct":
            ok = await download_direct_link(identifier, dest)
            return dest if ok else None
        elif link_type == "gdrive_file":
            ok = await download_gdrive_file(identifier, dest)
            return dest if ok else None

    return None


async def action_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    action  = query.data.split(":")[1]

    if action == "trim":
        await query.message.reply_text(
            "✂️ **Trim Video**\n\n"
            "Send the time range in this format:\n"
            "`HH:MM:SS - HH:MM:SS`\n\n"
            "Example: `00:01:30 - 00:03:45`",
            parse_mode=ParseMode.MARKDOWN
        )
        ctx.user_data["awaiting"] = "trim"
        return

    if action == "manual_scht":
        await query.message.reply_text(
            "📸 **Manual Screenshots**\n\n"
            "Send timestamps separated by commas:\n"
            "`HH:MM:SS, HH:MM:SS, ...`",
            parse_mode=ParseMode.MARKDOWN
        )
        ctx.user_data["awaiting"] = "manual_scht"
        return

    processing_msg = await query.message.reply_text("⚙️ Processing... please wait.")

    user_rec  = await db.get_user(user_id)
    settings  = user_rec["settings"]
    limits    = get_user_limits(user_rec["tier"])

    with tempfile.TemporaryDirectory() as tmpdir:
        path = await resolve_file(ctx, tmpdir)
        if not path:
            await processing_msg.edit_text("❌ Could not download the file. Please try again.")
            return

        # ── SCREENSHOTS ─────────────────────────────────────────────────────
        if action.startswith("scht:") or action == "scht":
            count_str = action.replace("scht:", "")
            try:
                count = int(count_str)
            except ValueError:
                count = 4
            count = min(count, limits["max_screenshots"])

            images = await generate_screenshots(path, count, tmpdir, settings["scht_gen_mode"])
            if not images:
                await processing_msg.edit_text("❌ Screenshot generation failed.")
                return

            await db.increment_stat(user_id, "screenshots")

            if settings["upload_mode"] == "tile":
                tile_path = os.path.join(tmpdir, "tile.jpg")
                ok = await make_tile_image(images, tile_path)
                if ok:
                    with open(tile_path, "rb") as f:
                        await query.message.reply_photo(photo=f, caption=f"📸 {count} screenshots")
                else:
                    await processing_msg.edit_text("❌ Tile generation failed.")
                    return
            else:
                media_group = []
                for img in images:
                    with open(img, "rb") as f:
                        media_group.append(InputMediaPhoto(media=f.read()))
                await query.message.reply_media_group(media=media_group)

            await processing_msg.delete()

        # ── SAMPLE VIDEO ────────────────────────────────────────────────────
        elif action == "sample":
            dur = min(settings["sample_duration"], limits["max_sample_sec"])
            out = os.path.join(tmpdir, "sample.mp4")
            ok  = await generate_sample_video(path, dur, out)
            if ok:
                with open(out, "rb") as f:
                    await query.message.reply_video(video=f, caption=f"🎞 Sample ({dur}s)")
                await db.increment_stat(user_id, "samples")
            else:
                await processing_msg.edit_text("❌ Sample generation failed.")
                return
            await processing_msg.delete()

        # ── MEDIA INFO ──────────────────────────────────────────────────────
        elif action == "mediainfo":
            if settings["mediainfo_mode"] == "simple":
                info = parse_simple_mediainfo(path)
                await query.message.reply_text(info, parse_mode=ParseMode.MARKDOWN)
            else:
                full = parse_detailed_mediainfo(path)
                url  = await upload_to_telegraph("Media Info", full, config.TELEGRAPH_TOKEN)
                if url:
                    await query.message.reply_text(
                        f"📊 **Detailed Media Info**\n\n[View on Telegraph]({url})",
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    await query.message.reply_text(f"```\n{full[:3000]}\n```", parse_mode=ParseMode.MARKDOWN)
            await db.increment_stat(user_id, "mediainfos")
            await processing_msg.delete()

        # ── THUMBNAIL ───────────────────────────────────────────────────────
        elif action in ("thumbs", "covers"):
            out = os.path.join(tmpdir, "thumb.jpg")
            ok  = await extract_thumbnail(path, out)
            if ok:
                with open(out, "rb") as f:
                    await query.message.reply_photo(photo=f, caption="🖼 Thumbnail")
                await db.increment_stat(user_id, "thumbs")
            else:
                await processing_msg.edit_text("❌ Thumbnail extraction failed.")
                return
            await processing_msg.delete()


# Separate handler for screenshot count buttons
async def scht_count_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, count_str = query.data.split(":")
    count = int(count_str)

    user_id     = query.from_user.id
    user_rec    = await db.get_user(user_id)
    settings    = user_rec["settings"]
    limits      = get_user_limits(user_rec["tier"])
    count       = min(count, limits["max_screenshots"])

    processing_msg = await query.message.reply_text("⚙️ Generating screenshots... please wait.")

    with tempfile.TemporaryDirectory() as tmpdir:
        path = await resolve_file(ctx, tmpdir)
        if not path:
            await processing_msg.edit_text("❌ Could not download the file. Please try again.")
            return

        images = await generate_screenshots(path, count, tmpdir, settings["scht_gen_mode"])
        if not images:
            await processing_msg.edit_text("❌ Screenshot generation failed.")
            return

        await db.increment_stat(user_id, "screenshots")

        if settings["upload_mode"] == "tile":
            tile_path = os.path.join(tmpdir, "tile.jpg")
            ok = await make_tile_image(images, tile_path)
            if ok:
                with open(tile_path, "rb") as f:
                    await query.message.reply_photo(photo=f, caption=f"📸 {count} screenshots")
            else:
                await processing_msg.edit_text("❌ Tile generation failed.")
                return
        else:
            media_group = []
            for img in images:
                with open(img, "rb") as f:
                    media_group.append(InputMediaPhoto(media=f.read()))
            await query.message.reply_media_group(media=media_group)

    await processing_msg.delete()


# ══════════════════════════════════════════════════════════════════════════════
#  AWAITING TEXT (trim times, manual timestamps)
# ══════════════════════════════════════════════════════════════════════════════

async def handle_awaiting_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    awaiting = ctx.user_data.get("awaiting")
    if not awaiting:
        return
    user_id = update.effective_user.id
    text    = update.message.text.strip()

    if awaiting == "trim":
        start, end = parse_time_range(text)
        if not start or not end:
            await update.message.reply_text("❌ Invalid format. Use `HH:MM:SS - HH:MM:SS`", parse_mode=ParseMode.MARKDOWN)
            return

        user_rec = await db.get_user(user_id)
        limits   = get_user_limits(user_rec["tier"])

        msg = await update.message.reply_text("✂️ Trimming video...")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = await resolve_file(ctx, tmpdir)
            if not path:
                await msg.edit_text("❌ Could not get the file.")
                return
            out = os.path.join(tmpdir, "trimmed.mp4")
            ok  = await trim_video(path, start, end, out)
            if ok:
                with open(out, "rb") as f:
                    await update.message.reply_video(video=f, caption=f"✂️ Trimmed: {start} → {end}")
                await db.increment_stat(user_id, "trims")
            else:
                await msg.edit_text("❌ Trim failed. Check time values.")
                return
        await msg.delete()
        ctx.user_data.pop("awaiting", None)


# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_promote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: `/promote {user_id}`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    await db.promote_user(uid)
    try:
        await ctx.bot.send_message(
            uid,
            "🎉 **Congratulations!** You've been promoted to **Donor tier**!\n\n"
            "🔓 **Your new limits:**\n"
            "• Screenshots: up to 50\n"
            "• Sample videos: up to 5 minutes\n"
            "• Trim: up to 20 minutes\n\n"
            "Thank you for supporting the bot! ❤️",
            parse_mode=ParseMode.MARKDOWN
        )
    except TelegramError:
        pass
    await update.message.reply_text(f"✅ User `{uid}` promoted to donor tier.", parse_mode=ParseMode.MARKDOWN)


async def cmd_demote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: `/demote {user_id}`", parse_mode=ParseMode.MARKDOWN)
        return
    uid = int(args[0])
    await db.demote_user(uid)
    await update.message.reply_text(f"✅ User `{uid}` demoted to normal tier.", parse_mode=ParseMode.MARKDOWN)


async def cmd_verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin: /verify — allows the current group to use the bot."""
    if not is_admin(update.effective_user.id):
        return
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text("❌ Run this command inside a group.")
        return
    await db.verify_group(chat.id, update.effective_user.id)
    await update.message.reply_text(f"✅ Group **{chat.title}** is now verified to use this bot.", parse_mode=ParseMode.MARKDOWN)


async def cmd_unverify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    chat = update.effective_chat
    await db.unverify_group(chat.id)
    await update.message.reply_text("✅ Group unverified.")


async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    # Broadcast the replied-to message
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Reply to a message to broadcast it.")
        return
    ids  = await db.get_all_broadcast_ids()
    sent = failed = 0
    prog = await update.message.reply_text(f"📡 Broadcasting to {len(ids)} users...")
    for uid in ids:
        try:
            await update.message.reply_to_message.forward(uid)
            sent += 1
        except TelegramError:
            failed += 1
        await asyncio.sleep(0.05)
    await prog.edit_text(f"✅ Broadcast done!\n• Sent: {sent}\n• Failed: {failed}")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    daily   = await db.get_stats_range(1)
    weekly  = await db.get_stats_range(7)
    monthly = await db.get_stats_range(30)
    total_users  = await db.get_user_count()
    active_day   = await db.get_active_users(1)
    active_week  = await db.get_active_users(7)
    active_month = await db.get_active_users(30)

    def row(label, d): return (
        f"  📸 Screenshots: {d['screenshots']}\n"
        f"  ✂️ Trims: {d['trims']}\n"
        f"  🎞 Samples: {d['samples']}\n"
        f"  📋 MediaInfo: {d['mediainfos']}\n"
        f"  🖼 Thumbs: {d['thumbs']}\n"
        f"  📦 Total ops: {d['total']}"
    )

    text = (
        f"📊 **Bot Statistics**\n\n"
        f"👥 **Users**\n"
        f"  Total: {total_users}\n"
        f"  Active today: {active_day}\n"
        f"  Active this week: {active_week}\n"
        f"  Active this month: {active_month}\n\n"
        f"📅 **Daily**\n{row('daily', daily)}\n\n"
        f"📅 **Weekly**\n{row('weekly', weekly)}\n\n"
        f"📅 **Monthly**\n{row('monthly', monthly)}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════════════════
#  APPLICATION SETUP
# ══════════════════════════════════════════════════════════════════════════════

def build_app() -> Application:
    app = Application.builder().token(config.BOT_TOKEN).build()

    # Public commands
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("privacy", cmd_privacy))
    app.add_handler(CommandHandler("donate",  cmd_donate))
    app.add_handler(CommandHandler("settings", cmd_settings))

    # Admin commands
    app.add_handler(CommandHandler("promote",    cmd_promote))
    app.add_handler(CommandHandler("demote",     cmd_demote))
    app.add_handler(CommandHandler("verify",     cmd_verify))
    app.add_handler(CommandHandler("unverify",   cmd_unverify))
    app.add_handler(CommandHandler("broadcast",  cmd_broadcast))
    app.add_handler(CommandHandler("stats",      cmd_stats))

    # Inline callbacks
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^s:"))
    app.add_handler(CallbackQueryHandler(action_callback,   pattern=r"^action:"))
    app.add_handler(CallbackQueryHandler(scht_count_callback, pattern=r"^scht:\d+$"))

    # FSub join requests
    app.add_handler(ChatJoinRequestHandler(handle_join_request))

    # Media & links
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.Document.VIDEO,
        handle_media
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_combined_text   # defined below
    ))

    return app


async def handle_combined_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Route text to either awaiting handler or link handler."""
    if ctx.user_data.get("awaiting"):
        await handle_awaiting_text(update, ctx)
    elif update.message.text and update.message.text.startswith("http"):
        await handle_media(update, ctx)


if __name__ == "__main__":
    app = build_app()
    logger.info("Starting Screenshot Generator Bot...")
    app.run_polling(drop_pending_updates=True)
