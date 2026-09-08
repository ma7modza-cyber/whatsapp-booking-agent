"""Bookings storage and slot math. SQLite, one file, zero setup."""
import json
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DB_PATH = os.environ.get("DB_PATH", "bookings.db")

with open(os.path.join(os.path.dirname(__file__), "salon.json"), encoding="utf-8") as f:
    SALON = json.load(f)

TZ = ZoneInfo(SALON["timezone"])


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            service_id TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed',
            created_at TEXT NOT NULL
        )"""
    )
    return conn


def get_service(service_id_or_name):
    needle = service_id_or_name.strip().lower()
    for s in SALON["services"]:
        if needle in (s["id"].lower(), s["name"].lower()):
            return s
    return None


def list_services():
    return [
        {"name": s["name"], "duration_minutes": s["duration_minutes"], "price_ils": s["price_ils"]}
        for s in SALON["services"]
    ]


def _day_bounds(date_str):
    weekday = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A").lower()
    hours = SALON["opening_hours"].get(weekday)
    if not hours:
        return None
    open_t = datetime.strptime(hours[0], "%H:%M").time()
    close_t = datetime.strptime(hours[1], "%H:%M").time()
    return open_t, close_t


def is_open(date_str):
    return _day_bounds(date_str) is not None


def available_slots(date_str, duration_minutes):
    bounds = _day_bounds(date_str)
    if not bounds:
        return []
    open_t, close_t = bounds
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    step = SALON["slot_step_minutes"]

    with _conn() as conn:
        rows = conn.execute(
            "SELECT time, duration_minutes FROM bookings WHERE date = ? AND status = 'confirmed'",
            (date_str,),
        ).fetchall()
    busy = []
    for time_str, dur in rows:
        start = datetime.combine(day, datetime.strptime(time_str, "%H:%M").time())
        busy.append((start, start + timedelta(minutes=dur)))

    now = datetime.now(TZ)
    slots = []
    cursor = datetime.combine(day, open_t)
    end = datetime.combine(day, close_t)
    while cursor + timedelta(minutes=duration_minutes) <= end:
        slot_start = cursor
        slot_end = cursor + timedelta(minutes=duration_minutes)
        if day == now.date() and slot_start.time() <= now.time():
            cursor += timedelta(minutes=step)
            continue
        if not any(slot_start < b_end and b_start < slot_end for b_start, b_end in busy):
            slots.append(slot_start.strftime("%H:%M"))
        cursor += timedelta(minutes=step)
    return slots


def create_booking(customer_id, customer_name, service_id, date_str, time_str):
    service = get_service(service_id)
    if not service:
        return {"ok": False, "error": f"Unknown service '{service_id}'. Use get_services to list them."}
    if time_str not in available_slots(date_str, service["duration_minutes"]):
        return {"ok": False, "error": "That time is not available. Offer the customer other slots."}
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO bookings (customer_id, customer_name, service_id, date, time, duration_minutes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (customer_id, customer_name, service["id"], date_str, time_str,
             service["duration_minutes"], datetime.now(TZ).isoformat(timespec="seconds")),
        )
        booking_id = cur.lastrowid
    return {"ok": True, "booking_id": booking_id, "service": service["name"],
            "date": date_str, "time": time_str, "price_ils": service["price_ils"]}


def list_customer_bookings(customer_id):
    with _conn() as conn:
        rows = conn.execute(
            """SELECT id, service_id, date, time FROM bookings
               WHERE customer_id = ? AND status = 'confirmed' ORDER BY date, time""",
            (customer_id,),
        ).fetchall()
    result = []
    for booking_id, service_id, date_str, time_str in rows:
        service = get_service(service_id)
        result.append({"booking_id": booking_id,
                       "service": service["name"] if service else service_id,
                       "date": date_str, "time": time_str})
    return result


def cancel_booking(customer_id, booking_id):
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE bookings SET status = 'cancelled' WHERE id = ? AND customer_id = ? AND status = 'confirmed'",
            (booking_id, customer_id),
        )
    if cur.rowcount == 0:
        return {"ok": False, "error": "No such booking for this customer."}
    return {"ok": True, "booking_id": booking_id}
