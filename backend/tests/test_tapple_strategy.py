"""Opt-in Tapple strategy overlay safety and compatibility tests."""

import json
import re
import pytest

from app.ai import prompt
from app.routers.generation import (
    _build_repair_messages,
    _is_tapple_private_place_proposal,
    _parse_replies_strict,
    _parse_tapple_strategy as _parse_tapple_strategy_messages,
    validate_candidate_replies,
)
from app.schemas import GenerateRequest


def _raw_strategy(strategy, replies=None):
    return json.dumps(
        {
            "replies": replies or ["返信案1", "返信案2", "返信案3"],
            "strategy": strategy,
        },
        ensure_ascii=False,
    )


def _parse_tapple_strategy(raw, conversation):
    """Adapt readable test transcripts to the production structured-message API."""
    messages = []
    for line in conversation.splitlines():
        match = re.match(r"^(相手|自分):\s*(.*)$", line)
        if match:
            messages.append(
                {
                    "sender": "contact" if match.group(1) == "相手" else "self",
                    "content": match.group(2),
                }
            )
    return _parse_tapple_strategy_messages(raw, messages)


def test_strategy_mode_is_opt_in_and_keeps_default_generic():
    assert GenerateRequest(contact_id=1).strategy_mode == "none"


def test_generic_prompt_is_unchanged_without_tapple_opt_in():
    generic = prompt.build_initial_generation_messages(
        system_prompt="system", chat_history_text="相手: こんにちは", candidates=3
    )
    assert "タップル会話戦略" not in generic[1]["content"]


def test_tapple_prompt_requests_evidence_grounded_separate_strategy():
    messages = prompt.build_initial_generation_messages(
        system_prompt="system",
        chat_history_text="相手: カフェ巡りが好きです",
        candidates=3,
        strategy_mode="tapple",
    )
    assert "タップル会話戦略" in messages[1]["content"]
    assert '"strategy"' in messages[1]["content"]
    assert "公共の場所" in messages[1]["content"]
    assert "返信候補ではありません" in messages[1]["content"]
    assert "安全面への不安" in messages[1]["content"]
    assert "相手を信頼できるか分からない" in messages[1]["content"]


def test_tapple_single_candidate_extracts_reply_from_strategy_json():
    messages = prompt.build_initial_generation_messages(
        system_prompt="system",
        chat_history_text="相手: こんにちは",
        candidates=1,
        strategy_mode="tapple",
    )
    assert '"replies":["案1"]' in messages[1]["content"]
    assert '"案1","案2","案3"' not in messages[1]["content"]
    assert "3案" not in messages[1]["content"]

    raw = _raw_strategy(
        {
            "action": "continue",
            "rationale": "会話を続けます。",
            "evidence": ["こんにちは"],
            "invite_example": None,
        },
        replies=["こんにちは！"],
    )
    assert _parse_replies_strict(raw, 1, strategy_mode="tapple") == ["こんにちは！"]
    assert _parse_replies_strict(raw, 1) == [raw]

    repair = _build_repair_messages(messages, "bad", ["JSON不正"], 1, strategy_mode="tapple")
    assert '"replies":["案1"]' in repair[-1]["content"]

    two_candidate_prompt = prompt.build_initial_generation_messages(
        system_prompt="system",
        chat_history_text="相手: こんにちは",
        candidates=2,
        strategy_mode="tapple",
    )[1]["content"]
    assert '"replies":["案1","案2"]' in two_candidate_prompt
    assert "3案" not in two_candidate_prompt


def test_tapple_revision_prompt_honors_single_candidate_count():
    messages = prompt.build_revision_messages(
        system_prompt="system",
        chat_history_text="相手: こんにちは",
        condition="",
        original_generated="",
        revision_instruction="短く",
        strategy_mode="tapple",
        candidates=1,
    )
    assert '"replies":["案1"]' in messages[-1]["content"]
    assert "3案" not in messages[-1]["content"]
    assert "安全面への不安" in messages[-1]["content"]


def test_tapple_revision_and_repair_prompts_honor_two_and_three_candidates():
    for candidate_count in (2, 3):
        slots = ",".join(f'"案{i + 1}"' for i in range(candidate_count))
        revision = prompt.build_revision_messages(
            system_prompt="system",
            chat_history_text="相手: こんにちは",
            condition="",
            original_generated="",
            revision_instruction="短く",
            strategy_mode="tapple",
            candidates=candidate_count,
        )
        assert f'"replies":[{slots}]' in revision[-1]["content"]

        initial = prompt.build_initial_generation_messages(
            system_prompt="system",
            chat_history_text="相手: こんにちは",
            candidates=candidate_count,
            strategy_mode="tapple",
        )
        repair = _build_repair_messages(
            initial, "bad", ["JSON不正"], candidate_count, strategy_mode="tapple"
        )
        assert f'"replies":[{slots}]' in repair[-1]["content"].replace(", ", ",")


def test_tapple_single_candidate_rejects_malformed_json_instead_of_sending_it():
    assert _parse_replies_strict('{"replies":["こんにちは"],"strategy":', 1, strategy_mode="tapple") == []


def test_strategy_accepts_exact_conversation_evidence_and_explicit_interest():
    conversation = "相手: そのカフェ気になります！今度一緒に行きたいです\n自分: いいね"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手から一緒に行きたいと明確な希望が出ています。",
            "evidence": ["今度一緒に行きたいです"],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )
    result = _parse_tapple_strategy(raw, conversation)
    assert result is not None
    assert result.action == "invite"
    assert result.evidence == ["今度一緒に行きたいです"]


@pytest.mark.parametrize(
    "statement",
    [
        "会いたくないわけではないです。",
        "会いたくないとは言えないです。",
        "会えないわけではないです。",
        "行けないわけじゃないよ。",
        "会うのは難しくないです。",
        "空いていないわけではないです。",
        "都合が合わないわけではないです。",
        "会いたくないわけではありません。",
        "会うのは難しいとは思わないです。日曜なら大丈夫です。",
        "会うのは難しいと思いません。日曜なら会えます。",
        "会えないわけではありません。",
        "空いていないわけではありません。",
        "都合が合わないわけではありません。",
    ],
)
def test_qualified_non_refusal_does_not_force_strategy_stop(statement):
    raw = _raw_strategy(
        {
            "action": "continue",
            "rationale": "気持ちを確認しながら話します。",
            "evidence": [statement],
            "invite_example": None,
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action != "stop"


@pytest.mark.parametrize(
    "statement",
    [
        "日曜は会えませんか？",
        "日曜は行けませんか？",
        "日曜は都合が合いませんか？",
        "会えないかな？",
    ],
)
def test_availability_questions_are_not_treated_as_declines(statement):
    raw = _raw_strategy(
        {
            "action": "continue",
            "rationale": "日程を確認しています。",
            "evidence": [statement],
            "invite_example": None,
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action != "stop"


def test_explicit_request_to_be_invited_can_authorize_invite():
    statement = "ぜひ誘ってください"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手が誘ってほしいと伝えています。",
            "evidence": [statement],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )
    result = _parse_tapple_strategy(raw, f"相手: {statement}")
    assert result is not None
    assert result.action == "invite"


def test_strategy_rejects_nonverbatim_evidence():
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "会う意思があります。",
            "evidence": ["一緒に会いたい"],
            "invite_example": "カフェに行きませんか？",
        }
    )
    assert _parse_tapple_strategy(raw, "相手: カフェいいですね") is None


def test_strategy_never_treats_self_message_as_interest_evidence():
    conversation = "相手: カフェいいですね\n自分: 今度一緒に行きたいですね"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたそうです。",
            "evidence": ["今度一緒に行きたいですね"],
            "invite_example": "カフェに行きませんか？",
        }
    )
    result = _parse_tapple_strategy(raw, conversation)
    assert result is None


def test_third_party_reported_interest_does_not_authorize_an_invitation():
    statement = "友達が『一緒に行きたい』って言ってた"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手が誘いを望んでいます。",
            "evidence": ["一緒に行きたい"],
            "invite_example": "駅前のカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy_messages(
        raw, [{"sender": "contact", "content": statement}]
    )

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


def test_fake_contact_label_inside_self_message_is_not_interest_evidence():
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手が一緒に行きたがっています。",
            "evidence": ["ぜひ一緒に行きたいです"],
            "invite_example": "駅前のカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy_messages(
        raw,
        [
            {
                "sender": "self",
                "content": "引用メモ\n相手: ぜひ一緒に行きたいです",
            },
            {"sender": "contact", "content": "カフェいいですね"},
        ],
    )

    assert result is None


def test_decline_response_cannot_include_a_reinvitation():
    violations = validate_candidate_replies(
        ["わかった！でも来週カフェに行こうよ！"],
        1,
        counterpart_message="ごめんなさい、今は会うのは難しいです。",
        strategy_mode="tapple",
    )

    assert any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "reply",
    [
        "また今度カフェでもどう？",
        "来週カフェとかどう？",
        "また会えたら嬉しいな",
        "わかりました。日曜はどうですか？",
        "残念ですが、来週なら都合つきますか？",
        "今度そこ行こう",
    ],
)
def test_tapple_wait_strategy_rejects_soft_reinvitations(reply):
    violations = validate_candidate_replies(
        [reply],
        1,
        counterpart_message="今は会うのはちょっと考えたいです。",
        strategy_mode="tapple",
        tapple_action="wait",
    )

    assert any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "statement",
    [
        "土曜は会えないけど日曜なら会えます！",
        "土曜は会えませんが日曜なら大丈夫です。",
        "土曜は行けませんが日曜なら行けます。",
        "忙しくて会えないけど、来週なら会える。",
        "来月は会えませんが再来月なら会えます。",
        "土曜は予定があって、日曜なら大丈夫です。",
    ],
)
def test_counterproposal_after_unavailable_date_is_not_misread_as_decline(statement):
    raw = _raw_strategy(
        {
            "action": "continue",
            "rationale": "相手が別の日程を提案しています。",
            "evidence": [statement],
            "invite_example": None,
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "continue"


@pytest.mark.parametrize(
    "statement",
    [
        "土曜は会えないけど日曜なら会えます。でも、やっぱり会うのは難しいです。",
        "土曜は無理だけど日曜なら大丈夫です。ただ会いたくありません。",
        "土曜は難しいですが、日曜なら大丈夫です。でもやっぱり会えません。",
        "土曜は難しいですが、日曜なら大丈夫です。でもやっぱり行けません。",
        "土曜なら大丈夫ですが、日曜は会えません。",
        "土曜は会えませんが日曜なら会えません。",
        "土曜は行けませんが日曜なら行けません。",
        "土曜は難しいですが、日曜なら空いてません。",
        "来月は無理ですが再来月なら会えます。でも再来月も都合が悪いです。",
    ],
)
def test_explicit_decline_after_counterproposal_takes_precedence(statement):
    raw = _raw_strategy(
        {
            "action": "continue",
            "rationale": "日曜を提案しています。",
            "evidence": [statement],
            "invite_example": None,
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")
    violations = validate_candidate_replies(
        ["日曜はどうですか？"],
        1,
        counterpart_message=statement,
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert result is not None
    assert result.action == "stop"
    assert any(
        refusal in result.evidence[0]
        for refusal in (
            "会うのは難し",
            "会いたくありません",
            "会えません",
            "行けません",
            "空いてません",
            "都合が悪",
        )
    )
    assert any("誘い" in violation for violation in violations)


def test_scheduling_after_explicit_acceptance_is_allowed():
    violations = validate_candidate_replies(
        ["ぜひ！日曜はどうですか？"],
        1,
        counterpart_message="ぜひ一緒に行きたいです！",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_explicit_later_refusal_overrides_earlier_acceptance_for_scheduling():
    statement = "ぜひ一緒に行きたいです。でも会いたくないです。"
    violations = validate_candidate_replies(
        ["日曜はどうですか？"],
        1,
        counterpart_message=statement,
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "counterpart_message",
    [
        "友達が『一緒に行きたい』って言ってた",
        "友達が土曜は難しいけど、日曜なら大丈夫って言ってた。",
        "カフェは行きたいけど、まだ会うのは不安です。",
    ],
)
def test_scheduling_is_not_allowed_for_hearsay_or_hedged_interest(counterpart_message):
    violations = validate_candidate_replies(
        ["そうなんですね。日曜はどうですか？"],
        1,
        counterpart_message=counterpart_message,
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("誘い" in violation for violation in violations)


def test_first_person_counterproposal_is_not_blocked_by_unrelated_friend_availability():
    violations = validate_candidate_replies(
        ["日曜ならどうですか？"],
        1,
        counterpart_message="友達は土曜は無理だけど、私は日曜なら大丈夫です。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_third_party_date_does_not_authorize_scheduling_on_that_date_for_self():
    violations = validate_candidate_replies(
        ["日曜ならどうですか？"],
        1,
        counterpart_message=(
            "友達が土曜は難しいけど日曜なら大丈夫って言ってました。"
            "私は来週なら会えます。"
        ),
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("誘い" in violation for violation in violations)


def test_first_person_date_is_allowed_when_it_is_the_one_they_offered():
    violations = validate_candidate_replies(
        ["来週ならどうですか？"],
        1,
        counterpart_message=(
            "友達が土曜は難しいけど日曜なら大丈夫って言ってました。"
            "私は来週なら会えます。"
        ),
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_scheduling_is_allowed_after_natural_unavailable_date_counterproposal():
    violations = validate_candidate_replies(
        ["日曜ならどうですか？"],
        1,
        counterpart_message="土曜は予定があって、日曜なら大丈夫です。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_both_suggested_dates_unavailable_do_not_authorize_scheduling():
    violations = validate_candidate_replies(
        ["日曜はどうですか？"],
        1,
        counterpart_message="ぜひ一緒に行きたいです！でも土曜も日曜も予定があって厳しいです。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "reply",
    [
        "来月ホテルで会いましょう。",
        "家で飲みましょう。",
        "部屋で会いませんか？",
        "自宅で待ち合わせしよう。",
        "ホテルを待ち合わせ場所にしよう。",
        "ホテル集合でどうですか？",
        "お互いの家で落ち合いませんか？",
        "部屋集合にしよう。",
        "家に来ませんか？",
        "ホテル泊まりで会おう。",
        "ホテル行こう。",
        "ホテルに泊まりませんか？",
        "家に泊まりませんか？",
        "ホテルで一泊しませんか？",
        "家に泊まっていきませんか？",
        "ホテルに泊まっていかない？",
        "ホテルの客室で会いましょう。",
        "民泊で会いましょう。",
        "自宅の中で会いましょう。",
        "家の中で会おう。",
        "ホテルに泊まっていく？",
        "家に泊まっていく？",
        "部屋に泊まっていく？",
        "客室に泊まっていく？",
        "家で映画を見よう。",
        "家でご飯を食べませんか？",
        "家で会ってからカフェに行こう。",
        "家に寄ってからカフェに行こう。",
        "ホテルのスイートルームで会おう。",
        "スイートで会いませんか？",
        "家で映画でも見ませんか？",
        "家で料理しませんか？",
        "うちでゆっくりしようよ。",
        "カフェで会ってから家で過ごそう。",
        "カフェの後にホテルで休もう。",
        "ホテルのバーで飲んだ後に部屋に行かない？",
        "家に寄ってからカフェで会いましょう。",
        "ホテル行かない？",
        "ホテルで会わない？",
        "ラブホ行かない？",
        "ホテル泊まろ？",
        "泊まっていかない？",
        "お泊まりしない？",
        "家飲みしよ",
        "うち飲みしよ",
        "ホテルに行きませんか？",
        "うちで飲も。",
        "家でご飯食べよ。",
        "家寄ってよ。",
        "うちで飲まない？",
        "うちで飲まない",
        "家で映画見ない？",
        "家でご飯食べない？",
        "家でご飯食べない",
        "家に寄らない？",
        "家に寄らない",
        "今度うちおいでよ。",
        "家寄っていかない？",
        "宅飲みしようよ。",
        "カフェの後は家でどう？",
    ],
)
def test_tapple_reply_cannot_suggest_a_private_place(reply):
    violations = validate_candidate_replies(
        [reply],
        1,
        counterpart_message="今回は会うのは遠慮します。",
        strategy_mode="tapple",
        tapple_action="stop",
    )

    assert any("場所" in violation for violation in violations)


def test_tapple_reply_cannot_insult_the_contact_after_decline():
    violations = validate_candidate_replies(
        ["自分勝手ですね。無理しないでください。"],
        1,
        counterpart_message="今回は会うのは遠慮します。",
        strategy_mode="tapple",
        tapple_action="stop",
    )

    assert any("侮辱" in violation for violation in violations)


def test_tapple_allows_rejecting_a_private_venue_and_confirming_a_public_alternative():
    violations = validate_candidate_replies(
        ["ホテルはやめて、駅前のカフェにしましょう。"],
        1,
        counterpart_message="ぜひ一緒に行きたいです！",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("場所" in violation or "誘い" in violation for violation in violations)


def test_tapple_allows_negated_hotel_mention_with_public_meeting_proposal():
    violations = validate_candidate_replies(
        ["ホテルには行かずカフェで会いましょう。"],
        1,
        counterpart_message="ぜひ一緒に行きたいです！",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("場所" in violation or "誘い" in violation for violation in violations)


def test_tapple_allows_negated_hotel_stay_with_public_meeting_proposal():
    violations = validate_candidate_replies(
        ["ホテルに泊まらずカフェで会いましょう。"],
        1,
        counterpart_message="ぜひ一緒に行きたいです！",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("場所" in violation or "誘い" in violation for violation in violations)


def test_tapple_allows_negated_hotel_meeting_with_public_alternative():
    violations = validate_candidate_replies(
        ["ホテルでは会わずカフェで会いましょう。"],
        1,
        counterpart_message="ぜひ一緒に行きたいです！",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("場所" in violation or "誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "reply",
    [
        "ホテルは使わずカフェで会おう。",
        "ホテルには行かない。カフェで会いましょう。",
        "ホテルでは会わない。カフェにしよう。",
    ],
)
def test_tapple_allows_rejecting_hotel_and_suggesting_public_cafe(reply):
    violations = validate_candidate_replies(
        [reply],
        1,
        counterpart_message="ぜひ一緒に行きたいです！",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("場所" in violation for violation in violations)


def test_tapple_allows_rejecting_suite_and_suggesting_public_cafe():
    violations = validate_candidate_replies(
        ["スイートルームではなくカフェで会いましょう。"],
        1,
        counterpart_message="ぜひ一緒に行きたいです！",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("場所" in violation or "誘い" in violation for violation in violations)


def test_tapple_allows_rejecting_home_meeting_and_suggesting_public_cafe():
    violations = validate_candidate_replies(
        ["家で会うのはやめて、カフェにしよう。"],
        1,
        counterpart_message="ぜひ一緒に行きたいです！",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("場所" in violation or "誘い" in violation for violation in violations)


@pytest.mark.parametrize("reply", ["家に来ないでください。", "家に来ないでね。"])
def test_tapple_does_not_treat_negative_come_over_requests_as_proposals(reply):
    assert not _is_tapple_private_place_proposal(reply)


def test_tapple_allows_public_hotel_lobby_as_meeting_location():
    violations = validate_candidate_replies(
        ["ホテルのロビーで会いましょう。"],
        1,
        counterpart_message="ぜひ一緒に行きたいです！",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("場所" in violation or "誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "reply",
    [
        "今度は会わない？",
        "今度遊びに行かない？",
        "今度デートしない？",
        "今度映画見ない？",
        "今度映画見ない",
        "ホテル行かない？",
        "お茶しない？",
        "お茶しない",
        "今度お茶しない？",
    ],
)
def test_tapple_rejects_casual_reinvitation_after_decline(reply):
    violations = validate_candidate_replies(
        [reply],
        1,
        counterpart_message="今回は会うのは遠慮します。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "reply",
    [
        "家の近くのカフェで会いましょう。",
        "駅前のホテルのカフェで会いましょう。",
        "ホテルのカフェで会いましょう。",
        "ホテルのラウンジで会いましょう。",
        "ホテルのバーで会いましょう。",
    ],
)
def test_tapple_does_not_treat_safe_public_venue_mentions_as_private(reply):
    violations = validate_candidate_replies(
        [reply],
        1,
        counterpart_message="ぜひ一緒に行きたいです！",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("場所" in violation for violation in violations)


def test_tapple_does_not_cancel_a_private_place_violation_with_later_negation():
    violations = validate_candidate_replies(
        ["ホテルに行こう。ホテルはやめよう。"],
        1,
        counterpart_message="ぜひ一緒に行きたいです！",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("場所" in violation for violation in violations)


def test_private_place_invitation_is_repaired_before_reply_is_returned(client, monkeypatch):
    from app import database

    decline = "今回は会うのは遠慮します。"
    first = _raw_strategy(
        {
            "action": "stop",
            "rationale": "相手は会うことを断っています。",
            "evidence": [decline],
            "invite_example": None,
        },
        replies=["わかりました。来月ホテルで会いましょう。"],
    )
    repaired = _raw_strategy(
        {
            "action": "stop",
            "rationale": "相手の断りを尊重します。",
            "evidence": [decline],
            "invite_example": None,
        },
        replies=["わかりました。教えてくれてありがとう。"],
    )

    class QueuedProvider:
        name = "gemini"

        def __init__(self):
            self.responses = [first, repaired]

        def generate(self, **_kwargs):
            return self.responses.pop(0)

        def available_models(self):
            return ["gemini-3.5-flash-lite"]

    provider = QueuedProvider()
    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *_args: provider)
    monkeypatch.setattr(
        "app.routers.generation.get_ai_config",
        lambda: {
            "provider": "gemini",
            "model": "gemini-3.5-flash-lite",
            "api_key": "test-key",
            "temperature": 0.2,
            "max_tokens": 512,
            "history_limit": 50,
            "fallback_provider": "gemini",
            "fallback_model": "gemini-3.1-flash-lite",
            "fallback_api_key": "test-key",
            "secondary_api_key": "",
        },
    )
    monkeypatch.setattr("app.routers.generation.time.sleep", lambda *_args: None)
    database.set_setting("ai_provider", "gemini")
    database.set_setting("ai_model", "gemini-3.5-flash-lite")
    database.set_setting("api_key_gemini", "test-key")

    contact_id = client.post("/api/contacts", json={"name": "テストさん"}).json()["id"]
    client.post(
        f"/api/contacts/{contact_id}/messages",
        json={"sender": "contact", "content": decline},
    )
    response = client.post(
        "/api/generate",
        json={"contact_id": contact_id, "candidates": 1, "strategy_mode": "tapple"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["replies"] == ["わかりました。\n教えてくれてありがとう。"]
    assert response.json()["strategy"]["action"] == "stop"


def test_decline_forces_stop_even_if_model_says_invite():
    conversation = "相手: ごめんなさい、会うのはまだ考えていません"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "会いましょう。",
            "evidence": ["ごめんなさい、会うのはまだ考えていません"],
            "invite_example": "カフェに行きませんか？",
        }
    )
    result = _parse_tapple_strategy(raw, conversation)
    assert result is not None
    assert result.action == "stop"
    assert result.invite_example is None


def test_soft_but_clear_decline_forces_stop():
    statement = "会うのはちょっと難しいです"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "会う意思があります。",
            "evidence": [statement],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )
    result = _parse_tapple_strategy(raw, f"相手: {statement}")
    assert result is not None
    assert result.action == "stop"


def test_positive_desire_to_meet_is_not_misread_as_decline():
    conversation = "相手: 最近会いたくなってきた"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "会いたいという意思があります。",
            "evidence": ["最近会いたくなってきた"],
            "invite_example": "人通りのあるカフェでお茶しませんか？",
        }
    )
    result = _parse_tapple_strategy(raw, conversation)
    assert result is not None
    assert result.action == "wait"


def test_negative_or_conditional_meeting_phrases_never_authorize_invite():
    for statement in (
        "一緒に行けないです",
        "一緒に行けたらいいですね",
        "一緒に行きたいけど、今は難しいです",
        "会いたくないです",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "会う意思があります。",
                "evidence": [statement],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )
        result = _parse_tapple_strategy(raw, f"相手: {statement}")
        assert result is not None, statement
        assert result.action in {"wait", "stop"}, statement
        assert result.invite_example is None, statement


def test_negated_desire_to_meet_forces_stop():
    for statement in (
        "一緒に行きたいとは思わない",
        "一緒に行きたいとは思いません",
        "一緒に行きたいとは思っていない",
        "一緒に行きたいとは思ってない",
        "一緒に行きたいとも思っていません",
        "会いたいとまでは思わない",
        "一緒に行きたいわけではない",
        "一緒に行きたいわけじゃない",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "会う意思があります。",
                "evidence": [statement],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )
        result = _parse_tapple_strategy(raw, f"相手: {statement}")
        assert result is not None, statement
        assert result.action == "stop", statement
        assert result.invite_example is None, statement


def test_no_intention_to_meet_forces_stop():
    statement = "今は会うつもりはありません"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "会う意思があります。",
            "evidence": [statement],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )
    result = _parse_tapple_strategy(raw, f"相手: {statement}")
    assert result is not None
    assert result.action == "stop"
    assert result.invite_example is None


def test_soft_deferrals_wait_without_repeating_the_invitation():
    for statement in (
        "また今度でお願いします",
        "今はタイミングが合わないです",
        "ごめん、ちょっと考えたいです",
        "まだ早いかな",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "誘ってみます。",
                "evidence": [statement],
                "invite_example": "駅前のカフェでお茶しませんか？",
            }
        )
        result = _parse_tapple_strategy(raw, f"相手: {statement}")
        assert result is not None, statement
        assert result.action == "wait", statement
        assert result.invite_example is None, statement


def test_slightly_positive_meeting_emotion_is_not_misread_as_decline():
    for statement in (
        "会うのちょっと楽しみです",
        "会うのはちょっと緊張するけど楽しみ",
        "デートはちょっと楽しみです",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "会う意思があります。",
                "evidence": [statement],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )
        result = _parse_tapple_strategy(raw, f"相手: {statement}")
        assert result is not None, statement
        assert result.action == "wait", statement
        assert result.invite_example is None, statement


def test_explicit_interest_with_safety_concern_does_not_authorize_invite():
    statement = "ぜひ一緒に行きたいですが、安全面が心配です。"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": [statement],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


def test_invite_gate_checks_full_message_when_evidence_quotes_only_interest():
    statement = "ぜひ一緒に行きたいですが、あなたを信用できるか分からないです。"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": ["一緒に行きたい"],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


@pytest.mark.parametrize(
    "statement",
    [
        "ぜひ一緒に行きたいですが、身元が分からず不安です。",
        "ぜひ一緒に行きたいですが、相手のことをよく知らなくて不安です。",
    ],
)
def test_invite_gate_blocks_identity_and_familiarity_safety_concerns(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": [statement],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


def test_unrelated_weather_worry_does_not_block_accepted_date_scheduling():
    violations = validate_candidate_replies(
        ["日曜はどうですか？"],
        1,
        counterpart_message="明日の天気が心配ですが、ぜひ一緒に行きたいです。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_weather_worry_does_not_block_accepted_date_with_meeting_anticipation():
    violations = validate_candidate_replies(
        ["日曜はどうですか？"],
        1,
        counterpart_message="明日の天気が心配ですが、会えるのを楽しみにしています。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_denied_meeting_safety_concern_does_not_block_explicit_invite():
    statement = "会うのは不安ではありません。ぜひ一緒に行きたいです。"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": [statement],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "invite"
    assert result.invite_example is not None


def test_ambiguous_reply_or_response_speed_cannot_authorize_invite():
    conversation = "相手: いいですね！\n相手: 返信早いですね"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "反応が良いです。",
            "evidence": ["返信早いですね"],
            "invite_example": "カフェに行きませんか？",
        }
    )
    result = _parse_tapple_strategy(raw, conversation)
    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


def test_general_interest_in_a_place_is_not_explicit_willingness_to_meet_the_user():
    conversation = "相手: そのカフェ、行ってみたいです"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "カフェに興味がありそうです。",
            "evidence": ["そのカフェ、行ってみたいです"],
            "invite_example": "そのカフェに一緒に行きませんか？",
        }
    )
    result = _parse_tapple_strategy(raw, conversation)
    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


def test_invitation_example_must_be_public_and_must_not_suggest_contact_exchange():
    conversation = "相手: 今度一緒に行きたいです"
    private_example = _parse_tapple_strategy(
        _raw_strategy(
            {
                "action": "invite",
                "rationale": "相手に明確な参加意思があります。",
                "evidence": ["今度一緒に行きたいです"],
                "invite_example": "家で会って、そのあとLINE交換しませんか？",
            }
        ),
        conversation,
    )
    assert private_example is not None
    assert private_example.action == "invite"
    assert private_example.invite_example is None

    public_example = _parse_tapple_strategy(
        _raw_strategy(
            {
                "action": "invite",
                "rationale": "相手に明確な参加意思があります。",
                "evidence": ["今度一緒に行きたいです"],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        ),
        conversation,
    )
    assert public_example is not None
    assert public_example.invite_example == "人の多いカフェでお茶しませんか？"

    public_example_with_unrelated_home_word = _parse_tapple_strategy(
        _raw_strategy(
            {
                "action": "invite",
                "rationale": "相手に明確な参加意思があります。",
                "evidence": ["今度一緒に行きたいです"],
                "invite_example": "家族の話もできる駅前のカフェでお茶しませんか？",
            }
        ),
        conversation,
    )
    assert public_example_with_unrelated_home_word is not None
    assert public_example_with_unrelated_home_word.invite_example == "家族の話もできる駅前のカフェでお茶しませんか？"

    for private_itinerary in (
        "人通りのあるカフェで会ったあと、家で映画を見ませんか？",
        "駅前のカフェで会ったあと、うちで映画を見ませんか？",
        "駅前のカフェのあと、おうちで映画を見ませんか？",
        "駅前のカフェのあと、お家で映画を見ませんか？",
        "カフェで話したあと、家飲みしませんか？",
        "カフェで話したあと、うち飲みしませんか？",
        "カフェの個室でゆっくり話しませんか？",
        "人の多いカフェのあと、ホテルの客室に行きませんか？",
    ):
        result = _parse_tapple_strategy(
            _raw_strategy(
                {
                    "action": "invite",
                    "rationale": "相手に明確な参加意思があります。",
                    "evidence": ["今度一緒に行きたいです"],
                    "invite_example": private_itinerary,
                }
            ),
            conversation,
        )
        assert result is not None
        assert result.action == "invite"
        assert result.invite_example is None, private_itinerary


def test_tapple_reply_candidates_reject_contact_exchange_requests_only_in_tapple_mode():
    unsafe_replies = [
        "今度カフェ行こう！よかったらLINE交換しない？",
        "インスタ教えてもらってもいいですか？",
        "電話番号交換しませんか？",
        "LINE使ってますか？",
        "LINEやっていますか？",
        "LINEで話しませんか？",
        "インスタで話しませんか？",
        "LINEで連絡取りませんか？",
        "InstagramのDMで話しませんか？",
        "LINEしない？",
        "よかったらLINEしませんか？",
    ]

    for reply in unsafe_replies:
        violations = validate_candidate_replies([reply], 1, strategy_mode="tapple")
        assert any("連絡先" in violation for violation in violations), reply
        assert validate_candidate_replies([reply], 1) == []

    harmless_mention = "インスタのアカウントかわいいね"
    assert validate_candidate_replies([harmless_mention], 1, strategy_mode="tapple") == []


def test_strategy_comes_from_the_repaired_output_when_repair_is_accepted(client, monkeypatch):
    from app import database

    accepted_interest = "ぜひ誘ってください"
    first = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手から誘ってほしいと伝えています。",
            "evidence": [accepted_interest],
            "invite_example": "駅前のカフェでお茶しませんか？",
        },
        replies=["LINEで話しませんか？"],
    )
    repaired = _raw_strategy(
        {
            "action": "continue",
            "rationale": "まず行きたい場所を聞きます。",
            "evidence": [accepted_interest],
            "invite_example": None,
        },
        replies=["いいですね！どんなカフェが好きですか？"],
    )

    class QueuedProvider:
        name = "gemini"

        def __init__(self):
            self.responses = [first, repaired]

        def generate(self, **_kwargs):
            return self.responses.pop(0)

        def available_models(self):
            return ["gemini-3.5-flash-lite"]

    provider = QueuedProvider()
    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *_args: provider)
    monkeypatch.setattr(
        "app.routers.generation.get_ai_config",
        lambda: {
            "provider": "gemini",
            "model": "gemini-3.5-flash-lite",
            "api_key": "test-key",
            "temperature": 0.2,
            "max_tokens": 512,
            "history_limit": 50,
            "fallback_provider": "gemini",
            "fallback_model": "gemini-3.1-flash-lite",
            "fallback_api_key": "test-key",
            "secondary_api_key": "",
        },
    )
    monkeypatch.setattr("app.routers.generation.time.sleep", lambda *_args: None)
    database.set_setting("ai_provider", "gemini")
    database.set_setting("ai_model", "gemini-3.5-flash-lite")
    database.set_setting("api_key_gemini", "test-key")

    contact_id = client.post("/api/contacts", json={"name": "テストさん"}).json()["id"]
    client.post(
        f"/api/contacts/{contact_id}/messages",
        json={"sender": "contact", "content": accepted_interest},
    )
    response = client.post(
        "/api/generate",
        json={"contact_id": contact_id, "candidates": 1, "strategy_mode": "tapple"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["replies"] == ["いいですね！\nどんなカフェが好きですか？"]
    assert response.json()["strategy"]["action"] == "continue"


@pytest.mark.parametrize(
    "decline",
    [
        "ごめんなさい、今は会うのは難しいです。",
        "今は会うのはちょっと考えたいです。",
    ],
)
def test_decline_reinvitation_is_repaired_before_a_reply_is_returned(
    client, monkeypatch, decline
):
    from app import database

    first = _raw_strategy(
        {
            "action": "invite",
            "rationale": "もう一度誘ってみます。",
            "evidence": [decline],
            "invite_example": "駅前のカフェでお茶しませんか？",
        },
        replies=["わかった！でも来週カフェに行こうよ！"],
    )
    repaired = _raw_strategy(
        {
            "action": "stop",
            "rationale": "相手が会うのは難しいと伝えています。",
            "evidence": [decline],
            "invite_example": None,
        },
        replies=["わかった、教えてくれてありがとう。無理しないでね。"],
    )

    class QueuedProvider:
        name = "gemini"

        def __init__(self):
            self.responses = [first, repaired]

        def generate(self, **_kwargs):
            return self.responses.pop(0)

        def available_models(self):
            return ["gemini-3.5-flash-lite"]

    provider = QueuedProvider()
    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *_args: provider)
    monkeypatch.setattr(
        "app.routers.generation.get_ai_config",
        lambda: {
            "provider": "gemini",
            "model": "gemini-3.5-flash-lite",
            "api_key": "test-key",
            "temperature": 0.2,
            "max_tokens": 512,
            "history_limit": 50,
            "fallback_provider": "gemini",
            "fallback_model": "gemini-3.1-flash-lite",
            "fallback_api_key": "test-key",
            "secondary_api_key": "",
        },
    )
    monkeypatch.setattr("app.routers.generation.time.sleep", lambda *_args: None)
    database.set_setting("ai_provider", "gemini")
    database.set_setting("ai_model", "gemini-3.5-flash-lite")
    database.set_setting("api_key_gemini", "test-key")

    contact_id = client.post("/api/contacts", json={"name": "テストさん"}).json()["id"]
    client.post(
        f"/api/contacts/{contact_id}/messages",
        json={"sender": "contact", "content": decline},
    )
    response = client.post(
        "/api/generate",
        json={"contact_id": contact_id, "candidates": 1, "strategy_mode": "tapple"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["replies"] == ["わかった、教えてくれてありがとう。\n無理しないでね。"]
    assert response.json()["strategy"]["action"] == "stop"


def test_strategy_is_omitted_when_final_replies_are_replaced_by_safe_clarifications(
    client, monkeypatch
):
    from app import database

    statement = "あれってどうなった？"
    raw = _raw_strategy(
        {
            "action": "continue",
            "rationale": "様子を確認します。",
            "evidence": [statement],
            "invite_example": None,
        },
        replies=["そこってどうなった？"],
    )

    class FixedProvider:
        name = "gemini"

        def generate(self, **_kwargs):
            return raw

        def available_models(self):
            return ["gemini-3.5-flash-lite"]

    provider = FixedProvider()
    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *_args: provider)
    monkeypatch.setattr(
        "app.routers.generation.get_ai_config",
        lambda: {
            "provider": "gemini",
            "model": "gemini-3.5-flash-lite",
            "api_key": "test-key",
            "temperature": 0.2,
            "max_tokens": 512,
            "history_limit": 50,
            "fallback_provider": "gemini",
            "fallback_model": "gemini-3.1-flash-lite",
            "fallback_api_key": "test-key",
            "secondary_api_key": "",
        },
    )
    monkeypatch.setattr("app.routers.generation.time.sleep", lambda *_args: None)
    validation_results = iter(
        [
            ["最初の候補をrepairへ回す"],
            ["状況を確認できる情報がありません"],
            [],
        ]
    )
    monkeypatch.setattr(
        "app.routers.generation.validate_candidate_replies",
        lambda *_args, **_kwargs: next(validation_results),
    )
    monkeypatch.setattr(
        "app.routers.generation._build_safe_reference_clarification_candidates",
        lambda *_args, **_kwargs: ["何の話だったっけ？"],
    )
    database.set_setting("ai_provider", "gemini")
    database.set_setting("ai_model", "gemini-3.5-flash-lite")
    database.set_setting("api_key_gemini", "test-key")

    contact_id = client.post("/api/contacts", json={"name": "テストさん"}).json()["id"]
    client.post(
        f"/api/contacts/{contact_id}/messages",
        json={"sender": "contact", "content": statement},
    )
    response = client.post(
        "/api/generate",
        json={"contact_id": contact_id, "candidates": 1, "strategy_mode": "tapple"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["replies"] == ["何の話だったっけ？"]
    assert "strategy" not in response.json()


def test_malformed_or_missing_strategy_does_not_break_reply_parsing():
    assert _parse_tapple_strategy('{"replies":["a","b","c"]}', "相手: こんにちは") is None
    assert _parse_tapple_strategy('{"strategy":{"action":"maybe"}}', "相手: こんにちは") is None
