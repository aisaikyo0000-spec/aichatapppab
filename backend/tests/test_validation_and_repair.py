"""学習優先型アーキテクチャ (Learning-First Architecture) の包括的テスト。

Hard Validation, Repair, 返信ペア構築, 全件スタイル集計, Few-shot検索, プレビューAPI連携を検証。
"""
import json
import pytest
from fastapi import HTTPException

from app.routers import generation
from app.ai import prompt
from app.ai.base import AIError


def test_time_line_cleaning():
    """メッセージ内の単独時刻行のみを除去し、本文中の時刻は残す。"""
    raw_content = (
        "15:19\n"
        "こんにちは！\n"
        "明日の18:30にお店予約しました！\n"
        "20:40"
    )
    cleaned = prompt.clean_chat_message_content(raw_content)
    assert "15:19" not in cleaned
    assert "20:40" not in cleaned
    assert "明日の18:30にお店予約しました！" in cleaned
    assert "こんにちは！" in cleaned


def test_strict_parse_replies():
    """厳格パース: JSONおよびラベル分割のみ受け入れ、雑な改行フォールバックを排除。"""
    # 1. 正常JSON
    json_data = json.dumps({"replies": ["案1です！\n文2\n文3", "案2です！\n文2\n文3", "案3です！\n文2\n文3"]})
    assert len(generation._parse_replies_strict(json_data, 3)) == 3

    # 2. 正常ラベル分割
    label_data = (
        "【案1】\n映画いいですね！\n最近何見ました？\n気になります笑\n"
        "【案2】\n映画館行ってきたんですね！\nポップコーン食べました？\n教えてください😊\n"
        "【案3】\nいいなー！\n最近映画館行ってないです笑\n久しぶりに行きたくなりました"
    )
    assert len(generation._parse_replies_strict(label_data, 3)) == 3

    # 3. 1案3行の通常テキスト（3案と誤認してはならず、パース失敗として空リストを返す）
    three_lines_single_reply = (
        "映画いいですね！\n"
        "最近映画館行けてないです笑\n"
        "おすすめの作品ありますか？"
    )
    assert generation._parse_replies_strict(three_lines_single_reply, 3) == []


def test_hard_validator_detects_violations():
    """Hardバリデータが案数不一致、空文、AI_QUESTION混入、候補重複を検知すること。"""
    # 正常ケース（句点や短文が含まれていてもHardバリデーションは通過する）
    valid_replies = [
        "映画いいですね。\n最近何見ました？",
        "映画館行ってきたんですね！\n何がおすすめですか？😊",
        "いいなー！\n今度行ってみませんか？",
    ]
    assert generation.validate_candidate_replies(valid_replies, 3) == []

    # 違反1: 案数不足
    assert any("案数" in e for e in generation.validate_candidate_replies(valid_replies[:2], 3))

    # 違反2: 空文字
    v_empty = ["", valid_replies[1], valid_replies[2]]
    assert any("空文字" in e for e in generation.validate_candidate_replies(v_empty, 3))

    # 違反3: AI_QUESTION タグ混入
    v_ai_q = ["映画いいですね！\n[AI_QUESTION]何見ました？[/AI_QUESTION]", valid_replies[1], valid_replies[2]]
    assert any("AI_QUESTIONタグ" in e for e in generation.validate_candidate_replies(v_ai_q, 3))

    # 違反4: 候補同士の重複・極端な高類似
    v_dupe = [valid_replies[0], valid_replies[0], valid_replies[2]]
    assert any("重複しています" in e for e in generation.validate_candidate_replies(v_dupe, 3))

    # Step 2: 質問なしは正常系（違反ではない）
    v_no_q = ["映画いいですね！", "映画館行ってきたんですね！", "いいなー！"]
    assert generation.validate_candidate_replies(v_no_q, 3) == []
    # 「質問しない」条件でも質問なしが許容される（従来通り）
    assert generation.validate_candidate_replies(v_no_q, 3, condition="質問しない") == []


def test_followup_validator_rejects_only_exact_normalized_latest_self_echo():
    """追いメッセージは直近の自分の文そのものだけを表記差込みで拒否する。"""
    latest_self = "カフェ楽しみだね！ また話そう。"
    replies = [
        "カフェ楽しみだね、また話そう！",  # punctuation/spacing-only difference
        "カフェ楽しみだね！おすすめのお店も気になる笑",  # topic overlap is allowed
        "また話そう！",  # short acknowledgment variant is not an exact echo
    ]

    violations = generation.validate_candidate_replies(
        replies,
        expected_candidates=3,
        mode="followup",
        last_self_message=latest_self,
    )

    assert any("直近の自分のメッセージをそのまま繰り返しています" in error for error in violations)
    assert generation.validate_candidate_replies(
        replies,
        expected_candidates=3,
        mode="normal",
        last_self_message=latest_self,
    ) == []


def test_followup_validator_ignores_empty_or_nonmatching_last_self_message():
    replies = ["いいですね！", "楽しそう！", "また話そう！"]
    assert generation.validate_candidate_replies(
        replies, mode="followup", last_self_message=" \t。！？"
    ) == []
    assert generation.validate_candidate_replies(
        replies, mode="followup", last_self_message="映画もいいですね！"
    ) == []


def test_normal_validator_rejects_ungrounded_personal_experience_answer():
    """本人の経験を尋ねられ、根拠がないのに肯定する返信を拒否する。"""
    replies = [
        "ありますよ！",
        "行ったことないです",
        "お店によっていろいろありそうですよね",
    ]

    violations = generation.validate_candidate_replies(
        replies,
        expected_candidates=3,
        mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=[],
    )

    assert any("本人の経験を確認できる情報がありません" in error for error in violations)
    assert any("案2に本人の経験を確認できる情報がありません" in error for error in violations)
    assert any("[AI_QUESTION]" in error for error in violations)
    assert not any("中立的な反応" in error for error in violations)


def test_normal_validator_allows_neutral_reply_and_grounded_experience():
    """中立的な反応は許可し、会話履歴に本人の経験があれば経験回答も許可する。"""
    replies = [
        "ありますよ！",
        "寿司いいですね！",
        "お店によっていろいろありそうですよね",
    ]

    assert generation.validate_candidate_replies(
        replies,
        mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=["自分: 寿司屋には行ったことがあります"],
    ) == []
    assert generation.validate_candidate_replies(
        replies,
        mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=["自分: 寿司屋によく行きます"],
    ) == []
    assert generation.validate_candidate_replies(
        ["たまに行きます"], expected_candidates=1, mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=["自分: 寿司屋にはたまに行きます"],
    ) == []
    assert generation.validate_candidate_replies(
        replies,
        mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=["自分: 寿司が好きです"],
    )
    assert generation.validate_candidate_replies(
        replies,
        mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=[],
    )
    assert generation.validate_candidate_replies(
        replies,
        mode="normal",
        counterpart_message="最近お寿司食べたんだ",
        known_self_facts=[],
    ) == []


def test_normal_validator_checks_each_experience_claim_polarity_and_strength():
    question = "寿司って行ったことある？"
    one_visit = ["自分: 寿司屋には行ったことがあります"]
    negative_fact = ["自分: 寿司屋には行ったことがありません"]

    # A positive fact cannot license a negative answer; a single visit also
    # cannot license a habitual claim such as "たまに行く".
    for candidate, fact in (
        ("行ったことないです", one_visit),
        ("たまに行きます", one_visit),
    ):
        violations = generation.validate_candidate_replies(
            [candidate], expected_candidates=1, mode="normal", counterpart_message=question,
            known_self_facts=fact,
        )
        assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations)

    # Conversely, a matching negative fact must license a negative answer.
    assert generation.validate_candidate_replies(
        ["行ったことないです"], expected_candidates=1, mode="normal", counterpart_message=question,
        known_self_facts=negative_fact,
    ) == []


def test_normal_validator_catches_natural_past_experience_claims_per_candidate():
    question = "寿司って行ったことある？"
    unsupported = [
        "この前行ったよ",
        "一度行きました",
        "行きました",
        "行きましたよ",
        "食べたよ",
        "見たんだよね",
    ]
    for candidate in unsupported:
        violations = generation.validate_candidate_replies(
            [candidate], expected_candidates=1, mode="normal", counterpart_message=question,
            known_self_facts=[],
        )
        assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations), candidate


def test_normal_validator_matches_topic_and_action_not_unrelated_facts():
    # A movie preference and sushi visit cannot ground a movie visit.
    assert generation.validate_candidate_replies(
        ["行ったよ"], expected_candidates=1, mode="normal", counterpart_message="映画行ったことある？",
        known_self_facts=["自分: 映画が好きで寿司屋には行ったことがあります"],
    )
    # A matching movie fact grounds the answer even when the topic is omitted.
    assert generation.validate_candidate_replies(
        ["見たよ"], expected_candidates=1, mode="normal", counterpart_message="映画行ったことある？",
        known_self_facts=["自分: 映画を見た"],
    ) == []


def test_normal_validator_does_not_reject_unrelated_or_neutral_replies():
    assert generation.validate_candidate_replies(
        ["いいですね！", "そうなんですね", "お店によって違いそうですね"],
        mode="normal", counterpart_message="寿司って行ったことある？",
        known_self_facts=[],
    ) == []


def test_normal_validator_handles_experience_polarity_without_false_positives():
    question = "寿司って行ったことある？"
    positive_fact = ["自分: 寿司屋には行ったことがあります"]
    negative_fact = ["自分: 寿司屋には行ったことがありません"]

    for candidate in ("ないよ", "行ってない", "行ってません"):
        violations = generation.validate_candidate_replies(
            [candidate], expected_candidates=1, mode="normal", counterpart_message=question,
            known_self_facts=positive_fact,
        )
        assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations), candidate
    for candidate in ("行ったことはないです", "行ったことはありません", "行ったことはないよ"):
        violations = generation.validate_candidate_replies(
            [candidate], expected_candidates=1, mode="normal", counterpart_message=question,
            known_self_facts=positive_fact,
        )
        assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations), candidate

    assert generation.validate_candidate_replies(
        ["行ったことないです"], expected_candidates=1, mode="normal",
        counterpart_message=question, known_self_facts=negative_fact,
    ) == []
    assert generation.validate_candidate_replies(
        ["ないよ"], expected_candidates=1, mode="normal",
        counterpart_message=question, known_self_facts=negative_fact,
    ) == []

    # A third party's experience is not a claim about the app user.
    assert generation.validate_candidate_replies(
        ["行ったことない人にはおすすめですよ"], expected_candidates=1,
        mode="normal", counterpart_message=question, known_self_facts=positive_fact,
    ) == []

    # Double negation has indeterminate polarity, so it cannot borrow positive evidence.
    violations = generation.validate_candidate_replies(
        ["行ったことなくはないです"], expected_candidates=1, mode="normal",
        counterpart_message=question, known_self_facts=positive_fact,
    )
    assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations)


def test_normal_validator_detects_te_kita_experience_but_allows_general_existence():
    question = "寿司って食べたことある？"
    for candidate in ("食べてきたよ", "見てきた", "行ってきたよ"):
        violations = generation.validate_candidate_replies(
            [candidate], expected_candidates=1, mode="normal", counterpart_message=question,
            known_self_facts=[],
        )
        assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations), candidate

    assert generation.validate_candidate_replies(
        ["そういうお店ありますよね"], expected_candidates=1, mode="normal",
        counterpart_message="寿司って行ったことある？", known_self_facts=[],
    ) == []


def test_normal_validator_uses_last_experience_question_and_its_topic_action():
    counterpart = "この前映画に行ったことあるって話したよね？寿司食べたことある？"
    previous_topic_fact = ["自分: 映画には行ったことがあります"]
    violations = generation.validate_candidate_replies(
        ["食べたよ"], expected_candidates=1, mode="normal", counterpart_message=counterpart,
        known_self_facts=previous_topic_fact,
    )
    assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations)
    assert generation.validate_candidate_replies(
        ["食べたよ"], expected_candidates=1, mode="normal", counterpart_message=counterpart,
        known_self_facts=["自分: 寿司を食べたことがあります"],
    ) == []


def test_normal_validator_recognizes_natural_experience_question_forms():
    for counterpart in ("寿司屋行ったことあるの？", "寿司って行ったことあるんですか？", "行ったことはある？"):
        violations = generation.validate_candidate_replies(
            ["ありますよ"], expected_candidates=1, mode="normal", counterpart_message=counterpart,
            known_self_facts=[],
        )
        assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations), counterpart


def test_specific_question_requires_a_topic_explicit_fact_for_experience_claims():
    for candidate, fact in (
        ("行ったことあるよ", "自分: 行ったことがあります"),
        ("行ったことないよ", "自分: 行ったことがない"),
        ("行ったことないよ", "自分: 映画館には行ったことがない"),
    ):
        violations = generation.validate_candidate_replies(
            [candidate], expected_candidates=1, mode="normal",
            counterpart_message="寿司って行ったことある？", known_self_facts=[fact],
        )
        assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations), (candidate, fact)


def test_temporal_phrase_immediately_before_action_does_not_become_topic():
    violations = generation.validate_candidate_replies(
        ["今まで行ったことあるよ"], expected_candidates=1, mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=["自分: 今まで行ったことがあります"],
    )
    assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations)


def test_normal_validator_scopes_polarity_to_the_experience_answer_clause():
    ungrounded_positive = generation.validate_candidate_replies(
        ["ありますよ。詳しい人にはおすすめを聞きたいです"], expected_candidates=1, mode="normal",
        counterpart_message="寿司って行ったことある？", known_self_facts=[],
    )
    assert any("案1に本人の経験を確認できる情報がありません" in e for e in ungrounded_positive)

    # A later third-party negative must not turn the user's positive answer into a negative one.
    assert generation.validate_candidate_replies(
        ["ありますよ。行ったことない人にもおすすめです"], expected_candidates=1, mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=["自分: 寿司屋には行ったことがあります"],
    ) == []
    violations = generation.validate_candidate_replies(
        ["行ったことありますよ、行ったことない人にもおすすめです"], expected_candidates=1,
        mode="normal", counterpart_message="寿司って行ったことある？", known_self_facts=[],
    )
    assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations)


def test_normal_validator_matches_generic_and_compound_experience_claims():
    assert generation.validate_candidate_replies(
        ["ありますよ"], expected_candidates=1, mode="normal", counterpart_message="海外経験ありますか？",
        known_self_facts=["自分: 海外旅行に行ったことがあります"],
    ) == []
    assert generation.validate_candidate_replies(
        ["自分も行ったことあります"], expected_candidates=1, mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=["自分: 寿司屋には行ったことがあります"],
    ) == []
    assert generation.validate_candidate_replies(
        ["自分もありますよ"], expected_candidates=1, mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=["自分: 寿司屋には行ったことがあります"],
    ) == []
    assert generation.validate_candidate_replies(
        ["自分もありますよ"], expected_candidates=1, mode="normal",
        counterpart_message="寿司って行ったことある？", known_self_facts=[],
    )
    assert generation.validate_candidate_replies(
        ["寿司食べに行ったよ"], expected_candidates=1, mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=["自分: 寿司屋には行ったことがあります"],
    ) == []


def test_normal_validator_rejects_unsupported_current_experience_claim():
    violations = generation.validate_candidate_replies(
        ["行ってるよ"], expected_candidates=1, mode="normal",
        counterpart_message="寿司って行ったことある？", known_self_facts=[],
    )
    assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations)


def test_normal_validator_catches_prefixed_and_unavailable_experience_answers():
    for candidate in ("うん、ありますよ", "はい、あるよ", "ううん、ないよ", "いや、ないよ", "一応あるよ", "実はあるよ",
                      "まだ行けてないよ", "行けたことないです"):
        violations = generation.validate_candidate_replies(
            [candidate], expected_candidates=1, mode="normal",
            counterpart_message="寿司って行ったことある？", known_self_facts=[],
        )
        assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations), candidate


def test_normal_validator_ignores_positive_claims_about_other_people_and_scopes_contrast():
    for candidate in ("行ったことある人多いよね", "友達は行ったことあるよ", "彼氏は行ったことあるよ"):
        assert generation.validate_candidate_replies(
            [candidate], expected_candidates=1, mode="normal",
            counterpart_message="寿司って行ったことある？", known_self_facts=[],
        ) == []

    assert generation.validate_candidate_replies(
        ["行ったことあるけど行ってない時もある"], expected_candidates=1, mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=["自分: 寿司屋には行ったことがあります"],
    ) == []
    assert generation.validate_candidate_replies(
        ["行ったことありますが詳しくないです"], expected_candidates=1, mode="normal",
        counterpart_message="寿司って行ったことある？",
        known_self_facts=["自分: 寿司屋には行ったことがあります"],
    ) == []
    for candidate in ("行ったことありますが今は行ってません", "行ったことありますが最近は行ってません"):
        assert generation.validate_candidate_replies(
            [candidate], expected_candidates=1, mode="normal",
            counterpart_message="寿司って行ったことある？",
            known_self_facts=["自分: 寿司屋には行ったことがあります"],
        ) == []


def test_experience_topic_ignores_temporal_adverbs_when_matching_facts():
    for fact in ("自分: 旅行にはまだ行けてない", "自分: 友達の家にはまだ行けてない"):
        violations = generation.validate_candidate_replies(
            ["まだ行けてないよ"], expected_candidates=1, mode="normal",
            counterpart_message="寿司って行ったことある？", known_self_facts=[fact],
        )
        assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations), fact
    for candidate, fact in (
        ("今は行ってないよ", "自分: 映画館には今は行ってない"),
        ("前は行ってたよ", "自分: 映画館には前は行ってた"),
    ):
        violations = generation.validate_candidate_replies(
            [candidate], expected_candidates=1, mode="normal",
            counterpart_message="寿司って行ったことある？", known_self_facts=[fact],
        )
        assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations), (candidate, fact)


def test_generic_experience_does_not_match_unrelated_subtopic_fact():
    violations = generation.validate_candidate_replies(
        ["ありますよ"], expected_candidates=1, mode="normal", counterpart_message="海外経験ありますか？",
        known_self_facts=["自分: 海外ドラマを見たことがあります"],
    )
    assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations)


def test_generic_experience_requires_a_topic_match_when_question_has_no_topic():
    violations = generation.validate_candidate_replies(
        ["ありますよ"], expected_candidates=1, mode="normal", counterpart_message="経験ありますか？",
        known_self_facts=["自分: 映画を見たことがあります"],
    )
    assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations)


def test_generic_overseas_experience_only_matches_travel_or_residence_facts():
    for fact in ("自分: 海外料理を食べたことがあります", "自分: 海外ドラマを見たことがあります"):
        violations = generation.validate_candidate_replies(
            ["ありますよ"], expected_candidates=1, mode="normal", counterpart_message="海外経験ありますか？",
            known_self_facts=[fact],
        )
        assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations), fact

    for fact in (
        "自分: 海外旅行に行ったことがあります", "自分: 海外に住んだことがあります",
        "自分: 3年間海外に住んでいました", "自分: 学生時代に海外に住んでいた",
    ):
        assert generation.validate_candidate_replies(
            ["ありますよ"], expected_candidates=1, mode="normal", counterpart_message="海外経験ありますか？",
            known_self_facts=[fact],
        ) == []


def test_overseas_residence_present_tense_is_experience_and_third_party_is_not():
    for candidate in ("海外に住んでいます", "海外に住んでるよ"):
        violations = generation.validate_candidate_replies(
            [candidate], expected_candidates=1, mode="normal", counterpart_message="海外経験ありますか？",
            known_self_facts=[],
        )
        assert any("案1に本人の経験を確認できる情報がありません" in e for e in violations), candidate

    assert generation.validate_candidate_replies(
        ["海外に住んでる人多いよね"], expected_candidates=1, mode="normal",
        counterpart_message="海外経験ありますか？", known_self_facts=[],
    ) == []
    assert generation.validate_candidate_replies(
        ["海外に住んでいます"], expected_candidates=1, mode="normal",
        counterpart_message="海外経験ありますか？", known_self_facts=["自分: 海外に住んでいます"],
    ) == []


def test_normal_validator_accepts_periodic_habit_evidence():
    assert generation.validate_candidate_replies(
        ["たまに行きます"], expected_candidates=1, mode="normal",
        counterpart_message="寿司屋って行ったことある？",
        known_self_facts=["自分: 月に一度は寿司屋に行きます"],
    ) == []


def test_experience_repair_requests_private_user_confirmation_not_sendable_guess():
    messages = generation._build_repair_messages(
        [{"role": "system", "content": "unknown experience"}],
        '{"replies":["ありますよ"]}',
        ["案1に本人の経験を確認できる情報がありません。"],
        candidates=3,
    )
    repair = messages[-1]["content"]
    assert "[AI_QUESTION]" in repair
    assert "アプリ利用者" in repair
    assert "相手に送る返信候補として確認質問を作らない" in repair


def test_unresolved_reference_repair_remains_a_sendable_counterpart_question():
    messages = generation._build_repair_messages(
        [{"role": "system", "content": "unknown referent"}],
        '{"replies":["まだ決まってないです"]}',
        ["あれ・それの参照先が会話履歴から特定できません。"],
        candidates=3,
    )
    repair = messages[-1]["content"]
    assert "通常のJSON replies" in repair
    assert "相手に送る短い確認質問" in repair
    assert "[AI_QUESTION]" not in repair


@pytest.mark.parametrize(
    "candidate",
    [
        "今のところ順調に進んでますよ！",
        "まだ決まってないです",
        "これから決める感じですね！",
    ],
)
def test_normal_validator_rejects_ungrounded_status_for_unresolved_reference(candidate):
    violations = generation.validate_candidate_replies(
        [candidate], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？",
        known_self_facts=[],
        chat_history_text="相手: あれどうなった？",
    )
    assert any("状況を確認できる情報がありません" in error for error in violations)


def test_normal_validator_allows_short_counterpart_clarification_for_unresolved_reference():
    assert generation.validate_candidate_replies(
        ["何のことでしたっけ？"], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[],
        chat_history_text="相手: あれどうなった？",
    ) == []


@pytest.mark.parametrize("candidate", [
    "何のことでしたっけ？",
    "どの話のこと？",
])
def test_unresolved_status_reference_requires_an_explicit_counterpart_clarification(candidate):
    assert generation.validate_candidate_replies(
        [candidate], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[],
        chat_history_text="相手: あれどうなった？",
    ) == []


@pytest.mark.parametrize("candidate", [
    "え、どの件だっけ？",
    "何のことだったっけ？",
    "どの件でしたっけ？",
])
def test_unresolved_status_accepts_natural_counterpart_clarification_variants(candidate):
    assert generation.validate_candidate_replies(
        [candidate], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[],
        chat_history_text="相手: あれどうなった？",
    ) == []


def test_unresolved_status_accepts_casual_laughing_referent_question():
    assert generation.validate_candidate_replies(
        ["どの話だっけ笑"], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[],
        chat_history_text="相手: あれどうなった？",
    ) == []


@pytest.mark.parametrize("candidate", [
    "あれ何の話だっけ？",
    "それ何の話？",
])
def test_unresolved_status_accepts_direct_demonstrative_referent_questions(candidate):
    assert generation.validate_candidate_replies(
        [candidate], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[],
        chat_history_text="相手: あれどうなった？",
    ) == []


@pytest.mark.parametrize("candidate", [
    "まだ決まってないんですか？",
    "順調に進んでるんですか？",
    "これから決めるんですか？",
    "まだ決まってないんですか",
])
def test_unresolved_status_reference_rejects_status_questions_that_do_not_resolve_referent(candidate):
    violations = generation.validate_candidate_replies(
        [candidate], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[],
        chat_history_text="相手: あれどうなった？",
    )
    assert any("不明な参照先への確認" in error for error in violations)


@pytest.mark.parametrize("candidate", [
    "最近忙しかったけど何の件だっけ？",
    "旅行の予約したけどどの件だっけ？",
    "旅行の日程のことだったよね、どの件だっけ？",
    "何の件だっけ？明日映画に行くよ。",
])
def test_unresolved_reference_clarification_rejects_unrelated_or_unverified_clauses(candidate):
    violations = generation.validate_candidate_replies(
        [candidate], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[],
        chat_history_text="相手: あれどうなった？",
    )
    assert any("不明な参照先への確認" in error for error in violations)


@pytest.mark.parametrize("candidate", [
    "まだ決まってないよ",
    "順調に進んでるよ",
    "これから決めるよ",
    "ちょっと待ってね！",
    "わかったよ！",
])
def test_unresolved_status_reference_rejects_assertions_and_nonclarifying_filler(candidate):
    violations = generation.validate_candidate_replies(
        [candidate], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[],
        chat_history_text="相手: あれどうなった？",
    )
    assert any("相手に短く確認" in error for error in violations)


def test_old_status_from_a_different_topic_does_not_ground_unresolved_reference():
    history = (
        "自分: 転職はまだ決まってない\n"
        "相手: 週末の映画の予定どうする？\n"
        "自分: どっちでもいいよ\n"
        "相手: あれどうなった？"
    )
    violations = generation.validate_candidate_replies(
        ["まだ決まってないよ"], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[],
        chat_history_text=history,
    )
    assert any("状況を確認できる情報がありません" in error for error in violations)


@pytest.mark.parametrize(
    ("history", "reply", "should_pass"),
    [
        (
            "自分: 旅行の日程はまだ決まってない\n"
            "自分: 旅行の日程はもう決まった\n"
            "相手: あれどうなった？",
            "もう決まったよ",
            True,
        ),
        (
            "自分: 旅行の日程はまだ決まってない\n"
            "自分: 旅行の日程はもう決まった\n"
            "相手: あれどうなった？",
            "まだ決まってないよ",
            False,
        ),
    ],
)
def test_unresolved_status_uses_the_latest_status_in_the_immediately_preceding_context(history, reply, should_pass):
    violations = generation.validate_candidate_replies(
        [reply], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[],
        chat_history_text=history,
    )
    assert (not violations) is should_pass


def test_every_status_claim_in_a_reply_must_be_supported():
    history = "自分: 旅行の日程はまだ決まってない\n相手: あれどうなった？"
    violations = generation.validate_candidate_replies(
        ["まだ決まってないけど映画はもう決まったよ"],
        expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[],
        chat_history_text=history,
    )
    assert any("状況を確認できる情報がありません" in error for error in violations)


def test_unresolved_status_rejects_multiple_same_category_claims_even_when_one_is_supported():
    history = "自分: 旅行の日程はまだ決まってない\n相手: あれどうなった？"
    violations = generation.validate_candidate_replies(
        ["旅行の日程はまだ決まってないよ。引っ越し先もまだ決まってないんだ"],
        expected_candidates=1,
        mode="normal",
        counterpart_message="あれどうなった？",
        known_self_facts=[],
        chat_history_text=history,
    )
    assert any("状況を確認できる情報がありません" in error for error in violations)


@pytest.mark.parametrize("candidate", [
    "旅行の日程はまだ決まってないよ。最近寿司屋によく行くよ",
    "まだ決まってないよ。私は猫を飼ってる",
    "まだ決まってないよ最近寿司屋によく行くよ",
    "まだ決まってないから最近寿司屋によく行くよ",
])
def test_unresolved_status_rejects_unrelated_claims_added_to_supported_status(candidate):
    history = "自分: 旅行の日程はまだ決まってない\n相手: あれどうなった？"
    violations = generation.validate_candidate_replies(
        [candidate],
        expected_candidates=1,
        mode="normal",
        counterpart_message="あれどうなった？",
        known_self_facts=[],
        chat_history_text=history,
    )
    assert any("状況を確認できる情報がありません" in error for error in violations)


@pytest.mark.parametrize("candidate", [
    "え、まだ決まってないよ",
    "うーん、まだ決まってないよ",
    "今のところまだ決まってないよ",
    "たぶんまだ決まってないよ",
    "たぶん、まだ決まってないよ",
    "まだ決まってないと思う",
    "まだ決まってないと思うよ",
    "まだ決まってないかもしれないね",
])
def test_unresolved_status_allows_harmless_discourse_preface_with_supported_single_status(candidate):
    history = "自分: 旅行の日程はまだ決まってない\n相手: あれどうなった？"
    assert generation.validate_candidate_replies(
        [candidate],
        expected_candidates=1,
        mode="normal",
        counterpart_message="あれどうなった？",
        known_self_facts=[],
        chat_history_text=history,
    ) == []


@pytest.mark.parametrize("message", ["明日何時にする？", "今日仕事？"])
def test_status_guard_does_not_reject_unrelated_normal_questions(message):
    assert generation.validate_candidate_replies(
        ["明日なら大丈夫だよ"], expected_candidates=1, mode="normal",
        counterpart_message=message, known_self_facts=[],
        chat_history_text=f"相手: {message}",
    ) == []


def test_normal_validator_allows_status_supported_by_prior_self_message():
    assert generation.validate_candidate_replies(
        ["まだ決まってないよ"], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=["自分: 旅行の日程はまだ決まってない"],
        chat_history_text="自分: 旅行の日程はまだ決まってない\n相手: あれどうなった？",
    ) == []


def test_normal_validator_does_not_treat_unrelated_history_as_status_evidence():
    violations = generation.validate_candidate_replies(
        ["まだ決まってないよ"], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=["自分: 寿司が好きです"],
        chat_history_text="自分: 寿司が好きです\n相手: あれどうなった？",
    )
    assert any("状況を確認できる情報がありません" in error for error in violations)


def test_normal_validator_does_not_reject_status_when_referent_is_not_ambiguous():
    assert generation.validate_candidate_replies(
        ["まだ決まってないよ"], expected_candidates=1, mode="normal",
        counterpart_message="旅行の日程どうなった？", known_self_facts=[],
        chat_history_text="相手: 旅行の日程どうなった？",
    ) == []


def test_unresolved_status_can_use_the_immediately_adjacent_self_status():
    assert generation.validate_candidate_replies(
        ["まだ決まってないよ"], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[],
        chat_history_text="自分: 旅行の日程はまだ決まってないんだ\n相手: あれどうなった？",
    ) == []


def test_unresolved_status_cannot_reuse_status_after_an_intervening_topic():
    history = (
        "自分: 旅行の日程はまだ決まってないんだ\n"
        "相手: ところで寿司好き？\n"
        "自分: 寿司いいよね\n"
        "相手: あれどうなった？"
    )
    assert any("状況を確認できる情報がありません" in error for error in generation.validate_candidate_replies(
        ["まだ決まってないよ"], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[], chat_history_text=history,
    ))
    assert generation.validate_candidate_replies(
        ["どの件のことですか？"], expected_candidates=1, mode="normal",
        counterpart_message="あれどうなった？", known_self_facts=[], chat_history_text=history,
    ) == []


def test_unresolved_status_repair_requests_counterpart_clarification_not_app_user_question():
    repair = generation._build_repair_messages(
        [{"role": "system", "content": "unknown status"}],
        '{"replies":["まだ決まってないです"]}',
        ["案1に状況を確認できる情報がありません。参照先が不明なため相手に確認してください。"],
        candidates=3,
    )[-1]["content"]
    assert "相手に送る短い確認質問" in repair
    assert "通常のJSON replies" in repair
    assert "[AI_QUESTION]" not in repair


def test_ambiguous_status_repair_fills_missing_safe_clarification_candidates_and_saves_only_them(client, monkeypatch):
    cid = client.post("/api/contacts", json={"name": "曖昧参照の安全候補", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "あれどうなった？"})

    calls = 0

    class SparseClarificationRepairProvider:
        name = "sparse_clarification_repair_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal calls
            calls += 1
            if calls % 2 == 1:
                return json.dumps({"replies": [
                    "まだ決まってないです",
                    "順調に進んでますよ！",
                    "これから決める感じですね！",
                ]})
            return json.dumps({"replies": [
                "え、どの件だっけ？",
                "何のことだったっけ？",
                "ちょっと待ってね！",
            ]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: SparseClarificationRepairProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "sparse_clarification_repair_fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })

    response = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})

    assert response.status_code == 200
    result = response.json()
    assert calls == 2
    assert len(result["replies"]) == 3
    assert all(generation._is_short_counterpart_clarification(reply) for reply in result["replies"])
    assert all("決まってない" not in reply and "順調に進んで" not in reply for reply in result["replies"])
    conn = generation.database.get_conn()
    try:
        saved = conn.execute(
            "SELECT generated_text FROM generation_history WHERE id IN (?, ?, ?)",
            tuple(result["history_ids"]),
        ).fetchall()
        assert len(saved) == 3
        assert {row["generated_text"] for row in saved} == set(result["replies"])
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("message", "history"),
    [
        ("あれどうなった？寿司屋って行ったことある？", "相手: あれどうなった？寿司屋って行ったことある？"),
        ("あれどうなった？", "自分: 旅行の日程はまだ決まってない\n相手: あれどうなった？"),
    ],
)
def test_safe_reference_clarification_fallback_is_disabled_for_personal_experience_or_known_status(message, history):
    assert generation._build_safe_reference_clarification_candidates(
        ["ちょっと待ってね！"],
        3,
        counterpart_message=message,
        known_self_facts=[],
        chat_history_text=history,
        tone="",
        condition="",
        current_datetime=None,
    ) is None


def test_safe_reference_fallback_does_not_preserve_unverified_clause_before_clarification():
    safe = generation._build_safe_reference_clarification_candidates(
        ["旅行の日程のことだったよね、どの件だっけ？"],
        3,
        counterpart_message="あれどうなった？",
        known_self_facts=[],
        chat_history_text="相手: あれどうなった？",
        tone="",
        condition="",
        current_datetime=None,
    )
    assert safe is None


def test_safe_reference_clarification_fallback_is_normal_mode_only():
    assert generation._build_safe_reference_clarification_candidates(
        ["どの件だっけ？"],
        3,
        counterpart_message="あれどうなった？",
        known_self_facts=[],
        chat_history_text="相手: あれどうなった？",
        tone="",
        condition="",
        current_datetime=None,
        mode="followup",
    ) is None


@pytest.mark.parametrize(
    ("question", "fact"),
    [
        ("寿司屋行ったことあったりする？", "自分: 寿司屋には行ったことがあります"),
        ("寿司屋って行ったこととかある？", "自分: 寿司屋には行ったことがあります"),
        ("寿司行ったことあるんだっけ？", "自分: 寿司には行ったことがあります"),
        ("寿司って食べたことあるかな？", "自分: 寿司を食べたことがあります"),
    ],
)
def test_experience_question_variants_require_matching_facts(question, fact):
    violations = generation.validate_candidate_replies(
        ["ありますよ"], expected_candidates=1, mode="normal",
        counterpart_message=question, known_self_facts=[],
    )
    assert any("案1に本人の経験を確認できる情報がありません" in error for error in violations)
    assert generation.validate_candidate_replies(
        ["ありますよ"], expected_candidates=1, mode="normal",
        counterpart_message=question, known_self_facts=[fact],
    ) == []


def test_generation_passes_latest_counterpart_message_and_self_facts_to_validator(client, monkeypatch):
    """通常モードの初回・修復検証へ最新メッセージと本人事実を渡す。"""
    cid = client.post("/api/contacts", json={"name": "事実境界テスト", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "寿司屋には行ったことがあります"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "寿司って行ったことある？"})

    calls = 0

    class FakeProvider:
        name = "fact_boundary_test"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal calls
            calls += 1
            return json.dumps({"replies": [
                "ありますよ！",
                "寿司いいですね！",
                "お店によっていろいろありそうですよね",
            ]})

        def available_models(self):
            return []

    validator_args = []
    original_validator = generation.validate_candidate_replies

    def capture_validator_call(*args, **kwargs):
        validator_args.append(kwargs.copy())
        return original_validator(*args, **kwargs)

    monkeypatch.setattr(generation, "validate_candidate_replies", capture_validator_call)
    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "fact_boundary_test", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })

    response = client.post("/api/generate", json={"contact_id": cid, "mode": "normal", "candidates": 3})

    assert response.status_code == 200
    assert calls == 1
    assert validator_args[0].get("counterpart_message") == "寿司って行ったことある？"
    assert "寿司屋には行ったことがあります" in validator_args[0].get("known_self_facts", [])
    assert "自分: 寿司屋には行ったことがあります" in validator_args[0].get("chat_history_text", "")
def test_followup_echo_is_rejected_on_initial_and_repair_validation(client, monkeypatch):
    """初回とRepairの両方で履歴由来の直近自分文を使ってechoを拒否する。"""
    cid = client.post("/api/contacts", json={"name": "echo検証", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "元気にしてる？"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "最近忙しいです"})

    calls = 0

    class EchoThenRepairProvider:
        name = "echo_then_repair"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal calls
            calls += 1
            if calls == 1:
                return json.dumps({"replies": [
                    "元気にしてる！",  # punctuation-only echo
                    "また話せたらうれしいな！",
                    "落ち着いたらゆっくりできるといいね",
                ]})
            assert "直近の自分のメッセージをそのまま繰り返しています" in str(messages)
            return json.dumps({"replies": [
                "最近忙しいんですね。無理しすぎないでくださいね",
                "落ち着いたらゆっくりできるといいですね",
                "また話せたらうれしいです",
            ]})

        def available_models(self):
            return []

    validator_latest_self = []
    original_validator = generation.validate_candidate_replies

    def capture_validator_call(*args, **kwargs):
        validator_latest_self.append(kwargs.get("last_self_message"))
        return original_validator(*args, **kwargs)

    monkeypatch.setattr(generation, "validate_candidate_replies", capture_validator_call)
    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: EchoThenRepairProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "echo_then_repair",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    })

    response = client.post("/api/generate", json={"contact_id": cid, "mode": "followup", "candidates": 3})

    assert response.status_code == 200
    assert calls == 2
    assert validator_latest_self == ["元気にしてる？", "元気にしてる？"]


def test_ai_question_fullmatch():
    """AI_QUESTION は全体一致のみ検出し、部分一致を誤検知しないこと。"""
    pure_q = "[AI_QUESTION]コナンって見たことありますか？[/AI_QUESTION]"
    assert generation._extract_ai_question(pure_q) == "コナンって見たことありますか？"
    assert generation._extract_ai_question("[AI_QUESTION]質問[/AI_QUESTION]余計な文") is None

    partial_q = '{"replies": ["いいですね！\n[AI_QUESTION]質問[/AI_QUESTION]\n行きましょう！"]}'
    assert generation._extract_ai_question(partial_q) is None


def test_generate_with_repair_success(client, monkeypatch):
    """初回生成にHard違反（重複候補や空文）があっても、修復プロンプトによる1回再生成で合格すれば200を返す。"""
    cid = client.post("/api/contacts", json={"name": "修復テスト相手", "profile": "読書好き"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "本読んでます"})

    call_count = 0

    class RepairingFakeProvider:
        name = "repairing_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 初回: 案1と案2が重複するHard違反
                return json.dumps({"replies": [
                    "本いいですね！\n何読んでるんですか？",
                    "本いいですね！\n何読んでるんですか？",
                    "本いいなー！\n最近読めてないです笑\nどんな本ですか？",
                ]})
            else:
                # 2回目（修復後）: 重複を解消した出力
                assert any("前回の出力に以下の不備が検知されました" in str(m) for m in messages)
                return json.dumps({"replies": [
                    "本いいですね！\n何読んでるんですか？\n気になります笑",
                    "読書好きなんですね！\nおすすめありますか？\n教えてください",
                    "本いいなー！\n最近読めてないです笑\n何か探してみようと思いますが、おすすめありますか？",
                ]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: RepairingFakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "repairing_fake",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    })

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    assert call_count == 2
    assert len(r.json()["replies"]) == 3
    conn = generation.database.get_conn()
    try:
        batch = conn.execute(
            "SELECT outcome FROM generation_batches WHERE id = ?",
            (r.json()["batch_id"],),
        ).fetchone()
        assert batch["outcome"] == "pending"
    finally:
        conn.close()


def test_unknown_experience_repair_returns_private_question_not_chat_reply(client, monkeypatch):
    """未知の本人経験は利用者確認へ回し、チャット相手向け返信として保存しない。"""
    cid = client.post("/api/contacts", json={"name": "経験確認テスト", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "寿司って行ったことある？"})

    calls = 0

    class ExperienceQuestionProvider:
        name = "experience_question_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal calls
            calls += 1
            if calls == 1:
                return json.dumps({"replies": ["ありますよ！", "寿司いいですね！", "お店によって違いそうですね"]})
            return "[AI_QUESTION]寿司屋に行ったことがあるか教えてください[/AI_QUESTION]"

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: ExperienceQuestionProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "experience_question_fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })

    response = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert response.status_code == 200
    result = response.json()
    assert calls == 2
    assert result["replies"] == []
    assert result["history_ids"] == []
    assert result["question"] == "寿司屋に行ったことがあるか教えてください"


def test_ambiguous_status_ai_question_is_repaired_as_counterpart_clarifications(client, monkeypatch):
    """曖昧な「あれ」の確認を利用者向け質問として返さず、送信候補へ修復する。"""
    cid = client.post("/api/contacts", json={"name": "曖昧参照AI_QUESTION", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "あれどうなった？"})

    calls = 0

    class AmbiguousStatusQuestionProvider:
        name = "ambiguous_status_question_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal calls
            calls += 1
            if calls == 1:
                return "[AI_QUESTION]どの件のことですか？[/AI_QUESTION]"
            return json.dumps({"replies": [
                "どの話だっけ笑",
                "何のことだったっけ？",
                "どの件でしたっけ？",
            ]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: AmbiguousStatusQuestionProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "ambiguous_status_question_fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })

    response = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})

    assert response.status_code == 200
    result = response.json()
    assert calls == 2
    assert result["replies"]
    assert "question" not in result
    assert len(result["history_ids"]) == 3


def test_ambiguous_status_ai_question_retries_and_fails_without_history(client, monkeypatch):
    """曖昧参照へのAI_QUESTIONだけが続けば利用者へ漏らさず履歴も保存しない。"""
    cid = client.post("/api/contacts", json={"name": "曖昧参照失敗", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "あれどうなった？"})

    calls = 0

    class AlwaysPrivateQuestionProvider:
        name = "always_private_question_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal calls
            calls += 1
            return "[AI_QUESTION]どの件のことですか？[/AI_QUESTION]"

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: AlwaysPrivateQuestionProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "always_private_question_fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })

    response = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "candidate_validation_failed"
    assert "question" not in response.json()
    assert calls == 6
    conn = generation.database.get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0] == 0
    finally:
        conn.close()


def test_unresolved_status_does_not_block_ai_question_for_unknown_personal_experience(client, monkeypatch):
    """同じ発言に曖昧な状況照会と未確認の本人経験があれば後者は利用者へ確認する。"""
    cid = client.post("/api/contacts", json={"name": "複合質問", "profile": ""}).json()["id"]
    client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "contact", "content": "あれどうなった？寿司屋って行ったことある？"},
    )

    calls = 0

    class CombinedQuestionProvider:
        name = "combined_question_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal calls
            calls += 1
            return "[AI_QUESTION]寿司屋に行ったことがあるか教えてください[/AI_QUESTION]"

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: CombinedQuestionProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "combined_question_fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })

    response = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})

    assert response.status_code == 200
    result = response.json()
    assert calls == 1
    assert result["question"] == "寿司屋に行ったことがあるか教えてください"
    assert result["replies"] == []
    assert result["history_ids"] == []


def test_unresolved_status_does_not_route_counterpart_clarification_to_user(client, monkeypatch):
    """複合質問のAI_QUESTIONでも、状況の確認文は利用者向けに返さない。"""
    cid = client.post("/api/contacts", json={"name": "複合質問の宛先", "profile": ""}).json()["id"]
    client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "contact", "content": "あれどうなった？寿司屋って行ったことある？"},
    )

    calls = 0

    class WrongRecipientQuestionProvider:
        name = "wrong_recipient_question_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal calls
            calls += 1
            if calls == 1:
                return "[AI_QUESTION]どの件のことですか？[/AI_QUESTION]"
            return json.dumps({"replies": [
                "どの話のこと？",
                "どの件かな？",
                "何の話のこと？",
            ]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: WrongRecipientQuestionProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "wrong_recipient_question_fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })

    response = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})

    assert response.status_code == 200
    result = response.json()
    assert calls == 2
    assert "question" not in result
    assert len(result["replies"]) == 3


def test_auto_evaluation_persistence_failure_does_not_fail_saved_generation(client, monkeypatch):
    """自動評価の保存失敗だけで、保存済み返信をAPI上の生成失敗にしない。"""
    cid = client.post("/api/contacts", json={"name": "自動評価保存失敗テスト", "profile": "読書好き"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "本読んでます"})

    class ValidFakeProvider:
        name = "valid_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            return json.dumps({"replies": [
                "本いいですね！\n何読んでるんですか？",
                "読書好きなんですね！\nおすすめありますか？",
                "本いいなー！\n最近読めてないです笑\n何かおすすめありますか？",
            ]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: ValidFakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "valid_fake",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    })

    def fail_auto_evaluation_save(**kwargs):
        raise RuntimeError("simulated evaluation persistence failure")

    monkeypatch.setattr(generation, "_save_auto_evaluations", fail_auto_evaluation_save)
    response = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["replies"]) == 3
    assert len(payload["history_ids"]) == 3
    conn = generation.database.get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM generation_evaluations").fetchone()[0] == 0
        batch = conn.execute(
            "SELECT outcome FROM generation_batches WHERE id = ?",
            (payload["batch_id"],),
        ).fetchone()
        assert batch["outcome"] == "pending"
    finally:
        conn.close()


def test_generate_repair_failure_auto_clean_retries_and_graceful_fallback(client, monkeypatch):
    """全試行で違反候補しか得られない場合、候補も履歴も返さず構造化502にする。"""
    cid = client.post("/api/contacts", json={"name": "修復失敗テスト相手", "profile": "読書好き"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "本読んでます"})

    call_count = 0

    class BadFakeProvider:
        name = "bad_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal call_count
            call_count += 1
            # 常に空文字違反を返す
            return json.dumps({"replies": ["", "何読んでるんですか？", "気になります笑"]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: BadFakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "bad_fake",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    })

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 502
    payload = r.json()
    assert payload["detail"] == {
        "code": "candidate_validation_failed",
        "message": "生成結果が検証基準を満たしませんでした。条件を変えて再度お試しください。",
    }
    assert "replies" not in payload
    assert "history_ids" not in payload
    # 既存の生成試行・修復回数は維持される。
    assert call_count == 6
    conn = generation.database.get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0] == 0
        batch = conn.execute(
            "SELECT outcome FROM generation_batches ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert batch["outcome"] == "generation_failed"
    finally:
        conn.close()


def test_generate_rejects_ungrounded_status_candidates_without_saving_them(client, monkeypatch):
    """曖昧参照への状態断定・埋め草だけの候補は履歴へ保存しない。"""
    cid = client.post("/api/contacts", json={"name": "曖昧な状態テスト", "profile": ""}).json()["id"]
    client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "contact", "content": "あれどうなった？"},
    )

    call_count = 0

    class UngroundedStatusProvider:
        name = "ungrounded_status"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal call_count
            call_count += 1
            return json.dumps({"replies": [
                "まだ決まってないよ",
                "順調に進んでるよ",
                "ちょっと待ってね！",
            ]})

        def available_models(self):
            return []

    monkeypatch.setattr(
        "app.routers.generation.factory.get_provider",
        lambda *a, **k: UngroundedStatusProvider(),
    )
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "ungrounded_status",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    })

    response = client.post(
        "/api/generate",
        json={"contact_id": cid, "condition": "", "candidates": 3},
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "candidate_validation_failed"
    assert "replies" not in response.json()
    assert "history_ids" not in response.json()
    assert call_count == 6
    conn = generation.database.get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0] == 0
        batch = conn.execute(
            "SELECT outcome FROM generation_batches ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert batch["outcome"] == "generation_failed"
    finally:
        conn.close()


def test_generate_provider_error_marks_batch_failed(client, monkeypatch):
    """providerのHTTPエラーでもbatchを失敗終端にし、試行数と履歴を増やさない。"""
    cid = client.post("/api/contacts", json={"name": "provider失敗テスト", "profile": "読書好き"}).json()["id"]

    class FailingFakeProvider:
        name = "failing_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            raise AIError("provider failed", code="provider_error")

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: FailingFakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "failing_fake",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    })

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "provider_error"
    conn = generation.database.get_conn()
    try:
        batch = conn.execute(
            "SELECT outcome, attempt_no FROM generation_batches ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert batch["outcome"] == "generation_failed"
        assert batch["attempt_no"] == 1
        assert conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0] == 0
    finally:
        conn.close()


def test_batch_failure_update_does_not_hide_provider_http_error(client, monkeypatch):
    """失敗状態のDB更新が失敗しても、元のprovider由来HTTPエラーを維持する。"""
    cid = client.post("/api/contacts", json={"name": "失敗状態更新テスト", "profile": ""}).json()["id"]
    fail_next_connection = False
    original_get_conn = generation.database.get_conn

    def get_conn_with_one_failure():
        nonlocal fail_next_connection
        if fail_next_connection:
            fail_next_connection = False
            raise RuntimeError("simulated database connection failure")
        return original_get_conn()

    class FailingFakeProvider:
        name = "failing_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal fail_next_connection
            fail_next_connection = True
            raise AIError("provider failed", code="provider_error")

        def available_models(self):
            return []

    monkeypatch.setattr(generation.database, "get_conn", get_conn_with_one_failure)
    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: FailingFakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "failing_fake",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    })

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "provider_error"


def test_batch_creation_close_error_does_not_hide_commit_result(client, monkeypatch):
    """commit後のclose失敗ではbatch_idを返し、commit自体の失敗は隠さない。"""
    cid = client.post("/api/contacts", json={"name": "batch closeテスト", "profile": ""}).json()["id"]
    original_get_conn = generation.database.get_conn

    class CloseFailConnection:
        def __init__(self, conn, *, fail_commit=False):
            self._conn = conn
            self._fail_commit = fail_commit

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def commit(self):
            if self._fail_commit:
                raise RuntimeError("simulated commit failure")
            self._conn.commit()

        def close(self):
            self._conn.close()
            raise RuntimeError("simulated close failure")

    monkeypatch.setattr(
        generation.database,
        "get_conn",
        lambda: CloseFailConnection(original_get_conn()),
    )
    batch_id = generation._create_or_update_batch(
        contact_id=cid,
        trigger_message_id=None,
        condition="",
        revision_instruction="",
    )
    assert isinstance(batch_id, int)
    conn = original_get_conn()
    try:
        persisted = conn.execute(
            "SELECT outcome FROM generation_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        assert persisted["outcome"] == "pending"
    finally:
        conn.close()

    monkeypatch.setattr(
        generation.database,
        "get_conn",
        lambda: CloseFailConnection(original_get_conn(), fail_commit=True),
    )
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        generation._create_or_update_batch(
            contact_id=cid,
            trigger_message_id=None,
            condition="",
            revision_instruction="",
        )


def test_user_reply_pairs_construction_and_turn_aggregation(client):
    """全会話メッセージから連続turnを集約し、正しい (contact turn -> self turn) 教師ペアが構築されること。"""
    cid = client.post("/api/contacts", json={"name": "ペアテスト相手", "profile": ""}).json()["id"]

    # 連続 contact messages
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "はじめまして！"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェ巡りよく行きます！おすすめありますか？"})
    # 連続 self messages（実送信は「。」や1〜2文を含んでいても教師として保持される）
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "はじめまして。マッチありがとうございます。"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "渋谷のカフェによく行きます！"})

    pairs = generation.build_user_reply_pairs()
    assert len(pairs) >= 1
    p = [pair for pair in pairs if pair["contact_id"] == cid][0]

    assert "はじめまして！\nカフェ巡りよく行きます！おすすめありますか？" in p["contact_turn"]
    assert "はじめまして。マッチありがとうございます。\n渋谷のカフェによく行きます！" in p["self_turn"]


def test_user_learned_style_profile_calculation(client):
    """全送信メッセージからユーザースタイルプロファイル（文量、敬語率、記号、絵文字）が正しく算出されること。"""
    cid = client.post("/api/contacts", json={"name": "プロファイル相手", "profile": ""}).json()["id"]

    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "映画いいですね！\n最近何見ました？笑"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "コナン観てきました😊\n面白かったです！"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "ポップコーン食べましたよ笑"})

    profile = generation.analyze_user_learned_style(cid)
    assert profile["metrics"]["total_count"] >= 3
    wp = profile["weighted_profile"]
    assert wp["warai_ratio"] > 0
    assert wp["avg_emojis"] > 0
    assert "笑/w" in profile["summary"]
    assert "ユーザー実績スタイル" in profile["summary"]


def test_retrieved_reply_pairs_priority_and_scoring(client):
    """返信ペアの検索において、同一相手のペアが他相手のペアより優先されスコアが付与されること。"""
    cid_local = client.post("/api/contacts", json={"name": "ローカル相手", "profile": ""}).json()["id"]
    cid_global = client.post("/api/contacts", json={"name": "グローバル相手", "profile": ""}).json()["id"]

    # ローカル相手とのペア
    client.post(f"/api/contacts/{cid_local}/messages", json={"sender": "contact", "content": "週末は何して過ごしてますか？"})
    client.post(f"/api/contacts/{cid_local}/messages", json={"sender": "self", "content": "休日はよく映画観たりカフェ行ってます！"})

    # グローバル相手とのペア
    client.post(f"/api/contacts/{cid_global}/messages", json={"sender": "contact", "content": "映画好きなんですね！"})
    client.post(f"/api/contacts/{cid_global}/messages", json={"sender": "self", "content": "映画館で観るのが一番好きです笑"})

    # ローカル相手に対する生成時のペア検索
    retrieved, total_count = generation.retrieve_relevant_reply_pairs(
        cid_local,
        current_contact_turn="週末は何してますか？",
        condition="",
        max_pairs=5,
    )
    assert total_count >= 2
    assert len(retrieved) >= 1
    # 最上位は同一相手（local）のペアであること
    assert retrieved[0]["contact_id"] == cid_local
    assert retrieved[0]["score"] > 0.3
    assert "local" in retrieved[0]["scope"]


def test_counterpart_style_analysis_separation_and_metrics(client):
    """相手の文章スタイルの分析が contact ごとに完全分離され、各指標が正しく集計されること。"""
    cid_a = client.post("/api/contacts", json={"name": "敬語相手A", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid_a}/messages", json={
        "sender": "contact",
        "content": "15:19\nはじめまして！マッチありがとうございます。\nカフェ巡りがお好きなんですね！\nおすすめのお店はありますでしょうか？"
    })
    client.post(f"/api/contacts/{cid_a}/messages", json={
        "sender": "contact",
        "content": "ご連絡ありがとうございます。\n休日はよく読書をしています。\n普段はどのような本を読まれますか？"
    })

    cid_b = client.post("/api/contacts", json={"name": "タメ口相手B", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid_b}/messages", json={
        "sender": "contact",
        "content": "ヤッホー！マッチありがとー😊✨\n映画めっちゃ好きなんだけど最近何見たー？笑"
    })
    client.post(f"/api/contacts/{cid_b}/messages", json={
        "sender": "contact",
        "content": "それめっちゃ分かるwww\n今度おすすめ教えてー🙌"
    })

    style_a = generation.analyze_counterpart_style(cid_a)
    metrics_a = style_a["metrics"]
    assert metrics_a["sample_count"] == 2
    assert "敬語" in metrics_a["tone_label"]
    assert metrics_a["keigo_ratio"] >= 0.65
    assert metrics_a["avg_emojis"] == 0.0
    assert metrics_a["q_pct"] == 100
    assert "15:19" not in style_a["summary"]

    style_b = generation.analyze_counterpart_style(cid_b)
    metrics_b = style_b["metrics"]
    assert metrics_b["sample_count"] == 2
    assert "タメ口" in metrics_b["tone_label"]
    assert metrics_b["avg_emojis"] >= 1.0
    assert metrics_b["warai_pct"] == 100

    assert "タメ口" not in style_a["summary"]
    assert "丁寧な敬語" not in style_b["summary"]


def test_preview_includes_learning_first_data(client):
    """preview API に ユーザースタイルプロファイル、検索された返信ペア、相手スタイル、全件会話履歴が含まれること。"""
    cid = client.post("/api/contacts", json={"name": "プレビュー相手学習", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "こんにちは！よろしくお願いします😊"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "はじめまして！よろしくお願いします"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェよく行きます！おすすめありますか？"})

    r = client.post("/api/generate/preview", json={"contact_id": cid, "condition": ""})
    assert r.status_code == 200
    data = r.json()

    # pieces 内部のデータ検証
    assert "user_style_profile" in data
    assert "summary" in data["user_style_profile"]
    assert "retrieved_pairs" in data
    assert "total_reply_pairs_count" in data
    assert "counterpart_style" in data

    # system_prompt 内部のブロック存在確認
    assert "【USER LEARNED STYLE PROFILE】" in data["system_prompt"]
    assert "【RETRIEVED USER REPLY PAIRS】" in data["system_prompt"]
    assert "【COUNTERPART WRITING STYLE】" in data["system_prompt"]
