import streamlit as st

from common.auth import verify_login, issue_token

st.set_page_config(page_title="Demo Portal", page_icon="🛰️", layout="centered")

st.markdown(
    """
    <style>
    .hero {
        background: linear-gradient(135deg, #7C5CFF 0%, #4F8CFF 100%);
        padding: 2rem 2rem 1.6rem 2rem;
        border-radius: 18px;
        margin-bottom: 1.6rem;
        box-shadow: 0 8px 30px rgba(124, 92, 255, 0.25);
    }
    .hero h1 { color: white; margin: 0; font-size: 2rem; }
    .hero p { color: rgba(255,255,255,0.85); margin: 0.4rem 0 0 0; }
    div[data-testid="stForm"] {
        background: #181B27;
        border: 1px solid rgba(139,124,255,0.2);
        border-radius: 14px;
        padding: 1.4rem 1.6rem;
    }
    .pill {
        display: inline-block;
        padding: 0.15rem 0.7rem;
        border-radius: 999px;
        background: rgba(139,124,255,0.15);
        color: #B9AEFF;
        font-size: 0.78rem;
        font-weight: 600;
        margin-right: 0.4rem;
    }
    </style>
    <div class="hero">
        <h1>🛰️ Demo Portal</h1>
        <p>Provider Management &amp; Activity Logs for the endpoint agent</p>
    </div>
    """,
    unsafe_allow_html=True,
)

if "logged_in_user" not in st.session_state:
    st.session_state.logged_in_user = None
    st.session_state.token = None

if st.session_state.logged_in_user:
    user = st.session_state.logged_in_user
    st.success(f"Logged in as **{user['display_name']}** ({user['email']})")

    st.markdown('<span class="pill">DEVICE TOKEN</span>', unsafe_allow_html=True)
    st.caption(
        "Paste this into `agent_config.yaml` as `auth_header_value: \"Bearer <token>\"` before packaging "
        "a test device's exe. The same token authenticates both outbound connections: the ingest "
        "`backend_url` (unchanged) and the new `ws_url` (`wss://<this portal>/ws/agent`) that the device "
        "opens for config-push — no separate per-device registration needed, it shows up on Provider "
        "Management automatically once connected."
    )
    st.code(st.session_state.token, language=None)

    col1, col2 = st.columns(2)
    with col1:
        st.page_link("pages/1_Provider_Management.py", label="Go to Provider Management", icon="🧩")
    with col2:
        st.page_link("pages/2_Activity_Logs.py", label="Go to Activity Logs", icon="📜")

    st.divider()
    if st.button("Log out"):
        st.session_state.logged_in_user = None
        st.session_state.token = None
        st.rerun()
else:
    st.info("Demo credentials — **john.doe@example.com** / **demo1234**")

    with st.form("login_form"):
        email = st.text_input("Email", placeholder="john.doe@example.com")
        password = st.text_input("Password", type="password", placeholder="••••••••")
        submitted = st.form_submit_button("Log in", use_container_width=True, type="primary")

    if submitted:
        user = verify_login(email, password)
        if user:
            st.session_state.logged_in_user = user
            st.session_state.token = issue_token(user["email"], user["display_name"])
            st.rerun()
        else:
            st.error("Invalid email or password.")
