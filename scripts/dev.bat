@echo off
setlocal
cd /d "%~dp0.."

if not exist .venv (
    echo [ERROR] セットアップが完了していません。setup.bat を実行してください。
    exit /b 1
)
call .venv\Scripts\activate.bat

if "%BACKEND_PORT%"=="" set BACKEND_PORT=8000

echo Backend (http://127.0.0.1:%BACKEND_PORT%) を起動します...
start "MRA Backend" cmd /k "python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port %BACKEND_PORT% --reload"

echo Frontend (http://127.0.0.1:5173) を起動します...
cd frontend
start "MRA Frontend" cmd /k "npm run dev"

endlocal
