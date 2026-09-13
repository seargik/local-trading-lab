import streamlit as st

from app_src.walk_forward_ui import render_walk_forward_validation

st.set_page_config(page_title="Walk-Forward Validation", layout="wide")
render_walk_forward_validation()
