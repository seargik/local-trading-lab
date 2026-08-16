@echo off
cd /d %~dp0
call start_collector_background.bat
call start_analyzer_background.bat
call start_windows.bat
