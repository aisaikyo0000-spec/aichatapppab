@echo off
setlocal
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe scripts\stop_server.py
) else (
    python scripts\stop_server.py 2>nul
)
exit /b 0
