"""Step 7: 自然な会話生成の改善テスト（Tests 1〜10＋代表50ケース）。

方針（Step 7 仕様）:
- 固定ルール（必ず短文・必ず質問なし等）は検証しない
- 質問禁止・長さ上限のような Hard 制約がないことを確認する
- 代表50ケース（step7_representative_cases.json）がすべて期待通りになること
"""
from __future__ import annotations

import json

from app.ai import naturalness, prompt
from app.routers.generation import validate_candidate_replies

from step7_representative_cases import load_step7_representative_cases


def _ledger(intent="report", prev_q=False):
    return {
        "counterpart_intent": intent,
        "known_self_facts": [],
        "already_asked_questions": [],
        "prev_self_ended_with_question": prev_q,
    }


def test_1_question_free_replies_are_valid():
    """Test 1: 質問しなくても成立するケース（バリデーション通過）。"""
    replies = ["それはきついな", "いいな", "それは眠そう"]
    assert validate_candidate_replies(replies, 3) == []


def test_2_short_replies_are_valid_candidates():
    """Test 2: 短い返信が候補として成立する（笑・相槌・一言）。"""
    replies = ["笑", "そっか", "いいな"]
    assert validate_candidate_replies(replies, 3) == []
    res = naturalness.evaluate_candidate_naturalness("笑", "面白いことあった", _ledger("reaction"), [])
    assert res["score"] >= 0.80


def test_3_no_question_every_time():
    """Test 3: 直近 self が質問終わりなら追加質問が抑制される（禁止ではなく優先度）。"""
    ledger_prev_q = _ledger("report", prev_q=True)
    res = naturalness.evaluate_candidate_naturalness(
        "それはきついね。どこで働いてるの？", "今日バイト8時間だった", ledger_prev_q, []
    )
    assert res["signals"].get("consecutive_question") is True
    q = next(p for p in res["penalties"] if p["key"] == "question")
    assert q["score"] < 0.85
    # 回答内容があれば抑制されない（相手の質問への回答＋質問）
    ledger_q = _ledger("question", prev_q=True)
    res2 = naturalness.evaluate_candidate_naturalness(
        "14時がいいな。何時に集まる？", "明日何時にする？", ledger_q, []
    )
    assert res2["signals"].get("consecutive_question", False) is False
def test_4_no_parroting():
    """Test 4: 相手発言をそのまま繰り返さない（Echo 検出）。"""
    res = naturalness.evaluate_candidate_naturalness(
        "今日バイト8時間だったんだね", "今日バイト8時間だった", _ledger("report"), []
    )
    assert any(p["key"] == "echo" for p in res["penalties"])
    # 態度の付加（いいな等）は反復とみなさない
    res2 = naturalness.evaluate_candidate_naturalness(
        "札幌いいな", "昨日友達と札幌行ってきた", _ledger("report"), []
    )
    assert not any(p["key"] == "echo" for p in res2["penalties"])


def test_5_no_bloating_for_long_input():
    """Test 5: 長文入力に対して必要以上に長文化した候補は Length Fit で減点。"""
    cp = "昨日友達と札幌に行ってきたんだ。時計台を見て、ラーメンを食べて、夜はすすきのを散歩したよ。"
    long_rep = "札幌に行ってきたんだね！時計台も見たんだね！ラーメンも食べたんだね！すすきのの散歩もいいね！" * 4
    res = naturalness.evaluate_candidate_naturalness(long_rep, cp, _ledger("report"), [])
    length = next(p for p in res["penalties"] if p["key"] == "length")
    assert length["score"] < 0.70


def test_6_no_forced_long_for_short_input():
    """Test 6: 短文入力に長文回答を強制しない（短文ガイダンス＋短文通過）。"""
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: 眠い",
        counterpart_length_tier="short",
        counterpart_length_chars=2,
    )
    assert "短い入力というだけで内容のある状態共有を一言に縮めず" in sysp
    assert "自然に完結するなら短く返す" in sysp
    assert "短い候補を必ず1案以上" not in sysp
    # 固定ルール（必ず○文・最低○文字）がないこと
    assert "必ず3文" not in sysp
    assert "最低" not in sysp
    assert validate_candidate_replies(["それは眠そう", "眠いよね", "ゆっくり休んで"], 3) == []


def test_7_no_forced_diversity():
    """Test 7: 3候補の機械的差別化をしない（意味のある違いだけ）。"""
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: 今日バイト8時間だった",
    )
    assert "自然に異なる焦点がある場合だけ分ける" in sysp
    assert "内容や長さ、質問の有無を機械的に変えない" in sysp


def test_8_question_candidates_allowed_when_needed():
    """Test 8: 質問が必要なケースでは質問候補を生成できる（質問自体は潰さない）。"""
    res = naturalness.evaluate_candidate_naturalness(
        "14時がいいな。何時に集まる？", "明日何時にする？", _ledger("question"), []
    )
    assert res["score"] >= 0.60
    assert not any(p["key"] == "question" and p["score"] < 0.80 for p in res["penalties"])


def test_9_hard_invariants_intact():
    """Test 9: 既存 Hard Invariants を維持（7項目）。"""
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: こんにちは",
    )
    for phrase in (
        "架空の自己開示",
        "話者の完全分離",
        "「さん」付け",
        "オウム返し",
        '{"replies"',
        "[AI_QUESTION]",
    ):
        assert phrase in sysp
    # 機械的な定型表現は検出する。接続語だけでは違反にしない。
    bad = [
        "とのことですが、いいですね！\n質問ありますか？",
        "いいですね！\nいいですね！",
        "いいですね！\nいいですね！\nいいですね！",
    ]
    violations = validate_candidate_replies(bad, 3)
    assert any("形式的な報告表現" in v for v in violations)
    natural_reporting = validate_candidate_replies(
        ["前に温泉を求めて旅行したとのことですが、楽しそうですね！", "いいね！", "おつかれ！"],
        3,
    )
    assert not any("形式的な報告表現" in v for v in natural_reporting)
    assert not validate_candidate_replies(["ほかにも行ったことある？", "気になる！", "楽しそう！"], 3)


def test_10_generation_api_intact(client, monkeypatch):
    """Test 10: 既存の Generation API が壊れていない（生成→batch→history→evaluations）。"""
    cid = client.post("/api/contacts", json={"name": "ステップ7相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "今日バイト8時間だった"})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            return json.dumps({"replies": ["それはきついな", "8時間は長いね", "おつかれ"]})

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
    assert data["batch_id"] is not None
    assert len(data["history_ids"]) == 3
    assert len(data["final_scores"]) == 3
    # 自動評価行が作られている
    evals = client.get("/api/evaluations", params={"batch_id": data["batch_id"]}).json()
    assert len(evals) == 3


def test_30_representative_cases_all_green():
    """代表50ケース: Intent一致・Promptマーカー・バリデーション・期待順位のすべてが緑。"""
    cases = load_step7_representative_cases()
    assert len(cases) == 50
    intents = {c["intent"] for c in cases}
    assert intents == {"report", "reaction", "question", "invitation", "emotional_share", "answer_required"}

    failures = []
    for case in cases:
        contact = case["contact"]
        pred = prompt.classify_counterpart_intent(contact)
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
        markers_ok = (
            "【COUNTERPART INTENT】" in sysp
            and "質問必須" not in sysp
            and "3段構成" not in sysp
        )
        valid = validate_candidate_replies(case["candidates"], 3) == []
        scored = [
            naturalness.evaluate_candidate_naturalness(c, contact, ledger, [])["score"]
            for c in case["candidates"]
        ]
        order = sorted(range(3), key=lambda i: scored[i], reverse=True)
        hit = order[0] == case["expect_first"]
        if not (pred == case["intent"] and markers_ok and valid and hit):
            failures.append(case["id"])

    assert failures == []
