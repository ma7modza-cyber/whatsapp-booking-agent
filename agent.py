"""The AI receptionist: DeepSeek chat + tool calling. Used by both the CLI and the WhatsApp server."""
import json
import logging
import os
import threading
from datetime import datetime

import requests
import bookings
import businesses
import whatsapp

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")

SYSTEM_PROMPT = """You are the WhatsApp receptionist for {business_name}, a men's hair salon.

Rules:
- Reply in the SAME language the customer writes in: Hebrew, Arabic, or English.
- Keep every message short and warm, like a real WhatsApp chat. No formal letters, no bullet-point essays.
- Customers can: see services and prices, check available times, book, see their bookings, cancel.
- NEVER invent times. Only offer slots returned by the get_available_slots tool.
- Never skip the barber choice or choose a barber for the customer. In Hebrew ask: "איזה ספר תרצה? עומר, עלי או יוסף?" (using the configured names returned by get_workers).
- Booking flow: find out which service -> ask which barber (use get_workers and show all three choices) -> which day -> offer real available times for that barber -> ask for BOTH their first name and family name -> confirm the booking with the tool -> repeat back their full name, service, barber, day, time, price.
- Never call book_appointment until the customer has provided both a first name and a family name.
- When you call book_appointment, pass the language of THIS conversation (en, he, or ar) as the 'language' argument, so the booking is saved in the customer's language.
- When listing bookings, each booking comes with a ready-made 'line' field. Output those lines exactly as they are, one per line - never rewrite, reorder, or translate them.
- To cancel: call my_bookings (or the owner booking tools) first, take the booking_id from that result, and call cancel_booking with it. NEVER ask the customer for a booking ID - they don't see one.
- If the day they want is closed, say so and suggest the next open day.
- Today is {today} ({weekday}). Current salon time is {now}. All dates you pass to tools must be YYYY-MM-DD.
- Prices are in shekels (ILS).
- If someone asks something unrelated to the salon, answer briefly and steer back to booking.
"""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_services",
        "description": "List the salon's services with duration and price.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_workers",
        "description": "List the barbers the customer can choose from in the conversation language.",
        "parameters": {"type": "object", "properties": {
            "language": {"type": "string", "enum": ["en", "he", "ar"]}}}}},
    {"type": "function", "function": {
        "name": "get_available_slots",
        "description": "Get free start times for a service on a date. Returns [] when the salon is closed or fully booked.",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "service": {"type": "string", "description": "Service id or name"},
            "worker": {"type": "string", "description": "Barber id or name"}},
            "required": ["date", "service", "worker"]}}},
    {"type": "function", "function": {
        "name": "book_appointment",
        "description": "Book an appointment after the customer picked a service, barber, date, time, and gave both their first name and family name.",
        "parameters": {"type": "object", "properties": {
            "first_name": {"type": "string", "description": "Customer first/given name"},
            "family_name": {"type": "string", "description": "Customer family/last name"},
            "service": {"type": "string"},
            "worker": {"type": "string", "description": "Chosen barber id or name"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "time": {"type": "string", "description": "HH:MM, 24h"},
            "language": {"type": "string", "enum": ["en", "he", "ar"],
                         "description": "Language of this conversation - en, he, or ar"}},
            "required": ["first_name", "family_name", "service", "worker", "date", "time"]}}},
    {"type": "function", "function": {
        "name": "my_bookings",
        "description": "List this customer's upcoming bookings.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "cancel_booking",
        "description": "Cancel one of the customer's bookings by booking_id.",
        "parameters": {"type": "object", "properties": {
            "booking_id": {"type": "integer"}},
            "required": ["booking_id"]}}},

]

OWNER_TOOLS = [
    {"type": "function", "function": {
        "name": "owner_bookings_today",
        "description": "For the salon owner only: list all confirmed bookings for today, with customer names, services, and times.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "owner_all_bookings",
        "description": "For the salon owner only: list all confirmed bookings, with customer names, services, dates, and times.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "owner_bookings_for_date",
        "description": "For the salon owner only: list all confirmed bookings for one specific date, with customer names, services, and times. Use this for dates such as tomorrow after resolving them to YYYY-MM-DD.",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["date"]}}},
    {"type": "function", "function": {
        "name": "owner_clear_bookings_for_date",
        "description": "For the salon owner only: permanently delete every booking for one specific date when the owner asks to clear that day's bookings. Returns the number cleared.",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["date"]}}},
    {"type": "function", "function": {
        "name": "owner_clear_all_bookings",
        "description": "For the salon owner only: permanently delete every booking for this business when the owner explicitly asks to clear, wipe, or reset ALL bookings. Returns the number cleared.",
        "parameters": {"type": "object", "properties": {}}}},
]

_conversations = {}
_post_booking = set()
_state_lock = threading.RLock()
_MAX_HISTORY_MESSAGES = 20
_REPLY_LOCKS = tuple(threading.RLock() for _ in range(64))


def _notify_owner(business_id, text):
    """WhatsApp the tenant's owner(s) about a booking event.

    Runs on a daemon thread: the send is a blocking Twilio/Meta REST call and
    the Twilio webhook budget is ~15s, so a slow or rejected send must never
    delay or break the customer reply. Errors are logged, never raised."""
    def _send():
        config = businesses.get_business(business_id)
        for number in config.get("owner_whatsapp_numbers", []):
            try:
                whatsapp.send_text(businesses.normalize_number(number), text, business_id)
            except Exception:
                logging.exception("owner notification failed")
    threading.Thread(target=_send, daemon=True).start()

# A confirmed booking leaves the prior date/time in the conversation. Without this
# guard, a short follow-up such as "thanks" can make the model try that slot again.
_NEW_REQUEST_WORDS = (
    "book", "booking", "appointment", "available", "availability", "slot",
    "time", "service", "price", "cost", "cancel", "change", "move", "another",
    "tomorrow", "today", "sunday", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday",
    "לקבוע", "תור", "פנוי", "זמין", "שעה", "שירות", "מחיר", "לבטל", "לשנות",
    "حجز", "موعد", "متاح", "فاضي", "ساعة", "وقت", "خدمة", "سعر", "إلغاء", "تغيير",
)


def _looks_like_new_request(text):
    lowered = text.casefold()
    return any(word in lowered for word in _NEW_REQUEST_WORDS)


def _post_booking_reply(text):
    """Return a brief acknowledgement in the customer's language."""
    if any("\u0590" <= ch <= "\u05ff" for ch in text):
        return "בשמחה! נתראה 😊"
    if any("\u0600" <= ch <= "\u06ff" for ch in text):
        return "العفو! بنشوفك قريب 😊"
    return "You're welcome! See you then 😊"


def _run_tool(business_id, customer_id, name, args):
    owner_numbers = {
        businesses.normalize_number(n)
        for n in businesses.get_business(business_id).get("owner_whatsapp_numbers", [])
    }
    normalized_customer_id = businesses.normalize_number(customer_id)
    if name == "get_services":
        return {"services": bookings.list_services(business_id)}
    if name == "get_workers":
        return {"workers": bookings.list_workers(business_id, args.get("language"))}
    if name == "get_available_slots":
        service = bookings.get_service(business_id, args.get("service", ""))
        if not service:
            return {"error": "Unknown service. Call get_services first."}
        worker = bookings.get_worker(business_id, args.get("worker", ""))
        if not worker:
            return {"error": "Unknown barber. Call get_workers first."}
        return {"date": args["date"], "worker": bookings.worker_display_name(worker), "open": bookings.is_open(business_id, args["date"]),
                "slots": bookings.available_slots(business_id, args["date"], service["duration_minutes"], worker["id"])}
    if name == "book_appointment":
        required = ("first_name", "family_name", "service", "worker", "date", "time")
        missing = [field for field in required if not str(args.get(field, "")).strip()]
        if missing:
            return {"ok": False, "error": "Missing required booking details: " + ", ".join(missing)}
        result = bookings.create_booking(business_id, customer_id, args["first_name"], args["family_name"],
                                         args["service"], args["worker"], args["date"], args["time"], args.get("language"))
        if result.get("ok"):
            _notify_owner(business_id, "New booking:\n" + bookings.booking_line(
                result["service"], result["date"], result["time"], result["customer_name"], result["worker"]))
        return result
    if name == "my_bookings":
        return {"bookings": bookings.list_customer_bookings(business_id, customer_id)}
    owner_tool_names = {
        "owner_bookings_today", "owner_all_bookings", "owner_bookings_for_date",
        "owner_clear_bookings_for_date", "owner_clear_all_bookings",
    }
    if name in owner_tool_names and normalized_customer_id not in owner_numbers:
        logger.warning("rejected owner tool business=%s customer=%s tool=%s",
                       business_id, customer_id, name)
        return {"ok": False, "error": "Owner access required."}
    if name == "owner_bookings_today":
        today = datetime.now(bookings.business_timezone(business_id)).strftime("%Y-%m-%d")
        return {"date": today, "bookings": bookings.list_bookings(business_id, today)}
    if name == "owner_all_bookings":
        return {"bookings": bookings.list_bookings(business_id)}
    if name in ("owner_bookings_for_date", "owner_clear_bookings_for_date"):
        date_str = str(args.get("date", "")).strip()
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return {"ok": False, "error": "A valid date in YYYY-MM-DD format is required."}
        if name == "owner_bookings_for_date":
            return {"date": date_str, "bookings": bookings.list_bookings(business_id, date_str)}
        cleared = bookings.clear_bookings(business_id, date_str)
        logger.info("owner cleared bookings business=%s customer=%s date=%s count=%d",
                    business_id, customer_id, date_str, cleared)
        return {"ok": True, "cleared_count": cleared, "date": date_str,
                "message": f"Cleared {cleared} bookings for {date_str}"}
    if name == "owner_clear_all_bookings":
        cleared = bookings.clear_bookings(business_id)
        logger.info("owner cleared all bookings business=%s customer=%s count=%d",
                    business_id, customer_id, cleared)
        return {"ok": True, "cleared_count": cleared,
                "message": f"Cleared {cleared} bookings"}
    if name == "cancel_booking":
        result = bookings.cancel_booking(business_id, customer_id, int(args["booking_id"]))
        if result.get("ok"):
            _notify_owner(business_id, "Booking cancelled:\n" + bookings.booking_line(
                result["service"], result["date"], result["time"], result["customer_name"], result["worker"]))
        return result
    return {"error": f"Unknown tool {name}"}


def _call_llm(messages, tools):
    resp = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": messages, "tools": tools, "temperature": 0.4},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def _reply_unlocked(customer_id, text, business_id=None):
    """One customer message in, one receptionist reply out. Raises on API error."""
    business_id = business_id or businesses.DEFAULT_BUSINESS_ID
    logger.info("reply start business=%s customer=%s text_length=%d", business_id, customer_id, len(text))
    config = businesses.get_business(business_id)
    owner_numbers = {
        businesses.normalize_number(n)
        for n in config.get("owner_whatsapp_numbers", [])
    }
    normalized_customer_id = businesses.normalize_number(customer_id)
    conversation_key = (business_id, customer_id)
    with _state_lock:
        was_post_booking = conversation_key in _post_booking
        _post_booking.discard(conversation_key)
        if was_post_booking and not _looks_like_new_request(text):
            answer = _post_booking_reply(text)
            history = _conversations.setdefault(conversation_key, [])
            history.extend([
                {"role": "user", "content": text},
                {"role": "assistant", "content": answer},
            ])
            del history[:-_MAX_HISTORY_MESSAGES]
            return answer

    if not API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not set. Put it in your .env file.")

    now = datetime.now(bookings.business_timezone(business_id))
    system = SYSTEM_PROMPT.replace("{business_name}", config["name"]).replace("{today}", now.strftime("%Y-%m-%d")) \
                          .replace("{weekday}", now.strftime("%A")) \
                          .replace("{now}", now.strftime("%H:%M"))
    with _state_lock:
        history = _conversations.setdefault(conversation_key, [])
        history.append({"role": "user", "content": text})
        del history[:-_MAX_HISTORY_MESSAGES]
        history_snapshot = list(history)
    is_owner = normalized_customer_id in owner_numbers
    if is_owner:
        system += ("\n- This sender is the salon owner. They may ask for today's bookings, "
                   "all bookings, or bookings for a specific date; use the matching owner booking "
                   "tool and include customer names. Resolve relative dates such as tomorrow from "
                   "the salon date above, and pass YYYY-MM-DD to owner_bookings_for_date. "
                   "If they ask to clear bookings for a specific day, call "
                   "owner_clear_bookings_for_date. Only call owner_clear_all_bookings when "
                   "they explicitly ask to clear every booking. Reply with the tool's exact message field.")
    tools = TOOLS + OWNER_TOOLS if is_owner else TOOLS
    messages = [{"role": "system", "content": system}] + history_snapshot

    for round_number in range(1, 7):  # tool-call rounds
        msg = _call_llm(messages, tools)
        messages.append(msg)
        tool_calls = msg.get("tool_calls") or []
        logger.info("llm round=%d customer=%s tool_calls=%d content_length=%d",
                    round_number, customer_id, len(tool_calls), len(msg.get("content") or ""))
        if not tool_calls:
            answer = msg.get("content")
            if not isinstance(answer, str) or not answer.strip():
                logger.error("empty assistant reply customer=%s round=%d message=%r",
                             customer_id, round_number, msg)
                answer = "Sorry, something got stuck on my side - can you say that again?"
            with _state_lock:
                history.append({"role": "assistant", "content": answer})
                del history[:-_MAX_HISTORY_MESSAGES]
            logger.info("reply ready customer=%s answer_length=%d", customer_id, len(answer))
            return answer
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name", "")
            try:
                args = json.loads(function.get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise ValueError("tool arguments must be a JSON object")
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("invalid tool arguments customer=%s tool=%s error=%s",
                               customer_id, name, exc)
                args = {}
            try:
                result = _run_tool(business_id, customer_id, name, args)
            except Exception:
                logger.exception("tool failed customer=%s tool=%s", customer_id, name)
                result = {"ok": False, "error": "The booking tool failed. Ask the customer to try again."}
            logger.info("tool result customer=%s tool=%s ok=%s error=%s",
                        customer_id, name, result.get("ok"), result.get("error"))
            if name == "book_appointment" and result.get("ok"):
                with _state_lock:
                    _post_booking.add(conversation_key)
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": json.dumps(result, ensure_ascii=False)})
    logger.error("tool round limit reached customer=%s", customer_id)
    return "Sorry, something got stuck on my side - can you say that again?"


def reply(customer_id, text, business_id=None):
    """Serialize each customer conversation while allowing unrelated chats in parallel."""
    lock = _REPLY_LOCKS[hash((business_id, customer_id)) % len(_REPLY_LOCKS)]
    with lock:
        return _reply_unlocked(customer_id, text, business_id)
