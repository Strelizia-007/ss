# 🎬 Screenshot Generator Bot

A full-featured Telegram media utility bot — screenshots, trim, sample video, thumbnails, media info, admin tools, FSub, and more.

---

## 🚀 Setup

### 1. System Dependencies
```bash
sudo apt update
sudo apt install ffmpeg mediainfo -y
```

### 2. Python Dependencies
```bash
pip install -r requirements.txt
```

### 3. Environment Variables
Set these in your shell or a `.env` file:

| Variable              | Description                                  |
|-----------------------|----------------------------------------------|
| `BOT_TOKEN`           | Your BotFather token                         |
| `API_ID`              | Telegram API ID (from my.telegram.org)       |
| `API_HASH`            | Telegram API Hash                            |
| `ADMIN_IDS`           | Comma-separated admin user IDs               |
| `FSUB_CHANNEL_ID`     | Private channel ID for force-subscribe       |
| `SUPPORT_GROUP_ID`    | Group ID where 18+ alerts are sent           |
| `LOG_CHANNEL_ID`      | Optional log channel                         |
| `MONGO_URI`           | MongoDB connection string                    |
| `TELEGRAPH_TOKEN`     | Telegraph access token (auto-created if blank)|

### 4. Run
```bash
python bot.py
```

---

## 📋 Commands

### User Commands
| Command     | Description                          |
|-------------|--------------------------------------|
| `/start`    | Welcome message                      |
| `/help`     | Full command guide                   |
| `/settings` | Configure bot behaviour              |
| `/privacy`  | Privacy policy                       |
| `/donate`   | Support the bot + unlock higher limits|

### Admin Commands
| Command                   | Description                                        |
|---------------------------|----------------------------------------------------|
| `/promote {user_id}`      | Promote user to donor tier (unlock higher limits)  |
| `/demote {user_id}`       | Revert user to normal tier                         |
| `/verify`                 | Run in a group to allow bot usage there            |
| `/unverify`               | Disable bot in a group                             |
| `/broadcast`              | Reply to a message to forward it to all users      |
| `/stats`                  | View daily/weekly/monthly usage statistics         |

---

## ⚙️ Settings (via /settings)

| Setting              | Options                          |
|----------------------|----------------------------------|
| Upload Mode          | Tile Image / Individual Photos   |
| Sample Duration      | 30s / 60s / 120s / 180s / 300s  |
| MediaInfo Mode       | Simple / Detailed (→ Telegraph)  |
| Watermark on Video   | On / Off                         |
| Watermark on Photos  | On / Off                         |

---

## 🔗 Supported Links
- ✅ Direct HTTP/HTTPS video links (`.mp4`, `.mkv`, `.avi`, `.webm`, etc.)
- ✅ Google Drive file links
- ✅ Google Drive folder links
- ❌ YouTube, streaming sites, DRM content

---

## 🛡️ Features
- **FSub**: Auto-approves join requests and sends welcome message
- **18+ Detection**: Keyword-based, alerts support group with user details
- **Group Verification**: Bot works in groups only if admin runs `/verify`
- **Donor Tiers**: Normal vs Promoted limits
- **MediaInfo**: Simple inline or detailed on Telegraph
- **Stats**: Per-user and global daily/weekly/monthly counters

---

## 📦 File Structure
```
screenshotbot/
├── bot.py          — Main bot, all handlers
├── config.py       — Configuration & message templates
├── database.py     — MongoDB via Motor
├── media_utils.py  — FFmpeg, MediaInfo, GDrive, 18+ detection
├── requirements.txt
└── README.md
```
