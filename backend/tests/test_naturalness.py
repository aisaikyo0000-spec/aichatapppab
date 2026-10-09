"""Step 4: Naturalness Score のテスト。

方針:
- 「短いほど自然」「質問がないほど高得点」のような固定ルールは検証しない
- 会話に対して不自然な余計なものが入っていないかを評価できているかを確認する
"""
from __future__ import annotations

from app.ai import naturalness
from app.ai.naturalness import (
    combine_candidate_scores,
    count_meaningful_questions,
    detect_echo,
    detect_repetition,
    evaluate_candidate_naturalness,
)


def _ledger(intent="report", facts=None):
    return {
        "counterpart_intent": intent,
        "known_self_facts": facts or [],
        "already_asked_questions": [],
    }


def test_1_short_report_high_score():
    """Test 1: 短い報告への短い反応は高評価。"""
    res = evaluate_candidate_naturalness(
        "それはきついな", "今日バイト8時間だった", _ledger("report"), [],
    )
    assert res["score"] >= 0.80
    assert res["penalties"] == [] or all(p["score"] >= 0.6 for p in res["penalties"])


def test_2_excessive_questions_lower_than_simple():
    """Test 2: 過剰質問は Test 1 より低評価。"""
    base = evaluate_candidate_naturalness(
        "それはきついな", "今日バイト8時間だった", _ledger("report"), [],
    )
    over_q = evaluate_candidate_naturalness(
        "それはきついね。どこで働いてるの？何時まで？忙しかった？",
        "今日バイト8時間だった", _ledger("report"), [],
    )
    assert over_q["score"] < base["score"]
    assert any(p["key"] == "question" for p in over_q["penalties"])


def test_3_ignoring_topic_penalized_on_relevance():
    """Test 3: 話題無視は Relevance で大きく減点。"""
    res = evaluate_candidate_naturalness(
        "最近映画見てる？", "今日バイト8時間だった", _ledger("report"), [],
    )
    rel = next(p for p in res["penalties"] if p["key"] == "relevance")
    assert rel["score"] <= 0.40


def test_4_echo_penalty():
    """Test 4: 単純言い換えは Echo penalty。"""
    res = evaluate_candidate_naturalness(
        "今日バイト8時間だったんだね", "今日バイト8時間だった", _ledger("report"), [],
    )
    assert any(p["key"] == "echo" for p in res["penalties"])


def test_5_too_long_penalized_on_length():
    """Test 5: 短文への長文返信は Length Fit で減点。"""
    short = evaluate_candidate_naturalness("それは眠いな", "眠い", _ledger("reaction"), [])
    long_rep = evaluate_candidate_naturalness(
        "それは眠いよね。最近忙しかったのかな。自分も昨日遅くまで起きてて、"
        "朝起きるの大変だった。今日は早く寝たほうがいいかもね。ちなみに明日は何時起き？",
        "眠い", _ledger("reaction"), [],
    )
    assert long_rep["score"] < short["score"]
    assert any(p["key"] == "length" for p in long_rep["penalties"])


def test_6_answer_to_question_high_score():
    """Test 6: 質問への回答は高評価（質問がなくても減点しない）。"""
    res = evaluate_candidate_naturalness(
        "14時くらいで大丈夫", "明日何時にする？", _ledger("question"), [],
    )
    assert res["score"] >= 0.60
    assert not any(p["key"] == "question" and p["score"] < 0.5 for p in res["penalties"])


def test_7_ignoring_question_heavily_penalized():
    """Test 7: 質問無視は Relevance / Answer で大きく減点。"""
    good = evaluate_candidate_naturalness(
        "14時くらいで大丈夫", "明日何時にする？", _ledger("question"), [],
    )
    bad = evaluate_candidate_naturalness(
        "今日は疲れたな", "明日何時にする？", _ledger("question"), [],
    )
    assert bad["score"] < good["score"] - 0.20
    keys = {p["key"] for p in bad["penalties"]}
    assert "relevance" in keys or "answer" in keys


def test_8_repetition_penalty():
    """Test 8: 直前返信との繰り返しは Repetition penalty。"""
    res = evaluate_candidate_naturalness(
        "それは大変だったね", "今日バイトきつかった", _ledger("report"), ["それは大変だね"],
    )
    assert any(p["key"] == "repetition" for p in res["penalties"])
    # 参照なしなら減点なし
    clean = evaluate_candidate_naturalness(
        "それは大変だったね", "今日バイトきつかった", _ledger("report"), ["カフェ行ってきた"],
    )
    assert clean["score"] > res["score"]


def test_8b_short_generic_word_not_heavily_penalized():
    """短い一般語だけの一致では強く減点しない。"""
    score, _ = detect_repetition("いいね", ["いいね！"])
    assert score >= 0.80


def test_8c_topic_vocab_overlap_without_same_head_is_not_repetition():
    """同じ話題・文体の語彙共有だけでは繰り返しとみなさない（本人らしさの維持）。"""
    score, _ = detect_repetition(
        "カフェ巡りいいですね！\n最近美味しい珈琲探してました笑\nおすすめのお店ありますか？",
        ["カフェいいですね！\n最近新しいお店探してました笑\nおすすめありますか？"],
    )
    assert score >= 0.90


def test_9_unsupported_self_disclosure():
    """Test 9: 根拠のない自己開示は大きく減点。既存 Hard Validation とは別軸。"""
    res = evaluate_candidate_naturalness(
        "自分も昨日ラーメン食べた",
        "今日ラーメン食べた",
        _ledger("report", facts=["今日は天気がいいね", "仕事は接客業をしてます"]),
        [],
    )
    dis = next(p for p in res["penalties"] if p["key"] == "disclosure")
    assert dis["score"] <= 0.30
    # 根拠がある場合は減点なし
    grounded = evaluate_candidate_naturalness(
        "自分も昨日ラーメン食べた",
        "今日ラーメン食べた",
        _ledger("report", facts=["昨日は友達とラーメン食べに行った"]),
        [],
    )
    assert not any(p["key"] == "disclosure" for p in grounded["penalties"])


def test_17_ranking_short_reaction_first():
    """最重要: A(短い反応) > B(質問過多) > C(Echo寄り) の順になること。"""
    cp = "今日バイト8時間だった"
    ledger = _ledger("report")
    a = evaluate_candidate_naturalness("それはきついな", cp, ledger, [])
    b = evaluate_candidate_naturalness("それは大変だったね！どこで働いてるの？", cp, ledger, [])
    c = evaluate_candidate_naturalness("今日8時間バイトだったんだね。お疲れ様！", cp, ledger, [])
    assert a["score"] > b["score"] > c["score"]
    assert any(p["key"] == "question" for p in b["penalties"])
    assert any(p["key"] == "echo" for p in c["penalties"])


def test_question_itself_is_not_penalized():
    """質問があるだけでは減点しない（通常の1質問は許容）。"""
    res = evaluate_candidate_naturalness(
        "それはきついね。何時までだった？", "今日バイト8時間だった", _ledger("report"), [],
    )
    q = next((p for p in res["penalties"] if p["key"] == "question"), None)
    assert q is None or q["score"] >= 0.80


def test_question_necessity_uses_reliable_same_contact_gold_rate():
    low_rate = evaluate_candidate_naturalness(
        "眠れそう？",
        "眠い",
        _ledger("reaction"),
        [],
        gold_question_rate=0.1,
    )
    high_rate = evaluate_candidate_naturalness(
        "眠れそう？",
        "眠い",
        _ledger("reaction"),
        [],
        gold_question_rate=0.7,
    )

    assert low_rate["signals"]["question_necessity"] == "unnecessary"
    assert high_rate["signals"]["question_necessity"] == "optional"


def test_count_meaningful_questions():
    """質問数カウント: 情報取得3問とリアクション質問の区別。"""
    q = count_meaningful_questions("どこ？\nいつ？\n誰と？")
    assert q["informative"] == 3
    q2 = count_meaningful_questions("ほんと？")
    assert q2["reaction"] == 1 and q2["informative"] == 0


def test_detect_echo_exact_and_clean():
    """Echo: 完全一致は0点、無関係文は減点なし。"""
    s, _ = detect_echo("今日バイト8時間だった", "今日バイト8時間だった")
    assert s == 0.0
    s2, _ = detect_echo("それはきついな", "今日バイト8時間だった")
    assert s2 == 1.0


def test_combine_weights_and_constants():
    """結合式: final = style*0.40 + naturalness*0.60。重みは定数。"""
    assert naturalness.STYLE_WEIGHT == 0.40
    assert naturalness.NATURALNESS_WEIGHT == 0.60
    assert combine_candidate_scores(0.81, 0.91) == round(0.81 * 0.40 + 0.91 * 0.60, 3)


def test_e2e_generate_sorted_by_final_score(client, monkeypatch):
    """E2E: 3候補が final 降順で返り、score 系キーが含まれること。"""
    import json

    cid = client.post("/api/contacts", json={"name": "自然性相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "今日バイト8時間だった"})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            return json.dumps({"replies": [
                "今日8時間バイトだったんだね。お疲れ様！",
                "それは大変だったね！どこで働いてるの？",
                "それはきついな",
            ]})

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
    data = r.json()
    assert len(data["replies"]) == 3
    assert len(data["final_scores"]) == 3
    assert len(data["naturalness_scores"]) == 3
    assert data["final_scores"][0] >= data["final_scores"][1] >= data["final_scores"][2]
    # 自然な短い反応が先頭に来ること
    assert data["replies"][0] == "それはきついな"


def test_diagnostics_reports_naturalness_weights(client):
    """Diagnostics に naturalness の有効フラグと重みが含まれること。"""
    r = client.get("/api/learning/diagnostics")
    assert r.status_code == 200
    data = r.json()
    assert data["naturalness_enabled"] is True
    assert data["naturalness_weight"] == 0.60
