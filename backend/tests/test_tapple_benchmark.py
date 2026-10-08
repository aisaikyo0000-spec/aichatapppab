import json
import pytest

from app.routers import generation
from scripts.run_tapple_strategy_benchmark import (
    SCENARIOS,
    _evaluate_result,
    _write_artifact,
    summarize_expectations,
)


@pytest.mark.parametrize(
    "statement",
    [
        "行きたくないわけではありません。",
        "行きたくないわけじゃありません。",
        "行きたくないとは言えません。",
        "行きたくないとは限りません。",
    ],
)
def test_qualified_non_refusal_is_not_an_unsupported_intent_marker(statement):
    assert generation._TAPPLE_UNSUPPORTED_INTENT_INFERENCE_RE.search(statement) is None


def _valid_results():
    replies = {
        "explicit_interest": "いいですね、カフェ楽しみです！",
        "mutual_activity_interest": "そのカフェよさそうですね。よかったら今度一緒に行きませんか？",
        "shared_activity_low_reciprocity": "パンケーキのお店、気になりますね。",
        "accepted_invitation": "ありがとう、楽しみです！日程はいつがいいですか？",
        "ambiguous_interest": "カフェ気になりますね、どんなお店ですか？",
        "tentative_interest": "タイミングが合ったらぜひ、また話しましょう。",
        "counterproposal": "日曜なら大丈夫です、ありがとう！",
        "decline": "わかりました、無理しないでください。",
        "meeting_hesitation": "無理せず、もう少しメッセージで話しましょう。",
        "meeting_safety_concern": "不安な気持ちは大切にしたいです。無理せず話しましょう。",
        "declining_engagement": "そうなんですね。また話したくなったら話しましょう。",
    }
    results = []
    for scenario in SCENARIOS:
        last_contact = next(
            message["content"]
            for message in reversed(scenario["messages"])
            if message["sender"] == "contact"
        )
        action = scenario["allowed_actions"][0]
        results.append(
            {
                "id": scenario["id"],
                "expectation_met": True,
                "messages": scenario["messages"],
                "replies": [replies[scenario["id"]]],
                "strategy": {
                    "action": action,
                    "rationale": "相手の発言に合わせた次の進め方です。",
                    "evidence": [last_contact],
                    "invite_example": (
                        "駅前のカフェでお茶しませんか？" if action == "invite" else None
                    ),
                },
            }
        )
    return results


def test_tapple_benchmark_passes_only_when_all_scenarios_have_evidence_and_replies():
    results = _valid_results()

    summary = summarize_expectations(results, complete=True)

    assert summary["quality_pass"] is True
    assert summary["expectations_met"] == len(SCENARIOS)
    assert summary["expectation_failures"] == []
    assert summary["exit_code"] == 0


def test_tapple_benchmark_rejects_complete_run_with_wrong_strategy():
    results = _valid_results()
    results[1]["strategy"]["action"] = "stop"
    results[1]["expectation_met"] = True  # A stale status flag must not override raw evidence.

    summary = summarize_expectations(results, complete=True)

    assert summary["quality_pass"] is False
    assert summary["expectations_met"] == len(SCENARIOS) - 1
    assert summary["expectation_failures"] == [results[1]["id"]]
    assert summary["exit_code"] == 3


def test_tapple_benchmark_keeps_incomplete_run_distinct_from_quality_failure():
    results = _valid_results()[:1]

    summary = summarize_expectations(results, complete=False)

    assert summary["quality_pass"] is False
    assert summary["expectations_met"] == 1
    assert summary["expectation_failures"] == [scenario["id"] for scenario in SCENARIOS[1:]]
    assert summary["exit_code"] == 2


def test_tapple_benchmark_marks_duplicate_and_missing_scenarios_incomplete():
    results = _valid_results()
    results[1] = dict(results[0])

    summary = summarize_expectations(results, complete=True)

    assert summary["complete"] is False
    assert summary["quality_pass"] is False
    assert summary["expectation_failures"]
    assert summary["stopped_reason"] == "scenario_coverage_mismatch"
    assert summary["exit_code"] == 2


def test_tapple_artifact_does_not_claim_completion_for_duplicate_scenario_ids(tmp_path):
    results = _valid_results()
    results[1] = dict(results[0])
    artifact_path = tmp_path / "tapple.json"

    _write_artifact(artifact_path, results, complete=True)

    summary = json.loads(artifact_path.read_text(encoding="utf-8"))["summary"]
    assert summary["total"] == len(SCENARIOS)
    assert summary["complete"] is False
    assert summary["quality_pass"] is False
    assert summary["exit_code"] == 2


def test_tapple_expectation_summary_rejects_error_even_when_all_actions_match():
    results = _valid_results()
    results[0]["error"] = "HTTP 502"
    results[0]["error_code"] = "rate_limit"

    summary = summarize_expectations(results, complete=True)

    assert summary["complete"] is False
    assert summary["quality_pass"] is False
    assert summary["stopped_reason"] == "rate_limit_exhausted"
    assert summary["exit_code"] == 2


def test_interim_artifact_with_all_cases_has_incomplete_reason(tmp_path):
    results = _valid_results()
    artifact_path = tmp_path / "interim.json"

    _write_artifact(artifact_path, results, complete=False)

    summary = json.loads(artifact_path.read_text(encoding="utf-8"))["summary"]
    assert summary["complete"] is False
    assert summary["stopped_reason"] == "incomplete"
    assert summary["quality_pass"] is False
    assert summary["exit_code"] == 2


def test_tapple_benchmark_rejects_a_reinvitation_after_decline_even_when_action_is_stop():
    results = _valid_results()
    declined = next(result for result in results if result["id"] == "decline")
    declined["replies"] = ["わかった！でも来週カフェに行こうよ！"]
    declined["expectation_met"] = True

    summary = summarize_expectations(results, complete=True)

    assert summary["quality_pass"] is False
    assert declined["id"] in summary["expectation_failures"]


@pytest.mark.parametrize("scenario_id", ["meeting_hesitation", "meeting_safety_concern"])
@pytest.mark.parametrize(
    "reinvite",
    [
        "ぜひ来週会いましょう。",
        "ぜひ会いましょう。",
        "会いましょう。",
        "お会いしましょう。",
        "ぜひお会いしませんか？",
    ],
)
def test_tapple_benchmark_rejects_direct_reinvite_and_generic_reply_for_concern(scenario_id, reinvite):
    results = _valid_results()
    scenario = next(result for result in results if result["id"] == scenario_id)
    benchmark_scenario = next(item for item in SCENARIOS if item["id"] == scenario_id)
    scenario["replies"] = [reinvite]
    scenario["expectation_met"] = True

    assert "reinvitation_not_allowed" in _evaluate_result(benchmark_scenario, scenario)

    summary = summarize_expectations(results, complete=True)

    assert summary["quality_pass"] is False
    assert scenario_id in summary["expectation_failures"]

    results = _valid_results()
    scenario = next(result for result in results if result["id"] == scenario_id)
    scenario["replies"] = ["その話は面白いですね。"]
    scenario["expectation_met"] = True

    summary = summarize_expectations(results, complete=True)

    assert summary["quality_pass"] is False
    assert scenario_id in summary["expectation_failures"]


def test_tapple_benchmark_recomputes_result_instead_of_trusting_boolean():
    results = _valid_results()
    results[0]["expectation_met"] = True
    results[0]["strategy"]["action"] = "wait"

    summary = summarize_expectations(results, complete=True)

    assert summary["quality_pass"] is False
    assert results[0]["id"] in summary["expectation_failures"]


def test_accepted_invitation_scenario_expects_scheduling_not_another_invitation():
    accepted = next(
        scenario for scenario in SCENARIOS if scenario["id"] == "accepted_invitation"
    )

    assert accepted["allowed_actions"] == ["continue"]
    assert accepted["no_reinvitation"] is True


def test_tapple_benchmark_rejects_missing_reply_or_unverifiable_evidence():
    results = _valid_results()
    results[0]["replies"] = []
    results[1]["strategy"]["evidence"] = ["記録にない発言"]

    summary = summarize_expectations(results, complete=True)

    assert summary["quality_pass"] is False
    assert results[0]["id"] in summary["expectation_failures"]
    assert results[1]["id"] in summary["expectation_failures"]


@pytest.mark.parametrize(
    "malformed_result",
    [None, "not-an-object", {"id": []}],
    ids=["null-result", "string-result", "unhashable-id"],
)
def test_tapple_benchmark_fails_closed_on_malformed_result_records(malformed_result):
    results = _valid_results()
    results[0] = malformed_result

    summary = summarize_expectations(results, complete=True)

    assert summary["complete"] is False
    assert summary["quality_pass"] is False
    assert summary["exit_code"] == 2


def test_tapple_benchmark_requires_evidence_from_the_latest_contact_message():
    results = _valid_results()
    counterproposal = next(result for result in results if result["id"] == "counterproposal")
    counterproposal["strategy"]["evidence"] = ["カフェ行きたいです"]

    summary = summarize_expectations(results, complete=True)

    assert summary["quality_pass"] is False
    assert "counterproposal" in summary["expectation_failures"]
