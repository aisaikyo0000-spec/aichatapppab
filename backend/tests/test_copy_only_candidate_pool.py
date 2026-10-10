"""Tests for request-local salvage of candidates lost only to Gold-copy rejection."""

import json

from app.routers import generation


COPY_ONLY = ["案2が過去のGold返信をコピーしています。"]


def test_pool_collects_only_individually_hard_valid_candidates(monkeypatch):
    calls = []
    context = {
        "tone": "tame",
        "mode": "normal",
        "counterpart_message": "仕事で疲れた",
        "known_self_facts": ["好きな食べ物はカレー"],
    }

    def validate(replies, expected_candidates=3, **kwargs):
        calls.append((replies, expected_candidates, kwargs))
        return ["copy"] if replies == ["Gold copy"] else []

    monkeypatch.setattr(generation, "validate_candidate_replies", validate)
    pool = []

    generation._collect_gold_copy_salvage_candidates(
        ["おつかれ笑", "Gold copy", "ゆっくり休んでくださいね"],
        COPY_ONLY,
        expected_candidates=3,
        validation_kwargs=context,
        candidate_pool=pool,
        strategy_mode="none",
        style_fallback_active=False,
    )

    assert pool == ["おつかれ笑", "ゆっくり休んでくださいね"]
    assert [call[0] for call in calls] == [["おつかれ笑"], ["Gold copy"], ["ゆっくり休んでくださいね"]]
    assert all(call[1] == 1 and call[2] == context for call in calls)


def test_pool_rejects_mixed_count_tapple_and_style_fallback_batches(monkeypatch):
    def unexpected_validation(*_args, **_kwargs):
        raise AssertionError("ineligible batch must not be singleton-validated")

    monkeypatch.setattr(generation, "validate_candidate_replies", unexpected_validation)
    cases = [
        (["a", "b", "c"], [*COPY_ONLY, "案1に未確認情報があります"], "none", False),
        (["a", "b"], COPY_ONLY, "none", False),
        (["a", "b", "c"], COPY_ONLY, "tapple", False),
        (["a", "b", "c"], COPY_ONLY, "none", True),
    ]

    for replies, violations, strategy_mode, style_fallback_active in cases:
        pool = []
        generation._collect_gold_copy_salvage_candidates(
            replies,
            violations,
            expected_candidates=3,
            validation_kwargs={"mode": "normal"},
            candidate_pool=pool,
            strategy_mode=strategy_mode,
            style_fallback_active=style_fallback_active,
        )
        assert pool == []


def test_candidate_pool_is_request_local_ordered_and_deduplicated():
    pool = []
    context = {"mode": "normal"}

    def accept_all(replies, expected_candidates=3, **_kwargs):
        assert expected_candidates == 1
        return []

    original = generation.validate_candidate_replies
    generation.validate_candidate_replies = accept_all
    try:
        generation._collect_gold_copy_salvage_candidates(
            ["same", "first attempt", "copy"], COPY_ONLY, expected_candidates=3,
            validation_kwargs=context, candidate_pool=pool, strategy_mode="none",
            style_fallback_active=False,
        )
        generation._collect_gold_copy_salvage_candidates(
            ["same", "second attempt", "copy"], COPY_ONLY, expected_candidates=3,
            validation_kwargs=context, candidate_pool=pool, strategy_mode="none",
            style_fallback_active=False,
        )
    finally:
        generation.validate_candidate_replies = original

    assert pool == ["same", "first attempt", "copy", "second attempt"]


def test_pool_excludes_individual_candidates_with_unsupported_self_claims(monkeypatch):
    def validate(replies, expected_candidates=3, **_kwargs):
        assert expected_candidates == 1
        return ["unsupported personal fact"] if replies == ["私は毎週旅行します"] else []

    monkeypatch.setattr(generation, "validate_candidate_replies", validate)
    pool = []
    generation._collect_gold_copy_salvage_candidates(
        ["おつかれ笑", "私は毎週旅行します", "ゆっくり休んでくださいね"],
        COPY_ONLY,
        expected_candidates=3,
        validation_kwargs={"mode": "normal"},
        candidate_pool=pool,
        strategy_mode="none",
        style_fallback_active=False,
    )

    assert pool == ["おつかれ笑", "ゆっくり休んでくださいね"]


def test_pool_batch_is_returned_only_after_full_expected_count_revalidation(monkeypatch):
    calls = []
    context = {"mode": "normal", "tone": "tame"}

    def validate(replies, expected_candidates=3, **kwargs):
        calls.append((replies, expected_candidates, kwargs))
        return [] if replies == ["one", "two", "three"] else ["candidate overlap"]

    monkeypatch.setattr(generation, "validate_candidate_replies", validate)
    pool = ["one", "two"]
    assert generation._validated_gold_copy_salvage_batch(
        pool, expected_candidates=3, validation_kwargs=context
    ) is None
    assert calls == []

    pool.append("three")
    assert generation._validated_gold_copy_salvage_batch(
        pool, expected_candidates=3, validation_kwargs=context
    ) == ["one", "two", "three"]
    assert calls == [(["one", "two", "three"], 3, context)]

    pool[2] = "overlapping third"
    assert generation._validated_gold_copy_salvage_batch(
        pool, expected_candidates=3, validation_kwargs=context
    ) is None
    assert calls[-1] == (["one", "two", "overlapping third"], 3, context)


def test_pool_batch_that_fails_full_duplicate_check_is_not_salvaged(monkeypatch):
    calls = []

    def validate(replies, expected_candidates=3, **_kwargs):
        calls.append((replies, expected_candidates))
        return ["candidate duplicate"] if replies[0] == replies[2] else []

    monkeypatch.setattr(generation, "validate_candidate_replies", validate)
    pool = ["same", "different", "same"]

    assert generation._validated_gold_copy_salvage_batch(
        pool, expected_candidates=3, validation_kwargs={"mode": "normal"}
    ) is None
    assert calls == [(["same", "different", "same"], 3)]


def test_three_attempts_salvage_repair_candidates_without_extra_ai_calls(client, monkeypatch):
    contact_id = client.post(
        "/api/contacts", json={"name": "候補統合テスト", "profile": ""}
    ).json()["id"]
    client.post(
        f"/api/contacts/{contact_id}/messages",
        json={"sender": "contact", "content": "仕事で疲れた"},
    )
    ai_calls = []

    class FakeProvider:
        name = "copy_pool_test"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            del model, messages, temperature, max_tokens, json_mode
            ai_calls.append(None)
            attempt = (len(ai_calls) + 1) // 2
            if len(ai_calls) % 2:
                return json.dumps({"replies": [
                    f"initial-{attempt}-a", f"initial-{attempt}-b", f"initial-{attempt}-c"
                ]})
            return json.dumps({"replies": [
                f"safe-{attempt}-a", f"COPIED-GOLD-{attempt}", f"safe-{attempt}-b"
            ]})

        def available_models(self):
            return []

    def fake_get_provider(*_args, **_kwargs):
        return FakeProvider()

    def staged_validator(replies, expected_candidates=3, **_kwargs):
        if expected_candidates == 1:
            return ["過去のGold返信をコピーしています"] if replies[0].startswith("COPIED-GOLD") else []
        if any(reply.startswith("initial-") for reply in replies):
            return ["案1に相手の時間情報を確認できる情報がありません。"]
        if any(reply.startswith("COPIED-GOLD") for reply in replies):
            return ["案2が過去のGold返信をコピーしています。"]
        return []

    monkeypatch.setattr(generation.factory, "get_provider", fake_get_provider)
    monkeypatch.setattr(generation, "validate_candidate_replies", staged_validator)
    monkeypatch.setattr(generation, "get_ai_config", lambda: {
        "provider": "copy_pool_test", "model": "fake-model", "api_key": "test",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })

    response = client.post(
        "/api/generate",
        json={"contact_id": contact_id, "condition": "", "candidates": 3},
    )

    assert response.status_code == 200
    assert len(ai_calls) == 6
    replies = response.json()["replies"]
    assert len(replies) == 3
    assert all("COPIED-GOLD" not in reply and "initial-" not in reply for reply in replies)
    assert set(replies) == {"safe-1-a", "safe-1-b", "safe-2-a"}


def test_existing_gold_copy_validator_still_rejects_exact_reference():
    reference = "新作スイーツ気になりますね！美味しそうですね。"
    violations = generation.validate_candidate_replies(
        [reference],
        expected_candidates=1,
        style_reference_replies=[reference],
    )

    assert violations == [
        "案1が過去のGold返信をコピーしています。内容は引き継ぎつつ、今回の会話に合わせて独立した文面にしてください。"
    ]
