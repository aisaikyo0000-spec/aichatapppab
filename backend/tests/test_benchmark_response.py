from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_response import extract_api_error_code


def test_extracts_rate_limit_code_from_fastapi_error_response():
    response = SimpleNamespace(
        status_code=502,
        json=lambda: {"detail": {"code": "rate_limit", "message": "limited"}},
    )

    assert extract_api_error_code(response) == "rate_limit"


def test_extracts_non_quota_provider_error_code():
    response = SimpleNamespace(
        status_code=502,
        json=lambda: {"detail": {"code": "provider_error"}},
    )

    assert extract_api_error_code(response) == "provider_error"


def test_malformed_or_success_response_has_no_error_code():
    malformed = SimpleNamespace(status_code=502, json=lambda: (_ for _ in ()).throw(ValueError()))
    success = SimpleNamespace(status_code=200, json=lambda: {"replies": ["こんにちは"]})

    assert extract_api_error_code(malformed) is None
    assert extract_api_error_code(success) is None
