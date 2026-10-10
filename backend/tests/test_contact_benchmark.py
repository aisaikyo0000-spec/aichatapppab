"""Offline tests for Contact Bench signatures and contact-specific generation context."""
from __future__ import annotations

import json
import sys
from statistics import median
from pathlib import Path
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from app.ai import factory
from app.routers import generation
import run_contact_benchmark as contact_benchmark
from run_contact_benchmark import (
    CONTACTS,
    PROBE,
    build_contact_report_entry,
    contact_quality_status,
    load_contact_fixture,
    _finalize_contact_style_diagnostics,
    _new_contact_style_diagnostics,
    _reset_contact_repair_trace,
    seed_and_generate,
    style_sig,
)


def test_style_signature_detects_casual_laugh_and_length():
    assert style_sig("おつかれ笑") == {"len": 5, "tame": 1, "keigo": 0, "laugh": 1}


def test_style_signature_detects_polite_reply_without_laugh():
    assert style_sig("お疲れ様でした") == {"len": 7, "tame": 0, "keigo": 1, "laugh": 0}


def test_contact_benchmark_generates_same_probe_with_contact_specific_gold_context(
    client, monkeypatch
):
    captured_messages = []

    class FakeProvider:
        def generate(self, **kwargs):
            messages = kwargs["messages"]
            captured_messages.append(messages)
            system_prompt = messages[0]["content"]
            if "自分（手入力正解）: 新作気になる笑" in system_prompt:
                replies = [
                    "仕事おつかれ、しんどいよね笑",
                    "それは疲れるよね、ほんとおつかれ笑",
                    "仕事って疲れるよね",
                ]
            elif "自分（手入力正解）: 新作スイーツ気になりますね！美味しそうですね。" in system_prompt:
                replies = [
                    "お仕事お疲れ様です。大変でしたね。",
                    "仕事で疲れるときってありますよね。お疲れ様です。",
                    "お仕事お疲れ様です。",
                ]
            else:
                replies = [
                    "お仕事おつかれさま、しんどいね",
                    "お仕事お疲れ様です。大変ですよね",
                    "それは疲れちゃうよね、お疲れ様です！",
                ]
            return json.dumps(
                {"replies": replies},
                ensure_ascii=False,
            )

    original_get_ai_config = generation.get_ai_config
    original_instrumented_helpers = {
        "soft_issues": generation._contact_style_soft_repair_issues,
        "repair_messages": generation._build_repair_messages,
        "repair_improves": generation._contact_style_repair_improves,
        "parse_replies": generation._parse_replies_strict,
        "validate_candidates": generation.validate_candidate_replies,
    }
    monkeypatch.setattr(generation, "get_ai_config", original_get_ai_config)
    monkeypatch.setattr(factory, "get_provider", lambda *_args, **_kwargs: FakeProvider())
    expected_get_provider = factory.get_provider
    expected_generation_get_provider = generation.factory.get_provider

    result = seed_and_generate(
        client,
        key="test-only-key",
        model="gemini-3.5-flash-lite",
        delay_seconds=0,
        probe=PROBE,
    )

    assert generation._contact_style_soft_repair_issues is original_instrumented_helpers["soft_issues"]
    assert generation._build_repair_messages is original_instrumented_helpers["repair_messages"]
    assert generation._contact_style_repair_improves is original_instrumented_helpers["repair_improves"]
    assert generation._parse_replies_strict is original_instrumented_helpers["parse_replies"]
    assert generation.validate_candidate_replies is original_instrumented_helpers["validate_candidates"]
    assert generation.get_ai_config is original_get_ai_config
    assert factory.get_provider is expected_get_provider
    assert generation.factory.get_provider is expected_generation_get_provider

    assert list(result) == ["A", "B", "C"]
    for contact_result in result.values():
        diagnostics = contact_result["contact_style_diagnostics"]
        assert diagnostics["style_check_invocations"] >= 1
        assert diagnostics["style_check_status"] in {
            "mismatch_detected",
            "no_mismatch",
            "skipped_insufficient_same_contact_gold",
            "skipped_sensitive_context",
            "skipped_fewer_than_two_candidates",
        }
        assert diagnostics["initial_hard_violations_existed"] in (True, False)
        assert diagnostics["checker_profile"]["sample_count"] == 6
        assert diagnostics["same_contact_gold_profile"] == diagnostics["checker_profile"]
        assert diagnostics["observed_candidate_aggregates"]["candidate_count"] == 3
        assert diagnostics["sensitive_context_skip"] is False
        assert "replies" not in diagnostics
    assert len(captured_messages) >= len(CONTACTS)

    expected_style_examples = {
        "A": "自分（手入力正解）: 新作気になる笑",
        "B": "自分（手入力正解）: 新作スイーツ気になりますね！美味しそうですね。",
        "C": "自分（手入力正解）: それ気になる！どんな味だろう",
    }
    system_prompts = [messages[0]["content"] for messages in captured_messages]
    for name, expected_example in expected_style_examples.items():
        matching_prompts = [prompt for prompt in system_prompts if expected_example in prompt]
        assert matching_prompts
        prompt_text = matching_prompts[0]
        assert any(
            PROBE in message["content"]
            for messages in captured_messages
            if messages[0]["content"] == prompt_text
            for message in messages
        )
        assert "【SAME-CONTACT RECENT GOLD REPLIES】" in prompt_text
        assert expected_style_examples[name] in prompt_text
        matching_messages = [
            messages
            for messages in captured_messages
            if messages[0]["content"] == prompt_text
        ]
        assert matching_messages
        assert all(
            "【相手別Gold傾向の優先】" not in messages[1]["content"]
            for messages in matching_messages
        )
        assert all(
            example not in prompt_text
            for other_name, example in expected_style_examples.items()
            if other_name != name
        )
        assert result[name]["style_profile"]["gold_samples"] == 6
        assert result[name]["style_profile"]["tier"] == "same_contact_recent_manual_gold"

    profiles = {name: result[name]["style_profile"] for name in CONTACTS}
    assert profiles["A"]["length_median"] < profiles["C"]["length_median"]
    assert profiles["C"]["length_median"] < profiles["B"]["length_median"]
    assert profiles["A"]["tame_ratio"] > profiles["C"]["tame_ratio"]
    assert profiles["B"]["keigo_ratio"] > profiles["C"]["keigo_ratio"]

    returned_replies = {name: result[name]["replies"] for name in CONTACTS}
    signatures = {
        name: [style_sig(reply) for reply in replies]
        for name, replies in returned_replies.items()
    }
    assert median(signature["len"] for signature in signatures["A"]) < median(
        signature["len"] for signature in signatures["C"]
    )
    assert median(signature["len"] for signature in signatures["C"]) < median(
        signature["len"] for signature in signatures["B"]
    )
    assert sum(signature["tame"] for signature in signatures["A"]) >= 2
    assert all(signature["keigo"] for signature in signatures["B"])
    assert any(signature["tame"] for signature in signatures["C"])
    assert any(signature["keigo"] for signature in signatures["C"])
    assert all(
        reply not in {gold_reply for _, gold_reply in CONTACTS[name]}
        for name, replies in returned_replies.items()
        for reply in replies
    )


def test_contact_benchmark_repair_tracking_forwards_candidate_issues_keyword(
    client, monkeypatch
):
    """The diagnostic wrapper must preserve the production repair-gate argument."""
    pairs = [
        ("今日は忙しかった", "大変だったね笑"),
        ("カフェに行った", "いいね！"),
        ("新作気になる", "おいしそう笑"),
        ("週末は映画", "楽しそうだね"),
        ("雨降ってきた", "ほんとだね笑"),
        ("最近どう？", "ぼちぼちだよ笑"),
    ]
    baseline = [
        "仕事おつかれ、しんどいよね笑",
        "それは疲れるよね、ほんとおつかれ笑",
        "仕事って疲れるよね",
    ]
    fake_issues = [
        "同一相手Goldでは会話調の返信もよく使われています。候補群の会話調が不足しています。"
    ]

    class FakeProvider:
        def generate(self, **_kwargs):
            return json.dumps({"replies": baseline}, ensure_ascii=False)

    original_get_ai_config = generation.get_ai_config
    received_candidate_issues = []

    def repair_improves(
        _previous, _candidate, _context, _issues, *, candidate_issues=None
    ):
        received_candidate_issues.append(candidate_issues)
        return False

    monkeypatch.setattr(generation, "get_ai_config", original_get_ai_config)
    monkeypatch.setattr(factory, "get_provider", lambda *_a, **_k: FakeProvider())
    monkeypatch.setattr(
        generation, "_contact_style_soft_repair_issues", lambda *_a, **_k: fake_issues
    )
    monkeypatch.setattr(generation, "_contact_style_repair_improves", repair_improves)

    result = seed_and_generate(
        client,
        key="test-only-key",
        model="gemini-3.5-flash-lite",
        delay_seconds=0,
        probe=PROBE,
        contacts={"A": pairs},
    )

    assert "error" not in result["A"]
    diagnostics = result["A"]["contact_style_diagnostics"]
    assert diagnostics["repair_stages"]["reached_quality_gate"] is True
    assert diagnostics["repair_rejected"] == 1
    assert received_candidate_issues == [fake_issues]


def test_contact_benchmark_safe_reference_replacement_reports_final_repair_validation(
    client, monkeypatch
):
    calls = 0

    class SafeClarificationProvider:
        def generate(self, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                replies = [
                    "順調に進んでいますよ！",
                    "まだ決まっていません。",
                    "これから決める予定です！",
                ]
            else:
                replies = [
                    "え、どの件だっけ？",
                    "何のことだったっけ？",
                    "ちょっと待ってね！",
                ]
            return json.dumps({"replies": replies}, ensure_ascii=False)

    monkeypatch.setattr(generation, "get_ai_config", generation.get_ai_config)
    monkeypatch.setattr(factory, "get_provider", lambda *_args, **_kwargs: SafeClarificationProvider())
    monkeypatch.setattr(generation, "_contact_style_soft_repair_issues", lambda *_a, **_k: [])

    result = seed_and_generate(
        client,
        key="test-only-key",
        model="gemini-3.5-flash-lite",
        delay_seconds=0,
        probe="あれどうなった？",
        contacts={"A": CONTACTS["A"]},
    )

    assert calls == 2
    assert "error" not in result["A"]
    assert all(generation._is_short_counterpart_clarification(reply) for reply in result["A"]["replies"])
    diagnostics = result["A"]["contact_style_diagnostics"]
    assert diagnostics["final_hard_validation"] == {
        "stage": "hard_repair",
        "outcome": "passed",
    }
    hard_repair = diagnostics["hard_validation_rejections"]["hard_repair"]
    assert hard_repair["count"] >= 1
    assert "unresolved_reference" in hard_repair["categories"]
    assert hard_repair["last_outcome"] == "passed"
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "順調に進んでいます" not in serialized
    assert "何のことだったっけ" not in serialized


def test_contact_gold_fixture_requires_anonymous_a_b_c_groups_with_six_pairs(
    tmp_path,
):
    fixture = {
        "contacts": {
            label: [
                {"incoming": f"相手の発言{index}", "gold": f"自分の返信{label}{index}"}
                for index in range(6)
            ]
            for label in ("A", "B", "C")
        }
    }
    path = tmp_path / "contact-gold.json"
    path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")

    loaded = load_contact_fixture(path)

    assert list(loaded) == ["A", "B", "C"]
    assert all(len(pairs) == 6 for pairs in loaded.values())
    assert loaded["B"][0] == ("相手の発言0", "自分の返信B0")


def test_contact_gold_fixture_rejects_named_groups_and_insufficient_gold(tmp_path):
    named_fixture = {
        "contacts": {
            label: [{"incoming": "相手", "gold": "返信"} for _ in range(6)]
            for label in ("Alice", "Bob", "Carol")
        }
    }
    too_small_fixture = {
        "contacts": {
            label: [{"incoming": "相手", "gold": "返信"} for _ in range(5)]
            for label in ("A", "B", "C")
        }
    }
    named_path = tmp_path / "named.json"
    small_path = tmp_path / "small.json"
    named_path.write_text(json.dumps(named_fixture), encoding="utf-8")
    small_path.write_text(json.dumps(too_small_fixture), encoding="utf-8")

    with pytest.raises(ValueError, match="A, B, C"):
        load_contact_fixture(named_path)
    with pytest.raises(ValueError, match="six"):
        load_contact_fixture(small_path)


@pytest.mark.parametrize(
    ("path_kind", "payload", "message"),
    [
        ("missing", None, "could not be read"),
        ("invalid_json", "{", "could not be read"),
        (
            "malformed_pair",
            {"contacts": {label: ["not an object"] * 6 for label in ("A", "B", "C")}},
            "pairs must be objects",
        ),
        (
            "empty_message",
            {
                "contacts": {
                    label: [{"incoming": " ", "gold": "返信"}] * 6
                    for label in ("A", "B", "C")
                }
            },
            "empty incoming",
        ),
        (
            "empty_gold",
            {
                "contacts": {
                    label: [{"incoming": "相手", "gold": " "}] * 6
                    for label in ("A", "B", "C")
                }
            },
            "empty gold",
        ),
        (
            "overlong_message",
            {
                "contacts": {
                    label: [{"incoming": "相手", "gold": "返" * 2001}] * 6
                    for label in ("A", "B", "C")
                }
            },
            "over 2000",
        ),
        (
            "too_many_pairs",
            {
                "contacts": {
                    label: [{"incoming": "相手", "gold": "返信"}] * 13
                    for label in ("A", "B", "C")
                }
            },
            "at most 12",
        ),
        (
            "invalid_contacts_shape",
            {"contacts": ["A", "B", "C"]},
            "A, B, C",
        ),
        (
            "missing_contact",
            {"contacts": {"A": [], "B": [], "D": []}},
            "A, B, C",
        ),
    ],
)
def test_contact_gold_fixture_rejects_invalid_or_unbounded_input(
    tmp_path, path_kind, payload, message
):
    path = tmp_path / f"{path_kind}.json"
    if path_kind != "missing":
        path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match=message):
        load_contact_fixture(path)


def test_contact_benchmark_marks_generated_outputs_for_manual_quality_review():
    complete = contact_quality_status(generation_complete=True, expected_replies=9)
    incomplete = contact_quality_status(generation_complete=False, expected_replies=9)

    assert complete == {
        "status": "manual_review_required",
        "adaptation_pass": False,
        "expected_replies": 9,
    }
    assert incomplete == {
        "status": "generation_incomplete",
        "adaptation_pass": False,
        "expected_replies": 9,
    }


def test_contact_benchmark_report_records_successful_route_without_credentials():
    result = build_contact_report_entry(
        {
            "replies": ["返信1", "返信2", "返信3"],
            "style_profile": {"tier": "same_contact_recent_manual_gold"},
            "successful_route": {"account": "primary", "model": "gemini-3.5-flash-lite"},
            "models_used": ["gemini-3.5-flash-lite"],
        },
        gold_pairs=[("相手", "Gold")],
    )

    assert result["successful_route"] == {
        "account": "primary",
        "model": "gemini-3.5-flash-lite",
    }
    assert result["models_used"] == ["gemini-3.5-flash-lite"]
    assert "api_key" not in result


def test_contact_benchmark_reports_aggregates_for_final_replies_not_rejected_draft():
    stale_aggregates = contact_benchmark._safe_candidate_aggregates(
        ["古い案その1です", "古い案その2です", "古い案その3です"]
    )
    final_replies = ["短い！", "少しだけ丁寧に返す案です！", "中間の長さの返答です！"]
    result = build_contact_report_entry(
        {
            "replies": final_replies,
            "style_profile": {},
            "contact_style_diagnostics": {
                "observed_candidate_aggregates": stale_aggregates,
            },
        },
        gold_pairs=[("相手", "Gold")],
    )

    assert result["contact_style_diagnostics"]["observed_candidate_aggregates"] == (
        contact_benchmark._safe_candidate_aggregates(final_replies)
    )
    assert result["contact_style_diagnostics"]["observed_candidate_aggregates"] != stale_aggregates


def test_contact_benchmark_restores_provider_and_config_hooks_when_setup_fails():
    original_config = generation.get_ai_config
    original_provider = factory.get_provider
    original_generation_provider = generation.factory.get_provider

    class FailingClient:
        def post(self, *_args, **_kwargs):
            raise RuntimeError("fixture setup failed")

    with pytest.raises(RuntimeError, match="fixture setup failed"):
        seed_and_generate(
            FailingClient(),
            key="test-only-key",
            model="gemini-3.5-flash-lite",
            delay_seconds=0,
            probe=PROBE,
            contacts={"A": CONTACTS["A"]},
        )

    assert generation.get_ai_config is original_config
    assert factory.get_provider is original_provider
    assert generation.factory.get_provider is original_generation_provider


def test_contact_style_diagnostics_are_aggregate_only_and_report_repair_outcomes():
    diagnostics = _new_contact_style_diagnostics()
    diagnostics["soft_issues_detected"] = True
    diagnostics["issue_categories"].update({"length", "laugh_marker"})
    diagnostics["repair_attempted"] = 2
    diagnostics["repair_accepted"] = 1
    diagnostics["repair_rejected"] = 1
    diagnostics["rejection_reasons"].add("quality_gate_rejected")

    result = _finalize_contact_style_diagnostics(diagnostics)

    assert result["soft_issues_detected"] is True
    assert result["issue_categories"] == ["laugh_marker", "length"]
    assert result["repair_attempted"] == 2
    assert result["repair_accepted"] == 1
    assert result["repair_rejected"] == 1
    assert result["rejection_reasons"] == ["quality_gate_rejected"]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "candidate text" not in serialized
    assert "Gold text" not in serialized
    assert "prompt text" not in serialized
    assert "secret API key" not in serialized


def test_validation_and_quality_gate_rejections_keep_only_safe_categories():
    diagnostics = _new_contact_style_diagnostics()
    secret_fragments = [
        "PRIVATE_MESSAGE_A本人の経験を確認できる情報がありません",
        "PRIVATE_MESSAGE_B候補の参照先を確認できる情報がありません",
        "PRIVATE_MESSAGE_C本人の経験を確認できる情報がありません",
    ]
    for stage, violation in zip(
        ("initial", "hard_repair", "style_followup"), secret_fragments
    ):
        contact_benchmark._record_hard_validation_rejection(
            diagnostics, stage, [violation]
        )

    contact_benchmark._record_quality_gate_rejection(
        diagnostics, "style_repair", "mean_naturalness_regression"
    )
    contact_benchmark._record_quality_gate_rejection(
        diagnostics, "style_followup", "secondary_style_score_tradeoff"
    )
    contact_benchmark._record_hard_validation_rejection(
        diagnostics, "initial", ["UNIQUE_PRIVATE_VALIDATION_TEXT_should_not_escape"]
    )
    contact_benchmark._record_quality_gate_rejection(
        diagnostics, "style_repair", "UNIQUE_PRIVATE_QUALITY_TEXT_should_not_escape"
    )

    result = _finalize_contact_style_diagnostics(diagnostics)
    assert result["hard_validation_rejections"] == {
        "initial": {
            "count": 2,
            "categories": ["other_validation_violation", "unverified_self_experience"],
            "category_counts": {
                "other_validation_violation": 1,
                "unverified_self_experience": 1,
            },
            "candidate_category_counts": {},
            "last_outcome": "not_run",
        },
        "hard_repair": {
            "count": 1,
            "categories": ["unresolved_reference"],
            "category_counts": {"unresolved_reference": 1},
            "candidate_category_counts": {},
            "last_outcome": "not_run",
        },
        "style_repair": {
            "count": 0,
            "categories": [],
            "category_counts": {},
            "candidate_category_counts": {},
            "last_outcome": "not_run",
        },
        "style_followup": {
            "count": 1,
            "categories": ["unverified_self_experience"],
            "category_counts": {"unverified_self_experience": 1},
            "candidate_category_counts": {},
            "last_outcome": "not_run",
        },
    }
    assert result["quality_gate_rejections"] == {
        "style_repair": {
            "count": 2,
            "categories": ["mean_naturalness_regression", "unclassified_quality_rejection"],
        },
        "style_followup": {"count": 1, "categories": ["secondary_style_score_tradeoff"]},
    }
    serialized = json.dumps(result, ensure_ascii=False)
    for secret in secret_fragments:
        assert secret not in serialized
    assert "PRIVATE_MESSAGE_" not in serialized
    assert "本人の経験を確認できる情報" not in serialized
    assert "UNIQUE_PRIVATE_" not in serialized


@pytest.mark.parametrize(
    ("violation", "category"),
    [
        ("出力案数が 2 件です。必ず 3 件作成してください。", "reply_count"),
        ("案1が空文字です。", "empty_reply"),
        ("出力が正しいJSON形式または期待される案の形式になっていません。", "invalid_reply_structure"),
        ("案1にAI_QUESTIONタグ [AI_QUESTION] が混入しています。", "ai_question_tag"),
        ("1つの返信が3分割されて出力されています。", "fragmented_reply"),
        ("案1にタメ口指定と矛盾する敬語・丁寧語終止が含まれています。", "tone_conflict"),
        ("案1と案2の内容・表現が重複しています。", "duplicate_candidates"),
        ("案1に本人の経験を確認できる情報がありません。", "unverified_self_experience"),
        ("案1に本人の好みを確認できる情報がありません。", "unverified_self_preference"),
        ("案1に本人の予定・生活習慣を確認できる情報がありません。", "unverified_self_schedule"),
        ("案1が確認済みの予定と逆の空き状況を答えています。", "unverified_self_schedule"),
        ("案1に本人の未確認の希望を追加しています。", "unsupported_self_fact"),
        ("案1に本人の近況を確認できる情報がありません。", "unsupported_self_fact"),
        ("案1に仕事の状況を確認できる情報がありません。", "unsupported_work_context"),
        ("案1に相手の継続的な状況を確認できる情報がありません。", "unsupported_recurring_context"),
        ("案1に相手の負荷や疲れの理由を確認できる情報がありません。", "unsupported_effort_context"),
        ("案1に休日・休暇を確認できる情報がありません。", "unsupported_leave_context"),
        ("案1に催促や返信を問い詰める表現が含まれています。", "unsafe_pressure"),
        ("案1に状況を確認できる情報がありません。", "unsupported_status"),
        ("案1は質問を重ねすぎています。", "excessive_questioning"),
        ("案1は相手の状態を言い換えただけで新しい反応がありません。", "echo_or_repeat"),
        ("案1に助詞が抜けた不自然な表現があります。", "language_quality"),
    ],
)
def test_hard_validation_rule_families_map_to_fixed_categories(violation, category):
    diagnostics = _new_contact_style_diagnostics()

    contact_benchmark._record_hard_validation_rejection(
        diagnostics, "initial", [violation]
    )

    result = _finalize_contact_style_diagnostics(diagnostics)
    rejection = result["hard_validation_rejections"]["initial"]
    assert rejection["categories"] == [category]
    assert rejection["category_counts"] == {category: 1}
    assert violation not in json.dumps(result, ensure_ascii=False)


def test_hard_validation_category_counts_count_each_occurrence_without_text():
    diagnostics = _new_contact_style_diagnostics()
    violations = [
        "案1に仕事の状況を確認できる情報がありません。",
        "案2に勤務状況を確認できる情報がありません。",
    ]

    contact_benchmark._record_hard_validation_rejection(
        diagnostics, "hard_repair", violations
    )

    result = _finalize_contact_style_diagnostics(diagnostics)
    rejection = result["hard_validation_rejections"]["hard_repair"]
    assert rejection["count"] == 1
    assert rejection["categories"] == ["unsupported_work_context"]
    assert rejection["category_counts"] == {"unsupported_work_context": 2}
    serialized = json.dumps(result, ensure_ascii=False)
    assert all(violation not in serialized for violation in violations)


def test_hard_validation_counts_categories_per_candidate_without_text():
    diagnostics = _new_contact_style_diagnostics()
    violations = [
        "案1に仕事の状況を確認できる情報がありません。PRIVATE_REPLY_A",
        "案2に仕事の状況を確認できる情報がありません。PRIVATE_REPLY_B",
        "案2に質問を重ねすぎています。PRIVATE_REPLY_C",
        "案1と案3の内容・表現が重複しています。PRIVATE_REPLY_D",
        "入力から候補番号を抽出できない PRIVATE_REPLY_E 案1 案2",
        "案4と案5の内容・表現が重複しています。PRIVATE_REPLY_F",
    ]

    contact_benchmark._record_hard_validation_rejection(
        diagnostics, "style_repair", violations
    )

    result = _finalize_contact_style_diagnostics(diagnostics)
    rejection = result["hard_validation_rejections"]["style_repair"]
    assert rejection["category_counts"] == {
        "duplicate_candidates": 2,
        "other_validation_violation": 1,
        "unsupported_work_context": 2,
        "excessive_questioning": 1,
    }
    assert rejection["candidate_category_counts"] == {
        "1": {"duplicate_candidates": 1, "unsupported_work_context": 1},
        "2": {"excessive_questioning": 1, "unsupported_work_context": 1},
        "3": {"duplicate_candidates": 1},
    }
    serialized = json.dumps(result, ensure_ascii=False)
    for violation in violations:
        assert violation not in serialized
    assert "PRIVATE_REPLY_" not in serialized


@pytest.mark.parametrize(
    ("violation", "category"),
    [
        ("案1に実際の安全性を保証する表現があります。安全を断定しないでください。", "tapple_unsafe_safety_assurance"),
        ("案1に自宅・ホテル・個室などで会う提案があります。", "tapple_private_place"),
        ("案1に相手を侮辱する表現があります。", "tapple_disrespectful_reply"),
        ("案1に外部連絡先の交換や移動を促す表現があります。", "tapple_external_contact_request"),
        ("案1が会うことへの断り・迷い・安全面の懸念に対して考え直すよう求めています。", "tapple_pressure_after_hesitation"),
        ("案1に、会う誘いを返信文へ混ぜています。", "tapple_unpermitted_invitation"),
    ],
)
def test_tapple_validation_rule_families_map_to_fixed_categories(violation, category):
    diagnostics = _new_contact_style_diagnostics()

    contact_benchmark._record_hard_validation_rejection(
        diagnostics, "initial", [violation]
    )

    result = _finalize_contact_style_diagnostics(diagnostics)
    rejection = result["hard_validation_rejections"]["initial"]
    assert rejection["categories"] == [category]
    assert rejection["category_counts"] == {category: 1}
    assert violation not in json.dumps(result, ensure_ascii=False)


def test_contact_benchmark_style_summaries_are_numeric_and_text_free():
    from types import SimpleNamespace

    profile = SimpleNamespace(
        sample_count=6,
        keigo_ratio=0.25,
        hybrid_ratio=0.5,
        tame_ratio=0.25,
        laugh_ratio=0.5,
        char_median=22,
        char_p25=14,
        char_p75=30,
        frequent_emojis=["secret emoji"],
    )
    profile_summary = contact_benchmark._safe_style_profile_summary(profile)
    candidate_summary = contact_benchmark._safe_candidate_aggregates(
        ["短い笑", "丁寧に返します。"]
    )

    assert profile_summary == {
        "sample_count": 6,
        "keigo_ratio": 0.25,
        "hybrid_ratio": 0.5,
        "tame_ratio": 0.25,
        "laugh_ratio": 0.5,
        "char_median": 22,
        "char_p25": 14,
        "char_p75": 30,
    }
    assert candidate_summary["candidate_count"] == 2
    assert candidate_summary["tone_ratios"] == {
        "keigo": 0.5,
        "hybrid": 0.5,
        "tame": 0.0,
    }
    assert candidate_summary["laugh_ratio"] == 0.5
    assert set(candidate_summary["length"]) == {"mean_chars", "median_chars"}
    assert "secret emoji" not in json.dumps(profile_summary, ensure_ascii=False)
    assert "短い笑" not in json.dumps(candidate_summary, ensure_ascii=False)
    assert "丁寧に返します" not in json.dumps(candidate_summary, ensure_ascii=False)


def test_contact_style_diagnostics_mark_repairs_without_observed_decision():
    diagnostics = _new_contact_style_diagnostics()
    diagnostics["repair_attempted"] = 1

    result = _finalize_contact_style_diagnostics(diagnostics)

    assert result["repair_attempted"] == 1
    assert result["repair_accepted"] == 0
    assert result["repair_rejected"] == 0
    assert result["rejection_reasons"] == ["repair_did_not_reach_quality_decision"]
    assert result["style_check_invocations"] == 0
    assert result["style_check_status"] == "not_run"
    assert result["initial_hard_violations_existed"] is None
    assert result["repair_stages"] == {
        "reached_parse": False,
        "parse_succeeded": False,
        "reached_hard_validation": False,
        "hard_validation_passed": False,
        "reached_quality_gate": False,
    }


def test_contact_benchmark_resets_pending_repair_parse_state_at_contact_boundaries():
    state = {"pending_style_repair_parse": True}

    _reset_contact_repair_trace(state)

    assert state["pending_style_repair_parse"] is False


@pytest.mark.parametrize("probe", [" ", "x" * 2001])
def test_contact_benchmark_rejects_invalid_probe_before_setup_or_api(
    tmp_path, monkeypatch, probe
):
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_contact_benchmark.py", "--out", str(tmp_path / "result.json"), "--probe", probe],
    )

    def fail_if_setup_or_api_is_reached(*_args, **_kwargs):
        raise AssertionError("invalid probe must be rejected before setup or API work")

    monkeypatch.setattr(contact_benchmark, "read_gemini_api_key", fail_if_setup_or_api_is_reached)
    monkeypatch.setattr(contact_benchmark.database, "init_db", fail_if_setup_or_api_is_reached)
    monkeypatch.setattr(contact_benchmark, "seed_and_generate", fail_if_setup_or_api_is_reached)

    assert contact_benchmark.main() == 2


def test_contact_benchmark_restores_db_path_when_database_initialization_fails(
    tmp_path, monkeypatch
):
    original_db_path = Path("test-original-db.sqlite")
    monkeypatch.setattr(contact_benchmark.config, "DB_PATH", original_db_path)
    monkeypatch.setattr(contact_benchmark, "read_gemini_api_key", lambda _path: "test-key")
    monkeypatch.setattr(
        contact_benchmark.database,
        "init_db",
        lambda: (_ for _ in ()).throw(RuntimeError("init failed")),
    )
    db_path = tmp_path / "temporary.db"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_contact_benchmark.py",
            "--out",
            str(tmp_path / "report.json"),
            "--db",
            str(db_path),
        ],
    )

    with pytest.raises(RuntimeError, match="init failed"):
        contact_benchmark.main()

    assert contact_benchmark.config.DB_PATH == original_db_path


def test_contact_benchmark_artifact_does_not_include_probe_text(tmp_path, monkeypatch):
    probe = "private probe sentinel"
    out_path = tmp_path / "report.json"
    monkeypatch.setattr(contact_benchmark, "read_gemini_api_key", lambda _path: "test-key")
    monkeypatch.setattr(contact_benchmark.database, "init_db", lambda: None)
    monkeypatch.setattr(contact_benchmark, "TestClient", lambda _app: object())
    monkeypatch.setattr(
        contact_benchmark,
        "seed_and_generate",
        lambda *_args, **_kwargs: {
            name: {
                "replies": ["reply one", "reply two", "reply three"],
                "style_profile": {},
                "models_used": [],
                "successful_route": None,
            }
            for name in CONTACTS
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_contact_benchmark.py", "--out", str(out_path), "--probe", probe],
    )

    assert contact_benchmark.main() == 0
    artifact = out_path.read_text(encoding="utf-8")
    payload = json.loads(artifact)
    assert probe not in artifact
    assert payload["probe_char_count"] == len(probe)
    assert "probe" not in payload


def test_contact_gold_fixture_rejects_file_larger_than_bound(tmp_path):
    path = tmp_path / "too-large.json"
    path.write_bytes(b" " * 256_001)

    with pytest.raises(ValueError, match="exceeds 256000 bytes"):
        load_contact_fixture(path)


def test_contact_benchmark_keeps_supplied_fixture_gold_isolated_by_contact(
    client, monkeypatch
):
    captured_messages = []

    class FakeProvider:
        def generate(self, **kwargs):
            captured_messages.append(kwargs["messages"])
            return json.dumps(
                {"replies": ["ゆっくり休めそう？笑", "無理せず休んでくださいね", "そうなんだね！"]},
                ensure_ascii=False,
            )

    fixtures = {
        label: [
            (f"相手の話題{index}", f"Gold{label}固有の返信{index}")
            for index in range(6)
        ]
        for label in ("A", "B", "C")
    }
    monkeypatch.setattr(factory, "get_provider", lambda *_args, **_kwargs: FakeProvider())

    result = seed_and_generate(
        client,
        key="test-only-key",
        model="gemini-3.5-flash-lite",
        delay_seconds=0,
        probe=PROBE,
        contacts=fixtures,
    )

    assert list(result) == ["A", "B", "C"]
    for label, other_labels in {"A": ("B", "C"), "B": ("A", "C"), "C": ("A", "B")}.items():
        assert result[label]["successful_route"] == {
            "account": "primary",
            "model": "gemini-3.5-flash-lite",
        }
        assert result[label]["models_used"] == ["gemini-3.5-flash-lite"]
        matching_prompts = [
            messages[0]["content"]
            for messages in captured_messages
            if f"Gold{label}固有の返信" in messages[0]["content"]
        ]
        assert matching_prompts
        assert all(
            all(f"Gold{other}固有の返信" not in text for other in other_labels)
            for text in matching_prompts
        )
