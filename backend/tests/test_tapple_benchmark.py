import json

from scripts.run_tapple_strategy_benchmark import (
    _write_artifact,
    summarize_expectations,
)


def test_tapple_benchmark_passes_only_when_all_three_expectations_are_met():
    results = [
        {"id": "explicit_interest", "expectation_met": True},
        {"id": "ambiguous_interest", "expectation_met": True},
        {"id": "decline", "expectation_met": True},
    ]

    summary = summarize_expectations(results, complete=True)

    assert summary["quality_pass"] is True
    assert summary["expectations_met"] == 3
    assert summary["expectation_failures"] == []
    assert summary["exit_code"] == 0


def test_tapple_benchmark_rejects_complete_run_with_wrong_strategy():
    results = [
        {"id": "explicit_interest", "expectation_met": True},
        {"id": "ambiguous_interest", "expectation_met": False},
        {"id": "decline", "expectation_met": True},
    ]

    summary = summarize_expectations(results, complete=True)

    assert summary["quality_pass"] is False
    assert summary["expectations_met"] == 2
    assert summary["expectation_failures"] == ["ambiguous_interest"]
    assert summary["exit_code"] == 3


def test_tapple_benchmark_keeps_incomplete_run_distinct_from_quality_failure():
    results = [
        {"id": "explicit_interest", "expectation_met": True},
    ]

    summary = summarize_expectations(results, complete=False)

    assert summary["quality_pass"] is False
    assert summary["expectations_met"] == 1
    assert summary["expectation_failures"] == ["ambiguous_interest", "decline"]
    assert summary["exit_code"] == 2


def test_tapple_benchmark_marks_duplicate_and_missing_scenarios_incomplete():
    results = [
        {"id": "explicit_interest", "expectation_met": True},
        {"id": "explicit_interest", "expectation_met": True},
        {"id": "decline", "expectation_met": True},
    ]

    summary = summarize_expectations(results, complete=True)

    assert summary["complete"] is False
    assert summary["quality_pass"] is False
    assert summary["expectation_failures"] == ["ambiguous_interest"]
    assert summary["stopped_reason"] == "scenario_coverage_mismatch"
    assert summary["exit_code"] == 2


def test_tapple_artifact_does_not_claim_completion_for_duplicate_scenario_ids(tmp_path):
    results = [
        {"id": "explicit_interest", "expectation_met": True},
        {"id": "explicit_interest", "expectation_met": True},
        {"id": "decline", "expectation_met": True},
    ]
    artifact_path = tmp_path / "tapple.json"

    _write_artifact(artifact_path, results, complete=True)

    summary = json.loads(artifact_path.read_text(encoding="utf-8"))["summary"]
    assert summary["total"] == 3
    assert summary["complete"] is False
    assert summary["quality_pass"] is False
    assert summary["exit_code"] == 2
