"""学習優先型アーキテクチャ (Learning-First Architecture) の End-to-End 包括テスト。

1. source / generation_history_id のエンドツーエンド追跡と整合性検証
2. Few-shot トピック検索精度（カフェクエリでカフェペアが最上位）
3. プロンプト縮約・文字数・ブロック数の回帰検証
4. Soft Style Scoring と候補並べ替え（案1がスタイルスコア最高）
5. メッセージ単位の排他的トーン分類検証
"""
import json
import pytest
from fastapi import HTTPException

from app.routers import generation
from app.ai import prompt
from app import database


def test_e2e_source_tracking_and_history_sync(client):
    """生成案送信と手入力送信における source / generation_history_id の end-to-end 接続と422整合性検証。"""
    cid = client.post("/api/contacts", json={"name": "E2E相手", "profile": "カフェ"}).json()["id"]
    cid_other = client.post("/api/contacts", json={"name": "他相手", "profile": ""}).json()["id"]

    # 1. generation_history を作成
    conn = database.get_conn()
    now = database.now_iso()
    cur = conn.execute(
        "INSERT INTO generation_history (contact_id, provider, model, generated_text, is_sent, created_at)"
        " VALUES (?, 'gemini', 'gemini-3.5-flash-lite', 'カフェいいですね！\nおすすめありますか？笑', 0, ?)",
        (cid, now),
    )
    hist_id = cur.lastrowid
    conn.commit()
    conn.close()

    # 2. 生成カードからの送信 (source='generated', generation_history_id=hist_id, 本文一致 -> 201 成功)
    r = client.post(
        f"/api/contacts/{cid}/messages",
        json={
            "sender": "self",
            "content": "カフェいいですね！\nおすすめありますか？笑",
            "source": "generated",
            "generation_history_id": hist_id,
        },
    )
    assert r.status_code == 201
    msg_data = r.json()
    assert msg_data["source"] == "generated"
    assert msg_data["generation_history_id"] == hist_id

    # generation_history の is_sent, is_adopted が 1 に更新されていることの確認
    conn = database.get_conn()
    hist_row = conn.execute("SELECT is_sent, is_adopted FROM generation_history WHERE id = ?", (hist_id,)).fetchone()
    conn.close()
    assert hist_row["is_sent"] == 1
    assert hist_row["is_adopted"] == 1

    # 3. チャット欄からの手動送信 (source='manual' -> 201 成功)
    r_manual = client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "self", "content": "手入力メッセージです！", "source": "manual"},
    )
    assert r_manual.status_code == 201
    assert r_manual.json()["source"] == "manual"
    assert r_manual.json()["generation_history_id"] is None

    # 4. 厳格な 422 バリデーション検証:
    # 4a. source='generated' で generation_history_id なし -> 422
    r_no_id = client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "self", "content": "本文", "source": "generated"},
    )
    assert r_no_id.status_code == 422
    assert "generation_history_id が必須" in r_no_id.json()["detail"]

    # 4b. source='generated' で存在しない generation_history_id -> 422
    r_not_found = client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "self", "content": "本文", "source": "generated", "generation_history_id": 999999},
    )
    assert r_not_found.status_code == 422
    assert "存在しません" in r_not_found.json()["detail"]

    # 4c. source='generated' で別 contact の generation_history_id -> 422
    r_wrong_contact = client.post(
        f"/api/contacts/{cid_other}/messages",
        json={"sender": "self", "content": "カフェいいですね！\nおすすめありますか？笑", "source": "generated", "generation_history_id": hist_id},
    )
    assert r_wrong_contact.status_code == 422
    assert "contact_id が現在の相手と一致しません" in r_wrong_contact.json()["detail"]

    # 4d. source='generated' で本文不一致 -> 422
    r_mismatch = client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "self", "content": "全く異なる本文", "source": "generated", "generation_history_id": hist_id},
    )
    assert r_mismatch.status_code == 422
    assert "テキストと一致しません" in r_mismatch.json()["detail"]

    # 4e. source='manual' で generation_history_id 指定 -> 422
    r_manual_with_id = client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "self", "content": "手入力本文", "source": "manual", "generation_history_id": hist_id},
    )
    assert r_manual_with_id.status_code == 422
    assert "指定することはできません" in r_manual_with_id.json()["detail"]


def test_revision_prompt_no_legacy_soft_rules():
    """再生成プロンプト（指示あり・指示なし）から旧固定Softルール（3文以上、句点禁止、顔絵文字固定等）が完全に消失していること。"""
    # 1. 修正指示なし
    msgs_no_inst = prompt.build_revision_messages(
        system_prompt="システムプロンプト",
        chat_history_text="相手: こんにちは",
        condition="",
        original_generated="案1: こんばんは！",
        revision_instruction="",
    )
    full_text_no_inst = " ".join(m["content"] for m in msgs_no_inst)
    assert "原則3文以上" not in full_text_no_inst
    assert "3〜5文" not in full_text_no_inst
    assert "句点" not in full_text_no_inst
    assert "顔絵文字" not in full_text_no_inst
    assert "1文1行" not in full_text_no_inst
    assert "【USER LEARNED STYLE PROFILE】" in full_text_no_inst
    assert "架空の自己開示・事実捏造の禁止" in full_text_no_inst

    # 2. 修正指示あり
    msgs_with_inst = prompt.build_revision_messages(
        system_prompt="システムプロンプト",
        chat_history_text="相手: こんにちは",
        condition="",
        original_generated="案1: こんばんは！",
        revision_instruction="もっと冗談っぽく",
    )
    full_text_with_inst = " ".join(m["content"] for m in msgs_with_inst)
    assert "原則3文以上" not in full_text_with_inst
    assert "3〜5文" not in full_text_with_inst
    assert "句点" not in full_text_with_inst
    assert "顔絵文字" not in full_text_with_inst
    assert "1文1行" not in full_text_with_inst
    assert "もっと冗談っぽく" in full_text_with_inst


def test_idempotent_legacy_migration(client):
    """DBマイグレーションの冪等性: 新規作成した manual メッセージが次回 init_db() 呼び出しで legacy_unknown に上書きされないこと。"""
    cid = client.post("/api/contacts", json={"name": "冪等相手", "profile": ""}).json()["id"]

    # 新規 manual メッセージ作成
    r = client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "self", "content": "新規の手入力です", "source": "manual"},
    )
    assert r.status_code == 201
    msg_id = r.json()["id"]
    assert r.json()["source"] == "manual"

    # 再度 init_db を実行
    database.init_db()

    # source が manual のまま維持されていること
    conn = database.get_conn()
    row = conn.execute("SELECT source FROM messages WHERE id = ?", (msg_id,)).fetchone()
    conn.close()
    assert row["source"] == "manual"


def test_topic_retrieval_ranking_cafe_and_movie(client):
    """Few-shot検索において、トピックキーワード一致がストップワードを凌駕して正しく上位にランクインすること。"""
    cid_cafe = client.post("/api/contacts", json={"name": "カフェ相手", "profile": ""}).json()["id"]
    cid_music = client.post("/api/contacts", json={"name": "音楽相手", "profile": ""}).json()["id"]
    cid_movie = client.post("/api/contacts", json={"name": "映画相手", "profile": ""}).json()["id"]

    # 1. カフェペア
    client.post(f"/api/contacts/{cid_cafe}/messages", json={"sender": "contact", "content": "カフェで珈琲飲むのが好きです"})
    client.post(f"/api/contacts/{cid_cafe}/messages", json={"sender": "self", "content": "おすすめのカフェ教えてください！"})

    # 2. 音楽ペア（一般的な相槌が多い）
    client.post(f"/api/contacts/{cid_music}/messages", json={"sender": "contact", "content": "そうなんですね！やっぱり生音と一体感ですかね〜"})
    client.post(f"/api/contacts/{cid_music}/messages", json={"sender": "self", "content": "一回行ってみたいんですよねー！"})

    # 3. 映画ペア
    client.post(f"/api/contacts/{cid_movie}/messages", json={"sender": "contact", "content": "最近映画館で映画観てきました！"})
    client.post(f"/api/contacts/{cid_movie}/messages", json={"sender": "self", "content": "ポップコーン食べながら映画観るの最高ですよね笑"})

    # クエリ1: カフェ巡り
    retrieved_cafe, _ = generation.retrieve_relevant_reply_pairs(
        contact_id=9999,
        current_contact_turn="カフェ巡り好きなんですね！",
        condition="",
        max_pairs=5,
    )
    assert len(retrieved_cafe) >= 1
    # 最上位はカフェペアであること
    assert "カフェ" in retrieved_cafe[0]["contact_turn"] or "カフェ" in retrieved_cafe[0]["self_turn"]
    assert retrieved_cafe[0]["topic_matches"] > 0

    # クエリ2: 映画
    retrieved_movie, _ = generation.retrieve_relevant_reply_pairs(
        contact_id=9999,
        current_contact_turn="映画館で新作観てきました！",
        condition="",
        max_pairs=5,
    )
    assert len(retrieved_movie) >= 1
    assert "映画" in retrieved_movie[0]["contact_turn"] or "映画" in retrieved_movie[0]["self_turn"]


def test_prompt_compactness_and_block_limits(client):
    """プロンプト縮約: システムプロンプト文字数が 10,000 文字未満、ブロック数が 26 個未満であること。"""
    cid = client.post("/api/contacts", json={"name": "縮約テスト相手", "profile": "読書好き"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "こんにちは！"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "はじめまして！よろしくお願いします"})

    ctx = generation._build_context(cid, "読書の話を広げる", "hybrid")
    sysp = ctx["system_prompt"]

    # 文字数上限（10,000文字未満）
    assert len(sysp) < 10000
    # ブロック数上限（【 の個数が 26個未満。Step 8 で仕様必須の【FACT BOUNDARY】1見出しを追加したため25→26）
    assert sysp.count("【") < 26

    # 必須主要ブロックが存在すること
    assert "【REPLY DIRECTIVE】" in sysp
    assert "【CORE RULES】" in sysp
    assert "【USER LEARNED STYLE PROFILE】" in sysp
    assert "【RETRIEVED USER REPLY PAIRS】" in sysp
    assert "【COUNTERPART WRITING STYLE】" in sysp
    assert "【CONTACT & CHAT HISTORY】" in sysp
    assert "【FINAL TASK】" in sysp


def test_soft_style_scoring_and_candidate_sorting(client, monkeypatch):
    """Soft Style Scoring による 3 案のスコアリングと本命案（案1）の並べ替え、履歴同期の検証。"""
    cid = client.post("/api/contacts", json={"name": "ソートテスト相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェ行ってきました！"})

    # 自分の過去送信データ（ハイブリッド調、平均2〜3行、笑/wあり、質問あり）
    for _ in range(5):
        client.post(f"/api/contacts/{cid}/messages", json={
            "sender": "self",
            "content": "カフェいいですね！\n最近新しいお店探してました笑\nおすすめありますか？",
        })

    class MultiQualityFakeProvider:
        name = "multi_quality_fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            # 案1: 短文・無愛想（低スコア）
            # 案2: プロファイルと完璧に合致（高スコア: 敬語+笑+質問+適度な行数）
            # 案3: 超長文（中スコア）
            return json.dumps({"replies": [
                "カフェいいですね！\nどんなカフェですか？",
                "カフェ巡りいいですね！\n最近新しいお店探してました笑\nおすすめのお店ありますか？",
                "カフェ行ってきたんですね。\n落ち着いた雰囲気のお店だとゆっくりできそうですね。\nおすすめのお店があったら、今度ぜひ教えてください！",
            ]})

        def available_models(self):
            return []

    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: MultiQualityFakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "multi_quality_fake",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    })

    r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
    assert r.status_code == 200
    data = r.json()
    replies = data["replies"]
    scores = data.get("style_scores", [])
    history_ids = data["history_ids"]

    assert len(replies) == 3
    assert len(scores) == 3
    assert len(history_ids) == 3

    # スコアが降順（案1 >= 案2 >= 案3）になっていること
    assert scores[0] >= scores[1] >= scores[2]
    # 最高スコアの案（プロファイル完全合致）が案1になっていること
    assert "おすすめのお店ありますか？" in replies[0]

    # 保存された履歴がソート後の返信順と完全一致していること
    conn = database.get_conn()
    h_row = conn.execute("SELECT generated_text FROM generation_history WHERE id = ?", (history_ids[0],)).fetchone()
    conn.close()
    assert h_row["generated_text"] == replies[0]


def test_exclusive_tone_classification(client):
    """各メッセージが keigo/hybrid/tame に排他的に分類され、プロファイル比率の合計が 1.0 になること。"""
    cid = client.post("/api/contacts", json={"name": "トーン相手", "profile": ""}).json()["id"]

    # 純粋敬語
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "はじめまして。よろしくお願いします。"})
    # ハイブリッド（敬語＋笑）
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "それめっちゃ分かります笑！\n行ってみたいですね"})
    # タメ口
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "だよね！\n今度行こうよ！"})

    profile = generation.analyze_user_learned_style(cid)
    wp = profile["weighted_profile"]

    k_r = wp["keigo_ratio"]
    h_r = wp["hybrid_ratio"]
    t_r = wp["tame_ratio"]

    # 比率が排他的で、合計が約 1.0 であること
    assert abs((k_r + h_r + t_r) - 1.0) < 0.05
    assert k_r > 0
    assert h_r > 0
    assert t_r > 0


def test_build_initial_generation_messages_no_legacy_soft_rules():
    """初回メッセージ構築（build_initial_generation_messages）に旧固定Softルールが含まれないこと。"""
    msgs = prompt.build_initial_generation_messages(
        system_prompt="システムプロンプト",
        chat_history_text="相手: こんにちは",
        candidates=3,
    )
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"

    user_content = msgs[1]["content"]
    assert "原則3文以上" not in user_content
    assert "3〜5文" not in user_content
    assert "1文1行" not in user_content
    assert "「。」不使用" not in user_content
    assert "顔絵文字0〜2個" not in user_content
    assert "【USER LEARNED STYLE PROFILE】" in user_content
    assert "【RETRIEVED USER REPLY PAIRS】" in user_content
    assert "3案作成" in user_content


def test_self_generation_execution_path_clean_from_legacy_rules(client):
    """self 通常生成および再生成の実行経路全体において、旧固定Softルール文字列が一切送信されないことの検証。"""
    cid = client.post("/api/contacts", json={"name": "クリーン検証相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェ行きませんか？"})

    # 1. 初回生成メッセージ経路
    ctx = generation._build_context(cid, "カフェの話", "hybrid")
    init_msgs = prompt.build_initial_generation_messages(
        system_prompt=ctx["system_prompt"],
        chat_history_text=ctx["chat_text"],
        candidates=3,
    )
    full_init_text = " ".join(m["content"] for m in init_msgs)
    for forbidden in ["原則3文以上", "3〜5文", "1文1行の絶対遵守", "「。」不使用", "顔絵文字0〜2個"]:
        assert forbidden not in full_init_text

    # 2. 再生成メッセージ経路（指示なし）
    rev_no_inst = prompt.build_revision_messages(
        system_prompt=ctx["system_prompt"],
        chat_history_text=ctx["chat_text"],
        condition="カフェの話",
        original_generated="案1: カフェいいですね！",
        revision_instruction="",
    )
    full_rev_no = " ".join(m["content"] for m in rev_no_inst)
    for forbidden in ["原則3文以上", "3〜5文", "1文1行の絶対遵守", "「。」不使用", "顔絵文字0〜2個"]:
        assert forbidden not in full_rev_no

    # 3. 再生成メッセージ経路（指示あり）
    rev_with_inst = prompt.build_revision_messages(
        system_prompt=ctx["system_prompt"],
        chat_history_text=ctx["chat_text"],
        condition="カフェの話",
        original_generated="案1: カフェいいですね！",
        revision_instruction="もっと冗談っぽく",
    )
    full_rev_with = " ".join(m["content"] for m in rev_with_inst)
    for forbidden in ["原則3文以上", "3〜5文", "1文1行の絶対遵守", "「。」不使用", "顔絵文字0〜2個"]:
        assert forbidden not in full_rev_with


def test_tone_instruction_no_ungrounded_self_disclosure():
    """トーン指示文（keigo, tame, hybrid）に「僕も最近〜」「僕も行ってみたい」等の自己事実仮定が含まれないこと。"""
    for t in ["keigo", "tame", "hybrid"]:
        p = prompt.build_system_prompt(
            rules=[], references=[], learning_materials=[], condition="", contact={"name": "相手"},
            chat_history_text="", training_examples=[], self_profile={}, role="self", tone=t
        )
        assert "僕も最近ちょこちょこ観てます" not in p
        assert "僕も行ってみたい" not in p
        assert "俺も行ってみたい" not in p
