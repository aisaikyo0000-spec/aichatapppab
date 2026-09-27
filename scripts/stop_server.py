"""Stop background server cleanly."""
import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
pid_file = root / "data" / "server.pid"
port_file = root / "data" / "server.port"

if pid_file.exists():
    try:
        pid = pid_file.read_text(encoding="ascii").strip()
        if pid:
            subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True)
    except Exception:
        pass
    try:
        pid_file.unlink(missing_ok=True)
    except Exception:
        pass

if port_file.exists():
    try:
        port = port_file.read_text(encoding="ascii").strip()
        if port:
            netstat = subprocess.run(["netstat", "-a", "-n", "-o"], capture_output=True, text=True, encoding="cp932", errors="replace")
            for line in netstat.stdout.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", parts[-1]], capture_output=True)
    except Exception:
        pass
    try:
        port_file.unlink(missing_ok=True)
    except Exception:
        pass
