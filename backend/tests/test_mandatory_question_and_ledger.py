"""毎回必ず質問する絶対ルール、Conversation State Ledger、会話履歴参照のユニットテスト。"""
import json
import pytest
from app.ai import prompt
from app.routers import generation
from app import database


def test_mandatory_question_prompt_rules():
    """HARD INVARIANTS 第9条および OUTPUT CONTRACT 第4条に質問必須ルールが含まれること。"""
    contact = {"name": "さくら", "profile": "旅行とカフェが好き"}
    ledger = {
        "current_topic": "カフェ巡り",
        "last_contact_message": "カフェ巡りが好きです！",
        "unresolved_question": "おすすめのカフェありますか？",
        "already_asked_questions": ["休日は何してますか？", "どこ住みですか？"],
    }
    sysp = prompt.build_system_prompt(
        contact=contact,
        condition="カフェの話を広げる",
        conversation_ledger=ledger,
    )

    # 1. HARD INVARIANTS 第9条に質問必須
    assert "9. 質問必須（絶対ルール）" in sysp
    assert "会話のキャッチボールを維持するため" in sysp

    # 2. HARD INVARIANTS 第10条に文脈参照
    assert "10. 会話継続・文脈参照" in sysp

    # 3. OUTPUT CONTRACT 第4条に質問必須
    assert "4. 質問必須の絶対遵守" in sysp

    # 4. CHAT HISTORY 内に Ledger と最優先返答対象
    assert "【CONVERSATION STATE & CONTEXT CONTINUATION】" in sysp
    assert "最優先返答対象" in sysp
    assert "カフェ巡りが好きです！" in sysp
    assert "相手からの直近質問（要回答）: おすすめのカフェありますか？" in sysp
    assert "休日は何してますか？" in sysp
    assert "どこ住みですか？" in sysp


def test_build_conversation_state_ledger_extraction():
    """会話メッセージリストから未解決質問・既出質問・直近話題が正しく抽出されること。"""
    messages = [
        {"sender": "contact", "content": "はじめまして！よろしくお願いします😊"},
        {"sender": "self", "content": "はじめまして！\n休日は何されてるんですか？"},
        {"sender": "contact", "content": "休日はよくカフェ巡りしてます！\n〇〇さんはカフェ好きですか？"},
    ]

    ledger = prompt.build_conversation_state_ledger(messages, condition="カフェの話")

    # 相手の直近の未解決質問
    assert ledger["unresolved_question"] == "〇〇さんはカフェ好きですか？"
    # 自分が過去に聞いた質問
    assert any("休日は何されてるんですか" in q for q in ledger["already_asked_questions"])
    # 直近の相手メッセージ
    assert "カフェ巡りしてます" in ledger["last_contact_message"]


def test_validate_candidate_replies_mandatory_question():
    """バリデータが質問のない候補を検知し、質問がある候補は通過すること。"""
    valid_replies = [
        "カフェ巡りいいですね！\n最近美味しいお店見つけました笑\nおすすめの場所ありますか？",
        "カフェ好きです！\nコーヒーよく飲まれますか？",
        "休日のカフェ落ち着きますよね。\n普段どのエリアに行かれますか？",
    ]
    violations = generation.validate_candidate_replies(valid_replies, expected_candidates=3)
    assert violations == []

    invalid_replies = [
        "カフェ巡りいいですね！\n最近美味しいお店見つけました笑\nおすすめの場所ありますか？",
        "カフェ好きです！\nコーヒーよく飲みます！",
        "休日のカフェ落ち着きますよね。\n普段どのエリアに行かれますか？",
    ]
    violations = generation.validate_candidate_replies(invalid_replies, expected_candidates=3)
    assert len(violations) == 1
    assert "案2に相手への質問が含まれていません" in violations[0]


def test_validate_candidate_replies_condition_no_question_exception():
    """condition に「質問しない」等の指定がある場合、質問なしでもバリデーションを通過すること。"""
    no_q_replies = [
        "カフェ巡りいいですね！\n最近美味しいお店見つけました笑",
        "カフェ好きです！\nコーヒーよく飲みます！",
        "休日のカフェ落ち着きますよね！",
    ]
    assert len(generation.validate_candidate_replies(no_q_replies, expected_candidates=3)) == 3
    assert generation.validate_candidate_replies(no_q_replies, expected_candidates=3, condition="質問しない") == []
    assert generation.validate_candidate_replies(no_q_replies, expected_candidates=3, condition="質問不要で短く") == []
    assert generation.validate_candidate_replies(no_q_replies, expected_candidates=3, condition="共感のみで質問は入れない") == []


def test_clean_contact_profile_ocr_noise_removal():
    """OCRノイズや画面UIゴミ行が綺麗に除去されること。"""
    raw_ocr = """さがす
いいね
1
トーク
マイページ
すいぞくかん！
ゆづ
24歳北海道
休みの日は家族や友達と出かけたり
家でまったり過ごすことが多いです
美味しいご飯大好き
28 %
VIPオプションを契約すると
"""
    cleaned = prompt.clean_contact_profile(raw_ocr)
    assert "さがす" not in cleaned
    assert "いいね" not in cleaned
    assert "トーク" not in cleaned
    assert "VIPオプション" not in cleaned
    assert "休みの日は家族や友達と出かけたり" in cleaned
    assert "家でまったり過ごすことが多いです" in cleaned


def test_smart_format_one_sentence_per_line():
    """引用助詞や接続助詞の直前で不自然に文が切断されないこと。"""
    cases = [
        ("リピートしたい！と思ったお店はありましたか？", "リピートしたい！と思ったお店はありましたか？"),
        ("今一番これが食べたい！って思っているジャンルやメニューはありますか？", "今一番これが食べたい！って思っているジャンルやメニューはありますか？"),
        ("カフェ行きたいですね！\n最近新しいお店探してました笑\nおすすめありますか？", "カフェ行きたいですね！\n最近新しいお店探してました笑\nおすすめありますか？"),
    ]
    for orig, expected in cases:
        assert prompt.format_one_sentence_per_line(orig) == expected


def test_e2e_generate_with_ledger_and_question_validation(client, monkeypatch):
    """E2Eで _build_context に ledger が含まれ、生成APIが質問必須バリデーションを通して正常に返ること。"""
    cid = client.post("/api/contacts", json={"name": "文脈テスト相手", "profile": "カフェ巡り"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "こんにちは！カフェ巡り好きです"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "こんにちは！\n普段どのあたりに行かれますか？", "source": "manual"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "渋谷や表参道が多いです！\nおすすめありますか？"})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            # プロンプト内に直近質問と既出質問が含まれていることを検証
            all_str = "".join(str(m) for m in messages)
            assert "最優先返答対象" in all_str
            assert "普段どのあたりに行かれますか？" in all_str
            assert "おすすめありますか？" in all_str

            return json.dumps({
                "replies": [
                    "表参道いいですね！\n最近オープンしたカフェが気になってます笑\n一緒に行ってみませんか？",
                    "渋谷のカフェ落ち着くところ多いですよね！\nよく行くお店とかありますか？",
                    "表参道はおしゃれなお店多いですよね！\nコーヒーと紅茶どちらがお好きですか？",
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

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "カフェの話題を続ける", "candidates": 3})
    assert r.status_code == 200
    data = r.json()
    assert len(data["replies"]) == 3
    for rep in data["replies"]:
        # 1文1行改行かつ質問が含まれていること
        assert "\n" in rep
        assert "？" in rep or "?" in rep

