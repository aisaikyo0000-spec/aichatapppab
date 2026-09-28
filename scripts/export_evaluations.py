"""評価データをCSV/JSONにエクスポートする。

使い方:
    python scripts/export_evaluations.py [--format csv|json] [--out PATH] [--db path/to/app.db]

会話本文は重複保存せず、ID参照＋必要最小限のテキスト（候補文・相手直前文）を
含める。分析用途の読み取り専用スクリプト（DBへの書き込みは行わない）。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402

COLUMNS = [
    "id",
    "batch_id",
    "history_id",
    "candidate_index",
    "counterpart_intent",
    "counterpart_message",
    "generated_text",
    "naturalness_score",
    "style_score",
    "final_score",
    "human_rating",
    "human_feedback",
    "feedback_tags",
    "created_at",
]


def fetch_rows(db_path: Path) -> tuple[list[str], list[dict]]:
    if not db_path.exists():
        raise FileNotFoundError(f"DBが見つかりません: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "generation_evaluations" not in tables:
            raise RuntimeError("generation_evaluations テーブルがありません（サーバ起動で作成されます）")
        rows = conn.execute(
            "SELECT e.id, e.generation_batch_id AS batch_id, e.history_id,"
            " e.candidate_index, e.counterpart_intent,"
            " h.counterpart_message, h.generated_text,"
            " e.naturalness_score, e.style_score, e.final_score,"
            " e.human_rating, e.human_feedback, e.feedback_tags, e.created_at"
            " FROM generation_evaluations e"
            " LEFT JOIN generation_history h ON e.history_id = h.id"
            " ORDER BY e.id ASC"
        ).fetchall()
        return COLUMNS, [dict(r) for r in rows]
    finally:
        conn.close()


def export_evaluations(db_path: Path, fmt: str, out_path: Path) -> tuple[Path, int]:
    """評価データをエクスポートし、(出力パス, 件数) を返す。テストからも利用する。"""
    columns, rows = fetch_rows(Path(db_path))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        out_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        out_path.write_text(buf.getvalue(), encoding="utf-8-sig")
    return out_path, len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="評価データをCSV/JSONにエクスポートする")
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument("--db", default=str(config.DB_PATH), help="対象DBパス")
    parser.add_argument(
        "--out", default=None,
        help="出力パス（省略時は data/evaluations_YYYYMMDD_HHMMSS.<ext>）",
    )
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_out = config.DATA_DIR / f"evaluations_{stamp}.{args.format}"
    out = Path(args.out) if args.out else default_out
    try:
        path, count = export_evaluations(Path(args.db), args.format, out)
    except (FileNotFoundError, RuntimeError) as exc:
        print(exc)
        return 1
    print(f"Exported {count} evaluations -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
