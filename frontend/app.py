import os

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="ChatApp", page_icon="💬")


def api(method: str, path: str, **kwargs) -> requests.Response:
    headers = {}
    if "token" in st.session_state:
        headers["Authorization"] = f"Bearer {st.session_state.token}"
    response = requests.request(method, f"{API_URL}{path}", headers=headers, timeout=60, **kwargs)
    if response.status_code == 401 and "token" in st.session_state:
        st.session_state.clear()  # token expired or invalid: back to login
        st.rerun()
    return response


def login_page():
    st.title("💬 ChatApp")
    login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

    with login_tab:
        with st.form("login"):
            username = st.text_input("Username", key="login_username")
            password = st.text_input("Password", type="password", key="login_password")
            if st.form_submit_button("Log in"):
                r = api("POST", "/auth/login", data={"username": username, "password": password})
                if r.ok:
                    st.session_state.token = r.json()["access_token"]
                    st.session_state.username = username
                    st.rerun()
                else:
                    st.error("Incorrect username or password")

    with signup_tab:
        with st.form("signup"):
            username = st.text_input("Username", key="signup_username")
            password = st.text_input("Password (min 8 characters)", type="password", key="signup_password")
            if st.form_submit_button("Create account"):
                r = api("POST", "/auth/signup", json={"username": username, "password": password})
                if r.status_code == 201:
                    st.success("Account created! Log in on the other tab.")
                elif r.status_code == 409:
                    st.error("Username already taken")
                else:
                    st.error("Username needs 3+ characters, password 8+")


def chat_page():
    conversation_id = st.session_state.get("conversation_id")

    with st.sidebar:
        st.write(f"Logged in as **{st.session_state.username}**")
        if st.button("➕ New chat"):
            st.session_state.conversation_id = None
            st.rerun()
        if conversation_id is not None and st.button("🗑️ Delete this chat"):
            api("DELETE", f"/conversations/{conversation_id}")
            st.session_state.conversation_id = None
            st.rerun()
        st.divider()
        for c in api("GET", "/conversations").json():
            button_type = "primary" if c["id"] == conversation_id else "secondary"
            if st.button(c["title"], key=f"conv_{c['id']}", type=button_type):
                st.session_state.conversation_id = c["id"]
                st.rerun()
        st.divider()
        if st.button("Log out"):
            st.session_state.clear()
            st.rerun()

    st.title("💬 ChatApp")

    if conversation_id is not None:
        r = api("GET", f"/conversations/{conversation_id}/messages")
        if not r.ok:
            st.session_state.conversation_id = None
            st.rerun()
        for m in r.json():
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

    if prompt := st.chat_input("Ask anything"):
        if conversation_id is None:
            conversation_id = api("POST", "/conversations").json()["id"]
            st.session_state.conversation_id = conversation_id
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                r = api("POST", f"/conversations/{conversation_id}/messages", json={"content": prompt})
            if r.ok:
                st.rerun()
            if r.status_code == 503:
                st.warning(r.json()["detail"])
            else:
                st.error(f"Something went wrong (status {r.status_code}). Try again.")



if "token" in st.session_state:
    chat_page()
else:
    login_page()
