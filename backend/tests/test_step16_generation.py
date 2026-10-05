"""Step 16: 生成内容の自然化テスト。

- 質問不要時の質問なし候補保証（soft repair・初回のみ・ベストエフォート）
- 意味不一致・感情不一致の検出
- A/B/C 役割の目安（固定なし）
- 70ケース fixture の維持
"""
from __future__ import annotations

import json

from app.ai import naturalness, prompt
from app.routers import generation


def _ledger(intent="report"):
    return {
        "counterpart_intent": intent,
        "known_self_facts": [],
        "already_asked_questions": [],
    }


def test_question_free_repair_trigger():
    """質問不要なのに全案質問つきの場合のみ soft repair 対象。」"""
    assert generation._needs_question_free_variety(
        replies=["いいですね！どこで食べたんですか？", "いいですね！何が好きですか？", "いいですね！いつ行ったんですか？"],
        candidates=3, intent="report", has_unresolved_question=False,
        condition="", mode="normal",
    ) is True
    # 単一候補は対象外
    assert generation._needs_question_free_variety(
        replies=["どこで食べたんですか？"],
        candidates=1, intent="report", has_unresolved_question=False,
        condition="", mode="normal",
    ) is False
    # 質問intent・未解決質問・followup・質問要求は対象外
    assert generation._needs_question_free_variety(
        replies=["a？", "b？", "c？"],
        candidates=3, intent="question", has_unresolved_question=False,
        condition="", mode="normal",
    ) is False
    assert generation._needs_question_free_variety(
        replies=["a？", "b？", "c？"],
        candidates=3, intent="report", has_unresolved_question=True,
        condition="", mode="normal",
    ) is False
    assert generation._needs_question_free_variety(
        replies=["a？", "b？", "c？"],
        candidates=3, intent="report", has_unresolved_question=False,
        condition="", mode="followup",
    ) is False
    assert generation._needs_question_free_variety(
        replies=["a？", "b？", "c？"],
        candidates=3, intent="report", has_unresolved_question=False,
        condition="質問して", mode="normal",
    ) is False
    # 1案でも質問なしがあれば対象外
    assert generation._needs_question_free_variety(
        replies=["いいね", "b？", "c？"],
        candidates=3, intent="report", has_unresolved_question=False,
        condition="", mode="normal",
    ) is False


def test_question_free_repair_e2e(client, monkeypatch):
    """E2E: 全案質問つきの初回出力で repair が1回走り、200で返ること。」"""
    cid = client.post("/api/contacts", json={"name": "質問なし相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "今日ラーメン食べた"})

    calls = []

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            calls.append(len(messages))
            if len(calls) == 1:
                return json.dumps({"replies": [
                    "いいですね！どこで食べたんですか？",
                    "いいですね！何が好きですか？",
                    "いいですね！いつ行ったんですか？",
                ]})
            return json.dumps({"replies": [
                "いいね",
                "いいですね！どこで食べたんですか？",
                "ラーメン食べたんだね",
            ]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })
    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    assert len(calls) == 2  # 初回 + soft repair
    assert len(r.json()["replies"]) == 3


def test_meaning_mismatch():
    """§17: 意味不一致は Relevance で低評価。話題無関係は明確に下がる。」"""
    bad = naturalness.evaluate_candidate_naturalness(
        "明日は大雨だね。", "昨日病院行ってきた", _ledger("report"), []
    )
    good = naturalness.evaluate_candidate_naturalness(
        "大変だったね", "昨日病院行ってきた", _ledger("report"), []
    )
    assert bad["score"] < good["score"]


def test_sentiment_mismatch():
    """§18: 感情不一致の4パターン。」"""
    cases = [
        ("今日は悲しいことがあった", "よかったね！最高の一日だね！", "report"),
        ("うれしいことがあった！", "それは深刻だね。大変だったね。", "report"),
        ("上司に怒られた", "どこで働いてるの？何時まで？忙しいの？", "report"),
        ("今日バイト8時間だった", "明日も頑張ってくださいね！応援してます！", "report"),
    ]
    for contact, cand, intent in cases:
        res = naturalness.evaluate_candidate_naturalness(cand, contact, _ledger(intent), [])
        assert res["score"] < 0.90, (contact, cand, res["score"])


def test_candidate_roles_guidance():
    """§23-26: A/B/C 役割は目安であり固定ではないこと。」"""
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: 今日疲れた",
    )
    assert "案1は最も自然で短い反応" in sysp
    assert "固定パターンは禁止" in sysp


def test_question_budget_guideline():
    """§21: 質問数の目安（0〜1）を naturalness が反映すること。」"""
    one_q = naturalness.evaluate_candidate_naturalness(
        "それはきついね。何時までだった？", "今日バイト8時間だった", _ledger("report"), []
    )
    three_q = naturalness.evaluate_candidate_naturalness(
        "どこで働いてるの？何時まで？忙しかった？", "今日バイト8時間だった", _ledger("report"), []
    )
    assert one_q["score"] > three_q["score"]
