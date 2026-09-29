"""Step 7 代表30ケースの Before/After 比較を実行する。

使い方:
    python scripts/compare_before_after.py --out PATH [--cases PATH]

各ケースについて Intent 分類・Prompt マーカー・バリデーション・Naturalness
ランキングを記録し、JSON に保存する。コード変更前後で実行して差分を見る。
読み取り専用（生成API・外部LLMは呼ばない）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from app.ai import prompt  # noqa: E402
from app.ai.naturalness import evaluate_candidate_naturalness  # noqa: E402
from app.routers.generation import validate_candidate_replies  # noqa: E402
from step7_representative_cases import load_step7_representative_cases  # noqa: E402


def evaluate_case(case: dict) -> dict:
    contact = case["contact"]
    intent_pred = prompt.classify_counterpart_intent(contact)
    ledger = prompt.build_conversation_state_ledger(
        [{"sender": "contact", "content": contact}], ""
    )
    tier = prompt.classify_message_length(contact)
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text=f"相手: {contact}",
        conversation_ledger=ledger,
        counterpart_length_tier=tier,
        counterpart_length_chars=len(contact),
    )
    markers = {
        "has_intent_block": "【COUNTERPART INTENT】" in sysp,
        "no_hard_question": ("質問必須" not in sysp and "必ず質問" not in sysp),
        "no_3step": ("3段構成" not in sysp and "反応→自己開示→質問" not in sysp),
    }
    if len(contact) <= 20:
        markers["short_guidance"] = "短いリアクション" in sysp

    cands = case["candidates"]
    violations = validate_candidate_replies(cands, 3)
    scored = []
    for c in cands:
        res = evaluate_candidate_naturalness(c, contact, ledger, [])
        scored.append(res["score"])
    order = sorted(range(len(cands)), key=lambda i: scored[i], reverse=True)

    return {
        "id": case["id"],
        "intent_label": case["intent"],
        "intent_pred": intent_pred,
        "intent_match": intent_pred == case["intent"],
        "markers": markers,
        "markers_ok": all(markers.values()),
        "validation_pass": violations == [],
        "violations": violations,
        "naturalness_scores": scored,
        "rank_order": order,
        "expect_first": case["expect_first"],
        "expect_hit": order[0] == case["expect_first"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 7 代表30ケースの比較を実行")
    parser.add_argument("--out", default=None, help="結果JSONの出力先")
    parser.add_argument("--cases", default=None, help="ケースJSONのパス")
    args = parser.parse_args()

    if args.cases:
        cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    else:
        cases = load_step7_representative_cases()

    results = [evaluate_case(c) for c in cases]
    summary = {
        "total": len(results),
        "intent_accuracy": round(sum(1 for r in results if r["intent_match"]) / len(results), 3),
        "markers_ok_rate": round(sum(1 for r in results if r["markers_ok"]) / len(results), 3),
        "validation_pass_rate": round(sum(1 for r in results if r["validation_pass"]) / len(results), 3),
        "expect_hit_rate": round(sum(1 for r in results if r["expect_hit"]) / len(results), 3),
    }
    payload = {"summary": summary, "cases": results}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
