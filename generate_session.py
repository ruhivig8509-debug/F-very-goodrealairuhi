"""
Run this script LOCALLY (not on Render) to generate a Telethon session string.
You need to provide your phone number and the OTP sent by Telegram.
Save the output session string as the SESSION_STRING environment variable.
"""

from telethon.sessions import StringSession
from telethon.sync import TelegramClient

print("=" * 50)
print("  Telethon Session String Generator")
print("=" * 50)

API_ID = int(input("Enter your API_ID: "))
API_HASH = input("Enter your API_HASH: ")

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    session_string = client.session.save()
    print("\n" + "=" * 50)
    print("YOUR SESSION STRING (save this securely):")
    print("=" * 50)
    print(session_string)
    print("=" * 50)
    print("\nSet this as the SESSION_STRING environment variable on Render.")
    print("WARNING: Anyone with this string can access your Telegram account!")
