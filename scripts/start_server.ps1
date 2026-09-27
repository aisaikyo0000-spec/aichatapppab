# Matching Reply Assistant 起動スクリプト（二重起動防止付き）

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "=== Matching Reply Assistant Server Starter ===" -ForegroundColor Cyan

# 1. 既存のポート8000〜8004プロセスを安全に終了
$ports = @(8000, 8001, 8002, 8003, 8004)
$netstat = netstat -ano
foreach ($p in $ports) {
    $matches = $netstat | Select-String ":$p\s+.*LISTENING\s+(\d+)"
    foreach ($m in $matches) {
        if ($m.Matches[0].Groups[1].Value) {
            $pidToKill = [int]$m.Matches[0].Groups[1].Value
            Write-Host "Stopping existing process on port $p (PID: $pidToKill)..." -ForegroundColor Yellow
            Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
        }
    }
}

# 2. 最新バックエンド起動 (ポート 8000)
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $venvPython = "python"
}

Write-Host "Starting backend uvicorn on http://127.0.0.1:8000..." -ForegroundColor Green
& $venvPython -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
