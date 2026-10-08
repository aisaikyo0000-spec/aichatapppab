"""Offline tests for Contact Bench signatures and contact-specific generation context."""
from __future__ import annotations

import json
import sys
from statistics import median
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from app.ai import factory
from app.routers import generation
from run_contact_benchmark import CONTACTS, PROBE, seed_and_generate, style_sig


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
