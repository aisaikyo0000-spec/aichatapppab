"""Step 5: 生成品質評価パイプラインのテスト。

- 自動評価（生成時に保存）と人間評価（POST で upsert）は別々に保存される
- 自動評価を人間評価で書き換えない
- export（CSV/JSON）が動作する
- 既存 history/batch との関連が壊れない
- Naturalness Score 自体は変更されていない（決定性）
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT.parents[0] / "scripts"))

from app.ai.naturalness import evaluate_candidate_naturalness  # noqa: E402


def _fake_config():
    return {
        "provider": "fake",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    }


def _generate_three(client, monkeypatch, contact_text="今日バイト8時間だった"):
    """モック生成で3候補を作り、(batch_id, history_ids) を返す。"""
    cid = client.post("/api/contacts", json={"name": "評価相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": contact_text})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            return json.dumps({"replies": ["それはきついな", "8時間は長いね", "おつかれ"]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", _fake_config)

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    return r.json()["batch_id"], r.json()["history_ids"]


def test_1_save_and_get_evaluation(client, monkeypatch):
    """Test 1: 評価を保存・取得できる。生成時に自動評価行が作られていること。"""
    batch_id, history_ids = _generate_three(client, monkeypatch)

    # 自動保存: スコアあり・人間評価なし
    r = client.get("/api/evaluations")
    assert r.status_code == 200
    auto_rows = [e for e in r.json() if e["history_id"] in history_ids]
    assert len(auto_rows) == 3
    for e in auto_rows:
        assert e["generation_batch_id"] == batch_id
        assert e["naturalness_score"] is not None
        assert e["style_score"] is not None
        assert e["final_score"] is not None
        assert e["human_rating"] is None
        assert e["generated_text"] != ""
        assert e["counterpart_message"] == "今日バイト8時間だった"

    # 人間評価を保存
    hid = history_ids[0]
    r = client.post("/api/evaluations", json={
        "history_id": hid,
        "rating": "good",
        "feedback": "そのまま送れる",
        "feedback_tags": ["good", "natural"],
    })
    assert r.status_code == 200
    data = r.json()
    assert data["history_id"] == hid
    assert data["human_rating"] == "good"
    assert data["human_feedback"] == "そのまま送れる"
    assert data["feedback_tags"] == ["good", "natural"]
    # 自動スコアは保持されている
    assert data["naturalness_score"] is not None


def test_2_same_history_id_upsert(client, monkeypatch):
    """Test 2: 同じ history_id への再保存は upsert（行が増えない・後勝ち）。"""
    _, history_ids = _generate_three(client, monkeypatch)
    hid = history_ids[0]

    client.post("/api/evaluations", json={"history_id": hid, "rating": "good"})
    r = client.post("/api/evaluations", json={
        "history_id": hid, "rating": "bad", "feedback": "AIっぽい",
        "feedback_tags": ["ai_like"],
    })
    assert r.status_code == 200
    assert r.json()["human_rating"] == "bad"
    assert r.json()["human_feedback"] == "AIっぽい"

    rows = [e for e in client.get("/api/evaluations").json() if e["history_id"] == hid]
    assert len(rows) == 1


def test_3_null_rating_allowed(client, monkeypatch):
    """Test 3: human_rating が NULL でも保存できる（自動評価のみの行）。"""
    _, history_ids = _generate_three(client, monkeypatch)
    hid = history_ids[1]

    r = client.post("/api/evaluations", json={"history_id": hid, "feedback": "後で評価する"})
    assert r.status_code == 200
    assert r.json()["human_rating"] is None
    assert r.json()["human_feedback"] == "後で評価する"

    r = client.get("/api/evaluations", params={"rating": "unrated"})
    assert r.status_code == 200
    assert any(e["history_id"] == hid for e in r.json())


def test_4_invalid_rating_and_tags_rejected(client, monkeypatch):
    """Test 4: 不正な rating・tag・history_id を拒否する。"""
    _, history_ids = _generate_three(client, monkeypatch)
    hid = history_ids[0]

    r = client.post("/api/evaluations", json={"history_id": hid, "rating": "great"})
    assert r.status_code == 422

    r = client.post("/api/evaluations", json={"history_id": hid, "rating": "good", "feedback_tags": ["unknown_tag"]})
    assert r.status_code == 422

    r = client.post("/api/evaluations", json={"history_id": 99999, "rating": "good"})
    assert r.status_code == 404


def test_5_list_filters(client, monkeypatch):
    """Test 5: batch / rating フィルタで取得できる。"""
    batch_id, history_ids = _generate_three(client, monkeypatch)
    client.post("/api/evaluations", json={"history_id": history_ids[0], "rating": "good"})
    client.post("/api/evaluations", json={"history_id": history_ids[1], "rating": "bad"})

    r = client.get("/api/evaluations", params={"batch_id": batch_id})
    assert r.status_code == 200
    assert len(r.json()) == 3

    r = client.get("/api/evaluations", params={"rating": "good"})
    assert r.status_code == 200
    assert all(e["human_rating"] == "good" for e in r.json())
    assert any(e["history_id"] == history_ids[0] for e in r.json())

    r = client.get("/api/evaluations", params={"rating": "bad"})
    assert r.status_code == 200
    assert any(e["history_id"] == history_ids[1] for e in r.json())


def test_6_export_csv_and_json(client, monkeypatch, tmp_path):
    """Test 6: CSV/JSON export ができる。"""
    import export_evaluations

    batch_id, history_ids = _generate_three(client, monkeypatch)
    client.post("/api/evaluations", json={"history_id": history_ids[0], "rating": "good", "feedback": "自然"})

    from app import config

    csv_path, csv_count = export_evaluations.export_evaluations(
        config.DB_PATH, "csv", tmp_path / "evals.csv"
    )
    assert csv_count == 3
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f.read().splitlines())
        assert "human_rating" in (reader.fieldnames or [])
        rows = list(reader)
    assert len(rows) == 3
    by_hist = {row["history_id"]: row for row in rows}
    assert by_hist[str(history_ids[0])]["human_rating"] == "good"
    assert by_hist[str(history_ids[0])]["counterpart_intent"] == "report"
    assert by_hist[str(history_ids[0])]["generated_text"] != ""

    json_path, json_count = export_evaluations.export_evaluations(
        config.DB_PATH, "json", tmp_path / "evals.json"
    )
    assert json_count == 3
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(data) == 3
    assert data[0]["batch_id"] == batch_id


def test_7_history_relation_intact(client, monkeypatch):
    """Test 7: 既存 history/batch との関連が壊れない。"""
    batch_id, history_ids = _generate_three(client, monkeypatch)

    r = client.get(f"/api/history?contact_id=1")
    assert r.status_code == 200

    evals = [e for e in client.get("/api/evaluations").json() if e["history_id"] in history_ids]
    hists = {h["id"]: h for h in client.get("/api/history").json()}
    for e in evals:
        assert e["history_id"] in hists
        assert e["generated_text"] == hists[e["history_id"]]["generated_text"]
        assert e["generation_batch_id"] == batch_id


def test_8_naturalness_unchanged_and_deterministic():
    """Test 8: Naturalness Score が変更されていない（決定性＋既知の順序）。"""
    ledger = {"counterpart_intent": "report", "known_self_facts": [], "already_asked_questions": []}
    cp = "今日バイト8時間だった"
    a1 = evaluate_candidate_naturalness("それはきついな", cp, ledger, [])
    a2 = evaluate_candidate_naturalness("それはきついな", cp, ledger, [])
    assert a1 == a2
    assert a1["score"] >= 0.80
    b = evaluate_candidate_naturalness("それは大変だったね！どこで働いてるの？", cp, ledger, [])
    c = evaluate_candidate_naturalness("今日8時間バイトだったんだね。お疲れ様！", cp, ledger, [])
    assert a1["score"] > b["score"] > c["score"]
