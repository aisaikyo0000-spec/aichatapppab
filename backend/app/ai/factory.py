"""AI Providerのファクトリ。

Provider名から対応するAIProviderインスタンスを生成する。
新しいProviderを追加する場合はここに登録する。
"""
from __future__ import annotations

from .base import AIError, AIProvider
from .cerebras import CerebrasProvider
from .gemini import GeminiProvider
from .nvidia import NVIDIAProvider

_REGISTRY: dict[str, type[AIProvider]] = {
    "cerebras": CerebrasProvider,
    "nvidia": NVIDIAProvider,
    "gemini": GeminiProvider,
}


class FallbackConfigError(AIError):
    """フォールバック設定が不完全なときのエラー。"""

    def __init__(self, message: str):
        super().__init__(message, code="provider_error")


def get_provider(provider_name: str, api_key: str) -> AIProvider:
    name = (provider_name or "").strip().lower()
    cls = _REGISTRY.get(name)
    if cls is None:
        raise AIError(f"未対応のAI Providerです: {provider_name}", code="provider_error")
    return cls(api_key)


def list_providers() -> list[dict]:
    result: list[dict] = []
    for name, cls in _REGISTRY.items():
        infos = cls(api_key="").model_infos() if hasattr(cls, "model_infos") else [{"name": m} for m in cls.MODELS]
        result.append({"name": name, "models": [i["name"] for i in infos], "model_infos": infos})
    return result


def get_fallback(cfg: dict) -> tuple[AIProvider, dict] | None:
    """フォールバック設定があれば (プロバイダ, 設定) を返す。未設定ならNone。

    主プロバイダがレート制限などで失敗したときに使用する。
    """
    name = (cfg.get("fallback_provider") or "").strip()
    api_key = (cfg.get("fallback_api_key") or "").strip()
    if not name or not api_key:
        return None
    provider = get_provider(name, api_key)
    model = (cfg.get("fallback_model") or "").strip()
    if not model:
        model = provider.available_models()[0]
    fb_cfg = {
        "provider": name,
        "model": model,
        "api_key": api_key,
        "temperature": cfg.get("temperature", 0.7),
        "max_tokens": cfg.get("max_tokens", 1024),
    }
    return provider, fb_cfg
