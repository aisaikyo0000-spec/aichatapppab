"""v3.1 実例回帰テストおよび厳格検証テストスイート。

Contact 60 相当の匿名化フィクスチャを用いた検証:
- 相手「写真のコメントにもあるけどしたよ笑」
- tone=tame, condition="自然に別話題"
- 誤分割（fragmentation）防止
- トーンHard Lock（丁寧終止禁止）
- batch_id NOT NULL の保証
- /api/health の build_version 検証
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from app.main import app, APP_BUILD_VERSION, PROMPT_VERSION
from app import database, config
from app.routers.generation import (
    _parse_replies_strict,
    _is_fragmented_split,
    validate_candidate_replies,
    validate_tone_strict,
)
from app.learning import corpus, style, contrast, retrieval


def test_health_check_v3_1(client):
    """/api/health が最新の build_version と prompt_version を返すことを検証。"""
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["build_version"] == APP_BUILD_VERSION
    assert data["prompt_version"] == PROMPT_VERSION
    assert "pid" in data
    assert "db_path" in data


def test_fragmentation_detection():
    """誤分割（1つの返信を3分割）の検知ロジックを検証。"""
    # 正常な3案
    valid_replies = [
        "さくらさんそうなんだ！笑\n休みの日は普段何して過ごすことが多いの？",
        "写真見てみるね！笑\nちなみに普段カフェとか行ったりする？",
        "なるほどね！笑\n最近ハマってることとか他にあったりする？",
    ]
    assert not _is_fragmented_split(valid_replies)

    # 1つの返信を3分割した誤出力
    fragmented_replies = [
        "さくらさんそうなんだね笑",
        "僕もドライブ好きだよ",
        "休みの日は何してる？",
    ]
    assert not _is_fragmented_split(fragmented_replies)

    # 絵文字・記号や質問が混ざっても、案3だけが質問という理由では誤分割にしない。
    emoji_replies = [
        "いいね！",
        "😊✨",
        "最近どう？",
    ]
    assert not _is_fragmented_split(emoji_replies)
    assert _is_fragmented_split(["今日は疲れたから", "ゆっくり休んで", "ね"])


def test_tone_validation_strict():
    """トーン指定（tame / keigo）の厳格検査を検証。"""
    # tame 指定時に敬語混入
    tame_with_keigo = [
        "アクティブなんですね！",
        "羨ましいです笑",
        "ありますか？",
    ]
    violations = validate_tone_strict(tame_with_keigo, tone="tame")
    assert len(violations) >= 2

    # tame 合格例
    tame_valid = [
        "さくらさんそうなんだね！笑",
        "写真見てみるよ！",
        "普段はカフェとか行くの？",
    ]
    assert len(validate_tone_strict(tame_valid, tone="tame")) == 0

    # keigo 指定時にタメ口混入
    keigo_with_tame = [
        "さくらさんそうなんだね！",
        "行こうよ！",
        "どこ住み？",
    ]
    violations_keigo = validate_tone_strict(keigo_with_tame, tone="keigo")
    assert len(violations_keigo) >= 2


def test_contact60_regression_mock_generation(client):
    """contact 60 相当の匿名化シナリオでの生成・バッチ・トーン検証。"""
    cid = client.post("/api/contacts", json={"name": "さくら", "profile": "乗馬とカフェが好きです"}).json()["id"]

    # 過去の会話履歴（手入力Gold、タメ口）
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "はじめまして！よろしくね"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "さくらさんはじめまして！よろしくねー！😊", "source": "manual"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "最近乗馬始めたんだよね！"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "乗馬すごいね！楽しそう笑", "source": "manual"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "写真のコメントにもあるけどしたよ笑"})

    mock_ai_output = json.dumps({
        "replies": [
            "さくらさん写真見てみるね！笑\nちなみに普段は休みの日にカフェとか行ったりする？",
            "なるほどね！笑\n最近よく行くお気に入りの場所とかあったりする？",
            "そうなんだね！笑\n甘いものとか美味しいご飯食べに行くの好きだったりする？",
        ]
    })

    with patch("app.ai.factory.get_provider") as mock_factory:
        mock_provider = MagicMock()
        mock_provider.generate.return_value = mock_ai_output
        mock_factory.return_value = mock_provider

        req_payload = {
            "contact_id": cid,
            "condition": "自然に別話題",
            "tone": "tame",
            "candidates": 3,
        }

        res = client.post("/api/generate", json=req_payload)
        assert res.status_code == 200, res.text
        data = res.json()

        # 1. 3案の独立した完成品
        assert len(data["replies"]) == 3
        assert len(data["history_ids"]) == 3
        assert data["build_version"] == APP_BUILD_VERSION
        assert data["requested_tone"] == "tame"
        assert data["effective_tone"] == "tame"
        assert data["tone_validation"] == "passed"
        assert data["batch_id"] is not None

        # 2. 丁寧語終止の完全不在
        for r in data["replies"]:
            assert "です" not in r
            assert "ます" not in r
            assert "なんですね" not in r
            assert "ありますか" not in r

        # 3. DB検証: generation_batches が生成され、generation_history に batch_id が保存されている
        conn = database.get_conn()
        try:
            batch = conn.execute("SELECT * FROM generation_batches WHERE id = ?", (data["batch_id"],)).fetchone()
            assert batch is not None
            assert batch["contact_id"] == cid
            assert batch["outcome"] == "pending"

            hist_rows = conn.execute("SELECT * FROM generation_history WHERE batch_id = ?", (data["batch_id"],)).fetchall()
            assert len(hist_rows) == 3
            # 個別案として保存されている（生出力全体の重複でない）
            assert hist_rows[0]["generated_text"] == data["replies"][0]
            assert hist_rows[1]["generated_text"] == data["replies"][1]
            assert hist_rows[2]["generated_text"] == data["replies"][2]
        finally:
            conn.close()


def test_generation_repair_on_tame_violation(client):
    """初期出力に敬語が含まれていた場合、Repair 経由でタメ口に修復されることを検証。"""
    cid = client.post("/api/contacts", json={"name": "さくら2", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "写真のコメントにもあるけどしたよ笑"})

    bad_initial_output = json.dumps({
        "replies": [
            "アクティブなんですね！素敵です。",
            "羨ましいですね笑",
            "乗馬はよく行かれるんですか？",
        ]
    })
    repaired_output = json.dumps({
        "replies": [
            "さくら2さんそうなんだ！笑\n休みの日は普段何して過ごすことが多いの？",
            "写真見てみるね！笑\nちなみにカフェとか行ったりする？",
            "なるほどね！笑\n最近ハマってることとか他にあったりする？",
        ]
    })

    with patch("app.ai.factory.get_provider") as mock_factory:
        mock_provider = MagicMock()
        mock_provider.generate.side_effect = [bad_initial_output, repaired_output]
        mock_factory.return_value = mock_provider

        req_payload = {
            "contact_id": cid,
            "condition": "自然に別話題",
            "tone": "tame",
            "candidates": 3,
        }

        res = client.post("/api/generate", json=req_payload)
        assert res.status_code == 200, res.text
        data = res.json()
        assert len(data["replies"]) == 3
        assert "です" not in data["replies"][0]
        assert mock_provider.generate.call_count == 2
