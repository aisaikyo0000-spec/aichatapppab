"""生成品質評価の集計を表示する。

使い方:
    python scripts/evaluate_generation.py [--db path/to/app.db]

自動評価（naturalness/style/final）の平均と、人間評価（good/neutral/bad）の
件数を集計する。自動評価と人間評価の不一致（例: naturalness が高いのに
human=bad）の把握が目的。DBへの書き込みは行わない。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402


def _avg(rows: list[sqlite3.Row], key: str) -> float | None:
    vals = [r[key] for r in rows if r[key] is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def summarize(db_path: Path) -> dict:
    if not db_path.exists():
        return {"error": f"DBが見つかりません: {db_path}"}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "generation_evaluations" not in tables:
            return {"error": "generation_evaluations テーブルがありません（サーバ起動で作成されます）"}
        rows = conn.execute("SELECT * FROM generation_evaluations").fetchall()
        batches = {r["generation_batch_id"] for r in rows if r["generation_batch_id"] is not None}
        humans = [r for r in rows if r["human_rating"] is not None]
        bad = [r for r in humans if r["human_rating"] == "bad"]
        good = [r for r in humans if r["human_rating"] == "good"]
        return {
            "batches": len(batches),
            "candidates": len(rows),
            "avg_naturalness": _avg(rows, "naturalness_score"),
            "avg_style": _avg(rows, "style_score"),
            "avg_final": _avg(rows, "final_score"),
            "human_good": len([r for r in humans if r["human_rating"] == "good"]),
            "human_neutral": len([r for r in humans if r["human_rating"] == "neutral"]),
            "human_bad": len(bad),
            "human_unrated": len(rows) - len(humans),
            # 不一致の兆候: 人間が bad とした候補の平均 naturalness
            "avg_naturalness_of_human_bad": _avg(bad, "naturalness_score"),
            "avg_naturalness_of_human_good": _avg(good, "naturalness_score"),
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="生成品質評価の集計を表示する")
    parser.add_argument("--db", default=str(config.DB_PATH), help="対象DBパス")
    args = parser.parse_args()

    result = summarize(Path(args.db))
    if "error" in result:
        print(result["error"])
        return 1
    print("Generation Evaluation")
    print("---------------------")
    print(f"Batches:             {result['batches']}")
    print(f"Candidates:          {result['candidates']}")
    print(f"Average Naturalness: {result['avg_naturalness']}")
    print(f"Average Style:       {result['avg_style']}")
    print(f"Average Final:       {result['avg_final']}")
    print(f"Human Good:          {result['human_good']}")
    print(f"Human Neutral:       {result['human_neutral']}")
    print(f"Human Bad:           {result['human_bad']}")
    print(f"Human Unrated:       {result['human_unrated']}")
    print(f"Avg Nat (human-bad): {result['avg_naturalness_of_human_bad']}")
    print(f"Avg Nat (human-good): {result['avg_naturalness_of_human_good']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
