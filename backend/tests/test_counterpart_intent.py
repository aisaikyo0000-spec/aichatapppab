"""Step 3: 相手意図（counterpart_intent）の決定論的分類テスト。

Intent 7種: question / invitation / answer_required / closing / emotional_share / reaction / report。
優先順位: question > invitation > answer_required > emotional_share > reaction > report。
"""
from __future__ import annotations

import json

from app.ai import prompt
from app.routers import generation


def test_1_report():
    """Test 1: 単純な出来事共有は report。"""
    assert prompt.classify_counterpart_intent("今日バイト8時間だった") == "report"


def test_2_reaction():
    """Test 2: 感情・リアクションは reaction。"""
    assert prompt.classify_counterpart_intent("眠い") == "reaction"


def test_3_question():
    """Test 3: 質問は question。"""
    assert prompt.classify_counterpart_intent("明日何時にする？") == "question"


def test_4_invitation():
    """Test 4: 誘い・提案は invitation。"""
    assert prompt.classify_counterpart_intent("今度一緒に行こうよ") == "invitation"


def test_5_emotional_share():
    """Test 5: 感情・悩みの共有は emotional_share。"""
    assert prompt.classify_counterpart_intent("今日ちょっと嫌なことあってさ") == "emotional_share"


def test_6_answer_required():
    """Test 6: 疑問符なしの要回答連絡は answer_required。"""
    assert prompt.classify_counterpart_intent("明日は14時集合ね") == "answer_required"


def test_explicit_conversation_closure_has_a_distinct_intent_and_policy():
    text = "今日はこのへんで"
    assert prompt.classify_counterpart_intent(text) == "closing"

    ledger = prompt.build_conversation_state_ledger(
        [{"sender": "contact", "content": text}], condition=""
    )
    assert ledger["counterpart_intent"] == "closing"

    system_prompt = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text=f"相手: {text}",
        conversation_ledger=ledger,
    )
    assert "【COUNTERPART INTENT】\nclosing" in system_prompt
    assert "新しい話題・質問・連絡時期の約束を足さず" in system_prompt


def test_answer_required_precedes_a_closing_cue_in_the_same_message():
    assert prompt.classify_counterpart_intent("了解、また連絡するね") == "answer_required"
    assert prompt.classify_counterpart_intent("今日はありがとう、明日は14時集合ね") == "answer_required"


def test_combined_future_contact_phrase_is_classified_as_closing():
    assert prompt.classify_counterpart_intent("また明日連絡するね") == "closing"


def test_priority_question_over_invitation():
    """優先順位: 疑問符つきの誘いは question が勝つ。"""
    assert prompt.classify_counterpart_intent("今週どっか行く？") == "question"


def test_tired_alone_is_reaction_but_ongoing_is_emotional():
    """「疲れた」単体は reaction、「最近なんか疲れる」は emotional_share。"""
    assert prompt.classify_counterpart_intent("疲れた") == "reaction"
    assert prompt.classify_counterpart_intent("最近なんか疲れる") == "emotional_share"


def test_default_and_empty_is_report():
    """判定不能・空文は report（デフォルト）。"""
    assert prompt.classify_counterpart_intent("") == "report"
    assert prompt.classify_counterpart_intent("はじめまして！よろしくお願いします") == "report"
    assert prompt.classify_counterpart_intent("どうでもいいよ") == "report"


def test_ledger_contains_counterpart_intent():
    """Ledger に counterpart_intent が含まれ、直近相手文から算出されること。"""
    messages = [
        {"sender": "contact", "content": "今日バイト8時間だった"},
        {"sender": "self", "content": "それはきついな"},
        {"sender": "contact", "content": "眠い"},
    ]
    ledger = prompt.build_conversation_state_ledger(messages, condition="")
    assert ledger["counterpart_intent"] == "reaction"
    assert ledger["last_contact_message"] == "眠い"


def test_ledger_empty_messages_defaults_to_report():
    """履歴なし Ledger の intent は report。"""
    ledger = prompt.build_conversation_state_ledger([], condition="")
    assert ledger["counterpart_intent"] == "report"


def test_prompt_contains_intent_report():
    """report の場合に COUNTERPART INTENT が Prompt に入ること。"""
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: 今日バイト8時間だった",
        conversation_ledger={
            "current_topic": "今日バイト8時間だった",
            "last_contact_message": "今日バイト8時間だった",
            "counterpart_intent": "report",
            "unresolved_question": None,
            "already_asked_questions": [],
        },
    )
    assert "【COUNTERPART INTENT】\nreport" in sysp
    assert "無理に質問して会話を延長しない" in sysp
    # report でも質問禁止の Hard Rule は作らない
    assert "質問禁止" not in sysp
    assert "必ず短文" not in sysp


def test_prompt_contains_intent_question():
    """question の場合に COUNTERPART INTENT が Prompt に入ること。"""
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: 明日何時にする？",
        conversation_ledger={
            "current_topic": "明日何時にする？",
            "last_contact_message": "明日何時にする？",
            "counterpart_intent": "question",
            "unresolved_question": "明日何時にする？",
            "already_asked_questions": [],
        },
    )
    assert "【COUNTERPART INTENT】\nquestion" in sysp
    assert "相手の質問への回答を最優先" in sysp


def test_prompt_contains_all_intent_policies():
    """全Intentの方針文が定義され、Prompt に反映できること。"""
    for intent in ("question", "invitation", "answer_required", "closing", "report", "reaction", "emotional_share"):
        sysp = prompt.build_system_prompt(
            contact={"name": "相手", "profile": ""},
            condition="",
            conversation_ledger={
                "current_topic": "話題",
                "last_contact_message": "本文",
                "counterpart_intent": intent,
                "unresolved_question": None,
                "already_asked_questions": [],
            },
        )
        assert f"【COUNTERPART INTENT】\n{intent}" in sysp
        assert f"Intent別方針（{intent}）" in sysp


def test_e2e_intent_flows_into_context_and_prompt(client):
    """E2E: 実メッセージから intent が算出され、system_prompt に入ること（§16 実生成確認）。"""
    cases = [
        ("今日バイト8時間だった", "report"),
        ("眠い", "reaction"),
        ("明日何時にする？", "question"),
        ("今度一緒に行こうよ", "invitation"),
        ("今日ちょっと嫌なことあってさ", "emotional_share"),
        ("今日はこのへんで", "closing"),
    ]
    for idx, (text, want) in enumerate(cases):
        cid = client.post("/api/contacts", json={"name": f"意図相手{idx}", "profile": ""}).json()["id"]
        client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": text})
        ctx = generation._build_context(cid, "", "", "normal")
        assert ctx["pieces"]["conversation_ledger"]["counterpart_intent"] == want
        assert f"【COUNTERPART INTENT】\n{want}" in ctx["system_prompt"]


def test_e2e_generate_with_intent_report_short_reply(client, monkeypatch):
    """E2E: report に対して質問なし短文がそのまま返ること（Step 2 仕様の維持確認）。"""
    cid = client.post("/api/contacts", json={"name": "意図生成相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "今日バイト8時間だった"})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            all_str = "".join(m.get("content", "") for m in messages)
            assert "【COUNTERPART INTENT】\nreport" in all_str
            return json.dumps({"replies": ["それはきついな", "8時間は長いね", "おつかれ"]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "fake",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    })

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    assert len(r.json()["replies"]) == 3


def test_e2e_generate_preserves_answer_to_direct_question(client, monkeypatch):
    """生成APIが、providerの直接質問への回答を送信候補として保持する。"""
    cid = client.post("/api/contacts", json={"name": "予定確認相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "明日何時にする？"})
    expected_replies = [
        "明日14時で大丈夫だよ！",
        "14時くらいでどうかな？",
        "明日なら14時に行けるよ！",
    ]

    class AnsweringProvider:
        name = "answering_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            prompt_text = "".join(m.get("content", "") for m in messages)
            assert "相手の質問への回答を最優先" in prompt_text
            assert "明日何時にする？" in prompt_text
            return json.dumps({"replies": expected_replies})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: AnsweringProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "answering_fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })

    response = client.post(
        "/api/generate",
        json={"contact_id": cid, "condition": "明日14時で大丈夫", "candidates": 3},
    )

    assert response.status_code == 200
    replies = response.json()["replies"]
    assert len(replies) == 3
    assert all("14時" in reply for reply in replies)


def test_e2e_generate_keeps_substantive_emotional_share_reply(client, monkeypatch):
    """短い感情共有への具体的な受け止めが、生成APIで定型一言に潰れない。"""
    cid = client.post("/api/contacts", json={"name": "感情共有相手", "profile": ""}).json()["id"]
    incoming = "最近仕事が忙しくて休めてなくて、ちょっとしんどい"
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": incoming})
    candidate = "休めない日が続くのはしんどいね…\n少しでも休める時間が取れるといいね"

    class EmpatheticProvider:
        name = "empathetic_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            prompt_text = "".join(m.get("content", "") for m in messages)
            assert "【COUNTERPART INTENT】\nemotional_share" in prompt_text
            assert incoming in prompt_text
            return json.dumps({"replies": [candidate, "忙しいのが続くとつらいよね…少しでも休めるといいね", "それはしんどいね！無理しすぎないでね"]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: EmpatheticProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "empathetic_fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })

    response = client.post(
        "/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3}
    )

    assert response.status_code == 200
    replies = response.json()["replies"]
    assert len(replies) == 3
    assert replies[0] == candidate
    assert "休めない日が続く" in replies[0]
    assert "しんどい" in replies[0]
