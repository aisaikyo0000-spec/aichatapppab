"""メッセージの追加・編集・削除とチャットエクスポートAPI。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from .. import database
from ..schemas import MessageCreate, MessageOut, MessageUpdate

router = APIRouter(prefix="/api", tags=["messages"])


def _check_contact(conn, contact_id: int) -> None:
    row = conn.execute(
        "SELECT id FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="相手が見つかりません")


def _touch_contact(conn, contact_id: int) -> None:
    conn.execute(
        "UPDATE contacts SET updated_at = ? WHERE id = ?",
        (database.now_iso(), contact_id),
    )


@router.get("/contacts/{contact_id}/messages", response_model=list[MessageOut])
def list_messages(contact_id: int):
    conn = database.get_conn()
    try:
        _check_contact(conn, contact_id)
        rows = conn.execute(
            "SELECT * FROM messages WHERE contact_id = ? ORDER BY created_at ASC, id ASC",
            (contact_id,),
        ).fetchall()
        return [
            MessageOut(
                id=r["id"],
                contact_id=r["contact_id"],
                sender=r["sender"],
                content=r["content"],
                source=r["source"] if "source" in r.keys() else "manual",
                generation_history_id=r["generation_history_id"] if "generation_history_id" in r.keys() else None,
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]
    finally:
        conn.close()


@router.post("/contacts/{contact_id}/messages", response_model=MessageOut, status_code=201)
def create_message(contact_id: int, body: MessageCreate):
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="メッセージを入力してください")
    now = database.now_iso()
    conn = database.get_conn()
    try:
        _check_contact(conn, contact_id)

        source = body.source
        gen_hist_id = body.generation_history_id

        # 1. source='manual' の場合: generation_history_id は指定不可
        if source == "manual" and gen_hist_id is not None:
            raise HTTPException(
                status_code=422,
                detail="source='manual' のメッセージに generation_history_id を指定することはできません",
            )

        # 2. source='generated' の場合: generation_history_id 必須 & 厳格検証
        if source == "generated":
            if gen_hist_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="source='generated' のメッセージには generation_history_id が必須です",
                )
            hist = conn.execute(
                "SELECT id, contact_id, generated_text, revised_text FROM generation_history WHERE id = ?",
                (gen_hist_id,),
            ).fetchone()
            if hist is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"指定された generation_history_id ({gen_hist_id}) が存在しません",
                )
            if hist["contact_id"] != contact_id:
                raise HTTPException(
                    status_code=422,
                    detail="generation_history の contact_id が現在の相手と一致しません",
                )

            # 本文一致検証 (generated_text または revised_text との完全一致)
            expected_texts = {
                (hist["revised_text"] or "").strip(),
                (hist["generated_text"] or "").strip(),
            }
            expected_texts.discard("")
            if content not in expected_texts:
                raise HTTPException(
                    status_code=422,
                    detail="送信メッセージの本文が生成履歴のテキストと一致しません",
                )

            # 送信フラグを同一トランザクションで更新
            conn.execute(
                "UPDATE generation_history SET is_sent = 1, is_adopted = 1 WHERE id = ?",
                (gen_hist_id,),
            )
            # 対応するバッチを candidate_sent に更新
            hist_row = conn.execute("SELECT batch_id FROM generation_history WHERE id = ?", (gen_hist_id,)).fetchone()
            if hist_row and hist_row["batch_id"]:
                conn.execute(
                    "UPDATE generation_batches SET outcome = 'candidate_sent', selected_history_id = ? WHERE id = ?",
                    (gen_hist_id, hist_row["batch_id"]),
                )

        cur = conn.execute(
            "INSERT INTO messages (contact_id, sender, content, source, generation_history_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (contact_id, body.sender, content, source, gen_hist_id, now, now),
        )
        new_msg_id = cur.lastrowid

        # 3. self 手入力送信時の Contrast 連携: 未解決バッチを manual_replaced に更新
        if body.sender == "self" and source == "manual":
            open_batch = conn.execute(
                "SELECT id FROM generation_batches "
                "WHERE contact_id = ? AND outcome IN ('pending', 'regenerated') "
                "ORDER BY id DESC LIMIT 1",
                (contact_id,),
            ).fetchone()
            if open_batch:
                conn.execute(
                    "UPDATE generation_batches SET outcome = 'manual_replaced', replacement_message_id = ? WHERE id = ?",
                    (new_msg_id, open_batch["id"]),
                )

        _touch_contact(conn, contact_id)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (new_msg_id,)
        ).fetchone()
        return MessageOut(
            id=row["id"],
            contact_id=row["contact_id"],
            sender=row["sender"],
            content=row["content"],
            source=row["source"],
            generation_history_id=row["generation_history_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    finally:
        conn.close()


@router.patch("/messages/{message_id}", response_model=MessageOut)
def update_message(message_id: int, body: MessageUpdate):
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="メッセージを入力してください")
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="メッセージが見つかりません")
        now = database.now_iso()
        conn.execute(
            "UPDATE messages SET content = ?, updated_at = ? WHERE id = ?",
            (content, now, message_id),
        )
        _touch_contact(conn, row["contact_id"])
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return MessageOut(
            id=updated["id"],
            contact_id=updated["contact_id"],
            sender=updated["sender"],
            content=updated["content"],
            created_at=updated["created_at"],
            updated_at=updated["updated_at"],
        )
    finally:
        conn.close()


@router.delete("/messages/{message_id}", status_code=204)
def delete_message(message_id: int):
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="メッセージが見つかりません")
        conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        _touch_contact(conn, row["contact_id"])
        conn.commit()
    finally:
        conn.close()


@router.get("/contacts/{contact_id}/export", response_class=PlainTextResponse)
def export_chat(contact_id: int):
    """チャット履歴を人間が読めるテキスト形式でエクスポートする。"""
    conn = database.get_conn()
    try:
        contact = conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        if contact is None:
            raise HTTPException(status_code=404, detail="相手が見つかりません")
        rows = conn.execute(
            "SELECT * FROM messages WHERE contact_id = ? ORDER BY created_at ASC, id ASC",
            (contact_id,),
        ).fetchall()
    finally:
        conn.close()

    lines = [f"チャット履歴: {contact['name']}", "=" * 40]
    if contact["profile"]:
        lines.append("プロフィール:")
        lines.append(contact["profile"])
        lines.append("-" * 40)
    for r in rows:
        who = "相手" if r["sender"] == "contact" else "自分"
        lines.append(f"{r['created_at'].replace('T', ' ')[:16]} {who}")
        lines.append(r["content"])
        lines.append("")
    return PlainTextResponse("\n".join(lines), media_type="text/plain; charset=utf-8")
