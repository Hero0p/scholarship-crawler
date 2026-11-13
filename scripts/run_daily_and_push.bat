@echo off
REM Fallback to GitHub Actions: run the daily crawl from this PC (Indian IP) and push the updated
REM database so the Streamlit Community Cloud app redeploys with fresh data.
REM Register with Task Scheduler:
REM   schtasks /Create /SC DAILY /ST 02:00 /TN "AtlasScholarshipCrawler" /TR "\"D:\Coding\Scholarship Crawler\scripts\run_daily_and_push.bat\""
REM (If you use this, disable the schedule in .github/workflows/crawl.yml so the two never both write the DB.)
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
set LOG=data\logs\scheduled_%DATE:~-4%%DATE:~3,2%%DATE:~0,2%.log
git pull --rebase >> "%LOG%" 2>&1
".venv\Scripts\python.exe" -m atlas.cli run --trigger SCHEDULED >> "%LOG%" 2>&1
".venv\Scripts\python.exe" -m atlas.cli export >> "%LOG%" 2>&1
".venv\Scripts\python.exe" -m atlas.cli checkpoint >> "%LOG%" 2>&1
git add data/atlas.db data/sample >> "%LOG%" 2>&1
for /f "delims=" %%f in ('".venv\Scripts\python.exe" -m atlas.cli evidence-files 2^>nul') do git add -f "%%f" >> "%LOG%" 2>&1
git commit -m "Scheduled crawl (local) %DATE%" >> "%LOG%" 2>&1
git push >> "%LOG%" 2>&1
