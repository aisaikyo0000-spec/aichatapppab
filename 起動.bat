@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto :err_python
where node >nul 2>nul
if errorlevel 1 goto :err_node

if not exist .venv\Scripts\python.exe (
    echo [setup] Python仮想環境を作成しています...
    python -m venv .venv
)

.venv\Scripts\python -c "import fastapi" >nul 2>nul
if errorlevel 1 (
    echo [setup] バックエンド依存パッケージをインストールしています...
    .venv\Scripts\python -m pip install -r backend\requirements.txt
)

if not exist frontend\node_modules (
    echo [setup] フロントエンド依存パッケージをインストールしています...
    pushd frontend
    call npm install
    popd
)

if not exist frontend\dist (
    echo [setup] フロントエンドをビルドしています...
    pushd frontend
    call npm run build
    popd
)

if not exist .env copy .env.example .env >nul
if not exist data mkdir data
if not exist logs mkdir logs

if "%BACKEND_PORT%"=="" (
    set BACKEND_PORT=8000
    for /l %%P in (8000,1,8020) do (
        netstat -ano | findstr /R /C:":%%P .*LISTENING" >nul 2>nul
        if errorlevel 1 (
            set BACKEND_PORT=%%P
            goto :port_found
        )
    )
)
:port_found

echo [起動] サーバーを起動しています (ポート !BACKEND_PORT!)...
.venv\Scripts\python.exe scripts\start_server.py !BACKEND_PORT!
if errorlevel 1 (
    echo [エラー] サーバーの起動に失敗しました。logs\server.err.log を確認してください。
    pause
    exit /b 1
)

echo [起動] サーバーが起動しました (http://127.0.0.1:!BACKEND_PORT!)
call :open_app
endlocal
exit /b 0

:open_app
if "%MRA_NO_BROWSER%"=="1" goto :eof
set "CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if defined CHROME (
    start "" "%CHROME%" --app="http://127.0.0.1:%BACKEND_PORT%"
) else (
    start "" "http://127.0.0.1:%BACKEND_PORT%"
)
goto :eof

:err_python
echo [エラー] Python が見つかりません。Python 3.10以上をインストールしてください: https://www.python.org
pause
exit /b 1

:err_node
echo [エラー] Node.js が見つかりません。Node.js 18以上をインストールしてください: https://nodejs.org
pause
exit /b 1
