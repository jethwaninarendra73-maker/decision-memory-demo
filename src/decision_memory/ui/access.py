"""An optional password screen for a hosted demo.

With `DM_ACCESS_PASSWORD` set (in the host's secrets, never in a committed file), the
app shows only this screen until the visitor enters it. It keeps casual visitors out of
a shared link; it is not user accounts or access control, and the demo roles inside the
app stay a demonstration control. The password is compared in constant time and never
logged, displayed or stored anywhere but the settings.
"""

from __future__ import annotations

import hmac
import logging

import streamlit as st
from pydantic import SecretStr

from decision_memory import SYNTHETIC_BANNER

logger = logging.getLogger(__name__)

GRANTED_KEY = "access_granted"
PASSWORD_KEY = "access_password_entry"
WRONG_PASSWORD = "That password is not right. Ask the person who shared the link."


def _matches(entered: str, password: SecretStr) -> bool:
    return hmac.compare_digest(entered.encode(), password.get_secret_value().encode())


def require_access(password: SecretStr | None) -> bool:
    """True if the app may be shown. Without a configured password, always True."""
    if password is None or st.session_state.get(GRANTED_KEY):
        return True
    st.warning(f"**{SYNTHETIC_BANNER}** · fictional data, advisory only", icon="⚠️")
    st.title(":material/neurology: Decision Memory")
    st.caption("A private demo. Enter the password you were given.")
    with st.form("access_form"):
        entered = st.text_input("Password", type="password", key=PASSWORD_KEY)
        submitted = st.form_submit_button("Open the demo", type="primary")
    if submitted:
        if _matches(entered, password):
            st.session_state[GRANTED_KEY] = True
            logger.info("Demo opened with the access password")
            st.rerun()
        logger.info("Wrong access password entered")
        st.error(WRONG_PASSWORD)
    return False
