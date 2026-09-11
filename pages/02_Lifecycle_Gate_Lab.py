from __future__ import annotations

import streamlit as st

from app_src.lifecycle_gate_ui import render_lifecycle_gate_lab
from app_src.settings import APP_NAME

st.set_page_config(page_title=f"{APP_NAME} · Lifecycle Gate Lab", layout="wide")
render_lifecycle_gate_lab()
