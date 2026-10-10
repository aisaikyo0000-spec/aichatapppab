"""Activity grounding for invitations in elliptical Tapple conversations."""

from app.routers.generation import validate_candidate_replies


def _invite_violations(reply: str, history: list[dict[str, str]]) -> list[str]:
    return validate_candidate_replies(
        [reply],
        expected_candidates=1,
        counterpart_message=history[-1]["content"],
        conversation_messages=history,
        strategy_mode="tapple",
        tapple_action="invite",
    )


def test_elliptical_activity_uses_nearest_prior_interest_across_filler_turn():
    history = [
        {"sender": "contact", "content": "陶芸に興味があります"},
        {"sender": "self", "content": "陶芸楽しそうですね！"},
        {"sender": "contact", "content": "そうなんですね"},
        {"sender": "self", "content": "体験してみたいですか？"},
        {"sender": "contact", "content": "体験してみたいです！"},
    ]

    violations = _invite_violations("今度一緒に映画を見に行きませんか？", history)

    assert any("誘い先" in violation for violation in violations)
    assert not any("strategy.action=invite" in violation for violation in violations)


def test_elliptical_activity_does_not_rebind_to_an_older_topic_after_new_interest():
    history = [
        {"sender": "contact", "content": "陶芸に興味があります"},
        {"sender": "self", "content": "陶芸楽しそうですね！"},
        {"sender": "contact", "content": "映画に興味があります！"},
        {"sender": "self", "content": "映画いいですね！"},
        {"sender": "contact", "content": "見てみたいです！"},
    ]

    violations = _invite_violations("今度一緒に陶芸を体験しませんか？", history)

    assert any("誘い先" in violation for violation in violations)
    assert not any("strategy.action=invite" in violation for violation in violations)
    assert not any(
        "誘い先" in violation
        for violation in _invite_violations("今度一緒に映画を見に行きませんか？", history)
    )


def test_elliptical_activity_without_resolvable_history_rejects_named_unrelated_target():
    history = [
        {"sender": "contact", "content": "体験してみたいです！"},
    ]

    violations = _invite_violations("今度一緒に映画を見に行きませんか？", history)

    assert any("誘い先" in violation for violation in violations)
    assert not any("strategy.action=invite" in violation for violation in violations)


def test_elliptical_activity_can_keep_a_direct_anaphoric_invitation_sendable():
    history = [
        {"sender": "contact", "content": "陶芸に興味があります"},
        {"sender": "self", "content": "陶芸楽しそうですね！"},
        {"sender": "contact", "content": "そうなんですね"},
        {"sender": "self", "content": "体験してみたいですか？"},
        {"sender": "contact", "content": "体験してみたいです！"},
    ]

    violations = _invite_violations("今度一緒に体験しに行きませんか？", history)

    assert not any("誘い先" in violation for violation in violations)
    assert not any("strategy.action=invite" in violation for violation in violations)


def test_elliptical_reply_can_resolve_activity_from_the_immediately_prior_self_suggestion():
    history = [
        {"sender": "contact", "content": "陶芸に興味があります"},
        {"sender": "self", "content": "陶芸いいですね！"},
        {"sender": "contact", "content": "そうなんですね"},
        {"sender": "self", "content": "今度は映画を一緒に見てみたいですね"},
        {"sender": "contact", "content": "見てみたいです！"},
    ]

    violations = _invite_violations("今度一緒に映画を見に行きませんか？", history)

    assert not any("誘い先" in violation for violation in violations)
    assert any(
        "誘い先" in violation
        for violation in _invite_violations("今度一緒に陶芸を体験しませんか？", history)
    )


def test_recent_explicit_disinterest_does_not_rebind_elliptical_activity_to_old_interest():
    history = [
        {"sender": "contact", "content": "カフェ巡りが好きです"},
        {"sender": "self", "content": "カフェ巡り楽しそうですね！"},
        {"sender": "contact", "content": "カフェはあまり好きではないです"},
        {"sender": "self", "content": "そうなんですね"},
        {"sender": "contact", "content": "体験してみたいです！"},
    ]

    violations = _invite_violations("今度一緒にカフェへ行きませんか？", history)

    assert any("誘い先" in violation for violation in violations)


def test_current_explicit_disinterest_blocks_invitation_to_same_activity():
    history = [
        {"sender": "contact", "content": "映画はあまり好きではないです。"},
    ]

    violations = _invite_violations("よかったら今度映画を見に行きませんか？", history)

    assert any("誘い先" in violation for violation in violations)


def test_current_disinterest_wins_over_a_different_positive_activity_in_same_turn():
    history = [
        {"sender": "contact", "content": "カフェ巡りが好きです"},
        {"sender": "self", "content": "カフェ巡りいいですね！"},
        {
            "sender": "contact",
            "content": "カフェはあまり好きじゃないです。最近は映画をよく見ます。",
        },
    ]

    violations = _invite_violations("よかったら今度カフェに行きませんか？", history)

    assert any("誘い先" in violation for violation in violations)


def test_current_mixed_interest_uses_positive_activity_without_inviting_disliked_one():
    history = [
        {
            "sender": "contact",
            "content": "カフェはあまり好きじゃないけど、陶芸体験してみたいです！",
        },
    ]

    assert any(
        "誘い先" in violation
        for violation in _invite_violations("今度カフェに行きませんか？", history)
    )
    assert not any(
        "誘い先" in violation
        for violation in _invite_violations("今度陶芸を体験しませんか？", history)
    )


def test_later_current_interest_overrides_past_disinterest_for_same_activity():
    histories_and_replies = [
        (
            "カフェは苦手でしたが、最近はまた気になって今度行ってみたいです！",
            "今度カフェへ行きませんか？",
        ),
        (
            "昔は映画が苦手でしたが、今は気になっていて見てみたいです！",
            "今度映画を見に行きませんか？",
        ),
    ]

    for counterpart_message, reply in histories_and_replies:
        history = [{"sender": "contact", "content": counterpart_message}]
        violations = _invite_violations(reply, history)
        assert not any("誘い先" in violation for violation in violations)


def test_persistent_current_disinterest_still_blocks_same_activity_invitation():
    history = [
        {"sender": "contact", "content": "昔からカフェが苦手で、今も気になりません。"},
    ]

    violations = _invite_violations("今度カフェへ行きませんか？", history)

    assert any("誘い先" in violation for violation in violations)


def test_anaphoric_go_proposal_matches_the_current_activity():
    history = [
        {"sender": "contact", "content": "今度そのカフェに行ってみたいです！"},
    ]

    for reply in (
        "ぜひ今度行ってみましょう！",
        "ぜひ今度一緒に行ってみましょう！",
    ):
        violations = _invite_violations(reply, history)
        assert not any("誘い先" in violation for violation in violations)
