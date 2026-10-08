import json
import sqlite3
import threading
from datetime import datetime, timezone

from config import DB_PATH

_lock = threading.Lock()


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with _lock:
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    started_at TEXT,
                    finished_at TEXT,
                    status TEXT,
                    summary_json TEXT
                );
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    record_id TEXT,
                    source TEXT,
                    name TEXT,
                    label TEXT,
                    score INTEGER,
                    status TEXT,
                    input_json TEXT,
                    result_json TEXT,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS crm_inbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT UNIQUE,
                    run_id TEXT,
                    record_id TEXT,
                    source TEXT,
                    payload_json TEXT,
                    received_at TEXT
                );
                CREATE TABLE IF NOT EXISTS failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    stage TEXT,
                    ref TEXT,
                    message TEXT,
                    created_at TEXT
                );
                """
            )
            conn.commit()
        finally:
            conn.close()


def _write(fn):
    with _lock:
        conn = _connect()
        try:
            result = fn(conn)
            conn.commit()
            return result
        finally:
            conn.close()


def _read(fn):
    with _lock:
        conn = _connect()
        try:
            return fn(conn)
        finally:
            conn.close()


def start_run(run_id):
    def fn(conn):
        conn.execute(
            "INSERT INTO runs (id, started_at, finished_at, status, summary_json) VALUES (?, ?, NULL, ?, ?)",
            (run_id, now(), "running", "{}"),
        )

    _write(fn)


def finish_run(run_id, status, summary):
    def fn(conn):
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, summary_json = ? WHERE id = ?",
            (now(), status, json.dumps(summary), run_id),
        )

    _write(fn)


def save_record(run_id, record, result, status):
    qual = (result or {}).get("qualification") or {}

    def fn(conn):
        conn.execute(
            """
            INSERT INTO records
            (run_id, record_id, source, name, label, score, status, input_json, result_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                record.get("id"),
                record.get("source"),
                record.get("name"),
                qual.get("label"),
                qual.get("score"),
                status,
                json.dumps(record),
                json.dumps(result or {}),
                now(),
            ),
        )

    _write(fn)


def save_failure(run_id, stage, ref, message):
    def fn(conn):
        conn.execute(
            "INSERT INTO failures (run_id, stage, ref, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, stage, ref, message, now()),
        )

    _write(fn)


def save_crm(payload):
    def fn(conn):
        try:
            cur = conn.execute(
                """
                INSERT INTO crm_inbox
                (idempotency_key, run_id, record_id, source, payload_json, received_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["idempotency_key"],
                    payload.get("run_id"),
                    payload.get("record_id"),
                    payload.get("source"),
                    json.dumps(payload),
                    now(),
                ),
            )
            return {"crm_id": cur.lastrowid, "duplicate": False}
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT id FROM crm_inbox WHERE idempotency_key = ?",
                (payload["idempotency_key"],),
            ).fetchone()
            return {"crm_id": row["id"] if row else None, "duplicate": True}

    return _write(fn)


def latest_run():
    def fn(conn):
        row = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        data = dict(row)
        data["summary"] = json.loads(data.pop("summary_json") or "{}")
        return data

    return _read(fn)


def records_for(run_id):
    def fn(conn):
        rows = conn.execute(
            "SELECT * FROM records WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["input"] = json.loads(item.pop("input_json") or "{}")
            item["result"] = json.loads(item.pop("result_json") or "{}")
            out.append(item)
        return out

    return _read(fn)


def failures_for(run_id):
    def fn(conn):
        rows = conn.execute(
            "SELECT * FROM failures WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    return _read(fn)


def crm_for(run_id):
    def fn(conn):
        rows = conn.execute(
            "SELECT * FROM crm_inbox WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            out.append(item)
        return out

    return _read(fn)
