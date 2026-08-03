import streamlit as st


def require_login() -> dict:
    if not st.session_state.get("logged_in_user"):
        st.warning("Please log in first.")
        st.page_link("Home.py", label="Back to login", icon="🔐")
        st.stop()
    return st.session_state.logged_in_user
