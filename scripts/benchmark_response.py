"""Helpers for handling API responses in live benchmark runners."""
from __future__ import annotations

from typing import Any


def generation_response_is_valid(payload: Any) -> bool:
    """Accept exactly three replies or one non-empty safety question."""
    if not isinstance(payload, dict):
        return False
    replies = payload.get("replies")
    if (
        not isinstance(replies, list)
        or any(not isinstance(reply, str) or not reply.strip() for reply in replies)
    ):
        return False

    if "question" in payload:
        question = payload["question"]
        return not replies and isinstance(question, str) and bool(question.strip())
    return len(replies) == 3


def benchmark_run_state(results: list[dict], expected_count: int) -> dict[str, Any]:
    """Describe completion from both expected count and per-case failures."""
    failures = [result for result in results if "error" in result]
    complete = expected_count > 0 and len(results) == expected_count and not failures
    if failures:
        stopped_reason = (
            "rate_limit_exhausted"
            if any(result.get("error_code") == "rate_limit" for result in failures)
            else "generation_error"
        )
    elif len(results) < expected_count:
        stopped_reason = "incomplete"
    else:
        stopped_reason = None
    return {
        "total": len(results),
        "expected_total": expected_count,
        "complete": complete,
        "stopped_reason": stopped_reason,
    }


def extract_api_error_code(response: Any) -> str | None:
    """Return a FastAPI error code when the response has the standard shape."""
    try:
        payload = response.json()
    except (AttributeError, TypeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if not isinstance(detail, dict):
        return None
    code = detail.get("code")
    return code if isinstance(code, str) and code else None
