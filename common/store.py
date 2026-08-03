import json
import sqlite3
from datetime import datetime, timezone

from common.paths import INGEST_DB


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(INGEST_DB, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            event_time TEXT NOT NULL,
            provider_key TEXT,
            provider_display TEXT,
            raw_json TEXT NOT NULL
        )
        """
    )
    return conn


def init_db() -> None:
    _conn().close()


def insert_records(records: list[dict]) -> int:
    conn = _conn()
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in records:
        is_conversation = "prompt" in r or "response" in r
        kind = "conversation" if is_conversation else "alert"
        event_time = r.get("captured_at") or r.get("timestamp") or now
        provider_key = r.get("provider")
        provider_display = r.get("provider_display_name") or r.get("provider") or r.get("source_app") or "Unknown"
        rows.append((now, kind, event_time, provider_key, provider_display, json.dumps(r)))
    conn.executemany(
        """
        INSERT INTO events (received_at, kind, event_time, provider_key, provider_display, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()
    return len(rows)


def fetch_events(limit: int = 500) -> list[dict]:
    conn = _conn()
    cur = conn.execute(
        """
        SELECT id, received_at, kind, event_time, provider_key, provider_display, raw_json
        FROM events
        ORDER BY event_time DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    )
    events = []
    for row in cur.fetchall():
        record = json.loads(row[6])
        events.append(
            {
                "id": row[0],
                "received_at": row[1],
                "kind": row[2],
                "event_time": row[3],
                "provider_key": row[4],
                "provider_display": row[5],
                "record": record,
            }
        )
    conn.close()
    return events


def event_count() -> int:
    conn = _conn()
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    return count
