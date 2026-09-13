import streamlit as st

from app_src.prospective_paper_validation_ui import render_prospective_paper_validation

st.set_page_config(page_title="Prospective Paper Validation", layout="wide")
render_prospective_paper_validation()
