"""Shared Gemini configuration for live benchmark runners."""
from __future__ import annotations


def build_gemini_benchmark_config(
    *, primary_key: str, secondary_key: str = "", model: str
) -> dict[str, object]:
    config: dict[str, object] = {
        "provider": "gemini",
        "model": model,
        "api_key": primary_key,
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
        "secondary_api_key": secondary_key,
    }
    if model == "gemini-3.5-flash-lite":
        config.update(
            {
                "fallback_provider": "gemini",
                "fallback_model": "gemini-3.1-flash-lite",
                "fallback_api_key": primary_key,
            }
        )
    return config
