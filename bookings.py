"""Multi-tenant booking storage and slot math. SQLite, one file, zero setup."""
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import businesses

DB_PATH = os.environ.get("DB_PATH", "bookings.db")


def business_config(business_id):
    return businesses.get_business(business_id)


def business_timezone(business_id):
    return ZoneInfo(business_config(business_id)["timezone"])


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id TEXT NOT NULL,
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(bookings)")}
    if "business_id" not in columns:
        conn.execute("ALTER TABLE bookings ADD COLUMN business_id TEXT")
        conn.execute(
            "UPDATE bookings SET business_id = ? WHERE business_id IS NULL OR business_id = ''",
            (businesses.DEFAULT_BUSINESS_ID,),
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bookings_business_date "
        "ON bookings (business_id, date, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bookings_business_customer "
        "ON bookings (business_id, customer_id, status)"
    )
    conn.commit()
    return conn


def get_service(business_id, service_id_or_name):
    needle = service_id_or_name.strip().lower()
    for service in business_config(business_id)["services"]:
        if needle in (service["id"].lower(), service["name"].lower()):
            return service
    return None


def list_services(business_id):
    return [
        {"name": s["name"], "duration_minutes": s["duration_minutes"], "price_ils": s["price_ils"]}
        for s in business_config(business_id)["services"]
    ]


def _day_bounds(business_id, date_str):
    weekday = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A").lower()
    hours = business_config(business_id)["opening_hours"].get(weekday)
    if not hours:
        return None
    return datetime.strptime(hours[0], "%H:%M").time(), datetime.strptime(hours[1], "%H:%M").time()


def is_open(business_id, date_str):
    return _day_bounds(business_id, date_str) is not None


def available_slots(business_id, date_str, duration_minutes):
    bounds = _day_bounds(business_id, date_str)
    if not bounds:
        return []
    config = business_config(business_id)
    open_t, close_t = bounds
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT time, duration_minutes FROM bookings "
            "WHERE business_id = ? AND date = ? AND status = 'confirmed'",
            (business_id, date_str),
        ).fetchall()
    busy = []
    for time_str, duration in rows:
        start = datetime.combine(day, datetime.strptime(time_str, "%H:%M").time())
        busy.append((start, start + timedelta(minutes=duration)))

    now = datetime.now(business_timezone(business_id))
    slots = []
    cursor = datetime.combine(day, open_t)
    end = datetime.combine(day, close_t)
    while cursor + timedelta(minutes=duration_minutes) <= end:
        slot_end = cursor + timedelta(minutes=duration_minutes)
        if day == now.date() and cursor.time() <= now.time():
            cursor += timedelta(minutes=config["slot_step_minutes"])
            continue
        if not any(cursor < busy_end and busy_start < slot_end for busy_start, busy_end in busy):
            slots.append(cursor.strftime("%H:%M"))
        cursor += timedelta(minutes=config["slot_step_minutes"])
    return slots


def create_booking(business_id, customer_id, customer_name, service_id, date_str, time_str):
    service = get_service(business_id, service_id)
    if not service:
        return {"ok": False, "error": f"Unknown service '{service_id}'. Use get_services to list them."}
    if time_str not in available_slots(business_id, date_str, service["duration_minutes"]):
        return {"ok": False, "error": "That time is not available. Offer the customer other slots."}
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO bookings
               (business_id, customer_id, customer_name, service_id, date, time, duration_minutes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (business_id, customer_id, customer_name, service["id"], date_str, time_str,
             service["duration_minutes"], datetime.now(business_timezone(business_id)).isoformat(timespec="seconds")),
        )
    return {"ok": True, "booking_id": cur.lastrowid, "service": service["name"],
            "date": date_str, "time": time_str, "price_ils": service["price_ils"]}


def list_customer_bookings(business_id, customer_id):
    with _conn() as conn:
        rows = conn.execute(
            """SELECT id, service_id, date, time FROM bookings
               WHERE business_id = ? AND customer_id = ? AND status = 'confirmed'
               ORDER BY date, time""",
            (business_id, customer_id),
        ).fetchall()
    result = []
    for booking_id, service_id, date_str, time_str in rows:
        service = get_service(business_id, service_id)
        result.append({"booking_id": booking_id, "service": service["name"] if service else service_id,
                       "date": date_str, "time": time_str})
    return result


def list_bookings(business_id, date_str=None):
    query = """SELECT customer_name, service_id, date, time FROM bookings
               WHERE business_id = ? AND status = 'confirmed'"""
    params = [business_id]
    if date_str:
        query += " AND date = ?"
        params.append(date_str)
    query += " ORDER BY date, time"
    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    result = []
    for customer_name, service_id, booking_date, time_str in rows:
        service = get_service(business_id, service_id)
        result.append({"customer_name": customer_name, "service": service["name"] if service else service_id,
                       "date": booking_date, "time": time_str})
    return result


def cancel_booking(business_id, customer_id, booking_id):
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE bookings SET status = 'cancelled'
               WHERE id = ? AND business_id = ? AND customer_id = ? AND status = 'confirmed'""",
            (booking_id, business_id, customer_id),
        )
    if cur.rowcount == 0:
        return {"ok": False, "error": "No such booking for this customer."}
    return {"ok": True, "booking_id": booking_id}
