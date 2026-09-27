#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

echo "=== Matching Reply Assistant: Setup ==="

command -v python3 >/dev/null 2>&1 || { echo "[ERROR] Python が見つかりません。Python 3.10 以上をインストールしてください。"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "[ERROR] Node.js が見つかりません。Node.js 18 以上をインストールしてください。"; exit 1; }

echo "[1/6] Python 環境を作成..."
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[2/6] Backend 依存関係をインストール..."
python -m pip install -r backend/requirements.txt

echo "[3/6] Frontend 依存関係をインストール..."
(cd frontend && npm install)

echo "[4/6] 必要なフォルダを作成..."
python -c "import sys; sys.path.insert(0,'backend'); from app.config import ensure_dirs; ensure_dirs()"

echo "[5/6] 設定ファイルを作成..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "      .env を作成しました。必要に応じて CEREBRAS_API_KEY を設定してください。"
fi

echo "[6/6] SQLite を初期化..."
python -c "import sys; sys.path.insert(0,'backend'); from app.database import init_db; init_db()"

echo
echo "=== セットアップ完了！ ./scripts/start.sh で起動できます ==="
