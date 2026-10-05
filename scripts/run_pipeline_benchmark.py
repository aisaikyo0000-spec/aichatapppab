"""本番パス live benchmark（Step 16-R 再挑戦用・読取以外はTemp DB）。

TestClient + 実Gemini で /api/generate フルパス（validate→repair→score→rank→record）を
70ケース実行する。実DB・本番データには触れない。APIキーは表示しない。
使い方:
    python scripts/run_pipeline_benchmark.py --out PATH [--start N] [--end N]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

_tmp = Path(tempfile.mkdtemp(prefix="pipebench_"))

from app import config  # noqa: E402

config.DB_PATH = _tmp / "app.db"
config.DATA_DIR = _tmp
config.BACKUPS_DIR = _tmp / "backups"
config.CONTACTS_IMAGE_DIR = _tmp / "contacts"

from app import database  # noqa: E402

database.init_db()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import generation  # noqa: E402
from compare_before_after import detect_issues, summarize_issues  # noqa: E402


def _read_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("GEMINI_API_KEY"):
            return s.partition("=")[2].strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="本番パス live benchmark")
    parser.add_argument("--out", required=True)
    parser.add_argument("--cases", default=str(ROOT / "backend" / "tests" / "step10_benchmark_inputs.json"))
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=-1)
    parser.add_argument("--model", default="gemini-3.5-flash-lite")
    args = parser.parse_args()

    key = _read_key()
    if not key:
        print("GEMINI_API_KEY missing")
        return 1
    generation.get_ai_config = lambda: {
        "provider": "gemini", "model": args.model, "api_key": key,
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    }

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    subset = cases[args.start : args.end if args.end >= 0 else len(cases)]
    client = TestClient(app)
    results = []
    for case in subset:
        entry: dict = {"id": case["id"], "contact": case["contact"]}
        try:
            # 直接生成との比較可能性のため表示名は「相手」に統一する
            # （プロンプトの「さん付け」指示により名前が文面に混入するため）
            cid = client.post("/api/contacts", json={"name": "相手", "profile": ""}).json()["id"]
            client.post(f"/api/contacts/{cid}/messages",
                        json={"sender": "contact", "content": case["contact"]})
            r = client.post("/api/generate",
                            json={"contact_id": cid, "condition": "", "candidates": 3})
            if r.status_code != 200:
                entry["error"] = f"HTTP {r.status_code}"
            else:
                data = r.json()
                entry["candidates"] = data["replies"]
                entry["final_scores"] = data.get("final_scores")
                entry["naturalness_scores"] = data.get("naturalness_scores")
                entry["issues"] = [detect_issues(c, case["contact"]) for c in data["replies"]]
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}"
        results.append(entry)
        time.sleep(1)
    all_issues = [iss for e in results for iss in e.get("issues", [])]
    summary = {
        "total": len(results),
        "errors": sum(1 for e in results if "error" in e),
        "issues": summarize_issues(all_issues),
    }
    Path(args.out).write_text(
        json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {args.out}")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
