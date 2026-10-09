"""本番パス live benchmark（Step 16-R 再挑戦用・読取以外はTemp DB）。

TestClient + 実Gemini で /api/generate フルパス（validate→repair→score→rank→record）を
70ケース実行する。実DB・本番データには触れない。APIキーは表示しない。
使い方:
    python scripts/run_pipeline_benchmark.py --out PATH [--start N] [--end N]
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

class CaseTraceCollector:
    """Collect privacy-conscious trace metadata for one benchmark case."""

    def __init__(self, *, include_prompt_text: bool = False) -> None:
        self.include_prompt_text = include_prompt_text
        self.retrieved_pairs: list[dict] = []
        self.model_calls: list[dict] = []
        self._pair_id_key = secrets.token_bytes(32)

    def record_retrieval(self, pairs: list[dict]) -> None:
        # Pair text is intentionally excluded: it can contain private chat data.
        fields = ("pair_id", "score", "label", "phase", "source")
        for pair in pairs:
            item = {field: pair.get(field) for field in fields}
            raw_pair_id = str(item.pop("pair_id") or "")
            item["pair_id"] = hmac.new(
                self._pair_id_key, raw_pair_id.encode("utf-8"), hashlib.sha256
            ).hexdigest()[:16]
            item["same_contact"] = bool(pair.get("is_same_contact", False))
            self.retrieved_pairs.append(item)

    def record_model_call(
        self, *, model: str, messages, provider: str = "", account: str = "unknown"
    ) -> None:
        canonical = json.dumps(
            messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        call = {
            "call_index": len(self.model_calls) + 1,
            "provider": provider,
            "account": account,
            "model": model,
            "prompt_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }
        if self.include_prompt_text:
            call["messages"] = json.loads(canonical)
        self.model_calls.append(call)

    def to_dict(self, *, input_text: str, intent: str) -> dict:
        return {
            "input": {"text": input_text, "intent": intent},
            "retrieved_pairs": list(self.retrieved_pairs),
            "model_calls": list(self.model_calls),
        }


def main() -> int:
    from benchmark_config import add_active_account_argument

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
    add_active_account_argument(parser)
    parser.add_argument("--quota-route-state", type=Path,
                        help="Optional run-local state shared across benchmark stages")
    parser.add_argument("--delay-seconds", type=float, default=6.0,
                        help="各ケース間の待機秒数。Geminiの短時間リクエスト上限を考慮する")
    parser.add_argument(
        "--trace",
        action="store_true",
        help="ケースごとに入力・検索結果の識別情報・各LLM呼び出しのプロンプトハッシュを保存する",
    )
    parser.add_argument(
        "--include-prompt-text",
        action="store_true",
        help="完全なLLM送信messagesを結果に保存する（会話・Gold例を含む可能性あり。共有先に注意）。--traceも有効化",
    )
    args = parser.parse_args()

    # Importing this script in tests must not initialize a database or touch app state.
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
        load_gemini_benchmark_route,
        record_gemini_benchmark_success,
        successful_gemini_benchmark_route,
    )
    from benchmark_response import (  # noqa: E402
        benchmark_run_state,
        extract_api_error_code,
        generation_response_is_valid,
    )
    from app.ai.naturalness import evaluate_candidate_naturalness  # noqa: E402

    args.trace = args.trace or args.include_prompt_text
    if args.include_prompt_text:
        print("WARNING: --include-prompt-text stores full prompts, including chat and retrieved-example text.")

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
        primary_key=key,
        secondary_key=secondary_key,
        model=args.model,
        active_account=args.active_account,
    )
    if args.quota_route_state is not None:
        load_gemini_benchmark_route(ai_config, args.quota_route_state)
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
    current_trace: dict[str, CaseTraceCollector | None] = {"collector": None}

    def _counting_get_provider(name, api_key):
        provider = _real_get_provider(name, api_key)
        orig_generate = provider.generate

        def _counting_generate(**kwargs):
            call_counter["n"] += 1
            collector = current_trace["collector"]
            if collector is not None:
                model = str(kwargs.get("model", ""))
                route = successful_gemini_benchmark_route(
                    ai_config, api_key=api_key, model=model
                )
                collector.record_model_call(
                    model=model,
                    messages=kwargs.get("messages", []),
                    provider=str(name),
                    account=(route or {}).get("account", "unknown"),
                )
            out = orig_generate(**kwargs)
            raw_capture["raws"].append(out)
            successful_attempts.append((api_key, kwargs["model"]))
            return out

        provider.generate = _counting_generate
        return provider

    _factory.get_provider = _counting_get_provider
    generation.factory.get_provider = _counting_get_provider

    original_retrieve = generation.learning.retrieval.retrieve_relevant_pairs

    def _tracing_retrieve(*args, **kwargs):
        pairs = original_retrieve(*args, **kwargs)
        collector = current_trace["collector"]
        if collector is not None:
            collector.record_retrieval(pairs)
        return pairs

    if args.trace:
        generation.learning.retrieval.retrieve_relevant_pairs = _tracing_retrieve

    results = []
    stopped_reason = None
    for case in subset:
        entry: dict = {"id": case["id"], "contact": case["contact"]}
        trace_collector = (
            CaseTraceCollector(include_prompt_text=args.include_prompt_text)
            if args.trace else None
        )
        current_trace["collector"] = trace_collector
        # Failed provider calls increment the request counter but have no raw
        # output; keep responses scoped to this case rather than slicing by calls.
        raw_capture = {"raws": []}
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
                    successful_route = successful_gemini_benchmark_route(
                        ai_config, api_key=successful_key, model=successful_model
                    )
                    if successful_route is not None:
                        entry["successful_route"] = successful_route
                    record_gemini_benchmark_success(
                        ai_config,
                        api_key=successful_key,
                        model=successful_model,
                        route_state_path=args.quota_route_state,
                    )
                data = r.json()
                if not generation_response_is_valid(data):
                    entry["error"] = "invalid_generation_response"
                    entry["error_code"] = "invalid_response"
                else:
                    entry["candidates"] = data["replies"]
                    if "question" in data:
                        entry["safe_user_question"] = data["question"]
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
        if trace_collector is not None:
            entry["trace"] = trace_collector.to_dict(
                input_text=str(case.get("contact", "")),
                intent=str(case.get("intent", "report")),
            )
        if entry["llm_calls"] > 1:
            # Step 17 §4-5: repair 前（1回目 raw）と repair 後（2回目以降 raw）を記録
            entry["repair_raws"] = list(raw_capture["raws"])
        results.append(entry)
        current_trace["collector"] = None
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
