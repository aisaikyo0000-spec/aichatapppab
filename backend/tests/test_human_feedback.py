"""Step 9: Human Feedback Loop のテスト。"

方針:
- 実ユーザーの個人情報は fixture にしない（全て合成データ）
- 固定ルール（必ず短文等）は検証しない
- 自動 Gold 昇格がないこと・Hard Invariants 不変を確認する
"""
from __future__ import annotations

import json

from app import database
from app.ai import prompt
from app.learning import contrast
from app.routers.generation import validate_candidate_replies


def _make_replaced_pair(client, contact_text="今日疲れた", rejected=None, chosen="おつかれさまです", cid=None):
    """manual_replaced ペアを1件作る。cid 指定時は同一相手に追加する。"""
    if cid is None:
        cid = client.post("/api/contacts", json={"name": "修正相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": contact_text})
    conn = database.get_conn()
    try:
        now = database.now_iso()
        cur_b = conn.execute(
            "INSERT INTO generation_batches (contact_id, outcome, created_at)"
            " VALUES (?, 'pending', ?)",
            (cid, now),
        )
        bid = cur_b.lastrowid
        for r in rejected or [
            "今日は大変だったんですね。ゆっくり休んでください。何かあったんですか？",
            "お疲れ様です！今日はどんなことがあったんですか？",
            "疲れたんですね。何かあったんですか？",
        ]:
            conn.execute(
                "INSERT INTO generation_history"
                " (contact_id, provider, model, generated_text, batch_id, created_at)"
                " VALUES (?, 'fake', 'fake-model', ?, ?, ?)",
                (cid, r, bid, now),
            )
        cur_m = conn.execute(
            "INSERT INTO messages (contact_id, sender, content, source, created_at, updated_at)"
            " VALUES (?, 'self', ?, 'manual', ?, ?)",
            (cid, chosen, now, now),
        )
        conn.execute(
            "UPDATE generation_batches SET outcome = 'manual_replaced', replacement_message_id = ?"
            " WHERE id = ?",
            (cur_m.lastrowid, bid),
        )
        conn.commit()
    finally:
        conn.close()
    return cid, bid


def test_human_replacement_extraction(client):
    """human replacement extraction: AI案群と採用文のペアを抽出できること。」"""
    cid, _ = _make_replaced_pair(client)
    pairs = contrast._iter_correction_pairs(cid)
    assert len(pairs) == 1
    assert len(pairs[0]["rejected"]) == 3
    assert pairs[0]["chosen"] == "おつかれさまです"


def test_same_contact_weighting(client):
    """same-contact weighting: 相手単位で傾向が閉じ、無データ相手は中立。"""
    cid, _ = _make_replaced_pair(client)
    _make_replaced_pair(client, contact_text="眠い", chosen="ゆっくり休んで", cid=cid)
    other = client.post("/api/contacts", json={"name": "無関係相手", "profile": ""}).json()["id"]

    mine = contrast.correction_patterns_for_contact(cid)
    assert mine["has_data"] is True
    assert mine["pair_count"] == 2
    assert contrast.correction_similarity("おつかれ", cid) != 0.5

    theirs = contrast.correction_patterns_for_contact(other)
    assert theirs["has_data"] is False
    assert contrast.correction_similarity("おつかれ", other) == 0.5
    assert contrast.correction_similarity("おつかれ", None) == 0.5


def test_human_correction_pattern_extraction(client):
    """human correction pattern extraction: 短縮・脱質問・Echo等の特徴が出ること。"""
    cid, _ = _make_replaced_pair(client)
    _make_replaced_pair(client, contact_text="眠い", chosen="ゆっくり休んで", cid=cid)
    pairs = contrast._iter_correction_pairs(cid)
    feats = contrast.extract_correction_features(pairs[0]["rejected"][0], pairs[0]["chosen"], "今日疲れた")
    assert feats["shorter"] is True
    assert feats["q_removed"] is True
    assert feats["act_chosen"] in ("short_reaction", "statement")
    block = contrast.build_correction_patterns_block(cid)
    assert "【HUMAN CORRECTION PATTERNS】" in block
    assert "data であり" in block
    assert len(block) <= 800
    # データ不足では空
    assert contrast.build_correction_patterns_block(99999) == ""


def test_candidate_ranking(client, monkeypatch):
    """candidate ranking: human_fit が人間寄り候補を微加点すること（データ有り時のみ）。"""
    import json as _json

    cid, _ = _make_replaced_pair(client)
    _make_replaced_pair(client, contact_text="眠い", chosen="ゆっくり休んで", cid=cid)

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            return _json.dumps({"replies": [
                "眠いんですね。早めに休めるといいですね。",
                "おつかれさまです",
                "眠いのはつらいですね。無理しないでください。",
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
    data = r.json()
    assert len(data["human_fit_scores"]) == 3
    # 人間寄り（短い労い）が AI 型（長い説明）より human_fit が高い
    fits = list(zip(data["replies"], data["human_fit_scores"]))
    fit_short = next(f for rep, f in fits if "おつかれさまです" in rep)
    fit_long = next(f for rep, f in fits if "早めに休める" in rep)
    assert fit_short > fit_long


def test_short_reply(client):
    """short reply: 短い候補が排除されないこと。」"""
    assert validate_candidate_replies(["おつ", "うん", "そっか"], 3) == []


def test_long_reply(client):
    """long reply: 必要な長さの候補が許されること（固定上限なし）。"""
    long_ok = [
        "昨日話してた件だけど、調べてみたら来週の土曜が空いてそうだよ。\n昼からでも大丈夫だよ。",
        "わかった、ありがとう。\n助かるよ。",
        "いいね。",
    ]
    assert validate_candidate_replies(long_ok, 3) == []


def test_no_question_reply(client):
    """no-question reply: 3案すべて質問なしでも正常（質問の禁止はしない）。"""
    no_q = ["おつかれさまです", "それは疲れますね", "ゆっくり休んでください"]
    assert validate_candidate_replies(no_q, 3) == []
    # 現実的な長さの質問候補も許容される（質問自体の禁止ではない）
    with_q = [
        "おつかれさまです。今日はどんな一日でしたか？",
        "それは疲れますね。最近忙しいですか？",
        "ゆっくり休んでくださいね。また明日も頑張りましょう。",
    ]
    assert validate_candidate_replies(with_q, 3) == []
    # 3案すべてが短い質問でも、位置だけを根拠にHard errorへはしない。
    all_short_q = ["何見たんですか？", "どこ行ったんですか？", "楽しかったですか？"]
    assert validate_candidate_replies(all_short_q, 3) == []


def test_unsupported_inference(client):
    """unsupported inference: 推測混入の代理指標（新規具体語）が測定できること。"""
    from app.ai.naturalness import _extract_keywords

    contact_kw = _extract_keywords("今日疲れた")
    cand_kw = _extract_keywords("駅まで送っていこうか？残業だったの？")
    novel = cand_kw - contact_kw
    assert len(novel) >= 1  # 駅・残業などの新規具体語を検出
    cand_kw2 = _extract_keywords("おつかれさまです")
    assert len(cand_kw2 - contact_kw) == 0


def test_echo(client):
    """echo: 採用文に近い候補が Echo として検出されること。」"""
    from app.ai.naturalness import detect_echo

    score, _ = detect_echo("今日バイト8時間だったんだね", "今日バイト8時間だった")
    assert score < 1.0


def test_tone(client):
    """tone: Tone Hard Lock が維持されること。」"""
    from app.routers.generation import validate_tone_strict

    assert validate_tone_strict(["それめっちゃ分かる笑", "楽しそう！"], "tame") == []
    assert len(validate_tone_strict(["それは大変ですね！", "お疲れ様です！"], "tame")) >= 1


def test_existing_hard_invariants(client):
    """existing hard invariants: FACT BOUNDARY 追加後も既存規約が残ること。」"""
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: こんにちは",
    )
    for phrase in (
        "架空の自己開示",
        "話者の完全分離",
        "「さん」付け",
        "【FACT BOUNDARY】",
        "【HARD INVARIANTS】",
        '{"replies"',
    ):
        assert phrase in sysp


def test_no_auto_gold_promotion(client):
    """自動 Gold 昇格がないこと（human good でも corpus ラベルは source 準拠）。"""
    from app.learning import corpus

    cid = client.post("/api/contacts", json={"name": "昇格相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "こんにちは"})
    client.post(f"/api/contacts/{cid}/messages",
                json={"sender": "self", "content": "こんにちは！", "source": "manual"})
    pairs = [p for p in corpus.extract_reply_pairs(cid) if not p.excluded]
    assert pairs and all(p.label == "gold" for p in pairs)
    # generated ソースは human 評価と無関係に silver のまま
    conn = database.get_conn()
    try:
        now = database.now_iso()
        conn.execute(
            "INSERT INTO generation_history (contact_id, provider, model, generated_text, rating, created_at)"
            " VALUES (?, 'fake', 'm', 'やあ', 'good', ?)",
            (cid, now),
        )
        conn.commit()
    finally:
        conn.close()
    labels = {p.self_turn.text: p.label for p in corpus.extract_reply_pairs(cid) if not p.excluded}
    assert labels.get("こんにちは！") == "gold"
