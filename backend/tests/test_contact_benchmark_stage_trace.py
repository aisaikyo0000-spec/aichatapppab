"""Regression tests for privacy-safe, stage-separated Contact Bench traces."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import run_contact_benchmark as contact_benchmark


def test_validation_attempts_and_returned_candidates_are_traced_separately():
    diagnostics = contact_benchmark._new_contact_style_diagnostics()
    initial = ["initial candidate A secret", "initial candidate B", "initial candidate C"]
    hard_repair = ["お疲れ様です！", "ゆっくり休んでくださいね。", "返信は急がなくて大丈夫ですよ！"]
    rejected_style_repair = ["style repair secret A", "style repair secret B", "style repair secret C"]
    rejected_style_followup = ["style followup secret A", "style followup secret B", "style followup secret C"]
    transient_candidates = []

    contact_benchmark._record_validation_attempt(
        diagnostics, "initial", initial, ["案1候補が不正な検証内容でした"]
    )
    transient_candidates.append(("initial", contact_benchmark._candidate_set_key(initial), False))
    contact_benchmark._record_validation_attempt(diagnostics, "hard_repair", hard_repair, [])
    transient_candidates.append((
        "hard_repair", contact_benchmark._candidate_set_key(hard_repair), True
    ))
    contact_benchmark._record_validation_attempt(
        diagnostics, "style_repair", rejected_style_repair,
        ["案1実際の安全性を保証する表現があります"],
    )
    transient_candidates.append((
        "style_repair", contact_benchmark._candidate_set_key(rejected_style_repair), False
    ))
    contact_benchmark._record_validation_attempt(
        diagnostics, "style_followup", rejected_style_followup,
        ["案2実際の安全性を保証する表現があります"],
    )
    transient_candidates.append((
        "style_followup", contact_benchmark._candidate_set_key(rejected_style_followup), False
    ))

    contact_benchmark._record_returned_candidates(
        diagnostics, list(reversed(hard_repair)), transient_candidates
    )
    result = contact_benchmark._finalize_contact_style_diagnostics(diagnostics)

    assert [attempt["stage"] for attempt in result["validation_attempts"]] == [
        "initial", "hard_repair", "style_repair", "style_followup"
    ]
    assert [attempt["outcome"] for attempt in result["validation_attempts"]] == [
        "rejected", "passed", "rejected", "rejected"
    ]
    assert result["validation_attempts"][1]["candidate_aggregates"] == (
        contact_benchmark._safe_stage_candidate_aggregates(hard_repair)
    )
    assert result["validation_attempts"][1]["candidate_aggregates"][
        "length_distribution_chars"
    ] == sorted(map(len, hard_repair))
    length_stats = result["validation_attempts"][1]["candidate_aggregates"]["length"]
    assert length_stats["p25_chars"] <= length_stats["median_chars"]
    assert length_stats["median_chars"] <= length_stats["p75_chars"]
    assert {"tone_ratios", "laugh_ratio"}.issubset(
        result["validation_attempts"][1]["candidate_aggregates"]
    )
    assert result["last_hard_validation_attempt"] == {
        "stage": "style_followup", "outcome": "rejected"
    }
    assert result["final_hard_validation"] == {
        "stage": "hard_repair", "outcome": "passed"
    }
    assert result["returned_candidates"] == {
        "candidate_aggregates": contact_benchmark._safe_stage_candidate_aggregates(
            list(reversed(hard_repair))
        ),
        "validation": {"stage": "hard_repair", "outcome": "passed"},
    }
    serialized = json.dumps(result, ensure_ascii=False)
    for secret in (*initial, *rejected_style_repair, *rejected_style_followup):
        assert secret not in serialized
    assert "安全性を保証する表現があります" not in serialized


def test_returned_candidates_without_matching_validation_are_not_misattributed():
    diagnostics = contact_benchmark._new_contact_style_diagnostics()
    validated = ["valid one", "valid two", "valid three"]
    returned = ["different one", "different two", "different three"]
    contact_benchmark._record_validation_attempt(diagnostics, "initial", validated, [])

    contact_benchmark._record_returned_candidates(
        diagnostics, returned,
        [("initial", contact_benchmark._candidate_set_key(validated), True)],
    )
    result = contact_benchmark._finalize_contact_style_diagnostics(diagnostics)

    assert result["returned_candidates"]["candidate_aggregates"] == (
        contact_benchmark._safe_stage_candidate_aggregates(returned)
    )
    assert result["returned_candidates"]["validation"] == {
        "stage": None, "outcome": "not_linked"
    }
    assert result["final_hard_validation"] == {
        "stage": None, "outcome": "not_linked"
    }
