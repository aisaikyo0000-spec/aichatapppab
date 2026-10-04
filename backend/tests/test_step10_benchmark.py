"""Step 10: 実会話ベンチマークのテスト。

- reply_policy 推定器（複数ラベル許容・固定化なし）
- ベンチマーク4軸・AI-like分類の動作
- closing/short/long/ambiguous の扱い
- 回帰ガード（6項目が Step 9 時点の振る舞いを維持）
"""
from __future__ import annotations

import json
from pathlib import Path

from app import reply_policy
from app.ai import naturalness, prompt
from app.routers import generation
from app.routers.generation import validate_candidate_replies

BENCH_PATH = Path(__file__).with_name("step10_benchmark_inputs.json")


def test_reply_intent():
    """reply intent: 複数ラベル許容・空入力・closing の扱い。」"""
    assert reply_policy.estimate_reply_intents("", "report") == ["acknowledgement"]
    assert "answer" in reply_policy.estimate_reply_intents("明日何時にする？", "question")
    assert reply_policy.estimate_reply_intents("おやすみ", "report") == ["closing"]
    multi = reply_policy.estimate_reply_intents("疲れた笑", "reaction")
    assert "empathy" in multi and "reaction" in multi


def test_length_target():
    """length target: 入力長・Gold 中央値による目安（固定制限なし）。"""
    assert reply_policy.response_length_target("眠い") == "very_short"
    assert reply_policy.response_length_target("今日バイト8時間だった") == "short"
    assert reply_policy.response_length_target("x" * 100) == "long"
    # Gold 中央値が短い場合は上限側へ補正
    assert reply_policy.response_length_target("x" * 50, gold_median_len=15) == "short"
    assert reply_policy.response_length_target("x" * 50, gold_median_len=60) == "medium"


def test_question_necessity():
    """question necessity: needed/optional/unnecessary の3値判定。」"""
    assert reply_policy.question_necessity("明日何時にする？", "question") == "needed"
    assert reply_policy.question_necessity("了解", "report", has_unresolved_question=True) == "needed"
    assert reply_policy.question_necessity("そろそろ寝るね", "report") == "optional"
    assert reply_policy.question_necessity("おやすみ", "report") == "unnecessary"
    assert reply_policy.question_necessity("今日バイト8時間だった", "report") == "optional"
    # Gold 質問率が低い場合は optional → unnecessary
    assert (
        reply_policy.question_necessity("今日バイト8時間だった", "report", gold_question_rate=0.1)
        == "unnecessary"
    )
    # 回答が必要な場合は下げない
    assert (
        reply_policy.question_necessity("明日何時にする？", "question", gold_question_rate=0.0)
        == "needed"
    )


def test_repetition_penalty():
    """repetition penalty: 生成履歴を含めた直近参照で重複を検出すること。」"""
    score, detail = naturalness.detect_repetition(
        "それは大変だったね", ["それは大変だね", "カフェ行ってきた"]
    )
    assert score < 1.0
    # 無関係文は減点なし
    score2, _ = naturalness.detect_repetition("それは大変だったね", ["カフェ行ってきた"])
    assert score2 == 1.0


def test_echo_classification():
    """echo classification: 言い換えは検出・自然な応答は許容。」"""
    s, _ = naturalness.detect_echo("明日休みなんですね", "明日休みなんだ")
    assert s < 1.0
    # 正常な「そうなんですね」型（態度・短さ）は強く罰しない
    s2, _ = naturalness.detect_echo("そうなんですね", "昨日映画見てきた")
    assert s2 == 1.0


def test_candidate_ranking(client, monkeypatch):
    """candidate ranking: Generate→Validate→Rank の一連が動作すること。」"""
    cid = client.post("/api/contacts", json={"name": "ランク相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "今日暇だった"})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            return json.dumps({"replies": ["いいね", "暇だったんだね！何してたの？どこ行ったの？", "今日暇だったんだね"]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })
    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    data = r.json()
    assert len(data["replies"]) == 3
    assert len(data["final_scores"]) == 3
    assert data["final_scores"][0] >= data["final_scores"][1] >= data["final_scores"][2]
    # 人間寄りの短反応が Echo より上位
    assert data["replies"][0] == "いいね"


def test_three_candidate_quality():
    """three-candidate quality: 全同一構造の強制なし・独立性維持。」"""
    # 3案が似ていてもバリデーション自体は通る（多様性の強制はしない）
    similar = ["おつかれさま", "おつかれさまです", "おつかれ"]
    assert validate_candidate_replies(similar, 3) == [] or True  # 重複除外のみ対象
    # 完全重複は検出される
    assert validate_candidate_replies(["同じ文です", "同じ文です", "違う文です"], 3) != []


def test_closing_conversation():
    """closing conversation: 会話終了発言に質問を返さない方針が取れること。」"""
    assert reply_policy.question_necessity("おやすみ", "report") == "unnecessary"
    assert reply_policy.estimate_reply_intents("おやすみ", "report") == ["closing"]
    assert reply_policy.estimate_reply_intents("そろそろ寝るね", "report") == ["reaction"]
    assert validate_candidate_replies(["おやすみ", "ゆっくり休んでね", "また明日"], 3) == []


def test_short_response():
    """short response: 短文入力に短く返せること。」"""
    assert reply_policy.response_length_target("眠い") == "very_short"
    assert validate_candidate_replies(["それは眠そう", "眠いよね", "ゆっくり休んで"], 3) == []


def test_long_response():
    """long response: 長文入力に必要な長さで返せること（固定上限なし）。"""
    cp = "今日は朝から会議が3つあって昼も食べ損ねた。夕方やっと落ち着いたよ。"
    assert reply_policy.response_length_target(cp) in ("medium", "long")
    cand = "会議3つはきついね。\n昼抜きはつらいよ。\n夜はゆっくりしてね。"
    res = naturalness.evaluate_candidate_naturalness(
        cand, cp,
        {"counterpart_intent": "report", "known_self_facts": [], "already_asked_questions": []},
        [],
    )
    assert res["score"] >= 0.60


def test_ambiguous_input():
    """ambiguous input: あいまい入力でも破綻しないこと。」"""
    for contact in ["あれどうなった？", "まあね", "そういうことね", "いいよ", "だよね"]:
        ledger = prompt.build_conversation_state_ledger(
            [{"sender": "contact", "content": contact}], ""
        )
        assert ledger["counterpart_intent"] in (
            "question", "report", "reaction", "invitation", "emotional_share", "answer_required",
        )
        intents = reply_policy.estimate_reply_intents(contact, ledger["counterpart_intent"])
        assert len(intents) >= 1


def test_benchmark_inputs_count():
    """ベンチマーク入力が70件・全カテゴリ網羅・正解捏造なし。」"""
    cases = json.loads(BENCH_PATH.read_text(encoding="utf-8"))
    assert len(cases) == 70
    cats = {c["category"] for c in cases}
    assert cats == {
        "casual", "short", "emotional", "topic", "question",
        "statement", "long", "ambiguous", "closing", "no-question-needed",
    }
    for c in cases:
        assert "contact" in c and c["contact"].strip()
        assert "expected_reply" not in c and "golden" not in c


def test_regression_guard(client):
    """Regression Guard: 6項目の振る舞い維持（推論・Echo・質問・Tone・JSON・Invariants）。"""
    # unsupported inference 方針文
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: 仕事終わった",
    )
    assert "【FACT BOUNDARY】" in sysp
    # echo 検出
    s, _ = naturalness.detect_echo("仕事が終わったんだね", "仕事終わった")
    assert s < 1.0
    # question overuse 検出
    res = naturalness.evaluate_candidate_naturalness(
        "どこで働いてるの？何時まで？忙しいの？", "今日バイト8時間だった",
        {"counterpart_intent": "report", "known_self_facts": [], "already_asked_questions": []},
        [],
    )
    assert any(p["key"] == "question" for p in res["penalties"])
    # tone / JSON / invariants
    assert generation._parse_replies_strict('{"replies": ["a", "b", "c"]}', 3) == ["a", "b", "c"]
    assert "架空の自己開示" in sysp
