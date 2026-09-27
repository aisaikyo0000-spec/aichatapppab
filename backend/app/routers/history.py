"""AI生成履歴の確認・復元・状態更新API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import database
from ..schemas import HistoryUpdate

router = APIRouter(prefix="/api/history", tags=["history"])


def _to_dict(row) -> dict:
    return {
        "id": row["id"],
        "contact_id": row["contact_id"],
        "provider": row["provider"],
        "model": row["model"],
        "current_condition": row["current_condition"],
        "generated_text": row["generated_text"],
        "revision_instruction": row["revision_instruction"],
        "revised_text": row["revised_text"],
        "is_adopted": bool(row["is_adopted"]),
        "is_copied": bool(row["is_copied"]),
        "is_sent": bool(row["is_sent"]),
        "rating": row["rating"] if "rating" in row.keys() else None,
        "rating_reason": row["rating_reason"] if "rating_reason" in row.keys() else None,
        "tone": row["tone"] if "tone" in row.keys() else "",
        "counterpart_message": row["counterpart_message"] if "counterpart_message" in row.keys() else "",
        "created_at": row["created_at"],
    }


@router.get("")
def list_history(contact_id: int | None = None, limit: int = 100):
    conn = database.get_conn()
    try:
        if contact_id is not None:
            rows = conn.execute(
                "SELECT * FROM generation_history WHERE contact_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (contact_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM generation_history ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_to_dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/{history_id}")
def get_history(history_id: int):
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM generation_history WHERE id = ?", (history_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="生成履歴が見つかりません")
        return _to_dict(row)
    finally:
        conn.close()


@router.patch("/{history_id}")
def update_history(history_id: int, body: HistoryUpdate):
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM generation_history WHERE id = ?", (history_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="生成履歴が見つかりません")
        updates: list[str] = []
        params: list = []
        for field in ("is_adopted", "is_copied", "is_sent"):
            value = getattr(body, field)
            if value is not None:
                updates.append(f"{field} = ?")
                params.append(1 if value else 0)
        for field in ("rating", "rating_reason"):
            value = getattr(body, field)
            if value is not None:
                updates.append(f"{field} = ?")
                params.append(value)
        if updates:
            params.append(history_id)
            conn.execute(
                f"UPDATE generation_history SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM generation_history WHERE id = ?", (history_id,)
            ).fetchone()
        return _to_dict(row)

    finally:
        conn.close()
