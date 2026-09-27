"""追いメッセージ（再活性化・会話復活）生成機能のユニットテストおよびE2Eテスト。"""
import json
import pytest
from app.ai import prompt
from app.routers import generation


def test_followup_system_prompt_structure():
    """追いメッセージモード時に催促禁止および3つの戦略アプローチ契約がプロンプトに含まれること。"""
    contact = {
        "name": "みお",
        "profile": "カフェ巡りとドライブが好きです！最近はパンケーキにハマってます🥞",
    }
    sysp = prompt.build_system_prompt(
        contact=contact,
        mode="followup",
        condition="カフェの話題で",
    )

    # 1. 追いメッセージディレクティブ
    assert "【追いメッセージ（再活性化・会話復活モード）】" in sysp
    assert "催促の完全禁止" in sysp
    assert "返信まだ" in sysp or "催促・問い詰め表現は全案で完全禁止" in sysp

    # 2. OUTPUT CONTRACT 3戦略アプローチ
    assert "追いメッセージ出力契約" in sysp
    assert "案1【行動報告・新事実フック】" in sysp
    assert "案2【軽快ツッコミ・ユーモアリセット】" in sysp
    assert "案3【写真なし体験・情景共有フック】" in sysp
    assert "直前の途切れた古い話題を続けることは厳禁" in sysp


def test_followup_validation_pressure_keywords():
    """追いメッセージモード時に催促・問い詰め表現が含まれる案をバリデーションで検知すること。"""
    # 正常な追いメッセージ案
    valid_followups = [
        "そういえばみおさんが言ってたパンケーキ屋さん見かけました笑\n結構並んでました！\nもう行かれました？",
        "もしかして冬眠中ですか？笑\n週末ゆっくり休めそうですか？",
        "今日食べたラーメンが美味しすぎて思わず共有です笑\n最近美味しいもの食べました？",
    ]
    violations = generation.validate_candidate_replies(valid_followups, expected_candidates=3, mode="followup")
    assert violations == []

    # 催促表現が含まれる案
    invalid_followups = [
        "返信まだですか？忙しいですか？",
        "パンケーキとクレープならどっち派ですか？笑",
        "既読スルー悲しいです！返事待ってます！",
    ]
    violations = generation.validate_candidate_replies(invalid_followups, expected_candidates=3, mode="followup")
    assert len(violations) >= 2
    assert any("催促や返信を問い詰める表現" in v for v in violations)


def test_e2e_generate_followup_mode(client, monkeypatch):
    """E2Eで mode='followup' を指定して生成APIが正常に動作すること。"""
    cid = client.post("/api/contacts", json={"name": "追いテスト相手", "profile": "旅行とカフェ"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "はじめまして！よろしくお願いします"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "はじめまして！\nカフェよく行かれるんですか？", "source": "manual"})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            all_str = "".join(str(m) for m in messages)
            assert "追いメッセージ" in all_str
            assert "行動報告・新事実フック" in all_str
            assert "写真なし体験・情景共有フック" in all_str

            return json.dumps({
                "replies": [
                    "そういえばプロフのカフェ気になって近くを通ったら思い出しました笑\n最近新しいお店行きました？",
                    "もしかして冬眠中ですか？笑\n最近バタバタしてましたかね！",
                    "今日食べたスイーツが美味しすぎて思わず共有です笑\n最近当たりのお店ありました？",
                ]
            })

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

    r = client.post("/api/generate", json={"contact_id": cid, "mode": "followup", "candidates": 3})
    assert r.status_code == 200
    data = r.json()
    assert len(data["replies"]) == 3
    for rep in data["replies"]:
        assert "\n" in rep
        assert "？" in rep or "?" in rep
