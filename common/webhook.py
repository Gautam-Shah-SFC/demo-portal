import requests


def push_config(device: dict, providers: list[str], custom_apps: list[dict]) -> dict:
    path = "/initialize" if not device.get("initialized") else "/publish"
    url = device["base_url"].rstrip("/") + path
    headers = {
        device.get("auth_header_name", "Authorization"): device.get("auth_header_value", ""),
        "Content-Type": "application/json",
    }
    payload = {"providers": providers, "custom_apps": custom_apps}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "network_error": True, "message": str(exc)}

    try:
        body = resp.json()
    except ValueError:
        body = {}

    if resp.status_code == 200:
        return {"ok": True, "status_code": 200, "body": body}

    message = body.get("message") if isinstance(body, dict) else None
    if resp.status_code == 401:
        message = message or "unauthorized"
    elif resp.status_code == 404:
        message = message or "not found"

    return {"ok": False, "status_code": resp.status_code, "message": message or f"HTTP {resp.status_code}"}
