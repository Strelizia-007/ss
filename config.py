"""
╔══════════════════════════════════════════════╗
║         SCREENSHOT GENERATOR BOT             ║
║              Configuration                   ║
╚══════════════════════════════════════════════╝
"""

import os

# ─── BOT CREDENTIALS ───────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
API_ID    = int(os.environ.get("API_ID", "0"))          # Telegram API ID
API_HASH  = os.environ.get("API_HASH", "YOUR_API_HASH") # Telegram API Hash

# ─── ADMIN ─────────────────────────────────────────────────────────────────────
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "123456789").split(",")]

# ─── CHANNELS ──────────────────────────────────────────────────────────────────
FSUB_CHANNEL_ID   = int(os.environ.get("FSUB_CHANNEL_ID", "-100xxxxxxxxxx"))  # Private channel ID
SUPPORT_GROUP_ID  = int(os.environ.get("SUPPORT_GROUP_ID", "-100xxxxxxxxxx")) # Support group for 18+ alerts
LOG_CHANNEL_ID    = int(os.environ.get("LOG_CHANNEL_ID", "-100xxxxxxxxxx"))   # Bot log channel

# ─── DATABASE ──────────────────────────────────────────────────────────────────
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME   = "screenshotbot"

# ─── TELEGRAPH ─────────────────────────────────────────────────────────────────
TELEGRAPH_TOKEN = os.environ.get("TELEGRAPH_TOKEN", "")  # Leave blank to auto-create

# ─── LIMITS ────────────────────────────────────────────────────────────────────
# Normal users
NORMAL_MAX_SCREENSHOTS  = 10
NORMAL_MAX_TRIM_MIN     = 10        # minutes
NORMAL_MAX_SAMPLE_SEC   = 30        # seconds

# Promoted users (donors)
PROMOTED_MAX_SCREENSHOTS = 50
PROMOTED_MAX_TRIM_MIN    = 20
PROMOTED_MAX_SAMPLE_SEC  = 300      # 5 minutes

# ─── SAMPLE VIDEO OPTIONS ──────────────────────────────────────────────────────
SAMPLE_DURATION_OPTIONS = [30, 60, 120, 180, 300]   # seconds

# ─── GDRIVE ────────────────────────────────────────────────────────────────────
GDRIVE_FOLDER_ENABLED = True
GDRIVE_SERVICE_ACCOUNT_JSON = os.environ.get("GDRIVE_SA_JSON", "")

# ─── 18+ DETECTION ─────────────────────────────────────────────────────────────
ADULT_KEYWORDS = [
    "xxx", "porn", "sex", "nude", "naked", "adult", "18+", "nsfw",
    "hentai", "erotic", "xvideos", "pornhub", "xnxx", "redtube",
    "onlyfans", "brazzers", "blacked", "realitykings"
]

# ─── MESSAGES ──────────────────────────────────────────────────────────────────
START_TEXT = """
🎬 **Welcome to Screenshot Generator Bot!** 🎉

Hey {name}! 👋 I'm your all-in-one **media utility bot** — built to make your workflow effortless and powerful. ❤️

━━━━━━━━━━━━━━━━━━━━━━
**What I can do for you:**

📸 **Screenshot Generation** — Extract stunning frames from any video
✂️ **Trim Video** — Cut precise clips from your media
🎞️ **Sample Video** — Generate preview clips (30s–5min)
📋 **Media Info** — Detailed or simple tech specs at a glance
🖼️ **Thumbnails** — Extract cover images instantly
🔗 **Direct Links** — Supports HTTP, HTTPS & Google Drive links

━━━━━━━━━━━━━━━━━━━━━━
**Quick Start:**
Just send me a **video file** or a **direct link** and I'll do the rest! 🚀

Use /help to explore all commands.
Use /settings to configure your preferences.

━━━━━━━━━━━━━━━━━━━━━━
🌟 _Powered by STRELIZIA_ | /donate to support us 🙏
"""

HELP_TEXT = """
📖 **Bot Commands & Guide** 🎬

━━━━━━━━━━━━━━━━━━━━━━
**🔧 Core Commands**
/start — Start the bot & view welcome message
/help — Show this help guide
/settings — Configure bot behaviour
/privacy — Privacy policy & data info
/donate — Support the bot ❤️

━━━━━━━━━━━━━━━━━━━━━━
**📸 How to Generate Screenshots**
1. Send a video file **or** paste a direct link
2. Choose number of screenshots (2–10)
3. Bot processes & sends them as tile image or individual photos

━━━━━━━━━━━━━━━━━━━━━━
**✂️ Trim Video**
Send file/link → tap **Trim Video!**
Enter start & end time (e.g. `00:01:30 - 00:03:45`)

━━━━━━━━━━━━━━━━━━━━━━
**🎞️ Sample Video**
Send file → tap **Generate Sample Video!**
Duration is set in /settings

━━━━━━━━━━━━━━━━━━━━━━
**📋 Media Info**
Send file → tap **Get Media Information**
Configure Simple/Detailed in /settings

━━━━━━━━━━━━━━━━━━━━━━
**🔗 Supported Links**
✅ Direct HTTP/HTTPS links
✅ Google Drive files & folders
❌ YouTube, Streaming sites — not supported

━━━━━━━━━━━━━━━━━━━━━━
**📊 Your Limits (Normal)**
• Screenshots: 10 per file
• Trim: up to 10 minutes
• Sample: up to 30 seconds

_Donate to unlock higher limits!_ /donate
"""

PRIVACY_TEXT = """
🔒 **Privacy Policy** — Screenshot Generator Bot

━━━━━━━━━━━━━━━━━━━━━━
**What data we collect:**
• Your Telegram User ID (for settings & limits)
• Usage statistics (anonymous, aggregated only)
• Files you send are processed in memory and **never stored permanently**

**What we do NOT do:**
• We never share your data with third parties
• We never store your media files after processing
• We never log your file contents

**File Processing:**
All files are processed on-the-fly using FFmpeg. Temporary files are deleted immediately after sending results back to you.

**Your Rights:**
You can request deletion of your data at any time by contacting support.

━━━━━━━━━━━━━━━━━━━━━━
_Bot by STRELIZIA | Last updated: 2025_
"""

DONATE_TEXT = """
💝 **Support Screenshot Generator Bot** 🙏

This bot is **free to use**, but keeping the servers running costs money. Every contribution helps! ❤️

━━━━━━━━━━━━━━━━━━━━━━
**💰 Donation Options**
• UPI / GPay / PhonePe — scan QR below
• PayPal — [Click here](https://paypal.me/yourlink)
• Crypto — contact admin

━━━━━━━━━━━━━━━━━━━━━━
**🎁 Donor Benefits (₹20+)**
After donating, send payment screenshot to admin:

🔓 **Unlocked for you:**
• Screenshots: up to **50 per file**
• Sample videos: up to **5 minutes**
• Trim videos: up to **20 minutes**
• Priority processing queue

━━━━━━━━━━━━━━━━━━━━━━
Even ₹10, ₹20, ₹50 makes a difference 🙌
_Thanks a ton — every bit helps keep this going!_ 🎉
"""
