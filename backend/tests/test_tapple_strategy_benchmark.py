from pathlib import Path
import sys

import pytest

from app.routers import generation


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from run_tapple_strategy_benchmark import (
    SCENARIOS,
    TAPPLE_BENCHMARK_CANDIDATES,
    _evaluate_result,
    expectation_met,
)


def _three_replies(reply):
    """Keep the target reply plus two short contextual phrasings for validator tests."""
    core = reply.rstrip("。.!！?？ ")
    return [
        reply,
        f"そうなんですね、{core}",
        f"うん、{core}",
    ]


def _declining_engagement_replies(reply):
    """Pair the tested phrase with distinct, pressure-free contextual off-ramps."""
    options = (
        "そうなんですね。また話せるときに話しましょう。",
        "了解です。無理せず過ごしてくださいね",
        "気にしないでね。また話したくなったら話そう",
        "わかった。またね",
        "うん、また話せるときに話そう",
    )
    selected = [reply]
    for option in options:
        if all(generation._jaccard_similarity(option, prior) < 0.85 for prior in selected):
            selected.append(option)
        if len(selected) == TAPPLE_BENCHMARK_CANDIDATES:
            return selected
    raise AssertionError("Could not assemble three distinct declining-engagement replies")


def test_tapple_benchmark_requests_three_replies_and_requires_all_three():
    assert TAPPLE_BENCHMARK_CANDIDATES == 3
    scenario = next(item for item in SCENARIOS if item["id"] == "meeting_hesitation")
    latest_contact = scenario["messages"][-1]["content"]
    result = {
        "strategy": {
            "action": "wait",
            "rationale": "相手の迷いを尊重します。",
            "evidence": [latest_contact],
            "invite_example": None,
        },
        "replies": [
            "無理せずメッセージで話しましょう。",
            "焦らず、もう少しここで話せたらうれしいです。",
        ],
    }

    assert not expectation_met(scenario, result)
    result["replies"] = [
        "無理せずメッセージで話しましょう。",
        "焦らず、もう少しここで話せたらうれしいです。",
        "会うことは急がず、安心できるペースで話しましょう。",
    ]
    assert expectation_met(scenario, result)


def test_tapple_benchmark_fails_case_when_any_reply_violates_context_safety():
    scenario = next(item for item in SCENARIOS if item["id"] == "meeting_hesitation")
    latest_contact = scenario["messages"][-1]["content"]
    result = {
        "strategy": {
            "action": "wait",
            "rationale": "相手の迷いを尊重します。",
            "evidence": [latest_contact],
            "invite_example": None,
        },
        "replies": [
            "無理せずメッセージで話しましょう。",
            "急がず、自分のペースで大丈夫です。",
            "ぜひ来週会いましょう。",
        ],
    }

    assert not expectation_met(scenario, result)


def test_tapple_benchmark_rejects_duplicate_reply_triplet():
    scenario = next(item for item in SCENARIOS if item["id"] == "decline")
    latest_contact = scenario["messages"][-1]["content"]
    reply = "わかりました、教えてくれてありがとう！"
    result = {
        "strategy": {
            "action": "stop",
            "rationale": "相手の意思を尊重します。",
            "evidence": [latest_contact],
            "invite_example": None,
        },
        "replies": [reply, reply, reply],
    }

    assert "reply_validation_failed" in _evaluate_result(scenario, result)


def test_live_tapple_benchmark_covers_positive_ambiguous_and_declined_invites():
    assert {scenario["id"] for scenario in SCENARIOS} == {
        "explicit_interest",
        "mutual_activity_interest",
        "unlisted_shared_hobby",
        "shared_activity_low_reciprocity",
        "accepted_invitation",
        "ambiguous_interest",
        "tentative_interest",
        "counterproposal",
        "decline",
        "meeting_hesitation",
        "meeting_safety_concern",
        "declining_engagement",
        "recent_activity_disinterest",
        "different_activity_does_not_clear_disinterest",
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
            "replies": _three_replies(reply),
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
        {
            **result_for("counterproposal", "continue", "日曜なら大丈夫です、ありがとう！何時ごろがよさそうですか？"),
            "replies": [
                "日曜なら大丈夫です、ありがとう！何時ごろがよさそうですか？",
                "日曜がいいですね！何時ごろなら空いてますか？",
                "日曜日ならうれしいです！時間は何時頃に決めますか？",
            ],
        },
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


def test_explicit_interest_fixture_is_not_already_an_accepted_invitation():
    scenario = next(item for item in SCENARIOS if item["id"] == "explicit_interest")
    last_contact_index = max(
        index
        for index, turn in enumerate(scenario["messages"])
        if turn["sender"] == "contact"
    )
    latest_contact = next(
        turn["content"]
        for turn in reversed(scenario["messages"])
        if turn["sender"] == "contact"
    )

    assert scenario["expected_action"] == "invite"
    assert not generation._TAPPLE_ACCEPTED_INVITATION_RE.search(latest_contact)
    assert generation._TAPPLE_ACTIVITY_INTEREST_RE.search(latest_contact)
    assert not generation._has_prior_self_tapple_invitation(
        scenario["messages"], last_contact_index
    )
    assert any(
        turn["sender"] == "self"
        and generation._TAPPLE_ACTIVITY_INTEREST_RE.search(turn["content"])
        for turn in scenario["messages"]
    )


@pytest.mark.parametrize(
    "scenario_id, reply",
    [
        ("explicit_interest", "カフェ"),
        ("ambiguous_interest", "カフェ"),
        ("counterproposal", "日曜"),
        ("unlisted_shared_hobby", "ボルダリング"),
    ],
)
def test_tapple_benchmark_rejects_fragmentary_replies_despite_matching_markers(
    scenario_id, reply
):
    scenario = next(item for item in SCENARIOS if item["id"] == scenario_id)
    latest_contact = next(
        turn["content"]
        for turn in reversed(scenario["messages"])
        if turn["sender"] == "contact"
    )
    action = scenario.get("expected_action", scenario["allowed_actions"][0])
    result = {
        "strategy": {
            "action": action,
            "rationale": "会話の反応に沿って判断します。",
            "evidence": [latest_contact],
            "invite_example": "駅前のカフェでお茶しませんか？" if action == "invite" else None,
        },
        "replies": _three_replies(reply),
    }

    assert not expectation_met(scenario, result)


def test_tapple_benchmark_rejects_absolute_safety_reassurance():
    scenario = next(item for item in SCENARIOS if item["id"] == "meeting_safety_concern")
    latest_contact = scenario["messages"][-1]["content"]
    result = {
        "strategy": {
            "action": "wait",
            "rationale": "会うことへの懸念が残っています。",
            "evidence": [latest_contact],
            "invite_example": None,
        },
        "replies": _three_replies("絶対安全なので安心してください。"),
    }

    assert not expectation_met(scenario, result)


@pytest.mark.parametrize(
    "reply",
    [
        "いいですね、カフェ楽しみです！自宅で会いましょう。",
        "バカですね、カフェ楽しみです！",
    ],
)
def test_tapple_benchmark_applies_production_reply_validation_to_invite_scenarios(
    reply,
):
    scenario = next(item for item in SCENARIOS if item["id"] == "explicit_interest")
    latest_contact = scenario["messages"][-1]["content"]
    result = {
        "strategy": {
            "action": "invite",
            "rationale": "相手の希望に沿って返します。",
            "evidence": [latest_contact],
            "invite_example": "駅前のカフェでお茶しませんか？",
        },
        "replies": _three_replies(reply),
    }

    assert not expectation_met(scenario, result)


@pytest.mark.parametrize(
    "scenario_id, action, rationale",
    [
        (
            "ambiguous_interest",
            "continue",
            "相手は明確に会いたいと強く希望しています。",
        ),
        (
            "tentative_interest",
            "wait",
            "断られたので今後の誘いは完全にやめるべきです。",
        ),
    ],
)
def test_tapple_benchmark_rejects_rationales_that_overstate_contact_intent(
    scenario_id, action, rationale
):
    scenario = next(item for item in SCENARIOS if item["id"] == scenario_id)
    latest_contact = scenario["messages"][-1]["content"]
    result = {
        "strategy": {
            "action": action,
            "rationale": rationale,
            "evidence": [latest_contact],
            "invite_example": None,
        },
        "replies": _three_replies("カフェ気になりますね、どんなお店ですか？"),
    }

    assert not expectation_met(scenario, result)


def test_tapple_benchmark_does_not_use_superseded_interest_to_support_rationale():
    scenario = {
        "id": "accepted_then_declined",
        "messages": [
            {"sender": "contact", "content": "今度一緒にカフェへ行きたいです。"},
            {"sender": "self", "content": "駅前のカフェへ行きませんか？"},
            {"sender": "contact", "content": "やっぱり会うのは難しいです。"},
        ],
        "expected_action": "stop",
        "allowed_actions": ["stop"],
        "no_reinvitation": True,
        "reply_must_contain_any": ["わかりました", "無理に誘いません", "大丈夫"],
    }
    result = {
        "strategy": {
            "action": "stop",
            "rationale": "相手は明確に会いたいと強く希望しています。",
            "evidence": [scenario["messages"][-1]["content"]],
            "invite_example": None,
        },
        "replies": _three_replies("わかりました。無理に誘いません。"),
    }

    assert not expectation_met(scenario, result)


@pytest.mark.parametrize(
    "scenario_id, rationale",
    [
        (
            "ambiguous_interest",
            "相手は明確に会いたいとは言っていません。会話を続けます。",
        ),
        (
            "tentative_interest",
            "断られたわけではないので、急がず様子を見ます。",
        ),
    ],
)
def test_tapple_benchmark_allows_rationales_that_explicitly_deny_overclaim(
    scenario_id, rationale
):
    scenario = next(item for item in SCENARIOS if item["id"] == scenario_id)
    latest_contact = scenario["messages"][-1]["content"]
    result = {
        "strategy": {
            "action": scenario["allowed_actions"][0],
            "rationale": rationale,
            "evidence": [latest_contact],
            "invite_example": None,
        },
        "replies": _three_replies("カフェ気になりますね、どんなお店ですか？"),
    }

    assert expectation_met(scenario, result)


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
            "replies": _three_replies("無理せず、もう少しメッセージで話しましょう。"),
        }
        assert expectation_met(scenario, result), scenario_id

        result["replies"] = _three_replies("ぜひ来週会いましょう。")
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
        "replies": _three_replies("わかりました。考え直してもらえるとうれしいです。"),
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
        "replies": _three_replies("了解です。また話そう。"),
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
        "そうなんですね。ゆっくり休んでくださいね",
        "ゆっくり休んでくださいね",
        "そうなんだ。気が向いたらまた話そう",
        "うん、気にしないで",
        "うん、わかった。無理せず休んでね",
        "そうなんだね。また話そうね",
        "気にしないでね、また話せるときに話そう",
    ):
        result["replies"] = _declining_engagement_replies(natural_reply)
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
        result["replies"] = _three_replies(reply)
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
        "replies": _three_replies("パンケーキのお店、気になりますね。"),
    }

    assert expectation_met(weak, result)
    assert weak["expected_action"] == "wait"
    warm = scenarios["mutual_activity_interest"]
    assert warm["expected_action"] == "invite"
    assert len(warm["messages"]) >= 5
    assert any("？" in message["content"] for message in warm["messages"])
