import importlib
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch


class WeeklyBookingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["DB_PATH"] = os.path.join(self.temp.name, "bookings.db")
        import bookings
        import agent
        self.bookings = importlib.reload(bookings)
        self.agent = importlib.reload(agent)
        with self.bookings._conn() as conn:
            rows = [
                ("in-sun", "2026-09-20", "10:00"),
                ("in-wed", "2026-09-23", "14:30"),
                ("out-next", "2026-09-28", "11:00"),
            ]
            for name, date, time in rows:
                conn.execute(
                    """INSERT INTO bookings
                       (business_id,customer_id,customer_name,first_name,family_name,
                        service_id,date,time,duration_minutes,service_name,worker_id,
                        worker_name,status,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("barber-shop", "+972500000009", name, name, "Test", "haircut",
                     date, time, 30, "תספורת", "omar", "עומר", "confirmed",
                     "2026-09-20T09:00:00+03:00"),
                )

    def tearDown(self):
        self.temp.cleanup()

    def test_range_query_excludes_outside_booking(self):
        result = self.bookings.list_bookings(
            "barber-shop", start_date="2026-09-20", end_date="2026-09-26")
        self.assertEqual([r["customer_name"] for r in result], ["in-sun", "in-wed"])
        self.assertNotIn("out-next", repr(result))

    def test_owner_week_is_sunday_through_saturday(self):
        fixed = datetime(2026, 9, 20, 16, 0,
                         tzinfo=self.bookings.business_timezone("barber-shop"))
        with patch.object(self.agent, "datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fixed
            result = self.agent._run_tool(
                "barber-shop", "+972543161309", "owner_bookings_this_week", {})
        self.assertEqual(result["start_date"], "2026-09-20")
        self.assertEqual(result["end_date"], "2026-09-26")
        self.assertEqual([r["customer_name"] for r in result["bookings"]],
                         ["in-sun", "in-wed"])
        self.assertNotIn("out-next", repr(result))


if __name__ == "__main__":
    unittest.main()
