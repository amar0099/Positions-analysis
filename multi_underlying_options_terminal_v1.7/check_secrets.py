import streamlit as st

st.set_page_config(page_title="FYERS Credential Check")

st.title("FYERS Credential Diagnostic")

keys = [
    "FYERS_CLIENT_ID",
    "FYERS_SECRET_KEY",
    "FYERS_USERNAME",
    "FYERS_PIN",
    "FYERS_TOTP_KEY"
]

for key in keys:
    try:
        value = st.secrets.get(key, "")
        status = "FOUND" if value else "MISSING"
        st.write(f"{key}: {status}")
    except Exception as e:
        st.error(f"{key}: {e}")