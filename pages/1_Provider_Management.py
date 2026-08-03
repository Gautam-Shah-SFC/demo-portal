import re

import streamlit as st

from common.auth import load_users, token_for_email
from common.devices import add_device, delete_device, load_devices, update_device
from common.guard import require_login
from common.providers import PROVIDERS, BROWSER_PROCESS_NAMES
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
    .device-url { color: #9AA0AE; font-size: 0.85rem; }
    .badge-ok { color: #34D399; font-weight: 600; font-size: 0.8rem; }
    .badge-pending { color: #FBBF24; font-weight: 600; font-size: 0.8rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🧩 Provider Management")
st.caption("Pick which AI platforms each device monitors, and push the change live — no reinstall, no rebuild.")

# ---------------------------------------------------------------- add device
with st.expander("➕ Register a new device", expanded=len(load_devices()) == 0):
    users = load_users()
    with st.form("add_device_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Device name", placeholder="Test Device 1")
            base_url = st.text_input("Base URL", placeholder="https://abcd1234.ngrok-free.app")
        with c2:
            email = st.selectbox("Built with user/token", options=list(users.keys()),
                                  format_func=lambda e: f"{users[e]['display_name']} <{e}>")
            auth_header_name = st.text_input("Auth header name", value="Authorization")
        submitted = st.form_submit_button("Register device", type="primary")

    if submitted:
        if not name or not base_url:
            st.error("Device name and base URL are required.")
        else:
            token = token_for_email(email)
            add_device(name, base_url, email, f"Bearer {token}", auth_header_name)
            st.success(f"Registered '{name}'.")
            st.rerun()

st.divider()

# -------------------------------------------------------------- device cards
devices = load_devices()

if not devices:
    st.info("No devices registered yet. Add one above to get started.")

for device in devices:
    st.markdown('<div class="device-card">', unsafe_allow_html=True)

    header_l, header_r = st.columns([4, 1])
    with header_l:
        st.markdown(f'<div class="device-name">{device["name"]}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="device-url">{device["base_url"]} · user: {device["user_email"]}</div>',
                    unsafe_allow_html=True)
        if device.get("initialized"):
            st.markdown('<span class="badge-ok">● live</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="badge-pending">● not yet pushed</span>', unsafe_allow_html=True)
    with header_r:
        if st.button("Remove", key=f"remove_{device['id']}"):
            delete_device(device["id"])
            st.rerun()

    # ---- providers form -------------------------------------------------
    with st.form(key=f"providers_form_{device['id']}"):
        st.markdown("**Fixed providers**")
        cols = st.columns(6)
        checked_fixed = {}
        for i, (key, meta) in enumerate(PROVIDERS.items()):
            with cols[i]:
                checked_fixed[key] = st.checkbox(
                    meta["label"],
                    value=key in device.get("active_providers", []),
                    key=f"prov_{device['id']}_{key}",
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
                        key=f"custom_{device['id']}_{entry['label']}",
                    )

        save = st.form_submit_button("💾 Save & push", type="primary")

    if save:
        providers_list = [k for k, v in checked_fixed.items() if v]
        custom_apps_payload = []
        updated_custom_platforms = []
        for entry in custom_platforms:
            is_on = checked_custom.get(entry["label"], False)
            updated_entry = dict(entry, enabled=is_on)
            updated_custom_platforms.append(updated_entry)
            if is_on:
                if "domain_pattern" in entry:
                    custom_apps_payload.append({
                        "process_name": entry["process_name"],
                        "domain_pattern": entry["domain_pattern"],
                    })
                else:
                    custom_apps_payload.append({
                        "process_name": entry["process_name"],
                        "window_title_pattern": entry.get("window_title_pattern", ""),
                    })

        result = push_config(device, providers_list, custom_apps_payload)
        if result["ok"]:
            update_device(
                device["id"],
                active_providers=providers_list,
                custom_platforms=updated_custom_platforms,
                initialized=True,
            )
            st.toast(f"Pushed to {device['name']}", icon="✅")
            body = result.get("body", {})
            st.success(f"active_providers={body.get('active_providers', providers_list)}  "
                       f"active_custom_apps={body.get('active_custom_apps', custom_apps_payload)}")
            st.rerun()
        else:
            if result.get("network_error"):
                st.error(f"Device unreachable: {result['message']}")
            else:
                st.error(f"HTTP {result.get('status_code', '?')}: {result.get('message')}")

    # ---- add custom platform --------------------------------------------
    with st.expander(f"➕ Add a custom platform for {device['name']}"):
        target_kind = st.radio(
            "Target type", ["Browser site", "Desktop / IDE app"],
            key=f"kind_{device['id']}", horizontal=True,
        )

        with st.form(key=f"add_custom_form_{device['id']}", clear_on_submit=True):
            if target_kind == "Browser site":
                process_name = st.selectbox("Browser", sorted(BROWSER_PROCESS_NAMES),
                                             key=f"browser_{device['id']}")
                domain = st.text_input("Domain", placeholder="example.com", key=f"domain_{device['id']}")
                label = st.text_input("Label (optional)", placeholder="Grok", key=f"label_b_{device['id']}")
                window_title = None
            else:
                process_name = st.text_input("Process name", placeholder="Windsurf.exe",
                                              key=f"proc_{device['id']}")
                window_title = st.text_input("Window title keyword (optional, blank = unrestricted)",
                                              key=f"title_{device['id']}")
                label = st.text_input("Label (optional)", placeholder="Windsurf", key=f"label_d_{device['id']}")
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
                update_device(device["id"], custom_platforms=new_custom_platforms)

                providers_list = device.get("active_providers", [])
                custom_apps_payload = []
                for e in new_custom_platforms:
                    if not e.get("enabled", True):
                        continue
                    if "domain_pattern" in e:
                        custom_apps_payload.append({"process_name": e["process_name"],
                                                     "domain_pattern": e["domain_pattern"]})
                    else:
                        custom_apps_payload.append({"process_name": e["process_name"],
                                                     "window_title_pattern": e.get("window_title_pattern", "")})

                result = push_config(device, providers_list, custom_apps_payload)
                if result["ok"]:
                    update_device(device["id"], initialized=True)
                    st.toast(f"Added '{entry['label']}' and pushed live.", icon="✅")
                else:
                    if result.get("network_error"):
                        st.warning(f"Saved, but device unreachable: {result['message']}")
                    else:
                        st.warning(f"Saved, but push failed: HTTP {result.get('status_code')} — {result.get('message')}")
                st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
