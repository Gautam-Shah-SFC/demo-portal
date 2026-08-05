def to_wire_payload(custom_platforms: list[dict]) -> list[dict]:
    payload = []
    for entry in custom_platforms:
        if not entry.get("enabled", True):
            continue
        if "domain_pattern" in entry:
            payload.append({"process_name": entry["process_name"], "domain_pattern": entry["domain_pattern"]})
        else:
            payload.append({
                "process_name": entry["process_name"],
                "window_title_pattern": entry.get("window_title_pattern", ""),
            })
    return payload
