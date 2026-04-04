"""
Run this ONCE to generate a Telethon StringSession for your bot.
Copy the printed session string into your TELETHON_SESSION env var.

Usage:
    python gen_session.py
"""

import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID   = int(input("Enter API_ID: "))
API_HASH = input("Enter API_HASH: ").strip()
BOT_TOKEN = input("Enter BOT_TOKEN: ").strip()

async def main():
    async with TelegramClient(StringSession(), API_ID, API_HASH) as client:
        await client.start(bot_token=BOT_TOKEN)
        session_str = client.session.save()
        print("\n✅ Your TELETHON_SESSION string:\n")
        print(session_str)
        print("\nSet this as the TELETHON_SESSION environment variable.\n")

asyncio.run(main())
