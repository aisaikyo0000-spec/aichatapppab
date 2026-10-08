"""Opt-in Tapple strategy overlay safety and compatibility tests."""

import json

from app.ai import prompt
from app.routers.generation import (
    _build_repair_messages,
    _parse_replies_strict,
    _parse_tapple_strategy,
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
        "人の多いカフェのあと、ホテルのラウンジに行きませんか？",
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
