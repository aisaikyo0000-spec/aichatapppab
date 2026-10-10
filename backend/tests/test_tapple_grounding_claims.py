from app.routers import generation


def test_grounding_does_not_mistake_future_invitation_for_recent_self_activity():
    history = [
        {"sender": "contact", "content": "あのカフェ気になります！"},
        {"sender": "self", "content": "僕もそのカフェ気になってます"},
        {"sender": "contact", "content": "今度そのカフェに行ってみたいです！"},
    ]

    violations = generation.validate_candidate_replies(
        ["僕も気になってるので今度そのカフェ一緒に行ってみませんか？"],
        expected_candidates=1,
        counterpart_message=history[-1]["content"],
        strategy_mode="tapple",
        tapple_action="continue",
        conversation_messages=history,
        known_self_facts=[],
    )

    assert not any("本人の近況を確認できる情報がありません" in item for item in violations)


def test_tapple_invite_integration_accepts_grounded_cafe_invitation():
    history = [
        {"sender": "contact", "content": "あのカフェ気になります！"},
        {"sender": "self", "content": "僕もそのカフェ気になってます"},
        {"sender": "contact", "content": "今度そのカフェに行ってみたいです！"},
    ]

    violations = generation.validate_candidate_replies(
        ["僕も気になってるので今度そのカフェ一緒に行ってみませんか？"],
        expected_candidates=1,
        counterpart_message=history[-1]["content"],
        strategy_mode="tapple",
        tapple_action="invite",
        conversation_messages=history,
        known_self_facts=[],
    )

    assert not any("本人の近況を確認できる情報がありません" in item for item in violations)
    assert not any("誘い先が相手の現在の話題と結びついていません" in item for item in violations)


def test_tapple_grounding_allows_acknowledging_counterpart_desire_without_claiming_own():
    counterpart = "今度そのカフェに行ってみたいです！"
    violations = generation.validate_candidate_replies(
        ["行ってみたいですよね！よかったら今度そのカフェに行ってみませんか？"],
        expected_candidates=1,
        counterpart_message=counterpart,
        strategy_mode="tapple",
        tapple_action="continue",
        conversation_messages=[{"sender": "contact", "content": counterpart}],
        known_self_facts=[],
    )

    assert not any("本人の未確認の希望を追加しています" in item for item in violations)


def test_tapple_grounding_still_rejects_unverified_first_person_desire():
    counterpart = "今度そのカフェに行ってみたいです！"
    violations = generation.validate_candidate_replies(
        ["僕もそのカフェに行ってみたいです！"],
        expected_candidates=1,
        counterpart_message=counterpart,
        strategy_mode="tapple",
        tapple_action="continue",
        conversation_messages=[{"sender": "contact", "content": counterpart}],
        known_self_facts=[],
    )

    assert any("本人の未確認の希望を追加しています" in item for item in violations)


def test_tapple_grounding_still_rejects_unverified_recent_self_activity():
    counterpart = "今度そのカフェに行ってみたいです！"
    violations = generation.validate_candidate_replies(
        ["僕も今日そのカフェに行ってきました！"],
        expected_candidates=1,
        counterpart_message=counterpart,
        strategy_mode="tapple",
        tapple_action="continue",
        conversation_messages=[{"sender": "contact", "content": counterpart}],
        known_self_facts=[],
    )

    assert any("本人の近況を確認できる情報がありません" in item for item in violations)
