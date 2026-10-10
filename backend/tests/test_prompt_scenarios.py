"""Comprehensive test cases for the updated prompt, rules, and scenarios."""
import pytest
from app.ai import prompt
from app.routers import generation
from app import database


@pytest.mark.parametrize("tone", ["", "keigo", "tame"])
def test_user_punctuation_preference_is_soft_and_respects_tone_mode(tone):
    """ユーザーの記号の好みを反映しつつ、口調指定と自然さを優先する。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        tone=tone,
    )

    assert "「、」「。」は基本的に使わない" in system_prompt
    assert "「！」「？」や絵文字" in system_prompt
    assert "機械的に足さず" in system_prompt
    assert "読みやすさと自然さを優先" in system_prompt
    if tone == "keigo":
        assert "【敬語】" in system_prompt
    elif tone == "tame":
        assert "【タメ口】" in system_prompt


def test_scenario_1_single_topic():
    """TEST 1: 単一話題に対するプロンプト生成"""
    rules = prompt.load_knowledge_texts("rules")
    refs = prompt.load_knowledge_texts("references")
    training = prompt.load_knowledge_texts("training")

    p = prompt.build_system_prompt(
        rules=rules,
        references=refs,
        learning_materials=training,
        condition="映画の話を広げる",
        contact={"name": "花子", "profile": "映画が好きです"},
        chat_history_text="相手: 昨日映画見てきた！",
        training_examples=[],
        self_profile={"name": "太郎"},
        role="self",
    )
    assert "【REPLY DIRECTIVE】" in p
    assert "映画の話を広げる" in p
    assert "【CORE RULES】" in p
    assert "【FINAL TASK】" in p


def test_scenario_2_and_14_multi_topic():
    """TEST 2 & 14: 複数話題の優先順位とメイン深掘り＋サブ一言ルール"""
    rules = prompt.load_knowledge_texts("rules")
    p = prompt.build_system_prompt(
        rules=rules,
        references=[],
        learning_materials=[],
        condition="ボルダリングの話を広げる",
        contact={"name": "花子", "profile": ""},
        chat_history_text="相手: 昨日コナンの映画見てきた！あと最近ボルダリングも始めたんだ",
        training_examples=[],
        self_profile={},
        role="self",
    )
    assert "【REPLY DIRECTIVE】" in p
    assert "ボルダリングの話を広げる" in p
    assert "【CORE RULES】" in p


def test_scenario_3_counterpart_question():
    """TEST 3: 相手からの質問に対する優先回答ルール"""
    rules = prompt.load_knowledge_texts("rules")
    p = prompt.build_system_prompt(
        rules=rules,
        references=[],
        learning_materials=[],
        condition="",
        contact={"name": "花子", "profile": ""},
        chat_history_text="相手: 最近ハマってる食べ物とかありますか？",
        training_examples=[],
        self_profile={},
        role="self",
    )
    assert "【CORE RULES】" in p
    assert "会話履歴の既出情報重複禁止" in p
    assert "相手: 最近ハマってる食べ物とかありますか？" in p


def test_scenario_4_5_6_reply_directive():
    """TEST 4, 5, 6: 返信方向・追加自由記述の直接命令遵守"""
    rules = prompt.load_knowledge_texts("rules")
    p = prompt.build_system_prompt(
        rules=rules,
        references=[],
        learning_materials=[],
        condition="軽く共感して映画の話を広げる。質問は絶対にしないこと。",
        contact={"name": "花子", "profile": ""},
        chat_history_text="相手: 昨日映画見てきた！",
        training_examples=[],
        self_profile={},
        role="self",
    )
    assert "【REPLY DIRECTIVE】" in p
    assert "指示を無視して一般的な「無難な返信」を生成してはならない" in p
    assert "「自然な返信にする」ことを理由に、ユーザーの具体的な指示を変更・省略してはならない" in p
    assert "質問は絶対にしないこと" in p


def test_scenario_7_and_15_unknown_entity_ai_question():
    """TEST 7 & 15: 未知作品に対するAI確認質問 ([AI_QUESTION]) の出力・パース"""
    # 1. generation.py が [AI_QUESTION] を検知して question を返すこと
    raw_with_closing = "[AI_QUESTION]ブルーロックのアニメって見たことありますか？[/AI_QUESTION]"
    q = generation._extract_ai_question(raw_with_closing)
    assert q == "ブルーロックのアニメって見たことありますか？"

    # 閉じタグ省略形でも検知できること
    raw_without_closing = "[AI_QUESTION]\nブルーロックのアニメって見たことありますか？"
    q_no_close = generation._extract_ai_question(raw_without_closing)
    assert q_no_close == "ブルーロックのアニメって見たことありますか？"


def test_scenario_8_known_entity_user_knowledge():
    """TEST 8: USER KNOWLEDGE に情報がある場合の通常返信進行"""
    uk = "質問: ブルーロック見たことありますか？ 回答: 見たことないです"
    p = prompt.build_system_prompt(
        rules=[],
        references=[],
        learning_materials=[],
        condition="",
        contact={"name": "花子", "profile": ""},
        chat_history_text="相手: 最近ブルーロック見たんだけど見たことある？",
        training_examples=[],
        self_profile={},
        role="self",
        user_knowledge=uk,
    )
    assert "【SELF & MY INFO & USER KNOWLEDGE】" in p
    assert "見たことないです" in p


def test_scenario_9_10_11_natural_lengths_and_no_question():
    """TEST 9, 10, 11: 質問不要な場合の自然な対応と柔軟な文字数"""
    from pathlib import Path
    rules_dir = Path(__file__).resolve().parents[2] / "knowledge" / "rules"
    rules_text = "\n".join(p.read_text(encoding="utf-8") for p in rules_dir.glob("*.txt"))
    # 質問が必須でないこと
    assert "質問は最大1個まで。ただし質問は必須ではない" in rules_text
    # 柔軟な文字数
    assert "固定の文字数制限に囚われず" in rules_text or "相手の文章量やテンポに合わせる" in rules_text


def test_scenario_12_suppress_ai_politeness():
    """TEST 12: AI特有の過剰な丁寧語（そうなんですね！等）の抑制"""
    from pathlib import Path
    rules_dir = Path(__file__).resolve().parents[2] / "knowledge" / "rules"
    rules_text = "\n".join(p.read_text(encoding="utf-8") for p in rules_dir.glob("*.txt"))
    assert "そうなんですね！" in rules_text  # 避けるべき表現として記載
    assert "素敵ですね！" in rules_text
    assert "丁寧すぎる接客口調" in rules_text


def test_scenario_13_no_brackets_and_diverse_endings():
    """TEST 13: かぎかっこの除去と多様な文末"""
    raw_text = "「君の名は。」は『最高』【おすすめ】でした！"
    stripped = prompt.strip_brackets(raw_text)
    assert stripped == "君の名は。は最高おすすめでした！"
