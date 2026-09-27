"""相手のプロフィール画像の管理API。

画像は data/contacts/{contact_id}/ 配下に保存し、パスはDBで管理する。
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import config, database
from ..schemas import ImageUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["images"])

MAX_IMAGES = 20
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _to_dict(row) -> dict:
    return {
        "id": row["id"],
        "contact_id": row["contact_id"],
        "description": row["description"],
        "sort_order": row["sort_order"],
        "created_at": row["created_at"],
        "url": f"/api/images/{row['id']}/file",
    }


def _check_contact(conn, contact_id: int) -> None:
    row = conn.execute(
        "SELECT id FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="相手が見つかりません")


@router.get("/contacts/{contact_id}/images")
def list_images(contact_id: int):
    conn = database.get_conn()
    try:
        _check_contact(conn, contact_id)
        rows = conn.execute(
            "SELECT * FROM contact_images WHERE contact_id = ? ORDER BY sort_order ASC, id ASC",
            (contact_id,),
        ).fetchall()
        return [_to_dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/contacts/{contact_id}/images", status_code=201)
async def upload_image(contact_id: int, file: UploadFile, description: str = Form("")):
    filename = Path(file.filename or "").name
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="対応形式は jpg / png / gif / webp です",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="画像ファイルが空です")

    conn = database.get_conn()
    try:
        _check_contact(conn, contact_id)
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM contact_images WHERE contact_id = ?",
            (contact_id,),
        ).fetchone()["c"]
        if count >= MAX_IMAGES:
            raise HTTPException(status_code=400, detail=f"画像は最大{MAX_IMAGES}枚までです")
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM contact_images WHERE contact_id = ?",
            (contact_id,),
        ).fetchone()["m"]
    finally:
        conn.close()

    folder = config.CONTACTS_IMAGE_DIR / str(contact_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid4().hex}{ext}"
    try:
        path.write_bytes(content)
    except OSError as exc:
        logger.error("image write failed: %s", exc)
        raise HTTPException(status_code=500, detail="画像の保存に失敗しました")

    now = database.now_iso()
    conn = database.get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO contact_images (contact_id, file_path, description, sort_order, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (contact_id, str(path), description, max_order + 1, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM contact_images WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _to_dict(row)
    finally:
        conn.close()


def _get_image_row_or_404(conn, image_id: int):
    row = conn.execute(
        "SELECT * FROM contact_images WHERE id = ?", (image_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    return row


@router.get("/images/{image_id}/file")
def get_image_file(image_id: int):
    conn = database.get_conn()
    try:
        row = _get_image_row_or_404(conn, image_id)
    finally:
        conn.close()
    path = Path(row["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="画像ファイルが見つかりません")
    return FileResponse(path)


@router.patch("/images/{image_id}")
def update_image(image_id: int, body: ImageUpdate):
    conn = database.get_conn()
    try:
        _get_image_row_or_404(conn, image_id)
        conn.execute(
            "UPDATE contact_images SET description = ?, sort_order = ? WHERE id = ?",
            (body.description, body.sort_order if body.sort_order is not None else 0, image_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM contact_images WHERE id = ?", (image_id,)
        ).fetchone()
        return _to_dict(row)
    finally:
        conn.close()


@router.delete("/images/{image_id}", status_code=204)
def delete_image(image_id: int):
    conn = database.get_conn()
    try:
        row = _get_image_row_or_404(conn, image_id)
        conn.execute("DELETE FROM contact_images WHERE id = ?", (image_id,))
        conn.commit()
    finally:
        conn.close()
    try:
        path = Path(row["file_path"])
        if path.exists():
            path.unlink()
    except OSError as exc:
        logger.warning("image file delete failed: %s", exc)
