"""Opt-in Tapple strategy overlay safety and compatibility tests."""

import json
import re
import sys
from pathlib import Path
import pytest

from app.ai import prompt
from app.routers.generation import (
    _build_repair_messages,
    _repair_violation_categories,
    _is_tapple_private_place_proposal,
    _parse_replies_strict,
    _parse_tapple_strategy as _parse_tapple_strategy_messages,
    _build_safe_tapple_fallback_strategy,
    _tapple_strategy_output_violations,
    _unqualified_tapple_decline_matches,
    validate_candidate_replies,
)
from app.schemas import GenerateRequest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import run_tapple_strategy_benchmark


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


def test_tapple_system_and_generation_prompts_share_one_compatible_json_contract():
    system_prompt = prompt.build_system_prompt(
        contact={"name": "相手"}, strategy_mode="tapple"
    )
    initial = prompt.build_initial_generation_messages(
        system_prompt=system_prompt,
        chat_history_text="相手: こんにちは",
        strategy_mode="tapple",
    )
    revision = prompt.build_revision_messages(
        system_prompt=system_prompt,
        chat_history_text="相手: こんにちは",
        condition="",
        original_generated="こんにちは！",
        revision_instruction="短く",
        strategy_mode="tapple",
    )

    for message in (initial[0], initial[1], revision[0], revision[-1]):
        assert '"strategy"' in message["content"]
        assert '"replies"' in message["content"]
        assert '出力は必ず JSON形式の {"replies": ["案1の返信文章"' not in message["content"]
        assert '"invite_example":null' in message["content"]
        assert "strategyは必須" in message["content"]
        assert "invite_exampleはinvite時のみ文字列" in message["content"]


def test_generic_system_prompt_keeps_replies_only_json_contract():
    system_prompt = prompt.build_system_prompt(contact={"name": "相手"})

    assert '出力は必ず JSON形式の {"replies": ["案1の独立返信文章"' in system_prompt
    assert '"strategy"' not in system_prompt


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
    assert "inviteは相手が同意したという意味ではなく" in messages[1]["content"]
    assert "具体的な共通の活動や場所への関心" in messages[1]["content"]


def test_tapple_prompt_treats_reciprocal_near_term_activity_interest_as_invite_ready():
    messages = prompt.build_initial_generation_messages(
        system_prompt="system",
        chat_history_text=(
            "相手: 駅前のパンケーキのお店が気になっています\n"
            "自分: 僕もパンケーキが好きです\n"
            "相手: 近いうちに行ってみたいです"
        ),
        candidates=1,
        strategy_mode="tapple",
    )

    instruction = messages[1]["content"]
    assert "近いうちに行ってみたい" in instruction
    assert "共通の活動への関心と会話の相互性" in instruction
    assert "inviteを基本方針として選んでください" in instruction
    assert "明示的に一緒に行きたいと言われるまで待つ必要はありません" in instruction
    assert "短い相づちだけの場合は誘いません" in instruction


def test_tapple_prompt_keeps_reply_grounded_when_invite_is_recommended():
    messages = prompt.build_initial_generation_messages(
        system_prompt="system",
        chat_history_text=(
            "相手: 駅前のカフェに行ってみたいです\n"
            "自分: 僕もカフェが気になっています"
        ),
        candidates=1,
        strategy_mode="tapple",
    )

    instruction = messages[1]["content"]
    assert "会話にない自分の体験・予定・意向を事実として足さない" in instruction
    assert "自分が見ていない写真を見た前提にしない" in instruction
    assert "action=inviteの場合、各返信候補にも会話で根拠づけられる低圧な誘いを含め" in instruction
    assert "相手が関心を示した同じ活動へ誘ってください" in instruction
    assert "action=continueやwaitなどinvite以外の場合に" in instruction
    assert "invite_exampleは誘いの方向性を示す別例" in instruction
    assert "その文面を候補へそのままコピーしない" in instruction
    assert "質問形で相手の意向を尋ねる提案" in instruction


def test_tapple_prompts_require_a_safe_example_for_invite():
    initial = prompt.build_initial_generation_messages(
        system_prompt="system",
        chat_history_text="相手: 今度一緒に行きたいです！",
        candidates=1,
        strategy_mode="tapple",
    )
    initial_text = initial[1]["content"]

    assert "actionでinviteを選ぶ場合はinvite_exampleを必ず埋め" in initial_text
    assert "具体的な店名や日時を会話にないのに作らない" in initial_text
    assert "invite_exampleの文面にも駅前やカフェなど公共の場所だと分かる表現" in initial_text
    assert '"invite_example":null' in initial_text
    assert "strategyは必須" in initial_text
    assert "invite_exampleはinvite時のみ文字列" in initial_text

    repair = _build_repair_messages(
        initial,
        _raw_strategy(
            {
                "action": "invite",
                "rationale": "相手から一緒に行きたいと言われています。",
                "evidence": ["今度一緒に行きたいです！"],
                "invite_example": None,
            },
            replies=["一緒に行けるの嬉しい！"],
        ),
        ["invite戦略には安全な誘い方の例が必要です"],
        1,
        strategy_mode="tapple",
    )
    repair_text = repair[-1]["content"]
    assert "actionでinviteを選ぶ場合はinvite_exampleを必ず埋め" in repair_text
    assert "invite_exampleの文面にも駅前やカフェなど公共の場所だと分かる表現" in repair_text
    assert "会話にない自分の体験・予定・意向を事実として足さない" in repair_text
    assert "inviteを基本方針として選んでください" in repair_text
    assert '"invite_example":null' in repair_text
    assert "strategyは必須" in repair_text
    assert '"invite_example":"安全な公共の場所を使った低圧な誘い方の例"' not in repair_text


def test_ambiguous_interest_alone_does_not_authorize_an_invitation():
    statement = "カフェいいですね！行ってみたいな。"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "行きたいと言っています。",
            "evidence": [statement],
            "invite_example": "よかったら駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


def test_wait_rationale_acknowledges_activity_interest_without_mutual_invitation():
    latest = "駅前のパンケーキのお店、近いうちに行ってみたいです"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "会う提案につながる具体的な関心が確認できないため、今は誘いません。",
            "evidence": [latest],
            "invite_example": "よかったら駅前のパンケーキのお店でお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(
        raw,
        "相手: カフェ巡りが好きです。パンケーキもよく食べます\n"
        "自分: 僕もカフェ好きです。パンケーキもよく食べます\n"
        "相手: そうなんですね\n"
        "自分: 駅前のパンケーキのお店も気になってます\n"
        f"相手: {latest}",
    )

    assert result is not None
    assert result.action == "wait"
    assert "活動への関心は見られます" in result.rationale
    assert "会う意思や一緒に行く提案はまだ確認でき" in result.rationale
    assert "具体的な関心が確認できない" not in result.rationale


def test_wait_rationale_preserves_explicit_safety_concern():
    latest = "カフェには行ってみたいですが、会うのは少し怖いです"
    raw = _raw_strategy(
        {
            "action": "wait",
            "rationale": "具体的な関心が確認できませんが、会うことに怖さがあるため、誘わず相手の安心を優先します。",
            "evidence": [latest],
            "invite_example": None,
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {latest}")

    assert result is not None
    assert result.action == "wait"
    assert "会うことに怖さがある" in result.rationale
    assert "具体的な関心が確認できませんが" in result.rationale
    assert "相互性も弱い" not in result.rationale


def test_wait_rationale_corrects_contradictory_no_activity_interest_claim():
    latest = "パンケーキのお店、近いうちに行ってみたいです"
    raw = _raw_strategy(
        {
            "action": "wait",
            "rationale": "具体的な関心が確認できないため、今は誘いません。",
            "evidence": [latest],
            "invite_example": None,
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {latest}")

    assert result is not None
    assert result.action == "wait"
    assert "活動への関心は見られます" in result.rationale
    assert "具体的な関心が確認できない" not in result.rationale


def test_tapple_prompt_uses_declining_engagement_as_a_cue_without_using_reply_speed():
    messages = prompt.build_initial_generation_messages(
        system_prompt="system",
        chat_history_text="相手: うん",
        candidates=3,
        strategy_mode="tapple",
    )

    assert "直近の相手発言が続けて短い相づち" in messages[1]["content"]
    assert "返信速度だけで" in messages[1]["content"]
    assert "追撃や説得" in messages[1]["content"]


def test_tapple_single_candidate_extracts_reply_from_strategy_json():
    messages = prompt.build_initial_generation_messages(
        system_prompt="system",
        chat_history_text="相手: こんにちは",
        candidates=1,
        strategy_mode="tapple",
    )
    assert '"replies": ["案1"]' in messages[1]["content"]
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
    assert '"replies": ["案1"]' in repair[-1]["content"]

    two_candidate_prompt = prompt.build_initial_generation_messages(
        system_prompt="system",
        chat_history_text="相手: こんにちは",
        candidates=2,
        strategy_mode="tapple",
    )[1]["content"]
    assert '"replies": ["案1", "案2"]' in two_candidate_prompt
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
    assert '"replies": ["案1"]' in messages[-1]["content"]
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
        assert f'"replies": [{slots.replace(",", ", ")}]' in revision[-1]["content"]

        initial = prompt.build_initial_generation_messages(
            system_prompt="system",
            chat_history_text="相手: こんにちは",
            candidates=candidate_count,
            strategy_mode="tapple",
        )
        repair = _build_repair_messages(
            initial, "bad", ["JSON不正"], candidate_count, strategy_mode="tapple"
        )
        assert f'"replies": [{slots.replace(",", ", ")}]' in repair[-1]["content"]


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
    assert "AIは相手の信頼性や実際の安全性を判断できません" in result.rationale


def test_strategy_can_suggest_a_low_pressure_invite_without_assuming_consent():
    conversation = (
        "相手: 最近カフェ巡りにはまっています。駅前のパンケーキのお店が気になっていて\n"
        "自分: 僕もカフェ好きです。パンケーキもよく食べます\n"
        "相手: 甘いものだと何が好きですか？\n"
        "自分: パンケーキやプリンが好きです。新しいお店を探すのも楽しいですよね\n"
        "相手: 駅前のパンケーキのお店、写真を見たらおいしそうで近いうちに行ってみたいです！"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "共通の話題が続いており、相手も店に関心を示しています。まだ会うことへの同意ではないため、断りやすい形で打診します。",
            "evidence": ["近いうちに行ってみたいです"],
            "invite_example": "よかったら今度、駅前のカフェでパンケーキを食べませんか？難しければ気にしないでください。",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "invite"
    assert result.invite_example is not None
    assert "会うことへの同意" in result.rationale


def test_activity_interest_must_match_the_shared_activity_subject():
    for latest in (
        "カフェはよく行きます。新作の映画を見てみたいです！",
        "パンケーキは好きですが、映画を見てみたいです！",
        "パンケーキは好きとはいえ、映画を見てみたいです！",
    ):
        conversation = (
            "相手: 最近カフェ巡りとパンケーキが好きです\n"
            "自分: 僕もカフェとパンケーキが好きです\n"
            f"相手: {latest}"
        )
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "共通の話題があるので誘います。",
                "evidence": ["映画を見てみたいです"],
                "invite_example": "よかったら駅前のカフェに行きませんか？",
            }
        )

        result = _parse_tapple_strategy(raw, conversation)

        assert result is not None
        assert result.action == "wait"


def test_unlisted_shared_hobby_can_support_low_pressure_invite():
    conversation = (
        "相手: ボルダリングに興味があります\n"
        "自分: 僕もボルダリングが好きです\n"
        "相手: 最近はじめたところです。ボルダリングを体験してみたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "二人の共通の趣味に相手が関心を示しています。",
            "evidence": ["ボルダリングを体験してみたいです"],
            "invite_example": "よかったら今度、近くのボルダリングジムで一緒に体験してみませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "invite"
    assert result.invite_example is not None


def test_shared_adjective_does_not_count_as_shared_activity():
    conversation = (
        "相手: 美味しいカレーが好きです\n"
        "自分: 僕も美味しい料理が好きです\n"
        "相手: 美味しいラーメンを食べてみたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "共通の話題に相手が関心を示しています。",
            "evidence": ["美味しいラーメンを食べてみたいです"],
            "invite_example": "よかったら今度、駅前のラーメン店に行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


def test_recent_disinterest_blocks_invite_for_unlisted_shared_hobby():
    conversation = (
        "相手: ボルダリングに興味があります\n"
        "自分: 僕もボルダリングが好きです\n"
        "相手: 最近ボルダリングを体験してみたいです\n"
        "自分: ボルダリングはあまり得意ではないです\n"
        "相手: ボルダリングを体験してみたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手の希望に合わせて誘います。",
            "evidence": ["ボルダリングを体験してみたいです"],
            "invite_example": "よかったら今度、近くのボルダリングジムで一緒に体験してみませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


def test_later_disinterest_in_same_message_overrides_earlier_activity_interest():
    conversation = (
        "相手: カフェ巡りが好きです\n"
        "自分: 僕もカフェが好きです。カフェは苦手でした\n"
        "相手: 今度一緒にカフェに行きたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "共通の趣味なので誘います。",
            "evidence": ["今度一緒にカフェに行きたいです"],
            "invite_example": "よかったら今度、駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


def test_unrelated_later_self_message_does_not_clear_activity_disinterest():
    conversation = (
        "相手: ボルダリングに興味があります\n"
        "自分: 僕もボルダリングが好きです\n"
        "相手: 最近ボルダリングを体験してみたいです\n"
        "自分: ボルダリングは好きではないです\n"
        "自分: 週末は映画を見ます\n"
        "相手: ボルダリングを体験してみたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手の希望に合わせて誘います。",
            "evidence": ["ボルダリングを体験してみたいです"],
            "invite_example": "よかったら今度、近くのボルダリングジムで一緒に体験してみませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


def test_explicit_later_interest_renews_activity_after_disinterest():
    conversation = (
        "相手: カフェ巡りが好きです\n"
        "自分: 僕もカフェが好きです\n"
        "相手: 今度カフェに行ってみたいです\n"
        "自分: カフェは苦手でした\n"
        "自分: 今はカフェが好きです\n"
        "相手: 今度一緒にカフェに行きたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "本人が今はカフェを好きだと伝え、相手も一緒に行きたいと話しています。",
            "evidence": ["今度一緒にカフェに行きたいです"],
            "invite_example": "よかったら今度、駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "invite"
    assert result.invite_example is not None


def test_full_latest_message_hedge_blocks_invite_even_when_evidence_omits_it():
    latest = "駅前のカフェに行ってみたいです。タイミングが合えばかな"
    conversation = (
        "相手: カフェ巡りが好きです\n"
        "自分: 僕もカフェ好きです。駅前のお店が気になります\n"
        f"相手: {latest}"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "共通の話題があるので誘います。",
            "evidence": ["行ってみたいです"],
            "invite_example": "よかったら駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "wait"


def test_sparse_acknowledgment_before_interest_does_not_count_as_warm_engagement():
    conversation = (
        "相手: カフェ巡りが好きです。パンケーキもよく食べます\n"
        "自分: 僕もカフェ好きです。パンケーキもよく食べます\n"
        "相手: そうなんですね\n"
        "自分: 駅前のパンケーキのお店も気になってます\n"
        "相手: 駅前のパンケーキのお店、写真を見たらおいしそうで近いうちに行ってみたいです！"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "共通の話題があるので誘います。",
            "evidence": ["近いうちに行ってみたいです"],
            "invite_example": "よかったら駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "wait"


def test_unrelated_recent_question_does_not_supply_shared_activity_engagement():
    conversation = (
        "相手: カフェ巡りが好きで、パンケーキもよく食べます\n"
        "自分: 僕もカフェ好きです。パンケーキも食べます\n"
        "相手: 休日は何をしているんですか？\n"
        "自分: 最近は映画をよく見ます\n"
        "相手: 駅前のパンケーキのお店、近いうちに行ってみたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手の関心に合わせて誘います。",
            "evidence": ["近いうちに行ってみたいです"],
            "invite_example": "よかったら駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "wait"


@pytest.mark.parametrize(
    "latest_self_reply",
    [
        "プリンが好きです。カフェはあまり好きではないです",
        "プリンが好きです。カフェはあまり得意じゃないです",
        "プリンが好きです。最近カフェには行かなくなりました",
    ],
)
def test_recent_disinterest_blocks_invite_from_reusing_an_older_shared_activity(
    latest_self_reply,
):
    conversation = (
        "相手: カフェ巡りが好きです\n"
        "自分: 僕もカフェが好きです\n"
        "相手: パンケーキは何が好きですか？\n"
        f"自分: {latest_self_reply}\n"
        "相手: 駅前のカフェに行ってみたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手が行きたい場所なので誘います。",
            "evidence": ["駅前のカフェに行ってみたいです"],
            "invite_example": "よかったら駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


def test_disinterest_in_another_activity_does_not_block_shared_activity_invite():
    conversation = (
        "相手: カフェ巡りが好きです\n"
        "自分: 僕もカフェが好きです\n"
        "相手: パンケーキは何が好きですか？\n"
        "自分: カフェは好きです、でも映画は嫌いです\n"
        "相手: 駅前のカフェに行ってみたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "二人ともカフェに関心を示しているため、断りやすく提案します。",
            "evidence": ["駅前のカフェに行ってみたいです"],
            "invite_example": "よかったら駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "invite"


@pytest.mark.parametrize(
    ("latest_self_reply", "latest_contact_message", "expected_action"),
    [
        (
            "プリンが好きです。カフェはあまり得意じゃないです",
            "今度一緒にカフェに行きたいです",
            "wait",
        ),
        (
            "プリンが好きです。カフェに行くのは苦手です",
            "今度一緒にカフェに行きたいです",
            "wait",
        ),
        (
            "プリンが好きです。カフェに行くのはあまり好きじゃないです",
            "今度一緒にカフェに行きたいです",
            "wait",
        ),
        (
            "プリンが好きです。カフェは好きです、でも映画は嫌いです",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェは好きだけど人混みが苦手です",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェは苦手ではありません",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェは嫌いじゃありません",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェは苦手じゃありません",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェは嫌いではありません",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェ好き、でも会話は苦手です",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェ好き、でも甘いものは苦手です",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェは嫌いというわけではないです",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェは苦手というほどではないです",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェは苦手なわけではないです",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェは嫌いなわけではないです",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェは好きではないけど今は好きです",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェは苦手でした。今は好きです",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェは苦手でした。今は映画が好きです",
            "今度一緒にカフェに行きたいです",
            "wait",
        ),
        (
            "プリンが好きです。カフェは最近行かなくなったけど、また行きたいです",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェには行かないです",
            "今度一緒にカフェに行きたいです",
            "wait",
        ),
        (
            "プリンが好きです。カフェはまだ行ってないけど気になってます",
            "今度一緒にカフェに行きたいです",
            "invite",
        ),
        (
            "プリンが好きです。カフェはあまり好きではないです",
            "今度一緒に映画を見に行きたいです",
            "invite",
        ),
    ],
)
def test_recent_disinterest_only_blocks_direct_invitation_for_same_activity(
    latest_self_reply, latest_contact_message, expected_action
):
    conversation = (
        "相手: カフェ巡りが好きです\n"
        "自分: 僕もカフェが好きです\n"
        "相手: パンケーキは何が好きですか？\n"
        f"自分: {latest_self_reply}\n"
        f"相手: {latest_contact_message}"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手が一緒に行きたいと言っているため提案します。",
            "evidence": [latest_contact_message],
            "invite_example": "よかったら一緒に行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == expected_action
    if expected_action == "wait":
        assert result.invite_example is None


def test_generic_hontouni_does_not_count_as_shared_book_interest():
    conversation = (
        "相手: 本当に？\n"
        "自分: 本当にそうですね\n"
        "相手: 本当に行ってみたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "共通の話題があるので誘います。",
            "evidence": ["本当に行ってみたいです"],
            "invite_example": "よかったら人の多い書店に行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "wait"


@pytest.mark.parametrize(
    "earlier_concern",
    [
        "初対面の人と会うのは安全面が少し不安です",
        "実際に会うのはまだ少し迷っています",
    ],
)
def test_unresolved_prior_safety_concern_or_hesitation_blocks_later_activity_interest(earlier_concern):
    conversation = (
        "相手: カフェ巡りが好きです。パンケーキのお店も気になっています\n"
        "自分: 僕もカフェが好きで、パンケーキをよく食べます\n"
        f"相手: {earlier_concern}\n"
        "自分: 駅前は人通りの多い場所です\n"
        "相手: 甘いものだと何が好きですか？\n"
        "自分: パンケーキやプリンが好きです\n"
        "相手: 駅前のパンケーキのお店、近いうちに行ってみたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "共通の話題があるので誘います。",
            "evidence": ["近いうちに行ってみたいです"],
            "invite_example": "よかったら駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "wait"


def test_explicitly_resolved_prior_safety_concern_does_not_block_later_interest():
    conversation = (
        "相手: カフェ巡りが好きです。パンケーキのお店も気になっています\n"
        "自分: 僕もカフェが好きで、パンケーキをよく食べます\n"
        "相手: 初対面の人と会うのは安全面が少し不安です\n"
        "自分: 人通りの多い駅前のお店なら安心できそうです\n"
        "相手: 安全面の不安はなくなりました\n"
        "自分: そう言ってもらえてよかったです\n"
        "相手: 甘いものだと何が好きですか？\n"
        "自分: パンケーキやプリンが好きです\n"
        "相手: 駅前のパンケーキのお店、近いうちに行ってみたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "共通の話題があり、不安も解消したと確認できたため提案します。",
            "evidence": ["近いうちに行ってみたいです"],
            "invite_example": "よかったら駅前のカフェでパンケーキを食べませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "invite"


def test_tapple_keeps_prior_decline_until_contact_clearly_reopens_meeting():
    conversation = (
        "相手: カフェ巡りが好きです。\n"
        "自分: 今度一緒に行きませんか？\n"
        "相手: 会うつもりはありません。\n"
        "自分: 分かりました。もう誘いません。\n"
        "相手: 新作のケーキを食べてみたいです。"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "新作のケーキに関心があります。",
            "evidence": ["新作のケーキを食べてみたいです"],
            "invite_example": "よかったら一緒に行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "stop"
    assert result.invite_example is None


def test_clear_contact_reversal_can_reopen_an_invitation_after_prior_decline():
    conversation = (
        "相手: カフェ巡りが好きです。\n"
        "自分: 今度一緒に行きませんか？\n"
        "相手: 会うつもりはありません。\n"
        "自分: 分かりました。もう誘いません。\n"
        "相手: さっきは断ったけど、やっぱりカフェに一緒に行きたいです。"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手から改めて関心が示されました。",
            "evidence": ["やっぱりカフェに一緒に行きたいです"],
            "invite_example": "よかったら駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "invite"


@pytest.mark.parametrize(
    "reopening_message",
    ["あなたと会いたいです。", "あなたと会ってみたいです。"],
)
def test_unprompted_direct_meeting_interest_reopens_prior_refusal(reopening_message):
    conversation = (
        "相手: カフェ巡りが好きです。\n"
        "自分: 今度一緒に行きませんか？\n"
        "相手: 会うつもりはありません。\n"
        "自分: 分かりました。もう誘いません。\n"
        f"相手: {reopening_message}"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手本人が会いたいと明確に伝えています。",
            "evidence": [reopening_message],
            "invite_example": "よかったら駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "invite"


def test_direct_reversal_alongside_third_party_interest_reopens_prior_refusal():
    conversation = (
        "相手: 会うつもりはありません。\n"
        "自分: 分かりました。もう誘いません。\n"
        "相手: 友達は一緒に行きたいと言っていますが、私はあなたと会いたいです。"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手本人が会いたい意思を明確に示しました。",
            "evidence": ["私はあなたと会いたいです"],
            "invite_example": "よかったら駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "invite"


@pytest.mark.parametrize(
    "decline",
    [
        "会うのは遠慮させていただきます。",
        "会うのは遠慮させてもらいます。",
        "会うのはご遠慮ください。",
        "会うのはご遠慮いただけますか。",
    ],
)
def test_polite_enryo_refusal_remains_a_decline(decline):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手がカフェに興味を示しています。",
            "evidence": ["カフェに行ってみたいです"],
            "invite_example": "駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(
        raw,
        f"相手: {decline}\n自分: 分かりました。もう誘いません。\n"
        "相手: 新作のカフェに行ってみたいです。",
    )

    assert result is not None
    assert result.action == "stop"


@pytest.mark.parametrize(
    "statement",
    [
        "会うときは遠慮なく希望を教えてください。ぜひ一緒に行きたいです。",
        "会うなら遠慮なく誘ってください。",
        "今回は遠慮なく誘ってください。",
    ],
)
def test_positive_encouragement_with_enryo_naku_is_not_a_meeting_decline(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手が会うことに前向きです。",
            "evidence": [statement],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "invite"


def test_current_explicit_decline_overrides_contradictory_reopening_language():
    conversation = (
        "相手: カフェ巡りが好きです。\n"
        "自分: 今度一緒に行きませんか？\n"
        "相手: さっきは断ったけど、やっぱりカフェに一緒に行きたいです。"
        "でもあなたとは会うつもりはありません。"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手から改めて関心が示されました。",
            "evidence": ["カフェに一緒に行きたいです"],
            "invite_example": "よかったら駅前のカフェに行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "stop"
    assert result.invite_example is None


def test_contact_counterproposal_reopens_after_a_date_specific_unavailability():
    conversation = (
        "相手: カフェ巡りが好きです。\n"
        "自分: 土曜に駅前のカフェに行きませんか？\n"
        "相手: 土曜は予定があって会えません。\n"
        "自分: 分かりました。また都合のいい時で大丈夫です。\n"
        "相手: 来週なら都合がつきます。"
    )
    raw = _raw_strategy(
        {
            "action": "continue",
            "rationale": "相手から別の日程の提案がありました。",
            "evidence": ["来週なら都合がつきます"],
            "invite_example": None,
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "continue"


def test_multiple_date_specific_unavailabilities_can_reopen_on_a_later_date():
    conversation = (
        "相手: 今週は予定があって会えませんが、来週も都合が悪くて会えません。\n"
        "自分: 分かりました。また都合のいい日があれば教えてください。\n"
        "相手: 来月なら都合がつきます。"
    )
    raw = _raw_strategy(
        {
            "action": "continue",
            "rationale": "相手から別の日程の提案がありました。",
            "evidence": ["来月なら都合がつきます"],
            "invite_example": None,
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "continue"


def test_third_party_interest_does_not_reopen_contact_refusal():
    conversation = (
        "相手: 会うつもりはありません。\n"
        "自分: 分かりました。もう誘いません。\n"
        "相手: さっきは断ったけど、友達は一緒に行きたいと言っています。"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "同行者にも関心があるようです。",
            "evidence": ["友達は一緒に行きたいと言っています"],
            "invite_example": "よかったら一緒に行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "stop"


def test_date_availability_does_not_reopen_a_firm_meeting_refusal():
    conversation = (
        "相手: カフェ巡りが好きです。\n"
        "自分: 今度一緒に行きませんか？\n"
        "相手: 会うつもりはありません。\n"
        "自分: 分かりました。もう誘いません。\n"
        "相手: 来週なら都合がつきます。\n"
        "自分: そうなんですね。\n"
        "相手: 新作のケーキを食べてみたいです。"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "新作のケーキに関心があります。",
            "evidence": ["新作のケーキを食べてみたいです"],
            "invite_example": "よかったら一緒に行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "stop"


def test_firm_refusal_in_a_date_message_survives_a_later_counterproposal():
    conversation = (
        "相手: 土曜は予定があって会えません。でも、あなたとは会うつもりはありません。\n"
        "自分: 分かりました。無理に誘いません。\n"
        "相手: 来週なら都合がつきます。\n"
        "自分: そうなんですね。\n"
        "相手: 新作のケーキを食べてみたいです。"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "新作のケーキに関心があります。",
            "evidence": ["新作のケーキを食べてみたいです"],
            "invite_example": "よかったら一緒に行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "stop"


def test_earlier_firm_refusal_in_same_message_is_not_downgraded_to_date_unavailability():
    conversation = (
        "相手: あなたとは会うつもりはありません。でも土曜は予定があって会えません。\n"
        "自分: 分かりました。無理に誘いません。\n"
        "相手: 来週なら都合がつきます。\n"
        "自分: そうなんですね。\n"
        "相手: 新作のケーキを食べてみたいです。"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "新作のケーキに関心があります。",
            "evidence": ["新作のケーキを食べてみたいです"],
            "invite_example": "よかったら一緒に行きませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "stop"


def test_later_refusal_overrides_date_counterproposal_in_same_message():
    conversation = (
        "相手: 土曜は予定があって会えません。\n"
        "自分: 分かりました。無理に誘いません。\n"
        "相手: 来週なら都合がつきますが、やっぱり会うのはやめます。"
    )
    raw = _raw_strategy(
        {
            "action": "continue",
            "rationale": "相手が来週なら都合がつくと言っています。",
            "evidence": ["来週なら都合がつきます"],
            "invite_example": None,
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "stop"


@pytest.mark.parametrize(
    "tentative_or_negative_reassurance",
    [
        "安心できそうにないです",
        "不安がなくなっていません",
        "安全面は大丈夫ではないです",
        "安全面ではまだ安心したとは言えません",
        "安全面の心配はなくなったわけではありません",
        "安全面の不安はなくなったとは言えないです",
        "会うことに不安はないと思います",
        "会うのは怖くないでしょう",
        "怖くない気がします",
        "安全面の不安はなくなったかも",
        "安全面の不安はなくなったかな",
        "安全面は大丈夫ですか？",
        "安全面の不安はなくなりましたが、会うこと自体はまだ怖いです",
        "安全面の不安はなくなりましたが、まだ怖いです",
    ],
)
def test_tentative_or_negative_reassurance_does_not_clear_prior_safety_concern(
    tentative_or_negative_reassurance,
):
    conversation = (
        "相手: カフェ巡りが好きです。パンケーキのお店も気になっています\n"
        "自分: 僕もカフェが好きで、パンケーキをよく食べます\n"
        "相手: 初対面の人と会うのは安全面が少し不安です\n"
        "自分: 人通りの多い駅前のお店なら安心できそうです\n"
        f"相手: {tentative_or_negative_reassurance}\n"
        "自分: パンケーキやプリンが好きです\n"
        "相手: 甘いものだと何が好きですか？\n"
        "自分: パンケーキやプリンが好きです\n"
        "相手: 駅前のパンケーキのお店、近いうちに行ってみたいです"
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "共通の話題があるので誘います。",
            "evidence": ["近いうちに行ってみたいです"],
            "invite_example": "よかったら駅前のカフェでパンケーキを食べませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, conversation)

    assert result is not None
    assert result.action == "wait"


def test_tapple_benchmark_includes_a_receptive_but_not_yet_agreed_invitation_case():
    scenario = next(
        scenario
        for scenario in run_tapple_strategy_benchmark.SCENARIOS
        if scenario["id"] == "mutual_activity_interest"
    )
    latest_contact = next(
        message["content"]
        for message in reversed(scenario["messages"])
        if message["sender"] == "contact"
    )
    result = {
        "strategy": {
            "action": "invite",
            "rationale": "共通の話題と相手の関心を踏まえ、断りやすい形で提案します。会うことへの同意はまだ確認できていません。",
            "evidence": ["近いうちに行ってみたいです"],
            "invite_example": "よかったら今度、駅前のカフェに行きませんか？難しければ大丈夫です。",
        },
        "replies": [
            "駅前のパンケーキのお店、よかったら一緒に行ってみませんか？",
            "パンケーキいいですね！駅前のお店、よかったら今度一緒に行きませんか？",
            "パンケーキいいですね！よかったら今度そのお店に行きませんか？",
        ],
    }

    assert "近いうちに行ってみたいです" in latest_contact
    assert scenario["expected_action"] == "invite"
    assert run_tapple_strategy_benchmark._evaluate_result(scenario, result) == []


def test_tapple_benchmark_covers_recent_disinterest_in_the_proposed_activity():
    scenario = next(
        scenario
        for scenario in run_tapple_strategy_benchmark.SCENARIOS
        if scenario["id"] == "recent_activity_disinterest"
    )

    assert scenario["expected_action"] == "wait"
    assert "あまり好きではない" in scenario["messages"][-2]["content"]
    result = {
        "strategy": {
            "action": "wait",
            "rationale": "今の本人の好みと違うため、カフェへ誘わず話題を受け止めます。",
            "evidence": ["駅前のカフェに行ってみたいです"],
            "invite_example": None,
        },
        "replies": [
            "プリンの話も出てましたね。どんな種類が好きですか？",
            "駅前のお店ってプリンもあるんですか？",
            "カフェのお店なんですね。前に話していたプリンも置いてあるんでしょうか？",
        ],
    }
    assert run_tapple_strategy_benchmark._evaluate_result(scenario, result) == []


def test_tapple_benchmark_covers_an_unlisted_shared_hobby():
    scenario = next(
        scenario
        for scenario in run_tapple_strategy_benchmark.SCENARIOS
        if scenario["id"] == "unlisted_shared_hobby"
    )

    assert scenario["expected_action"] == "invite"
    result = {
        "strategy": {
            "action": "invite",
            "rationale": "二人の共通の趣味に相手が関心を示しています。",
            "evidence": ["ボルダリングを体験してみたいです"],
            "invite_example": "よかったら今度、近くのボルダリングジムで体験してみませんか？",
        },
        "replies": [
            "ボルダリング体験楽しそうですね！よかったら今度一緒に行きませんか？",
            "いいですね！今度ボルダリングを一緒にやってみませんか？",
            "ボルダリング気になります！よかったら一緒に体験してみませんか？",
        ],
    }
    assert run_tapple_strategy_benchmark._evaluate_result(scenario, result) == []


def test_tapple_benchmark_does_not_use_another_interest_to_clear_disinterest():
    scenario = next(
        scenario
        for scenario in run_tapple_strategy_benchmark.SCENARIOS
        if scenario["id"] == "different_activity_does_not_clear_disinterest"
    )

    assert scenario["expected_action"] == "wait"
    assert "今は映画が好き" in scenario["messages"][-2]["content"]
    result = {
        "strategy": {
            "action": "wait",
            "rationale": "映画への関心はカフェへの苦手意識を解消しないため、別の話題に応じます。",
            "evidence": ["今度一緒にカフェに行きたいです"],
            "invite_example": None,
        },
        "replies": [
            "映画が好きなんですね！最近観て印象に残った作品ありますか？",
            "映画いいですね、最近なにか観ましたか？",
            "どんなジャンルの映画をよく観ますか？",
        ],
    }
    assert run_tapple_strategy_benchmark._evaluate_result(scenario, result) == []


@pytest.mark.parametrize(
    "scenario_id, parsed_action",
    [
        ("mutual_activity_interest", "invite"),
        ("unlisted_shared_hobby", "invite"),
        ("shared_activity_low_reciprocity", "wait"),
        ("recent_activity_disinterest", "wait"),
        ("different_activity_does_not_clear_disinterest", "wait"),
    ],
)
def test_activity_interest_pair_runs_through_production_strategy_parser(scenario_id, parsed_action):
    scenario = next(
        scenario
        for scenario in run_tapple_strategy_benchmark.SCENARIOS
        if scenario["id"] == scenario_id
    )
    strategy = {
        "action": "invite",
        "rationale": (
            "共通の話題が続き、相手も関心を広げているため、断りやすく提案します。"
            if parsed_action == "invite"
            else "共通の話題があっても、相手の反応が短いため今は待ちます。"
        ),
        "evidence": [scenario["messages"][-1]["content"]],
        "invite_example": (
            "よかったら今度、近くのボルダリングジムで体験してみませんか？"
            if scenario_id == "unlisted_shared_hobby"
            else "よかったら駅前のカフェでパンケーキを食べませんか？難しければ大丈夫です。"
        ),
    }

    parsed = _parse_tapple_strategy_messages(
        _raw_strategy(strategy), scenario["messages"]
    )

    assert parsed is not None
    assert parsed.action == parsed_action


def test_tapple_benchmark_requires_wait_after_engagement_declines():
    scenario = next(
        scenario
        for scenario in run_tapple_strategy_benchmark.SCENARIOS
        if scenario["id"] == "declining_engagement"
    )
    latest_contact = next(
        message["content"]
        for message in reversed(scenario["messages"])
        if message["sender"] == "contact"
    )
    result = {
        "strategy": {
            "action": "wait",
            "rationale": "直近は短い返答が続いています。今は追わずに待ちます。",
            "evidence": [latest_contact],
            "invite_example": None,
        },
        "replies": [
            "そうなんですね。また話せるときに話しましょう。",
            "また話したくなったら話そう",
            "了解です。無理せず過ごしてくださいね",
        ],
    }

    assert run_tapple_strategy_benchmark._evaluate_result(scenario, result) == []


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


@pytest.mark.parametrize(
    "statement",
    [
        "友達が『一緒に行きたい』って言ってた",
        "妹があなたと一緒に行きたいと言ってました",
        "先輩が一緒に行きたいと話していました",
    ],
)
def test_third_party_reported_interest_does_not_authorize_an_invitation(statement):
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
        "ぜひ来週会いましょう。",
        "ぜひ会いましょう。",
        "会いましょう。",
        "お会いしましょう。",
        "ぜひお会いしませんか？",
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
        conversation_messages=[
            {"sender": "self", "content": "今度カフェに一緒に行きませんか？"},
            {"sender": "contact", "content": "ぜひ一緒に行きたいです！"},
        ],
    )

    assert not any("誘い" in violation for violation in violations)


def test_general_activity_interest_without_a_prior_invitation_does_not_allow_scheduling():
    violations = validate_candidate_replies(
        ["来週カフェに行きませんか？"],
        1,
        counterpart_message="カフェに行きたいです。",
        strategy_mode="tapple",
        tapple_action="continue",
        conversation_messages=[
            {"sender": "contact", "content": "カフェに行きたいです。"},
        ],
    )

    assert any("誘い" in violation for violation in violations)


def test_short_acceptance_of_prior_invitation_allows_scheduling():
    violations = validate_candidate_replies(
        ["楽しみです！日曜はどうですか？"],
        1,
        counterpart_message="うん、いいよ！楽しみ！",
        strategy_mode="tapple",
        tapple_action="continue",
        conversation_messages=[
            {"sender": "self", "content": "今度カフェに一緒に行きませんか？"},
            {"sender": "contact", "content": "うん、いいよ！楽しみ！"},
        ],
    )

    assert not any("誘い" in violation for violation in violations)


def test_explicit_joint_meeting_interest_can_move_to_scheduling_without_prior_invite():
    violations = validate_candidate_replies(
        ["楽しみです！日曜はどうですか？"],
        1,
        counterpart_message="ぜひ一緒に行きたいです！",
        strategy_mode="tapple",
        tapple_action="continue",
        conversation_messages=[
            {"sender": "contact", "content": "ぜひ一緒に行きたいです！"},
        ],
    )

    assert not any("誘い" in violation for violation in violations)


def test_short_positive_reply_does_not_accept_an_old_invitation_after_topic_change():
    violations = validate_candidate_replies(
        ["来週カフェに行きませんか？"],
        1,
        counterpart_message="いいですね！",
        strategy_mode="tapple",
        tapple_action="continue",
        conversation_messages=[
            {"sender": "contact", "content": "カフェが好きです。"},
            {"sender": "self", "content": "今度カフェに一緒に行きませんか？"},
            {"sender": "contact", "content": "最近映画も見ました。"},
            {"sender": "self", "content": "どんな映画を見たんですか？"},
            {"sender": "contact", "content": "いいですね！"},
        ],
    )

    assert any("誘い" in violation for violation in violations)


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
        "会いたいけど少し怖いです。",
        "会いたいけど少し不安です。",
        "土曜は無理だけど日曜なら大丈夫です。でも会うのは少し怖いです。",
        "土曜は難しいけど日曜なら大丈夫です。でも会うのは不安です。",
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


def test_scheduling_is_not_allowed_when_negated_safety_is_uncertain():
    for counterpart_message in (
        "怖くないかもしれませんが、土曜は無理だけど日曜なら大丈夫です。",
        "不安じゃないとは思えませんが、土曜は無理だけど日曜なら大丈夫です。",
    ):
        violations = validate_candidate_replies(
            ["日曜はどうですか？"],
            1,
            counterpart_message=counterpart_message,
            strategy_mode="tapple",
            tapple_action="continue",
        )

        assert any("誘い" in violation for violation in violations), counterpart_message


def test_first_person_counterproposal_is_not_blocked_by_unrelated_friend_availability():
    violations = validate_candidate_replies(
        ["日曜ならどうですか？"],
        1,
        counterpart_message="友達は土曜は無理だけど、私は日曜なら大丈夫です。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "third_party_availability",
    [
        "友達が土曜は難しいけど日曜なら大丈夫って言ってました。",
        "妹が土曜は難しいけど日曜なら大丈夫って言ってました。",
    ],
)
def test_third_party_date_does_not_authorize_scheduling_on_that_date_for_self(
    third_party_availability,
):
    violations = validate_candidate_replies(
        ["日曜ならどうですか？"],
        1,
        counterpart_message=f"{third_party_availability}私は来週なら会えます。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("誘い" in violation for violation in violations)


def test_reported_third_party_interest_does_not_authorize_date_scheduling():
    statement = "友達がぜひ行きたいと話していたそうです。"
    violations = validate_candidate_replies(
        ["日曜はどうですか？"],
        1,
        counterpart_message=statement,
        conversation_messages=[{"sender": "contact", "content": statement}],
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
    assert response.json()["replies"] == ["わかりました。教えてくれてありがとう。"]
    assert response.json()["strategy"]["action"] == "stop"


def test_invitation_strategy_without_example_is_repaired(client, monkeypatch):
    from app import database

    interest = "今度そのカフェに行ってみたいです！"
    replies = [
        "よかったら今度、そのカフェに一緒に行ってみませんか？",
        "駅前のカフェ、今度一緒に行きませんか？",
        "都合が合えば、そのカフェに一緒に行ってみませんか？",
    ]
    first = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手がカフェに行ってみたいと話しているため、低圧に意向を尋ねます。",
            "evidence": [interest],
            "invite_example": None,
        },
        replies=replies,
    )
    repaired = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手がカフェに行ってみたいと話しているため、低圧に意向を尋ねます。",
            "evidence": [interest],
            "invite_example": "人目のあるカフェでお茶しませんか？",
        },
        replies=replies,
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
        json={"sender": "contact", "content": "駅前に気になるカフェがあるんです"},
    )
    client.post(
        f"/api/contacts/{contact_id}/messages",
        json={"sender": "self", "content": "僕もそのカフェが気になっています"},
    )
    client.post(
        f"/api/contacts/{contact_id}/messages",
        json={"sender": "contact", "content": interest},
    )
    response = client.post(
        "/api/generate",
        json={"contact_id": contact_id, "candidates": 3, "strategy_mode": "tapple"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["strategy"]["action"] == "invite"
    assert response.json()["strategy"]["invite_example"] == "人目のあるカフェでお茶しませんか？"
    assert provider.responses == []


@pytest.mark.parametrize(
    "counterpart_message, action",
    [
        ("ごめんなさい、今は会うのは難しいです。", "stop"),
        ("会いたい気持ちはありますが、会うのはまだ少し迷っています。", "wait"),
        ("会いたいですが、直接会うのは安全面が不安です。", "wait"),
    ],
)
def test_tapple_rejects_indirect_persuasion_after_decline_or_concern(
    counterpart_message, action
):
    violations = validate_candidate_replies(
        ["わかりました。考え直してもらえるとうれしいです。"],
        1,
        counterpart_message=counterpart_message,
        strategy_mode="tapple",
        tapple_action=action,
    )

    assert any("考え直す" in violation for violation in violations)


def test_tapple_allows_empathy_and_an_off_ramp_after_decline():
    violations = validate_candidate_replies(
        ["わかりました。教えてくれてありがとう。無理せず過ごしてね。"],
        1,
        counterpart_message="ごめんなさい、今は会うのは難しいです。",
        strategy_mode="tapple",
        tapple_action="stop",
    )

    assert violations == []


@pytest.mark.parametrize(
    "history",
    [
        [
            {"sender": "contact", "content": "会うのはまだ少し迷っています。"},
            {"sender": "self", "content": "急がなくて大丈夫です。"},
            {"sender": "contact", "content": "そうですね。"},
        ],
        [
            {"sender": "contact", "content": "直接会うのは安全面が不安です。"},
            {"sender": "self", "content": "無理に決めなくて大丈夫です。"},
            {"sender": "contact", "content": "そうですね。"},
        ],
        [
            {"sender": "contact", "content": "直接会うのは安全面が不安です。"},
            {"sender": "self", "content": "無理に決めなくて大丈夫です。"},
            {"sender": "contact", "content": "お化け屋敷は怖くないです。"},
        ],
        [
            {"sender": "contact", "content": "直接会うのは安全面が不安です。"},
            {"sender": "self", "content": "無理に決めなくて大丈夫です。"},
            {"sender": "contact", "content": "会うのは怖くないですし、安全面が気になります。"},
        ],
        [
            {"sender": "contact", "content": "会うつもりはありません。"},
            {"sender": "self", "content": "分かりました。もう誘いません。"},
            {"sender": "contact", "content": "そうですね。"},
        ],
    ],
    ids=[
        "prior-hesitation",
        "prior-safety-concern",
        "unrelated-fear-denial",
        "contradictory-safety-concern",
        "prior-decline",
    ],
)
def test_tapple_carries_unresolved_meeting_boundary_into_reply_validation(history):
    violations = validate_candidate_replies(
        ["わかりました。考え直してもらえるとうれしいです。"],
        1,
        counterpart_message=history[-1]["content"],
        chat_history_text="相手: 会うことへの懸念があります\n自分: 急がなくて大丈夫です\n相手: そうですね。",
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="wait",
    )

    assert any("考え直す" in violation for violation in violations)


@pytest.mark.parametrize(
    "pressure_reply",
    [
        "わかりました。一度だけ会ってくれませんか？",
        "一回だけ、会うことを考えてくれませんか？",
        "もう一度だけチャンスをください。",
        "最後に一度だけお願いします。",
        "断られたのは分かっていますが、少しだけ会ってほしいです。",
        "今回だけ会ってくれませんか？",
        "そう言わずに会ってもらえませんか？",
        "もう少し考えてくれませんか？",
        "ちょっとだけ会うことを考えてもらえませんか？",
        "考え直していただけると幸いです。",
    ],
)
def test_tapple_blocks_one_more_chance_pressure_after_historical_decline(pressure_reply):
    history = [
        {"sender": "contact", "content": "会うつもりはありません。"},
        {"sender": "self", "content": "分かりました。もう誘いません。"},
        {"sender": "contact", "content": "そうですね。"},
    ]

    violations = validate_candidate_replies(
        [pressure_reply],
        1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="stop",
    )

    assert any("考え直す" in violation for violation in violations)


@pytest.mark.parametrize(
    "boundary_message",
    [
        "直接会うのは安全面が不安です。",
        "会うのは少し迷っています。",
    ],
)
def test_tapple_invite_action_cannot_override_unresolved_historical_boundary(
    boundary_message,
):
    history = [
        {"sender": "contact", "content": boundary_message},
        {"sender": "self", "content": "無理に決めなくて大丈夫です。"},
        {"sender": "contact", "content": "そうですね。"},
    ]

    violations = validate_candidate_replies(
        ["そうなんですね。よかったら来週カフェに行きませんか？"],
        1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="invite",
    )

    assert any("誘い" in violation or "断り" in violation for violation in violations)


@pytest.mark.parametrize(
    "reply, expects_action_mismatch",
    [
        ("駅前のカフェ気になりますよね笑", True),
        ("今度一緒に行ってみますか！", False),
        ("今度一緒にボルダリングを体験してみませんか？", False),
    ],
)
def test_tapple_invite_action_requires_each_candidate_to_make_a_low_pressure_invite(
    reply, expects_action_mismatch
):
    history = [
        {"sender": "contact", "content": "駅前に気になるカフェがあるんです"},
        {"sender": "self", "content": "僕もそのカフェが気になってます"},
        {"sender": "contact", "content": "今度そのカフェに行ってみたいです！"},
    ]

    violations = validate_candidate_replies(
        [reply],
        1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="invite",
    )

    action_violations = [
        violation for violation in violations if "strategy.action=invite" in violation
    ]
    assert bool(action_violations) is expects_action_mismatch


@pytest.mark.parametrize(
    "reply",
    [
        "カフェいいですね、今度一緒に映画を見に行きませんか？",
        "カフェも気になりますし、今度映画を見に行きませんか？",
        "今度カフェでお茶して、そのあと映画を見に行きませんか？",
    ],
)
def test_tapple_invitation_must_target_the_discussed_activity(reply):
    history = [
        {"sender": "contact", "content": "駅前に気になるカフェがあるんです"},
        {"sender": "self", "content": "僕もそのカフェが気になってます"},
        {"sender": "contact", "content": "今度そのカフェに行ってみたいです！"},
    ]

    violations = validate_candidate_replies(
        [reply],
        1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="invite",
    )

    assert any("誘い先" in violation for violation in violations)


def test_tapple_repair_guidance_turns_unverified_desire_into_an_invitation_question():
    violation = "案2に本人の未確認の希望を追加しています。本人の好みや希望を作らないでください。"
    categories = _repair_violation_categories([violation])
    repair = _build_repair_messages(
        [],
        "{\"replies\":[\"僕も行きたいです\"]}",
        [violation],
        1,
        strategy_mode="tapple",
        tapple_action="invite",
    )
    repair_text = repair[-1]["content"]

    assert "unsupported_personal_desire" in categories
    assert "希望を断定しない" in repair_text
    assert "相手の意向を尋ねる質問形の誘い" in repair_text
    assert "action=inviteを維持" in repair_text
    assert "誘い先は相手が関心を示した活動そのもの" in repair_text
    assert "同行を前提にした表現や新しい誘いに変えない" not in repair_text
    assert "自然な反応か関連する短い問いで返してください" not in repair_text


def test_tapple_positive_interest_does_not_clear_unresolved_safety_concern_for_scheduling():
    history = [
        {"sender": "contact", "content": "直接会うのは安全面が不安です。"},
        {"sender": "self", "content": "無理に決めなくて大丈夫です。"},
        {"sender": "contact", "content": "ぜひ一緒に行きたいです。"},
    ]

    violations = validate_candidate_replies(
        ["よかったです。来週カフェでお茶しませんか？"],
        1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("誘い" in violation or "断り" in violation for violation in violations)


def test_tapple_third_party_interest_does_not_clear_contact_hesitation():
    history = [
        {"sender": "contact", "content": "会うのはまだ少し迷っています。"},
        {"sender": "self", "content": "無理に決めなくて大丈夫です。"},
        {"sender": "contact", "content": "友達は一緒に行きたいと言っています。"},
    ]

    violations = validate_candidate_replies(
        ["よかったら来週カフェに行きませんか？"],
        1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="invite",
    )

    assert any("誘い" in violation or "断り" in violation for violation in violations)


@pytest.mark.parametrize(
    "statement",
    [
        "友達に会いたいです。",
        "友達と会いたいです。",
        "友達から『会いたいです』と言われました。",
        "友達から『会いたいです』と言っていました。",
        "友達から『会いたいです』って言ってました。",
        "友達から『会いたいです』と話していました。",
        "友達は『あなたに会いたいです』と言っていました。",
        "友達は『あなたに会いたいです』って言ってました。",
    ],
)
def test_tapple_wish_to_meet_third_party_does_not_clear_contact_hesitation(statement):
    history = [
        {"sender": "contact", "content": "会うのはまだ少し迷っています。"},
        {"sender": "self", "content": "無理に決めなくて大丈夫です。"},
        {"sender": "contact", "content": statement},
    ]

    violations = validate_candidate_replies(
        ["よかったら来週カフェに行きませんか？"],
        1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="invite",
    )

    assert any("誘い" in violation or "断り" in violation for violation in violations)


@pytest.mark.parametrize(
    "statement",
    [
        "友達は一緒に行きたいと言っていますが、私はあなたと会いたいです。",
        "友達はいるけど、私はあなたと会いたいです。",
        "友達は『あなたに会いたいです』と言っていましたが、私はあなたと会いたいです。",
        "妹の話ですが、私はあなたと一緒に行きたいです。",
        "親切な人で、私もあなたと一緒に行きたいです。",
    ],
)
def test_tapple_first_person_interest_can_clear_hesitation_alongside_third_party_interest(
    statement,
):
    history = [
        {"sender": "contact", "content": "会うのはまだ少し迷っています。"},
        {"sender": "self", "content": "無理に決めなくて大丈夫です。"},
        {
            "sender": "contact",
            "content": statement,
        },
    ]

    violations = validate_candidate_replies(
        ["嬉しいです。よかったら来週カフェに行きませんか？"],
        1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="invite",
    )

    assert not any("誘い" in violation or "断り" in violation for violation in violations)


def test_tapple_safety_resolution_does_not_clear_later_standalone_fear():
    history = [
        {"sender": "contact", "content": "直接会うのは安全面が不安です。"},
        {"sender": "self", "content": "無理に決めなくて大丈夫です。"},
        {
            "sender": "contact",
            "content": "安全面の不安はありません。まだ不安です。",
        },
    ]

    violations = validate_candidate_replies(
        ["よかったら来週カフェに行きませんか？"],
        1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="invite",
    )

    assert any("誘い" in violation or "断り" in violation for violation in violations)


@pytest.mark.parametrize(
    "resolution",
    [
        "安全面の不安はなくなりました。",
        "安全面の不安はありません。",
        "会うことに不安はないです。",
        "直接会うのは怖くありません。",
        "安全面の不安はなくなりました。明日は雨でしょう。",
    ],
)
def test_tapple_does_not_treat_resolved_safety_concern_as_current_pressure_context(
    resolution,
):
    history = [
        {"sender": "contact", "content": "直接会うのは安全面が不安です。"},
        {"sender": "self", "content": "無理に決めなくて大丈夫です。"},
        {"sender": "contact", "content": f"{resolution}ぜひ一緒に行きたいです。"},
    ]

    violations = validate_candidate_replies(
        ["わかりました。考え直してもらえるとうれしいです。"],
        1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("考え直す" in violation for violation in violations)


def test_tapple_allows_supportive_reply_when_prior_safety_concern_is_unresolved():
    history = [
        {"sender": "contact", "content": "直接会うのは安全面が不安です。"},
        {"sender": "self", "content": "無理に決めなくて大丈夫です。"},
        {"sender": "contact", "content": "そうですね。"},
    ]

    violations = validate_candidate_replies(
        ["分かりました。無理に会わなくて大丈夫です。"],
        1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="wait",
    )

    assert violations == []


def test_production_generation_repairs_pressure_after_historical_hesitation(
    client, monkeypatch
):
    from app import database

    latest_contact = "そうですね。"
    unsafe_reply = "わかりました。考え直してもらえるとうれしいです。"
    safe_reply = "分かりました。無理に会わなくて大丈夫です。"
    strategy = {
        "action": "wait",
        "rationale": "会うことへの迷いが残っています。",
        "evidence": [latest_contact],
        "invite_example": None,
    }
    responses = [
        _raw_strategy(strategy, replies=[unsafe_reply]),
        _raw_strategy(strategy, replies=[safe_reply]),
    ]

    class QueuedProvider:
        name = "gemini"

        def generate(self, **_kwargs):
            return responses.pop(0)

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
    for sender, content in (
        ("contact", "会うのはまだ少し迷っています。"),
        ("self", "急がなくて大丈夫です。"),
        ("contact", latest_contact),
    ):
        client.post(
            f"/api/contacts/{contact_id}/messages",
            json={"sender": sender, "content": content},
        )

    response = client.post(
        "/api/generate",
        json={"contact_id": contact_id, "candidates": 1, "strategy_mode": "tapple"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["replies"] == ["分かりました。無理に会わなくて大丈夫です。"]
    assert response.json()["strategy"]["action"] == "wait"
    assert responses == []


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


@pytest.mark.parametrize(
    "decline",
    [
        "お会いするのは難しいです。",
        "お会いするつもりはありません。",
        "お会いするのは遠慮させていただきます。",
        "お会いできません。",
        "お会いできかねます。",
        "お会いすることはできません。",
        "お会いしたくありません。",
        "お会いしたくないです。",
        "お会いしたくはありません。",
        "お会いしたくはないです。",
        "お会いできないです。",
        "お会いするのを控えます。",
        "お会いするのは控えさせていただきます。",
        "お会いするのは厳しいです。",
        "お会いするのは遠慮いたします。",
        "お誘いは辞退させていただきます。",
        "今回は見送らせてください。",
        "今回は控えさせていただきます。",
        "お会いするのは差し控えさせていただきます。",
        "お会いすることは致しかねます。",
        "ご一緒するのは難しいです。",
        "会うことは控えたいです。",
        "今回はお誘いをお断りさせていただきます。",
        "お会いするのはお断りいたします。",
        "お誘いはお断りしたいです。",
        "お会いすることをお断りしたいです。",
        "今回はお断りいたします。",
        "今回のお誘いについてはお断りさせていただきます。",
        "今回はお断り。",
        "今回はお断りです。",
        "お誘いについてはお断り。",
        "今回はお誘いについてはお断り。",
        "会うのはご遠慮いただきたいです。",
        "一度会ってみたいと思っていましたが、やっぱりお会いするのはやめておきます。",
    ],
)
def test_formal_meeting_refusals_block_invite(decline):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "会うことに前向きです。",
            "evidence": [decline],
            "invite_example": "よかったら来週カフェで会いませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {decline}")
    violations = validate_candidate_replies(
        ["よかったら来週カフェで会いませんか？"],
        1,
        counterpart_message=decline,
        strategy_mode="tapple",
        tapple_action="invite",
    )

    assert result is not None
    assert result.action != "invite"
    assert result.invite_example is None
    assert any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "statement",
    [
        "お会いするのは難しくありません。ぜひ一緒に行きたいです。",
        "お会いするのは難しいとは言えません。ぜひ一緒に行きたいです。",
        "お会いするのは難しいとは限りません。ぜひ一緒に行きたいです。",
        "お会いするのは難しいわけではありません。ぜひ一緒に行きたいです。",
        "お会いするのは難しいわけじゃありません。ぜひ一緒に行きたいです。",
        "お会いするのは難しくはないです。ぜひ一緒に行きたいです。",
        "お会いするのは難しいとは思っていません。ぜひ一緒に行きたいです。",
        "お会いするのは難しいことではありません。ぜひ一緒に行きたいです。",
        "お会いしたくありませんとは思っていません。ぜひ会いたいです。",
        "お断りなく進めてください。ぜひ会いましょう。",
        "お会いするのは難しいとは思えません。ぜひ会いたいです。",
        "お会いできませんとは言い切れません。ぜひ会いたいです。",
        "お会いするのは難しいと感じません。ぜひ会いたいです。",
        "お会いするのは難しいことはありません。ぜひ会いたいです。",
        "お会いするのは無理ではありません。ぜひ会いたいです。",
        "会うのは無理じゃないです。ぜひ会いたいです。",
        "会うのは無理じゃなくて大丈夫です。ぜひ会いたいです。",
        "会うのは厳しくありません。ぜひ会いたいです。",
        "前回会うのは厳しくなかったので、今回も会いたいです。",
        "会うのは難しくなかったです。ぜひ会いたいです。",
        "会うのは難しくはなかったです。ぜひ会いたいです。",
        "会うのは難しかったです。ぜひ会いたいです。",
        "会うのは厳しかったです。ぜひ会いたいです。",
        "会うのは難しいかもしれませんが、私は会いたいです。",
        "会うのは厳しいかもしれませんが、私は会いたいです。",
        "会うのは無理かもしれません。",
        "もし会うのが無理なら、正直に教えてください。私はぜひ会いたいです。",
        "万が一会うのが難しければ、遠慮なく言ってください。私はあなたと会いたいです。",
        "会うのは無理と言われましたが、私はぜひ会いたいです。",
        "会うのは無理と友達に言われましたが、私はぜひ会いたいです。",
        "前回は会えませんでしたが、今度はぜひ会いたいです。",
        "今日は会えませんが、明日なら会えます。",
        "会うのは無理だとは思いません。ぜひ会いたいです。",
        "会うのは難しいとは思ってない。ぜひ会いたいです。",
        "「会うのは無理」と私は言いましたが、私はぜひ会いたいです。",
        "「会うのは無理」と言いましたが、会いたいです。",
        "「会うのは無理」。私はぜひ会いたいです。",
        "私は「会うのは無理」と友達に言いましたが、今は会いたいです。",
        "会うのは無理だと思われたくないです。ぜひ会いたいです。",
        "会うのは難しいと言われるかもしれませんが、私は会いたいです。",
        "会うのは無理だったけど、今はぜひ会いたいです。",
        "今はあなたと会いたいです。前は会うのは無理だと思っていました。",
        "現在は会いたいです。以前は会うのは難しいと思っていました。",
        "ご一緒するのは無理ではありません。ぜひ会いたいです。",
        "会うのは無理ではありません。ぜひ会いたいです。",
        "お会いできませんとは思えません。ぜひ会いたいです。",
        "お会いできませんと感じません。ぜひ会いたいです。",
        "お会いできませんということはありません。ぜひ会いたいです。",
        "お会いできませんが、来週なら会えます。",
        "お会いするのは難しいですが、来週に会えます。",
        "お会いするのは難しいけど、来週なら大丈夫です。",
        "お会いするのは厳しいですが、来週なら会えます。",
        "会うのは無理ですが来週なら会えます。",
        "会うのは無理だけど来週なら大丈夫です。",
        "友達はお会いするのは難しいと言っていましたが、私はぜひ一緒に行きたいです。",
        "友達が会いたくないと言っていましたが、私はぜひ会いたいです。",
        "友達はお会いしたくありませんと言っていましたが、私はぜひ会いたいです。",
        "友人から会いたくないと言われましたが、私はぜひ会いたいです。",
        "友人から、会うつもりはないと聞きましたが、私はぜひ会いたいです。",
        "「会うのは無理」と言われましたが、私はぜひ会いたいです。",
        "「会うのは無理」と友達に言われましたが、私はぜひ会いたいです。",
        "「会うのは無理」と友達から言われましたが、私はぜひ会いたいです。",
        "『会うのは無理』と元彼に言われましたが、私はぜひ会いたいです。",
        "『会うのは無理』と別の人に言われましたが、私はぜひ会いたいです。",
        "『会うのは無理』と彼氏に言われましたが、私はぜひ会いたいです。",
    ],
)
def test_formal_refusal_match_preserves_negation_counterproposal_and_attribution(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手本人から会う意思が示されています。",
            "evidence": [statement],
            "invite_example": "よかったら来週カフェで会いませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action != "stop"


@pytest.mark.parametrize(
    "statement",
    [
        "今は会うつもりはありませんが、いつかは会ってみたいです。",
        "今回はお断りしますが、また今度はぜひ会いたいです。",
        "会うのは今は難しいですが、来月なら会いたいです。",
    ],
)
def test_vague_future_interest_does_not_reopen_current_decline(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手は将来会いたいと伝えています。",
            "evidence": [statement],
            "invite_example": "来月カフェで会いませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action != "invite"
    assert result.invite_example is None


@pytest.mark.parametrize(
    "statement",
    [
        "会いたくないです。でも来週なら会えます。",
        "お会いしたくありませんが、来週なら会えます。",
        "お会いするつもりはありません。でも来週なら会えます。",
        "会うことは控えたいですが、今は会うつもりはありません。来週なら会えます。",
        "お会いするのは控えさせていただきます。来週なら会えます。",
        "お会いしたくはありませんが、来週なら会えます。",
        "今は会いたいです。でも、やはり会うのは無理です。",
    ],
)
def test_hard_meeting_refusal_is_not_reopened_by_later_availability(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "来週なら会えると伝えています。",
            "evidence": [statement],
            "invite_example": "来週カフェで会いませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")
    violations = validate_candidate_replies(
        ["来週カフェで会いませんか？"],
        1,
        counterpart_message=statement,
        strategy_mode="tapple",
        tapple_action="invite",
    )

    assert result is not None
    assert result.action in {"stop", "wait"}
    assert result.invite_example is None
    assert any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "statement",
    [
        "会うことは考えられません。",
        "会う気分ではありません。",
        "今は会う気分ではないです。",
        "会う気分じゃないです。",
        "会うのはできれば避けたいです。",
    ],
)
def test_colloquial_firm_meeting_refusals_stop_invitation_strategy(statement):
    result = _parse_tapple_strategy(
        _raw_strategy(
            {
                "action": "continue",
                "rationale": "会話を続けます。",
                "evidence": [statement],
                "invite_example": None,
            }
        ),
        f"相手: {statement}",
    )

    assert result is not None
    assert result.action == "stop"


def test_colloquial_meeting_reluctance_waits_without_being_promoted_to_refusal():
    statement = "カフェには行きたいですが、会うのは気が進みません。"
    result = _parse_tapple_strategy(
        _raw_strategy(
            {
                "action": "invite",
                "rationale": "相手の関心に合わせて誘います。",
                "evidence": [statement],
                "invite_example": "駅前のカフェで会いませんか？",
            }
        ),
        f"相手: {statement}",
    )

    assert result is not None
    assert result.action == "wait"


def test_ellipsis_after_direct_meeting_mention_is_treated_as_hesitation():
    statement = "カフェは楽しみですが、直接会うのはちょっと…"
    result = _parse_tapple_strategy(
        _raw_strategy(
            {
                "action": "invite",
                "rationale": "相手の関心に合わせて誘います。",
                "evidence": [statement],
                "invite_example": "駅前のカフェで会いませんか？",
            }
        ),
        f"相手: {statement}",
    )

    assert result is not None
    assert result.action == "wait"


@pytest.mark.parametrize(
    "statement",
    [
        "今は会いたいです。前は会うのは無理だと思っていました。でも今も会うのは無理です。",
        "今は会いたいです。前は会うのは無理だと思っていましたが、今は会いたくありません。",
    ],
)
def test_historical_decline_does_not_hide_later_current_refusal(statement):
    declines = _unqualified_tapple_decline_matches(statement)

    assert declines
    assert declines[-1].group(0) in {"会うのは無理", "会いたくありません"}


@pytest.mark.parametrize(
    "statement",
    [
        "友達が「今は会いたいです。前は会うのは無理だと思っていました」と言っていました。",
        "彼女は「今は会いたいです。前は会うのは難しいと思っていました」と話していた。",
    ],
)
def test_reported_third_party_quote_does_not_create_current_self_intent(statement):
    assert _unqualified_tapple_decline_matches(statement) == []


@pytest.mark.parametrize(
    "statement",
    [
        "今はあなたと会いたいです。前は会うのは無理だと思っていました。",
        "前は会うのは無理だと思っていました。今はあなたと会いたいです。",
    ],
)
def test_clear_current_desire_is_not_hedged_by_historical_difficulty(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "相手本人が今は会いたいと伝えています。",
            "evidence": [statement],
            "invite_example": "よかったら人通りのあるカフェで会いませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "invite"


def test_third_party_availability_does_not_reopen_contact_meeting_difficulty():
    statement = "お会いするのは難しいですが、友達は来週なら会えます。"
    violations = validate_candidate_replies(
        ["よかったら来週カフェで会いませんか？"],
        1,
        counterpart_message=statement,
        strategy_mode="tapple",
        tapple_action="invite",
    )

    assert any("誘い" in violation for violation in violations)


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


def test_first_meeting_wary_interest_blocks_invite_strategy_and_reply():
    statement = "ぜひ一緒に行きたいですが、初対面なので少し警戒しています。"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": ["ぜひ一緒に行きたい"],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")
    violations = validate_candidate_replies(
        ["ぜひ行きましょう。駅前のカフェでお茶しませんか？"],
        1,
        counterpart_message=statement,
        strategy_mode="tapple",
        tapple_action="invite",
    )

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None
    assert any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "statement",
    [
        "一緒に行きたいですが、初めて会う人なので警戒しています。",
        "ぜひ一緒に行きたいです。会ったことがない人には警戒してしまいます。",
        "一緒に行きたいですが、初対面なので警戒しないといけないと思います。",
        "ぜひ一緒に行きたいです。初対面なので警戒しないと危ないと思います。",
        "ぜひ一緒に行きたいですが、初対面は不安です。",
        "ぜひ一緒に行きたいですが、初対面なので不安です。",
        "ぜひ一緒に行きたいですが、初対面は怖いです。",
        "ぜひ一緒に行きたいです。会うのはまだ緊張します。",
        "ぜひ一緒に行きたいです。会うのは嫌ではないけど緊張します。",
        "ぜひ一緒に行きたいです。直接会うのは緊張します。",
        "ぜひ一緒に行きたいです。会うのはちょっとハードルが高いです。",
        "ぜひ一緒に行きたいです。まだ会う勇気が出ません。",
        "ぜひ一緒に行きたいです。まだ会う勇気がないです。",
        "ぜひ一緒に行きたいです。会うのが不安です、天気も気になります。",
        "ぜひ一緒に行きたいです。少し不安です。",
        "ぜひ会いたいです。緊張します。",
        "ぜひ一緒に行きたいです。会いたいけど、まだ少し緊張します。",
        "ぜひ一緒に行きたいですが、直接会うのは勇気がいります。",
        "ぜひ一緒に行きたいです。初対面なので少し勇気がいります。",
        "ぜひ一緒に行きたいです。ちょっと怖いけど、昼間のカフェなら大丈夫です。",
        "会ったことがない人とは慎重に進めたいです。ぜひ一緒に行きたいです。",
        "初対面なので慎重にしたいです。ぜひ一緒に行きたいです。",
        "初対面なので警戒心がないと危ないです。ぜひ一緒に行きたいです。",
        "ぜひ一緒に行きたいです。初対面だし、警戒しないとですよね。",
        "警戒しないとね。ぜひ一緒に行きたいです。",
    ],
)
def test_first_meeting_wary_variants_block_invite(statement):
    evidence = next(
        phrase
        for phrase in ("ぜひ一緒に行きたい", "一緒に行きたい", "ぜひ会いたい", "会いたい")
        if phrase in statement
    )
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": [evidence],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")
    violations = validate_candidate_replies(
        ["ぜひ行きましょう。駅前のカフェでお茶しませんか？"],
        1,
        counterpart_message=statement,
        strategy_mode="tapple",
        tapple_action="invite",
    )

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None
    assert any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "statement",
    [
        "初対面だけど警戒していません。ぜひ一緒に行きたいです。",
        "初対面でも警戒しません。ぜひ一緒に行きたいです。",
        "初対面でも警戒しないタイプです。ぜひ一緒に行きたいです。",
        "初対面では不安ではありません。ぜひ一緒に行きたいです。",
        "初対面でも怖くありません。ぜひ一緒に行きたいです。",
        "初対面なので警戒しなくて大丈夫です。ぜひ一緒に行きたいです。",
        "初対面だけど警戒しなくて大丈夫です。ぜひ一緒に行きたいです。",
        "初対面なので警戒しなくていいです。ぜひ一緒に行きたいです。",
        "初対面なので警戒する必要はありません。ぜひ一緒に行きたいです。",
        "初対面なので警戒することはありません。ぜひ一緒に行きたいです。",
        "初対面でも怖いとは思いません。ぜひ一緒に行きたいです。",
    ],
)
def test_denied_first_meeting_wary_concern_does_not_block_invite(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": ["ぜひ一緒に行きたい"],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "invite"
    assert result.invite_example is not None


def test_explicit_denial_of_meeting_resistance_does_not_block_invite():
    for statement in (
        "初対面に抵抗はありません。ぜひ一緒に行きたいです。",
        "会うことに抵抗はないです。ぜひ一緒に行きたいです。",
        "会うのに抵抗感はありません。ぜひ一緒に行きたいです。",
        "会うことに怖さはありません。ぜひ一緒に行きたいです。",
        "会うことに抵抗を感じていません。ぜひ一緒に行きたいです。",
        "初対面でも不安に感じません。ぜひ一緒に行きたいです。",
        "会うのは緊張しません。ぜひ一緒に行きたいです。",
        "直接会うのは緊張しないです。ぜひ一緒に行きたいです。",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "一緒に行きたいという意思があります。",
                "evidence": ["ぜひ一緒に行きたい"],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )

        result = _parse_tapple_strategy(raw, f"相手: {statement}")

        assert result is not None, statement
        assert result.action == "invite", statement
        assert result.invite_example is not None, statement


def test_unrelated_fear_does_not_block_invite():
    for statement in (
        "ぜひ一緒に行きたいです。怖い映画が好きです。",
        "ぜひ一緒に行きたいです。会うのは楽しみです。怖い映画も好きです。",
        "ぜひ一緒に行きたいです。初対面でも怖い映画が好きです。",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "一緒に行きたいという意思があります。",
                "evidence": ["ぜひ一緒に行きたい"],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )

        result = _parse_tapple_strategy(raw, f"相手: {statement}")
        violations = validate_candidate_replies(
            ["ぜひ行きましょう。駅前のカフェでお茶しませんか？"],
            1,
            counterpart_message=statement,
            strategy_mode="tapple",
            tapple_action="invite",
        )

        assert result is not None, statement
        assert result.action == "invite", statement
        assert result.invite_example is not None, statement
        assert not any("誘い" in violation for violation in violations), statement


def test_unrelated_concerns_do_not_block_invite():
    for statement in (
        "会うのは楽しみですが、母の体調が心配です。ぜひ一緒に行きたいです。",
        "会うのは楽しみですが今日の体調が心配です。ぜひ一緒に行きたいです。",
        "会うのは楽しみですが母の体調が心配です。ぜひ一緒に行きたいです。",
        "会うのは楽しみだけど、明日の予定が不安です。ぜひ一緒に行きたいです。",
        "会うのは楽しみだけど明日の予定が不安です。ぜひ一緒に行きたいです。",
        "会うのは楽しみですが、仕事の面接で緊張しています。ぜひ一緒に行きたいです。",
        "会うのは楽しみですが、試験が不安です。ぜひ一緒に行きたいです。",
        "会うのは楽しみですが、怖い映画が苦手です。ぜひ一緒に行きたいです。",
        "会う日は雨が心配です。ぜひ一緒に行きたいです。",
        "デートの日程が心配です。ぜひ一緒に行きたいです。",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "一緒に行きたいという意思があります。",
                "evidence": ["ぜひ一緒に行きたい"],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )

        result = _parse_tapple_strategy(raw, f"相手: {statement}")

        assert result is not None, statement
        assert result.action == "invite", statement
        assert result.invite_example is not None, statement


def test_work_or_company_context_does_not_hide_date_safety_concern():
    for concern in (
        "仕事帰りに会うのは安全面で不安です。",
        "仕事の後に会うのは安全面が心配です。",
        "会社の近くで会うのは安全か分からなくて不安です。",
    ):
        statement = f"ぜひ一緒に行きたいですが、{concern}"
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "一緒に行きたいという意思があります。",
                "evidence": ["ぜひ一緒に行きたい"],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )

        result = _parse_tapple_strategy(raw, f"相手: {statement}")

        assert result is not None, statement
        assert result.action == "wait", statement
        assert result.invite_example is None, statement


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


def test_unrelated_work_contrast_does_not_block_explicit_invite():
    statement = "今日は仕事が大変だけど、ぜひ一緒に行きたいです。"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": ["ぜひ一緒に行きたいです"],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "invite"
    assert result.invite_example is not None


def test_unrelated_work_problem_does_not_block_explicit_invite():
    statement = "仕事のことで悩んでいましたが、ぜひ一緒に行きたいです。"
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": ["ぜひ一緒に行きたいです"],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "invite"
    assert result.invite_example is not None


def test_explicit_hesitation_before_positive_interest_blocks_invite():
    for statement in (
        "まだ迷っていますが、ぜひ一緒に行きたいです。",
        "正直まだ迷っていますが、ぜひ一緒に行きたいです。",
        "私はまだ迷っていますが、ぜひ一緒に行きたいです。",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "一緒に行きたいという意思があります。",
                "evidence": ["ぜひ一緒に行きたいです"],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )

        result = _parse_tapple_strategy(raw, f"相手: {statement}")

        assert result is not None, statement
        assert result.action == "wait", statement
        assert result.invite_example is None, statement


def test_hesitation_in_separate_sentence_after_positive_interest_blocks_invite():
    for statement in (
        "ぜひ一緒に行きたいです。少し迷っています。",
        "ぜひ一緒に行きたいです。最近いろいろ考えていました。まだ迷っています。",
        "ぜひ一緒に行きたいです。日程のことを確認しました。まだ迷っています。",
        "ぜひ一緒に行きたいです。会う日で迷っています。少し迷っています。",
        "ぜひ一緒に行きたいです。日程を確認しました。仕事も調整しました。場所も考えました。まだ迷っています。",
        "ぜひ一緒に行きたいです。家族にも相談したけど、まだ迷っています。",
        "ぜひ一緒に行きたいです。日程の確認は済んだけど、まだ迷っています。",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "一緒に行きたいという意思があります。",
                "evidence": ["ぜひ一緒に行きたいです"],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )

        result = _parse_tapple_strategy(raw, f"相手: {statement}")

        assert result is not None, statement
        assert result.action == "wait", statement
        assert result.invite_example is None, statement


@pytest.mark.parametrize(
    "statement",
    [
        "ぜひ一緒に行きたいです。仕事は忙しいけど、会うのは迷っています。",
        "ぜひ一緒に行きたいです。場所はいいけど、会うのは少し迷っています。",
        "ぜひ一緒に行きたいです。会いたいけど少し迷う。",
        "ぜひ一緒に行きたいです。会うかどうか迷う。",
        "ぜひ一緒に行きたいです。会うのは少し悩んでる。",
    ],
)
def test_explicit_meeting_hesitation_survives_unrelated_or_logistics_context(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": ["ぜひ一緒に行きたいです"],
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
        "ぜひ一緒に行きたいです。会いたいけど少し迷う。",
        "ぜひ一緒に行きたいです。会いたいけど、少し迷う。",
        "ぜひ一緒に行きたいです。会うかどうか迷う。",
        "ぜひ一緒に行きたいです。会うのは少し悩んでる。",
    ],
)
def test_casual_meeting_hesitation_blocks_invite(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "会うことへの迷いが残っています。",
            "evidence": ["ぜひ一緒に行きたいです"],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


def test_work_anxiety_does_not_count_as_meeting_safety_concern():
    for statement in (
        "仕事の不安はないとは言えませんが、ぜひ一緒に行きたいです。",
        "仕事については不安がないとは言えませんが、ぜひ一緒に行きたいです。",
        "仕事についての不安は消えないけど、ぜひ一緒に行きたいです。",
        "仕事の安全について不安ですが、会うことは楽しみです。ぜひ一緒に行きたいです。",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "一緒に行きたいという意思があります。",
                "evidence": ["ぜひ一緒に行きたいです"],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )

        result = _parse_tapple_strategy(raw, f"相手: {statement}")

        assert result is not None, statement
        assert result.action == "invite", statement
        assert result.invite_example is not None, statement


def test_unrelated_work_hesitation_does_not_block_invite():
    for statement in (
        "ぜひ一緒に行きたいです。仕事のことで悩んでいないとは言えません。",
        "まだ仕事のことで迷っていますが、ぜひ一緒に行きたいです。",
        "ぜひ一緒に行きたいです。資格を取るか迷っています。",
        "ぜひ一緒に行きたいです。転職先をどこにするか迷っています。",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "一緒に行きたいという意思があります。",
                "evidence": ["ぜひ一緒に行きたいです"],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )

        result = _parse_tapple_strategy(raw, f"相手: {statement}")

        assert result is not None, statement
        assert result.action == "invite", statement
        assert result.invite_example is not None, statement


def test_work_context_does_not_hide_explicit_meeting_hesitation():
    for statement in (
        "仕事もあるので会うのは少し迷っています。ぜひ一緒に行きたいです。",
        "仕事が忙しいので会うのはまだ迷っています。ぜひ一緒に行きたいです。",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "一緒に行きたいという意思があります。",
                "evidence": ["ぜひ一緒に行きたいです"],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )

        result = _parse_tapple_strategy(raw, f"相手: {statement}")

        assert result is not None, statement
        assert result.action == "wait", statement


def test_thinking_about_meeting_logistics_is_not_hesitation():
    for statement in (
        "来週会う日程を考えています。ぜひ一緒に行きたいです。",
        "来週会う日程で迷っています。ぜひ一緒に行きたいです。",
        "会う日の候補で迷っています。ぜひ一緒に行きたいです。",
        "会う日がいつか迷っています。ぜひ一緒に行きたいです。",
        "ぜひ一緒に行きたいです。会う日で迷っています。",
        "ぜひ一緒に行きたいです。会う日取りで迷っています。",
        "ぜひ一緒に行きたいです。会う曜日で迷っています。",
        "ぜひ一緒に行きたいです。どこで会うか迷っています。",
        "ぜひ一緒に行きたいです。何を着るか迷っています。",
        "ぜひ一緒に行きたいです。何を話すか悩んでいます。",
        "ぜひ一緒に行きたいです。家族のことで悩んでいました。",
        "会う日をどちらにするか悩んでいます。ぜひ一緒に行きたいです。",
        "一緒に行きたいけど、会う日の候補で迷っています。",
        "会う日の候補はいいけど、少し迷っています。ぜひ一緒に行きたいです。",
        "会うのが楽しみですが、何を着ていくか迷っています。ぜひ一緒に行きたいです。",
        "会う前に何を話すか悩んでいます。ぜひ一緒に行きたいです。",
    ):
        raw = _raw_strategy(
            {
                "action": "invite",
                "rationale": "一緒に行きたいという意思があります。",
                "evidence": ["一緒に行きたい"],
                "invite_example": "人の多いカフェでお茶しませんか？",
            }
        )

        result = _parse_tapple_strategy(raw, f"相手: {statement}")

        assert result is not None, statement
        assert result.action == "invite", statement
        assert result.invite_example is not None, statement


def test_schedule_choice_does_not_block_confirming_an_accepted_meeting():
    violations = validate_candidate_replies(
        ["では金曜にしましょう。"],
        1,
        counterpart_message="会う日をどちらにするか悩んでいます。ぜひ一緒に行きたいです。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "statement",
    [
        "ぜひ一緒に行きたいですが、もう少しメッセージで話してから会いたいです。",
        "ぜひ一緒に行きたいけど、もう少しやり取りしてから会いたいです。",
        "ぜひ一緒に行きたいですが、会うのはまだ早いと思います。",
    ],
)
def test_invite_gate_respects_request_to_delay_meeting(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": ["ぜひ一緒に行きたい"],
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
        "ぜひ一緒に行きたいです。会いたいけど、少し考える時間がほしいです。",
        "ぜひ一緒に行きたいです。決める前に時間をください。",
        "ぜひ一緒に行きたいです。少し考える時間をもらってもいいですか。",
        "ぜひ一緒に行きたいです。少し考えてから返事したいです。",
        "ぜひ一緒に行きたいです。返事をする時間がほしいです。",
        "ぜひ一緒に行きたいです。返事はもう少し考えてからしたいです。",
        "ぜひ一緒に行きたいです。返事をもう少し待ってもらえますか。",
        "ぜひ一緒に行きたいです。少し返事を待ってください。",
        "ぜひ一緒に行きたいです。少し考えたいです。",
        "ぜひ一緒に行きたいです。返事は明日まで待っていただけますか。",
        "ぜひ一緒に行きたいです。返事は明日します。",
        "ぜひ一緒に行きたいです。明日返事します。",
        "ぜひ一緒に行きたいです。明日までには返事するね。",
        "ぜひ一緒に行きたいです。あとで返事します。",
        "ぜひ一緒に行きたいです。明日まで待ってください。",
    ],
)
def test_invite_gate_respects_request_for_time_to_decide(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": ["ぜひ一緒に行きたいです"],
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
        "ぜひ一緒に行きたいです。会うかどうか迷っていません。",
        "ぜひ一緒に行きたいです。会うかどうかは迷いません。",
        "ぜひ一緒に行きたいです。会うこと自体は迷っていませんが、日程は迷っています。",
        "ぜひ一緒に行きたいです。会うかどうか迷っているとは言えません。",
        "ぜひ一緒に行きたいです。家族のことで悩んでいますが、会うこと自体は迷っていません。",
    ],
)
def test_explicit_denial_of_meeting_hesitation_does_not_block_invite(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": ["ぜひ一緒に行きたいです"],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "invite"
    assert result.invite_example is not None


@pytest.mark.parametrize(
    "statement",
    [
        "ぜひ一緒に行きたいです。会うかどうか迷っていないとは言えません。",
        "ぜひ一緒に行きたいです。会うかどうか迷っていないかもしれません。",
        "ぜひ一緒に行きたいです。会うかどうか迷っていないわけではありません。",
    ],
)
def test_ambiguous_denial_of_meeting_hesitation_still_blocks_invite(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": ["ぜひ一緒に行きたいです"],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


@pytest.mark.parametrize(
    "statement,evidence",
    [
        ("ぜひ一緒に行きたいですが、身元が分からず不安です。", "一緒に行きたい"),
        ("ぜひ一緒に行きたいですが、相手のことをよく知らなくて不安です。", "一緒に行きたい"),
        ("ぜひ一緒に行きたいですが、安全かどうか分からないです。", "一緒に行きたい"),
        ("ぜひ一緒に行きたいですが、安全か分からず迷っています。", "一緒に行きたい"),
        ("ぜひ一緒に行きたいですが、どんな方か分からず不安です。", "一緒に行きたい"),
        ("ぜひ一緒に行きたいですが、どんな人か知らなくて不安です。", "一緒に行きたい"),
        ("ぜひ一緒に行きたいですが、会ったことがなくて不安です。", "一緒に行きたい"),
        ("会いたいけど少し怖いです。", "会いたい"),
        ("会いたいけど少し不安です。", "会いたい"),
        ("ぜひ一緒に行きたいですが、あなたを信じていいか不安です。", "一緒に行きたい"),
        ("あなたを信じていいか分からないですが、ぜひ一緒に行きたいです。", "一緒に行きたい"),
        ("ぜひ一緒に行きたいですが、安全だと信じきれないです。", "一緒に行きたい"),
        ("ぜひ一緒に行きたいですが、会うのは少し抵抗があります。", "一緒に行きたい"),
        ("ぜひ一緒に行きたいですが、会うことにためらいがあります。", "一緒に行きたい"),
    ],
)
def test_invite_gate_blocks_safety_and_familiarity_concerns(statement, evidence):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": [evidence],
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
        counterpart_message="ぜひ一緒に行きたいです！明日の天気が心配ですが、会えるのを楽しみにしています。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_weather_worry_after_meeting_anticipation_does_not_block_scheduling():
    violations = validate_candidate_replies(
        ["ぜひ！日曜はどうですか？"],
        1,
        counterpart_message="ぜひ一緒に行きたいです。会うのを楽しみにしていますが、天気が心配です。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_explicit_hesitation_before_positive_interest_blocks_scheduling():
    for counterpart_message in (
        "まだ迷っていますが、ぜひ一緒に行きたいです。",
        "正直まだ迷っていますが、ぜひ一緒に行きたいです。",
        "私はまだ迷っていますが、ぜひ一緒に行きたいです。",
    ):
        violations = validate_candidate_replies(
            ["ぜひ！日曜はどうですか？"],
            1,
            counterpart_message=counterpart_message,
            strategy_mode="tapple",
            tapple_action="continue",
        )

        assert any("誘い" in violation for violation in violations), counterpart_message


def test_request_to_talk_more_before_meeting_blocks_date_scheduling():
    violations = validate_candidate_replies(
        ["ぜひ！日曜はどうですか？"],
        1,
        counterpart_message="ぜひ一緒に行きたいですが、もう少しメッセージで話してから会いたいです。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("誘い" in violation for violation in violations)


def test_work_anxiety_does_not_block_accepted_date_scheduling():
    for counterpart_message in (
        "仕事の不安はないとは言えませんが、ぜひ一緒に行きたいです。",
        "仕事については不安がないとは言えませんが、ぜひ一緒に行きたいです。",
        "仕事についての不安は消えないけど、ぜひ一緒に行きたいです。",
        "仕事の安全について不安ですが、会うことは楽しみです。ぜひ一緒に行きたいです。",
    ):
        violations = validate_candidate_replies(
            ["日曜はどうですか？"],
            1,
            counterpart_message=counterpart_message,
            strategy_mode="tapple",
            tapple_action="continue",
        )

        assert not any("誘い" in violation for violation in violations), counterpart_message


def test_work_or_company_context_does_not_allow_unsafe_date_scheduling():
    for concern in (
        "仕事帰りに会うのは安全面で不安です。",
        "仕事の後に会うのは安全面が心配です。",
        "会社の近くで会うのは安全か分からなくて不安です。",
    ):
        counterpart_message = f"ぜひ一緒に行きたいですが、{concern}"
        violations = validate_candidate_replies(
            ["日曜はどうですか？"],
            1,
            counterpart_message=counterpart_message,
            strategy_mode="tapple",
            tapple_action="continue",
        )

        assert any("誘い" in violation for violation in violations), counterpart_message


@pytest.mark.parametrize(
    "counterpart_message",
    [
        "ぜひ一緒に行きたいです。会いたいけど、少し考える時間がほしいです。",
        "ぜひ一緒に行きたいです。決める前に時間をください。",
        "ぜひ一緒に行きたいです。少し考える時間をもらってもいいですか。",
        "ぜひ一緒に行きたいです。少し考えてから返事したいです。",
        "ぜひ一緒に行きたいです。返事をする時間がほしいです。",
        "ぜひ一緒に行きたいです。返事はもう少し考えてからしたいです。",
        "ぜひ一緒に行きたいです。返事をもう少し待ってもらえますか。",
        "ぜひ一緒に行きたいです。少し返事を待ってください。",
        "ぜひ一緒に行きたいです。少し考えたいです。",
        "ぜひ一緒に行きたいです。返事は明日まで待っていただけますか。",
        "ぜひ一緒に行きたいです。返事は明日します。",
        "ぜひ一緒に行きたいです。明日返事します。",
        "ぜひ一緒に行きたいです。明日までには返事するね。",
        "ぜひ一緒に行きたいです。あとで返事します。",
        "ぜひ一緒に行きたいです。明日まで待ってください。",
    ],
)
def test_request_for_time_to_decide_blocks_date_scheduling(counterpart_message):
    violations = validate_candidate_replies(
        ["ぜひ！日曜はどうですか？"],
        1,
        counterpart_message=counterpart_message,
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "counterpart_message",
    [
        "ぜひ一緒に行きたいです。会うかどうか迷っていません。",
        "ぜひ一緒に行きたいです。会うかどうかは迷いません。",
        "ぜひ一緒に行きたいです。会うこと自体は迷っていませんが、日程は迷っています。",
        "ぜひ一緒に行きたいです。会うかどうか迷っているとは言えません。",
        "ぜひ一緒に行きたいです。家族のことで悩んでいますが、会うこと自体は迷っていません。",
    ],
)
def test_explicit_denial_of_meeting_hesitation_allows_date_scheduling(
    counterpart_message,
):
    violations = validate_candidate_replies(
        ["ぜひ！日曜はどうですか？"],
        1,
        counterpart_message=counterpart_message,
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "counterpart_message",
    [
        "ぜひ一緒に行きたいです。会うかどうか迷っていないとは言えません。",
        "ぜひ一緒に行きたいです。会うかどうか迷っていないかもしれません。",
        "ぜひ一緒に行きたいです。会うかどうか迷っていないわけではありません。",
    ],
)
def test_ambiguous_denial_of_meeting_hesitation_blocks_date_scheduling(
    counterpart_message,
):
    violations = validate_candidate_replies(
        ["ぜひ！日曜はどうですか？"],
        1,
        counterpart_message=counterpart_message,
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("誘い" in violation for violation in violations)


def test_unrelated_work_contrast_does_not_block_accepted_date_scheduling():
    violations = validate_candidate_replies(
        ["ぜひ！日曜はどうですか？"],
        1,
        counterpart_message="今日は仕事が大変だけど、ぜひ一緒に行きたいです。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_unrelated_work_problem_does_not_block_date_scheduling():
    violations = validate_candidate_replies(
        ["ぜひ！日曜はどうですか？"],
        1,
        counterpart_message="仕事のことで悩んでいましたが、ぜひ一緒に行きたいです。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_unrelated_workload_worry_does_not_block_date_scheduling():
    violations = validate_candidate_replies(
        ["ぜひ！日曜はどうですか？"],
        1,
        counterpart_message="ぜひ一緒に行きたいです。仕事が忙しくて悩んでいます。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_qualified_or_leading_work_hesitation_does_not_block_date_scheduling():
    for counterpart_message in (
        "ぜひ一緒に行きたいです。仕事のことで悩んでいないとは言えません。",
        "まだ仕事のことで迷っていますが、ぜひ一緒に行きたいです。",
        "ぜひ一緒に行きたいです。資格を取るか迷っています。",
        "ぜひ一緒に行きたいです。転職先をどこにするか迷っています。",
    ):
        violations = validate_candidate_replies(
            ["日曜はどうですか？"],
            1,
            counterpart_message=counterpart_message,
            strategy_mode="tapple",
            tapple_action="continue",
        )

        assert not any("誘い" in violation for violation in violations), counterpart_message


def test_work_context_does_not_hide_meeting_hesitation_from_scheduler():
    for counterpart_message in (
        "仕事もあるので会うのは少し迷っています。ぜひ一緒に行きたいです。",
        "仕事が忙しいので会うのはまだ迷っています。ぜひ一緒に行きたいです。",
    ):
        violations = validate_candidate_replies(
            ["日曜はどうですか？"],
            1,
            counterpart_message=counterpart_message,
            strategy_mode="tapple",
            tapple_action="continue",
        )

        assert any("誘い" in violation for violation in violations), counterpart_message


@pytest.mark.parametrize(
    "counterpart_message",
    [
        "ぜひ一緒に行きたいけど、仕事のことで悩んでいます。",
        "一緒に行きたいけど、会う日の候補で迷っています。",
        "ぜひ一緒に行きたいです。会う日で迷っています。",
        "ぜひ一緒に行きたいです。会う日取りで迷っています。",
        "ぜひ一緒に行きたいです。会う曜日で迷っています。",
        "ぜひ一緒に行きたいです。どこで会うか迷っています。",
        "ぜひ一緒に行きたいです。何を着るか迷っています。",
        "ぜひ一緒に行きたいです。何を話すか悩んでいます。",
        "ぜひ一緒に行きたいです。家族のことで悩んでいました。",
        "会う日の候補はいいけど、少し迷っています。ぜひ一緒に行きたいです。",
        "会うのが楽しみですが、何を着ていくか迷っています。ぜひ一緒に行きたいです。",
        "会う前に何を話すか悩んでいます。ぜひ一緒に行きたいです。",
    ],
)
def test_post_acceptance_unrelated_or_date_choice_concern_allows_scheduling(
    counterpart_message,
):
    violations = validate_candidate_replies(
        ["日曜はどうですか？"],
        1,
        counterpart_message=counterpart_message,
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_positive_anticipation_with_mada_does_not_block_date_scheduling():
    violations = validate_candidate_replies(
        ["日曜はどうですか？"],
        1,
        counterpart_message="ぜひ一緒に行きたいです。まだ行ったことないお店なので楽しみです。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert not any("誘い" in violation for violation in violations)


def test_hesitation_after_positive_interest_still_blocks_date_scheduling():
    violations = validate_candidate_replies(
        ["ぜひ！日曜はどうですか？"],
        1,
        counterpart_message="ぜひ一緒に行きたいですが、少し迷っています。",
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("誘い" in violation for violation in violations)


def test_hesitation_in_separate_sentence_after_acceptance_blocks_date_scheduling():
    for counterpart_message in (
        "ぜひ一緒に行きたいです。少し迷っています。",
        "ぜひ一緒に行きたいです。最近いろいろ考えていました。まだ迷っています。",
        "ぜひ一緒に行きたいです。日程のことを確認しました。まだ迷っています。",
        "ぜひ一緒に行きたいです。会う日で迷っています。少し迷っています。",
        "ぜひ一緒に行きたいです。日程を確認しました。仕事も調整しました。場所も考えました。まだ迷っています。",
        "ぜひ一緒に行きたいです。家族にも相談したけど、まだ迷っています。",
        "ぜひ一緒に行きたいです。日程の確認は済んだけど、まだ迷っています。",
    ):
        violations = validate_candidate_replies(
            ["ぜひ！日曜はどうですか？"],
            1,
            counterpart_message=counterpart_message,
            strategy_mode="tapple",
            tapple_action="continue",
        )

        assert any("誘い" in violation for violation in violations), counterpart_message


@pytest.mark.parametrize(
    "counterpart_message",
    [
        "ぜひ一緒に行きたいです。仕事は忙しいけど、会うのは迷っています。",
        "ぜひ一緒に行きたいです。場所はいいけど、会うのは少し迷っています。",
    ],
)
def test_explicit_meeting_hesitation_blocks_scheduling_after_logistics_context(
    counterpart_message,
):
    violations = validate_candidate_replies(
        ["ぜひ！日曜はどうですか？"],
        1,
        counterpart_message=counterpart_message,
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("誘い" in violation for violation in violations)


@pytest.mark.parametrize(
    "statement",
    [
        "会うのは不安ではありません。ぜひ一緒に行きたいです。",
        "会うのは怖くないです。ぜひ一緒に行きたいです。",
        "安全面は心配していません。ぜひ一緒に行きたいです。",
        "会うのは心配していないです。ぜひ一緒に行きたいです。",
        "安全面は心配していないです。ぜひ一緒に行きたいです。",
        "安全性に不安はないです。ぜひ一緒に行きたいです。",
        "安全面の不安は感じていません。ぜひ一緒に行きたいです。",
        "明日の予定が不安ではないとは言えません。ぜひ一緒に行きたいです。",
    ],
)
def test_denied_meeting_safety_concern_does_not_block_explicit_invite(statement):
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


@pytest.mark.parametrize(
    "statement",
    [
        "会うのは怖くないとは言えません。ぜひ一緒に行きたいです。",
        "不安ではないとは言えないですが、ぜひ一緒に行きたいです。",
        "会うのが怖くないとは言い切れませんが、ぜひ一緒に行きたいです。",
        "不安ではないと言い切れないですが、ぜひ一緒に行きたいです。",
        "怖くないかもしれませんが、ぜひ一緒に行きたいです。",
        "不安じゃないとは思えませんが、ぜひ一緒に行きたいです。",
        "初対面なので、警戒しなくても大丈夫とは思えません。ぜひ一緒に行きたいです。",
        "初対面だから警戒しなくていいとは思わないです。ぜひ一緒に行きたいです。",
        "初対面なので、警戒する必要はないとは言えません。ぜひ一緒に行きたいです。",
    ],
)
def test_qualified_denial_does_not_clear_safety_concern(statement):
    raw = _raw_strategy(
        {
            "action": "invite",
            "rationale": "一緒に行きたいという意思があります。",
            "evidence": ["ぜひ一緒に行きたいです"],
            "invite_example": "人の多いカフェでお茶しませんか？",
        }
    )

    result = _parse_tapple_strategy(raw, f"相手: {statement}")

    assert result is not None
    assert result.action == "wait"
    assert result.invite_example is None


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


@pytest.mark.parametrize(
    "public_hotel_example",
    [
        "ホテルのカフェでお茶しませんか？",
        "ホテルのロビーでお話ししませんか？",
    ],
)
def test_tapple_invitation_example_allows_public_hotel_venues(
    public_hotel_example,
):
    conversation = "相手: 今度一緒に行きたいです"
    result = _parse_tapple_strategy(
        _raw_strategy(
            {
                "action": "invite",
                "rationale": "相手に明確な参加意思があります。",
                "evidence": ["今度一緒に行きたいです"],
                "invite_example": public_hotel_example,
            }
        ),
        conversation,
    )

    assert result is not None
    assert result.action == "invite"
    assert result.invite_example == public_hotel_example


@pytest.mark.parametrize(
    "coercive_example",
    [
        "駅前のカフェで絶対来てね！",
        "駅前のカフェなら来るよね？",
        "断るなんてないよね、駅前のカフェで会おう！",
        "駅前のカフェに行くって約束したよね？考え直して！",
    ],
)
def test_tapple_invitation_example_rejects_pressure_and_assumed_consent(
    coercive_example,
):
    conversation = "相手: 今度一緒に行きたいです"
    result = _parse_tapple_strategy(
        _raw_strategy(
            {
                "action": "invite",
                "rationale": "相手に明確な参加意思があります。",
                "evidence": ["今度一緒に行きたいです"],
                "invite_example": coercive_example,
            }
        ),
        conversation,
    )

    assert result is not None
    assert result.action == "invite"
    assert result.invite_example is None
    assert _tapple_strategy_output_violations(result)


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


@pytest.mark.parametrize(
    "reply",
    [
        "そうですね。僕は絶対安全なので安心してください。",
        "そうですね。安心して会えるように、必ず安全を保証します。",
    ],
)
def test_tapple_reply_rejects_unverifiable_absolute_safety_assurances(reply):
    violations = validate_candidate_replies(
        [reply],
        1,
        counterpart_message="会うのは少し不安です。",
        strategy_mode="tapple",
        tapple_action="wait",
    )

    assert any("安全" in violation for violation in violations)


@pytest.mark.parametrize(
    "reply",
    [
        "不安ですよね。駅前のカフェなど、人目のある場所で会う方法もあります。",
        "安全を保証できるとは言えないので、無理せずメッセージで話しましょう。",
        "絶対安全とは言えません。不安ならメッセージで話しましょう。",
    ],
)
def test_tapple_reply_allows_empathy_and_qualified_safety_information(reply):
    violations = validate_candidate_replies(
        [reply],
        1,
        counterpart_message="会うのは少し不安です。",
        strategy_mode="tapple",
        tapple_action="wait",
    )

    assert not any("安全" in violation for violation in violations)


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
    assert response.json()["replies"] == ["いいですね！どんなカフェが好きですか？"]
    assert response.json()["strategy"]["action"] == "continue"


@pytest.mark.parametrize(
    "decline",
    [
        "ごめんなさい、今は会うのは難しいです。",
        "今は会うのはちょっと考えたいです。",
    ],
)
@pytest.mark.parametrize(
    "invalid_reply",
    [
        "わかった！でも来週カフェに行こうよ！",
        "わかりました。考え直してもらえるとうれしいです。",
    ],
)
def test_decline_reinvitation_is_repaired_before_a_reply_is_returned(
    client, monkeypatch, decline, invalid_reply
):
    from app import database

    first = _raw_strategy(
        {
            "action": "invite",
            "rationale": "もう一度誘ってみます。",
            "evidence": [decline],
            "invite_example": "駅前のカフェでお茶しませんか？",
        },
        replies=[invalid_reply],
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
    assert response.json()["replies"] == ["わかった、教えてくれてありがとう。無理しないでね。"]
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
    assert response.json()["strategy"]["action"] == "wait"
    assert response.json()["strategy"]["evidence"] == [statement]


def test_malformed_or_missing_strategy_does_not_break_reply_parsing():
    assert _parse_tapple_strategy('{"replies":["a","b","c"]}', "相手: こんにちは") is None
    assert _parse_tapple_strategy('{"strategy":{"action":"maybe"}}', "相手: こんにちは") is None


def test_tapple_strategy_mode_requires_a_usable_strategy():
    violations = _tapple_strategy_output_violations(None)

    assert violations
    assert "strategy" in violations[0]


def test_accepted_prior_invitation_is_scheduling_not_a_second_invitation():
    conversation = (
        "相手: コーヒー好きです\n"
        "自分: 今度、駅前のカフェに一緒に行きませんか？\n"
        "相手: ぜひ一緒に行きたいです！"
    )
    result = _parse_tapple_strategy(
        _raw_strategy(
            {
                "action": "invite",
                "rationale": "相手が誘いに同意しています。",
                "evidence": ["ぜひ一緒に行きたいです！"],
                "invite_example": "駅前のカフェでお茶しませんか？",
            }
        ),
        conversation,
    )

    assert result is not None
    assert result.action == "continue"
    assert result.invite_example is None


def test_invalid_strategy_fallback_preserves_accepted_invitation_state():
    conversation = [
        {"sender": "contact", "content": "コーヒー好きです"},
        {"sender": "self", "content": "今度、駅前のカフェに一緒に行きませんか？"},
        {"sender": "contact", "content": "ぜひ一緒に行きたいです！"},
    ]

    result = _build_safe_tapple_fallback_strategy(conversation)

    assert result is not None
    assert result.action == "continue"
    assert result.evidence == ["ぜひ一緒に行きたいです！"]


def test_wait_action_rejects_an_implicit_joint_invitation_in_replies():
    violations = validate_candidate_replies(
        ["そうなんですよ！ぜひ今度行ってみましょう"],
        1,
        strategy_mode="tapple",
        tapple_action="wait",
        counterpart_message="カフェいいですね！行ってみたいな。",
    )

    assert violations
    assert any("誘い" in violation for violation in violations)


@pytest.mark.parametrize("action", ["wait", "continue"])
def test_non_invite_action_does_not_dismiss_ambiguous_activity_interest_with_solo_advice(action):
    violations = validate_candidate_replies(
        ["もし機会があればぜひ行ってみてください"],
        1,
        strategy_mode="tapple",
        tapple_action=action,
        counterpart_message="カフェいいですね！行ってみたいな。",
    )

    assert any("一人で行くよう勧め" in violation for violation in violations)


def test_ambiguous_interest_does_not_create_a_personal_visit_plan():
    history = [
        {"sender": "self", "content": "駅前に気になるカフェができたみたいです"},
        {"sender": "contact", "content": "カフェいいですね！行ってみたいな。"},
    ]
    violations = validate_candidate_replies(
        ["近いうちに行ってみるつもりです"],
        1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("本人の活動予定" in violation for violation in violations)

    unanchored_history = [
        {"sender": "contact", "content": "カフェいいですね！"},
    ]
    unanchored_violations = validate_candidate_replies(
        ["カフェに行こうと思ってます"],
        1,
        counterpart_message=unanchored_history[-1]["content"],
        conversation_messages=unanchored_history,
        strategy_mode="tapple",
        tapple_action="continue",
    )
    assert any("本人の活動予定" in violation for violation in unanchored_violations)

    grounded_history = [
        {"sender": "self", "content": "今度カフェに行ってみようと思ってます"},
        {"sender": "contact", "content": "カフェいいですね！行ってみたいな。"},
    ]
    grounded_violations = validate_candidate_replies(
        ["近いうちに行ってみるつもりです"],
        1,
        counterpart_message=grounded_history[-1]["content"],
        conversation_messages=grounded_history,
        strategy_mode="tapple",
        tapple_action="continue",
    )
    assert not any("本人の活動予定" in violation for violation in grounded_violations)


def test_tapple_prompt_requires_strategy_and_action_consistent_replies():
    messages = prompt.build_initial_generation_messages(
        system_prompt="system",
        chat_history_text="相手: カフェいいですね！行ってみたいな。",
        strategy_mode="tapple",
    )
    instruction = messages[1]["content"]

    assert "strategyは必須" in instruction
    assert "waitまたはstop" in instruction
    assert "返信候補も会う提案を含めない" in instruction
    assert "invite以外の場合に、相手の活動への関心はあるが一緒に行く意思が不明" in instruction
    assert "相手が挙げた活動や話題に直接つながる返信" in instruction
    assert "店の雰囲気を知っているように述べず" in instruction
    assert "一人で行くよう勧めたり" in instruction
    assert "既知の関心を一度だけ共有" in instruction


def test_accepted_invitation_replies_advance_to_logistics_without_reinviting():
    conversation = [
        {"sender": "contact", "content": "コーヒー好きです"},
        {"sender": "self", "content": "今度、駅前のカフェに一緒に行きませんか？"},
        {"sender": "contact", "content": "ぜひ一緒に行きたいです！"},
    ]
    unadvanced = validate_candidate_replies(
        ["ぜひ行きましょう！", "ありがとうございます！", "楽しみです！"],
        3,
        counterpart_message="ぜひ一緒に行きたいです！",
        conversation_messages=conversation,
        strategy_mode="tapple",
        tapple_action="continue",
    )
    advanced = validate_candidate_replies(
        [
            "うれしい！いつ頃が都合よさそう？",
            "いいね！日程はまた相談しよう",
            "楽しみ！予定合わせていこうね",
        ],
        3,
        counterpart_message="ぜひ一緒に行きたいです！",
        conversation_messages=conversation,
        strategy_mode="tapple",
        tapple_action="continue",
    )

    assert any("日程調整" in violation for violation in unadvanced)
    assert not any("日程調整" in violation for violation in advanced)
