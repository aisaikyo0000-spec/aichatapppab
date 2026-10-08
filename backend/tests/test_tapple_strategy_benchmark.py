from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from run_tapple_strategy_benchmark import SCENARIOS, expectation_met


def test_live_tapple_benchmark_covers_positive_ambiguous_and_declined_invites():
    assert {scenario["id"] for scenario in SCENARIOS} == {
        "explicit_interest",
        "mutual_activity_interest",
        "shared_activity_low_reciprocity",
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


@pytest.mark.parametrize(
    "scenario_id, action",
    [
        ("decline", "stop"),
        ("meeting_hesitation", "wait"),
        ("meeting_safety_concern", "wait"),
    ],
)
def test_tapple_benchmark_rejects_indirect_persuasion_after_boundaries(
    scenario_id, action
):
    from run_tapple_strategy_benchmark import expectation_met

    scenario = next(item for item in SCENARIOS if item["id"] == scenario_id)
    latest_contact = next(
        turn["content"]
        for turn in reversed(scenario["messages"])
        if turn["sender"] == "contact"
    )
    result = {
        "strategy": {
            "action": action,
            "rationale": "相手の意思を尊重します。",
            "evidence": [latest_contact],
            "invite_example": None,
        },
        "replies": ["わかりました。考え直してもらえるとうれしいです。"],
    }

    assert not expectation_met(scenario, result)


def test_declining_engagement_requires_a_brief_contextual_reply_without_pursuit():
    scenario = next(item for item in SCENARIOS if item["id"] == "declining_engagement")
    required_markers = scenario["reply_must_contain_any"]
    assert scenario["no_follow_up_questions"] is True
    assert scenario["no_follow_up_pressure"] is True
    assert scenario["max_reply_sentences"] == 2
    contact_text = scenario["messages"][-1]["content"]
    result = {
        "strategy": {
            "action": "wait",
            "rationale": "短い返答が続いているので、今は追わずに待ちます。",
            "evidence": [contact_text],
            "invite_example": None,
        },
        "replies": ["了解です。また話そう。"],
    }

    assert required_markers
    assert expectation_met(scenario, result)


    for natural_reply in (
        "そうですね。また話しましょう。",
        "また話したくなったら話しましょう。",
        "そっか。また話そう。",
        "そっか。またタイミングが合ったら話そう",
        "了解です。また気が向いたら連絡してね",
        "そっか。また話せるときに話そう",
        "わかった。またね",
        "わかった。無理せず過ごしてね",
        "そうなんですね。無理せずゆっくりしてください",
        "そうなんですね。今日はゆっくり休んでくださいね",
        "今日はゆっくり休んでくださいね",
        "そうなんだ。気が向いたらまた話そう",
        "うん、気にしないで",
        "うん、わかった。無理せず休んでね",
        "そうなんだね。また話そうね",
        "気にしないでね、また話せるときに話そう",
    ):
        result["replies"] = [natural_reply]
        assert expectation_met(scenario, result), natural_reply

    for reply in (
        "了解です。今日は何してました？",
        "了解です。何してるの。また話そう。",
        "了解です。どう思う。また話そう。",
        "了解です。住んでる場所どこ",
        "了解です。最近どう",
        "そうなんですね。何かあったなら教えてください、もっと話したいです。",
        "了解です。何かあったなら教えて。また話そう。",
        "そうですね。よかったら最近のこと聞かせて。",
        "了解。また話したくなったら話しましょう、よかったら住んでる場所教えて。",
        "了解です。最近どういう感じ",
        "そっか、また話そう。最近どんな感じ",
        "了解です。いつ暇",
        "そっか。返信くれると嬉しいな",
        "了解です。落ち着いたら一言ちょうだい",
        "了解。また連絡くれると嬉しい",
        "了解です。また話そうところで明日カレー食べるんだ",
        "今日は仕事が忙しかったです。また話そう。",
        "了解です。明日カレー食べよう。",
        "そうですね。株価は上がっています。",
        "了解です。そうなんですね。無理せず、また話したくなったら話しましょう。",
        "なんで返事が短くなったの？今度カフェに行きませんか？",
    ):
        result["replies"] = [reply]
        assert not expectation_met(scenario, result), reply


def test_shared_activity_low_reciprocity_waits_while_warm_reciprocal_case_invites():
    scenarios = {scenario["id"]: scenario for scenario in SCENARIOS}
    weak = scenarios["shared_activity_low_reciprocity"]
    latest_contact = weak["messages"][-1]["content"]
    result = {
        "strategy": {
            "action": "wait",
            "rationale": "共通の話題はありますが、相手の反応がまだ短いため会話を続けます。",
            "evidence": [latest_contact],
            "invite_example": None,
        },
        "replies": ["パンケーキのお店、気になりますね。"],
    }

    assert expectation_met(weak, result)
    assert weak["expected_action"] == "wait"
    warm = scenarios["mutual_activity_interest"]
    assert warm["expected_action"] == "invite"
    assert len(warm["messages"]) >= 5
    assert any("？" in message["content"] for message in warm["messages"])
