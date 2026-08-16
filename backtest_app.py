from __future__ import annotations

import streamlit as st

from app_src.storage import Storage
from app_src.settings import APP_NAME, LAB_DB_PATH
from app_src.backtest_ui import render_backtest_tab
from app_src.insights_ui import render_foundation_toolkit_tab, render_handover_tab

st.set_page_config(page_title=f"{APP_NAME} – Backtest Lab", layout="wide")
st.title(f"{APP_NAME} – Backtest Lab")
st.caption("Standalone backtest window. Uses the same strategy library and saved results as the main app, but runs in a separate process/window.")

storage = Storage(LAB_DB_PATH)
lab_tab, toolkit_tab, handover_tab = st.tabs(['Backtest Lab', 'Foundation Toolkit', 'Handover'])
with lab_tab:
    render_backtest_tab(storage)
with toolkit_tab:
    render_foundation_toolkit_tab()
with handover_tab:
    render_handover_tab()
