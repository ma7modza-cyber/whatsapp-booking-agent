"""Thin wrapper around the Meta WhatsApp Cloud API for sending text replies."""
import os

import requests

API_VERSION = "v21.0"


def send_text(to, body):
    phone_number_id = os.environ.get("META_PHONE_NUMBER_ID", "")
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
