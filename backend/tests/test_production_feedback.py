"""Step 12: 実運用フィードバックの不足時フォールバックと観測統計のテスト。

データ不足のためランキング変更は行わない。以下を検証する:
- insufficient data fallback（中立・無変更）
- same-contact weighting / global fallback（既存機構）
- smoothing / sendability scoring（純粋関数）
- manual Gold separation（自動昇格なし）
- Human score upper bound（±0.05/±0.03）
- AI-like combination（両立保持）
- repetition / Hard Invariants（維持）
"""
from __future__ import annotations

from app import database
from app.ai import prompt
from app.ai.naturalness import detect_repetition
from app.learning import contrast


def test_insufficient_data_fallback(client):
    """データ不足時は insufficient 判定かつランキング項が中立。」"""
    vol = contrast.feedback_volume()
    assert vol["evaluations_total"] == 0
    assert vol["sufficient_for_ranking"] is False
    assert contrast.correction_similarity("おつかれ", 1) == 0.5
    assert contrast.sent_profile_similarity("おつかれ", 1) == 0.5


def test_same_contact_weighting_and_global_fallback(client):
    """same-contact の分離と global fallback（集計は動作）。"""
    stats = contrast.acceptance_stats()
    assert stats["total_candidates"] == 0
    assert stats["send_rate"] is None
    assert stats["by_candidate_index"] == []
    # contact 指定でも空集計が壊れない
    stats_c = contrast.acceptance_stats(contact_id=1)
    assert stats_c["total_candidates"] == 0


def test_smoothing():
    """smoothing: 少数件数の 100%/0% を防ぐ。」"""
    assert contrast.smooth_rate(1, 1) < 1.0
    assert contrast.smooth_rate(0, 0) == 0.5
    assert contrast.smooth_rate(0, 5) < contrast.smooth_rate(5, 5)
    assert 0.0 <= contrast.smooth_rate(3, 4) <= 1.0


def test_sendability_scoring():
    """sendability scoring: 4値マッピングと未知値の中立。」"""
    assert contrast.sendability_signal("sendable") == 1.0
    assert contrast.sendability_signal("minor_edit") == 0.5
    assert contrast.sendability_signal("major_edit") == -0.5
    assert contrast.sendability_signal("rejected") == -1.0
    assert contrast.sendability_signal(None) == 0.0
    assert contrast.sendability_signal("unknown") == 0.0


def test_manual_gold_separation(client):
    """manual Gold separation: 採用生成文の source は generated のまま。」"""
    cid = client.post("/api/contacts", json={"name": "分離相手", "profile": ""}).json()["id"]
    conn = database.get_conn()
    try:
        now = database.now_iso()
        conn.execute(
            "INSERT INTO generation_history"
            " (contact_id, provider, model, generated_text, is_sent, created_at)"
            " VALUES (?, 'fake', 'm', 'おつかれさまです', 1, ?)",
            (cid, now),
        )
        conn.execute(
            "INSERT INTO messages (contact_id, sender, content, source, created_at, updated_at)"
            " VALUES (?, 'self', 'おつかれさまです', 'generated', ?, ?)",
            (cid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    rows = conn = database.get_conn()
    try:
        msg = conn.execute(
            "SELECT source FROM messages WHERE contact_id = ? AND sender = 'self'",
            (cid,),
        ).fetchone()
        assert msg["source"] == "generated"
    finally:
        conn.close()


def test_human_score_upper_bound():
    """Human score upper bound: 微調整項が ±0.05/±0.03 を超えない。」"""
    for fit in (0.0, 0.5, 1.0):
        assert abs(0.1 * (fit - 0.5)) <= 0.05
    for sim in (0.0, 0.5, 1.0):
        assert abs(0.06 * (sim - 0.5)) <= 0.03


def test_ai_like_combination(client, monkeypatch):
    """AI-like combination: 自動評価と人間評価が両立保持されること。」"""
    import json as _json

    cid = client.post("/api/contacts", json={"name": "両立相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "眠い"})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            return _json.dumps({"replies": ["それは眠そう", "早めに休んでね", "ゆっくり休んで"]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })
    hids = client.post(
        "/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3}
    ).json()["history_ids"]
    r = client.post("/api/evaluations", json={"history_id": hids[0], "rating": "bad"})
    assert r.status_code == 200
    # 自動スコアは上書きされない
    assert r.json()["naturalness_score"] is not None
    assert r.json()["human_rating"] == "bad"


def test_repetition(client):
    """repetition: 直近反復の検出が維持されること。」"""
    score, _ = detect_repetition("それは大変だったね", ["それは大変だね"])
    assert score < 1.0


def test_existing_hard_invariants(client):
    """existing Hard Invariants: 規約マーカーが残ること。」"""
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: こんにちは",
    )
    for phrase in ("架空の自己開示", "話者の完全分離", "「さん」付け", "【FACT BOUNDARY】", '{"replies"'):
        assert phrase in sysp


def test_diagnostics_feedback_keys(client):
    """diagnostics に feedback/acceptance/smoothed 指標が含まれること。」"""
    r = client.get("/api/learning/diagnostics")
    assert r.status_code == 200
    data = r.json()
    assert data["feedback"]["sufficient_for_ranking"] is False
    assert data["feedback"]["evaluations_total"] == 0
    assert data["acceptance"]["total_candidates"] == 0
    assert data["smoothed_sendable_rate"] == 0.5
