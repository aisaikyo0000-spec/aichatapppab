"""Production Gemini defaults and model fallback behavior."""
from __future__ import annotations

import json

from app import database
from app.ai.config import get_ai_config
from app.ai.base import AIError


def test_fresh_install_defaults_to_gemini_35_with_gemini_31_fallback(client, monkeypatch):
    """A fresh database should be ready for the intended 3.5 -> 3.1 setup."""
    for name in (
        "AI_PROVIDER",
        "AI_MODEL",
        "AI_FALLBACK_PROVIDER",
        "AI_FALLBACK_MODEL",
        "AI_FALLBACK_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = get_ai_config()

    assert cfg["provider"] == "gemini"
    assert cfg["model"] == "gemini-3.5-flash-lite"
    assert cfg["fallback_provider"] == "gemini"
    assert cfg["fallback_model"] == "gemini-3.1-flash-lite"

    settings = client.get("/api/settings")
    assert settings.status_code == 200
    assert settings.json()["provider"] == "gemini"
    assert settings.json()["model"] == "gemini-3.5-flash-lite"
    assert settings.json()["fallback_provider"] == "gemini"
    assert settings.json()["fallback_model"] == "gemini-3.1-flash-lite"


def test_gemini_primary_key_is_reused_for_same_provider_fallback_without_exposure(
    client, monkeypatch,
):
    monkeypatch.delenv("AI_FALLBACK_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    secret = "gemini-db-secret-for-test"
    database.set_setting("ai_provider", "gemini")
    database.set_setting("ai_model", "gemini-3.5-flash-lite")
    database.set_setting("api_key_gemini", secret)
    database.set_setting("ai_fallback_provider", "gemini")
    database.set_setting("ai_fallback_model", "gemini-3.1-flash-lite")
    database.set_setting("ai_fallback_api_key", "")

    cfg = get_ai_config()
    assert cfg["api_key"] == secret
    assert cfg["fallback_api_key"] == secret

    settings = client.get("/api/settings")
    assert settings.status_code == 200
    body = settings.json()
    assert body["has_api_key"] is True
    assert body["has_fallback_api_key"] is True
    assert secret not in settings.text
    assert "api_key" not in body
    assert "fallback_api_key" not in body


def test_explicit_fallback_environment_key_is_reported_as_environment_source(
    client, monkeypatch
):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("AI_FALLBACK_API_KEY", "fallback-env-secret")
    database.set_setting("ai_provider", "gemini")
    database.set_setting("ai_model", "gemini-3.5-flash-lite")
    database.set_setting("api_key_gemini", "primary-db-secret")
    database.set_setting("ai_fallback_provider", "gemini")
    database.set_setting("ai_fallback_model", "gemini-3.1-flash-lite")
    database.set_setting("ai_fallback_api_key", "")

    cfg = get_ai_config()
    assert cfg["fallback_api_key"] == "fallback-env-secret"
    assert cfg["fallback_api_key_from_env"] is True

    settings = client.get("/api/settings")
    assert settings.status_code == 200
    assert settings.json()["fallback_api_key_env"] is True
    assert "fallback-env-secret" not in settings.text


def _configure_generation(
    monkeypatch,
    *,
    primary_behavior: str,
    fallback_responses: list[str] | None = None,
):
    calls: list[tuple[str, str]] = []
    fallback_calls: list[str] = []
    queued_fallback_responses = list(fallback_responses or [])

    reply = "いいですね！\n楽しんできてください😊"

    class PrimaryProvider:
        name = "gemini"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            calls.append(("primary", model))
            if primary_behavior == "rate_limit":
                raise AIError("quota reached", code="rate_limit")
            if primary_behavior == "empty_response":
                raise AIError("empty response", code="empty_response")
            if primary_behavior == "provider_error":
                raise AIError("upstream failed", code="provider_error")
            return json.dumps({"replies": [reply]}) if json_mode else reply

        def available_models(self):
            return ["gemini-3.5-flash-lite"]

    class FallbackProvider:
        name = "gemini"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            fallback_calls.append(model)
            calls.append(("fallback", model))
            if queued_fallback_responses:
                return queued_fallback_responses.pop(0)
            return json.dumps({"replies": [reply]}) if json_mode else reply

        def available_models(self):
            return ["gemini-3.1-flash-lite"]

    primary = PrimaryProvider()
    fallback = FallbackProvider()

    def get_provider(provider_name, api_key):
        if api_key == "primary-key":
            return primary
        if api_key == "fallback-key":
            return fallback
        raise AssertionError(f"unexpected provider key for {provider_name}")

    monkeypatch.setattr("app.routers.generation.factory.get_provider", get_provider)
    monkeypatch.setattr(
        "app.routers.generation.get_ai_config",
        lambda: {
            "provider": "gemini",
            "model": "gemini-3.5-flash-lite",
            "api_key": "primary-key",
            "temperature": 0.8,
            "max_tokens": 512,
            "history_limit": 50,
            "fallback_provider": "gemini",
            "fallback_model": "gemini-3.1-flash-lite",
            "fallback_api_key": "fallback-key",
        },
    )
    monkeypatch.setattr("app.routers.generation.time.sleep", lambda *_: None)
    return calls, fallback_calls


def _generate_once(client):
    cid = client.post("/api/contacts", json={"name": "テストさん"}).json()["id"]
    client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "contact", "content": "カフェ行きたい"},
    )
    response = client.post(
        "/api/generate",
        json={"contact_id": cid, "condition": "", "candidates": 1},
    )
    return cid, response


def test_generation_falls_back_once_immediately_on_rate_limit_and_records_used_model(
    client, monkeypatch
):
    calls, fallback_calls = _configure_generation(
        monkeypatch, primary_behavior="rate_limit"
    )

    cid, response = _generate_once(client)

    assert response.status_code == 200
    assert calls == [
        ("primary", "gemini-3.5-flash-lite"),
        ("fallback", "gemini-3.1-flash-lite"),
    ]
    assert fallback_calls == ["gemini-3.1-flash-lite"]


def test_generation_history_records_fallback_model_after_rate_limit(client, monkeypatch):
    _configure_generation(monkeypatch, primary_behavior="rate_limit")

    cid, response = _generate_once(client)

    assert response.status_code == 200
    history = client.get(f"/api/history?contact_id={cid}")
    assert history.status_code == 200
    assert history.json()[0]["model"] == "gemini-3.1-flash-lite"


def test_generation_stays_on_fallback_model_for_repair_after_rate_limit(client, monkeypatch):
    calls, fallback_calls = _configure_generation(
        monkeypatch,
        primary_behavior="rate_limit",
        fallback_responses=["", "いいですね！\n楽しんできてください😊"],
    )

    cid, response = _generate_once(client)

    assert response.status_code == 200
    assert calls == [
        ("primary", "gemini-3.5-flash-lite"),
        ("fallback", "gemini-3.1-flash-lite"),
        ("fallback", "gemini-3.1-flash-lite"),
    ]
    assert fallback_calls == ["gemini-3.1-flash-lite", "gemini-3.1-flash-lite"]
    history = client.get(f"/api/history?contact_id={cid}")
    assert history.status_code == 200
    assert history.json()[0]["model"] == "gemini-3.1-flash-lite"


def test_generation_stays_on_generic_fallback_for_repair_after_empty_response(
    client, monkeypatch
):
    calls, fallback_calls = _configure_generation(
        monkeypatch,
        primary_behavior="empty_response",
        fallback_responses=["", "いいですね！\n楽しんできてください😊"],
    )

    cid, response = _generate_once(client)

    assert response.status_code == 200
    assert calls == [
        ("primary", "gemini-3.5-flash-lite"),
        ("primary", "gemini-3.5-flash-lite"),
        ("primary", "gemini-3.5-flash-lite"),
        ("fallback", "gemini-3.1-flash-lite"),
        ("fallback", "gemini-3.1-flash-lite"),
    ]
    assert fallback_calls == ["gemini-3.1-flash-lite", "gemini-3.1-flash-lite"]
    history = client.get(f"/api/history?contact_id={cid}")
    assert history.status_code == 200
    assert history.json()[0]["model"] == "gemini-3.1-flash-lite"


def test_generation_keeps_primary_model_when_primary_succeeds(client, monkeypatch):
    calls, fallback_calls = _configure_generation(
        monkeypatch, primary_behavior="success"
    )

    cid, response = _generate_once(client)

    assert response.status_code == 200
    assert calls == [("primary", "gemini-3.5-flash-lite")]
    assert fallback_calls == []
    history = client.get(f"/api/history?contact_id={cid}")
    assert history.status_code == 200
    assert history.json()[0]["model"] == "gemini-3.5-flash-lite"


def test_generation_does_not_fallback_on_non_rate_limit_provider_error(
    client, monkeypatch
):
    calls, fallback_calls = _configure_generation(
        monkeypatch, primary_behavior="provider_error"
    )

    _cid, response = _generate_once(client)

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "provider_error"
    assert calls == [("primary", "gemini-3.5-flash-lite")]
    assert fallback_calls == []
