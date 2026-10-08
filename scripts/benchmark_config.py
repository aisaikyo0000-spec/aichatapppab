"""Shared Gemini configuration for live benchmark runners."""
from __future__ import annotations

import json
from pathlib import Path


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
    config: dict[str, object],
    *,
    api_key: str,
    model: str,
    route_state_path: Path | None = None,
) -> int | None:
    """Persist the last successful account/model without saving credential data."""
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
            if route_state_path is not None:
                route_state_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = route_state_path.with_name(
                    f"{route_state_path.name}.tmp"
                )
                temp_path.write_text(
                    json.dumps(
                        {
                            "account": attempt.get("account"),
                            "model": attempt.get("model"),
                        }
                    ),
                    encoding="utf-8",
                )
                temp_path.replace(route_state_path)
            return index
    return None


def load_gemini_benchmark_route(
    config: dict[str, object], route_state_path: Path
) -> int | None:
    """Resume from a prior benchmark's account/model if that route still exists."""
    try:
        route = json.loads(route_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(route, dict):
        return None
    account = route.get("account")
    model = route.get("model")
    if not isinstance(account, str) or not isinstance(model, str):
        return None

    attempts = config.get("quota_attempts")
    if not isinstance(attempts, list):
        return None
    for index, attempt in enumerate(attempts):
        if (
            isinstance(attempt, dict)
            and attempt.get("account") == account
            and attempt.get("model") == model
        ):
            config["quota_attempt_start_index"] = index
            return index
    return None
