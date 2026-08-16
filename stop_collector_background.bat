@echo off
cd /d %~dp0
if exist data\collector.pid (
  set /p PID=<data\collector.pid
  taskkill /PID %PID% /F
  del /Q data\collector.pid
) else (
  echo No collector.pid file found.
  pause
)
