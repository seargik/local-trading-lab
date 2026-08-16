@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Missing venv. Create it first.
  pause
  exit /b 1
)
.\.venv\Scripts\python.exe backtest_worker.py
