#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

[ -d .venv ] || { echo "[ERROR] セットアップが完了していません。./scripts/setup.sh を実行してください。"; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate

[ -d frontend/node_modules ] || { echo "[ERROR] セットアップが完了していません。./scripts/setup.sh を実行してください。"; exit 1; }

if [ ! -d frontend/dist ]; then
    echo "Frontend をビルドしています..."
    (cd frontend && npm run build)
fi

PORT="${BACKEND_PORT:-8000}"
echo "Matching Reply Assistant を起動します: http://127.0.0.1:${PORT}"
echo "停止するには Ctrl+C を押してください。"
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port "$PORT"
