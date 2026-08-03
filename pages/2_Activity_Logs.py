import streamlit as st
from streamlit_autorefresh import st_autorefresh

from common.guard import require_login
from common.providers import color_for
from common.store import fetch_events
from common.timeutil import relative_time

st.set_page_config(page_title="Activity Logs", page_icon="📜", layout="wide")
require_login()

st.markdown(
    """
    <style>
    .log-row {
        border: 1px solid rgba(139,124,255,0.18);
        border-radius: 12px;
        padding: 0.9rem 1.1rem;
        margin-bottom: 0.7rem;
        background: #14161F;
    }
    .badge {
        display: inline-block;
        padding: 0.15rem 0.65rem;
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 700;
        color: white;
        margin-right: 0.5rem;
    }
    .rel-time { color: #9AA0AE; font-size: 0.8rem; }
    .meta { color: #9AA0AE; font-size: 0.78rem; margin-top: 0.3rem; }
    .snippet { margin-top: 0.5rem; color: #D6D6E0; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📜 Activity Logs")
st.caption("Every captured event received from all devices — newest first.")

top_l, top_r = st.columns([3, 1])
with top_l:
    autorefresh_on = st.toggle("Auto-refresh every 5s", value=True)
with top_r:
    if st.button("🔄 Refresh now", use_container_width=True):
        st.rerun()

if autorefresh_on:
    st_autorefresh(interval=5000, key="logs_autorefresh")

events = fetch_events(limit=500)

kind_filter = st.multiselect("Filter by type", options=["conversation", "alert"],
                              default=["conversation", "alert"])
events = [e for e in events if e["kind"] in kind_filter]

st.metric("Total events shown", len(events))
st.divider()

if not events:
    st.info("No events yet. Once the agent's uploader posts to the ingest endpoint, they'll show up here.")

for e in events:
    record = e["record"]
    kind = e["kind"]
    color = color_for(e["provider_key"], e["provider_display"], kind)
    tag = e["provider_display"] if kind == "conversation" else record.get("source_app", "unknown")
    rel = relative_time(e["event_time"])

    st.markdown(
        f"""
        <div class="log-row">
            <span class="badge" style="background:{color}">{tag}</span>
            <span class="rel-time">{rel} · {e['event_time']}</span>
        """,
        unsafe_allow_html=True,
    )

    if kind == "conversation":
        st.markdown(
            f'<div class="meta">device page: {record.get("page_title", "—")} · '
            f'{record.get("url", "")}</div>',
            unsafe_allow_html=True,
        )
        prompt = record.get("prompt", "") or ""
        response = record.get("response", "") or ""
        preview = prompt[:200] + ("…" if len(prompt) > 200 else "")
        st.markdown(f'<div class="snippet"><b>Prompt:</b> {preview}</div>', unsafe_allow_html=True)
        with st.expander("Show full prompt & response"):
            st.markdown("**Prompt**")
            st.write(prompt)
            st.markdown("**Response**")
            st.write(response if response else "_(no response captured)_")
    else:
        st.markdown(
            f'<div class="meta">window: {record.get("window_title", "—")} · '
            f'detection: {record.get("detection_type", "—")} · '
            f'confidence: {record.get("confidence", "—")} · '
            f'rule: {record.get("rule_matched", "—")}</div>',
            unsafe_allow_html=True,
        )
        snippet = record.get("raw_snippet", "")
        st.markdown(f'<div class="snippet">{snippet}</div>', unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)
