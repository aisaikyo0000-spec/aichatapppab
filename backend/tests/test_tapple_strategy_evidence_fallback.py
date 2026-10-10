import json

from app.routers.generation import (
    _build_safe_tapple_fallback_strategy,
    _parse_tapple_strategy,
)


def test_safe_fallback_quotes_the_complete_decline_sentence():
    contact_message = "ごめんなさい、今は会うのは難しいです。カフェの話は楽しいです。"
    conversation = [{"sender": "contact", "content": contact_message}]

    strategy = _build_safe_tapple_fallback_strategy(conversation)

    assert strategy is not None
    assert strategy.action == "stop"
    assert strategy.evidence == ["ごめんなさい、今は会うのは難しいです。"]
    assert strategy.evidence[0] in contact_message


def test_parsed_decline_override_quotes_the_complete_decline_sentence():
    decline_sentence = "ごめんなさい、今は会うのは難しいです。"
    conversation = [{"sender": "contact", "content": decline_sentence}]
    raw = json.dumps(
        {
            "strategy": {
                "action": "wait",
                "rationale": "今は反応を待ちます。",
                "evidence": [decline_sentence],
                "invite_example": None,
            }
        },
        ensure_ascii=False,
    )

    strategy = _parse_tapple_strategy(raw, conversation)

    assert strategy is not None
    assert strategy.action == "stop"
    assert strategy.evidence == [decline_sentence]
    assert strategy.evidence[0] in decline_sentence
