from __future__ import annotations

import streamlit as st

from app_src.research_command_center_ui import render_research_command_center
from app_src.settings import APP_NAME

st.set_page_config(page_title=f"{APP_NAME} · Research Command Center", layout="wide")
render_research_command_center()
