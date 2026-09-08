"""Flask webhook for incoming WhatsApp messages.

Works with both providers, detected automatically per request:
- Twilio WhatsApp Sandbox (form-encoded POST to /webhook)
- Meta WhatsApp Cloud API (GET verification handshake + JSON POST to /webhook)

GET  /        - health check
GET  /webhook - Meta verification handshake (uses META_VERIFY_TOKEN)
POST /webhook - incoming WhatsApp messages (Twilio or Meta)
"""
import os
import threading

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
    # Meta's verification handshake. Twilio never calls GET.
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == os.environ.get("META_VERIFY_TOKEN", ""):
        return challenge, 200
    return "forbidden", 403


def _answer_and_send(sender, text):
    try:
        whatsapp.send_text(sender, agent.reply(sender, text))
    except Exception as exc:
        print(f"error handling message: {exc}")


@app.post("/webhook")
def incoming():
    if request.form.get("From", "").startswith("whatsapp:"):
        # Twilio webhook: application/x-www-form-urlencoded with From/Body.
        # Answer in a background thread and return 200 immediately -
        # Twilio resends the webhook if we take too long to respond.
        sender = request.form["From"]  # looks like "whatsapp:+15551234567"
        text = request.form.get("Body", "").strip()
        if text:
            threading.Thread(target=_answer_and_send, args=(sender, text), daemon=True).start()
        return "", 200

    # Meta webhook: JSON body.
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
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), threaded=True)
