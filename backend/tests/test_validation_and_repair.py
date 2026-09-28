"""学習優先型アーキテクチャ (Learning-First Architecture) の包括的テスト。

Hard Validation, Repair, 返信ペア構築, 全件スタイル集計, Few-shot検索, プレビューAPI連携を検証。
"""
import json
import pytest
from fastapi import HTTPException

from app.routers import generation
from app.ai import prompt


def test_time_line_cleaning():
    """メッセージ内の単独時刻行のみを除去し、本文中の時刻は残す。"""
    raw_content = (
        "15:19\n"
        "こんにちは！\n"
        "明日の18:30にお店予約しました！\n"
        "20:40"
    )
    cleaned = prompt.clean_chat_message_content(raw_content)
    assert "15:19" not in cleaned
    assert "20:40" not in cleaned
    assert "明日の18:30にお店予約しました！" in cleaned
    assert "こんにちは！" in cleaned


def test_strict_parse_replies():
    """厳格パース: JSONおよびラベル分割のみ受け入れ、雑な改行フォールバックを排除。"""
    # 1. 正常JSON
    json_data = json.dumps({"replies": ["案1です！\n文2\n文3", "案2です！\n文2\n文3", "案3です！\n文2\n文3"]})
    assert len(generation._parse_replies_strict(json_data, 3)) == 3

    # 2. 正常ラベル分割
    label_data = (
        "【案1】\n映画いいですね！\n最近何見ました？\n気になります笑\n"
        "【案2】\n映画館行ってきたんですね！\nポップコーン食べました？\n教えてください😊\n"
        "【案3】\nいいなー！\n最近映画館行ってないです笑\n久しぶりに行きたくなりました"
    )
    assert len(generation._parse_replies_strict(label_data, 3)) == 3

    # 3. 1案3行の通常テキスト（3案と誤認してはならず、パース失敗として空リストを返す）
    three_lines_single_reply = (
        "映画いいですね！\n"
        "最近映画館行けてないです笑\n"
        "おすすめの作品ありますか？"
    )
    assert generation._parse_replies_strict(three_lines_single_reply, 3) == []


def test_hard_validator_detects_violations():
    """Hardバリデータが案数不一致、空文、AI_QUESTION混入、候補重複を検知すること。"""
    # 正常ケース（句点や短文が含まれていてもHardバリデーションは通過する）
    valid_replies = [
        "映画いいですね。\n最近何見ました？",
        "映画館行ってきたんですね！\n何がおすすめですか？😊",
        "いいなー！\n今度行ってみませんか？",
    ]
    assert generation.validate_candidate_replies(valid_replies, 3) == []

    # 違反1: 案数不足
    assert any("案数" in e for e in generation.validate_candidate_replies(valid_replies[:2], 3))

    # 違反2: 空文字
    v_empty = ["", valid_replies[1], valid_replies[2]]
    assert any("空文字" in e for e in generation.validate_candidate_replies(v_empty, 3))

    # 違反3: AI_QUESTION タグ混入
    v_ai_q = ["映画いいですね！\n[AI_QUESTION]何見ました？[/AI_QUESTION]", valid_replies[1], valid_replies[2]]
    assert any("AI_QUESTIONタグ" in e for e in generation.validate_candidate_replies(v_ai_q, 3))

    # 違反4: 候補同士の重複・極端な高類似
    v_dupe = [valid_replies[0], valid_replies[0], valid_replies[2]]
    assert any("重複しています" in e for e in generation.validate_candidate_replies(v_dupe, 3))

    # Step 2: 質問なしは正常系（違反ではない）
    v_no_q = ["映画いいですね！", "映画館行ってきたんですね！", "いいなー！"]
    assert generation.validate_candidate_replies(v_no_q, 3) == []
    # 「質問しない」条件でも質問なしが許容される（従来通り）
    assert generation.validate_candidate_replies(v_no_q, 3, condition="質問しない") == []


def test_ai_question_fullmatch():
    """AI_QUESTION は全体一致のみ検出し、部分一致を誤検知しないこと。"""
    pure_q = "[AI_QUESTION]コナンって見たことありますか？[/AI_QUESTION]"
    assert generation._extract_ai_question(pure_q) == "コナンって見たことありますか？"

    partial_q = '{"replies": ["いいですね！\n[AI_QUESTION]質問[/AI_QUESTION]\n行きましょう！"]}'
    assert generation._extract_ai_question(partial_q) is None


def test_generate_with_repair_success(client, monkeypatch):
    """初回生成にHard違反（重複候補や空文）があっても、修復プロンプトによる1回再生成で合格すれば200を返す。"""
    cid = client.post("/api/contacts", json={"name": "修復テスト相手", "profile": "読書好き"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "本読んでます"})

    call_count = 0

    class RepairingFakeProvider:
        name = "repairing_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 初回: 案1と案2が重複するHard違反
                return json.dumps({"replies": [
                    "本いいですね！\n何読んでるんですか？",
                    "本いいですね！\n何読んでるんですか？",
                    "本いいなー！\n最近読めてないです笑\nどんな本ですか？",
                ]})
            else:
                # 2回目（修復後）: 重複を解消した出力
                assert any("前回の出力に以下の不備が検知されました" in str(m) for m in messages)
                return json.dumps({"replies": [
                    "本いいですね！\n何読んでるんですか？\n気になります笑",
                    "読書好きなんですね！\nおすすめありますか？\n教えてください",
                    "本いいなー！\n最近読めてないです笑\n何か探してみようと思いますが、おすすめありますか？",
                ]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: RepairingFakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "repairing_fake",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    })

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    assert call_count == 2
    assert len(r.json()["replies"]) == 3


def test_generate_repair_failure_auto_clean_retries_and_graceful_fallback(client, monkeypatch):
    """修復失敗時も0件エラーで止めず、全却下・新規再生成ループを回して最終的に正常応答（200 OK）すること。"""
    cid = client.post("/api/contacts", json={"name": "修復失敗テスト相手", "profile": "読書好き"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "本読んでます"})

    call_count = 0

    class BadFakeProvider:
        name = "bad_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            nonlocal call_count
            call_count += 1
            # 常に空文字違反を返す
            return json.dumps({"replies": ["", "何読んでるんですか？", "気になります笑"]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: BadFakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "bad_fake",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    })

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    # 0件エラー（502）で止まらず、自動リトライを経て200 OKで返答
    assert r.status_code == 200
    assert "replies" in r.json()
    # 複数回試行されたことを確認
    assert call_count >= 3


def test_user_reply_pairs_construction_and_turn_aggregation(client):
    """全会話メッセージから連続turnを集約し、正しい (contact turn -> self turn) 教師ペアが構築されること。"""
    cid = client.post("/api/contacts", json={"name": "ペアテスト相手", "profile": ""}).json()["id"]

    # 連続 contact messages
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "はじめまして！"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェ巡りよく行きます！おすすめありますか？"})
    # 連続 self messages（実送信は「。」や1〜2文を含んでいても教師として保持される）
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "はじめまして。マッチありがとうございます。"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "渋谷のカフェによく行きます！"})

    pairs = generation.build_user_reply_pairs()
    assert len(pairs) >= 1
    p = [pair for pair in pairs if pair["contact_id"] == cid][0]

    assert "はじめまして！\nカフェ巡りよく行きます！おすすめありますか？" in p["contact_turn"]
    assert "はじめまして。マッチありがとうございます。\n渋谷のカフェによく行きます！" in p["self_turn"]


def test_user_learned_style_profile_calculation(client):
    """全送信メッセージからユーザースタイルプロファイル（文量、敬語率、記号、絵文字）が正しく算出されること。"""
    cid = client.post("/api/contacts", json={"name": "プロファイル相手", "profile": ""}).json()["id"]

    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "映画いいですね！\n最近何見ました？笑"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "コナン観てきました😊\n面白かったです！"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "ポップコーン食べましたよ笑"})

    profile = generation.analyze_user_learned_style(cid)
    assert profile["metrics"]["total_count"] >= 3
    wp = profile["weighted_profile"]
    assert wp["warai_ratio"] > 0
    assert wp["avg_emojis"] > 0
    assert "笑/w" in profile["summary"]
    assert "ユーザー実績スタイル" in profile["summary"]


def test_retrieved_reply_pairs_priority_and_scoring(client):
    """返信ペアの検索において、同一相手のペアが他相手のペアより優先されスコアが付与されること。"""
    cid_local = client.post("/api/contacts", json={"name": "ローカル相手", "profile": ""}).json()["id"]
    cid_global = client.post("/api/contacts", json={"name": "グローバル相手", "profile": ""}).json()["id"]

    # ローカル相手とのペア
    client.post(f"/api/contacts/{cid_local}/messages", json={"sender": "contact", "content": "週末は何して過ごしてますか？"})
    client.post(f"/api/contacts/{cid_local}/messages", json={"sender": "self", "content": "休日はよく映画観たりカフェ行ってます！"})

    # グローバル相手とのペア
    client.post(f"/api/contacts/{cid_global}/messages", json={"sender": "contact", "content": "映画好きなんですね！"})
    client.post(f"/api/contacts/{cid_global}/messages", json={"sender": "self", "content": "映画館で観るのが一番好きです笑"})

    # ローカル相手に対する生成時のペア検索
    retrieved, total_count = generation.retrieve_relevant_reply_pairs(
        cid_local,
        current_contact_turn="週末は何してますか？",
        condition="",
        max_pairs=5,
    )
    assert total_count >= 2
    assert len(retrieved) >= 1
    # 最上位は同一相手（local）のペアであること
    assert retrieved[0]["contact_id"] == cid_local
    assert retrieved[0]["score"] > 0.3
    assert "local" in retrieved[0]["scope"]


def test_counterpart_style_analysis_separation_and_metrics(client):
    """相手の文章スタイルの分析が contact ごとに完全分離され、各指標が正しく集計されること。"""
    cid_a = client.post("/api/contacts", json={"name": "敬語相手A", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid_a}/messages", json={
        "sender": "contact",
        "content": "15:19\nはじめまして！マッチありがとうございます。\nカフェ巡りがお好きなんですね！\nおすすめのお店はありますでしょうか？"
    })
    client.post(f"/api/contacts/{cid_a}/messages", json={
        "sender": "contact",
        "content": "ご連絡ありがとうございます。\n休日はよく読書をしています。\n普段はどのような本を読まれますか？"
    })

    cid_b = client.post("/api/contacts", json={"name": "タメ口相手B", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid_b}/messages", json={
        "sender": "contact",
        "content": "ヤッホー！マッチありがとー😊✨\n映画めっちゃ好きなんだけど最近何見たー？笑"
    })
    client.post(f"/api/contacts/{cid_b}/messages", json={
        "sender": "contact",
        "content": "それめっちゃ分かるwww\n今度おすすめ教えてー🙌"
    })

    style_a = generation.analyze_counterpart_style(cid_a)
    metrics_a = style_a["metrics"]
    assert metrics_a["sample_count"] == 2
    assert "敬語" in metrics_a["tone_label"]
    assert metrics_a["keigo_ratio"] >= 0.65
    assert metrics_a["avg_emojis"] == 0.0
    assert metrics_a["q_pct"] == 100
    assert "15:19" not in style_a["summary"]

    style_b = generation.analyze_counterpart_style(cid_b)
    metrics_b = style_b["metrics"]
    assert metrics_b["sample_count"] == 2
    assert "タメ口" in metrics_b["tone_label"]
    assert metrics_b["avg_emojis"] >= 1.0
    assert metrics_b["warai_pct"] == 100

    assert "タメ口" not in style_a["summary"]
    assert "丁寧な敬語" not in style_b["summary"]


def test_preview_includes_learning_first_data(client):
    """preview API に ユーザースタイルプロファイル、検索された返信ペア、相手スタイル、全件会話履歴が含まれること。"""
    cid = client.post("/api/contacts", json={"name": "プレビュー相手学習", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "こんにちは！よろしくお願いします😊"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "はじめまして！よろしくお願いします"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェよく行きます！おすすめありますか？"})

    r = client.post("/api/generate/preview", json={"contact_id": cid, "condition": ""})
    assert r.status_code == 200
    data = r.json()

    # pieces 内部のデータ検証
    assert "user_style_profile" in data
    assert "summary" in data["user_style_profile"]
    assert "retrieved_pairs" in data
    assert "total_reply_pairs_count" in data
    assert "counterpart_style" in data

    # system_prompt 内部のブロック存在確認
    assert "【USER LEARNED STYLE PROFILE】" in data["system_prompt"]
    assert "【RETRIEVED USER REPLY PAIRS】" in data["system_prompt"]
    assert "【COUNTERPART WRITING STYLE】" in data["system_prompt"]
