import asyncio
import hashlib
import hmac
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from common.auth import decode_token
from common.crypto import decrypt
from common.custom_apps import to_wire_payload
from common.devices import get_device, mark_connected, mark_disconnected, update_device
from common.servicenow_store import get_connection as get_servicenow_connection
from common.store import init_db, insert_records, upsert_record
from servicenow.scheduler import run_forever as servicenow_scheduler_run

app = FastAPI(title="Demo Portal Ingest")
init_db()

# device_id -> live WebSocket connection, held in-memory (single-worker demo only)
CONNECTIONS: dict[str, WebSocket] = {}

_servicenow_stop_event = asyncio.Event()
_servicenow_task: asyncio.Task | None = None


@app.on_event("startup")
async def _start_servicenow_scheduler():
    global _servicenow_task
    _servicenow_task = asyncio.create_task(servicenow_scheduler_run(_servicenow_stop_event))


@app.on_event("shutdown")
async def _stop_servicenow_scheduler():
    _servicenow_stop_event.set()
    if _servicenow_task:
        await _servicenow_task


@app.post("/api/ingest/activity")
async def ingest_activity(request: Request, authorization: str | None = Header(default=None)):
    payload = await request.json()
    records = payload.get("records", [])
    if not isinstance(records, list):
        return JSONResponse(status_code=400, content={"status": "error", "message": "records must be a list"})

    count = insert_records(records)
    return {"status": "ok", "received": count}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/ingest/servicenow/{connection_id}")
async def servicenow_webhook(connection_id: str, request: Request, x_signature: str | None = Header(default=None)):
    connection = get_servicenow_connection(connection_id)
    if not connection or connection.get("sync_mode") != "webhook":
        return JSONResponse(status_code=404, content={"status": "error", "message": "unknown connection"})

    body = await request.body()
    secret = decrypt(connection.get("webhook_secret_encrypted", ""))
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not secret or not x_signature or not hmac.compare_digest(expected, x_signature):
        return JSONResponse(status_code=401, content={"status": "error", "message": "invalid or missing signature"})

    payload = await request.json()
    external_id = payload.get("external_id")
    if not external_id:
        return JSONResponse(status_code=400, content={"status": "error", "message": "external_id is required"})

    record = {
        "id": external_id,
        "provider": "servicenow",
        "provider_display_name": "ServiceNow",
        "source_subtype": payload.get("source_subtype", "custom_agent"),
        "prompt": payload.get("prompt", ""),
        "response": payload.get("response", ""),
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    # Scoped to this connection, same reasoning as the poll path in
    # servicenow/sync.py -- external_id alone is only unique per instance.
    upsert_record(record, source_type="servicenow", external_id=f"{connection_id}:{external_id}")
    return {"status": "ok"}


def _bearer_user(auth_header: str | None) -> dict | None:
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    return decode_token(token)


@app.websocket("/ws/agent")
async def ws_agent(websocket: WebSocket):
    user = _bearer_user(websocket.headers.get("authorization"))
    if user is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    device_id: str | None = None

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            if msg_type == "hello":
                device_id = msg.get("device_id")
                if not device_id:
                    continue
                CONNECTIONS[device_id] = websocket
                device = mark_connected(device_id, user.get("email"), msg.get("monitored_apps_hash"))

                # Re-assert the last-known desired state so a disconnect/reconnect window
                # never silently drops a config push (see plan's "detect drift" note).
                if device.get("active_providers") or device.get("custom_platforms"):
                    await websocket.send_json({
                        "type": "publish",
                        "request_id": "reconnect-resync",
                        "providers": device.get("active_providers", []),
                        "custom_apps": to_wire_payload(device.get("custom_platforms", [])),
                    })

            elif msg_type == "publish_result":
                if device_id:
                    update_device(device_id, last_publish_result=msg)

            # unrecognized message types are ignored, per the wire contract

    except WebSocketDisconnect:
        pass
    finally:
        if device_id and CONNECTIONS.get(device_id) is websocket:
            del CONNECTIONS[device_id]
            mark_disconnected(device_id)


@app.post("/internal/push/{device_id}")
async def internal_push(device_id: str, payload: dict, request: Request):
    if request.client is None or request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse(status_code=403, content={"status": "error", "message": "internal route, localhost only"})

    ws = CONNECTIONS.get(device_id)
    if ws is None:
        return JSONResponse(status_code=503, content={"status": "error", "message": "device offline"})

    message = {
        "type": "publish",
        "request_id": str(uuid.uuid4()),
        "providers": payload.get("providers", []),
        "custom_apps": payload.get("custom_apps", []),
    }
    await ws.send_json(message)
    return {"status": "sent"}
