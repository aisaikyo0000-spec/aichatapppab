@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === セットアップを開始します ===

set "PYTHON_CMD=python"
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"

%PYTHON_CMD% --version >nul 2>nul
if errorlevel 1 goto :install_python
:python_ok
where node >nul 2>nul
if errorlevel 1 goto :install_node
:node_ok

echo [1/5] Python仮想環境を作成しています...
if not exist .venv\Scripts\python.exe (
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo [エラー] Python仮想環境の作成に失敗しました。
        pause
        exit /b 1
    )
)

echo [2/5] バックエンド依存パッケージをインストールしています...
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
if errorlevel 1 (
    echo [エラー] バックエンドのインストールに失敗しました。
    pause
    exit /b 1
)

echo [3/5] フロントエンド依存パッケージをインストールしています...
if not exist frontend\node_modules (
    pushd frontend
    call npm install
    if errorlevel 1 (
        popd
        echo [エラー] フロントエンドのインストールに失敗しました。
        pause
        exit /b 1
    )
    popd
)

echo [4/5] フロントエンドをビルドしています...
if not exist frontend\dist (
    pushd frontend
    call npm run build
    if errorlevel 1 (
        popd
        echo [エラー] フロントエンドのビルドに失敗しました。
        pause
        exit /b 1
    )
    popd
)

echo [5/5] 設定ファイル・データフォルダを準備しています...
if not exist .env copy .env.example .env >nul
if not exist data mkdir data
if not exist logs mkdir logs

echo.
echo ================================================================
echo セットアップが完了しました。「起動.bat」を実行してアプリを開始してください。
echo ================================================================
pause
exit /b 0

:install_python
echo [setup] Python が見つかりません。手動でインストールしてください: https://www.python.org/downloads/
pause
exit /b 1

:install_node
echo [setup] Node.js が見つかりません。手動でインストールしてください: https://nodejs.org/
pause
exit /b 1
