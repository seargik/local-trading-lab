@echo off
cd /d %~dp0
if not exist .venv (
  py -3 -m venv .venv
)
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
