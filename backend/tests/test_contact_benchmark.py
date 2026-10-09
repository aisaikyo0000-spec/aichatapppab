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
    contact_quality_status,
    load_contact_fixture,
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
            if "【タメ口】" in system_prompt:
                replies = [
                    "おつかれ、今週大変だったね笑",
                    "今週ずっと忙しかったんだね、今日はゆっくり休んでね",
                    "それはしんどいね。週末は少し休めそう？",
                ]
            elif "【敬語】" in system_prompt:
                replies = [
                    "お仕事お疲れ様です。今日はゆっくり休んでくださいね。",
                    "今週はずっとお忙しかったんですね。週末は少し休めそうですか？",
                    "それは大変でしたね。無理せず過ごしてください。",
                ]
            else:
                replies = [
                    "それは大変だったね。今日はゆっくり休んで、できるだけ身体を休めてね。",
                    "今週は忙しかったんですね。週末は休めそうですか？",
                    "今週忙しかったんだね、お疲れ様！今日は休めそう？",
                ]
            return json.dumps(
                {"replies": replies},
                ensure_ascii=False,
            )

    original_get_ai_config = generation.get_ai_config
    monkeypatch.setattr(generation, "get_ai_config", original_get_ai_config)
    monkeypatch.setattr(factory, "get_provider", lambda *_args, **_kwargs: FakeProvider())

    result = seed_and_generate(
        client,
        key="test-only-key",
        model="gemini-3.5-flash-lite",
        delay_seconds=0,
        probe=PROBE,
    )

    assert list(result) == ["A", "B", "C"]
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
                {"replies": ["今日はゆっくり休めそう？笑", "無理せず休んでくださいね", "そうなんだね！"]},
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
