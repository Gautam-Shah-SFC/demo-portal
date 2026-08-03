import json
import uuid

from common.paths import DEVICES_JSON


def load_devices() -> list[dict]:
    if not DEVICES_JSON.exists():
        DEVICES_JSON.write_text("[]", encoding="utf-8")
    return json.loads(DEVICES_JSON.read_text(encoding="utf-8"))


def save_devices(devices: list[dict]) -> None:
    DEVICES_JSON.write_text(json.dumps(devices, indent=2), encoding="utf-8")


def add_device(name: str, base_url: str, user_email: str, auth_header_value: str,
               auth_header_name: str = "Authorization") -> dict:
    devices = load_devices()
    device = {
        "id": str(uuid.uuid4()),
        "name": name,
        "base_url": base_url.rstrip("/"),
        "user_email": user_email,
        "auth_header_name": auth_header_name,
        "auth_header_value": auth_header_value,
        "active_providers": [],
        "custom_platforms": [],
        "initialized": False,
    }
    devices.append(device)
    save_devices(devices)
    return device


def get_device(device_id: str) -> dict | None:
    for d in load_devices():
        if d["id"] == device_id:
            return d
    return None


def update_device(device_id: str, **fields) -> None:
    devices = load_devices()
    for d in devices:
        if d["id"] == device_id:
            d.update(fields)
            break
    save_devices(devices)


def delete_device(device_id: str) -> None:
    devices = [d for d in load_devices() if d["id"] != device_id]
    save_devices(devices)
