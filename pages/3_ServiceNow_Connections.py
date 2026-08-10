import secrets

import requests
import streamlit as st

from common.crypto import decrypt, encrypt
from common.guard import require_login
from common.servicenow_store import create_connection, delete_connection, list_connections, update_connection
from common.timeutil import relative_time
from servicenow.mappers import KNOWN_TABLE_LABELS, discover_table_schema
from servicenow.sync import sync_connection

st.set_page_config(page_title="ServiceNow Connections", page_icon="🔗", layout="wide")
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
    .conn-card {
        border: 1px solid rgba(46,125,50,0.3);
        border-radius: 16px;
        padding: 1.2rem 1.4rem;
        margin-bottom: 1.2rem;
        background: linear-gradient(180deg, rgba(46,125,50,0.06) 0%, rgba(24,27,39,0) 100%);
    }
    .conn-name { font-size: 1.1rem; font-weight: 700; }
    .conn-meta { color: #9AA0AE; font-size: 0.85rem; }
    .badge-active { color: #34D399; font-weight: 600; font-size: 0.8rem; }
    .badge-error { color: #EF4444; font-weight: 600; font-size: 0.8rem; }
    .badge-paused { color: #6B7280; font-weight: 600; font-size: 0.8rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🔗 ServiceNow Connections")
st.caption(
    "Pull prompt/response logs from ServiceNow's AI features (Now Assist, Virtual Agent, custom "
    "agents) into the same Activity Logs feed used by the endpoint agent. Only prompt and response "
    "text are ever fetched or stored — see servicenow_Design.md §1/§7 for the full data-minimization scope."
)


def _ngrok_public_url() -> str | None:
    try:
        resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=1)
        for t in resp.json().get("tunnels", []):
            if t.get("proto") == "https":
                return t["public_url"]
    except Exception:
        return None
    return None


# ---- add connection -------------------------------------------------------
connections = list_connections()
with st.expander("➕ Add a new connection", expanded=not connections):
    with st.form("add_connection_form"):
        instance_url = st.text_input("Instance URL", placeholder="https://your-instance.service-now.com")
        auth_type = st.radio("Auth type", ["basic", "oauth2"], format_func=lambda v: {
            "basic": "Basic (username/password)",
            "oauth2": "OAuth2 (password grant — no redirect URI needed)",
        }[v], horizontal=True)

        if auth_type == "basic":
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            client_id, client_secret = "", ""
        else:
            client_id = st.text_input("OAuth Client ID")
            client_secret = st.text_input("OAuth Client Secret", type="password")
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")

        tables_enabled = st.multiselect(
            "Tables to sync", options=list(KNOWN_TABLE_LABELS.keys()),
            format_func=lambda t: KNOWN_TABLE_LABELS[t],
        )
        sync_mode = st.radio("Sync mode", ["poll", "webhook"], format_func=lambda v: {
            "poll": "Poll (default — no customer-side setup beyond API access)",
            "webhook": "Webhook (lower latency — requires a Business Rule on their instance)",
        }[v], horizontal=True)
        poll_interval_sec = st.number_input("Poll interval (seconds)", min_value=15, value=60, step=15) \
            if sync_mode == "poll" else 60

        submitted = st.form_submit_button("Create connection", type="primary")

    if submitted:
        if not instance_url:
            st.error("Instance URL is required.")
        elif not tables_enabled:
            st.error("Select at least one table to sync.")
        else:
            fields = {
                "instance_url": instance_url.rstrip("/"),
                "auth_type": auth_type,
                "username": username,
                "password_encrypted": encrypt(password),
                "client_id": client_id,
                "client_secret_encrypted": encrypt(client_secret) if client_secret else "",
                "tables_enabled": tables_enabled,
                "sync_mode": sync_mode,
                "poll_interval_sec": int(poll_interval_sec),
                "status": "paused",  # admin reviews a successful "Test connection" before enabling
            }
            if sync_mode == "webhook":
                fields["webhook_secret_encrypted"] = encrypt(secrets.token_hex(32))
            connection = create_connection(**fields)
            st.success(f"Connection created ({connection['id'][:8]}…). Test it, then resume to start syncing.")
            st.rerun()

st.divider()

# ---- list connections -------------------------------------------------------
if not connections:
    st.info("No ServiceNow connections configured yet.")

for connection in connections:
    cid = connection["id"]
    st.markdown('<div class="conn-card">', unsafe_allow_html=True)

    header_l, header_r = st.columns([4, 1])
    with header_l:
        st.markdown(f'<div class="conn-name">{connection["instance_url"] or "(no URL set)"}</div>', unsafe_allow_html=True)
        table_labels = ", ".join(KNOWN_TABLE_LABELS.get(t, t) for t in connection.get("tables_enabled", []))
        st.markdown(
            f'<div class="conn-meta">auth: {connection.get("auth_type")} · mode: {connection.get("sync_mode")} · '
            f'tables: {table_labels or "none"}</div>',
            unsafe_allow_html=True,
        )
        status = connection.get("status", "paused")
        badge_class = {"active": "badge-active", "error": "badge-error", "paused": "badge-paused"}.get(status, "badge-paused")
        badge_icon = {"active": "🟢", "error": "🔴", "paused": "⏸️"}.get(status, "⏸️")
        last_synced = f" · last synced {relative_time(connection['last_synced_at'])}" if connection.get("last_synced_at") else " · never synced"
        st.markdown(f'<span class="{badge_class}">{badge_icon} {status}{last_synced}</span>', unsafe_allow_html=True)
        if connection.get("last_error"):
            st.caption(f"⚠️ {connection['last_error']}")
    with header_r:
        if st.button("Delete", key=f"delete_{cid}"):
            delete_connection(cid)
            st.rerun()

    btn_cols = st.columns(4)
    with btn_cols[0]:
        if status == "paused":
            if st.button("▶️ Resume", key=f"resume_{cid}"):
                update_connection(cid, status="active", last_error=None)
                st.rerun()
        else:
            if st.button("⏸️ Pause", key=f"pause_{cid}"):
                update_connection(cid, status="paused")
                st.rerun()
    with btn_cols[1]:
        if st.button("🔍 Test connection", key=f"test_{cid}"):
            results = {}
            for table in connection.get("tables_enabled", []):
                try:
                    results[table] = discover_table_schema(connection, table)
                except Exception as e:
                    results[table] = {"exists": False, "error": str(e)}
            st.session_state[f"test_result_{cid}"] = results
    with btn_cols[2]:
        if connection.get("sync_mode") == "poll" and st.button("🔄 Sync now", key=f"sync_{cid}"):
            with st.spinner("Syncing..."):
                sync_connection(cid)
            st.rerun()

    if f"test_result_{cid}" in st.session_state:
        for table, result in st.session_state[f"test_result_{cid}"].items():
            label = KNOWN_TABLE_LABELS.get(table, table)
            if result.get("error"):
                st.error(f"{label}: {result['error']}")
            elif not result.get("exists"):
                st.error(f"{label}: table not found or not readable with these credentials.")
            else:
                st.success(f"{label}: reachable. Sample fields: {', '.join(result['fields']) or '(table is empty)'}")

    if connection.get("sync_mode") == "webhook":
        with st.expander("Webhook setup"):
            base = _ngrok_public_url() or "<your-tunnel-host>"
            webhook_url = f"{base}/ingest/servicenow/{cid}"
            st.code(webhook_url, language=None)
            st.markdown("**HMAC secret** — paste into the Business Rule script below:")
            st.code(decrypt(connection.get("webhook_secret_encrypted", "")), language=None)
            st.markdown("Business Rule script template (send only `external_id`, `prompt`, `response`):")
            st.code(
                f"""var payload = JSON.stringify({{
    external_id: current.sys_id.toString(),
    prompt: current.getValue('prompt'),
    response: current.getValue('response')
}});
var secret = '<the HMAC secret above>';
var signature = new GlideDigest().getHexDigestSHA256(secret, payload); // pseudocode -- use an
                                                                        // HMAC-SHA256 helper, not a
                                                                        // plain digest, on real instances
var request = new sn_ws.RESTMessageV2();
request.setEndpoint('{webhook_url}');
request.setHttpMethod('POST');
request.setRequestHeader('X-Signature', signature);
request.setRequestBody(payload);
request.execute();""",
                language="javascript",
            )

    st.markdown("</div>", unsafe_allow_html=True)
