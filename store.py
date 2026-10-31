"""Job store for hf serve: the central job log in a SQLite file.

Several threads (one per HTTP request, plus the background worker) read and
write at the same time. A JSON file is not safe for that, SQLite is.
"""

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from hf import TERMINAL_STATUSES

# Columns of the jobs table and their SQLite types. The ones in JSON_FIELDS hold lists or dicts as JSON text.
JOB_COLUMNS = {
    "request_id": "TEXT PRIMARY KEY",
    "model": "TEXT",
    "prompt": "TEXT",
    "payload": "TEXT",
    "usd": "REAL",
    "kind": "TEXT",
    "status": "TEXT",
    "error": "TEXT",
    "result": "TEXT",
    "expires_at": "TEXT",
    "submitted": "TEXT",
    "last_polled": "REAL",
    "stored": "INTEGER NOT NULL DEFAULT 0",
    "files": "TEXT",
    "download_error": "TEXT",
    "download_attempts": "INTEGER NOT NULL DEFAULT 0",
    "next_download": "REAL",
    "idempotency_key": "TEXT UNIQUE",
}
JOB_FIELDS = tuple(JOB_COLUMNS)
JSON_FIELDS = {"payload", "result", "files"}
# Webhook IDs are kept this long for spotting repeated deliveries (Higgsfield retries for up to 2 hours).
WEBHOOK_ID_KEEP_SECONDS = 7 * 24 * 3600
# Fields that describe the state at Higgsfield. Once a job is finished, older states must not overwrite them.
STATUS_FIELDS = ("status", "result", "error", "expires_at")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS jobs ({", ".join(f"{name} {kind}" for name, kind in JOB_COLUMNS.items())});
CREATE TABLE IF NOT EXISTS webhook_events (
    webhook_id TEXT PRIMARY KEY,
    received REAL
);
CREATE TABLE IF NOT EXISTS characters (
    name TEXT PRIMARY KEY COLLATE NOCASE,
    data TEXT NOT NULL
);
"""


class JobStore:
    """All reads and writes go through one connection, protected by one lock."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: we share the connection between threads ourselves (with the lock).
        self._db = sqlite3.connect(path, check_same_thread=False)
        # Row objects can be read like dictionaries: row["status"].
        self._db.row_factory = sqlite3.Row
        # An RLock may be taken again by the thread that already holds it. That matters because
        # wait_until() holds the lock while its check calls get_job(), which takes it too.
        self._lock = threading.RLock()
        # A Condition lets threads sleep until another thread says "something changed".
        self.changed = threading.Condition(self._lock)
        with self._lock:
            # WAL lets readers and the writer work at the same time and survives crashes well.
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            self._add_missing_columns()
            self._db.commit()

    def _add_missing_columns(self) -> None:
        """A database from an older version lacks newer columns: add them (a tiny migration)."""
        existing = {row["name"] for row in self._db.execute("PRAGMA table_info(jobs)")}
        for name, kind in JOB_COLUMNS.items():
            if name not in existing:
                # SQLite cannot add a UNIQUE column later; the uniqueness then comes from an index.
                plain = kind.replace(" UNIQUE", "")
                self._db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {plain}")
                if "UNIQUE" in kind:
                    self._db.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS jobs_{name} ON jobs ({name})")
        webhook_columns = {row["name"] for row in self._db.execute("PRAGMA table_info(webhook_events)")}
        if "received" not in webhook_columns:
            self._db.execute("ALTER TABLE webhook_events ADD COLUMN received REAL")
            # Old IDs without a time count as "received now", so they are pruned a week from now.
            self._db.execute("UPDATE webhook_events SET received = ?", (time.time(),))

    # --- Jobs ---------------------------------------------------------------

    def _decode(self, row: sqlite3.Row) -> dict:
        job = dict(row)
        for field in JSON_FIELDS:
            if job[field] is not None:
                job[field] = json.loads(job[field])
        job["stored"] = bool(job["stored"])
        return job

    def upsert_job(self, request_id: str, **fields) -> dict:
        """Creates or updates a job and returns it. A finished status is never set back."""
        unknown = set(fields) - set(JOB_FIELDS)
        if unknown:
            raise ValueError(f"Unknown job fields: {', '.join(sorted(unknown))}")
        with self._lock:
            job = self.get_job(request_id) or {"request_id": request_id}
            # Answers can arrive out of order (a webhook before the submit response, a slow poll after
            # completion). Once finished, an unfinished state must not overwrite status or result.
            if (
                job.get("status") in TERMINAL_STATUSES
                and "status" in fields
                and fields["status"] not in TERMINAL_STATUSES
            ):
                for field in STATUS_FIELDS:
                    fields.pop(field, None)
            job.update(fields)
            try:
                self._write(job)
            except sqlite3.IntegrityError:
                # Another job already uses this Idempotency-Key: keep both jobs, drop the key on this one.
                job["idempotency_key"] = None
                self._write(job)
            self._db.commit()
            # Wake up everyone waiting in wait_until().
            self.changed.notify_all()
            return self.get_job(request_id)

    def _write(self, job: dict) -> None:
        values = [
            json.dumps(job.get(f)) if f in JSON_FIELDS and job.get(f) is not None else job.get(f) for f in JOB_FIELDS
        ]
        values[JOB_FIELDS.index("stored")] = int(bool(job.get("stored")))
        values[JOB_FIELDS.index("download_attempts")] = job.get("download_attempts") or 0
        columns = ", ".join(JOB_FIELDS)
        placeholders = ", ".join("?" for _ in JOB_FIELDS)
        updates = ", ".join(f"{field} = excluded.{field}" for field in JOB_FIELDS if field != "request_id")
        # A real upsert: update the existing row. ("INSERT OR REPLACE" would delete a row that only
        # shares the Idempotency-Key with this one.) "?" placeholders insert values safely.
        self._db.execute(
            f"INSERT INTO jobs ({columns}) VALUES ({placeholders}) ON CONFLICT(request_id) DO UPDATE SET {updates}",
            values,
        )

    def get_job(self, request_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE request_id = ?", (request_id,)).fetchone()
        return self._decode(row) if row else None

    def find_by_idempotency_key(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE idempotency_key = ?", (key,)).fetchone()
        return self._decode(row) if row else None

    def list_jobs(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM jobs ORDER BY submitted").fetchall()
        return [self._decode(row) for row in rows]

    def open_jobs(self) -> list[dict]:
        """Jobs that are not finished yet (also those without any status)."""
        return [job for job in self.list_jobs() if job["status"] not in TERMINAL_STATUSES]

    def jobs_to_store(self, now: float | None = None) -> list[dict]:
        """Completed jobs whose files still have to be downloaded (and are due, if now is given).

        Trainings have no files. Jobs with download_error were given up for good.
        """
        return [
            job
            for job in self.list_jobs()
            if job["status"] == "completed"
            and job["kind"] != "training"
            and not job["stored"]
            and not job["download_error"]
            and (now is None or job["next_download"] is None or job["next_download"] <= now)
        ]

    # --- Webhooks -----------------------------------------------------------

    def remember_webhook(self, webhook_id: str, now: float | None = None) -> bool:
        """True the first time an ID is seen, False for every repetition. Forgets IDs older than a week."""
        now = time.time() if now is None else now
        with self._lock:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO webhook_events (webhook_id, received) VALUES (?, ?)", (webhook_id, now)
            )
            self._db.execute("DELETE FROM webhook_events WHERE received < ?", (now - WEBHOOK_ID_KEEP_SECONDS,))
            self._db.commit()
        # rowcount is 0 if the row already existed and nothing was inserted.
        return cursor.rowcount == 1

    # --- Characters ---------------------------------------------------------

    def get_characters(self) -> dict[str, dict]:
        with self._lock:
            rows = self._db.execute("SELECT name, data FROM characters ORDER BY name").fetchall()
        return {row["name"]: json.loads(row["data"]) for row in rows}

    def put_character(self, name: str, data: dict) -> dict:
        """Creates or merges a character. The first spelling of the name is kept."""
        with self._lock:
            row = self._db.execute("SELECT name, data FROM characters WHERE name = ?", (name,)).fetchone()
            stored_name = row["name"] if row else name
            merged = {**(json.loads(row["data"]) if row else {}), **data}
            self._db.execute(
                "INSERT OR REPLACE INTO characters (name, data) VALUES (?, ?)", (stored_name, json.dumps(merged))
            )
            self._db.commit()
        return merged

    # --- Waiting ------------------------------------------------------------

    def wait_until(self, predicate: Callable[[], bool], timeout: float) -> bool:
        """Sleeps until predicate() is true (checked after every change) or the time is up."""
        with self.changed:
            return self.changed.wait_for(predicate, timeout)
