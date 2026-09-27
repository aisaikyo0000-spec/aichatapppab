@echo off
setlocal
cd /d "%~dp0.."

echo === Matching Reply Assistant: Setup ===

where python >nul 2>nul || (echo [ERROR] Python が見つかりません。Python 3.10 以上をインストールしてください。 & exit /b 1)
where node >nul 2>nul || (echo [ERROR] Node.js が見つかりません。Node.js 18 以上をインストールしてください。 & exit /b 1)

echo [1/6] Python 環境を作成...
if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate.bat

echo [2/6] Backend 依存関係をインストール...
python -m pip install -r backend\requirements.txt

echo [3/6] Frontend 依存関係をインストール...
cd frontend
call npm install
cd ..

echo [4/6] 必要なフォルダを作成...
python -c "import sys; sys.path.insert(0,'backend'); from app.config import ensure_dirs; ensure_dirs()"

echo [5/6] 設定ファイルを作成...
if not exist .env (
    copy .env.example .env >nul
    echo      .env を作成しました。必要に応じて CEREBRAS_API_KEY を設定してください。
)

echo [6/6] SQLite を初期化...
python -c "import sys; sys.path.insert(0,'backend'); from app.database import init_db; init_db()"

echo.
echo === セットアップ完了！ scripts\start.bat で起動できます ===
endlocal
