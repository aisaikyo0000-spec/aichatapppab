"""バックアップの作成・一覧・復元API。

バックアップは data/backups/ にZIPで保存する。
対象: data/（app.db・画像など）、knowledge/、training/
"""
from __future__ import annotations

import logging
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from .. import config, database
from ..schemas import BackupRestore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backup", tags=["backup"])

def _source_dirs() -> tuple:
    return (
        config.DATA_DIR,
        config.PROJECT_ROOT / "knowledge",
        config.PROJECT_ROOT / "training",
    )


def _checkpoint_db() -> None:
    """WALの内容をapp.dbへ反映して、一貫した状態でバックアップできるようにする。"""
    conn = database.get_conn()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


@router.post("")
def create_backup():
    config.ensure_dirs()
    _checkpoint_db()
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"backup_{now}.zip"
    path = config.BACKUPS_DIR / name
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root in _source_dirs():
                for f in sorted(root.rglob("*")):
                    if not f.is_file():
                        continue
                    # backups自身とSQLiteの一時ファイルは除外
                    if config.BACKUPS_DIR in f.parents:
                        continue
                    if f.name.endswith(("-wal", "-shm")):
                        continue
                    zf.write(f, arcname=str(f.relative_to(config.PROJECT_ROOT)))
    except OSError as exc:
        logger.error("backup create failed: %s", exc)
        raise HTTPException(status_code=500, detail="バックアップの作成に失敗しました")
    return {
        "file_name": name,
        "size": path.stat().st_size,
        "created_at": database.now_iso(),
    }


@router.get("")
def list_backups():
    config.ensure_dirs()
    items = []
    for p in sorted(config.BACKUPS_DIR.glob("backup_*.zip"), reverse=True):
        items.append(
            {
                "file_name": p.name,
                "size": p.stat().st_size,
                "created_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return items


@router.post("/restore")
def restore_backup(body: BackupRestore):
    name = Path(body.file_name).name
    path = config.BACKUPS_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="バックアップが見つかりません")

    tmp = config.BACKUPS_DIR / f"_restore_{uuid4().hex}"
    try:
        with zipfile.ZipFile(path) as zf:
            for member in zf.infolist():
                target = (tmp / member.filename).resolve()
                if not str(target).startswith(str(tmp.resolve())):
                    raise HTTPException(status_code=400, detail="不正なバックアップファイルです")
            zf.extractall(tmp)

        _checkpoint_db()

        allowed_roots = ("data", "knowledge", "training")
        copied = 0
        for src in tmp.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(tmp)
            if rel.parts and rel.parts[0] not in allowed_roots:
                continue
            dest = config.PROJECT_ROOT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
        logger.info("backup restored: %s (%d files)", name, copied)
        return {"restored": name, "files": copied}
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="バックアップファイルが壊れています")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
