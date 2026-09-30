@echo off
cd /d "%~dp0"
echo Starting FinGuard Live...
echo Open http://127.0.0.1:8000 in Chrome or Edge.
echo Keep this window open while using the app.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 server.py
) else (
  python server.py
)
pause
