import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import verify_pipeline_benchmark
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
                "llm_calls": 1,
                "models_used": ["gemini-3.5-flash-lite"],
                "successful_route": {
                    "account": "primary",
                    "model": "gemini-3.5-flash-lite",
                },
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
                        "personal_style_fit": None,
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


def test_pipeline_verifier_rejects_candidates_without_live_generation_provenance():
    artifact = _artifact()
    artifact["cases"][12].pop("successful_route")

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert "generation_provenance_invalid" in report["failures"]
    assert report["exit_code"] != 0


def test_pipeline_verifier_rejects_generated_candidates_without_model_history():
    artifact = _artifact()
    artifact["cases"][12]["models_used"] = []

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert "generation_provenance_invalid" in report["failures"]


def test_manual_review_bundle_includes_representative_and_flagged_outputs():
    artifact = _artifact()
    flagged_case = artifact["cases"][1]
    flagged_case["contact"] = "相手の元発言"
    flagged_case["candidates"][1] = "根拠のない候補"
    flagged_case["issues"][1]["unsupported_inference"] = 1
    flagged_case["issues"][1]["ai_like"] = 1
    flagged_case["four_axis"][1]["human_chat_fit"] = 0.85
    flagged_case["ai_like_patterns"] = [[], ["formulaic_empathy"], []]

    safe_case = artifact["cases"][2]
    safe_case["contact"] = "相手の好みの質問"
    safe_case["candidates"] = []
    safe_case["issues"] = []
    safe_case["four_axis"] = []
    safe_case["safe_user_question"] = "本人の好みを確認してください。"

    bundle = verify_pipeline_benchmark.build_pipeline_manual_review_bundle(
        artifact["cases"]
    )

    assert bundle["representative_cases"] == [
        CANONICAL_IDS[index] for index in (0, 9, 19, 29, 39, 49, 59, 69)
    ]
    assert bundle["flagged_candidates"] == [
        {
            "case_id": CANONICAL_IDS[1],
            "contact": "相手の元発言",
            "candidate": "根拠のない候補",
            "issues": flagged_case["issues"][1],
            "four_axis": flagged_case["four_axis"][1],
            "ai_like_patterns": ["formulaic_empathy"],
        }
    ]
    assert bundle["safe_user_questions"] == [
        {
            "case_id": CANONICAL_IDS[2],
            "contact": safe_case["contact"],
            "question": "本人の好みを確認してください。",
        }
    ]


def test_manual_review_bundle_handles_invalid_candidate_metrics():
    artifact = _artifact()
    issue = artifact["cases"][0]["issues"][0]
    issue["unsupported_inference"] = "invalid"

    bundle = verify_pipeline_benchmark.build_pipeline_manual_review_bundle(
        artifact["cases"]
    )

    assert bundle["flagged_candidates"] == []
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


def test_pipeline_verifier_rejects_missing_candidate_issue_records():
    artifact = _artifact()
    for case in artifact["cases"]:
        case["issues"] = []

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert "case_metrics_invalid" in report["failures"]


def test_pipeline_verifier_rejects_malformed_issue_or_axis_fields():
    artifact = _artifact()
    artifact["cases"][0]["issues"][0].pop("echo")
    artifact["cases"][1]["four_axis"][0]["human_chat_fit"] = "0.95"

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert "case_metrics_invalid" in report["failures"]


def test_pipeline_verifier_rejects_partial_candidate_sets_without_user_question():
    artifact = _artifact()
    artifact["cases"][0]["candidates"].pop()

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert "case_metrics_invalid" in report["failures"]


def test_pipeline_verifier_allows_explicit_safe_user_question_without_candidates():
    artifact = _artifact()
    case = artifact["cases"][0]
    case["candidates"] = []
    case["issues"] = []
    case["four_axis"] = []
    case["safe_user_question"] = "相手に好みを確認してください。"
    artifact["summary"]["issues"].update(
        {
            "candidates": 207,
            "ai_like_rate": 0.0,
            "too_many_questions_rate": 0.0,
            "echo_rate": 0.0,
        }
    )
    artifact["summary"]["four_axis_avg"].update(
        {
            "context_fit": 0.7,
            "human_chat_fit": 0.95,
            "conversation_fit": 0.97,
        }
    )

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is True


def test_pipeline_verifier_cli_does_not_accept_case_set_override(monkeypatch):
    from verify_pipeline_benchmark import main

    monkeypatch.setattr(
        sys,
        "argv",
        ["verify_pipeline_benchmark.py", "--artifact", "ignored.json", "--cases", "custom.json"],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2


def test_pipeline_verifier_cli_returns_json_for_invalid_utf8_artifact(tmp_path, monkeypatch, capsys):
    from verify_pipeline_benchmark import main

    artifact_path = tmp_path / "invalid.json"
    artifact_path.write_bytes(b"\xff")
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify_pipeline_benchmark.py", "--artifact", str(artifact_path)],
    )

    assert main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "quality_pass": False,
        "exit_code": 2,
        "failures": ["UnicodeDecodeError"],
    }


def test_pipeline_verifier_requires_complete_four_axis_record():
    artifact = _artifact()
    artifact["cases"][0]["four_axis"][0].pop("personal_style_fit")

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert "case_metrics_invalid" in report["failures"]


def test_pipeline_verifier_rejects_invalid_synthetic_personal_style_value():
    artifact = _artifact()
    artifact["cases"][0]["four_axis"][0]["personal_style_fit"] = "unknown"

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert "case_metrics_invalid" in report["failures"]


def test_pipeline_verifier_rejects_boolean_error_count_and_string_metrics():
    artifact = _artifact()
    artifact["summary"]["errors"] = False
    artifact["summary"]["issues"]["ai_like_rate"] = "0.0"

    report = verify_pipeline_artifact(artifact, CANONICAL_IDS)

    assert report["quality_pass"] is False
    assert "case_errors_present" in report["failures"]
    assert "reported_metrics_mismatch" in report["failures"]
