import os
import asyncio
import threading
import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from telethon import TelegramClient, events

# Load credentials from .env file located next to this script
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

# Telethon client; the login session is saved as sophie.session (git-ignored)
client = TelegramClient("sophie", API_ID, API_HASH)

# Flask app
app = Flask(__name__)

# Stores active conversation usernames and their message history
conversations = {}

# Holds a reference to the running asyncio event loop (set after client starts)
loop = None


def send_message_sync(telegram: str, message: str):
    """Send a Telegram message from a Flask route using the Telethon event loop."""
    future = asyncio.run_coroutine_threadsafe(
        client.send_message(telegram, message),
        loop
    )
    return future.result(timeout=15)


# -- Flask routes -------------------------------------------------------------

@app.route("/send", methods=["POST"])
def send():
    """Send a message to a Telegram user by username."""
    data = request.get_json(force=True)
    telegram = data.get("telegram")
    message = data.get("message")

    if not telegram or not message:
        return jsonify({"error": "Both 'telegram' and 'message' are required"}), 400

    try:
        send_message_sync(telegram, message)
        return jsonify({"status": "sent", "telegram": telegram}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/start-conversation", methods=["POST"])
def start_conversation():
    """Start a conversation with a user -- stores username and sends opening message."""
    data = request.get_json(force=True)
    telegram = data.get("telegram")
    message = data.get("message")

    if not telegram or not message:
        return jsonify({"error": "Both 'telegram' and 'message' are required"}), 400

    try:
        # Register the username in the conversations dict
        if telegram not in conversations:
            conversations[telegram] = []

        send_message_sync(telegram, message)
        conversations[telegram].append({"role": "assistant", "text": message})

        return jsonify({"status": "conversation started", "telegram": telegram}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -- Telethon event handler ---------------------------------------------------

@client.on(events.NewMessage(incoming=True))
async def handle_reply(event):
    """Forward incoming private Telegram messages to the n8n webhook with chat history."""
    try:
        # Only respond to one-on-one private chats. Ignore groups and channels
        # so Sophie doesn't react to messages from the many groups this account is in.
        if not event.is_private:
            return

        sender = await event.get_sender()
        username = sender.username if sender.username else str(sender.id)
        message_text = event.raw_text

        # Visible log so every caught message is confirmed in the console
        print(f"[Sophie] Incoming from @{username}: {message_text!r}")

        # Fetch the last 20 messages from this chat to build history
        raw_messages = await client.get_messages(sender, limit=20)

        # get_messages returns newest-first; reverse to oldest-first
        history = []
        for msg in reversed(raw_messages):
            if not msg.text:
                continue
            role = "lead" if msg.out is False else "assistant"
            history.append({"role": role, "message": msg.text})

        payload = {
            "telegram": username,
            "latest_message": message_text,
            "history": history,
        }

        try:
            requests.post(
                "http://localhost:5678/webhook/reply",
                json=payload,
                timeout=10
            )
        except Exception as e:
            print(f"[Sophie] Failed to forward reply to n8n: {e}")

    except Exception as e:
        print(f"[Sophie] Error in handle_reply: {e}")


# -- Entry point --------------------------------------------------------------

async def main():
    global loop
    loop = asyncio.get_running_loop()

    await client.start()
    me = await client.get_me()
    print(f"[Sophie] Telegram client connected as @{me.username} (id {me.id}).")

    # Run Flask in a background thread so it doesn't block the Telethon loop
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=3001, use_reloader=False),
        daemon=True
    )
    flask_thread.start()
    print("[Sophie] Flask server running on port 3001.")

    # Keep the Telethon client running indefinitely
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
