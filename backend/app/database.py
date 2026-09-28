"""SQLiteデータベースの初期化と共通アクセス。

データはプロジェクトルートの data/app.db に保存する。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    profile TEXT NOT NULL DEFAULT '',
    is_pinned INTEGER NOT NULL DEFAULT 0,
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    sender TEXT NOT NULL CHECK (sender IN ('contact', 'self')),
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    generation_history_id INTEGER REFERENCES generation_history(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_contact ON messages(contact_id, created_at);

CREATE TABLE IF NOT EXISTS contact_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    current_condition TEXT NOT NULL DEFAULT '',
    generated_text TEXT NOT NULL,
    revision_instruction TEXT NOT NULL DEFAULT '',
    revised_text TEXT NOT NULL DEFAULT '',
    is_adopted INTEGER NOT NULL DEFAULT 0,
    is_copied INTEGER NOT NULL DEFAULT 0,
    is_sent INTEGER NOT NULL DEFAULT 0,
    rating TEXT,
    rating_reason TEXT,
    tone TEXT,
    counterpart_message TEXT,
    batch_id INTEGER REFERENCES generation_batches(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation TEXT NOT NULL,
    ai_response TEXT NOT NULL,
    user_feedback TEXT NOT NULL DEFAULT '',
    corrected_response TEXT NOT NULL DEFAULT '',
    rating INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    messages TEXT NOT NULL DEFAULT '[]',
    persona_name TEXT,
    persona_profile TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES training_sessions(id) ON DELETE CASCADE,
    original_generated TEXT NOT NULL,
    revision_instruction TEXT NOT NULL,
    revised_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS knowledge_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_settings (
    contact_id INTEGER PRIMARY KEY REFERENCES contacts(id) ON DELETE CASCADE,
    provider TEXT,
    model TEXT,
    temperature REAL,
    max_tokens INTEGER,
    history_limit INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_knowledge_files (
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    knowledge_file_id INTEGER NOT NULL REFERENCES knowledge_files(id) ON DELETE CASCADE,
    PRIMARY KEY (contact_id, knowledge_file_id)
);

CREATE TABLE IF NOT EXISTS generation_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    trigger_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    condition TEXT NOT NULL DEFAULT '',
    revision_instruction TEXT NOT NULL DEFAULT '',
    parent_batch_id INTEGER REFERENCES generation_batches(id) ON DELETE SET NULL,
    attempt_no INTEGER NOT NULL DEFAULT 1,
    outcome TEXT NOT NULL DEFAULT 'pending',
    selected_history_id INTEGER REFERENCES generation_history(id) ON DELETE SET NULL,
    replacement_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL DEFAULT '',
    gender TEXT NOT NULL DEFAULT '',
    age TEXT NOT NULL DEFAULT '',
    occupation TEXT NOT NULL DEFAULT '',
    hobbies TEXT NOT NULL DEFAULT '',
    personality TEXT NOT NULL DEFAULT '',
    speaking_style TEXT NOT NULL DEFAULT '',
    profile TEXT NOT NULL DEFAULT '',
    my_info TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generation_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_batch_id INTEGER REFERENCES generation_batches(id) ON DELETE SET NULL,
    history_id INTEGER UNIQUE REFERENCES generation_history(id) ON DELETE CASCADE,
    candidate_index INTEGER NOT NULL DEFAULT 0,
    counterpart_intent TEXT NOT NULL DEFAULT '',
    naturalness_score REAL,
    style_score REAL,
    final_score REAL,
    human_rating TEXT CHECK (human_rating IN ('good', 'neutral', 'bad')),
    human_feedback TEXT NOT NULL DEFAULT '',
    feedback_tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evaluations_batch ON generation_evaluations(generation_batch_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_rating ON generation_evaluations(human_rating);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    config.ensure_dirs()
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO providers (name, is_enabled, created_at) VALUES (?, 1, ?)",
            ("cerebras", now_iso()),
        )
        # 既存DB用マイグレーション: training_sessions に persona_name / persona_profile を追加
        try:
            conn.execute("ALTER TABLE training_sessions ADD COLUMN persona_name TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE training_sessions ADD COLUMN persona_profile TEXT")
        except sqlite3.OperationalError:
            pass
        # 既存DB用マイグレーション: messages に source / generation_history_id を追加
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy_unknown'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN generation_history_id INTEGER REFERENCES generation_history(id) ON DELETE SET NULL")
        except sqlite3.OperationalError:
            pass

        # 既存DB用マイグレーション: legacy_unknown の一括移行（1回のみ実行、冪等性保証）
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            v_row = conn.execute("SELECT version FROM schema_migrations WHERE version = 'v2_legacy_unknown_backfill'").fetchone()
            if not v_row:
                # 1. generation_history と完全一致するものを generated に更新
                hist_rows = conn.execute(
                    "SELECT id, contact_id, generated_text, revised_text FROM generation_history "
                    "WHERE is_sent = 1 OR is_adopted = 1"
                ).fetchall()
                for h in hist_rows:
                    h_id = h["id"]
                    c_id = h["contact_id"]
                    text_to_match = (h["revised_text"] or h["generated_text"] or "").strip()
                    if c_id and text_to_match:
                        conn.execute(
                            "UPDATE messages SET source = 'generated', generation_history_id = ? "
                            "WHERE contact_id = ? AND sender = 'self' AND content = ? AND generation_history_id IS NULL",
                            (h_id, c_id, text_to_match),
                        )
                # 2. 残りの unlinked かつ source='manual' な移行前メッセージを legacy_unknown へ一括更新
                conn.execute(
                    "UPDATE messages SET source = 'legacy_unknown' WHERE generation_history_id IS NULL AND (source = 'manual' OR source IS NULL)"
                )
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES ('v2_legacy_unknown_backfill', ?)",
                    (now_iso(),),
                )
        except Exception as exc:
            logger.warning("Legacy backfill note: %s", exc)
        # 既存DB用マイグレーション: knowledge_files.type に 'training' を許可する
        # （旧スキーマは CHECK 制約で 'rules'/'references' のみ。CHECKは変更できないため再作成する）
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_files'"
        ).fetchone()
        if row and "CHECK" in (row["sql"] or ""):
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.executescript(
                """
                CREATE TABLE knowledge_files_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT INTO knowledge_files_new"
                " (id, type, file_name, file_path, enabled, created_at, updated_at)"
                " SELECT id, type, file_name, file_path, enabled, created_at, updated_at"
                " FROM knowledge_files"
            )
            conn.execute("DROP TABLE knowledge_files")
            conn.execute("ALTER TABLE knowledge_files_new RENAME TO knowledge_files")
            conn.execute("PRAGMA foreign_keys = ON")
            logger.info("knowledge_files スキーマを migration しました（type に training を追加）")
        # 既存DB用マイグレーション: 旧RENAME方式で壊れたFK参照（knowledge_files_old）を修復する
        ck = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'contact_knowledge_files'"
        ).fetchone()
        if ck and "knowledge_files_old" in (ck["sql"] or ""):
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.executescript(
                """
                CREATE TABLE contact_knowledge_files_new (
                    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                    knowledge_file_id INTEGER NOT NULL REFERENCES knowledge_files(id) ON DELETE CASCADE,
                    PRIMARY KEY (contact_id, knowledge_file_id)
                );
                """
            )
            conn.execute(
                "INSERT INTO contact_knowledge_files_new (contact_id, knowledge_file_id)"
                " SELECT contact_id, knowledge_file_id FROM contact_knowledge_files"
            )
            conn.execute("DROP TABLE contact_knowledge_files")
            conn.execute("ALTER TABLE contact_knowledge_files_new RENAME TO contact_knowledge_files")
            conn.execute("PRAGMA foreign_keys = ON")
            logger.info("contact_knowledge_files のFK参照を修復しました")
        # 既存DB用マイグレーション: 旧api_key（グローバル）をプロバイダごとの
        # api_key_{provider} へ移行する。旧キーは当時のプロバイダ用だったもの。
        legacy = conn.execute(
            "SELECT value FROM settings WHERE key = 'api_key'"
        ).fetchone()
        if legacy and str(legacy["value"]).strip():
            prow = conn.execute(
                "SELECT value FROM settings WHERE key = 'ai_provider'"
            ).fetchone()
            provider = (str(prow["value"]) if prow else "").strip().lower() or "cerebras"
            target = f"api_key_{provider}"
            cur = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (target,)
            ).fetchone()
            if not cur or not str(cur["value"]).strip():
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (target, str(legacy["value"])),
                )
                logger.info("API Keyを %s へ移行しました（旧api_key）", target)
            conn.execute("DELETE FROM settings WHERE key = 'api_key'")
        # 既存DB用マイグレーション: user_knowledge テーブルを追加
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS user_knowledge ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "question TEXT NOT NULL, "
                "answer TEXT NOT NULL, "
                "created_at TEXT NOT NULL"
                ")"
            )
        except sqlite3.OperationalError:
            pass
        # 既存DB用マイグレーション: user_profile に my_info を追加
        try:
            conn.execute("ALTER TABLE user_profile ADD COLUMN my_info TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        # 既存DB用マイグレーション: generation_history に rating, rating_reason, tone, counterpart_message, batch_id を追加
        for col_name in ("rating", "rating_reason", "tone", "counterpart_message", "batch_id"):
            try:
                col_type = "INTEGER" if col_name == "batch_id" else "TEXT"
                conn.execute(f"ALTER TABLE generation_history ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
    finally:
        conn.close()
    scan_knowledge_dirs()


def get_learned_preferences() -> str:
    """ユーザーの過去の評価データから、好みの傾向（文字数感、好むトーン、避けるべき傾向等）を抽出する。"""
    conn = get_conn()
    try:
        # 高評価・低評価の取得
        good_rows = conn.execute(
            "SELECT generated_text, revised_text, tone, rating_reason FROM generation_history"
            " WHERE rating = 'good' ORDER BY id DESC LIMIT 50"
        ).fetchall()
        bad_rows = conn.execute(
            "SELECT generated_text, revised_text, tone, rating_reason FROM generation_history"
            " WHERE rating = 'bad' ORDER BY id DESC LIMIT 50"
        ).fetchall()
    finally:
        conn.close()

    if not good_rows and not bad_rows:
        return ""

    lines = []
    if good_rows:
        lengths = [len((r["revised_text"] or r["generated_text"]).strip()) for r in good_rows]
        avg_len = sum(lengths) // len(lengths) if lengths else 0
        reasons = [r["rating_reason"].strip() for r in good_rows if r["rating_reason"] and r["rating_reason"].strip()]
        lines.append(f"- 好まれる返信の長さの目安: 約{avg_len}文字前後")
        if reasons:
            unique_reasons = list(dict.fromkeys(reasons))[:5]
            lines.append(f"- 高評価された理由・特徴: {', '.join(unique_reasons)}")
        # 最新の高評価例（直近2件）
        good_examples = []
        for r in good_rows[:2]:
            text = (r["revised_text"] or r["generated_text"]).strip()
            if text:
                good_examples.append(f"  ・「{text}」")
        if good_examples:
            lines.append("- ユーザーが高評価した過去の返信例:")
            lines.extend(good_examples)

    if bad_rows:
        bad_reasons = [r["rating_reason"].strip() for r in bad_rows if r["rating_reason"] and r["rating_reason"].strip()]
        if bad_reasons:
            unique_bad_reasons = list(dict.fromkeys(bad_reasons))[:5]
            lines.append(f"- 低評価された理由（避けるべき傾向）: {', '.join(unique_bad_reasons)}")
        else:
            lines.append("- 低評価された返信の傾向（冗長な文章、不自然な質問、AI接客口調）を避けること。")

    return "\n".join(lines)



def scan_knowledge_dirs() -> dict:
    """knowledgeフォルダ内のTXTファイルをDBへ登録し、削除されたファイルの登録を解除する。

    フォルダがソース・オブ・トゥルース。
    戻り値: {"rules": {"added": n, "removed": m}, ...} 種別ごとの件数。
    """
    kinds = (
        ("rules", config.KNOWLEDGE_RULES_DIR),
        ("references", config.KNOWLEDGE_REFERENCES_DIR),
        ("training", config.KNOWLEDGE_TRAINING_DIR),
    )
    result: dict[str, dict[str, int]] = {}
    for kind, folder in kinds:
        added = 0
        removed = 0
        conn = get_conn()
        try:
            registered = {
                r["file_path"]: r["id"]
                for r in conn.execute(
                    "SELECT id, file_path FROM knowledge_files WHERE type = ?", (kind,)
                ).fetchall()
            }
            on_disk: set[str] = set()
            for p in sorted(folder.glob("*.txt")):
                on_disk.add(str(p))
                if str(p) not in registered:
                    conn.execute(
                        "INSERT INTO knowledge_files (type, file_name, file_path, enabled, created_at, updated_at)"
                        " VALUES (?, ?, ?, 1, ?, ?)",
                        (kind, p.name, str(p), now_iso(), now_iso()),
                    )
                    logger.info("knowledge file registered: %s", p.name)
                    added += 1
            for fp, fid in registered.items():
                if fp not in on_disk:
                    conn.execute("DELETE FROM knowledge_files WHERE id = ?", (fid,))
                    logger.info("knowledge file unregistered: %s", Path(fp).name)
                    removed += 1
            conn.commit()
        finally:
            conn.close()
        result[kind] = {"added": added, "removed": removed}
    return result


def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_settings() -> dict[str, str]:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


_PROFILE_FIELDS = (
    "name",
    "gender",
    "age",
    "occupation",
    "hobbies",
    "personality",
    "speaking_style",
    "profile",
    "my_info",
)


def get_user_profile() -> dict:
    """AIが演じるユーザー自身のプロフィールを返す（未設定なら空文字）。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
    finally:
        conn.close()
    if row is None:
        return {k: "" for k in _PROFILE_FIELDS} | {"updated_at": ""}
    return dict(row)


def save_user_profile(data: dict) -> dict:
    """ユーザープロフィールを保存する。dataには更新したいフィールドのみ渡す。"""
    conn = get_conn()
    try:
        now = now_iso()
        row = conn.execute("SELECT id FROM user_profile WHERE id = 1").fetchone()
        if row is None:
            fields = {k: "" for k in _PROFILE_FIELDS}
            fields.update(data)
            conn.execute(
                "INSERT INTO user_profile"
                " (id, name, gender, age, occupation, hobbies, personality, speaking_style, profile, my_info, updated_at)"
                " VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fields["name"], fields["gender"], fields["age"], fields["occupation"],
                 fields["hobbies"], fields["personality"], fields["speaking_style"],
                 fields["profile"], fields["my_info"], now),
            )
        else:
            cols = ", ".join(f"{k} = ?" for k in data)
            conn.execute(
                f"UPDATE user_profile SET {cols}, updated_at = ? WHERE id = 1",
                (*data.values(), now),
            )
        conn.commit()
    finally:
        conn.close()
    return get_user_profile()


def get_user_knowledge() -> list[dict]:
    """AIが質問して得たユーザー情報を返す。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, question, answer, created_at FROM user_knowledge ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_user_knowledge(question: str, answer: str) -> dict:
    """AI質問に対するユーザーの回答を保存する。"""
    conn = get_conn()
    try:
        now = now_iso()
        cur = conn.execute(
            "INSERT INTO user_knowledge (question, answer, created_at) VALUES (?, ?, ?)",
            (question, answer, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, question, answer, created_at FROM user_knowledge WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def delete_user_knowledge(knowledge_id: int) -> None:
    """保存済みの質問回答を削除する。"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM user_knowledge WHERE id = ?", (knowledge_id,))
        conn.commit()
    finally:
        conn.close()


def get_user_knowledge_text() -> str:
    """プロンプトに組み込む形式でユーザー情報を返す（無効な破綻メモは除外）。"""
    items = get_user_knowledge()
    if not items:
        return ""
    invalid_keywords = {"しらない", "わかりません", "しらねえよ", "しね", "お前", "まかせうｒ", "ｇ", "g", "しらん"}
    lines = []
    for item in items:
        ans = (item.get("answer") or "").strip()
        if not ans:
            continue
        if any(k in ans for k in invalid_keywords) or len(ans) == 1:
            continue
        lines.append(f"Q: {item['question']}")
        lines.append(f"A: {ans}")
    return "\n".join(lines)
