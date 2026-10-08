from scripts.run_tapple_strategy_benchmark import summarize_expectations


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
