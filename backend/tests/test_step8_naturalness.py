"""Step 8: 実LLM自然性改善のテスト（Tests 1〜10）。

方針:
- 大量の固定禁止ルールは追加しない（検証は方針文の存在＋既存検証の維持）
- Gold の扱い・Hard Invariants・JSON契約は不変であることを確認する
"""
from __future__ import annotations

from app.ai import naturalness, prompt
from app.learning import style
from app.routers.generation import _is_fragmented_split, validate_candidate_replies


def _sysp(contact="今日疲れた", ledger=None):
    return prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text=f"相手: {contact}",
        conversation_ledger=ledger
        or {
            "current_topic": contact,
            "last_contact_message": contact,
            "counterpart_intent": "report",
            "unresolved_question": None,
            "already_asked_questions": [],
        },
    )


def test_1_fact_boundary_block():
    """Test 1: FACT BOUNDARY が Prompt に含まれること。"""
    sysp = _sysp()
    assert "【FACT BOUNDARY】" in sysp
    assert "具体的事実" in sysp
    assert "勝手に確定しない" in sysp
    assert "自然な感情反応" in sysp


def test_2_unsupported_inference_policy():
    """Test 2: 推測の事実化を禁じ、未知情報は質問で尋ねる方針があること。"""
    sysp = _sysp()
    assert "推測を事実として文章化しない" in sysp
    assert "断定ではなく質問として尋ねる" in sysp
    # サイレント自己チェック（結果出力なし）が user 指示にあること
    msgs = prompt.build_initial_generation_messages(system_prompt=sysp, candidates=3)
    assert "黙って確認" in msgs[1]["content"]
    assert "チェック結果自体は出力しない" in msgs[1]["content"]


def test_3_no_question_strategy():
    """Test 3: NO QUESTION が正式な戦略として許可されること。"""
    sysp = _sysp()
    assert "NO QUESTION" in sysp
    replies = ["おつかれさまです", "それは疲れますね", "ゆっくり休んでください"]
    assert validate_candidate_replies(replies, 3) == []


def test_4_short_message_handling():
    """Test 4: うん/そう/笑/わかる/疲れた/眠いに短く返せること。"""
    for contact in ["うん", "そう", "笑", "わかる", "疲れた", "眠い"]:
        ledger = prompt.build_conversation_state_ledger(
            [{"sender": "contact", "content": contact}], ""
        )
        tier = prompt.classify_message_length(contact)
        sysp = prompt.build_system_prompt(
            contact={"name": "相手", "profile": ""},
            condition="",
            chat_history_text=f"相手: {contact}",
            conversation_ledger=ledger,
            counterpart_length_tier=tier,
            counterpart_length_chars=len(contact),
        )
        assert "短い候補" in sysp


def test_5_echo_prevention():
    """Test 5: 単純 Echo（明日休みなんですね型）を検出すること。"""
    score, detail = naturalness.detect_echo("明日休みなんですね", "明日休みなんだ")
    assert score < 1.0
    # 自然な部分的引用（態度つき）は強く罰しない
    score2, _ = naturalness.detect_echo("札幌いいな", "昨日友達と札幌行ってきた")
    assert score2 == 1.0


def test_6_over_explanation_prevention():
    """Test 6: 説明的長文は Length Fit で減点されること。"""
    cp = "昨日映画見てきた"
    long_rep = "昨日映画を見に行ってきたんですね！それはとても良い体験だったということですね。要するに素晴らしい一日だったんですね。" * 3
    res = naturalness.evaluate_candidate_naturalness(
        long_rep, cp,
        {"counterpart_intent": "report", "known_self_facts": [], "already_asked_questions": []},
        [],
    )
    length = next(p for p in res["penalties"] if p["key"] == "length")
    assert length["score"] < 0.70


def test_7_self_disclosure_boundary():
    """Test 7: 履歴にない自己開示は disclosure で減点されること。"""
    res = naturalness.evaluate_candidate_naturalness(
        "自分も最近映画見ました", "映画見てきた",
        {"counterpart_intent": "report",
         "known_self_facts": ["今日は天気がいいね"],
         "already_asked_questions": []},
        [],
    )
    assert any(p["key"] == "disclosure" for p in res["penalties"])
    assert "自分の事実は CHAT HISTORY" in _sysp("映画見てきた")


def test_8_counterpart_fact_boundary():
    """Test 8: 相手事実の具体化（立ちっぱなし型）を許さない方針があること。"""
    sysp = _sysp("今日疲れた")
    assert "立ちっぱなし" in sysp or "具体化は禁止" in sysp
    assert "おつかれさまです" in sysp  # 自然な反応例は許可


def test_9_gold_style_preservation(client):
    """Test 9: Gold 階層の優先順位が維持されること（実例ベース）。"""
    cid = client.post("/api/contacts", json={"name": "ゴールド相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェ好き？"})
    client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "好きだよ！\nよく行くよ", "source": "manual",
    })
    prof = style.compute_hierarchical_profile(cid, "getting_to_know")
    assert prof["hierarchy_tier"] == "same_contact_recent_manual_gold"
    policy = style.to_learned_policy_prompt(prof)
    assert "短い相槌" in policy


def test_10_existing_invariants_regression(client, monkeypatch):
    """Test 10: 既存 Invariants の回帰なし（JSON・Tone・重複・[AI_QUESTION]）。"""
    # JSON strict parse
    import json

    from app.routers import generation

    assert generation._parse_replies_strict(json.dumps({"replies": ["a", "b", "c"]}), 3) == ["a", "b", "c"]
    assert generation._parse_replies_strict("a\nb\nc", 3) == []
    # fragmentation: 並列短候補は正常、3分割は検出
    assert not _is_fragmented_split(["14時とかどうですか？？", "14時大丈夫です！", "14時くらいはどうですか？"])
    assert _is_fragmented_split(["さくらさんそうなんだね笑", "僕もドライブ好きだよ", "休みの日は何してる？"])
    # AI_QUESTION fullmatch
    assert generation._extract_ai_question("[AI_QUESTION]行く？[/AI_QUESTION]") == "行く？"
    # E2E 生成が壊れていない
    cid = client.post("/api/contacts", json={"name": "回帰相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "眠い"})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            assert "【FACT BOUNDARY】" in "".join(m.get("content", "") for m in messages)
            return json.dumps({"replies": ["それは眠そう", "眠いよね", "ゆっくり休んで"]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })
    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    assert len(r.json()["replies"]) == 3
