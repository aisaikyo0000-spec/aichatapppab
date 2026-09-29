"""生成品質の統計分析（Step 6・読み取り専用）。

評価データ（generation_evaluations＋generation_history）から、
Human Rating / Feedback Tags / Intent 別の統計、不一致ケースの抽出、
代表ケースの分類、改善候補レポートの生成を行う。

重要:
- AIの自己評価だけで「改善した」と判断しない。human_rating / feedback_tags を主軸にする。
- 生成ロジック（prompt / naturalness / retrieval / style / contrast）は
  本モジュールから一切変更しない（読み取りのみ）。
- 新しいタグ・Intent が追加されても自動的に分析対象になる。
- 会話本文は重複保存せず、ID＋JOIN で復元する。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

# 不一致・代表ケースのしきい値
HIGH_SCORE = 0.80
LOW_SCORE = 0.60
MAX_EXAMPLES_PER_TAG = 20
MAX_REPRESENTATIVE_PER_CATEGORY = 10

# タグごとの観測パターン・潜在領域（静的マッピング。LLM による自動変更はしない）
TAG_IMPROVEMENT_HINTS: dict[str, dict[str, str]] = {
    "too_many_questions": {
        "observed": "生成候補が会話を継続するために質問を追加しすぎる。",
        "area": "OUTPUT CONTRACT / FINAL TASK",
    },
    "ai_like": {
        "observed": "自然な短い相槌より説明的な文章を生成する。",
        "area": "LEARNED USER RESPONSE POLICY",
    },
    "irrelevant": {
        "observed": "相手の直前発言と関係のない話題・反応を返す。",
        "area": "CONVERSATION STATE / COUNTERPART INTENT",
    },
    "awkward": {
        "observed": "文法は通るが会話として噛み合わない・不自然な言い回し。",
        "area": "HARD INVARIANTS / 文体実例",
    },
    "too_long": {
        "observed": "相手の発言量に対して返信が長すぎる。",
        "area": "COUNTERPART MESSAGE LENGTH / Length Fit",
    },
    "too_short": {
        "observed": "回答に必要な情報が不足するほど短い。",
        "area": "Intent別方針（question/answer_required）",
    },
    "echo": {
        "observed": "相手の発言を言い換えただけの返信。",
        "area": "Echo 検出 / HARD INVARIANTS",
    },
    "repetition": {
        "observed": "直近の自分の返信と同型の反応を繰り返す。",
        "area": "Repetition 検出 / recent_response_patterns",
    },
    "wrong_tone": {
        "observed": "相手・場面に合わない口調（敬語/タメ口の不一致）。",
        "area": "Tone Hard Lock / COUNTERPART STYLE ADAPTATION",
    },
    "unsupported_self_disclosure": {
        "observed": "根拠のない自己開示・事実の捏造を含む。",
        "area": "HARD INVARIANTS（架空自己開示禁止）/ Memory",
    },
}


def parse_tags(raw: Any) -> list[str]:
    """feedback_tags の防御的パース（NULL・不正JSON・未知タグでも落ちない）。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, str)]
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        tags = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if isinstance(tags, list):
        return [t for t in tags if isinstance(t, str)]
    return []


def load_evaluations(db_path: Path) -> list[dict[str, Any]]:
    """評価＋候補文・相手文を JOIN で復元する。0件なら空リスト（エラーにしない）。"""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "generation_evaluations" not in tables:
            return []
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
    finally:
        conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["feedback_tags"] = parse_tags(d.get("feedback_tags"))
        result.append(d)
    return result


def _avg(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def rating_summary(rows: list[dict]) -> dict[str, Any]:
    """Human Rating 別の件数・平均スコア。"""
    out: dict[str, Any] = {"total": len(rows)}
    for rating in ("good", "neutral", "bad"):
        subset = [r for r in rows if r.get("human_rating") == rating]
        out[rating] = {
            "count": len(subset),
            "avg_naturalness": _avg(subset, "naturalness_score"),
            "avg_style": _avg(subset, "style_score"),
            "avg_final": _avg(subset, "final_score"),
        }
    total_rated = sum(out[r]["count"] for r in ("good", "neutral", "bad"))
    out["unrated"] = len(rows) - total_rated
    out["good_rate"] = round(out["good"]["count"] / total_rated, 3) if total_rated else None
    out["bad_rate"] = round(out["bad"]["count"] / total_rated, 3) if total_rated else None
    return out


def tag_analysis(rows: list[dict]) -> dict[str, dict[str, Any]]:
    """タグ別の件数・割合・平均スコア・bad率（未知タグも自動集計）。"""
    by_tag: dict[str, list[dict]] = {}
    for r in rows:
        for tag in r.get("feedback_tags") or []:
            by_tag.setdefault(tag, []).append(r)
    total = len(rows) or 1
    out: dict[str, dict[str, Any]] = {}
    for tag in sorted(by_tag):
        subset = by_tag[tag]
        rated = [r for r in subset if r.get("human_rating") in ("good", "neutral", "bad")]
        bad = [r for r in rated if r["human_rating"] == "bad"]
        out[tag] = {
            "count": len(subset),
            "percentage": round(len(subset) / total, 3),
            "avg_naturalness": _avg(subset, "naturalness_score"),
            "avg_final": _avg(subset, "final_score"),
            "bad_rate": round(len(bad) / len(rated), 3) if rated else None,
        }
    return out


def intent_analysis(rows: list[dict]) -> dict[str, dict[str, Any]]:
    """Intent 別の件数・good/bad率・平均 naturalness・上位失敗タグ。"""
    by_intent: dict[str, list[dict]] = {}
    for r in rows:
        intent = (r.get("counterpart_intent") or "").strip() or "(unknown)"
        by_intent.setdefault(intent, []).append(r)
    out: dict[str, dict[str, Any]] = {}
    for intent in sorted(by_intent):
        subset = by_intent[intent]
        rated = [r for r in subset if r.get("human_rating") in ("good", "neutral", "bad")]
        good = [r for r in rated if r["human_rating"] == "good"]
        bad = [r for r in rated if r["human_rating"] == "bad"]
        tag_counts: dict[str, int] = {}
        for r in bad:
            for tag in r.get("feedback_tags") or []:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        top_failures = sorted(tag_counts, key=lambda t: (-tag_counts[t], t))[:5]
        out[intent] = {
            "count": len(subset),
            "good_rate": round(len(good) / len(rated), 3) if rated else None,
            "bad_rate": round(len(bad) / len(rated), 3) if rated else None,
            "avg_naturalness": _avg(subset, "naturalness_score"),
            "top_failure_tags": top_failures,
        }
    return out


def _score(r: dict, key: str) -> float | None:
    v = r.get(key)
    return float(v) if v is not None else None


def find_disagreements(
    rows: list[dict],
    high: float = HIGH_SCORE,
    low: float = LOW_SCORE,
) -> dict[str, list[dict]]:
    """AI評価と人間評価の不一致を3ケースで抽出する。"""
    case_a, case_b, case_c = [], [], []
    for r in rows:
        nat = _score(r, "naturalness_score")
        final = _score(r, "final_score")
        human = r.get("human_rating")
        if human == "bad" and nat is not None and nat >= high:
            case_a.append(r)
        if human == "good" and nat is not None and nat < low:
            case_b.append(r)
        if human == "bad" and final is not None and final >= high:
            case_c.append(r)
    return {
        "high_naturalness_but_bad": case_a,
        "low_naturalness_but_good": case_b,
        "high_final_but_bad": case_c,
    }


def tag_examples(rows: list[dict], tag: str, limit: int = MAX_EXAMPLES_PER_TAG) -> list[dict]:
    """指定タグ付き候補の具体例（相手文・候補文・feedback・スコア）。最大20件。"""
    out = []
    for r in rows:
        if tag in (r.get("feedback_tags") or []):
            out.append({
                "counterpart_message": r.get("counterpart_message") or "",
                "generated_text": r.get("generated_text") or "",
                "human_rating": r.get("human_rating"),
                "human_feedback": r.get("human_feedback") or "",
                "naturalness_score": r.get("naturalness_score"),
                "final_score": r.get("final_score"),
                "history_id": r.get("history_id"),
                "batch_id": r.get("batch_id"),
            })
            if len(out) >= limit:
                break
    return out


def representative_cases(
    rows: list[dict],
    high: float = HIGH_SCORE,
    low: float = LOW_SCORE,
    limit: int = MAX_REPRESENTATIVE_PER_CATEGORY,
) -> dict[str, list[dict]]:
    """4カテゴリの代表ケース（各最大10件）。

    A: High final + Good（ ranking が正しく機能）
    B: High final + Bad（ranking の誤り）
    C: Low final + Good（過小評価）
    D: Low final + Bad（正しく低評価）
    """
    def slim(r: dict) -> dict:
        return {
            "counterpart_message": r.get("counterpart_message") or "",
            "generated_text": r.get("generated_text") or "",
            "human_rating": r.get("human_rating"),
            "naturalness_score": r.get("naturalness_score"),
            "final_score": r.get("final_score"),
            "history_id": r.get("history_id"),
        }

    a = [r for r in rows if r.get("human_rating") == "good" and (_score(r, "final_score") or 0) >= high]
    b = [r for r in rows if r.get("human_rating") == "bad" and (_score(r, "final_score") or 0) >= high]
    c = [r for r in rows if r.get("human_rating") == "good" and (_score(r, "final_score") or 1) < low]
    d = [r for r in rows if r.get("human_rating") == "bad" and (_score(r, "final_score") or 1) < low]
    a.sort(key=lambda r: _score(r, "final_score") or 0, reverse=True)
    b.sort(key=lambda r: _score(r, "final_score") or 0, reverse=True)
    c.sort(key=lambda r: _score(r, "final_score") or 1)
    d.sort(key=lambda r: _score(r, "final_score") or 1)
    return {
        "A_high_good": [slim(r) for r in a[:limit]],
        "B_high_bad": [slim(r) for r in b[:limit]],
        "C_low_good": [slim(r) for r in c[:limit]],
        "D_low_bad": [slim(r) for r in d[:limit]],
    }


def improvement_candidates(tag_stats: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """タグ統計から改善候補レポートを生成する（修正の実施は決めない）。"""
    out = []
    for tag, stats in tag_stats.items():
        hint = TAG_IMPROVEMENT_HINTS.get(tag, {
            "observed": "未知タグ。代表例を確認してパターンを特定すること。",
            "area": "（要調査）",
        })
        bad_rate = stats.get("bad_rate")
        out.append({
            "tag": tag,
            "count": stats.get("count", 0),
            "bad_rate": bad_rate,
            "avg_naturalness": stats.get("avg_naturalness"),
            "observed_pattern": hint["observed"],
            "potential_area": hint["area"],
        })
    out.sort(key=lambda c: (-(c["bad_rate"] or 0), -(c["count"] or 0), c["tag"]))
    return out


def write_reports(rows: list[dict], out_dir: Path) -> dict[str, Path]:
    """分析結果5ファイルを reports/generation_quality/ へ出力する。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tags = tag_analysis(rows)
    intents = intent_analysis(rows)
    disagreements = find_disagreements(rows)
    payloads = {
        "summary.json": {
            "total_candidates": len(rows),
            "rating": rating_summary(rows),
            "disagreement_counts": {k: len(v) for k, v in disagreements.items()},
        },
        "tag_analysis.json": tags,
        "intent_analysis.json": intents,
        "disagreements.json": disagreements,
        "representative_cases.json": representative_cases(rows),
    }
    written = {}
    for name, payload in payloads.items():
        path = out_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written[name] = path
    return written
