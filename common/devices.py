import json
from datetime import datetime, timezone

from common.paths import DEVICES_JSON


def load_devices() -> list[dict]:
    if not DEVICES_JSON.exists():
        DEVICES_JSON.write_text("[]", encoding="utf-8")
    return json.loads(DEVICES_JSON.read_text(encoding="utf-8"))


def save_devices(devices: list[dict]) -> None:
    DEVICES_JSON.write_text(json.dumps(devices, indent=2), encoding="utf-8")


def get_device(device_id: str) -> dict | None:
    for d in load_devices():
        if d["device_id"] == device_id:
            return d
    return None


def get_or_create_device(device_id: str, user_email: str | None) -> dict:
    devices = load_devices()
    for d in devices:
        if d["device_id"] == device_id:
            return d
    device = {
        "device_id": device_id,
        "name": device_id,
        "user_email": user_email,
        "connected": False,
        "last_seen": None,
        "last_hello_hash": None,
        "active_providers": [],
        "custom_platforms": [],
        "last_publish_result": None,
    }
    devices.append(device)
    save_devices(devices)
    return device


def update_device(device_id: str, **fields) -> None:
    devices = load_devices()
    for d in devices:
        if d["device_id"] == device_id:
            d.update(fields)
            break
    save_devices(devices)


def delete_device(device_id: str) -> None:
    devices = [d for d in load_devices() if d["device_id"] != device_id]
    save_devices(devices)


def mark_connected(device_id: str, user_email: str | None, hello_hash: str | None) -> dict:
    device = get_or_create_device(device_id, user_email)
    update_device(
        device_id,
        user_email=user_email or device.get("user_email"),
        connected=True,
        last_seen=datetime.now(timezone.utc).isoformat(),
        last_hello_hash=hello_hash,
    )
    return get_device(device_id)


def mark_disconnected(device_id: str) -> None:
    update_device(device_id, connected=False, last_seen=datetime.now(timezone.utc).isoformat())
