from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from run_tapple_strategy_benchmark import SCENARIOS, expectation_met


def test_live_tapple_benchmark_covers_positive_ambiguous_and_declined_invites():
    assert {scenario["id"] for scenario in SCENARIOS} == {
        "explicit_interest",
        "accepted_invitation",
        "ambiguous_interest",
        "tentative_interest",
        "counterproposal",
        "decline",
        "meeting_hesitation",
        "meeting_safety_concern",
        "declining_engagement",
    }


def test_tapple_benchmark_expectations_distinguish_clear_ambiguous_and_declined():
    scenarios = {scenario["id"]: scenario for scenario in SCENARIOS}

    def result_for(scenario_id, action, reply):
        contact_text = next(
            turn["content"]
            for turn in reversed(scenarios[scenario_id]["messages"])
            if turn["sender"] == "contact"
        )
        return {
            "strategy": {
                "action": action,
                "rationale": "会話に基づく判断です。",
                "evidence": [contact_text],
                "invite_example": "駅前のカフェでお茶しませんか？" if action == "invite" else None,
            },
            "replies": [reply],
        }

    assert expectation_met(
        scenarios["explicit_interest"],
        result_for("explicit_interest", "invite", "いいですね、カフェ楽しみです！"),
    )
    assert not expectation_met(
        scenarios["explicit_interest"],
        result_for("explicit_interest", "wait", "いいですね、カフェ楽しみです！"),
    )
    assert expectation_met(
        scenarios["accepted_invitation"],
        result_for("accepted_invitation", "continue", "ありがとう、楽しみです！日程はいつがいいですか？"),
    )
    assert not expectation_met(
        scenarios["accepted_invitation"],
        result_for("accepted_invitation", "invite", "ありがとう、楽しみです！日程はいつがいいですか？"),
    )
    assert expectation_met(
        scenarios["ambiguous_interest"],
        result_for("ambiguous_interest", "continue", "カフェ気になりますね、どんなお店ですか？"),
    )
    assert not expectation_met(
        scenarios["ambiguous_interest"],
        result_for("ambiguous_interest", "invite", "カフェ気になりますね、どんなお店ですか？"),
    )
    assert expectation_met(
        scenarios["tentative_interest"],
        result_for("tentative_interest", "wait", "タイミングが合ったらぜひ、また話しましょう。"),
    )
    assert expectation_met(
        scenarios["counterproposal"],
        result_for("counterproposal", "continue", "日曜なら大丈夫です、ありがとう！"),
    )
    assert not expectation_met(
        scenarios["counterproposal"],
        result_for("counterproposal", "continue", "最近は映画を見ました。"),
    )
    assert expectation_met(
        scenarios["decline"],
        result_for("decline", "stop", "わかりました、無理しないでください。"),
    )
    assert not expectation_met(
        scenarios["decline"],
        result_for("decline", "invite", "わかりました、無理しないでください。"),
    )


def test_tapple_benchmark_scenarios_include_chat_context_and_allowed_actions():
    for scenario in SCENARIOS:
        assert [turn["sender"] for turn in scenario["messages"]][-1] == "contact"
        assert scenario["allowed_actions"]
        assert "stop" not in scenario["allowed_actions"] or scenario["id"] == "decline"


def test_live_benchmark_covers_meeting_hesitation_and_safety_boundaries():
    scenarios = {scenario["id"]: scenario for scenario in SCENARIOS}

    for scenario_id in ("meeting_hesitation", "meeting_safety_concern"):
        scenario = scenarios[scenario_id]
        assert "wait" in scenario["allowed_actions"]
        assert set(scenario["allowed_actions"]) <= {"wait", "continue"}
        assert scenario["no_reinvitation"] is True

        latest_contact = next(
            turn["content"]
            for turn in reversed(scenario["messages"])
            if turn["sender"] == "contact"
        )
        result = {
            "strategy": {
                "action": "wait",
                "rationale": "会うことへの懸念が残っています。",
                "evidence": [latest_contact],
                "invite_example": None,
            },
            "replies": ["無理せず、もう少しメッセージで話しましょう。"],
        }
        assert expectation_met(scenario, result), scenario_id

        result["replies"] = ["ぜひ来週会いましょう。"]
        assert not expectation_met(scenario, result), scenario_id
