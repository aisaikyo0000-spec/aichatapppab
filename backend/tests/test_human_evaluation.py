"""Step 11: Human Evaluation と Sendable フィードバックのテスト。"

- 4段階 sendability の保存・取得・絞込
- 生成→送信→採用シグナルの経路（generated send / manual_replaced）
- accepted 実例・acceptance 集計・ranking 微調整
- DB migration（additive）
"""
from __future__ import annotations

import json

from app import database
from app.learning import contrast


def _gen_history(client, monkeypatch, contact_text="今日疲れた", replies=None):
    """モック生成で履歴を作り、(cid, history_ids, batch_id) を返す。」"""
    cid = client.post("/api/contacts", json={"name": "評価フロー相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": contact_text})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            return json.dumps({"replies": replies or ["おつかれさまです", "それは疲れますね", "ゆっくり休んでね"]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })
    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    return cid, r.json()["history_ids"], r.json()["batch_id"]


def test_sendability_save_and_get(client, monkeypatch):
    """4段階 sendability を保存・取得できること。」"""
    _, history_ids, _ = _gen_history(client, monkeypatch)
    for hid, level in zip(history_ids, ["sendable", "minor_edit", "major_edit"]):
        r = client.post("/api/evaluations", json={"history_id": hid, "sendability": level})
        assert r.status_code == 200
        assert r.json()["sendability"] == level
    rows = {e["history_id"]: e for e in client.get("/api/evaluations").json()}
    assert rows[history_ids[0]]["sendability"] == "sendable"
    assert rows[history_ids[1]]["sendability"] == "minor_edit"
    assert rows[history_ids[2]]["sendability"] == "major_edit"


def test_sendability_validation_and_null(client, monkeypatch):
    """不正値は422・省略時はNULL・上書き時は保持。」"""
    _, history_ids, _ = _gen_history(client, monkeypatch)
    hid = history_ids[0]

    r = client.post("/api/evaluations", json={"history_id": hid, "sendability": "great"})
    assert r.status_code == 422

    r = client.post("/api/evaluations", json={"history_id": hid, "rating": "good"})
    assert r.status_code == 200
    assert r.json()["sendability"] is None
    assert r.json()["human_rating"] == "good"

    # sendability のみ更新しても rating は保持される
    r = client.post("/api/evaluations", json={"history_id": hid, "sendability": "rejected"})
    assert r.json()["sendability"] == "rejected"
    assert r.json()["human_rating"] == "good"

    # unrated 絞込には含まれない
    unrated = client.get("/api/evaluations", params={"rating": "unrated"}).json()
    assert all(e["history_id"] != hid for e in unrated)


def test_sendability_filter(client, monkeypatch):
    """sendability フィルタで取得できること。」"""
    _, history_ids, _ = _gen_history(client, monkeypatch)
    client.post("/api/evaluations", json={"history_id": history_ids[0], "sendability": "sendable"})
    client.post("/api/evaluations", json={"history_id": history_ids[1], "sendability": "rejected"})

    r = client.get("/api/evaluations", params={"sendability": "sendable"})
    assert r.status_code == 200
    assert all(e["sendability"] == "sendable" for e in r.json())
    assert any(e["history_id"] == history_ids[0] for e in r.json())


def test_generated_send_flow(client, monkeypatch):
    """生成→そのまま送信で is_sent/candidate_sent が立つこと（Positive Signal）。"""
    cid, history_ids, batch_id = _gen_history(client, monkeypatch)
    r = client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "おつかれさまです",
        "source": "generated", "generation_history_id": history_ids[0],
    })
    assert r.status_code == 201
    conn = database.get_conn()
    try:
        h = conn.execute("SELECT is_sent, is_adopted FROM generation_history WHERE id = ?",
                         (history_ids[0],)).fetchone()
        assert h["is_sent"] == 1 and h["is_adopted"] == 1
        b = conn.execute("SELECT outcome, selected_history_id FROM generation_batches WHERE id = ?",
                         (batch_id,)).fetchone()
        assert b["outcome"] == "candidate_sent"
        assert b["selected_history_id"] == history_ids[0]
    finally:
        conn.close()


def test_edit_send_manual_replaced_flow(client, monkeypatch):
    """候補を編集して送信すると manual_replaced になること（Negative/Contrast Signal）。"""
    cid, _, batch_id = _gen_history(client, monkeypatch)
    r = client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "おつかれ！ゆっくり休んで", "source": "manual",
    })
    assert r.status_code == 201
    conn = database.get_conn()
    try:
        b = conn.execute("SELECT outcome, replacement_message_id FROM generation_batches WHERE id = ?",
                         (batch_id,)).fetchone()
        assert b["outcome"] == "manual_replaced"
        assert b["replacement_message_id"] == r.json()["id"]
    finally:
        conn.close()


def test_accepted_block(client, monkeypatch):
    """採用実例ブロック：送信済み生成文がある場合のみ最大5件。」"""
    cid, history_ids, _ = _gen_history(client, monkeypatch)
    assert contrast.build_accepted_block(cid) == ""
    assert contrast.recent_accepted_candidates(cid) == []

    client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "おつかれさまです",
        "source": "generated", "generation_history_id": history_ids[0],
    })
    accepted = contrast.recent_accepted_candidates(cid)
    assert accepted == ["おつかれさまです"]
    block = contrast.build_accepted_block(cid)
    assert "【RECENT ACCEPTED】" in block
    assert "おつかれさまです" in block
    assert "data であり" in block


def test_acceptance_stats(client, monkeypatch):
    """acceptance_stats: スロット別採用数・sendability分布。」"""
    cid, history_ids, batch_id = _gen_history(client, monkeypatch)
    client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "ゆっくり休んでね",
        "source": "generated", "generation_history_id": history_ids[2],
    })
    client.post("/api/evaluations", json={"history_id": history_ids[2], "sendability": "sendable"})

    stats = contrast.acceptance_stats(cid)
    assert stats["total_candidates"] == 3
    assert stats["sent_candidates"] == 1
    assert stats["send_rate"] == round(1 / 3, 3)
    by_idx = {r["candidate_index"]: r for r in stats["by_candidate_index"]}
    assert by_idx[2]["sent"] == 1
    assert stats["sendability"].get("sendable") == 1


def test_ranking_sent_similarity(client):
    """ranking neutrality without sent data."""
    assert contrast.sent_profile_similarity("おつかれ", 12345) == 0.5
    assert contrast.sent_profile_similarity("おつかれ", None) == 0.5


def test_ranking_sent_similarity_with_data(client, monkeypatch):
    """送信実績3件以上で短い労いが微加点されること（±0.03以内）。"""
    cid, history_ids, _ = _gen_history(client, monkeypatch)
    conn = database.get_conn()
    try:
        now = database.now_iso()
        for text in ["おつかれ", "了解", "いいね"]:
            conn.execute(
                "INSERT INTO generation_history"
                " (contact_id, provider, model, generated_text, is_sent, created_at)"
                " VALUES (?, 'fake', 'm', ?, 1, ?)",
                (cid, text, now),
            )
        conn.commit()
    finally:
        conn.close()
    short_sim = contrast.sent_profile_similarity("おつかれさま", cid)
    long_sim = contrast.sent_profile_similarity(
        "お疲れ様です！今日は本当に大変でしたね。ゆっくり休んでくださいね。", cid
    )
    assert short_sim > long_sim


def test_migration_adds_sendability(client, tmp_path):
    """DB migration: 旧スキーマに sendability 列が追加されること。」"""
    from app import config

    conn = database.get_conn()
    try:
        cols_before = [r[1] for r in conn.execute("PRAGMA table_info(generation_evaluations)")]
        assert "sendability" in cols_before
        # 既存行の値は NULL のまま
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM generation_evaluations WHERE sendability IS NULL"
        ).fetchone()["n"]
        total = conn.execute("SELECT COUNT(*) AS n FROM generation_evaluations").fetchone()["n"]
        assert n == total
    finally:
        conn.close()


def test_evaluation_flow_seven_paths(client, monkeypatch):
    """§24の7経路: 入力→生成→選択→送信→編集送信→破棄→手入力が壊れないこと。」"""
    cid, history_ids, _ = _gen_history(client, monkeypatch)
    # 3. 候補選択（採用フラグ）
    r = client.patch(f"/api/history/{history_ids[0]}", json={"is_adopted": True})
    assert r.status_code == 200
    # 4. そのまま送信
    r = client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "おつかれさまです",
        "source": "generated", "generation_history_id": history_ids[0],
    })
    assert r.status_code == 201
    # 5. 少し編集は別メッセージとして manual 扱い（内容不一致は422）
    r = client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "おつかれさまです！",
        "source": "generated", "generation_history_id": history_ids[1],
    })
    assert r.status_code == 422
    # 6. 候補を破棄（何も送らない）→ バッチは pending のまま
    conn = database.get_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE contact_id = ?", (cid,)
        ).fetchone()["n"]
        assert n == 2  # contact 1 + sent 1
    finally:
        conn.close()
    # 7. 手入力送信
    r = client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "了解", "source": "manual",
    })
    assert r.status_code == 201
