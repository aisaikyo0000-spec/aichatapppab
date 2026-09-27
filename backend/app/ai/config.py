"""AI生成設定の解決。

優先順位: 設定画面(DB settings) > .env > デフォルト値
API Keyはプロバイダごとに解決する（DB保存 または .env環境変数）。
"""
from __future__ import annotations

import os
from typing import Any

from .. import config as app_config
from ..database import get_conn, get_setting

# プロバイダごとのAPI Key環境変数名
_PROVIDER_ENV_KEYS: dict[str, str] = {
    "cerebras": "CEREBRAS_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def _float_setting(key: str, default: float) -> float:
    try:
        return float(get_setting(key, os.getenv(key.upper(), str(default))))
    except ValueError:
        return default


def _int_setting(key: str, default: int) -> int:
    try:
        return int(get_setting(key, os.getenv(key.upper(), str(default))))
    except ValueError:
        return default


def _db_api_key(provider: str) -> str:
    """プロバイダ用のDB保存API Key。設定画面で登録されたキー。"""
    p = (provider or "").strip().lower()
    return get_setting(f"api_key_{p}", "").strip()


def _env_api_key(provider: str) -> str:
    """プロバイダ用のAPI Keyを.env環境変数から取得する。

    OS環境変数に古いキーが残っていても邪魔されないよう、.envファイルの値を優先し、
    .envが空のときだけOS環境変数にフォールバックする。
    """
    p = (provider or "").strip().lower()
    env_name = _PROVIDER_ENV_KEYS.get(p)
    if not env_name:
        return ""
    dotenv_val = _read_dotenv(env_name)
    if dotenv_val:
        return dotenv_val
    return os.getenv(env_name, "").strip()


def _read_dotenv(name: str) -> str:
    """.envファイルから指定キーの値を読む（空なら""）。

    ファイルサイズが小さいため毎回読む（.envを編集したら再起動なしで反映される）。
    """
    path = app_config.PROJECT_ROOT / ".env"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip()
    except OSError:
        pass
    return ""


def get_ai_config() -> dict[str, Any]:
    """現在のAI設定を解決して返す。API Keyはここで扱うがログへは出力しない。"""
    provider = get_setting(
        "ai_provider", os.getenv("AI_PROVIDER", app_config.DEFAULT_PROVIDER)
    ).strip()
    model = get_setting("ai_model", os.getenv("AI_MODEL", app_config.DEFAULT_MODEL)).strip()

    # メインのAPI Key: 設定画面(DB) > .env環境変数
    db_key = _db_api_key(provider)
    api_key = db_key or _env_api_key(provider)

    # フォールバック: 設定画面(DB) > AI_FALLBACK_API_KEY > フォールバック先プロバイダの環境変数
    fallback_provider = get_setting(
        "ai_fallback_provider", os.getenv("AI_FALLBACK_PROVIDER", "")
    ).strip()
    fallback_model = get_setting(
        "ai_fallback_model", os.getenv("AI_FALLBACK_MODEL", "")
    ).strip()
    fb_db_key = get_setting("ai_fallback_api_key", "").strip()
    fb_env_key = os.getenv("AI_FALLBACK_API_KEY", "").strip()
    fallback_api_key = fb_db_key or fb_env_key or _env_api_key(fallback_provider)

    return {
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "api_key_from_env": not db_key,
        "temperature": _float_setting("ai_temperature", app_config.DEFAULT_TEMPERATURE),
        "max_tokens": _int_setting("ai_max_tokens", app_config.DEFAULT_MAX_TOKENS),
        "history_limit": _int_setting("ai_history_limit", app_config.DEFAULT_HISTORY_LIMIT),
        # フォールバック設定（主プロバイダがレート制限等で失敗した時に使用）
        "fallback_provider": fallback_provider,
        "fallback_model": fallback_model,
        "fallback_api_key": fallback_api_key,
        "fallback_api_key_from_env": not fb_db_key,
    }


def get_contact_ai_config(
    contact_id: int, global_cfg: dict[str, Any]
) -> tuple[dict[str, Any], list[int]]:
    """相手ごとのAI設定を解決する。

    相手に設定がなければ全体設定をそのまま使う。
    戻り値: (有効な設定, 相手専用に選ばれた知識ファイルIDリスト)
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM contact_settings WHERE contact_id = ?", (contact_id,)
        ).fetchone()
        knowledge_ids = [
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
        return dict(global_cfg), knowledge_ids

    cfg = dict(global_cfg)
    if row["provider"]:
        cfg["provider"] = row["provider"]
    if row["model"]:
        cfg["model"] = row["model"]
    if row["temperature"] is not None:
        cfg["temperature"] = float(row["temperature"])
    if row["max_tokens"] is not None:
        cfg["max_tokens"] = int(row["max_tokens"])
    if row["history_limit"] is not None:
        cfg["history_limit"] = int(row["history_limit"])
    return cfg, knowledge_ids
