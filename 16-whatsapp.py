"""Send WhatsApp text replies.

Two providers, picked automatically:
- Twilio (REST API, no extra SDK) when TWILIO_ACCOUNT_SID is set in .env.
- Meta WhatsApp Cloud API otherwise.
"""
import os

import requests

import businesses

API_VERSION = "v21.0"


def _send_twilio(to, body, business_id):
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    config = businesses.get_business(business_id)
    from_number = config.get("twilio_whatsapp_from") or os.environ.get("TWILIO_WHATSAPP_FROM", "")
    if not sid or not token or not from_number:
        raise RuntimeError(
            "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM are not set in .env"
        )
    if not to.startswith("whatsapp:"):
        to = f"whatsapp:{to}"
    resp = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, token),
        data={"From": from_number, "To": to, "Body": body},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _send_meta(to, body, business_id):
    config = businesses.get_business(business_id)
    phone_number_id = config.get("meta_phone_number_id") or os.environ.get("META_PHONE_NUMBER_ID", "")
    token = os.environ.get("META_ACCESS_TOKEN", "")
    if not phone_number_id or not token:
        raise RuntimeError("META_PHONE_NUMBER_ID / META_ACCESS_TOKEN are not set in .env")
    resp = requests.post(
        f"https://graph.facebook.com/{API_VERSION}/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"messaging_product": "whatsapp", "to": to,
              "type": "text", "text": {"body": body}},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def send_text(to, body, business_id=None):
    business_id = business_id or businesses.DEFAULT_BUSINESS_ID
    if os.environ.get("TWILIO_ACCOUNT_SID"):
        return _send_twilio(to, body, business_id)
    return _send_meta(to, body, business_id)
