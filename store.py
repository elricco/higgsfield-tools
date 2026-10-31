"""Job store for hf serve: the central job log in a SQLite file.

Several threads (one per HTTP request, plus the background worker) read and
write at the same time. A JSON file is not safe for that, SQLite is.
"""

import json
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

from hf import TERMINAL_STATUSES

# Columns of the jobs table. The ones in JSON_FIELDS hold lists or dicts as JSON text.
JOB_FIELDS = (
    "request_id",
    "model",
    "prompt",
    "payload",
    "usd",
    "kind",
    "status",
    "error",
    "result",
    "expires_at",
    "submitted",
    "last_polled",
    "stored",
    "files",
    "download_error",
    "idempotency_key",
)
JSON_FIELDS = {"payload", "result", "files"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    request_id TEXT PRIMARY KEY,
    model TEXT,
    prompt TEXT,
    payload TEXT,
    usd REAL,
    kind TEXT,
    status TEXT,
    error TEXT,
    result TEXT,
    expires_at TEXT,
    submitted TEXT,
    last_polled REAL,
    stored INTEGER NOT NULL DEFAULT 0,
    files TEXT,
    download_error TEXT,
    idempotency_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS webhook_events (
    webhook_id TEXT PRIMARY KEY
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
            self._db.commit()

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
            # A webhook can arrive before the submit response is saved: never go back from "completed" to "queued".
            if job.get("status") in TERMINAL_STATUSES and fields.get("status") not in TERMINAL_STATUSES:
                fields.pop("status", None)
            job.update(fields)
            values = [
                json.dumps(job.get(f)) if f in JSON_FIELDS and job.get(f) is not None else job.get(f)
                for f in JOB_FIELDS
            ]
            values[JOB_FIELDS.index("stored")] = int(bool(job.get("stored")))
            placeholders = ", ".join("?" for _ in JOB_FIELDS)
            # "?" placeholders let sqlite3 insert the values safely – never build SQL from text yourself.
            self._db.execute(f"INSERT OR REPLACE INTO jobs ({', '.join(JOB_FIELDS)}) VALUES ({placeholders})", values)
            self._db.commit()
            # Wake up everyone waiting in wait_until().
            self.changed.notify_all()
            return self.get_job(request_id)

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

    def jobs_to_store(self) -> list[dict]:
        """Completed jobs whose files are not downloaded yet (and did not fail to download)."""
        return [
            job
            for job in self.list_jobs()
            if job["status"] == "completed" and not job["stored"] and not job["download_error"]
        ]

    # --- Webhooks -----------------------------------------------------------

    def remember_webhook(self, webhook_id: str) -> bool:
        """True the first time an ID is seen, False for every repetition."""
        with self._lock:
            cursor = self._db.execute("INSERT OR IGNORE INTO webhook_events (webhook_id) VALUES (?)", (webhook_id,))
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
