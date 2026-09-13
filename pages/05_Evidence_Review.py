import streamlit as st

from app_src.evidence_review_ui import render_evidence_review

st.set_page_config(page_title="Evidence Review", page_icon="🧪", layout="wide")
render_evidence_review()
