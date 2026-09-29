"""生成品質の統計分析を実行し、コンソール表示＋reports/へJSON出力する。

使い方:
    python scripts/analyze_generation_quality.py [--db path/to/app.db] [--out reports/generation_quality]

AIの自己評価だけで改善判断しない。人間評価（human_rating/feedback_tags）を主軸に、
AI評価との不一致・失敗パターン・改善候補を整理する。読み取り専用。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, quality_analysis as qa  # noqa: E402


def print_report(rows: list[dict]) -> None:
    summary = qa.rating_summary(rows)
    tags = qa.tag_analysis(rows)
    intents = qa.intent_analysis(rows)
    disagreements = qa.find_disagreements(rows)

    print("=== Generation Quality Analysis ===")
    print(f"Candidates: {len(rows)}")
    print()
    print("Human Rating")
    for rating in ("good", "neutral", "bad"):
        s = summary[rating]
        print(f"  {rating.capitalize():<8}: {s['count']}")
    print(f"  Unrated: {summary['unrated']}")
    print()
    print("Average scores by human rating")
    for rating in ("good", "neutral", "bad"):
        s = summary[rating]
        print(f"  {rating.capitalize():<8}: nat={s['avg_naturalness']} style={s['avg_style']} final={s['avg_final']}")
    print()
    print("Score / Human disagreement")
    print(f"  High score + Bad:      {len(disagreements['high_naturalness_but_bad'])}")
    print(f"  Low score + Good:      {len(disagreements['low_naturalness_but_good'])}")
    print(f"  High final + Bad:      {len(disagreements['high_final_but_bad'])}")
    print()
    print("Top Bad Tags")
    bad_tags = sorted(
        ((t, s) for t, s in tags.items() if (s['bad_rate'] or 0) > 0),
        key=lambda kv: (-kv[1]['count'], kv[0]),
    )[:10]
    for tag, s in bad_tags:
        print(f"  {tag:<24}: count={s['count']} bad_rate={s['bad_rate']} avg_nat={s['avg_naturalness']}")
    if not bad_tags:
        print("  (no tagged evaluations yet)")
    print()
    print("Intent analysis")
    for intent in sorted(intents):
        s = intents[intent]
        print(f"  {intent:<16}: n={s['count']} good={s['good_rate']} bad={s['bad_rate']} "
              f"avg_nat={s['avg_naturalness']} top_failures={s['top_failure_tags']}")
    if not intents:
        print("  (no evaluations yet)")
    print()
    print("Improvement Candidates")
    for i, cand in enumerate(qa.improvement_candidates(tags), 1):
        print(f"  {i}. Tag: {cand['tag']} (count={cand['count']}, bad_rate={cand['bad_rate']})")
        print(f"     Observed: {cand['observed_pattern']}")
        print(f"     Potential area: {cand['potential_area']}")
    if not tags:
        print("  (no data yet)")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成品質の統計分析")
    parser.add_argument("--db", default=str(config.DB_PATH), help="対象DBパス")
    parser.add_argument(
        "--out", default=str(ROOT / "reports" / "generation_quality"),
        help="レポート出力先",
    )
    args = parser.parse_args()

    rows = qa.load_evaluations(Path(args.db))
    print_report(rows)
    written = qa.write_reports(rows, Path(args.out))
    print()
    for name, path in written.items():
        print(f"Wrote {name} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
