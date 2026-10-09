"""いいねBOT（初回いいねメッセージ生成）のテストスイート。"""
import pytest
from app.routers import like_bot


def test_format_one_sentence_per_line():
    """1文ごとに改行されるフォーマッタの検証。"""
    raw_text = "〇〇さん、はじめまして！カフェ巡り素敵ですね。たくさんお話しできるとうれしいです😊"
    formatted = like_bot._format_one_sentence_per_line(raw_text)
    lines = formatted.splitlines()
    assert len(lines) >= 3
    assert lines[0] == "〇〇さん、はじめまして！"
    assert lines[1] == "カフェ巡り素敵ですね。"
    assert lines[2] == "たくさんお話しできるとうれしいです😊"


def test_like_bot_prompt_hobby_focus_and_forbid_work():
    """いいねBOTは話題範囲を保ち、語句の機械的な一律禁止はしない。"""
    profile_sample = "休日はカフェ巡りや映画鑑賞をして過ごしています。最近はドライブも始めました。"
    req = like_bot.LikeBotRequest(profile_text=profile_sample)

    import inspect
    source = inspect.getsource(like_bot.generate_like_message)
    assert "リアルなチャット言葉・文体" in source
    assert "仕事・学業・考え方・価値観への言及・共感は完全禁止" in source
    assert "1文ごとの改行の絶対遵守" in source
    assert "相手の名前呼びは必ず「さん」付け" in source
    assert "特定の語句を一律に禁止せず" in source


def test_like_bot_sanitizer_preserves_wording_and_reporting_clauses():
    messages = [
        "温泉を求めて旅行してきました！",
        "何かおすすめありますか？",
        "他に好きな映画はありますか？",
        "プロフィールにカフェ好きと書かれていたとのことですが、素敵ですね！",
        "旅行でリフレッシュできました！",
    ]

    assert [like_bot._sanitize_like_message(message) for message in messages] == messages


def test_like_bot_empty_profile_returns_400(client):
    """プロフィール文が空の場合は400エラーとなること。"""
    r = client.post("/api/like-bot/generate", json={"profile_text": "   "})
    assert r.status_code == 400
    assert "プロフィール文を入力してください" in r.json()["detail"]


def test_like_bot_generate_success(client, monkeypatch):
    """モックAIを用いたいいねBOT生成APIの正常系検証。"""
    from app.ai.base import AIProvider
    from app.ai import factory

    class FakeLikeBotProvider(AIProvider):
        name = "fake_like"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            # システムプロンプトに禁止事項が含まれていることを検証
            sys_msg = [m["content"] for m in messages if m["role"] == "system"][0]
            assert "リアルなチャット言葉・文体" in sys_msg
            assert "仕事・学業・考え方・価値観への言及・共感は完全禁止" in sys_msg
            assert "1文ごとの改行の絶対遵守" in sys_msg
            assert "さん」付け" in sys_msg
            assert "特定の語句や表現を一律に禁止せず" in sys_msg

            user_msg = [m["content"] for m in messages if m["role"] == "user"][0]
            assert "仕事・学業・価値観には触れない" in user_msg

            return (
                "花子さん、はじめまして！\nカフェ巡りの趣味、お店のチョイスがすごく素敵ですね！\n僕も美味しい珈琲が好きなので共感しました！\nたくさんお話しできるとうれしいです😊\n"
                "---\n"
                "花子さん、はじめまして！\n映画鑑賞がお好きなんですね！\n色んな作品を観られていて楽しそうです！\nたくさんお話しできるとうれしいです😊\n"
                "---\n"
                "花子さん、はじめまして！\n休日のドライブ、海沿いを走るの最高ですね！\n僕もドライブが好きなので親近感が湧きました！\nたくさんお話しできるとうれしいです😊"
            )

        def available_models(self):
            return []

    monkeypatch.setattr(factory, "get_provider", lambda *a, **k: FakeLikeBotProvider())
    monkeypatch.setattr(
        like_bot,
        "get_ai_config",
        lambda: {
            "provider": "fake_like",
            "model": "fake-model",
            "api_key": "x",
            "temperature": 0.7,
            "max_tokens": 1024,
        },
    )

    r = client.post(
        "/api/like-bot/generate",
        json={"profile_text": "カフェ巡りと映画とドライブが好きです！"},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["candidates"]) == 3
    assert data["message"] == data["candidates"][0]

    # 各案が1文1行で構成されていること
    for cand in data["candidates"]:
        lines = [l for l in cand.splitlines() if l.strip()]
        assert len(lines) >= 3
        assert lines[0].startswith("花子さん、はじめまして")
