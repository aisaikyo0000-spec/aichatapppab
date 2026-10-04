"""生成品質評価（自動評価＋人間評価）の保存・取得API。

設計:
- 自動評価（naturalness/style/final）は生成時に generation_evaluations へ保存される。
- 人間評価（human_rating/feedback/tags）は本APIで upsert する。
- 自動評価を人間評価で上書きしない（§7: 不一致は分析材料）。
- 会話本文は重複保存せず、history/batch/message のID参照で復元する。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from .. import database
from ..ai.prompt import classify_counterpart_intent
from ..schemas import EvaluationCreate, EvaluationOut

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])

# 人間評価の理由タグ（必須ではない）
FEEDBACK_TAGS = (
    "ai_like",
    "irrelevant",
    "too_long",
    "too_short",
    "too_many_questions",
    "echo",
    "repetition",
    "awkward",
    "wrong_tone",
    "unsupported_self_disclosure",
    "good",
    "natural",
)


def _parse_tags(raw: str) -> list[str]:
    try:
        tags = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [t for t in tags if isinstance(t, str)]


def _to_out(row) -> dict:
    keys = row.keys()
    return {
        "id": row["id"],
        "generation_batch_id": row["generation_batch_id"],
        "history_id": row["history_id"],
        "candidate_index": row["candidate_index"] or 0,
        "counterpart_intent": row["counterpart_intent"] or "",
        "naturalness_score": row["naturalness_score"],
        "style_score": row["style_score"],
        "final_score": row["final_score"],
        "generated_text": row["generated_text"] or "",
        "counterpart_message": row["counterpart_message"] or "",
        "contact_id": row["contact_id"],
        "human_rating": row["human_rating"],
        "human_feedback": row["human_feedback"] or "",
        "feedback_tags": _parse_tags(row["feedback_tags"]),
        "sendability": row["sendability"] if "sendability" in keys else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _get_full_row(conn, eval_id: int) -> dict:
    row = conn.execute(
        "SELECT e.*, h.generated_text, h.counterpart_message, h.contact_id"
        " FROM generation_evaluations e"
        " LEFT JOIN generation_history h ON e.history_id = h.id"
        " WHERE e.id = ?",
        (eval_id,),
    ).fetchone()
    return _to_out(row)


@router.post("", response_model=EvaluationOut)
def save_evaluation(body: EvaluationCreate):
    """人間評価を保存する（history_id 単位で upsert）。自動スコア列には触れない。"""
    for tag in body.feedback_tags:
        if tag not in FEEDBACK_TAGS:
            raise HTTPException(status_code=422, detail=f"未知のfeedback_tagです: {tag}")

    conn = database.get_conn()
    try:
        hist = conn.execute(
            "SELECT id, batch_id, counterpart_message FROM generation_history WHERE id = ?",
            (body.history_id,),
        ).fetchone()
        if hist is None:
            raise HTTPException(status_code=404, detail="生成履歴が見つかりません")

        now = database.now_iso()
        existing = conn.execute(
            "SELECT id FROM generation_evaluations WHERE history_id = ?", (body.history_id,)
        ).fetchone()
        if existing is None:
            # 自動行が存在しない場合（旧履歴への後付け評価）は参照情報を補完して作成する
            batch_id = hist["batch_id"]
            if batch_id:
                sib = conn.execute(
                    "SELECT COUNT(*) AS n FROM generation_history"
                    " WHERE batch_id = ? AND id <= ?",
                    (batch_id, body.history_id),
                ).fetchone()
                candidate_index = max(0, (sib["n"] or 1) - 1)
            else:
                candidate_index = 0
            intent = classify_counterpart_intent(hist["counterpart_message"] or "")
            cur = conn.execute(
                "INSERT INTO generation_evaluations"
                " (generation_batch_id, history_id, candidate_index, counterpart_intent,"
                "  human_rating, human_feedback, feedback_tags, sendability, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    batch_id, body.history_id, candidate_index, intent,
                    body.rating, body.feedback.strip(), json.dumps(body.feedback_tags, ensure_ascii=False),
                    body.sendability, now, now,
                ),
            )
            eval_id = cur.lastrowid
        else:
            # upsert: 人間評価列のみ更新し、自動スコア列は変更しない
            # rating/sendability が None の場合は既存値を保持（部分更新のため）
            prev = conn.execute(
                "SELECT human_rating, sendability FROM generation_evaluations WHERE id = ?",
                (existing["id"],),
            ).fetchone()
            new_rating = body.rating if body.rating is not None else prev["human_rating"]
            new_sendability = (
                body.sendability if body.sendability is not None else prev["sendability"]
            )
            conn.execute(
                "UPDATE generation_evaluations"
                " SET human_rating = ?, human_feedback = ?, feedback_tags = ?,"
                "     sendability = ?, updated_at = ?"
                " WHERE id = ?",
                (
                    new_rating, body.feedback.strip(),
                    json.dumps(body.feedback_tags, ensure_ascii=False),
                    new_sendability, now, existing["id"],
                ),
            )
            eval_id = existing["id"]
        conn.commit()
        return _get_full_row(conn, eval_id)
    finally:
        conn.close()


@router.get("", response_model=list[EvaluationOut])
def list_evaluations(
    batch_id: int | None = Query(default=None),
    rating: str | None = Query(default=None, pattern="^(good|neutral|bad|unrated)$"),
    sendability: str | None = Query(
        default=None, pattern="^(sendable|minor_edit|major_edit|rejected)$"
    ),
    limit: int = Query(default=100, ge=1, le=1000),
):
    """評価データを新しい順に取得する。rating=unrated で未評価のみに絞れる。"""
    conn = database.get_conn()
    try:
        query = (
            "SELECT e.*, h.generated_text, h.counterpart_message, h.contact_id"
            " FROM generation_evaluations e"
            " LEFT JOIN generation_history h ON e.history_id = h.id"
            " WHERE 1 = 1"
        )
        params: list = []
        if batch_id is not None:
            query += " AND e.generation_batch_id = ?"
            params.append(batch_id)
        if rating == "unrated":
            query += " AND e.human_rating IS NULL AND e.sendability IS NULL"
        elif rating in ("good", "neutral", "bad"):
            query += " AND e.human_rating = ?"
            params.append(rating)
        if sendability is not None:
            query += " AND e.sendability = ?"
            params.append(sendability)
        query += " ORDER BY e.id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [_to_out(r) for r in rows]
    finally:
        conn.close()
