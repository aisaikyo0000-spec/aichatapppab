"""Step 2: 質問なし返信の正常系テスト（Test A〜E）および生成確認ケース1〜4。

仕様:
- 質問なしの短い返信は正式な正常系
- 「反応→自己開示→質問」の3段構成固定なし
- 相手の発言量への簡易適応（short/medium/long）は傾向情報のみ（Hard Limitなし）
"""
from __future__ import annotations

import json

from app.ai import prompt
from app.routers import generation


def test_a_short_message_without_question_passes_validation():
    """Test A: 短い相手発言に対する質問なし返信が validation を通過する。"""
    replies = [
        "それはきついな",
        "8時間は長いね",
        "おつかれ",
    ]
    assert generation.validate_candidate_replies(replies, expected_candidates=3) == []


def test_b_ensure_has_question_does_not_modify():
    """Test B: 質問なし返信が ensure_has_question() によって改変されない。"""
    cases = [
        "それはきついな",
        "いいな",
        "それは眠そう",
        "14時くらいで大丈夫",
        "カフェ巡りいいですね！\n最近美味しいお店見つけました笑",
    ]
    for text in cases:
        assert generation.ensure_has_question(text) == text
        assert generation.ensure_has_question(text, condition="自然に", contact_name="さくら") == text


def test_c_answering_direct_question_is_not_blocked():
    """Test C: 相手の質問への回答だけの返信を妨げない。

    - 回答のみの案が validation を通過する
    - Prompt に要回答質問と回答指示が含まれる
    """
    answer_only = [
        "14時くらいで大丈夫",
        "渋谷なら行けるよ",
        "土曜は空いてる",
    ]
    assert generation.validate_candidate_replies(answer_only, expected_candidates=3) == []

    contact = {"name": "さくら", "profile": ""}
    ledger = {
        "current_topic": "明日何時にする？",
        "last_contact_message": "明日何時にする？",
        "unresolved_question": "明日何時にする？",
        "already_asked_questions": [],
    }
    sysp = prompt.build_system_prompt(
        contact=contact,
        condition="",
        conversation_ledger=ledger,
    )
    assert "相手からの直近質問（要回答）: 明日何時にする？" in sysp
    assert "相手が明確な質問をしている場合は" in sysp


def test_d_no_forced_three_step_structure_for_short_message():
    """Test D: 短い相手発言に対して3段構成を強制する Prompt が生成されない。

    - 3段構成・質問必須の文言が含まれない
    - 長さ区分 short の傾向情報が含まれる
    """
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: 眠い",
        counterpart_length_tier="short",
        counterpart_length_chars=2,
    )
    assert "3段構成" not in sysp
    assert "質問必須" not in sysp
    assert "質問を省いた案は不正" not in sysp
    assert "反応→自己開示→質問" not in sysp
    assert "【COUNTERPART MESSAGE LENGTH】" in sysp
    assert "短いリアクション" in sysp
    assert "Hard Limit ではない" in sysp


def test_e_all_three_without_questions_pass():
    """Test E: 3案すべてに質問がなくても validation を通過できる。"""
    replies = [
        "それはきついな",
        "いいな",
        "それは眠そう",
    ]
    assert generation.validate_candidate_replies(replies, expected_candidates=3) == []


def test_message_length_classification_boundaries():
    """長さ区分の境界値: short 0〜20 / medium 21〜80 / long 81以上。"""
    assert prompt.classify_message_length("") == "short"
    assert prompt.classify_message_length("眠い") == "short"
    assert prompt.classify_message_length("x" * 20) == "short"
    assert prompt.classify_message_length("x" * 21) == "medium"
    assert prompt.classify_message_length("x" * 80) == "medium"
    assert prompt.classify_message_length("x" * 81) == "long"


def _fake_config():
    return {
        "provider": "fake",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    }


def _setup_contact_with_message(client, name, contact_text):
    cid = client.post("/api/contacts", json={"name": name, "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": contact_text})
    return cid


def _patch_fake_provider(monkeypatch, replies):
    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            return json.dumps({"replies": replies})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", _fake_config)


def test_e2e_case1_short_sleepy_no_expansion(client, monkeypatch):
    """ケース1: 相手「眠い」→ 長文＋質問にならず、そのまま返ること。"""
    cid = _setup_contact_with_message(client, "確認相手1", "眠い")
    _patch_fake_provider(monkeypatch, ["それは眠そう", "眠いよね", "ゆっくり休んで"])

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    replies = r.json()["replies"]
    assert len(replies) == 3
    # 定型質問の自動付与が起きていないこと
    assert all("どうですか？笑" not in rep for rep in replies)
    # 短い返信が長い文章に膨張していないこと
    assert all(len(rep) <= 30 for rep in replies)


def test_e2e_case2_short_report_without_question(client, monkeypatch):
    """ケース2: 相手「今日バイト8時間だった」→ 質問なしの短い返信が成立すること。"""
    cid = _setup_contact_with_message(client, "確認相手2", "今日バイト8時間だった")
    _patch_fake_provider(monkeypatch, ["それはきついな", "8時間は長いね", "おつかれ"])

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    replies = r.json()["replies"]
    assert len(replies) == 3
    assert all("どうですか？笑" not in rep for rep in replies)


def test_e2e_case3_answer_to_direct_question(client, monkeypatch):
    """ケース3: 相手「明日何時にする？」→ 質問への回答を生成できること。"""
    cid = _setup_contact_with_message(client, "確認相手3", "明日何時にする？")
    _patch_fake_provider(monkeypatch, ["14時くらいで大丈夫", "15時でも大丈夫だよ", "午前中なら空いてるよ"])

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    assert len(r.json()["replies"]) == 3


def test_e2e_case4_ramen_not_only_questions(client, monkeypatch):
    """ケース4: 相手「今日ラーメン食べた」→ 「どこの？何ラーメン？」型の質問攻めだけにならないこと。"""
    cid = _setup_contact_with_message(client, "確認相手4", "今日ラーメン食べた")
    _patch_fake_provider(monkeypatch, ["いいな", "ラーメンいいね", "美味しそう"])

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    replies = r.json()["replies"]
    assert len(replies) == 3
    # 質問なし案がそのまま通ること（質問攻めの強制がない）
    assert any("？" not in rep and "?" not in rep for rep in replies)


def test_e2e_length_tier_wired_into_context(client):
    """_build_context で相手メッセージ長区分が pieces と system_prompt に反映されること。"""
    cid = _setup_contact_with_message(client, "確認相手5", "眠い")
    ctx = generation._build_context(cid, "", "", "normal")
    assert ctx["pieces"]["counterpart_length_tier"] == "short"
    assert ctx["pieces"]["counterpart_length_chars"] == 2
    assert "【COUNTERPART MESSAGE LENGTH】" in ctx["system_prompt"]
