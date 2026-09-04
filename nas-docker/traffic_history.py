"""Persistent rolling traffic totals from host interface byte counters."""

import logging
import os
import sqlite3
import threading
import time

WINDOW_SECONDS = 24 * 60 * 60
SAMPLE_SECONDS = 5
MAX_GAP_SECONDS = 2 * SAMPLE_SECONDS


class TrafficHistory:
    def __init__(self, db_path, sample_fn):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.sample_fn = sample_fn
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS intervals (
                end REAL PRIMARY KEY, start REAL NOT NULL, iface TEXT NOT NULL,
                rx INTEGER NOT NULL, tx INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS baseline (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                ts REAL NOT NULL, iface TEXT NOT NULL, rx INTEGER NOT NULL,
                tx INTEGER NOT NULL, boot_id TEXT NOT NULL
            );
        """)
        self.previous = self.db.execute("SELECT ts, iface, rx, tx, boot_id FROM baseline").fetchone()
        self.latest = self.previous
        self.healthy = False

    def start(self):
        if self.thread is None:
            self._sample()
            self.thread = threading.Thread(target=self._run, name="traffic-history", daemon=True)
            self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join()
        with self.lock:
            self.db.close()

    def _run(self):
        while not self.stop_event.wait(SAMPLE_SECONDS):
            self._sample()

    def _sample(self):
        try:
            sample = self.sample_fn()
            if sample is not None:
                iface, rx, tx, boot_id = sample
                if not iface or not boot_id or not isinstance(rx, int) or not isinstance(tx, int) or min(rx, tx) < 0:
                    raise ValueError("Traffic sample needs an interface, boot ID and nonnegative integer counters")
        except Exception:
            logging.exception("Traffic counter read failed")
            sample = None
        try:
            self._record(sample, time.time())
        except (OSError, sqlite3.Error):
            logging.exception("Traffic history write failed")
            with self.lock:
                self.healthy = False
                self.previous = None

    def _record(self, sample, now):
        with self.lock, self.db:
            self.db.execute("DELETE FROM intervals WHERE end <= ?", (now - WINDOW_SECONDS,))
            if sample is None:
                self.db.execute("DELETE FROM baseline")
                self.previous = None
                self.healthy = False
                return
            iface, rx, tx, boot_id = sample
            previous = self.previous
            last_end = self.db.execute("SELECT MAX(end) FROM intervals").fetchone()[0]
            if (self.latest and now < self.latest[0]) or (last_end is not None and now < last_end):
                # Wall-clock rollback makes interval placement ambiguous.
                self.db.execute("DELETE FROM intervals")
            elif previous:
                ts, old_iface, old_rx, old_tx, old_boot = previous
                if (0 < now - ts <= MAX_GAP_SECONDS and iface == old_iface
                        and boot_id == old_boot and rx >= old_rx and tx >= old_tx):
                    self.db.execute("INSERT INTO intervals VALUES (?, ?, ?, ?, ?)",
                                    (now, ts, iface, rx - old_rx, tx - old_tx))
            self.db.execute("INSERT OR REPLACE INTO baseline VALUES (1, ?, ?, ?, ?, ?)",
                            (now, iface, rx, tx, boot_id))
            self.previous = self.latest = (now, iface, rx, tx, boot_id)
            self.healthy = True

    def snapshot(self):
        now = time.time()
        with self.lock:
            age = now - self.latest[0] if self.latest else None
            result = {
                "rx_bytes": None, "tx_bytes": None, "coverage_seconds": 0,
                "window_seconds": WINDOW_SECONDS, "valid": False,
                "sample_age_seconds": max(0, int(age)) if age is not None else None,
                "iface": self.latest[1] if self.latest else None,
            }
            if not self.latest:
                return result
            end = self.latest[0]
            # ponytail: uniform traffic within the boundary bucket; use finer
            # sampling if sub-minute rolling-window accuracy becomes necessary.
            rx, tx, coverage = self.db.execute("""
                SELECT SUM(rx * (MIN(end, ?) - MAX(start, ?)) / (end - start)),
                       SUM(tx * (MIN(end, ?) - MAX(start, ?)) / (end - start)),
                       SUM(MIN(end, ?) - MAX(start, ?))
                FROM intervals WHERE iface = ? AND end > ? AND start < ?
            """, (end, end - WINDOW_SECONDS, end, end - WINDOW_SECONDS,
                  end, end - WINDOW_SECONDS, self.latest[1], end - WINDOW_SECONDS, end)).fetchone()
            result["coverage_seconds"] = min(WINDOW_SECONDS, int(coverage or 0))
            result["valid"] = bool(self.healthy and 0 <= age <= MAX_GAP_SECONDS and coverage)
            if result["valid"]:
                result["rx_bytes"] = round(rx)
                result["tx_bytes"] = round(tx)
            return result
