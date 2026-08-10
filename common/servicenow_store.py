import json
import uuid
from datetime import datetime, timezone

from common.paths import SERVICENOW_CONNECTIONS_JSON

DEFAULTS = {
    "tenant_id": "default",
    "instance_url": "",
    "auth_type": "basic",  # "basic" | "oauth2"
    "client_id": "",
    "client_secret_encrypted": "",
    "username": "",
    "password_encrypted": "",
    "refresh_token_encrypted": "",
    "tables_enabled": [],
    "sync_mode": "poll",  # "poll" | "webhook"
    "poll_interval_sec": 60,
    "webhook_secret_encrypted": "",
    "last_sync_watermark": {},
    "status": "paused",  # "active" | "error" | "paused"
    "last_error": None,
    "last_synced_at": None,
}


def load_connections() -> list[dict]:
    if not SERVICENOW_CONNECTIONS_JSON.exists():
        SERVICENOW_CONNECTIONS_JSON.write_text("[]", encoding="utf-8")
    return json.loads(SERVICENOW_CONNECTIONS_JSON.read_text(encoding="utf-8"))


def save_connections(connections: list[dict]) -> None:
    SERVICENOW_CONNECTIONS_JSON.write_text(json.dumps(connections, indent=2), encoding="utf-8")


def list_connections() -> list[dict]:
    return load_connections()


def get_connection(connection_id: str) -> dict | None:
    for c in load_connections():
        if c["id"] == connection_id:
            return c
    return None


def create_connection(**fields) -> dict:
    connections = load_connections()
    connection = {"id": str(uuid.uuid4()), **DEFAULTS, **fields}
    connections.append(connection)
    save_connections(connections)
    return connection


def update_connection(connection_id: str, **fields) -> None:
    connections = load_connections()
    for c in connections:
        if c["id"] == connection_id:
            c.update(fields)
            break
    save_connections(connections)


def delete_connection(connection_id: str) -> None:
    connections = [c for c in load_connections() if c["id"] != connection_id]
    save_connections(connections)


def set_status(connection_id: str, status: str, last_error: str | None = None) -> None:
    update_connection(connection_id, status=status, last_error=last_error)


def set_watermark(connection_id: str, table: str, value: str) -> None:
    connection = get_connection(connection_id)
    if not connection:
        return
    watermark = dict(connection.get("last_sync_watermark") or {})
    watermark[table] = value
    update_connection(connection_id, last_sync_watermark=watermark)


def touch_synced(connection_id: str) -> None:
    update_connection(connection_id, last_synced_at=datetime.now(timezone.utc).isoformat())
