"""The AI receptionist: DeepSeek chat + tool calling. Used by both the CLI and the WhatsApp server."""
import json
import os

import requests
from datetime import datetime
import bookings
import businesses

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")

SYSTEM_PROMPT = """You are the WhatsApp receptionist for {business_name}, a men's hair salon.

Rules:
- Reply in the SAME language the customer writes in: Hebrew, Arabic, or English.
- Keep every message short and warm, like a real WhatsApp chat. No formal letters, no bullet-point essays.
- Customers can: see services and prices, check available times, book, see their bookings, cancel.
- NEVER invent times. Only offer slots returned by the get_available_slots tool.
- Booking flow: find out which service -> which day -> offer real available times -> ask their name -> confirm the booking with the tool -> repeat back service, day, time, price.
- When you call book_appointment, pass the language of THIS conversation (en, he, or ar) as the 'language' argument, so the booking is saved in the customer's language.
- When listing bookings, each booking comes with a ready-made 'line' field. Output those lines exactly as they are, one per line - never rewrite, reorder, or translate them.
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
        "name": "get_available_slots",
        "description": "Get free start times for a service on a date. Returns [] when the salon is closed or fully booked.",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "service": {"type": "string", "description": "Service id or name"}},
            "required": ["date", "service"]}}},
    {"type": "function", "function": {
        "name": "book_appointment",
        "description": "Book an appointment after the customer picked a service, date, time, and gave their name.",
        "parameters": {"type": "object", "properties": {
            "customer_name": {"type": "string"},
            "service": {"type": "string"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "time": {"type": "string", "description": "HH:MM, 24h"},
            "language": {"type": "string", "enum": ["en", "he", "ar"],
                         "description": "Language of this conversation - en, he, or ar"}},
            "required": ["customer_name", "service", "date", "time"]}}},
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
]

_conversations = {}
_post_booking = set()

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
    if name == "get_available_slots":
        service = bookings.get_service(business_id, args.get("service", ""))
        if not service:
            return {"error": "Unknown service. Call get_services first."}
        return {"date": args["date"], "open": bookings.is_open(business_id, args["date"]),
                "slots": bookings.available_slots(business_id, args["date"], service["duration_minutes"])}
    if name == "book_appointment":
        return bookings.create_booking(business_id, customer_id, args["customer_name"], args["service"],
                                       args["date"], args["time"], args.get("language"))
    if name == "my_bookings":
        return {"bookings": bookings.list_customer_bookings(business_id, customer_id)}
    if name == "owner_bookings_today" and normalized_customer_id in owner_numbers:
        today = datetime.now(bookings.business_timezone(business_id)).strftime("%Y-%m-%d")
        return {"date": today, "bookings": bookings.list_bookings(business_id, today)}
    if name == "owner_all_bookings" and normalized_customer_id in owner_numbers:
        return {"bookings": bookings.list_bookings(business_id)}
    if name == "cancel_booking":
        return bookings.cancel_booking(business_id, customer_id, int(args["booking_id"]))
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


def reply(customer_id, text, business_id=None):
    """One customer message in, one receptionist reply out. Raises on API error."""
    business_id = business_id or businesses.DEFAULT_BUSINESS_ID
    config = businesses.get_business(business_id)
    owner_numbers = {
        businesses.normalize_number(n)
        for n in config.get("owner_whatsapp_numbers", [])
    }
    normalized_customer_id = businesses.normalize_number(customer_id)
    conversation_key = (business_id, customer_id)
    if conversation_key in _post_booking:
        _post_booking.discard(conversation_key)
        if not _looks_like_new_request(text):
            answer = _post_booking_reply(text)
            _conversations.setdefault(conversation_key, []).extend([
                {"role": "user", "content": text},
                {"role": "assistant", "content": answer},
            ])
            return answer

    if not API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not set. Put it in your .env file.")

    now = datetime.now(bookings.business_timezone(business_id))
    system = SYSTEM_PROMPT.replace("{business_name}", config["name"]).replace("{today}", now.strftime("%Y-%m-%d")) \
                          .replace("{weekday}", now.strftime("%A")) \
                          .replace("{now}", now.strftime("%H:%M"))
    history = _conversations.setdefault(conversation_key, [])
    history.append({"role": "user", "content": text})
    is_owner = normalized_customer_id in owner_numbers
    if is_owner:
        system += ("\n- This sender is the salon owner. They may ask for today's bookings "
                   "or all bookings; use the owner booking tools and include customer names.")
    tools = TOOLS + OWNER_TOOLS if is_owner else TOOLS
    messages = [{"role": "system", "content": system}] + history[-20:]

    for _ in range(6):  # tool-call rounds
        msg = _call_llm(messages, tools)
        messages.append(msg)
        if not msg.get("tool_calls"):
            answer = msg.get("content") or "..."
            history.append({"role": "assistant", "content": answer})
            return answer
        for call in msg["tool_calls"]:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _run_tool(business_id, customer_id, name, args)
            if name == "book_appointment" and result.get("ok"):
                _post_booking.add(conversation_key)
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False)})
    return "Sorry, something got stuck on my side - can you say that again?"
