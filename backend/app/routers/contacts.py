"""相手（コンタクト）のCRUD・ピン留め・アーカイブ・相手別AI設定API。"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import config, database
from ..schemas import ContactAiSettings, ContactCreate, ContactOut, ContactUpdate

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


def _to_out(row, profile_image_url: str | None = None) -> ContactOut:
    return ContactOut(
        id=row["id"],
        name=row["name"],
        profile=row["profile"],
        is_pinned=bool(row["is_pinned"]),
        is_archived=bool(row["is_archived"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_message=row["last_message"] if "last_message" in row.keys() else "",
        last_message_at=row["last_message_at"] if "last_message_at" in row.keys() else None,
        first_contact_message_at=row["first_contact_message_at"] if "first_contact_message_at" in row.keys() else None,
        contact_message_count=row["contact_message_count"] if "contact_message_count" in row.keys() else 0,
        profile_image_url=(
            row["profile_image_url"] if "profile_image_url" in row.keys() else profile_image_url
        ),
    )


def _profile_image_url(conn, contact_id: int) -> str | None:
    """先頭（sort_order最小）の画像をプロフィール画像として返す。"""
    row = conn.execute(
        "SELECT id FROM contact_images WHERE contact_id = ?"
        " ORDER BY sort_order ASC, id ASC LIMIT 1",
        (contact_id,),
    ).fetchone()
    return f"/api/images/{row['id']}/file" if row else None


def _get_or_404(conn, contact_id: int):
    sql = (
        "SELECT c.*,"
        " COALESCE((SELECT content FROM messages m WHERE m.contact_id = c.id"
        "  ORDER BY m.created_at DESC, m.id DESC LIMIT 1), '') AS last_message,"
        " (SELECT created_at FROM messages m WHERE m.contact_id = c.id"
        "  ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS last_message_at,"
        " (SELECT created_at FROM messages m WHERE m.contact_id = c.id AND m.sender = 'contact'"
        "  ORDER BY m.created_at ASC, m.id ASC LIMIT 1) AS first_contact_message_at,"
        " (SELECT COUNT(*) FROM messages m WHERE m.contact_id = c.id AND m.sender = 'contact') AS contact_message_count,"
        " (SELECT '/api/images/' || ci.id || '/file' FROM contact_images ci"
        "  WHERE ci.contact_id = c.id ORDER BY ci.sort_order ASC, ci.id ASC LIMIT 1)"
        "  AS profile_image_url"
        " FROM contacts c WHERE c.id = ?"
    )
    row = conn.execute(sql, (contact_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="相手が見つかりません")
    return row


@router.get("", response_model=list[ContactOut])
def list_contacts(include_archived: bool = False, search: str = ""):
    conn = database.get_conn()
    try:
        sql = (
            "SELECT c.*,"
            " COALESCE((SELECT content FROM messages m WHERE m.contact_id = c.id"
            "  ORDER BY m.created_at DESC, m.id DESC LIMIT 1), '') AS last_message,"
            " (SELECT created_at FROM messages m WHERE m.contact_id = c.id"
            "  ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS last_message_at,"
            " (SELECT created_at FROM messages m WHERE m.contact_id = c.id AND m.sender = 'contact'"
            "  ORDER BY m.created_at ASC, m.id ASC LIMIT 1) AS first_contact_message_at,"
            " (SELECT COUNT(*) FROM messages m WHERE m.contact_id = c.id AND m.sender = 'contact') AS contact_message_count,"
            " (SELECT '/api/images/' || ci.id || '/file' FROM contact_images ci"
            "  WHERE ci.contact_id = c.id ORDER BY ci.sort_order ASC, ci.id ASC LIMIT 1)"
            "  AS profile_image_url"
            " FROM contacts c"
        )
        conds: list[str] = []
        params: list = []
        if not include_archived:
            conds.append("c.is_archived = 0")
        if search:
            conds.append(
                "(c.name LIKE ? OR c.profile LIKE ? OR EXISTS ("
                "SELECT 1 FROM messages m WHERE m.contact_id = c.id AND m.content LIKE ?"
                "))"
            )
            p = f"%{search}%"
            params += [p, p, p]
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY c.is_pinned DESC, c.updated_at DESC"
        return [_to_out(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


@router.post("", response_model=ContactOut, status_code=201)
def create_contact(body: ContactCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="名前は必須です")
    now = database.now_iso()
    conn = database.get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO contacts (name, profile, is_pinned, is_archived, created_at, updated_at)"
            " VALUES (?, ?, 0, 0, ?, ?)",
            (name, body.profile, now, now),
        )
        conn.commit()
        return _to_out(_get_or_404(conn, cur.lastrowid))
    finally:
        conn.close()


@router.get("/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: int):
    conn = database.get_conn()
    try:
        return _to_out(_get_or_404(conn, contact_id), _profile_image_url(conn, contact_id))
    finally:
        conn.close()


@router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(contact_id: int, body: ContactUpdate):
    conn = database.get_conn()
    try:
        row = _get_or_404(conn, contact_id)
        updates: list[str] = []
        params: list = []
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(status_code=422, detail="名前は必須です")
            updates.append("name = ?")
            params.append(name)
        if body.profile is not None:
            updates.append("profile = ?")
            params.append(body.profile)
        if body.is_pinned is not None:
            updates.append("is_pinned = ?")
            params.append(1 if body.is_pinned else 0)
        if body.is_archived is not None:
            updates.append("is_archived = ?")
            params.append(1 if body.is_archived else 0)
        updates.append("updated_at = ?")
        params.append(database.now_iso())
        params.append(contact_id)
        conn.execute(
            f"UPDATE contacts SET {', '.join(updates)} WHERE id = ?", params
        )
        conn.commit()
        return _to_out(_get_or_404(conn, contact_id), _profile_image_url(conn, contact_id))
    finally:
        conn.close()


@router.delete("/{contact_id}", status_code=204)
def delete_contact(contact_id: int):
    """相手を完全削除する（チャット・画像・生成履歴も削除）。"""
    conn = database.get_conn()
    try:
        _get_or_404(conn, contact_id)
        image_rows = conn.execute(
            "SELECT file_path FROM contact_images WHERE contact_id = ?", (contact_id,)
        ).fetchall()
        conn.execute("DELETE FROM generation_history WHERE contact_id = ?", (contact_id,))
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        conn.commit()
    finally:
        conn.close()
    for row in image_rows:
        try:
            p = Path(row["file_path"])
            if p.exists():
                p.unlink()
        except OSError:
            pass
    try:
        folder = config.CONTACTS_IMAGE_DIR / str(contact_id)
        if folder.exists():
            shutil.rmtree(folder)
    except OSError:
        pass


@router.get("/{contact_id}/ai-settings", response_model=ContactAiSettings)
def get_contact_ai_settings(contact_id: int):
    """相手ごとのAI設定を取得する。未設定なら全てNone（全体設定を使う）。"""
    conn = database.get_conn()
    try:
        _get_or_404(conn, contact_id)
        row = conn.execute(
            "SELECT * FROM contact_settings WHERE contact_id = ?", (contact_id,)
        ).fetchone()
        ids = [
            r["knowledge_file_id"]
            for r in conn.execute(
                "SELECT knowledge_file_id FROM contact_knowledge_files"
                " WHERE contact_id = ? ORDER BY knowledge_file_id",
                (contact_id,),
            ).fetchall()
        ]
    finally:
        conn.close()
    if not row:
        return ContactAiSettings()
    return ContactAiSettings(
        provider=row["provider"] or None,
        model=row["model"] or None,
        temperature=row["temperature"],
        max_tokens=row["max_tokens"],
        history_limit=row["history_limit"],
        knowledge_file_ids=ids or None,
    )


@router.put("/{contact_id}/ai-settings", response_model=ContactAiSettings)
def update_contact_ai_settings(contact_id: int, body: ContactAiSettings):
    """相手ごとのAI設定を保存する。Noneの項目は全体設定を使う。"""
    conn = database.get_conn()
    try:
        _get_or_404(conn, contact_id)
        now = database.now_iso()
        conn.execute(
            "INSERT INTO contact_settings"
            " (contact_id, provider, model, temperature, max_tokens, history_limit, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(contact_id) DO UPDATE SET"
            " provider = excluded.provider, model = excluded.model,"
            " temperature = excluded.temperature, max_tokens = excluded.max_tokens,"
            " history_limit = excluded.history_limit, updated_at = excluded.updated_at",
            (
                contact_id,
                body.provider or None,
                body.model or None,
                body.temperature,
                body.max_tokens,
                body.history_limit,
                now,
            ),
        )
        conn.execute(
            "DELETE FROM contact_knowledge_files WHERE contact_id = ?", (contact_id,)
        )
        if body.knowledge_file_ids:
            for fid in body.knowledge_file_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO contact_knowledge_files"
                    " (contact_id, knowledge_file_id) VALUES (?, ?)",
                    (contact_id, fid),
                )
        conn.commit()
    finally:
        conn.close()
    return get_contact_ai_settings(contact_id)
