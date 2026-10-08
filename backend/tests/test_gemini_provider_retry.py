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
