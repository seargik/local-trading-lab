@echo off
setlocal
if exist data\local_lab.sqlite del /f /q data\local_lab.sqlite
if exist data\market_snapshot.json del /f /q data\market_snapshot.json
if exist data\analysis_cache.json del /f /q data\analysis_cache.json
if exist data\collector_state.json del /f /q data\collector_state.json
if exist data\analyzer_state.json del /f /q data\analyzer_state.json
if exist data\analysis_request.json del /f /q data\analysis_request.json
if exist data\collector.pid del /f /q data\collector.pid
if exist data\analyzer.pid del /f /q data\analyzer.pid
if exist logs\collector.log del /f /q logs\collector.log
if exist logs\analyzer.log del /f /q logs\analyzer.log
echo Local Lab state cleared.
endlocal
