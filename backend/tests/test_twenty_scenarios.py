"""Comprehensive 20+ Scenarios Test Suite verifying Core Rules, Tone, and Learning-First Architecture."""
import json
import pytest
from app.ai import prompt
from app.routers import generation
from app import database


def test_01_single_topic_with_question():
    """TEST 1: 単一話題に対するプロンプト構築とCORE RULES"""
    p = prompt.build_system_prompt(
        rules=prompt.load_knowledge_texts("rules"),
        references=[],
        learning_materials=[],
        condition="",
        contact={"name": "相手"},
        chat_history_text="相手: 最近サウナにハマってます！",
        training_examples=[],
        self_profile={},
        role="self",
    )
    assert "【CORE RULES】" in p
    assert "架空の自己開示・事実捏造の禁止" in p
    assert "【OUTPUT CONTRACT】" in p
    assert "完全独立の3案" in p


def test_02_single_topic_no_question():
    """TEST 2: 単一話題＋質問なし（自由記述「質問しない」の厳守）"""
    p = prompt.build_system_prompt(
        rules=prompt.load_knowledge_texts("rules"),
        references=[],
        learning_materials=[],
        condition="質問はしない",
        contact={"name": "相手"},
        chat_history_text="相手: プロジェクターで映画見るの憧れます！",
        training_examples=[],
        self_profile={},
        role="self",
    )
    assert "【REPLY DIRECTIVE】" in p
    assert "自由記述に「質問しない」とあれば絶対に質問を入れない" in p
    assert "質問はしない" in p


def test_03_multi_topic():
    """TEST 3: 複数話題（相手の複数話題を含むプロンプト）"""
    p = prompt.build_system_prompt(
        rules=prompt.load_knowledge_texts("rules"),
        references=[],
        learning_materials=[],
        condition="",
        contact={"name": "相手"},
        chat_history_text="相手: 最近映画見てて、あと週末北海道にも行ってきました！",
        training_examples=[],
        self_profile={},
        role="self",
    )
    assert "【CORE RULES】" in p
    assert "相手: 最近映画見てて、あと週末北海道にも行ってきました！" in p


def test_04_unknown_entity_ai_question():
    """TEST 4: 映画など知らない作品（知ったかぶりせず未経験を伝えるかAI確認質問）"""
    raw_with_closing = "[AI_QUESTION]ブルーロックって見たことありますか？[/AI_QUESTION]"
    q = generation._extract_ai_question(raw_with_closing)
    assert q == "ブルーロックって見たことありますか？"


def test_05_partner_uses_emoji():
    """TEST 5: 相手が絵文字を使う（相手スタイル分析への反映）"""
    style = generation.analyze_counterpart_style(1)
    assert "summary" in style
    assert "metrics" in style


def test_06_partner_no_emoji():
    """TEST 6: 相手が絵文字を使わない場合のフォールバック"""
    style = generation.analyze_counterpart_style(99999)
    assert style["metrics"]["status"] == "insufficient_data"


def test_07_keigo_tone():
    """TEST 7: 敬語（です・ます調ベース）"""
    p = prompt.build_system_prompt(
        rules=[], references=[], learning_materials=[], condition="", contact={"name": "相手"},
        chat_history_text="相手: 初めまして！よろしくお願いします", training_examples=[], self_profile={}, role="self",
        tone="keigo"
    )
    assert "【敬語】全ての返信で敬語（です・ます調）をベースとする" in p
    assert "堅苦しい接客口調" in p


def test_08_tame_tone():
    """TEST 8: タメ口（自然な友達同士の口調）"""
    p = prompt.build_system_prompt(
        rules=[], references=[], learning_materials=[], condition="", contact={"name": "相手"},
        chat_history_text="相手: ヤッホー！休み何してた？", training_examples=[], self_profile={}, role="self",
        tone="tame"
    )
    assert "【タメ口】全ての返信で自然な友達同士のカジュアルな口語" in p


def test_09_hybrid_tone():
    """TEST 9: ハイブリッドは本人Goldに沿って丁寧さと親しみを混ぜる。"""
    p = prompt.build_system_prompt(
        rules=[], references=[], learning_materials=[], condition="", contact={"name": "相手"},
        chat_history_text="相手: 映画好きなんですね！", training_examples=[], self_profile={}, role="self",
        tone="hybrid"
    )
    assert "【ハイブリッド】完全敬語にも完全タメ口にも寄せず" in p
    assert "本人Goldと同一相手への実績を基準に、丁寧さと親しみを自然に混ぜる" in p
    assert "語尾を一律にです・ます調へ揃えず" in p


def test_10_condition_no_question():
    """TEST 10: 自由記述で「質問なし」が最優先されること"""
    p = prompt.build_system_prompt(
        rules=[], references=[], learning_materials=[], condition="質問なしで共感だけ", contact={"name": "相手"},
        chat_history_text="相手: カフェ巡りしてきました！", training_examples=[], self_profile={}, role="self"
    )
    assert "質問なしで共感だけ" in p
    assert "Priority 1" in p


def test_11_condition_light_tone():
    """TEST 11: 自由記述で「軽めに」が反映されること"""
    p = prompt.build_system_prompt(
        rules=[], references=[], learning_materials=[], condition="軽めに短く返す", contact={"name": "相手"},
        chat_history_text="相手: 今日は疲れた〜", training_examples=[], self_profile={}, role="self"
    )
    assert "軽めに短く返す" in p


def test_12_regeneration_no_instruction_changes_strategy():
    """TEST 12: 再生成（指示なし：単なる言い換え禁止・会話戦略の変更）"""
    msgs = prompt.build_revision_messages(
        system_prompt="システムプロンプト",
        chat_history_text="相手: 映画見ました",
        condition="",
        original_generated="案1: 映画いいですね！最近何を観ましたか？",
        revision_instruction="",
    )
    user_msg = msgs[-1]["content"]
    assert "単に言い換える（単語や語尾を少し変えるだけ）ことは厳禁です" in user_msg
    assert "会話の戦略・切り口そのものを大きく変更した新しい3案" in user_msg


def test_13_regeneration_with_humor_instruction():
    """TEST 13: 再生成＋「もっと冗談っぽく」（ユーザー修正指示最優先）"""
    msgs = prompt.build_revision_messages(
        system_prompt="システムプロンプト",
        chat_history_text="相手: 休日は寝てばかりです笑",
        condition="",
        original_generated="案1: お疲れ様です！しっかり休んでくださいね。",
        revision_instruction="もっと冗談っぽく",
    )
    user_msg = msgs[-1]["content"]
    assert "修正指示: もっと冗談っぽく" in user_msg


def test_14_short_contact_message():
    """TEST 14: 短い相手メッセージに対する自然な3案"""
    p = prompt.build_system_prompt(
        rules=prompt.load_knowledge_texts("rules"),
        references=[],
        learning_materials=[],
        condition="",
        contact={"name": "相手"},
        chat_history_text="相手: お疲れ様です！",
        training_examples=[],
        self_profile={},
        role="self",
    )
    assert "【CORE RULES】" in p
    assert "【FINAL TASK】" in p


def test_15_long_contact_message():
    """TEST 15: 長い相手メッセージに対するプロンプト生成"""
    chat_text = (
        "相手: 今日友達と新しくできたカフェに行ってきたんですけど、"
        "パンケーキがすごく美味しくて感動しました！"
        "あと帰りに気になってた映画のチケットも買っちゃいました笑"
    )
    p = prompt.build_system_prompt(
        rules=prompt.load_knowledge_texts("rules"),
        references=[],
        learning_materials=[],
        condition="",
        contact={"name": "相手"},
        chat_history_text=chat_text,
        training_examples=[],
        self_profile={},
        role="self",
    )
    assert "パンケーキがすごく美味しくて感動しました！" in p


def test_16_parse_replies_json_and_multiline():
    """TEST 16: 3案パース処理の堅牢性（JSONおよび厳格パース）"""
    raw_json = json.dumps({"replies": ["映画いいですね！\n僕も好きです\n何観たんですか？", "映画館行ってきたんですね！\nポップコーン食べました？笑\n気になります", "いいなー！\n最近映画館行ってないです笑\n久しぶりに行きたくなりました"]})
    res = generation._parse_replies_strict(raw_json, 3)
    assert len(res) == 3
    assert "映画いいですね！\n僕も好きです\n何観たんですか？" == res[0]


def test_17_conversation_memory_and_anti_duplication():
    """TEST 17: 会話履歴の記憶と既出情報再質問・重複自己開示の禁止ルール"""
    p = prompt.build_system_prompt(
        rules=prompt.load_knowledge_texts("rules"),
        references=[],
        learning_materials=[],
        condition="",
        contact={"name": "相手"},
        chat_history_text="相手: 映画が好きでコナンよく見ます\n自分: 僕もコナン好きです！",
        training_examples=[],
        self_profile={},
        role="self",
    )
    assert "【CORE RULES】" in p
    assert "会話履歴の既出情報重複禁止" in p
    assert "過去にすでに聞いたことを再質問したり、既に話した自己開示を初めてのように繰り返さない" in p


def test_18_punctuation_no_period_rule():
    """TEST 18: 句点「。」等の記号分析とプロファイル"""
    profile = generation.analyze_user_learned_style(1)
    assert "summary" in profile
    assert "weighted_profile" in profile


def test_19_facial_emoji_only(client):
    """TEST 19: 絵文字頻出ランキングの算出"""
    cid = client.post("/api/contacts", json={"name": "絵文字相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "カフェ巡り好きです😊✨\nおすすめ教えてください🙌"})
    profile = generation.analyze_user_learned_style(cid)
    assert "top_emojis" in profile.get("weighted_profile", {})


def test_20_no_fabricated_self_disclosure():
    """TEST 20: 架空の自己開示・予定捏造の絶対禁止"""
    p = prompt.build_system_prompt(
        rules=prompt.load_knowledge_texts("rules"),
        references=[],
        learning_materials=[],
        condition="",
        contact={"name": "相手"},
        chat_history_text="相手: 何か運動してますか？",
        training_examples=[],
        self_profile={},
        role="self",
    )
    assert "架空の自己開示・事実捏造の禁止" in p


def test_21_honorific_san_rule():
    """TEST 21: 相手を呼ぶときは必ずさんづけルール"""
    p = prompt.build_system_prompt(
        rules=prompt.load_knowledge_texts("rules"),
        references=[],
        learning_materials=[],
        condition="",
        contact={"name": "ユキ"},
        chat_history_text="相手: はじめまして！",
        training_examples=[],
        self_profile={},
        role="self",
    )
    assert "相手の呼称: 相手の名前を呼ぶ時は必ず「さん」付け（呼び捨て・あだ名禁止）" in p


def test_22_prompt_avoids_echo_without_banning_natural_katakana():
    """TEST 22: 相手文の言い換えを避け、語彙は文脈に合わせて選ぶ。"""
    p = prompt.build_system_prompt(
        rules=prompt.load_knowledge_texts("rules"),
        references=[],
        learning_materials=[],
        condition="",
        contact={"name": "ユキ"},
        chat_history_text="相手: 休日はカフェ巡りしてます",
        training_examples=[],
        self_profile={},
        role="self",
    )
    assert "言い換え" in p
    assert "一律に禁止しない" in p
    assert "リフレッシュ" not in p
    assert "ほかに" not in p
    assert "〜を求めて行ってきました" not in p
    assert "『何か』は漢字を使わず平仮名" not in p
