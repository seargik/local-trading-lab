@echo off
setlocal
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  echo Virtual environment not found. Create it first with:
  echo py -3 -m venv .venv
  exit /b 1
)
start "Local Lab Backtest" cmd /k ".\.venv\Scripts\python.exe -m streamlit run .\backtest_app.py --server.port 8503"
