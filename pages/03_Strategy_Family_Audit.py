from __future__ import annotations

import streamlit as st

from app_src.settings import APP_NAME
from app_src.strategy_family_audit_ui import render_strategy_family_audit

st.set_page_config(page_title=f"{APP_NAME} · Strategy Family Audit", layout="wide")
render_strategy_family_audit()
