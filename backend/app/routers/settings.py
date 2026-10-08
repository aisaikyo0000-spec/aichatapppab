"""設定の取得・更新API。

API Keyの実値をFrontendへ返さない。has_api_keyのみ返す。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from ..ai import factory
from ..ai.config import get_ai_config
from ..database import get_setting, set_setting
from ..schemas import SettingsUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings():
    cfg = get_ai_config()
    return {
        "provider": cfg["provider"],
        "model": cfg["model"],
        "has_api_key": bool(cfg["api_key"]),
        "api_key_env": bool(cfg.get("api_key_from_env")),
        "temperature": cfg["temperature"],
        "max_tokens": cfg["max_tokens"],
        "history_limit": cfg["history_limit"],
        "fallback_provider": cfg.get("fallback_provider", ""),
        "fallback_model": cfg.get("fallback_model", ""),
        "has_fallback_api_key": bool(cfg.get("fallback_api_key")),
        "fallback_api_key_env": bool(cfg.get("fallback_api_key_from_env")),
        "has_secondary_api_key": bool(cfg.get("secondary_api_key")),
        "providers": factory.list_providers(),
    }


@router.put("")
def update_settings(body: SettingsUpdate):
    if body.provider is not None:
        set_setting("ai_provider", body.provider.strip())
    if body.model is not None:
        set_setting("ai_model", body.model.strip())
    # API Keyはプロバイダごとに保存する（api_key_{provider}）。
    # 空文字の場合は「変更しない」扱い。明示的に消すのは clear_api_key=True のみ。
    provider = (body.provider or get_setting("ai_provider", "")).strip().lower()
    if body.api_key is not None and body.api_key.strip():
        set_setting(f"api_key_{provider}", body.api_key.strip())
    elif body.clear_api_key:
        set_setting(f"api_key_{provider}", "")
    if body.temperature is not None:
        set_setting("ai_temperature", str(round(body.temperature, 2)))
    if body.max_tokens is not None:
        set_setting("ai_max_tokens", str(body.max_tokens))
    if body.history_limit is not None:
        set_setting("ai_history_limit", str(body.history_limit))
    if body.fallback_provider is not None:
        set_setting("ai_fallback_provider", body.fallback_provider.strip())
    if body.fallback_model is not None:
        set_setting("ai_fallback_model", body.fallback_model.strip())
    if body.fallback_api_key is not None and body.fallback_api_key.strip():
        set_setting("ai_fallback_api_key", body.fallback_api_key.strip())
    elif body.clear_fallback_api_key:
        set_setting("ai_fallback_api_key", "")
    return get_settings()
