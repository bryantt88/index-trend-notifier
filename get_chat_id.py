"""
Find the chat id of a Telegram group so you can put it in TELEGRAM_CHAT_IDS.

Steps:
  1. Add your bot to the group.
  2. In the group, send any command so the bot receives an update despite group
     privacy mode, e.g.:   /id@your_bot_username
     (Adding the bot also emits a 'my_chat_member' update, which this reads too.)
  3. Run:  python get_chat_id.py
     Copy the negative id it prints for your group into TELEGRAM_CHAT_IDS.

Read-only: it only calls getUpdates, never sends anything.
"""

import requests
import config as cfg

URL = f"https://api.telegram.org/bot{cfg.BOT_TOKEN}/getUpdates"


def main():
    if not cfg.BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN is not set (see .env.example).")
        return
    try:
        r = requests.get(URL, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"getUpdates failed: {e}")
        return

    if not data.get("ok"):
        print("Telegram returned an error:", data)
        return

    chats = {}
    for u in data.get("result", []):
        for key in ("message", "my_chat_member", "channel_post"):
            if key in u and "chat" in u[key]:
                c = u[key]["chat"]
                chats[c["id"]] = c

    if not chats:
        print("No chats seen yet. Add the bot to the group and send "
              "'/id@your_bot_username' in it, then re-run.")
        return

    print("Chats the bot can see:\n")
    for cid, c in chats.items():
        kind = c.get("type", "?")
        label = c.get("title") or c.get("username") or c.get("first_name") or ""
        marker = "  <- group/supergroup (use this)" if str(cid).startswith("-") else ""
        print(f"  {cid:<16} {kind:<12} {label}{marker}")
    print("\nPut the negative group id into TELEGRAM_CHAT_IDS.")


if __name__ == "__main__":
    main()
