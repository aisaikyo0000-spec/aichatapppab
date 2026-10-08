"""APIの基本テスト。

外部AI APIには接続しない（モック利用）。
実行: cd backend && ../.venv/Scripts/python -m pytest tests -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, database  # noqa: E402
from app.main import app  # noqa: E402




def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_contact_crud(client):
    # 作成
    r = client.post("/api/contacts", json={"name": "さくら", "profile": "映画好き"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["name"] == "さくら"

    # 一覧（最終メッセージは空）
    r = client.get("/api/contacts")
    assert r.status_code == 200
    assert r.json()[0]["last_message"] == ""

    # 更新
    r = client.patch(f"/api/contacts/{cid}", json={"is_pinned": True})
    assert r.status_code == 200
    assert r.json()["is_pinned"] is True

    # 削除
    r = client.delete(f"/api/contacts/{cid}")
    assert r.status_code == 204
    r = client.get(f"/api/contacts/{cid}")
    assert r.status_code == 404


def test_message_crud_and_export(client):
    cid = client.post("/api/contacts", json={"name": "テストさん", "profile": "カフェ巡りが好き"}).json()["id"]

    r = client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "こんにちは"})
    assert r.status_code == 201
    mid = r.json()["id"]

    r = client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "はじめまして"})
    assert r.status_code == 201

    r = client.get(f"/api/contacts/{cid}/messages")
    assert len(r.json()) == 2

    # 一覧の最終メッセージが更新されている
    r = client.get("/api/contacts")
    assert r.json()[0]["last_message"] == "はじめまして"

    # 編集
    r = client.patch(f"/api/messages/{mid}", json={"content": "こんにちは！（編集）"})
    assert r.status_code == 200
    assert r.json()["content"] == "こんにちは！（編集）"

    # エクスポート
    r = client.get(f"/api/contacts/{cid}/export")
    assert r.status_code == 200
    assert "チャット履歴" in r.text
    assert "カフェ巡りが好き" in r.text

    # 削除
    r = client.delete(f"/api/messages/{mid}")
    assert r.status_code == 204
    r = client.get(f"/api/contacts/{cid}/messages")
    assert len(r.json()) == 1


def test_generate_preview(client):
    """AIを呼び出さずに、AIへ渡される内容を組み立てて返す（§32 AI送信内容確認）。"""
    cid = client.post(
        "/api/contacts", json={"name": "さくら", "profile": "カフェ巡りが好き"}
    ).json()["id"]
    client.post(
        f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェ行きたいな"}
    )
    client.post(
        "/api/knowledge",
        json={"type": "rules", "file_name": "ルールA", "content": "自然な日本語にする"},
    )

    # Providerは呼ばれない（外部API不要）
    r = client.post(
        "/api/generate/preview",
        json={"contact_id": cid, "condition": "映画の話に", "candidates": 1},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rules"] == ["自然な日本語にする"]
    assert body["contact"]["name"] == "さくら"
    assert "カフェ行きたいな" in body["chat_history"]
    assert body["condition"] == "映画の話に"
    assert body["provider"]
    assert body["model"]
    assert "api_key" not in str(body).lower()


def test_contact_search_message_body(client):
    """検索は相手名だけでなくメッセージ本文にも一致する（§34 チャット検索）。"""
    a = client.post("/api/contacts", json={"name": "さくら"}).json()["id"]
    b = client.post("/api/contacts", json={"name": "ゆい"}).json()["id"]
    client.post(f"/api/contacts/{a}/messages", json={"sender": "contact", "content": "週末に映画を見たよ"})
    client.post(f"/api/contacts/{b}/messages", json={"sender": "contact", "content": "カフェ行きたいな"})

    r = client.get("/api/contacts", params={"search": "映画"})
    ids = [c["id"] for c in r.json()]
    assert ids == [a]

    r = client.get("/api/contacts", params={"search": "カフェ"})
    ids = [c["id"] for c in r.json()]
    assert ids == [b]


def test_generate_api_key_missing(client, monkeypatch):
    cid = client.post("/api/contacts", json={"name": "テストさん"}).json()["id"]
    # DBにも環境変数にもキーが無ければ api_key_missing になる
    database.set_setting("ai_provider", "cerebras")
    database.set_setting("api_key_cerebras", "")
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 1})
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "api_key_missing"


def test_generate_success_and_history(client, monkeypatch):
    cid = client.post("/api/contacts", json={"name": "テストさん", "profile": "カフェ巡りが好き"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェ行きたい"})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            assert any("RULES" in str(m) or "カフェ行きたい" in str(m) for m in messages)
            reply1 = "いいですね！\nカフェ巡りよく行きます\nおすすめのお店ありますか？"
            reply2 = "カフェ行きたいですね！\n最近新しいお店探してました笑\nどこか気になるところありますか？"
            reply3 = "カフェいいですね！\nのんびり過ごすの好きです\n今度おすすめ教えてくれませんか？"
            return json.dumps({"replies": [reply1, reply2, reply3]}) if json_mode else reply1

        def available_models(self):
            return []

    monkeypatch.setattr(
        "app.routers.generation.factory.get_provider",
        lambda *a, **k: FakeProvider(),
    )
    monkeypatch.setattr(
        "app.routers.generation.get_ai_config",
        lambda: {
            "provider": "fake",
            "model": "fake-model",
            "api_key": "x",
            "temperature": 0.8,
            "max_tokens": 512,
            "history_limit": 50,
        },
    )

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 1})
    assert r.status_code == 200
    assert r.json()["replies"] == ["いいですね！\nカフェ巡りよく行きます\nおすすめのお店ありますか？"]

    # 3案
    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    assert len(r.json()["replies"]) == 3

    # 履歴に各案個別に記録されている（1案で1件、3案で3件計4件）
    r = client.get(f"/api/history?contact_id={cid}")
    assert r.status_code == 200
    assert len(r.json()) == 4
    assert r.json()[0]["model"] == "fake-model"
    # 全レコードが全案同一の生テキストではなく個別案になっていることを確認
    history_texts = [h["generated_text"] for h in r.json()]
    assert "おすすめのお店ありますか？" in history_texts[3]
    assert "気になるところありますか？" in history_texts[1]


def test_knowledge_files(client):
    r = client.post("/api/knowledge", json={"type": "rules", "file_name": "ルールA", "content": "自然な日本語にする"})
    assert r.status_code == 201

    r = client.get("/api/knowledge?type=rules")
    assert r.status_code == 200
    assert len(r.json()) == 1
    kid = r.json()[0]["id"]

    # 無効化
    r = client.patch(f"/api/knowledge/{kid}", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    # 削除
    r = client.delete(f"/api/knowledge/{kid}")
    assert r.status_code == 204
    r = client.get("/api/knowledge")
    assert r.json() == []

    # 学習用（training）タイプの作成も可能
    r = client.post(
        "/api/knowledge",
        json={"type": "training", "file_name": "学習メモ", "content": "短文で返すのが好み"},
    )
    assert r.status_code == 201
    r = client.get("/api/knowledge?type=training")
    assert len(r.json()) == 1
    assert r.json()[0]["type"] == "training"


def test_knowledge_edit(client):
    """内容の編集・リネームがDBとTXTファイルの両方に反映される。"""
    r = client.post("/api/knowledge", json={"type": "rules", "file_name": "ルールA", "content": "自然な日本語にする"})
    assert r.status_code == 201
    kid = r.json()["id"]

    # 内容の取得
    r = client.get(f"/api/knowledge/{kid}")
    assert r.status_code == 200
    assert r.json()["content"] == "自然な日本語にする"

    # 内容の編集（TXTにも書き戻される）
    r = client.patch(f"/api/knowledge/{kid}", json={"content": "短文で返す\n絵文字は控えめに"})
    assert r.status_code == 200
    r = client.get(f"/api/knowledge/{kid}")
    assert r.json()["content"] == "短文で返す\n絵文字は控えめに"
    assert (config.KNOWLEDGE_RULES_DIR / "ルールA.txt").read_text(encoding="utf-8") == "短文で返す\n絵文字は控えめに"

    # リネーム（TXTもリネームされる）
    r = client.patch(f"/api/knowledge/{kid}", json={"file_name": "ルールB"})
    assert r.status_code == 200
    assert r.json()["file_name"] == "ルールB.txt"
    assert not (config.KNOWLEDGE_RULES_DIR / "ルールA.txt").exists()
    assert (config.KNOWLEDGE_RULES_DIR / "ルールB.txt").exists()

    # 同名リネームは409
    client.post("/api/knowledge", json={"type": "rules", "file_name": "ルールC", "content": "x"})
    r = client.patch(f"/api/knowledge/{kid}", json={"file_name": "ルールC"})
    assert r.status_code == 409

    # 存在しないIDは404
    r = client.patch("/api/knowledge/9999", json={"content": "x"})
    assert r.status_code == 404
    r = client.get("/api/knowledge/9999")
    assert r.status_code == 404


def test_knowledge_reload(client):
    """フォルダにTXTを置いて再読み込みすると登録され、削除すると登録解除される。"""
    rules_dir = config.KNOWLEDGE_RULES_DIR
    training_dir = config.KNOWLEDGE_TRAINING_DIR
    rules_dir.mkdir(parents=True, exist_ok=True)
    training_dir.mkdir(parents=True, exist_ok=True)

    # フォルダにファイルを置いて再読み込み
    (rules_dir / "ルールA.txt").write_text("自然な日本語にする", encoding="utf-8")
    (training_dir / "学習資料.txt").write_text("短文が好み", encoding="utf-8")
    r = client.post("/api/knowledge/reload")
    assert r.status_code == 200
    body = r.json()
    assert body["added"] == 2
    assert body["counts"]["rules"]["added"] == 1
    assert body["counts"]["training"]["added"] == 1

    # 再実行しても増えない（冪等）
    r = client.post("/api/knowledge/reload")
    assert r.json()["added"] == 0
    assert r.json()["removed"] == 0

    # フォルダからファイルを消すと登録解除される
    (rules_dir / "ルールA.txt").unlink()
    r = client.post("/api/knowledge/reload")
    assert r.json()["removed"] == 1
    assert r.json()["counts"]["rules"]["removed"] == 1
    r = client.get("/api/knowledge?type=rules")
    assert r.json() == []


def test_settings_do_not_expose_api_key(client):
    database.set_setting("api_key_cerebras", "secret-key")
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["has_api_key"] is True
    assert "secret-key" not in str(body)


def test_settings_update_does_not_clear_api_key_on_empty(client):
    """空文字のapi_keyではキーが消えない（モデル切替時の誤消去防止）。"""
    database.set_setting("api_key_cerebras", "secret-key")
    r = client.put("/api/settings", json={"api_key": "", "temperature": 0.9})
    assert r.status_code == 200
    assert database.get_setting("api_key_cerebras") == "secret-key"
    # プロバイダ切替を伴う保存でもキーは消えない
    r = client.put("/api/settings", json={"provider": "nvidia", "model": "nvidia/nemotron-3-nano-30b-a3b"})
    assert r.status_code == 200
    assert database.get_setting("api_key_cerebras") == "secret-key"
    # 明示的なクリアで消える（クリア対象はその時点のプロバイダ）
    r = client.put("/api/settings", json={"provider": "cerebras", "clear_api_key": True})
    assert r.status_code == 200
    assert database.get_setting("api_key_cerebras") == ""


def test_gemini_provider_registered(client):
    """Geminiプロバイダが設定APIに表示され、モデル一覧が返る。"""
    r = client.get("/api/settings")
    providers = {p["name"]: p["models"] for p in r.json()["providers"]}
    assert "gemini" in providers
    assert providers["gemini"][0] == "gemini-3.5-flash"


def test_gemini_provider_api_key_missing():
    """キー未設定ならapi_key_missingエラー。"""
    from app.ai.base import AIError
    from app.ai.gemini import GeminiProvider

    p = GeminiProvider("")
    try:
        p.generate(model="gemini-2.5-flash", messages=[], temperature=0.8, max_tokens=1024)
        assert False, "api_key_missingが発生すべき"
    except AIError as e:
        assert e.code == "api_key_missing"


def test_contact_ai_settings(client):
    cid = client.post("/api/contacts", json={"name": "テストさん"}).json()["id"]

    r = client.get(f"/api/contacts/{cid}/ai-settings")
    assert r.status_code == 200
    assert r.json()["provider"] is None

    kid = client.post(
        "/api/knowledge", json={"type": "rules", "file_name": "独自ルール", "content": "短く返す"}
    ).json()["id"]

    r = client.put(
        f"/api/contacts/{cid}/ai-settings",
        json={"provider": "cerebras", "model": "gpt-oss-20b", "temperature": 0.3, "knowledge_file_ids": [kid]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "cerebras"
    assert body["model"] == "gpt-oss-20b"
    assert body["temperature"] == 0.3
    assert body["knowledge_file_ids"] == [kid]

    # 設定を全体設定に戻す
    r = client.put(f"/api/contacts/{cid}/ai-settings", json={})
    assert r.status_code == 200
    assert r.json()["provider"] is None
    assert r.json()["knowledge_file_ids"] is None


PNG_1PX = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_contact_images(client):
    cid = client.post("/api/contacts", json={"name": "さくら"}).json()["id"]

    r = client.post(
        f"/api/contacts/{cid}/images",
        files={"file": ("a.png", PNG_1PX, "image/png")},
        data={"description": "海辺で撮影"},
    )
    assert r.status_code == 201
    img = r.json()
    assert img["description"] == "海辺で撮影"

    # ファイル配信
    r = client.get(f"/api/images/{img['id']}/file")
    assert r.status_code == 200
    assert r.content == PNG_1PX

    # 説明更新
    r = client.patch(f"/api/images/{img['id']}", json={"description": "カフェで撮影"})
    assert r.status_code == 200
    assert r.json()["description"] == "カフェで撮影"

    # 一覧
    r = client.get(f"/api/contacts/{cid}/images")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # 不正な拡張子は拒否
    r = client.post(f"/api/contacts/{cid}/images", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 400

    # 削除
    r = client.delete(f"/api/images/{img['id']}")
    assert r.status_code == 204
    r = client.get(f"/api/contacts/{cid}/images")
    assert r.json() == []


def _mock_training_ai(monkeypatch):
    """AI呼び出しをモックする。"""

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            if json_mode:
                return (
                    '{"naturalness":{"score":4,"comment":"自然です"},'
                    '"continuity":{"score":3,"comment":"もう少し質問が欲しい"},'
                    '"distance":{"score":5,"comment":"適切な距離感"},'
                    '"fit":{"score":4,"comment":"相手の発言に合っている"},'
                    '"overall":{"score":4,"comment":"総合的に良い返信"}}'
                )
            return "練習相手からの返信です！"

        def available_models(self):
            return []

    monkeypatch.setattr(
        "app.routers.training.factory.get_provider", lambda *a, **k: FakeProvider()
    )
    monkeypatch.setattr(
        "app.routers.training.get_ai_config",
        lambda: {
            "provider": "fake",
            "model": "fake-model",
            "api_key": "x",
            "temperature": 0.8,
            "max_tokens": 512,
            "history_limit": 50,
        },
    )


def test_training_sessions_and_ai_reply(client, monkeypatch):
    _mock_training_ai(monkeypatch)
    cid = client.post("/api/contacts", json={"name": "さくら"}).json()["id"]

    r = client.post("/api/training/sessions", json={"contact_id": cid})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["persona_name"] == "さくら"

    # 架空の相手名でも作成できる
    r = client.post("/api/training/sessions", json={"persona_name": "はな"})
    assert r.status_code == 201
    assert r.json()["persona_name"] == "はな"

    # ユーザーメッセージ追加 → AI返信
    r = client.post(f"/api/training/sessions/{sid}/messages", json={"content": "こんにちは！"})
    assert r.status_code == 201
    assert len(r.json()["messages"]) == 1

    r = client.post(f"/api/training/sessions/{sid}/ai-reply", json={"condition": "カフェの話で"})
    assert r.status_code == 200
    assert r.json()["reply"] == "練習相手からの返信です！"
    assert len(r.json()["messages"]) == 2
    assert r.json()["messages"][-1]["sender"] == "contact"

    r = client.get("/api/training/sessions")
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = client.get(f"/api/training/sessions/{sid}")
    assert r.status_code == 200
    assert len(r.json()["messages"]) == 2

    # セッション削除
    r = client.delete(f"/api/training/sessions/{sid}")
    assert r.status_code == 204
    r = client.get(f"/api/training/sessions/{sid}")
    assert r.status_code == 404
    r = client.get("/api/training/sessions")
    assert len(r.json()) == 1
    # 存在しないセッションの削除は404
    r = client.delete("/api/training/sessions/9999")
    assert r.status_code == 404


def test_training_session_profile(client, monkeypatch):
    """練習相手のプロフィール設定・編集・AI反映。"""
    captured = {}

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            captured["system"] = messages[0]["content"]
            return "こんにちは！"

        def available_models(self):
            return []

    monkeypatch.setattr(
        "app.routers.training.factory.get_provider", lambda *a, **k: FakeProvider()
    )

    # プロフィール付きで作成
    r = client.post(
        "/api/training/sessions",
        json={
            "persona_name": "みさき",
            "persona_gender": "女性",
            "persona_age": "24歳",
            "persona_hobbies": "カフェ巡り・映画",
            "persona_personality": "明るく穏やか",
            "persona_style": "丁寧語、絵文字多め",
        },
    )
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["persona_gender"] == "女性"
    assert "カフェ巡り・映画" in r.json()["persona_profile_display"]

    # 取得時にプロフィール項目が返る
    r = client.get(f"/api/training/sessions/{sid}")
    assert r.status_code == 200
    assert r.json()["persona_gender"] == "女性"
    assert r.json()["persona_age"] == "24歳"
    assert r.json()["persona_personality"] == "明るく穏やか"
    assert "明るく穏やか" in r.json()["persona_profile_display"]

    # 編集（部分更新）
    r = client.patch(f"/api/training/sessions/{sid}", json={"persona_hobbies": "ヨガ"})
    assert r.status_code == 200
    assert r.json()["persona_hobbies"] == "ヨガ"
    assert r.json()["persona_gender"] == "女性"  # 他の項目は保持

    # AI返信のシステムプロンプトにプロフィールが反映される
    client.post(f"/api/training/sessions/{sid}/ai-reply", json={"condition": "初対面"})
    assert "みさき" in captured["system"]
    assert "女性" in captured["system"]
    assert "ヨガ" in captured["system"]

    # 実在の相手から作ったセッションは編集不可
    cid = client.post("/api/contacts", json={"name": "さくら"}).json()["id"]
    r = client.post("/api/training/sessions", json={"contact_id": cid})
    sid2 = r.json()["id"]
    r = client.patch(f"/api/training/sessions/{sid2}", json={"persona_age": "30代"})
    assert r.status_code == 400


def test_training_my_reply_uses_self_profile(client, monkeypatch):
    """自分の返信生成が【SELF】プロフィールを使う。"""
    captured = {}

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            captured["system"] = messages[0]["content"]
            return "はじめまして！趣味の映画の話、ぜひ聞かせてください"

        def available_models(self):
            return []

    monkeypatch.setattr(
        "app.routers.training.factory.get_provider", lambda *a, **k: FakeProvider()
    )

    # 自分のプロフィールを設定
    client.put(
        "/api/profile",
        json={"name": "たろう", "hobbies": "映画", "speaking_style": "丁寧語、短文"},
    )

    # 練習セッション作成 → 相手の返信ありの状態
    r = client.post(
        "/api/training/sessions",
        json={"persona_name": "みさき", "persona_gender": "女性"},
    )
    sid = r.json()["id"]
    client.post(f"/api/training/sessions/{sid}/ai-reply", json={"condition": "初対面"})

    r = client.post(f"/api/training/sessions/{sid}/my-reply", json={"condition": "映画の話で"})
    assert r.status_code == 200
    assert "映画の話、ぜひ" in r.json()["reply"]
    assert "たろう" in captured["system"]
    assert "映画" in captured["system"]
    assert "丁寧語、短文" in captured["system"]


def test_training_ai_retry_on_empty_response(client, monkeypatch):
    """空レスポンスは1回リトライして成功する。"""
    calls = {"n": 0}

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            calls["n"] += 1
            if calls["n"] == 1:
                from app.ai.base import AIError

                raise AIError("empty", code="empty_response")
            return "こんにちは！"

        def available_models(self):
            return []

    monkeypatch.setattr(
        "app.routers.training.factory.get_provider", lambda *a, **k: FakeProvider()
    )

    r = client.post("/api/training/sessions", json={"persona_name": "はな"})
    sid = r.json()["id"]
    r = client.post(f"/api/training/sessions/{sid}/ai-reply", json={"condition": ""})
    assert r.status_code == 200
    assert r.json()["reply"] == "こんにちは！"
    assert calls["n"] == 2


def test_training_revise_my_reply(client, monkeypatch):
    """生成した返信の修正指示 → 再生成 → 履歴に記録される。"""
    captured = {}

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            captured["messages"] = messages
            return "もっと短くしました！"

        def available_models(self):
            return []

    monkeypatch.setattr(
        "app.routers.training.factory.get_provider", lambda *a, **k: FakeProvider()
    )

    r = client.post("/api/training/sessions", json={"persona_name": "はな"})
    sid = r.json()["id"]
    client.post(f"/api/training/sessions/{sid}/ai-reply", json={"condition": ""})

    r = client.post(
        f"/api/training/sessions/{sid}/revise-my-reply",
        json={
            "condition": "カフェの話",
            "revision_instruction": "もっと短くして",
            "original_generated": "はじめまして、ゆうたです！",
        },
    )
    assert r.status_code == 200
    assert r.json()["reply"] == "もっと短くしました！"

    # 修正指示がプロンプトに含まれる
    user_msgs = [m["content"] for m in captured["messages"] if m["role"] == "user"]
    assert any("もっと短くして" in m for m in user_msgs)

    # 履歴に記録されている
    r = client.get("/api/training/revisions")
    assert r.status_code == 200
    assert len(r.json()) == 1
    rev = r.json()[0]
    assert rev["revision_instruction"] == "もっと短くして"
    assert rev["original_generated"] == "はじめまして、ゆうたです！"
    assert rev["revised_text"] == "もっと短くしました！"

    # セッションを削除すると履歴も消える（CASCADE）
    client.delete(f"/api/training/sessions/{sid}")
    r = client.get("/api/training/revisions")
    assert r.json() == []


def test_training_evaluate(client, monkeypatch):
    _mock_training_ai(monkeypatch)
    r = client.post(
        "/api/training/evaluate",
        json={
            "conversation": [
                {"sender": "contact", "content": "こんにちは！"},
                {"sender": "self", "content": "はじめまして！"},
            ],
            "reply": "映画の話とかどうですか？",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 4
    assert body["overall"]["score"] == 4
    assert body["items"][0]["label"] == "自然さ"


def test_training_evaluate_retry_on_truncated_json(client, monkeypatch):
    """AI出力が途中で切れた場合、1回だけ再試行して成功する。"""
    calls = {"n": 0}

    class TruncatedProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            calls["n"] += 1
            if calls["n"] == 1:
                # 途中で切れたJSON（閉じ括弧がない）
                return '{"naturalness":{"score":4,"comment":"途中で切れた' 
            return (
                '{"naturalness":{"score":4,"comment":"自然です"},'
                '"continuity":{"score":3,"comment":"もう少し質問が欲しい"},'
                '"distance":{"score":5,"comment":"適切な距離感"},'
                '"fit":{"score":4,"comment":"相手の発言に合っている"},'
                '"overall":{"score":4,"comment":"総合的に良い返信"}}'
            )

        def available_models(self):
            return []

    monkeypatch.setattr(
        "app.routers.training.factory.get_provider", lambda *a, **k: TruncatedProvider()
    )
    monkeypatch.setattr(
        "app.routers.training.get_ai_config",
        lambda: {
            "provider": "fake",
            "model": "fake-model",
            "api_key": "x",
            "temperature": 0.8,
            "max_tokens": 512,
            "history_limit": 50,
        },
    )
    r = client.post(
        "/api/training/evaluate",
        json={
            "conversation": [{"sender": "contact", "content": "こんにちは！"}],
            "reply": "映画の話とかどうですか？",
        },
    )
    assert r.status_code == 200
    assert calls["n"] == 2
    assert r.json()["overall"]["score"] == 4


def test_training_examples(client):
    r = client.post(
        "/api/training/examples",
        json={
            "conversation": [{"sender": "contact", "content": "こんにちは"}],
            "ai_response": "AIの返信",
            "corrected_response": "修正後の返信",
            "rating": 4,
        },
    )
    assert r.status_code == 201
    eid = r.json()["id"]

    r = client.get("/api/training/examples")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["conversation"][0]["content"] == "こんにちは"

    r = client.delete(f"/api/training/examples/{eid}")
    assert r.status_code == 204
    r = client.get("/api/training/examples")
    assert r.json() == []


def test_backup_create_list_restore(client):
    cid = client.post("/api/contacts", json={"name": "バックアップさん"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "復元テスト"})

    r = client.post("/api/backup")
    assert r.status_code == 200
    name = r.json()["file_name"]
    assert name.endswith(".zip")

    r = client.get("/api/backup")
    assert r.status_code == 200
    assert any(b["file_name"] == name for b in r.json())

    # データを変えてから復元
    client.delete(f"/api/contacts/{cid}")
    r = client.get("/api/contacts")
    assert r.json() == []

    r = client.post("/api/backup/restore", json={"file_name": name})
    assert r.status_code == 200
    r = client.get("/api/contacts")
    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "バックアップさん"


def test_extract_json_robustness():
    """AI出力のJSON解析（前置き・コードフェンス・末尾カンマ）の堅牢性。"""
    from app.routers.training import _extract_json

    # 純粋なJSON
    assert _extract_json('{"a": 1}') == {"a": 1}
    # コードフェンス
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    # 前置き文＋後置き文
    assert _extract_json('以下が結果です。{"a": 1} 以上です。') == {"a": 1}
    # 末尾カンマ
    assert _extract_json('{"a": 1,}') == {"a": 1}
    assert _extract_json('{"a": [1, 2, 3,]}') == {"a": [1, 2, 3]}
    # 前置き文＋末尾カンマ
    assert _extract_json('自然な返信です。{"a": 1,} 追記。') == {"a": 1}
    # 文字列中の生改行（モデルがコメントを複数行で書いた場合）
    assert _extract_json('{"a": {"comment": "一行目\n二行目"}}') == {"a": {"comment": "一行目\n二行目"}}
    # 文字列中の括弧・エスケープは誤って切らない
    assert _extract_json('{"a": "text } with brace"}') == {"a": "text } with brace"}
    # 完全にJSONでない場合はNone
    assert _extract_json('JSONではありません') is None


def test_revision_messages_override_rules():
    """修正指示がPriority 1として適用されつつ、基本品質ルールが維持されること。"""
    from app.ai.prompt import build_revision_messages

    msgs = build_revision_messages(
        system_prompt="【RULES】体験系は未体験前提にする",
        chat_history_text="相手: 映画見ました",
        condition="",
        original_generated="まだ見たことないんですけど、どんなお話なんですか？",
        revision_instruction="わたしもみたことある",
    )

    # システムプロンプトにも修正指示と品質維持が追記される
    assert "前回の返信案と内容・表現が重複しないよう書き換えること" in msgs[0]["content"]
    assert "ユーザー本人の実績スタイルを維持すること" in msgs[0]["content"]
    # 最終ユーザー指示に修正指示と基本品質維持指示が含まれる
    last = msgs[-1]["content"]
    assert "わたしもみたことある" in last
    assert "絶対ルールを厳守してください" in last
    assert "重複しないよう" in last
    # 前回の返信がアシスタント発言として渡る
    assert msgs[2]["role"] == "assistant"
    assert "まだ見たことないんですけど" in msgs[2]["content"]


def test_strip_brackets():
    """生成文からかぎかっこ（「」『』【】）を強制除去する。"""
    from app.ai.prompt import strip_brackets

    assert strip_brackets("『君の名は。』を観ました") == "君の名は。を観ました"
    assert strip_brackets("「こんにちは」と言われました") == "こんにちはと言われました"
    assert strip_brackets("【重要】お知らせです") == "重要お知らせです"
    assert strip_brackets("括弧なしの文") == "括弧なしの文"


def test_format_chat_history_span():
    """メッセージのタイムスタンプからやり取り期間・通数がAIに渡る。"""
    from app.ai import prompt

    msgs = [
        {"sender": "contact", "content": "こんにちは！", "created_at": "2026-08-01T10:00:00+00:00"},
        {"sender": "self", "content": "はじめまして！", "created_at": "2026-08-01T10:05:00+00:00"},
        {"sender": "contact", "content": "昨日映画見ました", "created_at": "2026-08-09T20:00:00+00:00"},
    ]
    text = prompt.format_chat_history(msgs, 50)
    assert "8日間" in text
    assert "計3通" in text
    # タイムスタンプなしの場合は期間を出さない
    plain = prompt.format_chat_history(
        [{"sender": "contact", "content": "a"}, {"sender": "self", "content": "b"}], 50
    )
    assert "日間" not in plain


def test_user_profile_crud_and_generation_reflect(client):
    """自分のプロフィールの取得・更新と、生成時の【SELF】反映。"""
    # 初期状態は未設定
    r = client.get("/api/profile")
    assert r.status_code == 200
    p = r.json()
    assert p["name"] == ""

    # 部分更新
    r = client.put("/api/profile", json={"name": "ゆうた", "age": "20代", "speaking_style": "丁寧語"})
    assert r.status_code == 200
    assert r.json()["name"] == "ゆうた"
    assert r.json()["age"] == "20代"
    assert r.json()["gender"] == ""  # 未更新項目は保持

    # 更新した内容で取得できる
    r = client.get("/api/profile")
    assert r.json()["name"] == "ゆうた"

    # 空文字でクリアできる
    r = client.put("/api/profile", json={"name": "", "occupation": "ITエンジニア"})
    assert r.json()["name"] == ""
    assert r.json()["occupation"] == "ITエンジニア"

    # 生成プレビューに【SELF】として反映される
    cid = client.post("/api/contacts", json={"name": "さくら"}).json()["id"]
    r = client.post(
        "/api/generate/preview",
        json={"contact_id": cid, "condition": "はじめまして"},
    )
    assert r.status_code == 200
    pv = r.json()
    assert pv["self_profile"]["occupation"] == "ITエンジニア"

    # システムプロンプトに【SELF】として組み込まれる
    from app.ai import prompt

    sysp = prompt.build_system_prompt(
        rules=[],
        references=[],
        learning_materials=[],
        condition="はじめまして",
        contact={"name": "さくら", "profile": "", "images": []},
        chat_history_text="(まだ会話はありません)",
        training_examples=[],
        self_profile={"name": "ゆうた", "age": "20代", "occupation": "ITエンジニア"},
    )
    assert "【SELF】" in sysp
    assert "ITエンジニア" in sysp
    assert "20代" in sysp


def test_prompt_new_structure():
    """刷新されたプロンプト構造（REPLY DIRECTIVE最上位、排他逆質問、MULTI-TOPIC、STYLE EXAMPLES）の検証。"""
    from app.ai import prompt

    sysp = prompt.build_system_prompt(
        rules=["自然な日本語にする"],
        references=["参考ノウハウ"],
        learning_materials=["学習テキスト"],
        condition="映画の話に持っていきたい",
        contact={"name": "あおい", "profile": "カフェ巡り", "images": ["カフェの写真"]},
        chat_history_text="相手: こんにちは",
        training_examples=["良い返信例: こんにちは！"],
        self_profile={"name": "たろう", "occupation": "エンジニア"},
        user_knowledge="Kingdom見たことある",
        tone="tame",
        my_info="お酒は飲まない",
    )

    # 1. REPLY DIRECTIVE が最上部に配置されている
    assert "【REPLY DIRECTIVE】" in sysp
    assert "映画の話に持っていきたい" in sysp

    # 2. CORE RULES が含まれている
    assert "【CORE RULES】" in sysp
    assert "架空の自己開示・事実捏造の禁止" in sysp

    # 3. 各種コンテキストが含まれている
    assert "【SELF & MY INFO & USER KNOWLEDGE】" in sysp
    assert "お酒は飲まない" in sysp
    assert "Kingdom見たことある" in sysp
    assert "【FINAL TASK】" in sysp

    # REPLY DIRECTIVE が CORE RULES より前に出現していることの確認
    idx_directive = sysp.index("【REPLY DIRECTIVE】")
    idx_core = sysp.index("【CORE RULES】")
    assert idx_directive < idx_core


def test_generate_ai_question_response(client, monkeypatch):
    """AIが[AI_QUESTION]を返した場合にquestionフィールドが抽出されることの検証。"""
    from app.ai.base import AIProvider
    from app.ai import factory

    class MockAIQuestionProvider(AIProvider):
        name = "mock_q"
        def generate(self, **kwargs):
            return "[AI_QUESTION]Kingdomって見たことありますか？[/AI_QUESTION]"
        def available_models(self):
            return ["m1"]

    monkeypatch.setattr(factory, "get_provider", lambda p, k: MockAIQuestionProvider())

    cid = client.post("/api/contacts", json={"name": "あおい"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "Kingdom面白かった！"})

    r = client.post("/api/generate", json={"contact_id": cid, "condition": ""})
    assert r.status_code == 200
    data = r.json()
    assert data["question"] == "Kingdomって見たことありますか？"
    assert data["replies"] == []


def test_user_knowledge_api(client):
    """ユーザーナレッジ（逆質問の回答蓄積）のCRUD検証。"""
    # 登録
    r = client.post("/api/profile/knowledge", json={"question": "Kingdom見た？", "answer": "アニメだけ見た"})
    assert r.status_code == 200
    kid = r.json()["id"]
    assert r.json()["question"] == "Kingdom見た？"
    assert r.json()["answer"] == "アニメだけ見た"

    # 一覧取得
    r = client.get("/api/profile/knowledge")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["id"] == kid

    # 削除
    r = client.delete(f"/api/profile/knowledge/{kid}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # 削除後の一覧
    r = client.get("/api/profile/knowledge")
    assert len(r.json()) == 0
