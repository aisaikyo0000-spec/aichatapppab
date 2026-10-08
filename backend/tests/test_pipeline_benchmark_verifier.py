import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from verify_pipeline_benchmark import verify_pipeline_artifact


CASES_PATH = Path(__file__).parent / "step10_benchmark_inputs.json"
CANONICAL_CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))
CANONICAL_IDS = [str(case["id"]) for case in CANONICAL_CASES]


def _artifact(*, human=0.95, complete=True):
    cases = []
    for case_id in CANONICAL_IDS:
        cases.append(
            {
                "id": case_id,
                "candidates": ["候補"] * 3,
                "issues": [
                    {
                        "unsupported_inference": 0,
                        "echo": False,
                        "too_many_questions": False,
                        "over_explanation": False,
                        "ai_like": 0,
                    }
                    for _ in range(3)
                ],
                "four_axis": [
                    {
                        "context_fit": 0.7,
                        "human_chat_fit": human,
                        "conversation_fit": 0.97,
                    }
                    for _ in range(3)
                ],
            }
        )
    return {
        "summary": {
            "total": len(cases),
            "expected_total": len(cases),
            "complete": complete,
            "errors": 0,
            "issues": {
                "candidates": len(cases) * 3,
                "ai_like_rate": 0.0,
                "too_many_questions_rate": 0.0,
                "echo_rate": 0.0,
            },
            "four_axis_avg": {
                "context_fit": 0.7,
                "human_chat_fit": human,
                "conversation_fit": 0.97,
            },
        },
        "cases": cases,
    }


def test_pipeline_verifier_accepts_only_full_canonical_70_with_all_thresholds():
    report = verify_pipeline_artifact(_artifact(), CANONICAL_IDS)

    assert report["quality_pass"] is True
    assert report["coverage"]["total"] == 70
    assert report["coverage"]["unique"] is True
    assert report["exit_code"] == 0


def test_pipeline_verifier_rejects_subset_even_if_subset_claims_complete():
    artifact = _artifact()
    artifact["cases"] = artifact["cases"][:1]
    artifact["summary"].update({"total": 1, "expected_total": 1, "complete": True})

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert "canonical_case_coverage_mismatch" in report["failures"]
    assert report["exit_code"] != 0


def test_pipeline_verifier_rejects_duplicate_case_id():
    artifact = _artifact()
    artifact["cases"][1]["id"] = artifact["cases"][0]["id"]

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert report["coverage"]["unique"] is False
    assert "canonical_case_coverage_mismatch" in report["failures"]


def test_pipeline_verifier_rejects_metric_threshold_failure():
    report = verify_pipeline_artifact(_artifact(human=0.899), CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert "human_below_threshold" in report["failures"]
    assert report["exit_code"] != 0


def test_pipeline_verifier_rejects_error_cases_and_incomplete_summary():
    artifact = _artifact(complete=False)
    artifact["cases"][10]["error"] = "HTTP 502"

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert "incomplete_run" in report["failures"]
    assert "case_errors_present" in report["failures"]


def test_pipeline_verifier_rejects_metrics_that_do_not_match_case_data():
    artifact = _artifact()
    artifact["summary"]["issues"]["ai_like_rate"] = 0.02

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert "reported_metrics_mismatch" in report["failures"]
