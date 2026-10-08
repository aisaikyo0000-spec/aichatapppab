"""API credential-file parsing tests; test fixtures never use real credentials."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from api_key_file import read_gemini_api_key
import check_tapple_api_connectivity as connectivity
from check_tapple_api_connectivity import _make_probe_provider


def test_reads_gemini_api_key_from_env_assignment(tmp_path):
    key_file = tmp_path / ".env"
    key_file.write_text('OTHER=value\nGEMINI_API_KEY="AQ.test-token"\n', encoding="utf-8")

    assert read_gemini_api_key(key_file) == "AQ.test-token"


def test_reads_a_single_raw_credential_line(tmp_path):
    key_file = tmp_path / "gemini2.md"
    key_file.write_text("\nAQ.test-token\n", encoding="utf-8")

    assert read_gemini_api_key(key_file) == "AQ.test-token"


def test_rejects_ambiguous_multiline_raw_file(tmp_path):
    key_file = tmp_path / "ambiguous.md"
    key_file.write_text("AQ.first\nAQ.second\n", encoding="utf-8")

    assert read_gemini_api_key(key_file) == ""


def test_missing_file_returns_empty_without_exposing_path_content(tmp_path):
    assert read_gemini_api_key(tmp_path / "missing") == ""


def test_probe_provider_never_retries_and_spends_extra_requests():
    provider = _make_probe_provider("AQ.test-token")

    assert provider.MAX_RETRIES == 1


class _FakeProvider:
    def __init__(self, api_key):
        self.api_key = api_key


def test_uses_secondary_key_only_after_both_primary_models_are_rate_limited(monkeypatch):
    primary = _FakeProvider("primary-key")
    secondary = _FakeProvider("secondary-key")
    calls = []

    def probe(provider, model):
        calls.append((provider.api_key, model))
        if provider is primary:
            raise connectivity.AIError("limited", code="rate_limit")
        return True

    monkeypatch.setattr(connectivity, "_probe", probe)
    result = connectivity._select_available_model(
        primary, secondary, "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"
    )

    assert result == ("gemini-3.5-flash-lite", "secondary", None)
    assert calls == [
        ("primary-key", "gemini-3.5-flash-lite"),
        ("primary-key", "gemini-3.1-flash-lite"),
        ("secondary-key", "gemini-3.5-flash-lite"),
    ]


def test_does_not_use_secondary_key_for_non_quota_errors(monkeypatch):
    primary = _FakeProvider("primary-key")
    secondary = _FakeProvider("secondary-key")
    calls = []

    def probe(provider, model):
        calls.append((provider.api_key, model))
        raise connectivity.AIError("service error", code="provider_error")

    monkeypatch.setattr(connectivity, "_probe", probe)
    result = connectivity._select_available_model(
        primary, secondary, "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"
    )

    assert result == (None, "primary", "provider_error")
    assert calls == [("primary-key", "gemini-3.5-flash-lite")]


def test_uses_secondary_fallback_model_when_secondary_primary_is_rate_limited(monkeypatch):
    primary = _FakeProvider("primary-key")
    secondary = _FakeProvider("secondary-key")
    calls = []

    def probe(provider, model):
        calls.append((provider.api_key, model))
        if provider is primary or model == "gemini-3.5-flash-lite":
            raise connectivity.AIError("limited", code="rate_limit")
        return True

    monkeypatch.setattr(connectivity, "_probe", probe)
    result = connectivity._select_available_model(
        primary, secondary, "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"
    )

    assert result == ("gemini-3.1-flash-lite", "secondary", None)
    assert calls == [
        ("primary-key", "gemini-3.5-flash-lite"),
        ("primary-key", "gemini-3.1-flash-lite"),
        ("secondary-key", "gemini-3.5-flash-lite"),
        ("secondary-key", "gemini-3.1-flash-lite"),
    ]


def test_does_not_probe_secondary_when_keys_are_identical(monkeypatch):
    primary = _FakeProvider("same-key")
    secondary = _FakeProvider("same-key")
    calls = []

    def probe(provider, model):
        calls.append((provider.api_key, model))
        raise connectivity.AIError("limited", code="rate_limit")

    monkeypatch.setattr(connectivity, "_probe", probe)
    result = connectivity._select_available_model(
        primary, secondary, "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"
    )

    assert result == (None, "primary", "rate_limit")
    assert calls == [
        ("same-key", "gemini-3.5-flash-lite"),
        ("same-key", "gemini-3.1-flash-lite"),
    ]
