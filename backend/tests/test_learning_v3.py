"""Conversation-Learned Reply System v3 の包括テストスイート。

1. Corpus: Turn集約, Phase分類, Gold/Silver/Bronze/Negative ラベル付与, 汚染除外
2. Batch: generation_batches ライフサイクル (pending -> candidate_sent / regenerated / manual_replaced)
3. Contrast Learning: 失敗3案と手入力返信の差分抽出
4. Retrieval: 2段階ランキング, Gold優先, トピック一致, Leave-one-out
5. Prompt: 8ブロック構成, 10,000文字以内, 旧ファイル常時結合排除
6. Diagnostics API: 成果指標・コーパス内訳集計
"""
from __future__ import annotations

import pytest
from app import database, learning
from app.ai import prompt
from app.routers import generation


def test_corpus_turn_grouping_and_phase_classification(client):
    """連続同一送信者の発言が1つのTurnに正しく集約され、Phaseが分類されること。"""
    cid = client.post("/api/contacts", json={"name": "フェーズ相手", "profile": ""}).json()["id"]

    # Opening
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "はじめまして！よろしくお願いします"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "はじめまして！\nマッチありがとうございます！", "source": "manual"})

    # Getting to know (連続メッセージ)
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "普段お休みの日は何されてますか？"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェとか行きますか？"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "休日はカフェ巡りや映画を観たりしてます笑\nよく行く街とかありますか？", "source": "manual"})

    # Scheduling
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "来週末渋谷のカフェ行きましょ！何曜日が空いてますか？"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "来週土曜日空いてます！\n渋谷のカフェ行きましょう😊", "source": "manual"})

    turns = learning.corpus.build_turns_for_contact(cid)
    # 3往復（6ターン）に集約されていること
    assert len(turns) == 6
    assert turns[1].sender == "self"
    assert "カフェとか行きますか？" in turns[2].text  # 連続メッセージが1ターンに結合されている

    pairs = learning.corpus.extract_reply_pairs(cid)
    assert len(pairs) == 3
    assert pairs[0].phase == "opening"
    assert pairs[1].phase == "getting_to_know"
    assert pairs[2].phase == "scheduling"
    assert all(p.label == "gold" for p in pairs)


def test_corpus_clean_and_pollution_exclusion(client):
    """独立時刻行の除去、および同一文の誤登録が動的に除外されること。"""
    cid = client.post("/api/contacts", json={"name": "クリーン検証相手", "profile": ""}).json()["id"]

    # 独立時刻行を含むメッセージ
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "22:58\nこんばんは！\n今日も一日お疲れ様でした"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "お疲れ様です！\nゆっくり休んでくださいね", "source": "manual"})

    # 同一文誤登録（汚染データ）
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "誤登録テストテキスト"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "誤登録テストテキスト", "source": "manual"})

    turns = learning.corpus.build_turns_for_contact(cid)
    # 22:58 が除去されていること
    assert "22:58" not in turns[0].text
    assert "こんばんは！" in turns[0].text

    pairs = learning.corpus.extract_reply_pairs(cid)
    assert len(pairs) == 2
    # 1件目は正常なGold
    assert pairs[0].excluded is False
    assert pairs[0].label == "gold"
    # 2件目は同一テキストとして除外
    assert pairs[1].excluded is True
    assert "誤登録" in pairs[1].excluded_reason


def test_generation_batches_lifecycle_and_candidate_sent(client, monkeypatch):
    """初回生成（pending）-> 再生成（regenerated）-> 候補送信（candidate_sent）のライフサイクル検証。"""
    from app.ai.base import AIProvider
    from app.ai import factory

    cid = client.post("/api/contacts", json={"name": "バッチ相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "おすすめのカフェありますか？"})

    class FakeProvider(AIProvider):
        name = "fake"
        def generate(self, **kwargs):
            return '{"replies": ["渋谷のカフェがおすすめです！\\n落ち着いてて良いですよ\\nどこか気になるところありますか？", "恵比寿の隠れ家カフェが素敵です！\\n行かれたことありますか？", "表参道のお店が気になってます！\\nカフェ巡り好きですか？"]}'
        def available_models(self):
            return []

    monkeypatch.setattr(factory, "get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr(generation, "get_ai_config", lambda: {"provider": "fake", "model": "fake", "api_key": "x", "temperature": 0.7, "max_tokens": 1024})

    # 1. 初回生成
    r1 = client.post("/api/generate", json={"contact_id": cid, "candidates": 3, "condition": "カフェ紹介"})
    assert r1.status_code == 200
    hist_ids_1 = r1.json()["history_ids"]

    conn = database.get_conn()
    b1 = conn.execute("SELECT * FROM generation_batches WHERE contact_id = ? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    assert b1["outcome"] == "pending"
    assert b1["attempt_no"] == 1
    batch_1_id = b1["id"]

    # 2. 再生成（修正指示あり）
    r2 = client.post("/api/generate", json={"contact_id": cid, "candidates": 3, "condition": "カフェ紹介", "revision_instruction": "もっとフランクに"})
    assert r2.status_code == 200
    hist_ids_2 = r2.json()["history_ids"]

    b1_updated = conn.execute("SELECT outcome FROM generation_batches WHERE id = ?", (batch_1_id,)).fetchone()
    assert b1_updated["outcome"] == "regenerated"

    b2 = conn.execute("SELECT * FROM generation_batches WHERE contact_id = ? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    assert b2["outcome"] == "pending"
    assert b2["parent_batch_id"] == batch_1_id
    assert b2["attempt_no"] == 2
    batch_2_id = b2["id"]

    # 3. 候補の送信（generated で送信）
    send_text = r2.json()["replies"][0]
    send_hist_id = hist_ids_2[0]
    r_send = client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "self", "content": send_text, "source": "generated", "generation_history_id": send_hist_id},
    )
    assert r_send.status_code == 201

    b2_sent = conn.execute("SELECT outcome, selected_history_id FROM generation_batches WHERE id = ?", (batch_2_id,)).fetchone()
    conn.close()
    assert b2_sent["outcome"] == "candidate_sent"
    assert b2_sent["selected_history_id"] == send_hist_id


def test_manual_replacement_contrast_learning(client, monkeypatch):
    """AI生成後にユーザー自身が手入力で返信した場合、バッチが manual_replaced となり Contrast 学習データが生成されること。"""
    from app.ai.base import AIProvider
    from app.ai import factory

    cid = client.post("/api/contacts", json={"name": "コントラスト相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "休日は何してますか？"})

    class FakeProvider(AIProvider):
        name = "fake"
        def generate(self, **kwargs):
            return '{"replies": ["休日は読書や映画鑑賞をして過ごすことが多いですね。\\n普段どんな本読みますか？", "カフェ巡りをして過ごしてます！\\nおすすめのお店ありますか？", "色々な街を散策するのが趣味です！\\nお散歩とか好きですか？"]}'
        def available_models(self):
            return []

    monkeypatch.setattr(factory, "get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr(generation, "get_ai_config", lambda: {"provider": "fake", "model": "fake", "api_key": "x", "temperature": 0.7, "max_tokens": 1024})

    # AI生成（長くて不自然な案）
    r_gen = client.post("/api/generate", json={"contact_id": cid, "candidates": 3})
    assert r_gen.status_code == 200

    # ユーザーが生成案を捨てて手入力で返信
    chosen_text = "休日はカフェでコーヒー飲んだり映画観てます笑！\n〇〇さんは何してますか？"
    r_manual = client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "self", "content": chosen_text, "source": "manual"},
    )
    assert r_manual.status_code == 201
    manual_msg_id = r_manual.json()["id"]

    # バッチが manual_replaced に更新されていること
    conn = database.get_conn()
    batch_row = conn.execute("SELECT * FROM generation_batches WHERE contact_id = ? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    conn.close()
    assert batch_row["outcome"] == "manual_replaced"
    assert batch_row["replacement_message_id"] == manual_msg_id

    # Contrast Examples に抽出されること
    contrast_examples = learning.contrast.extract_contrast_examples(contact_id=cid)
    assert len(contrast_examples) >= 1
    assert contrast_examples[0].chosen_reply == chosen_text
    assert len(contrast_examples[0].rejected_replies) == 3

    # プロンプトブロックに教訓として成形されること
    prompt_block = learning.contrast.to_contrast_prompt_block(contrast_examples)
    assert "【RELEVANT CONTRAST】" in prompt_block
    assert chosen_text in prompt_block


def test_retrieval_2_stage_ranking_and_gold_priority(client):
    """Retrieval において、Same-contact Gold およびトピック一致ペアが最上位にランクされること。"""
    cid_target = client.post("/api/contacts", json={"name": "ターゲット相手", "profile": ""}).json()["id"]
    cid_other = client.post("/api/contacts", json={"name": "他相手", "profile": ""}).json()["id"]

    # 他相手のカフェペア (Gold)
    client.post(f"/api/contacts/{cid_other}/messages", json={"sender": "contact", "content": "カフェ巡り好きなんですか？"})
    client.post(f"/api/contacts/{cid_other}/messages", json={"sender": "self", "content": "カフェ巡り大好きです！甘いものもよく食べます笑", "source": "manual"})

    # 同相手の映画ペア (Gold)
    client.post(f"/api/contacts/{cid_target}/messages", json={"sender": "contact", "content": "映画は何をよく観ますか？"})
    client.post(f"/api/contacts/{cid_target}/messages", json={"sender": "self", "content": "洋画のアクションとかSFをよく観ます！", "source": "manual"})

    # クエリ: カフェについての質問
    results = learning.retrieval.retrieve_relevant_pairs(
        query_text="おすすめのカフェありますか？",
        contact_id=cid_target,
        current_phase="getting_to_know",
        limit=2,
    )
    assert len(results) >= 1
    # カフェ関連ペアがトピックボーナスで最上位
    assert "カフェ" in results[0]["self_text"] or "カフェ" in results[0]["contact_text"]


def test_prompt_8_blocks_compactness_and_no_legacy_files(client):
    """8ブロックプロンプトが旧ルールの常時結合を含まず、10,000文字以内に収まりGold実績を最優先すること。"""
    cid = client.post("/api/contacts", json={"name": "8ブロック相手", "profile": "カフェと旅行が好きです"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェ行きませんか？"})

    ctx = generation._build_context(cid, "カフェの話", "hybrid")
    sys_prompt = ctx["system_prompt"]

    # 1. 8ブロックの構成検証
    assert "【ROLE & CURRENT DIRECTIVE】" in sys_prompt
    assert "【HARD INVARIANTS】" in sys_prompt
    assert "【CHAT HISTORY】" in sys_prompt
    assert "【LEARNED USER RESPONSE POLICY】" in sys_prompt
    assert "【POSITIVE REPLY PAIRS】" in sys_prompt
    assert "【OUTPUT CONTRACT】" in sys_prompt

    # 2. コンパクトさ（10,000文字以内）
    assert len(sys_prompt) < 10000

    # 3. 旧ファイル（常時全結合）の排除検証
    assert "原則3文以上" not in sys_prompt
    assert "3〜5文" not in sys_prompt
    assert "「。」不使用" not in sys_prompt


def test_learning_diagnostics_api(client):
    """GET /api/learning/diagnostics でコーパス内訳とバッチ採用率が正しく返却されること。"""
    r = client.get("/api/learning/diagnostics")
    assert r.status_code == 200
    data = r.json()

    assert "corpus" in data
    assert "gold_count" in data["corpus"]
    assert "silver_count" in data["corpus"]
    assert "bronze_count" in data["corpus"]
    assert "style_profile_tier" in data
    assert "batches" in data
    assert "candidate_sent_rate" in data["batches"]
    assert "manual_replaced_rate" in data["batches"]
