"""Validate that a live pipeline artifact proves the full Step 18 70-case gate."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from compare_before_after import summarize_issues  # noqa: E402

EXPECTED_CASE_COUNT = 70
THRESHOLDS = {
    "ai_like_rate_max": 0.098,
    "context_fit_min": 0.605,
    "human_chat_fit_min": 0.900,
    "conversation_fit_min": 0.963,
    "too_many_questions_rate_max": 0.059,
    "echo_rate_max": 0.132,
}
MANUAL_REVIEW_SAMPLE_INDICES = (0, 9, 19, 29, 39, 49, 59, 69)


def _case_metrics(cases: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    all_issues = [
        issue
        for case in cases
        for issue in case.get("issues", [])
    ]
    issue_metrics = summarize_issues(all_issues)
    axis_metrics: dict[str, float | None] = {}
    for axis in ("context_fit", "human_chat_fit", "conversation_fit"):
        values = [
            score[axis]
            for case in cases
            for score in case.get("four_axis", [])
            if isinstance(score, dict)
            and isinstance(score.get(axis), (int, float))
            and not isinstance(score.get(axis), bool)
        ]
        axis_metrics[axis] = round(sum(values) / len(values), 3) if values else None
    return issue_metrics, axis_metrics


def _candidate_metrics_are_valid(cases: list[dict[str, Any]]) -> bool:
    issue_flags = ("echo", "too_many_questions", "over_explanation")
    axis_names = ("context_fit", "human_chat_fit", "conversation_fit")
    for case in cases:
        if not isinstance(case, dict):
            return False
        candidates = case.get("candidates")
        issues = case.get("issues")
        axes = case.get("four_axis")
        if not isinstance(candidates, list) or not isinstance(issues, list) or not isinstance(axes, list):
            return False
        if any(not isinstance(candidate, str) or not candidate.strip() for candidate in candidates):
            return False
        safe_question = case.get("safe_user_question")
        valid_candidate_count = len(candidates) == 3 or (
            not candidates and isinstance(safe_question, str) and bool(safe_question.strip())
        )
        if not valid_candidate_count or len(issues) != len(candidates) or len(axes) != len(candidates):
            return False
        for issue in issues:
            if not isinstance(issue, dict):
                return False
            if any(not isinstance(issue.get(flag), bool) for flag in issue_flags):
                return False
            for count_name in ("unsupported_inference", "ai_like"):
                count = issue.get(count_name)
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    return False
        for axis in axes:
            if not isinstance(axis, dict):
                return False
            if "personal_style_fit" not in axis or axis["personal_style_fit"] is not None:
                return False
            for name in axis_names:
                value = axis.get(name)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or not 0.0 <= value <= 1.0
                ):
                    return False
    return True


def _generation_provenance_is_valid(cases: list[dict[str, Any]]) -> bool:
    """Require per-case evidence emitted by the live generation pipeline."""
    for case in cases:
        if not isinstance(case, dict):
            return False

        llm_calls = case.get("llm_calls")
        if isinstance(llm_calls, bool) or not isinstance(llm_calls, int) or llm_calls < 1:
            return False

        route = case.get("successful_route")
        if not isinstance(route, dict):
            return False
        account = route.get("account")
        model = route.get("model")
        if (
            not isinstance(account, str)
            or account not in {"primary", "secondary"}
            or not isinstance(model, str)
            or not model.strip()
        ):
            return False

        models_used = case.get("models_used")
        candidates = case.get("candidates")
        if (
            not isinstance(models_used, list)
            or any(not isinstance(value, str) or not value.strip() for value in models_used)
            or not isinstance(candidates, list)
        ):
            return False
        if candidates and model not in models_used:
            return False

    return True


def build_pipeline_manual_review_bundle(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose representative cases and every candidate with a review signal."""
    representative_cases = [
        str(cases[index].get("id", ""))
        for index in MANUAL_REVIEW_SAMPLE_INDICES
        if index < len(cases) and isinstance(cases[index], dict)
    ]
    flagged_candidates: list[dict[str, Any]] = []
    safe_user_questions: list[dict[str, str]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id", ""))
        contact = str(case.get("contact", ""))
        safe_question = case.get("safe_user_question")
        if isinstance(safe_question, str) and safe_question.strip():
            safe_user_questions.append(
                {"case_id": case_id, "contact": contact, "question": safe_question}
            )

        candidates = case.get("candidates")
        issues = case.get("issues")
        axes = case.get("four_axis")
        patterns = case.get("ai_like_patterns")
        if not all(isinstance(value, list) for value in (candidates, issues, axes)):
            continue
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, str):
                continue
            issue = issues[index] if index < len(issues) else {}
            axis = axes[index] if index < len(axes) else {}
            pattern = (
                patterns[index]
                if isinstance(patterns, list) and index < len(patterns)
                else []
            )
            issue = issue if isinstance(issue, dict) else {}
            axis = axis if isinstance(axis, dict) else {}
            pattern = pattern if isinstance(pattern, list) else []
            unsupported = issue.get("unsupported_inference", 0)
            ai_like = issue.get("ai_like", 0)
            has_count_signal = any(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value > 0
                for value in (unsupported, ai_like)
            )
            below_threshold = any(
                isinstance(axis.get(key), (int, float))
                and not isinstance(axis.get(key), bool)
                and axis[key] < THRESHOLDS[threshold]
                for key, threshold in (
                    ("context_fit", "context_fit_min"),
                    ("human_chat_fit", "human_chat_fit_min"),
                    ("conversation_fit", "conversation_fit_min"),
                )
            )
            has_issue_signal = (
                has_count_signal
                or any(
                    issue.get(key) is True
                    for key in ("echo", "too_many_questions", "over_explanation")
                )
                or bool(pattern)
                or below_threshold
            )
            if has_issue_signal:
                flagged_candidates.append(
                    {
                        "case_id": case_id,
                        "contact": contact,
                        "candidate": candidate,
                        "issues": issue,
                        "four_axis": axis,
                        "ai_like_patterns": pattern,
                    }
                )
    return {
        "representative_cases": representative_cases,
        "flagged_candidates": flagged_candidates,
        "safe_user_questions": safe_user_questions,
    }


def _same_metric(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if (
        isinstance(left, bool)
        or not isinstance(left, (int, float))
        or isinstance(right, bool)
        or not isinstance(right, (int, float))
    ):
        return False
    try:
        return math.isclose(left, right, rel_tol=0.0, abs_tol=0.000001)
    except (TypeError, ValueError):
        return False


def verify_pipeline_artifact(
    artifact: dict[str, Any], canonical_case_ids: list[str]
) -> dict[str, Any]:
    """Fail closed unless the artifact covers the canonical 70 and meets all gates."""
    failures: list[str] = []
    expected_ids = [str(case_id) for case_id in canonical_case_ids]
    cases = artifact.get("cases")
    summary = artifact.get("summary")
    cases = cases if isinstance(cases, list) else []
    summary = summary if isinstance(summary, dict) else {}

    expected_unique = len(set(expected_ids)) == len(expected_ids)
    if len(expected_ids) != EXPECTED_CASE_COUNT or not expected_unique:
        failures.append("canonical_dataset_invalid")

    actual_ids = [str(case.get("id", "")) for case in cases if isinstance(case, dict)]
    actual_unique = len(actual_ids) == len(cases) and len(set(actual_ids)) == len(actual_ids)
    missing_ids = sorted(set(expected_ids) - set(actual_ids))
    unexpected_ids = sorted(set(actual_ids) - set(expected_ids))
    canonical_coverage = (
        len(actual_ids) == EXPECTED_CASE_COUNT
        and actual_unique
        and set(actual_ids) == set(expected_ids)
    )
    if not canonical_coverage:
        failures.append("canonical_case_coverage_mismatch")

    if (
        summary.get("complete") is not True
        or summary.get("total") != EXPECTED_CASE_COUNT
        or summary.get("expected_total") != EXPECTED_CASE_COUNT
    ):
        failures.append("incomplete_run")
    error_count = summary.get("errors")
    if (
        isinstance(error_count, bool)
        or not isinstance(error_count, int)
        or error_count != 0
        or any("error" in case for case in cases if isinstance(case, dict))
    ):
        failures.append("case_errors_present")
    if not _candidate_metrics_are_valid(cases):
        failures.append("case_metrics_invalid")
    if not _generation_provenance_is_valid(cases):
        failures.append("generation_provenance_invalid")

    issue_metrics, axis_metrics = _case_metrics(cases) if "case_metrics_invalid" not in failures else ({}, {})
    reported_issues = summary.get("issues")
    reported_axes = summary.get("four_axis_avg")
    if not isinstance(reported_issues, dict) or not isinstance(reported_axes, dict):
        failures.append("summary_metrics_missing")
    else:
        metric_pairs = [
            (reported_issues.get(key), issue_metrics.get(key))
            for key in ("candidates", "ai_like_rate", "too_many_questions_rate", "echo_rate")
        ] + [
            (reported_axes.get(key), axis_metrics.get(key))
            for key in ("context_fit", "human_chat_fit", "conversation_fit")
        ]
        if any(not _same_metric(reported, recomputed) for reported, recomputed in metric_pairs):
            failures.append("reported_metrics_mismatch")

    observed = {
        "ai_like_rate": issue_metrics.get("ai_like_rate"),
        "context_fit": axis_metrics.get("context_fit"),
        "human_chat_fit": axis_metrics.get("human_chat_fit"),
        "conversation_fit": axis_metrics.get("conversation_fit"),
        "too_many_questions_rate": issue_metrics.get("too_many_questions_rate"),
        "echo_rate": issue_metrics.get("echo_rate"),
    }
    if not isinstance(reported_issues, dict) or not isinstance(reported_axes, dict):
        failures.extend(
            name
            for name, value in observed.items()
            if value is None and f"{name}_missing" not in failures
        )

    threshold_failures = {
        "ai_like_above_threshold": observed["ai_like_rate"] is None
        or observed["ai_like_rate"] > THRESHOLDS["ai_like_rate_max"],
        "context_below_threshold": observed["context_fit"] is None
        or observed["context_fit"] < THRESHOLDS["context_fit_min"],
        "human_below_threshold": observed["human_chat_fit"] is None
        or observed["human_chat_fit"] < THRESHOLDS["human_chat_fit_min"],
        "conversation_below_threshold": observed["conversation_fit"] is None
        or observed["conversation_fit"] < THRESHOLDS["conversation_fit_min"],
        "questions_above_threshold": observed["too_many_questions_rate"] is None
        or observed["too_many_questions_rate"] > THRESHOLDS["too_many_questions_rate_max"],
        "echo_above_threshold": observed["echo_rate"] is None
        or observed["echo_rate"] > THRESHOLDS["echo_rate_max"],
    }
    failures.extend(name for name, failed in threshold_failures.items() if failed)

    return {
        "quality_pass": not failures,
        "exit_code": 0 if not failures else 2,
        "failures": failures,
        "coverage": {
            "total": len(actual_ids),
            "expected_total": EXPECTED_CASE_COUNT,
            "unique": actual_unique,
            "missing_ids": missing_ids,
            "unexpected_ids": unexpected_ids,
        },
        "observed": observed,
        "thresholds": THRESHOLDS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the canonical 70-case live benchmark artifact")
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    try:
        artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
        canonical_cases = json.loads(
            (ROOT / "backend" / "tests" / "step10_benchmark_inputs.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"quality_pass": False, "exit_code": 2, "failures": [type(exc).__name__]}))
        return 2
    if not isinstance(artifact, dict) or not isinstance(canonical_cases, list):
        print(json.dumps({"quality_pass": False, "exit_code": 2, "failures": ["invalid_input_shape"]}))
        return 2

    report = verify_pipeline_artifact(
        artifact,
        [str(case["id"]) for case in canonical_cases if isinstance(case, dict) and "id" in case],
    )
    report["manual_review"] = build_pipeline_manual_review_bundle(
        artifact.get("cases", []) if isinstance(artifact.get("cases"), list) else []
    )
    print(json.dumps(report, ensure_ascii=False))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
