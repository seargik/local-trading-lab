from __future__ import annotations

import streamlit as st

from app_src.settings import APP_NAME
from app_src.history_manager_ui import render_history_manager_tab

st.set_page_config(page_title=f"{APP_NAME} · Data History", layout="wide")
render_history_manager_tab()
