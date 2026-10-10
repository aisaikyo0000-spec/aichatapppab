from app.routers import generation
from app.ai import prompt


def _context(sample_count: int = 5) -> dict:
    return {
        "system_prompt": (
            "【USER'S SAME-CONTACT REPLY STYLE】 Gold hierarchy and explicit tone priority.\n"
            "同一相手Goldの距離感と文量を参考にする。\n"
            "本人Goldの文量中央値（観測値）: 42文字。\n"
            "相手が短文でも、本人Goldの文量傾向を保つ。"
        ),
        "chat_text": "相手: 今日は仕事だった\n自分:",
        "pieces": {
            "relationship_guidance": "同一相手Goldの距離感と文量を参考にする。",
            "style_profile": {"same_contact_gold_samples": sample_count},
        },
    }


def test_normal_generation_keeps_contact_style_once_in_system_without_duplicate_length_guidance():
    messages = generation._build_initial_generation_messages(
        _context(),
        mode="normal",
        candidates=3,
        strategy_mode="normal",
    )

    system_text = messages[0]["content"]
    user_text = messages[1]["content"]
    assert "【USER'S SAME-CONTACT REPLY STYLE】" in system_text
    assert "本人Goldの文量中央値（観測値）: 42文字" in system_text
    assert system_text.count("本人Goldの文量中央値（観測値）: 42文字") == 1
    assert "明示された口調指定があれば最優先する" in user_text
    assert "Goldの返信例はテンポや構成を参考にし、語句・文面をコピーしない" in user_text
    assert "【相手別Gold傾向の優先】" not in user_text
    assert "同一相手Goldの距離感と文量を参考にする。" not in user_text
    assert "直近発言が短くても、それだけを理由に全案を機械的に短くせず" not in user_text


def test_followup_generation_uses_contact_style_from_system_without_duplicate_user_block():
    messages = generation._build_initial_generation_messages(
        _context(),
        mode="followup",
        candidates=3,
        strategy_mode="normal",
    )

    system_text = messages[0]["content"]
    user_text = messages[1]["content"]
    assert "【USER'S SAME-CONTACT REPLY STYLE】" in system_text
    assert "同一相手Goldの距離感と文量を参考にする。" in system_text
    assert "【相手別Gold傾向の優先】" not in user_text
    assert "同一相手Goldの距離感と文量を参考にする。" not in user_text


def test_sparse_contact_data_does_not_add_contact_style_instruction():
    messages = generation._build_initial_generation_messages(
        _context(sample_count=4),
        mode="normal",
        candidates=3,
        strategy_mode="normal",
    )

    assert "【相手別Gold傾向の優先】" not in messages[1]["content"]


def test_complete_normal_prompt_keeps_contact_observations_without_repeating_content_policy():
    relationship_summary = (
        "本人Gold 8件の観測: 丁寧・砕けた文体が混在。"
        "同一相手Gold中央値42字、Global Gold中央値37字。"
    )
    system_prompt = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        same_contact_gold_samples=8,
        same_contact_reply_style_block=relationship_summary,
        conversation_ledger={
            "counterpart_intent": "emotional_share",
            "last_contact_message": "最近ちょっと疲れた",
        },
    )
    messages = prompt.build_initial_generation_messages(system_prompt=system_prompt)
    complete_prompt = system_prompt + "\n" + messages[1]["content"]

    assert complete_prompt.count("【REPLY CONTENT CHOICE】") == 1
    assert complete_prompt.count("本人Goldの文量傾向を参考にしつつ") == 1
    assert complete_prompt.count("会話を続けるためだけの質問は加えない") == 1
    assert "同一相手Gold中央値42字、Global Gold中央値37字" in complete_prompt
    assert complete_prompt.count("同一相手Gold中央値42字") == 1
    assert "本人Goldの文量分布を今回の返信量に反映する" not in complete_prompt
    assert "候補間で自然な違いが作れる場合だけ反応の焦点を変え" not in complete_prompt
    assert "質問は会話上必要な場合だけ使う" not in complete_prompt
