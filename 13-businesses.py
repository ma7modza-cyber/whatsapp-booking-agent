"""Business configuration and inbound-number routing."""
import json
import os

CONFIG_PATH = os.environ.get(
    "BUSINESSES_CONFIG",
    os.path.join(os.path.dirname(__file__), "businesses.json"),
)

with open(CONFIG_PATH, encoding="utf-8") as f:
    _CONFIG = json.load(f)

DEFAULT_BUSINESS_ID = _CONFIG["default_business_id"]
BUSINESSES = _CONFIG["businesses"]

if DEFAULT_BUSINESS_ID not in BUSINESSES:
    raise ValueError("default_business_id is not present in businesses")


def normalize_number(number):
    value = (number or "").strip()
    if value and not value.startswith("whatsapp:"):
        value = f"whatsapp:{value}"
    return value


_INBOUND_TO_BUSINESS = {}
for business_id, config in BUSINESSES.items():
    for number in config.get("inbound_whatsapp_numbers", []):
        normalized = normalize_number(number)
        if normalized in _INBOUND_TO_BUSINESS:
            raise ValueError(f"Inbound number {normalized} belongs to more than one business")
        _INBOUND_TO_BUSINESS[normalized] = business_id


def get_business(business_id):
    try:
        return BUSINESSES[business_id]
    except KeyError as exc:
        raise KeyError(f"Unknown business_id: {business_id}") from exc


def resolve_twilio_number(number):
    """Resolve Twilio's webhook `To` value to a business, or return None."""
    return _INBOUND_TO_BUSINESS.get(normalize_number(number))


def resolve_meta_phone_number_id(phone_number_id):
    """Resolve Meta webhook metadata.phone_number_id to a business, or None."""
    needle = str(phone_number_id or "").strip()
    if needle and needle == os.environ.get("META_PHONE_NUMBER_ID", "").strip():
        return DEFAULT_BUSINESS_ID
    for business_id, config in BUSINESSES.items():
        if str(config.get("meta_phone_number_id", "")).strip() == needle and needle:
            return business_id
    return None
