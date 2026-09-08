"""Flask webhook for the Meta WhatsApp Cloud API.

GET  /webhook - Meta verification handshake (uses META_VERIFY_TOKEN)
POST /webhook - incoming WhatsApp messages
GET  /        - health check
"""
import os

from dotenv import load_dotenv
from flask import Flask, request

import agent
import whatsapp

load_dotenv()
app = Flask(__name__)


@app.get("/")
def health():
    return "booking agent is up\n"


@app.get("/webhook")
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == os.environ.get("META_VERIFY_TOKEN", ""):
        return challenge, 200
    return "forbidden", 403


@app.post("/webhook")
def incoming():
    data = request.get_json(silent=True) or {}
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for message in value.get("messages", []):
                    if message.get("type") != "text":
                        continue
                    sender = message["from"]
                    text = message["text"]["body"]
                    answer = agent.reply(sender, text)
                    whatsapp.send_text(sender, answer)
    except Exception as exc:  # never fail the webhook - Meta retries on non-200
        print(f"error handling message: {exc}")
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
