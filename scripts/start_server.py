"""Start background server with fully detached process."""
import sys
import subprocess
import time
import urllib.request
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
port = sys.argv[1] if len(sys.argv) > 1 else "8000"

(root / "data").mkdir(exist_ok=True)
(root / "logs").mkdir(exist_ok=True)

pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
if not pythonw.exists():
    pythonw = root / ".venv" / "Scripts" / "python.exe"

log_out = open(root / "logs" / "server.log", "a", encoding="utf-8")
log_err = open(root / "logs" / "server.err.log", "a", encoding="utf-8")

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

p = subprocess.Popen(
    [
        str(pythonw),
        "-m", "uvicorn",
        "app.main:app",
        "--app-dir", "backend",
        "--host", "127.0.0.1",
        "--port", str(port),
    ],
    cwd=str(root),
    stdout=log_out,
    stderr=log_err,
    creationflags=DETACHED,
    close_fds=True,
)

(root / "data" / "server.pid").write_text(str(p.pid), encoding="ascii")
(root / "data" / "server.port").write_text(str(port), encoding="ascii")

# ヘルスチェック (最大15秒待機)
for _ in range(15):
    time.sleep(1)
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
        data = json.loads(req.read().decode("utf-8"))
        if data.get("status") == "ok":
            print(f"OK:{port}")
            sys.exit(0)
    except Exception:
        pass

print("ERROR: Timeout waiting for server")
sys.exit(1)
