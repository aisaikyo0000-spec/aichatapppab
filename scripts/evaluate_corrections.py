"""manual_replaced ペアに対する識別評価（Step 9・読み取り専用）。

使い方:
    python scripts/evaluate_corrections.py [--db path/to/app.db] [--out PATH] [--limit N]

各ペアについて棄却AI案と採用人間文を Naturalness (+human_fit) で採点し、
採用文が高得点になる割合（discrimination rate）を Before/After で比較する。
- Before: style/naturalness のみ（Step 8 式。style は文脈依存のため本評価では naturalness を使用）
- After: naturalness + human_fit 微調整
DBへの書き込みは行わない。会話本文は出力しない（集計のみ。--include-text でのみ例示出力）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, database  # noqa: E402
from app.ai import naturalness, prompt  # noqa: E402
from app.learning import contrast  # noqa: E402


def _ledger(intent: str) -> dict:
    return {
        "counterpart_intent": intent,
        "known_self_facts": [],
        "already_asked_questions": [],
    }


def score_pair(rejected: str, chosen: str, trigger: str, contact_id: int) -> dict:
    intent = prompt.classify_counterpart_intent(trigger)
    ledger = _ledger(intent)
    nat_rej = naturalness.evaluate_candidate_naturalness(rejected, trigger, ledger, [])["score"]
    nat_cho = naturalness.evaluate_candidate_naturalness(chosen, trigger, ledger, [])["score"]
    fit_rej = contrast.correction_similarity(rejected, contact_id)
    fit_cho = contrast.correction_similarity(chosen, contact_id)
    final_rej = round(nat_rej + 0.1 * (fit_rej - 0.5), 3)
    final_cho = round(nat_cho + 0.1 * (fit_cho - 0.5), 3)
    return {
        "nat_rejected": nat_rej,
        "nat_chosen": nat_cho,
        "fit_rejected": fit_rej,
        "fit_chosen": fit_cho,
        "final_rejected": final_rej,
        "final_chosen": final_cho,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="manual_replaced ペアの識別評価")
    parser.add_argument("--db", default=str(config.DB_PATH), help="対象DBパス")
    parser.add_argument("--out", default=None, help="結果JSONの出力先")
    parser.add_argument("--limit", type=int, default=500, help="最大ペア数")
    args = parser.parse_args()

    config.DB_PATH = Path(args.db)
    if not Path(args.db).exists():
        print(f"DBが見つかりません: {args.db}")
        return 1

    pairs = contrast._iter_correction_pairs(None, args.limit)
    if not pairs:
        print("manual_replaced ペアがありません")
        return 1

    nat_hits = final_hits = 0
    nat_gap_sum = final_gap_sum = 0.0
    for p in pairs:
        s = score_pair(p["rejected"][0], p["chosen"], p["trigger"], p["contact_id"])
        if s["nat_chosen"] > s["nat_rejected"]:
            nat_hits += 1
        if s["final_chosen"] > s["final_rejected"]:
            final_hits += 1
        nat_gap_sum += s["nat_chosen"] - s["nat_rejected"]
        final_gap_sum += s["final_chosen"] - s["final_rejected"]

    n = len(pairs)
    result = {
        "pairs": n,
        "discrimination_naturalness_only": round(nat_hits / n, 3),
        "discrimination_with_human_fit": round(final_hits / n, 3),
        "avg_gap_naturalness": round(nat_gap_sum / n, 3),
        "avg_gap_with_human_fit": round(final_gap_sum / n, 3),
    }
    print(json.dumps(result, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
