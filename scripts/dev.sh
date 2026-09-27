#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

[ -d .venv ] || { echo "[ERROR] セットアップが完了していません。./scripts/setup.sh を実行してください。"; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate

PORT="${BACKEND_PORT:-8000}"
echo "Backend (http://127.0.0.1:${PORT}) を起動します..."
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port "$PORT" --reload &
BACKEND_PID=$!

echo "Frontend (http://127.0.0.1:5173) を起動します..."
(cd frontend && npm run dev)

kill "$BACKEND_PID" 2>/dev/null || true
