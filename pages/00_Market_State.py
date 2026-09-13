import streamlit as st

from app_src.market_state_ui import render_market_state_identifier

st.set_page_config(page_title="Market State", page_icon="🧭", layout="wide")
render_market_state_identifier()
