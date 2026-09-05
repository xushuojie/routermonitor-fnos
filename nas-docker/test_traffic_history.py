"""Run with: python3 -m unittest discover -s nas-docker -p 'test_*.py'."""

import tempfile
import sqlite3
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from traffic_history import TrafficHistory


class TrafficHistoryTests(unittest.TestCase):
    @patch("traffic_history.WINDOW_SECONDS", 60)
    def test_window_restart_gaps_and_resets(self):
        window = 60
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "traffic.sqlite3")
            history = TrafficHistory(db_path, lambda: None)
            with patch("traffic_history.time.time", return_value=0):
                self.assertIsNone(history.snapshot()["rx_bytes"])
                history._record(("eth0", 100, 200, "boot-a"), 0)
                self.assertFalse(history.snapshot()["valid"])
            for ts in range(5, window + 6, 5):
                history._record(("eth0", 100 + ts * 3, 200 + ts * 2, "boot-a"), ts)
            with patch("traffic_history.time.time", return_value=window + 5):
                result = history.snapshot()
                self.assertEqual((result["rx_bytes"], result["tx_bytes"]),
                                 (window * 3, window * 2))
                self.assertEqual(result["coverage_seconds"], window)
            history.close()

            history = TrafficHistory(db_path, lambda: None)
            # Baseline persists; the same interface and boot can bridge a short restart.
            ts = window + 10
            history._record(("eth0", 100 + ts * 3, 200 + ts * 2, "boot-a"), ts)
            with patch("traffic_history.time.time", return_value=ts + 5):
                result = history.snapshot()
                self.assertEqual(result["rx_bytes"], window * 3)
                self.assertEqual(result["coverage_seconds"], window)
                self.assertEqual(result["sample_age_seconds"], 5)

            # A failed sample immediately invalidates totals and breaks the delta.
            history._record(None, ts + 5)
            with patch("traffic_history.time.time", return_value=ts + 5):
                self.assertIsNone(history.snapshot()["rx_bytes"])
            history._record(("eth0", 10**9, 10**9, "boot-a"), ts + 10)
            with patch("traffic_history.time.time", return_value=ts + 10):
                result = history.snapshot()
                self.assertEqual(result["coverage_seconds"], window - 10)
                self.assertEqual(result["rx_bytes"], (window - 10) * 3)

            # A new interface never inherits another interface's traffic.
            history._record(("eth1", 100, 100, "boot-a"), ts + 15)
            with patch("traffic_history.time.time", return_value=ts + 15):
                self.assertIsNone(history.snapshot()["rx_bytes"])
            history._record(("eth1", 200, 300, "boot-a"), ts + 20)
            with patch("traffic_history.time.time", return_value=ts + 20):
                self.assertEqual(history.snapshot()["tx_bytes"], 200)
            # Counter rollback and boot changes skip their entire intervals.
            history._record(("eth1", 10, 20, "boot-a"), ts + 25)
            history._record(("eth1", 1000, 2000, "boot-b"), ts + 30)
            history._record(("eth1", 10000, 20000, "boot-b"), ts + 50)
            with patch("traffic_history.time.time", return_value=ts + 50):
                result = history.snapshot()
                self.assertEqual(result["coverage_seconds"], 5)
                self.assertEqual(result["tx_bytes"], 200)
            with patch("traffic_history.time.time", return_value=ts + 61):
                self.assertFalse(history.snapshot()["valid"])
                self.assertIsNone(history.snapshot()["tx_bytes"])
            # A backwards clock cannot make overlapping histories count twice.
            history._record(("eth1", 10100, 20200, "boot-b"), ts + 40)
            with patch("traffic_history.time.time", return_value=ts + 40):
                self.assertEqual(history.snapshot()["coverage_seconds"], 0)
            history._record(("eth1", 10100, 20200, "boot-b"), ts + 45)
            with patch("traffic_history.time.time", return_value=ts + 45):
                self.assertTrue(history.snapshot()["valid"])
                self.assertEqual(history.snapshot()["tx_bytes"], 0)
            history.close()

    def test_background_sampler_does_not_need_http_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            sampled = threading.Event()
            calls = []

            def sample():
                calls.append(1)
                if len(calls) >= 2:
                    sampled.set()
                return "eth0", 100 * len(calls), 200 * len(calls), "boot"

            history = TrafficHistory(str(Path(directory) / "traffic.sqlite3"), sample)
            with patch("traffic_history.SAMPLE_SECONDS", 0.01):
                history.start()
                self.assertTrue(sampled.wait(1), "background sampling stopped without HTTP requests")
            history.close()

    def test_legacy_migration_per_interface_resets_restart_and_cached_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "traffic.sqlite3")
            with sqlite3.connect(db_path) as old_db:
                old_db.executescript("""
                    CREATE TABLE intervals (end REAL PRIMARY KEY, start REAL NOT NULL,
                                            iface TEXT NOT NULL, rx INTEGER NOT NULL, tx INTEGER NOT NULL);
                    CREATE TABLE baseline (id INTEGER PRIMARY KEY CHECK (id = 1), ts REAL NOT NULL,
                                           iface TEXT NOT NULL, rx INTEGER NOT NULL, tx INTEGER NOT NULL,
                                           boot_id TEXT NOT NULL);
                    INSERT INTO intervals VALUES (5, 0, 'physical:eth0,eth1', 100, 100);
                    INSERT INTO baseline VALUES (1, 5, 'physical:eth0,eth1', 2000, 2000, 'boot');
                """)

            def sample(first, second, epoch="100"):
                return ("physical:eth0,eth1", first + second, first + second, "boot",
                        {"epoch": epoch, "values": {"eth0": [first, first], "eth1": [second, second]}})

            history = TrafficHistory(db_path, lambda: None)
            self.assertEqual(history.db.execute("SELECT COUNT(*) FROM intervals").fetchone()[0], 1)
            history._record(sample(1000, 1000), 10)
            self.assertEqual(history.db.execute("SELECT COUNT(*) FROM intervals").fetchone()[0], 1)
            history._record(sample(1100, 1100), 15)
            history.close()
            history = TrafficHistory(db_path, lambda: None)
            self.assertEqual(history.previous[5], sample(1100, 1100)[4])
            history._record(sample(1200, 1200), 20)
            # Even a constant epoch cannot hide a reset beneath increasing totals.
            history._record(sample(0, 3000), 25)
            history._record(sample(100, 3100), 30)
            # A faster /net read can detect a reset and recovery between history samples.
            history._record(sample(200, 3200, "101"), 35)
            history._record(sample(300, 3300, "101"), 40)
            queries = []
            history.db.set_trace_callback(queries.append)
            with patch("traffic_history.time.time", return_value=40):
                for _ in range(3):
                    result = history.snapshot()
                    self.assertEqual((result["rx_bytes"], result["coverage_seconds"]), (900, 25))
            with patch("traffic_history.time.time", return_value=51):
                self.assertFalse(history.snapshot()["valid"])
            self.assertEqual(sum("SELECT SUM" in query for query in queries), 1)
            history._record(sample(400, 3400, "101"), 45)
            with patch("traffic_history.time.time", return_value=45):
                self.assertEqual(history.snapshot()["rx_bytes"], 1100)
            self.assertEqual(sum("SELECT SUM" in query for query in queries), 2)
            history.close()


if __name__ == "__main__":
    unittest.main()
