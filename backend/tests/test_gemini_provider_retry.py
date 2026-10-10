import logging
from unittest.mock import Mock

import pytest

from app.ai.base import AIError
from app.ai.gemini import GeminiProvider


@pytest.mark.parametrize(
    "model",
    ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
)
def test_quota_fallback_models_surface_rate_limit_without_provider_retries(
    monkeypatch, model
):
    provider = GeminiProvider("test-key")
    response = Mock(status_code=429, text="quota exceeded")
    calls = []
    sleeps = []
    monkeypatch.setattr(provider, "_send_request", lambda *_args: calls.append(model) or response)
    monkeypatch.setattr("app.ai.gemini.time.sleep", lambda delay: sleeps.append(delay))

    with pytest.raises(AIError) as exc_info:
        provider.generate(
            model=model,
            messages=[],
            temperature=0.2,
            max_tokens=128,
        )

    assert exc_info.value.code == "rate_limit"
    assert calls == [model]
    assert sleeps == []


def test_non_fallback_model_keeps_existing_transient_rate_limit_retries(monkeypatch):
    provider = GeminiProvider("test-key")
    response = Mock(status_code=429, text="quota exceeded")
    calls = []
    sleeps = []
    monkeypatch.setattr(
        provider,
        "_send_request",
        lambda *_args: calls.append("request") or response,
    )
    monkeypatch.setattr("app.ai.gemini.time.sleep", lambda delay: sleeps.append(delay))

    with pytest.raises(AIError) as exc_info:
        provider.generate(
            model="gemini-3.1-pro-preview",
            messages=[],
            temperature=0.2,
            max_tokens=128,
        )

    assert exc_info.value.code == "rate_limit"
    assert len(calls) == provider.MAX_RETRIES
    assert sleeps == [
        provider.RETRY_BASE_DELAY,
        provider.RETRY_BASE_DELAY * 2,
    ]


def test_gemini_invalid_auth_key_response_is_classified_as_invalid_api_key():
    provider = GeminiProvider("test-key")
    response = Mock(status_code=400, text='{"error":{"message":"Invalid Auth key"}}')

    with pytest.raises(AIError) as exc_info:
        provider._parse_response(response, response.text)

    assert exc_info.value.code == "invalid_api_key"


def test_gemini_response_body_is_not_written_to_logs(monkeypatch, caplog):
    provider = GeminiProvider("test-key")
    caplog.set_level(logging.INFO, logger="app.ai.gemini")
    response_body = '{"choices":[{"message":{"content":"PRIVATE_REPLY_SENTINEL"}}]}'
    response = Mock(
        status_code=200,
        text=response_body,
        json=lambda: {
            "choices": [{"message": {"content": "PRIVATE_REPLY_SENTINEL"}}]
        },
    )
    monkeypatch.setattr(provider, "_send_request", lambda *_args: response)

    provider.generate(
        model="gemini-3.5-flash-lite",
        messages=[],
        temperature=0.2,
        max_tokens=128,
    )

    assert "PRIVATE_REPLY_SENTINEL" not in caplog.text


def test_gemini_error_body_is_not_logged_or_returned(monkeypatch, caplog):
    provider = GeminiProvider("test-key")
    caplog.set_level(logging.INFO, logger="app.ai.gemini")
    response = Mock(
        status_code=500,
        text='{"error":{"message":"PRIVATE_ERROR_SENTINEL"}}',
    )
    monkeypatch.setattr(provider, "_send_request", lambda *_args: response)

    with pytest.raises(AIError) as exc_info:
        provider.generate(
            model="gemini-3.5-flash-lite",
            messages=[],
            temperature=0.2,
            max_tokens=128,
        )

    assert "PRIVATE_ERROR_SENTINEL" not in caplog.text
    assert "PRIVATE_ERROR_SENTINEL" not in str(exc_info.value)


def test_gemini_embedded_error_details_are_not_logged_or_returned(monkeypatch, caplog):
    provider = GeminiProvider("test-key")
    caplog.set_level(logging.INFO, logger="app.ai.gemini")
    response = Mock(
        status_code=200,
        text='{"error":{"message":"PRIVATE_ERROR_SENTINEL"}}',
        json=lambda: {"error": {"message": "PRIVATE_ERROR_SENTINEL"}},
    )
    monkeypatch.setattr(provider, "_send_request", lambda *_args: response)

    with pytest.raises(AIError) as exc_info:
        provider.generate(
            model="gemini-3.5-flash-lite",
            messages=[],
            temperature=0.2,
            max_tokens=128,
        )

    assert "PRIVATE_ERROR_SENTINEL" not in caplog.text
    assert "PRIVATE_ERROR_SENTINEL" not in str(exc_info.value)
