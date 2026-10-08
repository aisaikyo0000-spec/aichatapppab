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
    primary_models = [model]
    if model == "gemini-3.5-flash-lite":
        primary_models.append("gemini-3.1-flash-lite")
    secondary_models = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    quota_attempts = [
        {
            "provider": "gemini",
            "model": candidate_model,
            "api_key": primary_key,
            "account": "primary",
        }
        for candidate_model in primary_models
    ]
    if secondary_key and secondary_key != primary_key:
        quota_attempts.extend(
            {
                "provider": "gemini",
                "model": candidate_model,
                "api_key": secondary_key,
                "account": "secondary",
            }
            for candidate_model in secondary_models
        )
    config["quota_attempts"] = quota_attempts
    config["quota_attempt_start_index"] = 0
    if model == "gemini-3.5-flash-lite":
        config.update(
            {
                "fallback_provider": "gemini",
                "fallback_model": "gemini-3.1-flash-lite",
                "fallback_api_key": primary_key,
            }
        )
    return config


def record_gemini_benchmark_success(
    config: dict[str, object], *, api_key: str, model: str
) -> int | None:
    """Start the next isolated benchmark request at the last successful route."""
    attempts = config.get("quota_attempts")
    if not isinstance(attempts, list):
        return None
    for index, attempt in enumerate(attempts):
        if (
            isinstance(attempt, dict)
            and attempt.get("api_key") == api_key
            and attempt.get("model") == model
        ):
            config["quota_attempt_start_index"] = index
            return index
    return None
