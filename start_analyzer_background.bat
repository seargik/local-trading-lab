@echo off
cd /d %~dp0
if not exist .venv (
  py -3 -m venv .venv
)
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if not exist logs mkdir logs
start "Local Lab Analyzer" .\.venv\Scripts\python.exe analyzer_worker.py
