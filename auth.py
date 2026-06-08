"""Authentification simple — credentials dans .streamlit/secrets.toml."""

import streamlit as st


def is_authenticated() -> bool:
    return st.session_state.get("authenticated", False)


def get_current_user() -> str:
    return st.session_state.get("user", "")


def login(username: str, password: str) -> bool:
    users = st.secrets.get("users", {})
    if username in users and users[username] == password:
        st.session_state.authenticated = True
        st.session_state.user = username
        st.session_state.page = "dashboard"
        return True
    return False


def logout() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def show_login_page() -> None:
    st.markdown("""
    <style>
    .login-box {
        max-width: 420px; margin: 80px auto; padding: 2.5rem 2rem;
        background: white; border-radius: 12px;
        box-shadow: 0 4px 24px rgba(0,0,0,0.10);
    }
    .login-title { color: #1F4E79; font-size: 1.6rem; font-weight: 700;
                   text-align: center; margin-bottom: .3rem; }
    .login-sub   { color: #666; text-align: center; margin-bottom: 1.5rem; font-size:.95rem;}
    </style>
    <div class="login-box">
      <div class="login-title">👶 DIP Tire-Lait Express</div>
      <div class="login-sub">Connectez-vous pour continuer</div>
    </div>
    """, unsafe_allow_html=True)

    with st.form("login_form"):
        username = st.text_input("Identifiant", placeholder="votre identifiant")
        password = st.text_input("Mot de passe", type="password", placeholder="••••••••")
        submitted = st.form_submit_button("Se connecter", use_container_width=True, type="primary")

    if submitted:
        if login(username, password):
            st.rerun()
        else:
            st.error("Identifiant ou mot de passe incorrect.")
