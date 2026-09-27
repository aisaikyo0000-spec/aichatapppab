"""Run 10 iterations of start and stop bat tests."""
import os
import subprocess
import time
import urllib.request
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["MRA_NO_BROWSER"] = "1"

# 初期化
subprocess.run(["cmd.exe", "/c", "終了.bat"], cwd=str(ROOT), capture_output=True)
time.sleep(1)

results = []

for i in range(1, 11):
    print(f"=== テスト回数: {i} / 10 ===", flush=True)
    
    # 1. 起動.bat を実行
    res = subprocess.run(
        ["cmd.exe", "/c", "起動.bat"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="cp932",
        errors="replace",
        timeout=20,
    )
    
    pid_file = ROOT / "data" / "server.pid"
    port_file = ROOT / "data" / "server.port"
    
    if not pid_file.exists() or not port_file.exists():
        msg = f"第 {i} 回: 失敗 (PID/PORTファイル未生成) stdout={res.stdout} stderr={res.stderr}"
        print(msg, flush=True)
        results.append(msg)
        continue
    
    server_pid = int(pid_file.read_text(encoding="ascii").strip())
    port = int(port_file.read_text(encoding="ascii").strip())
    
    # 2. ヘルスチェック
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3)
        data = json.loads(req.read().decode("utf-8"))
        if data.get("status") != "ok":
            msg = f"第 {i} 回: 失敗 (ステータス異常: {data})"
            print(msg, flush=True)
            results.append(msg)
            continue
    except Exception as e:
        msg = f"第 {i} 回: 失敗 (HTTP接続エラー: {e})"
        print(msg, flush=True)
        results.append(msg)
        continue
    
    # 3. 終了.bat を実行
    stop_res = subprocess.run(
        ["cmd.exe", "/c", "終了.bat"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="cp932",
        errors="replace",
        timeout=10,
    )
    
    time.sleep(0.5)
    
    # 4. プロセスとファイルのクリーンアップ確認
    is_running = False
    try:
        t_proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {server_pid}", "/NH"],
            capture_output=True,
            text=True,
        )
        if str(server_pid) in t_proc.stdout:
            is_running = True
    except Exception:
        pass
    
    has_pid = pid_file.exists()
    has_port = port_file.exists()
    
    if is_running or has_pid or has_port:
        msg = f"第 {i} 回: 失敗 (プロセスまたはファイル残存: running={is_running}, pid={has_pid}, port={has_port})"
        print(msg, flush=True)
        results.append(msg)
    else:
        msg = f"第 {i} 回: 成功 (ポート {port}, PID {server_pid}, 正常起動・即時終了確認)"
        print(msg, flush=True)
        results.append(msg)

print("\n================== 最終結果 ==================", flush=True)
success_count = sum(1 for r in results if "成功" in r)
print(f"合計: 10回中 {success_count} 回 成功", flush=True)
for r in results:
    print(r, flush=True)

if success_count == 10:
    print("ALL 10 ITERATIONS PASSED PERFECTLY!", flush=True)
    exit(0)
else:
    exit(1)
