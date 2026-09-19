"""Multi-tenant booking storage and slot math. SQLite, one file, zero setup."""
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import businesses

DB_PATH = os.environ.get("DB_PATH", "bookings.db")

# WhatsApp applies the Unicode bidi algorithm to every message. A bookings-list
# line that starts with (or contains) a Hebrew/Arabic name can render with
# flipped field order. Force an LTR paragraph per line with U+200E and wrap each
# free-text field in a first-strong isolate (U+2068..U+2069) so every line keeps
# the same visual order - bullet, name, service, date, time - in any language.
_LRM = "\u200e"
_FSI = "\u2068"
_PDI = "\u2069"


def _bidi_field(text):
    return f"{_FSI}{text}{_PDI}"


def _guess_language(text):
    """Best-effort language guess from script, for bookings saved before
    service_name was stored."""
    for ch in text or "":
        if "\u0590" <= ch <= "\u05ff":
            return "he"
        if "\u0600" <= ch <= "\u06ff":
            return "ar"
    return None


def service_display_name(service, language=None):
    """Service name in the requested language, falling back to the default name."""
    names = service.get("names") or {}
    if language and language in names:
        return names[language]
    return service["name"]


def booking_line(service_name, date_str, time_str, customer_name=None):
    """One bidi-safe display line: '* name - service - date time' (name only
    on owner lists)."""
    parts = []
    if customer_name:
        parts.append(_bidi_field(customer_name))
    parts.append(_bidi_field(service_name))
    return f"{_LRM}\u2022 " + " - ".join(parts) + f" - {date_str} {time_str}"


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
            first_name TEXT,
            family_name TEXT,
            service_id TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            service_name TEXT,
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
    if "service_name" not in columns:
        conn.execute("ALTER TABLE bookings ADD COLUMN service_name TEXT")
    if "first_name" not in columns:
        conn.execute("ALTER TABLE bookings ADD COLUMN first_name TEXT")
    if "family_name" not in columns:
        conn.execute("ALTER TABLE bookings ADD COLUMN family_name TEXT")
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
    needle = service_id_or_name.strip().casefold()
    for service in business_config(business_id)["services"]:
        candidates = [service["id"], service["name"], *(service.get("names") or {}).values()]
        if needle in {c.casefold() for c in candidates}:
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


def available_slots(business_id, date_str, duration_minutes, conn=None):
    bounds = _day_bounds(business_id, date_str)
    if not bounds:
        return []
    config = business_config(business_id)
    open_t, close_t = bounds
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    owns_connection = conn is None
    conn = conn or _conn()
    try:
        rows = conn.execute(
            "SELECT time, duration_minutes FROM bookings "
            "WHERE business_id = ? AND date = ? AND status = 'confirmed'",
            (business_id, date_str),
        ).fetchall()
    finally:
        if owns_connection:
            conn.close()
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


def create_booking(business_id, customer_id, first_name, family_name, service_id, date_str, time_str, language=None):
    first_name = first_name.strip()
    family_name = family_name.strip()
    if not first_name or not family_name:
        return {"ok": False, "error": "Both first name and family name are required."}
    customer_name = f"{first_name} {family_name}"
    service = get_service(business_id, service_id)
    if not service:
        return {"ok": False, "error": f"Unknown service '{service_id}'. Use get_services to list them."}
    display_name = service_display_name(service, language)
    with _conn() as conn:
        # Lock before checking availability so two simultaneous webhook workers
        # cannot both claim the same slot.
        conn.execute("BEGIN IMMEDIATE")
        if time_str not in available_slots(
            business_id, date_str, service["duration_minutes"], conn=conn
        ):
            return {"ok": False, "error": "That time is not available. Offer the customer other slots."}
        cur = conn.execute(
            """INSERT INTO bookings
               (business_id, customer_id, customer_name, first_name, family_name, service_id, date, time, duration_minutes, service_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (business_id, customer_id, customer_name, first_name, family_name, service["id"], date_str, time_str,
             service["duration_minutes"], display_name,
             datetime.now(business_timezone(business_id)).isoformat(timespec="seconds")),
        )
    return {"ok": True, "booking_id": cur.lastrowid, "customer_name": customer_name,
            "first_name": first_name, "family_name": family_name, "service": display_name,
            "date": date_str, "time": time_str, "price_ils": service["price_ils"]}


def _resolve_display_name(business_id, service_id, stored_name, customer_name=None):
    if stored_name:
        return stored_name
    service = get_service(business_id, service_id)
    if not service:
        return service_id
    return service_display_name(service, _guess_language(customer_name))


def list_customer_bookings(business_id, customer_id):
    with _conn() as conn:
        rows = conn.execute(
            """SELECT id, service_id, date, time, service_name, customer_name FROM bookings
               WHERE business_id = ? AND customer_id = ? AND status = 'confirmed'
               ORDER BY date, time""",
            (business_id, customer_id),
        ).fetchall()
    result = []
    for booking_id, service_id, date_str, time_str, stored_name, customer_name in rows:
        display_name = _resolve_display_name(business_id, service_id, stored_name, customer_name)
        result.append({"booking_id": booking_id, "service": display_name,
                       "date": date_str, "time": time_str,
                       "line": booking_line(display_name, date_str, time_str)})
    return result


def list_bookings(business_id, date_str=None):
    query = """SELECT id, customer_name, first_name, family_name, service_id, date, time, service_name FROM bookings
               WHERE business_id = ? AND status = 'confirmed'"""
    params = [business_id]
    if date_str:
        query += " AND date = ?"
        params.append(date_str)
    query += " ORDER BY date, time"
    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    result = []
    for booking_id, customer_name, first_name, family_name, service_id, booking_date, time_str, stored_name in rows:
        display_name = _resolve_display_name(business_id, service_id, stored_name, customer_name)
        if not first_name:
            first_name, _, inferred_family_name = customer_name.partition(" ")
            family_name = family_name or inferred_family_name
        result.append({"booking_id": booking_id, "customer_name": customer_name,
                       "first_name": first_name, "family_name": family_name, "service": display_name,
                       "date": booking_date, "time": time_str,
                       "line": booking_line(display_name, booking_date, time_str, customer_name)})
    return result


def cancel_booking(business_id, customer_id, booking_id):
    with _conn() as conn:
        row = conn.execute(
            """SELECT customer_name, service_id, date, time, service_name FROM bookings
               WHERE id = ? AND business_id = ? AND customer_id = ? AND status = 'confirmed'""",
            (booking_id, business_id, customer_id),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "No such booking for this customer."}
        conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
    customer_name, service_id, date_str, time_str, stored_name = row
    display_name = _resolve_display_name(business_id, service_id, stored_name, customer_name)
    return {"ok": True, "booking_id": booking_id, "customer_name": customer_name,
            "service": display_name, "date": date_str, "time": time_str}


def clear_bookings(business_id, date_str=None):
    """Permanently delete one business's bookings, optionally for one date."""
    query = "DELETE FROM bookings WHERE business_id = ?"
    params = [business_id]
    if date_str is not None:
        query += " AND date = ?"
        params.append(date_str)
    with _conn() as conn:
        cursor = conn.execute(query, params)
        return cursor.rowcount
