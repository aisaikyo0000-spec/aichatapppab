from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_response import (
    benchmark_run_state,
    extract_api_error_code,
    generation_response_is_valid,
)


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


def test_final_case_error_cannot_mark_benchmark_complete():
    state = benchmark_run_state(
        [{"id": "case-1"}, {"id": "case-2", "error": "HTTP 502", "error_code": "rate_limit"}],
        expected_count=2,
    )

    assert state == {
        "total": 2,
        "expected_total": 2,
        "complete": False,
        "stopped_reason": "rate_limit_exhausted",
    }


def test_successful_full_run_is_complete_and_partial_run_is_explicit():
    complete = benchmark_run_state([{"id": "case-1"}, {"id": "case-2"}], expected_count=2)
    partial = benchmark_run_state([{"id": "case-1"}], expected_count=2)

    assert complete["complete"] is True
    assert complete["stopped_reason"] is None
    assert partial["complete"] is False
    assert partial["stopped_reason"] == "incomplete"


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"replies": ["A", "B", "C"]}, True),
        ({"replies": [], "question": "好みを教えてください。"}, True),
        ({"replies": ["A"], "question": "好みを教えてください。"}, False),
        ({"replies": ["A", "B", "C"], "question": "好みを教えてください。"}, False),
        ({"replies": [], "question": {"secret": "unreviewed"}}, False),
        ({"replies": [], "question": "  "}, False),
        ({"replies": ["A", "", "C"]}, False),
        ({"replies": "A", "question": "好みを教えてください。"}, False),
    ],
)
def test_generation_response_shape_is_exclusive_and_well_typed(payload, expected):
    assert generation_response_is_valid(payload) is expected
