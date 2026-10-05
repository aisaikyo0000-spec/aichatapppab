"""Step 13: Feedback 収集フローのテスト。

- implicit send（送信そのもの）と explicit evaluation（明示評価）の分離
- explicit 優先・重複防止・更新・来歴追跡
- diagnostics と整合性検出
"""
from __future__ import annotations

import json

from app import database
from app.learning import contrast


def _gen_history(client, monkeypatch, contact_text="今日疲れた"):
    cid = client.post("/api/contacts", json={"name": "収集相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": contact_text})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            return json.dumps({"replies": ["おつかれさまです", "それは疲れますね", "ゆっくり休んでね"]})

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


def test_implicit_send(client, monkeypatch):
    """implicit send: そのまま送信で暗黙シグナル（is_sent）が立つこと。」"""
    cid, history_ids, _ = _gen_history(client, monkeypatch)
    client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "おつかれさまです",
        "source": "generated", "generation_history_id": history_ids[0],
    })
    flow = contrast.evaluation_flow_counts()
    assert flow["implicit_sent_total"] == 1
    assert flow["explicit_total"] == 0


def test_explicit_evaluation(client, monkeypatch):
    """explicit evaluation: 明示評価が件数に載ること。」"""
    _, history_ids, _ = _gen_history(client, monkeypatch)
    client.post("/api/evaluations", json={"history_id": history_ids[1], "sendability": "minor_edit"})
    flow = contrast.evaluation_flow_counts()
    assert flow["explicit_total"] == 1
    assert flow["by_sendability"].get("minor_edit") == 1


def test_explicit_overrides_implicit(client, monkeypatch):
    """explicit overrides implicit: 送信後に「使えない」と評価しても両立保持。」"""
    cid, history_ids, _ = _gen_history(client, monkeypatch)
    client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "おつかれさまです",
        "source": "generated", "generation_history_id": history_ids[0],
    })
    r = client.post("/api/evaluations", json={"history_id": history_ids[0], "sendability": "rejected"})
    assert r.status_code == 200
    # 明示が優先され、自動スコアは保持される
    assert r.json()["sendability"] == "rejected"
    assert r.json()["naturalness_score"] is not None
    flow = contrast.evaluation_flow_counts()
    assert flow["explicit_total"] == 1
    # 明示済みのため implicit カウントからは外れる
    assert flow["implicit_sent_total"] == 0


def test_manual_replacement(client, monkeypatch):
    """manual replacement: 編集送信で manual_replaced になること。」"""
    cid, _, batch_id = _gen_history(client, monkeypatch)
    r = client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "おつかれ！", "source": "manual",
    })
    assert r.status_code == 201
    conn = database.get_conn()
    try:
        b = conn.execute("SELECT outcome FROM generation_batches WHERE id = ?",
                         (batch_id,)).fetchone()
        assert b["outcome"] == "manual_replaced"
    finally:
        conn.close()
    assert contrast.classify_edit_magnitude("おつかれさまです", "おつかれ！") in (
        "minor", "major", "rewrite",
    )


def test_candidate_provenance(client, monkeypatch):
    """candidate provenance: batch/history/index/contact を追跡できること。」"""
    cid, history_ids, batch_id = _gen_history(client, monkeypatch)
    rows = client.get("/api/evaluations", params={"batch_id": batch_id}).json()
    assert len(rows) == 3
    for e in rows:
        assert e["history_id"] in history_ids
        assert e["generation_batch_id"] == batch_id
        assert e["contact_id"] == cid
        assert e["candidate_index"] in (0, 1, 2)
        assert e["created_at"] and e["updated_at"]


def test_duplicate_prevention(client, monkeypatch):
    """duplicate prevention: 同一 history への再評価は1行に保たれること。」"""
    _, history_ids, _ = _gen_history(client, monkeypatch)
    hid = history_ids[0]
    for level in ("sendable", "sendable", "minor_edit"):
        client.post("/api/evaluations", json={"history_id": hid, "sendability": level})
    conn = database.get_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM generation_evaluations WHERE history_id = ?", (hid,)
        ).fetchone()["n"]
        assert n == 1
        row = conn.execute(
            "SELECT sendability FROM generation_evaluations WHERE history_id = ?", (hid,)
        ).fetchone()
        assert row["sendability"] == "minor_edit"
    finally:
        conn.close()


def test_evaluation_update(client, monkeypatch):
    """evaluation update: 後から変更でき、履歴は最新で取得できること。」"""
    _, history_ids, _ = _gen_history(client, monkeypatch)
    hid = history_ids[0]
    client.post("/api/evaluations", json={"history_id": hid, "rating": "good"})
    r = client.post("/api/evaluations", json={"history_id": hid, "rating": "bad"})
    assert r.json()["human_rating"] == "bad"
    rows = client.get("/api/evaluations").json()
    assert [e for e in rows if e["history_id"] == hid][0]["human_rating"] == "bad"


def test_diagnostics(client, monkeypatch):
    """diagnostics: evaluation_flow と integrity が含まれること。」"""
    cid, history_ids, _ = _gen_history(client, monkeypatch)
    client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "おつかれさまです",
        "source": "generated", "generation_history_id": history_ids[0],
    })
    client.post("/api/evaluations", json={"history_id": history_ids[1], "sendability": "sendable"})
    r = client.get("/api/learning/diagnostics")
    assert r.status_code == 200
    data = r.json()
    assert data["evaluation_flow"]["explicit_total"] == 1
    assert data["evaluation_flow"]["implicit_sent_total"] == 1
    assert data["evaluation_flow"]["by_sendability"].get("sendable") == 1
    assert data["evaluation_integrity"] == {
        "unlinked_evaluations": 0,
        "missing_history": 0,
        "duplicate_evaluations": 0,
    }


def test_unlinked_detection(client):
    """unlinked detection: 参照切れ検出が動作すること（正常時は0件）。"""
    integrity = contrast.evaluation_integrity()
    assert integrity["unlinked_evaluations"] == 0
    assert integrity["missing_history"] == 0
    assert integrity["duplicate_evaluations"] == 0


def test_e2e_full_flow(client, monkeypatch):
    """E2E: contact→generate→選択→送信→評価→diagnostics の一連。」"""
    cid, history_ids, batch_id = _gen_history(client, monkeypatch, "眠い")
    # 選択（採用）
    assert client.patch(f"/api/history/{history_ids[0]}", json={"is_adopted": True}).status_code == 200
    # 送信
    assert client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "おつかれさまです",
        "source": "generated", "generation_history_id": history_ids[0],
    }).status_code in (200, 201)
    # 評価
    assert client.post("/api/evaluations", json={
        "history_id": history_ids[0], "sendability": "sendable",
    }).status_code == 200
    # diagnostics
    data = client.get("/api/learning/diagnostics").json()
    assert data["evaluation_flow"]["explicit_total"] == 1


def test_e2e_edit_flow(client, monkeypatch):
    """E2E: generate→編集→送信→manual_replaced→評価。」"""
    cid, history_ids, batch_id = _gen_history(client, monkeypatch, "今日バイト8時間だった")
    r = client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "それはきついな", "source": "manual",
    })
    assert r.status_code == 201
    magnitude = contrast.classify_edit_magnitude("おつかれさまです", "それはきついな")
    assert magnitude in ("minor", "major", "rewrite")
    assert client.post("/api/evaluations", json={
        "history_id": history_ids[0], "sendability": "major_edit",
    }).status_code == 200
