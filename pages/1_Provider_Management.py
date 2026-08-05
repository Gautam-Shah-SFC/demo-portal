import re

import streamlit as st

from common.custom_apps import to_wire_payload
from common.devices import delete_device, load_devices, update_device
from common.guard import require_login
from common.providers import PROVIDERS, BROWSER_PROCESS_NAMES
from common.timeutil import relative_time
from common.webhook import push_config

st.set_page_config(page_title="Provider Management", page_icon="🧩", layout="wide")
require_login()

st.markdown(
    """
    <style>
    div[data-testid="stForm"] {
        background: #181B27;
        border: 1px solid rgba(139,124,255,0.18);
        border-radius: 14px;
        padding: 1.2rem 1.4rem;
    }
    .device-card {
        border: 1px solid rgba(139,124,255,0.25);
        border-radius: 16px;
        padding: 1.2rem 1.4rem;
        margin-bottom: 1.2rem;
        background: linear-gradient(180deg, rgba(139,124,255,0.06) 0%, rgba(24,27,39,0) 100%);
    }
    .device-name { font-size: 1.15rem; font-weight: 700; }
    .device-meta { color: #9AA0AE; font-size: 0.85rem; }
    .badge-ok { color: #34D399; font-weight: 600; font-size: 0.8rem; }
    .badge-off { color: #6B7280; font-weight: 600; font-size: 0.8rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🧩 Provider Management")
st.caption("Pick which AI platforms each device monitors, and push the change live over its WebSocket connection.")
st.info(
    "Devices register themselves automatically the first time they connect — there's no "
    "manual 'add device' step. Point a device's `ws_url` at `wss://<this portal>/ws/agent` "
    "with the token from the login page, and it'll show up here once connected.",
    icon="🔌",
)

devices = load_devices()

if not devices:
    st.warning("No devices have connected yet.")

for device in devices:
    device_id = device["device_id"]
    st.markdown('<div class="device-card">', unsafe_allow_html=True)

    header_l, header_r = st.columns([4, 1])
    with header_l:
        st.markdown(f'<div class="device-name">{device.get("name") or device_id}</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="device-meta">device_id: {device_id} · user: {device.get("user_email") or "—"}</div>',
            unsafe_allow_html=True,
        )
        if device.get("connected"):
            st.markdown('<span class="badge-ok">🟢 connected</span>', unsafe_allow_html=True)
        else:
            last_seen = f" · last seen {relative_time(device['last_seen'])}" if device.get("last_seen") else ""
            st.markdown(f'<span class="badge-off">⚫ offline{last_seen}</span>', unsafe_allow_html=True)

        result = device.get("last_publish_result")
        if result:
            if result.get("status") == "ok":
                st.caption(f"✅ Last confirmed by device: active_providers={result.get('active_providers')}")
            else:
                st.caption(f"⚠️ Device rejected last push: {result.get('message')}")
    with header_r:
        if st.button("Forget device", key=f"remove_{device_id}"):
            delete_device(device_id)
            st.rerun()

    with st.expander("Rename"):
        with st.form(key=f"rename_form_{device_id}"):
            new_name = st.text_input("Display name", value=device.get("name") or device_id)
            if st.form_submit_button("Save name"):
                update_device(device_id, name=new_name.strip() or device_id)
                st.rerun()

    # ---- providers form -------------------------------------------------
    with st.form(key=f"providers_form_{device_id}"):
        st.markdown("**Fixed providers**")
        cols = st.columns(6)
        checked_fixed = {}
        for i, (key, meta) in enumerate(PROVIDERS.items()):
            with cols[i]:
                checked_fixed[key] = st.checkbox(
                    meta["label"],
                    value=key in device.get("active_providers", []),
                    key=f"prov_{device_id}_{key}",
                )

        checked_custom = {}
        custom_platforms = device.get("custom_platforms", [])
        if custom_platforms:
            st.markdown("**Custom platforms**")
            ccols = st.columns(min(len(custom_platforms), 6) or 1)
            for i, entry in enumerate(custom_platforms):
                with ccols[i % len(ccols)]:
                    checked_custom[entry["label"]] = st.checkbox(
                        entry["label"],
                        value=entry.get("enabled", True),
                        key=f"custom_{device_id}_{entry['label']}",
                    )

        save = st.form_submit_button("💾 Save & push", type="primary")

    if save:
        providers_list = [k for k, v in checked_fixed.items() if v]
        updated_custom_platforms = [
            dict(entry, enabled=checked_custom.get(entry["label"], False)) for entry in custom_platforms
        ]
        custom_apps_payload = to_wire_payload(updated_custom_platforms)

        # Persist the desired state regardless of connectivity — the WS handler
        # re-asserts it automatically on this device's next `hello`.
        update_device(
            device_id,
            active_providers=providers_list,
            custom_platforms=updated_custom_platforms,
        )

        result = push_config(device_id, providers_list, custom_apps_payload)
        if result.get("ok"):
            st.toast(f"Pushed to {device.get('name') or device_id}", icon="✅")
            st.success("Sent — waiting for the device's publish_result to confirm.")
        elif result.get("offline"):
            st.warning("Device is offline. Saved — it'll be applied automatically the next time it connects.")
        elif result.get("network_error"):
            st.error(f"Couldn't reach the ingest process: {result['message']}")
        else:
            st.error(f"HTTP {result.get('status_code', '?')}: {result.get('message')}")
        st.rerun()

    # ---- add custom platform --------------------------------------------
    with st.expander(f"➕ Add a custom platform for {device.get('name') or device_id}"):
        target_kind = st.radio(
            "Target type", ["Browser site", "Desktop / IDE app"],
            key=f"kind_{device_id}", horizontal=True,
        )

        with st.form(key=f"add_custom_form_{device_id}", clear_on_submit=True):
            if target_kind == "Browser site":
                process_name = st.selectbox("Browser", sorted(BROWSER_PROCESS_NAMES),
                                             key=f"browser_{device_id}")
                domain = st.text_input("Domain", placeholder="example.com", key=f"domain_{device_id}")
                label = st.text_input("Label (optional)", placeholder="Grok", key=f"label_b_{device_id}")
                window_title = None
            else:
                process_name = st.text_input("Process name", placeholder="Windsurf.exe",
                                              key=f"proc_{device_id}")
                window_title = st.text_input("Window title keyword (optional, blank = unrestricted)",
                                              key=f"title_{device_id}")
                label = st.text_input("Label (optional)", placeholder="Windsurf", key=f"label_d_{device_id}")
                domain = None

            add_custom = st.form_submit_button("Add platform", type="primary")

        if add_custom:
            error = None
            entry = {"enabled": True}
            if target_kind == "Browser site":
                if not domain:
                    error = "Domain is required for a browser entry."
                else:
                    pattern = domain.replace(".", r"\.")
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        error = f"Invalid domain pattern: {exc}"
                    entry.update({
                        "process_name": process_name,
                        "domain_pattern": pattern,
                        "label": label.strip() or domain,
                    })
            else:
                if not process_name:
                    error = "Process name is required for a desktop/IDE entry."
                else:
                    try:
                        re.compile(window_title or "")
                    except re.error as exc:
                        error = f"Invalid window title pattern: {exc}"
                    entry.update({
                        "process_name": process_name,
                        "window_title_pattern": window_title or "",
                        "label": label.strip() or process_name,
                    })

            if error:
                st.error(error)
            else:
                new_custom_platforms = device.get("custom_platforms", []) + [entry]
                providers_list = device.get("active_providers", [])
                update_device(device_id, custom_platforms=new_custom_platforms)

                custom_apps_payload = to_wire_payload(new_custom_platforms)
                result = push_config(device_id, providers_list, custom_apps_payload)
                if result.get("ok"):
                    st.toast(f"Added '{entry['label']}' and pushed live.", icon="✅")
                elif result.get("offline"):
                    st.warning(f"Saved '{entry['label']}' — device is offline, will apply on reconnect.")
                elif result.get("network_error"):
                    st.warning(f"Saved, but couldn't reach the ingest process: {result['message']}")
                else:
                    st.warning(f"Saved, but push failed: HTTP {result.get('status_code')} — {result.get('message')}")
                st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
