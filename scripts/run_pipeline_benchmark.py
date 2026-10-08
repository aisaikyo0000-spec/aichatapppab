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
from compare_before_after import (  # noqa: E402
    build_case_prompt,
    classify_ai_like,
    detect_issues,
    four_axis_scores,
    summarize_issues,
)
from api_key_file import read_gemini_api_key  # noqa: E402
from benchmark_config import (  # noqa: E402
    build_gemini_benchmark_config,
    record_gemini_benchmark_success,
)
from benchmark_response import benchmark_run_state, extract_api_error_code  # noqa: E402
from app.ai.naturalness import evaluate_candidate_naturalness  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="本番パス live benchmark")
    parser.add_argument("--out", required=True)
    parser.add_argument("--cases", default=str(ROOT / "backend" / "tests" / "step10_benchmark_inputs.json"))
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=-1)
    parser.add_argument("--case-ids", default="",
                        help="特定ケースIDをカンマ区切りで指定。--start/--endより優先")
    parser.add_argument("--model", default="gemini-3.5-flash-lite")
    parser.add_argument("--env-file", default=str(ROOT / ".env"), help="API key file (value is never printed)")
    parser.add_argument("--secondary-env-file", default="",
                        help="Optional second-account key file (value is never printed)")
    parser.add_argument("--delay-seconds", type=float, default=6.0,
                        help="各ケース間の待機秒数。Geminiの短時間リクエスト上限を考慮する")
    args = parser.parse_args()

    key = read_gemini_api_key(Path(args.env_file))
    if not key:
        print("GEMINI_API_KEY missing")
        return 1
    secondary_key = (
        read_gemini_api_key(Path(args.secondary_env_file))
        if args.secondary_env_file
        else ""
    )
    ai_config = build_gemini_benchmark_config(
        primary_key=key, secondary_key=secondary_key, model=args.model
    )
    generation.get_ai_config = lambda: ai_config

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if args.case_ids.strip():
        wanted_ids = {case_id.strip() for case_id in args.case_ids.split(",") if case_id.strip()}
        subset = [case for case in cases if str(case.get("id", "")) in wanted_ids]
        missing_ids = wanted_ids - {str(case.get("id", "")) for case in subset}
        if missing_ids:
            parser.error(f"指定されたcase idが見つかりません: {', '.join(sorted(missing_ids))}")
    else:
        subset = cases[args.start : args.end if args.end >= 0 else len(cases)]
    client = TestClient(app)

    # Step 17 §4: LLM呼出回数を数える（1回より多ければ repair 経路が発動）
    from app.ai import factory as _factory

    _real_get_provider = _factory.get_provider
    call_counter = {"n": 0}
    raw_capture = {"raws": []}
    successful_attempts: list[tuple[str, str]] = []

    def _counting_get_provider(name, api_key):
        provider = _real_get_provider(name, api_key)
        orig_generate = provider.generate

        def _counting_generate(**kwargs):
            call_counter["n"] += 1
            out = orig_generate(**kwargs)
            raw_capture["raws"].append(out)
            successful_attempts.append((api_key, kwargs["model"]))
            return out

        provider.generate = _counting_generate
        return provider

    _factory.get_provider = _counting_get_provider
    generation.factory.get_provider = _counting_get_provider

    results = []
    stopped_reason = None
    for case in subset:
        entry: dict = {"id": case["id"], "contact": case["contact"]}
        calls_before = call_counter["n"]
        successful_before = len(successful_attempts)
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
                entry["error_code"] = extract_api_error_code(r)
            else:
                if len(successful_attempts) > successful_before:
                    successful_key, successful_model = successful_attempts[-1]
                    record_gemini_benchmark_success(
                        ai_config,
                        api_key=successful_key,
                        model=successful_model,
                    )
                data = r.json()
                entry["candidates"] = data["replies"]
                if data.get("question"):
                    entry["safe_user_question"] = data["question"]
                if len(entry["candidates"]) != 3 and not entry.get("safe_user_question"):
                    entry["error"] = "incomplete_candidate_set"
                history_ids = data.get("history_ids", [])
                conn = database.get_conn()
                try:
                    entry["models_used"] = [row["model"] for row in conn.execute(
                        f"SELECT DISTINCT model FROM generation_history WHERE id IN ({','.join('?' for _ in history_ids)})",
                        history_ids,
                    ).fetchall()] if history_ids else []
                finally:
                    conn.close()
                entry["final_scores"] = data.get("final_scores")
                entry["naturalness_scores"] = data.get("naturalness_scores")
                entry["issues"] = [detect_issues(c, case["contact"]) for c in data["replies"]]
                _, _, ledger = build_case_prompt(case)
                naturalness = [
                    evaluate_candidate_naturalness(reply, case["contact"], ledger, [])
                    for reply in data["replies"]
                ]
                entry["four_axis"] = [four_axis_scores(item) for item in naturalness]
                entry["ai_like_patterns"] = [
                    classify_ai_like(reply, case["contact"], case.get("intent", "report"))
                    for reply in data["replies"]
                ]
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}"
        entry["llm_calls"] = call_counter["n"] - calls_before
        if entry["llm_calls"] > 1:
            # Step 17 §4-5: repair 前（1回目 raw）と repair 後（2回目以降 raw）を記録
            entry["repair_raws"] = raw_capture["raws"][calls_before:]
        results.append(entry)
        if entry.get("error"):
            stopped_reason = (
                "rate_limit_exhausted"
                if entry.get("error_code") == "rate_limit"
                else "generation_error"
            )
        Path(args.out).write_text(
            json.dumps({
                **benchmark_run_state(results, len(subset)),
                "cases": results,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if stopped_reason:
            break
        time.sleep(max(0.0, args.delay_seconds))
    all_issues = [iss for e in results for iss in e.get("issues", [])]
    repaired = sum(1 for e in results if e.get("llm_calls", 1) > 1)
    axis_keys = ("context_fit", "human_chat_fit", "conversation_fit")
    axis_avg = {}
    for axis in axis_keys:
        values = [
            candidate[axis]
            for result in results
            for candidate in result.get("four_axis", [])
            if candidate.get(axis) is not None
        ]
        axis_avg[axis] = round(sum(values) / len(values), 3) if values else None
    summary = {
        **benchmark_run_state(results, len(subset)),
        "errors": sum(1 for e in results if "error" in e),
        "repaired_cases": repaired,
        "safe_user_question_cases": sum(1 for e in results if e.get("safe_user_question")),
        "issues": summarize_issues(all_issues),
        "four_axis_avg": axis_avg,
    }
    Path(args.out).write_text(
        json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {args.out}")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
