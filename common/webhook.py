import os

import requests

INGEST_PORT = os.environ.get("INGEST_PORT", "8000")
INGEST_INTERNAL_BASE = f"http://127.0.0.1:{INGEST_PORT}"


def push_config(device_id: str, providers: list[str], custom_apps: list[dict]) -> dict:
    url = f"{INGEST_INTERNAL_BASE}/internal/push/{device_id}"
    payload = {"providers": providers, "custom_apps": custom_apps}

    try:
        resp = requests.post(url, json=payload, timeout=5)
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "network_error": True, "message": str(exc)}

    if resp.status_code == 200:
        return {"ok": True, "sent": True}

    if resp.status_code == 503:
        return {"ok": False, "offline": True, "message": "device is not currently connected"}

    try:
        body = resp.json()
    except ValueError:
        body = {}
    return {"ok": False, "status_code": resp.status_code, "message": body.get("message", f"HTTP {resp.status_code}")}
