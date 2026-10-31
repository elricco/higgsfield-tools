"""Tests for store.py – a real SQLite file in a temporary folder."""

import threading
import time

import pytest

from store import JobStore


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "data" / "hf.db")


def test_upsert_merges_fields_and_decodes_json(store):
    store.upsert_job("r1", model="a/b", payload={"prompt": "a lake"}, usd=0.004, status="queued")
    job = store.upsert_job("r1", status="in_progress", files=["2026-10-31/x.png"])
    assert job["model"] == "a/b"
    assert job["payload"] == {"prompt": "a lake"}
    assert job["usd"] == 0.004
    assert job["status"] == "in_progress"
    assert job["files"] == ["2026-10-31/x.png"]
    assert job["stored"] is False
    assert store.get_job("r1") == job
    assert store.get_job("unknown") is None


def test_terminal_status_is_not_downgraded(store):
    store.upsert_job("r1", status="completed")
    assert store.upsert_job("r1", status="queued")["status"] == "completed"


def test_open_jobs_and_jobs_to_store(store):
    store.upsert_job("open", status="queued")
    store.upsert_job("no-status", model="a/b")
    store.upsert_job("done", status="completed")
    store.upsert_job("stored", status="completed", stored=True)
    store.upsert_job("broken", status="completed", download_error="HTTP 404")
    store.upsert_job("failed", status="failed")
    assert sorted(job["request_id"] for job in store.open_jobs()) == ["no-status", "open"]
    assert [job["request_id"] for job in store.jobs_to_store()] == ["done"]


def test_find_by_idempotency_key(store):
    store.upsert_job("r1", idempotency_key="key-1")
    assert store.find_by_idempotency_key("key-1")["request_id"] == "r1"
    assert store.find_by_idempotency_key("other") is None


def test_remember_webhook_dedupes(store):
    assert store.remember_webhook("msg_1") is True
    assert store.remember_webhook("msg_1") is False


def test_characters_case_insensitive(store):
    store.put_character("Mila", {"id": "ref-1", "model_version": "v2"})
    store.put_character("mila", {"status": "completed"})
    assert store.get_characters() == {"Mila": {"id": "ref-1", "model_version": "v2", "status": "completed"}}


def test_data_survives_reopen(tmp_path):
    JobStore(tmp_path / "hf.db").upsert_job("r1", status="queued")
    assert JobStore(tmp_path / "hf.db").get_job("r1")["status"] == "queued"


def test_list_jobs(store):
    store.upsert_job("r1")
    store.upsert_job("r2")
    assert sorted(job["request_id"] for job in store.list_jobs()) == ["r1", "r2"]


def test_wait_until_wakes_on_change(store):
    store.upsert_job("r1", status="queued")
    threading.Timer(0.1, lambda: store.upsert_job("r1", status="completed")).start()
    started = time.monotonic()
    assert store.wait_until(lambda: store.get_job("r1")["status"] == "completed", 2) is True
    assert time.monotonic() - started < 1


def test_wait_until_times_out(store):
    assert store.wait_until(lambda: False, 0.05) is False


# --- Review fixes -----------------------------------------------------------


def test_stale_update_does_not_overwrite_a_finished_result(store):
    store.upsert_job("r1", status="completed", result={"images": [{"url": "u"}]}, expires_at="2026-11-07T00:00:00Z")
    # A slow poll that started before completion answers late with an older state.
    job = store.upsert_job("r1", status="in_progress", result={"status": "in_progress"}, error=None, expires_at=None)
    assert job["status"] == "completed"
    assert job["result"] == {"images": [{"url": "u"}]}
    assert job["expires_at"] == "2026-11-07T00:00:00Z"


def test_same_idempotency_key_never_deletes_a_job(store):
    store.upsert_job("a", idempotency_key="k", usd=0.4)
    store.upsert_job("b", idempotency_key="k", usd=0.4)
    assert sorted(job["request_id"] for job in store.list_jobs()) == ["a", "b"]
    assert store.find_by_idempotency_key("k")["request_id"] == "a"


def test_jobs_to_store_respects_next_download_and_skips_trainings(store):
    store.upsert_job("now", status="completed")
    store.upsert_job("later", status="completed", next_download=200.0)
    store.upsert_job("training", status="completed", kind="training")
    assert [job["request_id"] for job in store.jobs_to_store(now=100.0)] == ["now"]
    assert sorted(job["request_id"] for job in store.jobs_to_store(now=300.0)) == ["later", "now"]


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3

    path = tmp_path / "hf.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE jobs (request_id TEXT PRIMARY KEY, status TEXT, stored INTEGER NOT NULL DEFAULT 0)")
    old.execute("INSERT INTO jobs (request_id, status) VALUES ('r1', 'completed')")
    old.commit()
    old.close()
    store = JobStore(path)
    assert store.upsert_job("r1", download_attempts=1)["download_attempts"] == 1


def test_old_webhook_ids_are_forgotten(store):
    week = 7 * 24 * 3600
    assert store.remember_webhook("old", now=1000.0) is True
    assert store.remember_webhook("old", now=1000.0 + 60) is False  # a retry within the window is a duplicate
    store.remember_webhook("new", now=1000.0 + week + 1)  # inserting prunes IDs older than the keep window
    count = store._db.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0]
    assert count == 1


def test_old_webhook_table_gets_received_column(tmp_path):
    import sqlite3

    path = tmp_path / "hf.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE webhook_events (webhook_id TEXT PRIMARY KEY)")
    old.execute("INSERT INTO webhook_events (webhook_id) VALUES ('m1')")
    old.commit()
    old.close()
    store = JobStore(path)
    assert store.remember_webhook("m1", now=5.0) is False
    assert store.remember_webhook("m2", now=5.0) is True
