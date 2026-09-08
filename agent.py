"""The AI receptionist: DeepSeek chat + tool calling. Used by both the CLI and the WhatsApp server."""
import json
import os

import requests
from datetime import datetime
from zoneinfo import ZoneInfo

import bookings

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")

SYSTEM_PROMPT = """You are the WhatsApp receptionist for {salon_name}, a men's hair salon.

Rules:
- Reply in the SAME language the customer writes in: Hebrew, Arabic, or English.
- Keep every message short and warm, like a real WhatsApp chat. No formal letters, no bullet-point essays.
- Customers can: see services and prices, check available times, book, see their bookings, cancel.
- NEVER invent times. Only offer slots returned by the get_available_slots tool.
- Booking flow: find out which service -> which day -> offer real available times -> ask their name -> confirm the booking with the tool -> repeat back service, day, time, price.
- If the day they want is closed, say so and suggest the next open day.
- Today is {today} ({weekday}). Current salon time is {now}. All dates you pass to tools must be YYYY-MM-DD.
- Prices are in shekels (ILS).
- If someone asks something unrelated to the salon, answer briefly and steer back to booking.
""".replace("{salon_name}", bookings.SALON["name"])

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
            "time": {"type": "string", "description": "HH:MM, 24h"}},
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

_conversations = {}


def _run_tool(customer_id, name, args):
    if name == "get_services":
        return {"services": bookings.list_services()}
    if name == "get_available_slots":
        service = bookings.get_service(args.get("service", ""))
        if not service:
            return {"error": "Unknown service. Call get_services first."}
        return {"date": args["date"], "open": bookings.is_open(args["date"]),
                "slots": bookings.available_slots(args["date"], service["duration_minutes"])}
    if name == "book_appointment":
        return bookings.create_booking(customer_id, args["customer_name"], args["service"],
                                       args["date"], args["time"])
    if name == "my_bookings":
        return {"bookings": bookings.list_customer_bookings(customer_id)}
    if name == "cancel_booking":
        return bookings.cancel_booking(customer_id, int(args["booking_id"]))
    return {"error": f"Unknown tool {name}"}


def _call_llm(messages):
    resp = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": messages, "tools": TOOLS, "temperature": 0.4},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def reply(customer_id, text):
    """One customer message in, one receptionist reply out. Raises on API error."""
    if not API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not set. Put it in your .env file.")

    now = datetime.now(bookings.TZ)
    system = SYSTEM_PROMPT.replace("{today}", now.strftime("%Y-%m-%d")) \
                          .replace("{weekday}", now.strftime("%A")) \
                          .replace("{now}", now.strftime("%H:%M"))
    history = _conversations.setdefault(customer_id, [])
    history.append({"role": "user", "content": text})
    messages = [{"role": "system", "content": system}] + history[-20:]

    for _ in range(6):  # tool-call rounds
        msg = _call_llm(messages)
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
            result = _run_tool(customer_id, name, args)
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False)})
    return "Sorry, something got stuck on my side - can you say that again?"
