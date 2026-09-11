from __future__ import annotations

import streamlit as st

from app_src.runtime_cycle_ui import render_runtime_cycle
from app_src.settings import APP_NAME

st.set_page_config(page_title=f"{APP_NAME} · Runtime Cycle", layout="wide")
render_runtime_cycle()
