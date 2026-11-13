@echo off
REM Daily Atlas pipeline run (used by Windows Task Scheduler). Logs to data\logs\scheduled_<date>.log
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
set LOG=data\logs\scheduled_%DATE:~-4%%DATE:~3,2%%DATE:~0,2%.log
".venv\Scripts\python.exe" -m atlas.cli run --trigger SCHEDULED >> "%LOG%" 2>&1
".venv\Scripts\python.exe" -m atlas.cli export >> "%LOG%" 2>&1
