"""Flask webhook for Twilio and Meta WhatsApp messages."""
import logging
import os
from xml.sax.saxutils import escape as xml_escape

from dotenv import load_dotenv
from flask import Flask, Response, request

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()

import agent
import businesses
import security
import whatsapp

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_WEBHOOK_BYTES", "65536"))
_limiter = security.RateLimiter(
    limit=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "30")), window_seconds=60
)
_EMPTY_TWIML = "<Response></Response>"
_ERROR_REPLY = "Sorry, something went wrong. Please try again."


def _twiml(message=None):
    body = _EMPTY_TWIML if message is None else (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Message>{xml_escape(message)}</Message></Response>"
    )
    return Response(body, status=200, mimetype="text/xml")


@app.get("/")
def health():
    return "booking agent is up\n"


@app.get("/webhook")
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    expected = os.environ.get("META_VERIFY_TOKEN")
    if expected and mode == "subscribe" and token and security.hmac.compare_digest(token, expected):
        return challenge or "", 200
    return "forbidden", 403


def _handle_twilio():
    if not security.valid_twilio_request(request):
        logger.warning("rejected invalid Twilio signature")
        return Response("forbidden", status=403)
    sender = request.form.get("From", "")
    business_id = businesses.resolve_twilio_number(request.form.get("To", ""))
    if not business_id:
        logger.warning("unknown Twilio destination")
        return _twiml()
    if not _limiter.allow(("twilio", sender)):
        logger.warning("Twilio rate limit sender=%s business=%s", sender, business_id)
        return _twiml(_ERROR_REPLY)
    text = request.form.get("Body", "").strip()
    if not text:
        return _twiml()
    logger.info("Twilio inbound sender=%s business=%s text_length=%d", sender, business_id, len(text))
    try:
        answer = agent.reply(sender, text, business_id)
    except Exception:
        logger.exception("error handling Twilio message sender=%s business=%s", sender, business_id)
        answer = _ERROR_REPLY
    if not isinstance(answer, str) or not answer.strip():
        logger.error("empty Twilio reply sender=%s business=%s", sender, business_id)
        answer = _ERROR_REPLY
    logger.info("Twilio outbound sender=%s business=%s answer_length=%d", sender, business_id, len(answer))
    return _twiml(answer)


def _handle_meta():
    if not security.valid_meta_request(request):
        logger.warning("rejected invalid Meta signature")
        return Response("forbidden", status=403)
    data = request.get_json(silent=True) or {}
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                business_id = businesses.resolve_meta_phone_number_id(
                    value.get("metadata", {}).get("phone_number_id")
                )
                if not business_id:
                    logger.warning("unknown Meta phone_number_id")
                    continue
                for message in value.get("messages", []):
                    if message.get("type") != "text":
                        continue
                    sender = message.get("from", "")
                    if not sender or not _limiter.allow(("meta", sender)):
                        logger.warning("Meta rate limit or missing sender business=%s", business_id)
                        continue
                    answer = agent.reply(sender, message.get("text", {}).get("body", ""), business_id)
                    whatsapp.send_text(sender, answer, business_id)
    except Exception:
        logger.exception("error handling Meta webhook")
    return "ok", 200


@app.post("/webhook")
def incoming():
    if request.form.get("From", "").startswith("whatsapp:"):
        return _handle_twilio()
    return _handle_meta()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), threaded=True)
