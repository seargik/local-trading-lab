@echo off
cd /d %~dp0
if exist data\analyzer.pid (
  set /p PID=<data\analyzer.pid
  taskkill /PID %PID% /F
  del /Q data\analyzer.pid
) else (
  echo No analyzer.pid file found.
  pause
)
