"""Flask webhook for incoming WhatsApp messages.

Works with both providers, detected automatically per request:
- Twilio WhatsApp Sandbox (form-encoded POST to /webhook, TwiML reply)
- Meta WhatsApp Cloud API (GET verification handshake + JSON POST to /webhook)

GET  /        - health check
GET  /webhook - Meta verification handshake (uses META_VERIFY_TOKEN)
POST /webhook - incoming WhatsApp messages (Twilio or Meta)
"""
import os
from xml.sax.saxutils import escape as xml_escape

from dotenv import load_dotenv
from flask import Flask, request, Response

# Load .env BEFORE importing agent/whatsapp - those modules read
# environment variables (API keys, DB path) at import time.
load_dotenv()

import agent
import whatsapp

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


@app.post("/webhook")
def incoming():
    if request.form.get("From", "").startswith("whatsapp:"):
        # Twilio webhook: application/x-www-form-urlencoded with From/Body.
        # Reply SYNCHRONOUSLY with TwiML: the sandbox/trial rejects free-form
        # REST API sends (error 21654, ContentSid required), while a TwiML
        # <Message> in the webhook response needs no pre-approved template.
        sender = request.form["From"]  # looks like "whatsapp:+15551234567"
        text = request.form.get("Body", "").strip()
        if not text:  # e.g. a media-only or status message - no reply needed
            return Response("<Response></Response>", status=200, mimetype="text/xml")
        try:
            answer = agent.reply(sender, text)
        except Exception as exc:
            print(f"error handling message: {exc}")
            answer = "Sorry, something went wrong. Please try again."
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Message>{xml_escape(answer)}</Message></Response>"
        )
        return Response(twiml, status=200, mimetype="text/xml")

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
