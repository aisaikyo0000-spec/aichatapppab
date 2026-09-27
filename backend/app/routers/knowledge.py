"""ルール・参考資料（knowledgeファイル）の管理API。"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import config, database
from ..schemas import KnowledgeFileCreate, KnowledgeFileUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _dir_for(kind: str) -> Path:
    if kind == "rules":
        return config.KNOWLEDGE_RULES_DIR
    if kind == "references":
        return config.KNOWLEDGE_REFERENCES_DIR
    return config.KNOWLEDGE_TRAINING_DIR


def _safe_file_name(raw: str) -> str:
    name = Path(raw).name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="ファイル名を入力してください")
    if not name.lower().endswith(".txt"):
        name += ".txt"
    return name


@router.post("/reload")
def reload_knowledge():
    """各フォルダ（rules / references / training）を再スキャンして読み込む。

    フォルダに新しく追加されたTXTファイルを登録し、
    フォルダから削除されたファイルの登録を解除する。
    """
    counts = database.scan_knowledge_dirs()
    return {
        "counts": counts,
        "added": sum(c["added"] for c in counts.values()),
        "removed": sum(c["removed"] for c in counts.values()),
    }


@router.get("")
def list_knowledge(type: str | None = None):
    conn = database.get_conn()
    try:
        if type:
            rows = conn.execute(
                "SELECT * FROM knowledge_files WHERE type = ? ORDER BY file_name",
                (type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM knowledge_files ORDER BY type, file_name"
            ).fetchall()
        return [
            {
                "id": r["id"],
                "type": r["type"],
                "file_name": r["file_name"],
                "enabled": bool(r["enabled"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


@router.post("", status_code=201)
def create_knowledge(body: KnowledgeFileCreate):
    file_name = _safe_file_name(body.file_name)
    folder = _dir_for(body.type)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_name
    if path.exists():
        raise HTTPException(status_code=409, detail="同名のファイルがすでに存在します")

    try:
        path.write_text(body.content, encoding="utf-8")
    except OSError as exc:
        logger.error("knowledge file write failed: %s", exc)
        raise HTTPException(status_code=500, detail="ファイルの保存に失敗しました")

    now = database.now_iso()
    conn = database.get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO knowledge_files (type, file_name, file_path, enabled, created_at, updated_at)"
            " VALUES (?, ?, ?, 1, ?, ?)",
            (body.type, file_name, str(path), now, now),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "type": body.type,
            "file_name": file_name,
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        }
    finally:
        conn.close()


@router.get("/{file_id}")
def get_knowledge(file_id: int):
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_files WHERE id = ?", (file_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="ファイルが見つかりません")
        path = Path(row["file_path"]) if row["file_path"] else _dir_for(row["type"]) / row["file_name"]
        content = ""
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            content = ""
        return {
            "id": row["id"],
            "type": row["type"],
            "file_name": row["file_name"],
            "enabled": bool(row["enabled"]),
            "content": content,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


@router.patch("/{file_id}")
def update_knowledge(file_id: int, body: KnowledgeFileUpdate):
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_files WHERE id = ?", (file_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="ファイルが見つかりません")

        folder = _dir_for(row["type"])
        folder.mkdir(parents=True, exist_ok=True)
        current_path = Path(row["file_path"]) if row["file_path"] else folder / row["file_name"]

        new_name = None
        if body.file_name is not None:
            new_name = _safe_file_name(body.file_name)
            if new_name != row["file_name"]:
                new_path = folder / new_name
                if new_path.exists():
                    raise HTTPException(status_code=409, detail="同名のファイルがすでに存在します")
                try:
                    if current_path.exists():
                        current_path.rename(new_path)
                except OSError as exc:
                    logger.error("knowledge file rename failed: %s", exc)
                    raise HTTPException(status_code=500, detail="ファイル名の変更に失敗しました")

        if body.content is not None:
            path = folder / (new_name or row["file_name"])
            try:
                path.write_text(body.content, encoding="utf-8")
            except OSError as exc:
                logger.error("knowledge file write failed: %s", exc)
                raise HTTPException(status_code=500, detail="ファイルの保存に失敗しました")

        now = database.now_iso()
        if new_name is not None and new_name != row["file_name"]:
            conn.execute(
                "UPDATE knowledge_files SET file_name = ?, file_path = ?, updated_at = ? WHERE id = ?",
                (new_name, str(folder / new_name), now, file_id),
            )
            conn.commit()
        elif body.content is not None or body.enabled is not None:
            conn.execute(
                "UPDATE knowledge_files SET updated_at = ? WHERE id = ?", (now, file_id)
            )
            conn.commit()
        if body.enabled is not None:
            conn.execute(
                "UPDATE knowledge_files SET enabled = ? WHERE id = ?",
                (1 if body.enabled else 0, file_id),
            )
            conn.commit()
        row = conn.execute(
            "SELECT * FROM knowledge_files WHERE id = ?", (file_id,)
        ).fetchone()
        return {
            "id": row["id"],
            "type": row["type"],
            "file_name": row["file_name"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


@router.delete("/{file_id}", status_code=204)
def delete_knowledge(file_id: int):
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_files WHERE id = ?", (file_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="ファイルが見つかりません")
        # DBレコードを削除してからファイルを削除する
        conn.execute("DELETE FROM knowledge_files WHERE id = ?", (file_id,))
        conn.commit()
        try:
            path = Path(row["file_path"])
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.warning("knowledge file delete failed: %s", exc)
    finally:
        conn.close()
