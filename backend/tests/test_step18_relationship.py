"""Step 18: 相手別・関係性別の会話距離適応。

- build_relationship_summary: 観測特徴のみ（関係ラベルなし）、実績0件時は空文字
- contact_tone_fit: Gold 3件未満は中立 0.5、tame/keigo の適合を判定
- ranking への影響は軽微（±0.02）かつ無データ時は中立
"""
from __future__ import annotations

import json

import pytest

from app.ai import prompt
from app import database
from app.learning import contrast, corpus, style
from app.routers import generation


def _style_repair_issues(replies, profile, **context_fields):
    context_fields.setdefault("same_contact_gold_median", None)
    context_fields.setdefault("global_gold_median", None)
    context = generation._ContactStyleRepairContext(
        profile=profile, **context_fields
    )
    return generation._contact_style_soft_repair_issues(replies, context)


def _style_repair_improves(
    previous_replies, candidate_replies, profile, *, previous_issues, **context_fields
):
    context_fields.setdefault("same_contact_gold_median", None)
    context_fields.setdefault("global_gold_median", None)
    context = generation._ContactStyleRepairContext(
        profile=profile, **context_fields
    )
    return generation._contact_style_repair_improves(
        previous_replies, candidate_replies, context, previous_issues
    )


def _seed_gold(client, name: str, pairs: list[tuple[str, str]]) -> int:
    cid = client.post("/api/contacts", json={"name": name, "profile": ""}).json()["id"]
    for contact_msg, self_msg in pairs:
        r = client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": contact_msg})
        assert r.status_code == 201
        r = client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": self_msg})
        assert r.status_code == 201
    return cid


def _seed_non_gold(client, name: str, pairs: list[tuple[str, str]]) -> int:
    cid = client.post("/api/contacts", json={"name": name, "profile": ""}).json()["id"]
    for contact_msg, self_msg in pairs:
        client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": contact_msg})
        response = client.post(
            f"/api/contacts/{cid}/messages",
            json={"sender": "self", "content": self_msg, "source": "legacy_unknown"},
        )
        assert response.status_code == 201
    return cid


def _seed_accepted_generation(cid: int, text: str = "いいですね笑") -> None:
    conn = database.get_conn()
    try:
        conn.execute(
            "INSERT INTO generation_history"
            " (contact_id, provider, model, generated_text, is_sent, created_at)"
            " VALUES (?, 'fake', 'test-model', ?, 1, ?)",
            (cid, text, database.now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


TAME_PAIRS = [
    ("今日暇だった", "いいなー笑"),
    ("眠い", "わかるよ笑"),
    ("雨降ってきた", "ほんとだね笑"),
    ("おつ", "おつおつ！"),
    ("まじ", "それはすごいね"),
]

KEIGO_PAIRS = [
    ("今日はありがとうございました", "こちらこそありがとうございました！とても楽しかったです！"),
    ("明日はよろしくお願いします", "こちらこそよろしくお願いいたします！準備を進めておきます！"),
    ("資料を送付しました", "資料を確認いたしました！ありがとうございます！"),
    ("会議は来週です", "承知いたしました！来週よろしくお願いいたします！"),
    ("お疲れ様でした", "お疲れ様でした！本日はありがとうございました！"),
]


def test_relationship_summary_empty_without_data(client):
    assert style.build_relationship_summary(None) == ""
    assert style.build_relationship_summary(999999) == ""


def test_relationship_summary_tame_contact(client):
    cid = _seed_gold(client, "Aさん", TAME_PAIRS)
    block = style.build_relationship_summary(cid)
    assert "この相手への返信距離感" in block
    assert "砕けた" in block
    assert "5件" in block
    # 関係ラベルを付けない（§3・§29）
    for label in ("恋人", "友達", "上司", "同僚", "先輩", "後輩"):
        assert label not in block


def test_relationship_summary_hides_question_rate_until_five_gold_samples(client):
    question_pairs = [
        ("カフェ行ったよ", "いいですね！どのあたりですか？"),
        ("映画見た", "それ気になります！いつ見たんですか？"),
        ("旅行してきた", "楽しそう！誰と行ったんですか？"),
    ]
    cid = _seed_gold(client, "質問率は少数", question_pairs)

    summary = style.build_relationship_summary(cid)

    assert "質問多め" not in summary
    assert "質問少なめ" not in summary
    assert "質問普通" not in summary


def test_relationship_summary_keigo_contact(client):
    cid = _seed_gold(client, "Bさん", KEIGO_PAIRS)
    block = style.build_relationship_summary(cid)
    assert "丁寧" in block
    assert "5件" in block
    assert "本人Goldの文量分布を今回の返信量に反映する" in block


def test_relationship_summary_keeps_mixed_contact_tone_mixed(client):
    mixed_pairs = [
        ("昨日映画見てきた", "映画いいですね！"),
        ("カフェ行ってきたよ", "カフェいいな、ゆっくりできそう"),
        ("コンビニで新作スイーツ見つけた", "それ気になる！"),
        ("天気いいね", "ほんとだね！"),
        ("週末はゆっくりできそう", "よかったですね！ゆっくりできそう"),
    ]
    cid = _seed_gold(client, "中間の距離感", mixed_pairs)

    summary = style.build_relationship_summary(cid)

    assert "丁寧さと砕け具合が混在" in summary
    assert "本人Goldの混合スタイルを基本" in summary
    assert "場面に合う範囲で丁寧さと本人らしい会話調を混ぜる" in summary
    assert "比率や候補数は固定しない" in summary
    assert "笑や絵文字だけでは口調適応と見なさない" in summary


def test_relationship_summary_distinguishes_hybrid_and_more_casual_gold(client):
    hybrid_pairs = [
        ("映画見てきた", "映画いいですね！気になるな笑"),
        ("カフェ行った", "カフェいいですね！行ってみたいな"),
        ("スイーツ食べた", "おいしそうですね！いいなー"),
        ("天気いいね", "過ごしやすそうですね！気分いいよね"),
        ("週末はゆっくりできそう", "よかったですね！ゆっくりできるといいね"),
    ]
    casual_pairs = [
        ("映画見てきた", "映画いいね笑"),
        ("カフェ行った", "カフェいいな笑"),
        ("スイーツ食べた", "おいしそう！"),
        ("天気いいね", "ほんとだね！"),
        ("週末はゆっくりできそう", "いいじゃん、のんびりしよう笑"),
    ]

    hybrid_summary = style.build_relationship_summary(
        _seed_gold(client, "丁寧さを混ぜる相手", hybrid_pairs)
    )
    casual_summary = style.build_relationship_summary(
        _seed_gold(client, "会話調が多い相手", casual_pairs)
    )

    assert "本人Goldの混合スタイルを基本" in hybrid_summary
    assert "本人Goldの砕けた会話調を基本" in casual_summary


def test_relationship_summary_includes_mixed_tone_guidance_below_old_threshold(client):
    mostly_polite_pairs = KEIGO_PAIRS[:3] + TAME_PAIRS[:3]
    cid = _seed_gold(client, "丁寧寄りの混合Gold", mostly_polite_pairs)

    summary = style.build_relationship_summary(cid)

    assert "丁寧さと砕け具合が混在" in summary
    assert "本人Goldでは砕けた会話調がやや多い" not in summary
    assert "一般的な丁寧語だけへ一律に寄せない" in summary
    assert "3案中少なくとも2案を敬語だけで終わらせず" not in summary


def test_explicit_tone_overrides_mixed_contact_tone_guidance(client):
    mixed_pairs = [
        ("映画を見た", "映画いいですね！"),
        ("カフェへ行った", "カフェいいな、ゆっくりできそう"),
        ("新作を見つけた", "それ気になる！"),
        ("天気いいね", "ほんとだね！"),
        ("週末は休みです", "よかったですね、ゆっくりできそう"),
    ]
    cid = _seed_gold(client, "明示トーン優先", mixed_pairs)

    summary = style.build_relationship_summary(cid, requested_tone="keigo")

    assert "明示トーン指定（敬語）を最優先" in summary
    assert "敬語語尾を使わない案を少なくとも1つ" not in summary


def test_inferred_tone_is_not_mislabeled_as_explicit_selection(client):
    cid = _seed_gold(client, "自動推定の丁寧口調", KEIGO_PAIRS)

    ctx = generation._build_context(cid, "", "", "normal")

    assert ctx["effective_tone"] == ""
    assert "明示トーン指定" not in ctx["system_prompt"]


def test_relationship_summary_uses_global_fallback_for_fewer_than_three_gold(client):
    cid = _seed_gold(client, "Cさん", TAME_PAIRS[:2])
    assert style.build_relationship_summary(cid) == ""


def test_sparse_contact_tone_summary_does_not_override_global_mixed_profile(client):
    _seed_gold(client, "Global mixed prior", KEIGO_PAIRS[:2] + TAME_PAIRS[:3])
    local_id = _seed_gold(
        client,
        "Sparse mixed contact",
        [
            ("映画みた", "映画いいですね！気になるな笑"),
            ("カフェ行った", "カフェいいな笑"),
            ("スイーツ食べた", "おいしそうですね！"),
        ],
    )

    summary = style.build_relationship_summary(local_id)

    assert "自動口調ではGoldの砕けた会話調を基本" not in summary
    assert "自動口調では本人Goldの混合スタイルを基本" not in summary


def test_relationship_summary_describes_contact_relative_message_length(client):
    _seed_gold(client, "短文のGlobal Gold", TAME_PAIRS)
    longer_pairs = [
        (f"話題{i}", "それは本当に大変でしたね！" + "今日は無理せずゆっくり休んでくださいね" * 2)
        for i in range(6)
    ]
    cid = _seed_gold(client, "長めの同一相手Gold", longer_pairs)

    summary = style.build_relationship_summary(cid)

    assert "Global Goldより相対的に長め" in summary
    assert "同一相手Goldの文量中央値は" in summary
    assert "返信の長さはこの差も参考にしつつ" in summary
    assert "現在の会話内容に合う範囲で決めること" in summary
    assert "質問は会話上必要な場合だけ使う" in summary
    assert "相手の発言を言い換えて水増ししたりしない" in summary


def test_relationship_summary_adapts_candidate_level_laugh_and_length_distribution(client, monkeypatch):
    cid = _seed_gold(
        client,
        "候補分布の相手",
        [(f"入力{i}", f"いいですね！楽しかったですね！") for i in range(6)],
    )
    monkeypatch.setattr(
        style,
        "compute_hierarchical_profile",
        lambda _cid: {
            "same_contact_blended_gold_profile": style.StyleProfile(
                sample_count=6, char_median=75, laugh_ratio=0.67
            ),
            "other_contact_gold_profile": style.StyleProfile(
                sample_count=20, char_median=50
            ),
            "same_contact_gold_samples": 6,
            "contact_adaptation_weight": 0.55,
        },
    )

    summary = style.build_relationship_summary(cid)

    assert "笑い表現の頻度は候補全体の参考" in summary
    assert "かなり長め" in summary
    assert "Goldの文量分布も参考にする" in summary
    assert "1案だけ" not in summary
    assert "状態や気持ちの共有には短い労いだけで毎回終えず" in summary
    assert "候補間で自然な違いが作れる場合だけ反応の焦点を変え" in summary


def test_contact_tone_fit_neutral_without_data(client):
    assert contrast.contact_tone_fit("おつかれ笑", None) == 0.5
    assert contrast.contact_tone_fit("おつかれ笑", 999999) == 0.5


def test_contact_tone_fit_prefers_matching_tone(client):
    cid_tame = _seed_gold(client, "Aさん", TAME_PAIRS)
    cid_keigo = _seed_gold(client, "Bさん", KEIGO_PAIRS)
    tame_cand = "おつかれ笑"
    keigo_cand = "お疲れ様でした。ありがとうございます。"
    # tame相手にはtame候補が高適合、keigo相手にはkeigo候補が高適合
    assert contrast.contact_tone_fit(tame_cand, cid_tame) >= contrast.contact_tone_fit(keigo_cand, cid_tame)
    assert contrast.contact_tone_fit(keigo_cand, cid_keigo) >= contrast.contact_tone_fit(tame_cand, cid_keigo)
    # 3件未満は中立
    cid_few = _seed_gold(client, "Cさん", TAME_PAIRS[:2])
    assert contrast.contact_tone_fit(tame_cand, cid_few) == 0.5


def test_contact_length_fit_prefers_observed_gold_length_without_hard_target(client):
    short_pairs = [
        (f"短い話題{i}", "いいね笑") for i in range(5)
    ]
    long_pairs = [
        (f"話題{i}", "それいいですね！" + "ゆっくり楽しめそうです" * 3)
        for i in range(5)
    ]
    short_cid = _seed_gold(client, "短文Gold", short_pairs)
    long_cid = _seed_gold(client, "長文Gold", long_pairs)
    short_reply = "それいいね笑"
    long_reply = "それいいですね！ゆっくり楽しめそうです。"

    assert contrast.contact_length_fit(short_reply, short_cid) > contrast.contact_length_fit(
        long_reply, short_cid
    )
    assert contrast.contact_length_fit(long_reply, long_cid) > contrast.contact_length_fit(
        short_reply, long_cid
    )
    assert contrast.contact_length_fit(short_reply, None) == 0.5
    sparse_cid = _seed_gold(client, "少数Gold", short_pairs[:2])
    assert contrast.contact_length_fit(short_reply, sparse_cid) == 0.5


def test_contact_question_rate_requires_reliable_same_contact_gold():
    assert generation._contact_style_question_rate(None) is None
    assert generation._contact_style_question_rate(
        style.StyleProfile(sample_count=4, question_ratio=0.1)
    ) is None
    assert generation._contact_style_question_rate(
        style.StyleProfile(sample_count=5, question_ratio=0.1)
    ) == 0.1
    assert generation._contact_style_question_rate(
        style.StyleProfile(sample_count=5, question_ratio=0.8), suppress=True
    ) is None


def test_learned_policy_never_emits_question_frequency_as_generation_cue():
    profile = style.StyleProfile(sample_count=12, question_ratio=0.8)
    sparse = style.to_learned_policy_prompt({
        "active_profile": profile,
        "hierarchy_tier": "same_contact_recent_manual_gold",
        "same_contact_gold_samples": 4,
    })
    reliable = style.to_learned_policy_prompt({
        "active_profile": profile,
        "hierarchy_tier": "same_contact_recent_manual_gold",
        "same_contact_gold_samples": 5,
    })

    for block in (sparse, reliable):
        assert "質問で終える割合" not in block
        assert "質問多め" not in block
        assert "質問少なめ" not in block
        assert "質問・深掘り・自己開示は会話上の意味があり相手が答えやすい場合に使う" in block


def test_relationship_summary_never_emits_question_frequency_for_reliable_gold(client):
    question_pairs = [
        ("カフェ行ったよ", "いいですね！どのあたりですか？"),
        ("映画見た", "それ気になります！いつ見たんですか？"),
        ("旅行してきた", "楽しそう！誰と行ったんですか？"),
        ("新作食べた", "美味しそう！何味ですか？"),
        ("水族館行った", "いいですね！何がいましたか？"),
    ]
    summary = style.build_relationship_summary(
        _seed_gold(client, "高質問率でも質問cueを出さない", question_pairs)
    )

    assert "質問多め" not in summary
    assert "質問少なめ" not in summary
    assert "質問普通" not in summary
    assert "質問率" not in summary
    assert "質問は会話上必要な場合だけ使う" in summary


def test_question_rate_is_suppressed_in_complete_prompt_after_self_question(client):
    question_pairs = [
        ("カフェ行ったよ", "いいですね！どのあたりですか？"),
        ("映画見た", "それ気になります！いつ見たんですか？"),
        ("旅行してきた", "楽しそう！誰と行ったんですか？"),
        ("新作食べた", "美味しそう！何味ですか？"),
        ("水族館行った", "いいですね！何がいましたか？"),
    ]
    cid = _seed_gold(client, "質問cueの履歴", question_pairs)
    client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "contact", "content": "今日は仕事で疲れた"},
    )

    ctx = generation._build_context(cid, "", "", "normal")
    system_prompt = ctx["system_prompt"]

    assert ctx["pieces"]["conversation_ledger"]["prev_self_ended_with_question"]
    assert "質問で終える割合" not in system_prompt
    assert "質問多め" not in system_prompt
    assert "質問少なめ" not in system_prompt
    assert "質問率" not in system_prompt
    assert "今回は質問なしでも成立する候補を優先" in system_prompt
    assert "相手が明確な質問をしている場合は必ず回答" in system_prompt
    assert "相手の明確な質問への回答や、未解決事項の確認に必要な質問は引き続き使ってよい" in system_prompt


def test_question_gold_preference_softens_consecutive_question_prompt():
    common_ledger = {
        "last_contact_message": "明日は休みなんだ",
        "counterpart_intent": "report",
        "prev_self_ended_with_question": True,
    }
    sparse_prompt = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        conversation_ledger=common_ledger,
    )
    high_question_gold_prompt = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        conversation_ledger={**common_ledger, "same_contact_gold_question_rate": 0.7},
    )

    assert "同一相手Goldでは質問で会話を返す傾向がある" not in sparse_prompt
    assert "質問率" not in high_question_gold_prompt
    assert "今回は質問なしでも成立する候補を優先" in high_question_gold_prompt
    assert "同一相手Goldでは質問で会話を返す傾向がある" not in high_question_gold_prompt


def test_combined_generation_prompt_omits_gold_question_rate_but_keeps_questions_optional(client):
    question_pairs = [
        ("カフェ行ったよ", "いいですね！どのあたりですか？"),
        ("映画見た", "それ気になります！いつ見たんですか？"),
        ("旅行してきた", "楽しそう！誰と行ったんですか？"),
        ("新作食べた", "美味しそう！何味ですか？"),
        ("水族館行った", "いいですね！何がいましたか？"),
    ]
    cid = _seed_gold(client, "質問頻度cue重複なし", question_pairs)
    learned_policy = style.to_learned_policy_prompt({
        "active_profile": style.StyleProfile(sample_count=8, question_ratio=0.8),
        "hierarchy_tier": "same_contact_recent_manual_gold",
        "same_contact_gold_samples": 5,
    })
    same_contact_summary = style.build_relationship_summary(cid)
    system_prompt = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        learned_policy_block=learned_policy,
        same_contact_reply_style_block=same_contact_summary,
        conversation_ledger={
            "counterpart_intent": "report",
            "prev_self_ended_with_question": True,
            "same_contact_gold_question_rate": 0.8,
        },
    )

    assert "質問で終える割合" not in system_prompt
    assert "質問率" not in system_prompt
    assert "質問多め" not in system_prompt
    assert "質問少なめ" not in system_prompt
    assert "質問は任意: 質問を含めるかどうかは会話状況次第" in system_prompt
    assert "会話上の意味があり相手が答えやすい質問は使ってよい" in system_prompt
    assert generation._contact_style_question_rate(
        style.StyleProfile(sample_count=8, question_ratio=0.8)
    ) == 0.8


def test_contact_gold_style_is_separate_from_counterpart_writing_style():
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        same_contact_gold_samples=12,
        same_contact_gold_length_median=50,
        counterpart_style_block="相手は短文中心です",
        same_contact_reply_style_block=(
            "この相手への本人Goldは混合口調が中心。笑と感嘆符を使う傾向。"
        ),
        counterpart_length_tier="short",
        counterpart_length_chars=7,
    )

    assert "【USER'S SAME-CONTACT REPLY STYLE】" in sysp
    assert "本人Goldは混合口調が中心" in sysp
    assert "相手は短文中心です" in sysp
    assert "十分な同一相手Goldに由来する傾向" in sysp
    assert "状態や気持ちの共有には、短文でも本人Goldらしい温度感と反応の厚みを保ち" in sysp
    user_style_start = sysp.index("【USER'S SAME-CONTACT REPLY STYLE】")
    counterpart_start = sysp.index("【COUNTERPART STYLE ADAPTATION】")
    assert user_style_start < counterpart_start
    counterpart_section = sysp[counterpart_start:]
    assert "本人Goldは混合口調が中心" not in counterpart_section

    sparse_prompt = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        same_contact_gold_samples=4,
        same_contact_reply_style_block="本人Goldの少数傾向",
    )
    assert "同一相手Goldはまだ少数のため弱い参考情報" in sparse_prompt
    assert "Globalの本人Goldを基準" in sparse_prompt
    assert "この本人Gold傾向を優先してください" not in sparse_prompt


def test_contact_length_nudge_breaks_near_tie_but_preserves_quality_gap():
    profile = style.StyleProfile(sample_count=6, char_median=40)
    near_tie = [
        {"reply": "x" * 40, "final": 0.500},
        {"reply": "x" * 10, "final": 0.495},
    ]
    clear_quality_gap = [
        {"reply": "x" * 10, "final": 0.500},
        {"reply": "x" * 40, "final": 0.470},
    ]
    forced_question_precedence = [
        {"reply": "x" * 40, "final": 0.480},  # 0.020 forced-question deduction already applied
        {"reply": "", "final": 0.500},
    ]

    generation._apply_contact_length_nudge(near_tie, profile, contact_id=1)
    generation._apply_contact_length_nudge(clear_quality_gap, profile, contact_id=1)
    generation._apply_contact_length_nudge(
        forced_question_precedence, profile, contact_id=1
    )

    assert near_tie[0]["final"] > near_tie[1]["final"]
    assert clear_quality_gap[0]["final"] > clear_quality_gap[1]["final"]
    assert forced_question_precedence[0]["final"] < forced_question_precedence[1]["final"]
    assert abs(near_tie[0]["contact_length_adjustment"]) <= 0.005


def test_initial_generation_prioritizes_contact_gold_without_fixed_length():
    relationship_summary = (
        "この相手のGoldはGlobalより長め。3案のうち1案は共感に具体的な反応を添える。"
    )

    messages = prompt.build_initial_generation_messages(
        system_prompt="contact style is available in system prompt",
        contact_style_instruction=relationship_summary,
    )

    user_instruction = messages[1]["content"]
    assert "確認できない事実や相手の状況" in user_instruction
    assert "プロフィールの嗜好は本人の経験の根拠にしない" in user_instruction
    assert "この相手に対する本人Goldの口調・文量を優先" in user_instruction
    assert "本人のその相手への口調・距離感を一般的な丁寧語より優先" in user_instruction
    assert "相手の直近文の短さやGlobalの一般傾向だけで全案を短くしない" in user_instruction
    assert "状態共有を毎回一言の労いに縮めず" in user_instruction
    assert "1案だけ" not in user_instruction
    assert "固定文字数には合わせない" in user_instruction
    assert relationship_summary in user_instruction

    long_relationship_summary = "この相手はGlobal Goldよりかなり長めに返す傾向がある。"
    long_messages = prompt.build_initial_generation_messages(
        system_prompt="contact style is available in system prompt",
        contact_style_instruction=long_relationship_summary,
    )
    long_user_instruction = long_messages[1]["content"]
    assert "状態共有を毎回一言の労いに縮めず" in long_user_instruction
    assert "1案だけ" not in long_user_instruction


def test_initial_generation_distinguishes_candidate_meaning_not_just_wording():
    messages = prompt.build_initial_generation_messages(
        system_prompt="system",
        contact_style_instruction="同一相手Goldの傾向を優先する",
    )

    user_instruction = messages[1]["content"]
    assert "自然に差が生まれる場合は反応の焦点を変えてよい" not in user_instruction
    assert "入力が短いという理由だけで内容のある状態共有を毎回一言の労いに縮めない" in user_instruction
    assert "本人のその相手への口調・距離感を一般的な丁寧語より優先" in user_instruction
    assert "会話上の意味があり答えやすい質問は使える" in user_instruction
    assert "質問は情報が本当に必要な場合だけ" not in user_instruction
    assert "内容への返答が自然に短く成立する場面では、簡潔に返して構いません" in user_instruction
    assert "短くするのは挨拶・相づち・受領・終了など内容の幅が狭い場面に限り" not in user_instruction


def test_contact_style_soft_repair_detects_large_gold_mismatch_but_honors_explicit_tone():
    mixed_contact = style.StyleProfile(
        sample_count=8,
        char_median=70,
        keigo_ratio=0.2,
        hybrid_ratio=0.4,
        tame_ratio=0.4,
    )
    overly_similar_polite_replies = [
        "今週は仕事でお疲れ様でした。ゆっくり休んでください。",
        "今週はお仕事で大変でした。早めに寝てください。",
        "お仕事が続いてお疲れ様でした。週末は休んでください。",
    ]

    issues = _style_repair_issues(
        overly_similar_polite_replies,
        mixed_contact,
        same_contact_gold_median=70,
        global_gold_median=45,
        explicit_tone="",
    )
    explicit_tone_issues = _style_repair_issues(
        overly_similar_polite_replies,
        mixed_contact,
        same_contact_gold_median=70,
        global_gold_median=45,
        explicit_tone="keigo",
    )

    assert any("Goldでは会話調の返信" in issue for issue in issues)
    assert any("今回の返信候補が短め" in issue for issue in issues)
    length_issue = next(issue for issue in issues if "今回の返信候補が短め" in issue)
    assert "会話で確認できる事実" in length_issue
    assert "確認できない具体的な行動を補わない" in length_issue
    assert not any("Goldでは会話調の返信" in issue for issue in explicit_tone_issues)
    assert any("今回の返信候補が短め" in issue for issue in explicit_tone_issues)
    assert any("休息・睡眠を勧める内容" in issue for issue in explicit_tone_issues)


def test_contact_style_soft_repair_ignores_casual_final_clause_and_sensitive_topics():
    mixed_contact = style.StyleProfile(
        sample_count=8,
        char_median=70,
        keigo_ratio=0.2,
        hybrid_ratio=0.4,
        tame_ratio=0.4,
    )
    mixed_replies = [
        "今週ずっとお仕事だったんですね！ゆっくり休んでね！",
        "お仕事お疲れ様です！無理しないでね！",
        "ゆっくり休んでね！本当にお疲れ様です！",
    ]

    mixed_issues = _style_repair_issues(
        mixed_replies,
        mixed_contact,
        same_contact_gold_median=None,
        global_gold_median=None,
    )
    sensitive_issues = _style_repair_issues(
        ["大変でしたね。無理しないでくださいね！"] * 3,
        mixed_contact,
        same_contact_gold_median=70,
        global_gold_median=45,
        counterpart_message="家族が入院していて不安です",
    )
    recent_history_issues = _style_repair_issues(
        ["大変でしたね。無理しないでくださいね！"] * 3,
        mixed_contact,
        same_contact_gold_median=70,
        global_gold_median=45,
        counterpart_message="今日はありがとう！",
        conversation_context="相手: 家族が入院していて不安です\n自分: それは心配だね",
    )

    assert not any("Goldでは会話調の返信" in issue for issue in mixed_issues)
    assert sensitive_issues == []
    assert recent_history_issues == []


def test_contact_style_soft_repair_detects_one_recommendation_repeated_across_all_options():
    profile = style.StyleProfile(
        sample_count=8, keigo_ratio=0.0, hybrid_ratio=0.5, tame_ratio=0.5, laugh_ratio=0.0
    )
    repeated_rest_advice = [
        "お仕事おつかれさま！今日はゆっくり休んでね",
        "本当におつかれさま！無理せずゆっくり寝てね",
        "お仕事大変だったね！早めに寝て身体を休めてね",
    ]
    mixed_focuses = [
        "お仕事おつかれさま！",
        "一日頑張ったね、本当にえらいよ",
        "疲れた日は無理せずゆっくり休んでね",
    ]
    short_reactions = ["おつかれ！", "大変だったね", "無理しないでね"]
    distinct_activities = [
        "たくさん食べて過ごしてね",
        "今日はのんびり過ごそう",
        "ゆっくり話してね",
    ]
    slow_conversation = ["ゆっくり話してね"] * 3
    common_rest_advice = [
        "今日はちゃんと寝てね",
        "早く寝た方がいいよ",
        "休めたらいいね",
    ]
    common_recovery_advice = [
        "今日はゆっくりしてね",
        "無理せず体を休めてくださいね",
        "今日はゆっくり過ごしてください",
    ]
    alternate_sleep_recovery_advice = [
        "今日は早寝してね",
        "睡眠をしっかり取ってくださいね",
        "疲れを癒やしてね",
    ]
    additional_recovery_advice = [
        "無理しないでね",
        "休めるといいね",
        "よく眠れるといいね",
        "疲れが取れるといいね",
        "今日はしっかり休養してね",
        "今日は休養を取ってね",
        "今日は休息を取ってね",
    ]

    repeated_issues = _style_repair_issues(
        repeated_rest_advice,
        profile,
    )
    mixed_issues = _style_repair_issues(
        mixed_focuses,
        profile,
    )
    short_issues = _style_repair_issues(
        short_reactions,
        profile,
    )
    activity_issues = _style_repair_issues(distinct_activities, profile)
    conversation_issues = _style_repair_issues(slow_conversation, profile)
    common_advice_issues = _style_repair_issues(common_rest_advice, profile)
    recovery_advice_issues = _style_repair_issues(common_recovery_advice, profile)
    alternate_recovery_issues = _style_repair_issues(
        alternate_sleep_recovery_advice, profile
    )
    additional_recovery_issues = _style_repair_issues(
        additional_recovery_advice[:3], profile
    )
    additional_recovery_detected = [
        generation._has_rest_recommendation(reply)
        for reply in additional_recovery_advice
    ]

    assert any("複数案が休息・睡眠を勧める内容で意味が重なっています" in issue for issue in repeated_issues)
    overlap_issue = next(issue for issue in repeated_issues if "複数案が休息・睡眠" in issue)
    assert "助言なしの労いや共感が今回の話題に自然なら候補に含めてもよい" in overlap_issue
    assert "確認できない理由や予定を補わない" in overlap_issue
    assert not any("複数案が休息・睡眠を勧める内容で意味が重なっています" in issue for issue in mixed_issues)
    assert not any("複数案が休息・睡眠を勧める内容で意味が重なっています" in issue for issue in short_issues)
    assert not any("複数案が休息・睡眠を勧める内容で意味が重なっています" in issue for issue in activity_issues)
    assert not any("複数案が休息・睡眠を勧める内容で意味が重なっています" in issue for issue in conversation_issues)
    assert any("複数案が休息・睡眠を勧める内容で意味が重なっています" in issue for issue in common_advice_issues)
    assert any("複数案が休息・睡眠を勧める内容で意味が重なっています" in issue for issue in recovery_advice_issues)
    assert any("複数案が休息・睡眠を勧める内容で意味が重なっています" in issue for issue in alternate_recovery_issues)
    assert any("複数案が休息・睡眠を勧める内容で意味が重なっています" in issue for issue in additional_recovery_issues)
    assert all(additional_recovery_detected)


def test_contact_style_rest_advice_detector_ignores_past_or_positive_mentions():
    assert not generation._has_rest_recommendation("今日は休めてよかったね")
    assert not generation._has_rest_recommendation("先週は休んでたんだね")
    assert generation._has_rest_recommendation("今日は休養を取ってね")


def test_contact_style_laugh_guidance_accounts_for_reliable_one_in_three_gold_rate():
    replies_without_laugh = ["おつかれさま！", "大変だったね", "仕事お疲れ様です"]

    issues = generation._contact_style_laugh_issues(
        replies_without_laugh,
        style.StyleProfile(sample_count=8, laugh_ratio=0.34),
    )
    low_confidence_issues = generation._contact_style_laugh_issues(
        replies_without_laugh,
        style.StyleProfile(sample_count=8, laugh_ratio=0.29),
    )

    assert any("笑" in issue or "w" in issue for issue in issues)
    assert low_confidence_issues == []


def test_contact_style_register_issue_describes_partial_conversational_coverage_accurately():
    issues = generation._contact_style_conversational_issues(
        ["おつかれさま！", "お疲れ様です！", "お疲れ様です！"],
        style.StyleProfile(sample_count=12, hybrid_ratio=0.9, tame_ratio=0.0),
    )

    assert issues
    assert "候補群の会話調は本人Goldの傾向より少なめです" in issues[0]
    assert "候補はすべて丁寧な口調" not in issues[0]


def test_contact_style_repair_accepts_an_alternative_without_repeated_rest_advice():
    profile = style.StyleProfile(
        sample_count=8, keigo_ratio=0.0, hybrid_ratio=0.5, tame_ratio=0.5, laugh_ratio=0.0
    )
    previous = [
        "お仕事おつかれさま！今日はゆっくり休んでね",
        "本当におつかれさま！無理せずゆっくり寝てね",
        "お仕事大変だったね！早めに寝て身体を休めてね",
    ]
    candidate = [
        "お仕事おつかれさま！",
        "一日頑張ったね、本当にえらいよ",
        "疲れた日は無理せずゆっくり休んでね",
    ]
    context = generation._ContactStyleRepairContext(
        profile=profile,
    )
    previous_issues = generation._contact_style_soft_repair_issues(previous, context)

    assert generation._contact_style_repair_improves(
        previous, candidate, context, previous_issues
    )


def test_contact_style_repair_rejects_an_alternative_that_keeps_repeating_rest_advice():
    profile = style.StyleProfile(
        sample_count=8, keigo_ratio=0.0, hybrid_ratio=0.5, tame_ratio=0.5, laugh_ratio=0.0
    )
    previous = [
        "お仕事おつかれさま！今日はゆっくり休んでね",
        "本当におつかれさま！無理せずゆっくり寝てね",
        "お仕事大変だったね！早めに寝て身体を休めてね",
    ]
    candidate = [
        "おつかれさま！今日はちゃんと寝てね",
        "大変だったね、リラックスしてください",
        "本当におつかれ！早く寝た方がいいよ",
    ]
    context = generation._ContactStyleRepairContext(profile=profile)
    previous_issues = generation._contact_style_soft_repair_issues(previous, context)

    assert not generation._contact_style_repair_improves(
        previous, candidate, context, previous_issues
    )


@pytest.mark.parametrize(
    ("secondary_regression", "expected"), [(0.06, True), (0.11, False)]
)
def test_contact_style_repair_allows_only_bounded_cross_dimension_tradeoffs(
    monkeypatch, secondary_regression, expected
):
    context = generation._ContactStyleRepairContext(
        profile=style.StyleProfile(sample_count=8, hybrid_ratio=0.7, tame_ratio=0.3)
    )
    previous = ["お疲れ様です！"] * 3
    candidate = ["おつかれ！"] * 3
    previous_issues = [
        "同一相手Goldでは会話調の返信もよく使われていますが、候補がすべて丁寧です。",
        "同一相手Goldより今回の返信候補が短めです。",
    ]
    monkeypatch.setattr(generation, "_contact_style_soft_repair_issues", lambda *_: [])
    monkeypatch.setattr(
        generation,
        "_contact_style_repair_scores",
        lambda *_: (
            {"conversational_register": 0.8, "length": 0.5},
            {"conversational_register": 0.3, "length": 0.5 + secondary_regression},
        ),
    )

    assert generation._contact_style_repair_improves(
        previous, candidate, context, previous_issues
    ) is expected


def test_contact_style_repair_must_reduce_recommendation_overlap_even_if_length_improves():
    profile = style.StyleProfile(
        sample_count=8,
        char_median=40,
        keigo_ratio=0.0,
        hybrid_ratio=0.5,
        tame_ratio=0.5,
        laugh_ratio=0.0,
    )
    previous = ["疲れたね、寝てね", "おつかれ、早く寝てね", "大変だったね、休んでね"]
    longer_but_still_repetitive = [
        "仕事おつかれさま！今日はゆっくり休んでね",
        "仕事おつかれさま！今日は早く寝てね",
        "仕事おつかれさま！無理せず休んでね",
    ]
    context = generation._ContactStyleRepairContext(
        profile=profile,
        same_contact_gold_median=40,
        global_gold_median=40,
    )
    previous_issues = generation._contact_style_soft_repair_issues(previous, context)

    assert any("複数案が休息・睡眠" in issue for issue in previous_issues)
    assert any("今回の返信候補が短め" in issue for issue in previous_issues)
    assert not generation._contact_style_repair_improves(
        previous, longer_but_still_repetitive, context, previous_issues
    )


def test_contact_style_repair_accepts_partial_reduction_in_repeated_rest_advice():
    profile = style.StyleProfile(
        sample_count=8,
        keigo_ratio=0.0,
        hybrid_ratio=0.5,
        tame_ratio=0.5,
        laugh_ratio=0.0,
    )
    previous = [
        "お仕事おつかれさま！今日はゆっくり休んでね",
        "本当におつかれさま！無理せずゆっくり寝てね",
        "お仕事大変だったね！早めに寝て身体を休めてね",
    ]
    partial_improvement = [
        "お仕事おつかれさま！今日はゆっくり休んでね",
        "本当におつかれさま！無理せずゆっくり寝てね",
        "お仕事おつかれさま！それは大変だったね",
    ]
    context = generation._ContactStyleRepairContext(profile=profile)
    previous_issues = generation._contact_style_soft_repair_issues(previous, context)

    assert generation._contact_style_repair_improves(
        previous, partial_improvement, context, previous_issues
    )


def test_contact_style_soft_repair_does_not_enforce_fixed_register_quota():
    mixed_contact = style.StyleProfile(
        sample_count=8,
        keigo_ratio=0.2,
        hybrid_ratio=0.4,
        tame_ratio=0.4,
    )
    conversational = "カフェいいですね！落ち着くよね！"
    polite_only = "お仕事お疲れ様です。どうぞゆっくりお休みください。"

    one_conversational = _style_repair_issues(
        [conversational, polite_only, polite_only],
        mixed_contact,
        same_contact_gold_median=None,
        global_gold_median=None,
    )
    no_conversational = _style_repair_issues(
        [polite_only, polite_only, polite_only],
        mixed_contact,
        same_contact_gold_median=None,
        global_gold_median=None,
    )

    assert not any("Goldでは会話調の返信" in issue for issue in one_conversational)
    assert any("Goldでは会話調の返信" in issue for issue in no_conversational)
    assert "少なくとも" not in " ".join(no_conversational)


def test_contact_style_register_repair_uses_gold_mix_without_candidate_count():
    hybrid_dominant = style.StyleProfile(
        sample_count=8, keigo_ratio=0.17, hybrid_ratio=0.51, tame_ratio=0.32
    )
    casual_dominant = style.StyleProfile(
        sample_count=8, keigo_ratio=0.19, hybrid_ratio=0.36, tame_ratio=0.46
    )
    conversational = "それいいね！"
    polite_only = "お仕事お疲れ様です。どうぞゆっくりお休みください。"

    mixed_issues = _style_repair_issues(
        [conversational, polite_only, polite_only],
        hybrid_dominant,
        same_contact_gold_median=None,
        global_gold_median=None,
    )
    all_polite_issues = _style_repair_issues(
        [polite_only, polite_only, polite_only],
        casual_dominant,
        same_contact_gold_median=None,
        global_gold_median=None,
    )

    assert not any("Goldでは会話調の返信" in issue for issue in mixed_issues)
    assert any("Goldでは会話調の返信" in issue for issue in all_polite_issues)


def test_contact_style_repair_catches_all_polite_candidates_when_gold_is_mixed():
    profile = style.StyleProfile(
        sample_count=12, keigo_ratio=0.48, hybrid_ratio=0.41, tame_ratio=0.11
    )
    all_polite = ["お仕事お疲れ様です！"] * 3

    issues = _style_repair_issues(
        all_polite,
        profile,
        same_contact_gold_median=50,
        global_gold_median=50,
    )

    assert any("Goldの会話調" in issue for issue in issues)
    assert not any("少なくとも" in issue for issue in issues)


def test_candidate_validation_rejects_unsupported_workload_inference_but_keeps_grounded_wish():
    context = {
        "counterpart_message": "仕事で疲れた",
        "known_self_facts": [],
    }

    inferred = generation.validate_candidate_replies(
        ["そんなに疲れるまで頑張ったんですね"],
        expected_candidates=1,
        **context,
    )
    grounded_wish = generation.validate_candidate_replies(
        ["お仕事お疲れ様です！少しでもゆっくり休めるといいですね"],
        expected_candidates=1,
        **context,
    )

    assert any("相手の負荷や疲れの理由" in issue for issue in inferred)
    assert not any("相手の負荷や疲れの理由" in issue for issue in grounded_wish)


def test_workday_negated_day_off_is_grounded_but_positive_day_off_is_not_work_context():
    assert generation._has_positive_work_context("今日は仕事休みじゃないよ")
    assert not generation._has_positive_work_context("今日は仕事休み")
    assert not generation._has_positive_work_context("今日は仕事休みだった")

    violations = generation.validate_candidate_replies(
        ["お仕事大変ですね"],
        expected_candidates=1,
        counterpart_message="今日は仕事休みじゃないよ",
    )

    assert not any("仕事の状況を確認できる情報がありません" in issue for issue in violations)


def test_contact_style_repair_prioritizes_grounded_content_over_style_markers():
    guidance = generation._repair_contact_style_guidance(
        frozenset({"contact_length", "contact_conversational"}), candidates=3
    )

    assert guidance.index("事実") < guidance.index("Goldの文量分布")
    assert "時間・頻度・勤務状況・原因" in guidance
    assert "事実を保てる場合だけ" in guidance


def test_contact_style_length_repair_does_not_expand_short_direct_answers():
    context = generation._ContactStyleRepairContext(
        profile=style.StyleProfile(sample_count=8, char_median=48),
        same_contact_gold_median=48,
        global_gold_median=32,
        counterpart_message="何時に会う？",
        counterpart_intent="question",
    )

    issues = generation._contact_style_length_issues(
        ["19時で大丈夫！", "19時なら行けるよ", "19時にしよ！"], context
    )

    assert not issues


def test_contact_style_length_repair_still_catches_substantive_emotional_shares():
    context = generation._ContactStyleRepairContext(
        profile=style.StyleProfile(sample_count=8, char_median=48),
        same_contact_gold_median=48,
        global_gold_median=32,
        counterpart_message="仕事で疲れた",
        counterpart_intent="emotional_share",
    )

    issues = generation._contact_style_length_issues(
        ["おつかれ！", "大丈夫？", "ゆっくり休んでね"], context
    )

    assert any("同一相手Goldより今回の返信候補が短め" in issue for issue in issues)


@pytest.mark.parametrize("repair_result", ["failure", "http_error", "question"])
def test_contact_style_soft_repair_preserves_hard_valid_replies_after_optional_repair_fails(
    client, monkeypatch, repair_result
):
    """A style-only repair must not turn already-valid replies into a failed request."""
    cid = _seed_gold(client, "Soft repair fallback", TAME_PAIRS[:5])
    # The style mismatch itself is covered by the unit test above. This patch focuses
    # on the retry state machine: a later clean-generation call can exhaust retries.
    class FailingAfterFirstProvider:
        name = "fake"

        def __init__(self):
            self.calls = 0

        def generate(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return '{"replies":["疲れたね！","本当におつかれさま！","ゆっくり休んでね！"]}'
            if self.calls == 2 and repair_result == "question":
                return "[AI_QUESTION]どの話題への返信ですか？[/AI_QUESTION]"
            if repair_result == "http_error":
                raise generation.HTTPException(status_code=429, detail="rate limited")
            raise RuntimeError("optional repair unavailable")

        def available_models(self):
            return []

    monkeypatch.setattr(
        "app.routers.generation.factory.get_provider",
        lambda *args, **kwargs: FailingAfterFirstProvider(),
    )
    monkeypatch.setattr(
        generation,
        "get_ai_config",
        lambda: {
            "provider": "fake",
            "model": "fake-model",
            "api_key": "x",
            "temperature": 0.8,
            "max_tokens": 512,
            "history_limit": 50,
        },
    )
    monkeypatch.setattr(
        generation,
        "_contact_style_soft_repair_issues",
        lambda *_args, **_kwargs: ["soft style preference"],
    )

    response = client.post(
        "/api/generate",
        json={"contact_id": cid, "condition": "", "candidates": 3},
    )

    assert response.status_code == 200
    assert response.json()["replies"] == [
        "疲れたね！",
        "本当におつかれさま！",
        "ゆっくり休んでね！",
    ]


def test_contact_style_soft_repair_keeps_initial_replies_when_repair_does_not_improve(
    client, monkeypatch
):
    cid = _seed_gold(client, "No worse style repair", TAME_PAIRS[:5])
    initial = ["おつかれ！", "大変だったね", "無理せず休んでね"]
    repair = ["お疲れ様です", "ゆっくり休んでください", "大変でしたね"]
    response = client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "contact", "content": "仕事で疲れた"},
    )
    assert response.status_code == 201

    class NonImprovingProvider:
        name = "fake"

        def __init__(self):
            self.calls = 0

        def generate(self, **_kwargs):
            self.calls += 1
            replies = initial if self.calls == 1 else repair
            return json.dumps({"replies": replies}, ensure_ascii=False)

        def available_models(self):
            return []

    provider = NonImprovingProvider()
    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: provider)
    monkeypatch.setattr(generation, "get_ai_config", lambda: {
        "provider": "fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })
    monkeypatch.setattr(
        generation,
        "_contact_style_soft_repair_issues",
        lambda *_args, **_kwargs: ["候補全体の丁寧さが同一相手Goldと異なります"],
    )
    monkeypatch.setattr(
        generation,
        "_contact_style_repair_improves",
        lambda *_args, **_kwargs: False,
    )

    response = client.post(
        "/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3}
    )

    assert response.status_code == 200
    assert provider.calls == 2
    assert response.json()["replies"] == initial


def test_contact_style_soft_repair_runs_after_semantic_hard_repair_and_keeps_safe_baseline(
    client, monkeypatch
):
    """Real hard/style checks keep a valid repair when its optional rewrite is rejected."""
    cid = _seed_gold(client, "Hard repair style check", TAME_PAIRS)
    assert client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "contact", "content": "寿司って行ったことある？"},
    ).status_code == 201
    hard_invalid = ["行ったことあります！", "ありますよ！", "よく行きます！"]
    hard_valid_style_mismatch = [
        "お寿司いいですね！",
        "美味しそうですね！",
        "お店によって違いそうですね！",
    ]

    class HardThenStyleRepairProvider:
        name = "fake"

        def __init__(self):
            self.calls = 0

        def generate(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({"replies": hard_invalid}, ensure_ascii=False)
            # Deliberately repeat the valid-but-style-mismatched set. The real
            # quality gate should reject this no-improvement optional rewrite.
            replies = hard_valid_style_mismatch
            return json.dumps({"replies": replies}, ensure_ascii=False)

        def available_models(self):
            return []

    provider = HardThenStyleRepairProvider()
    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: provider)
    monkeypatch.setattr(generation, "get_ai_config", lambda: {
        "provider": "fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })
    original_style_check = generation._contact_style_soft_repair_issues
    style_check_results = []

    def style_check(replies, *_args, **_kwargs):
        issues = original_style_check(replies, *_args, **_kwargs)
        style_check_results.append((list(replies), issues))
        return issues

    monkeypatch.setattr(generation, "_contact_style_soft_repair_issues", style_check)
    original_repair_improves = generation._contact_style_repair_improves
    repair_decisions = []

    def repair_improves(*args, **kwargs):
        improves = original_repair_improves(*args, **kwargs)
        repair_decisions.append(improves)
        return improves

    monkeypatch.setattr(generation, "_contact_style_repair_improves", repair_improves)
    original_validator = generation.validate_candidate_replies
    hard_validation_results = []

    def validate_and_capture(*args, **kwargs):
        violations = original_validator(*args, **kwargs)
        hard_validation_results.append((list(args[0]), violations))
        return violations

    monkeypatch.setattr(generation, "validate_candidate_replies", validate_and_capture)

    response = client.post(
        "/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3}
    )

    assert response.status_code == 200
    assert any(
        replies == hard_invalid
        and any("本人の経験を確認できる情報がありません" in issue for issue in issues)
        for replies, issues in hard_validation_results
    )
    assert provider.calls == 3  # initial, hard repair, optional style follow-up
    assert any(
        replies == hard_valid_style_mismatch and issues
        for replies, issues in style_check_results
    )
    assert repair_decisions and repair_decisions[-1] is False
    final_replies = response.json()["replies"]
    assert set(final_replies) == set(hard_valid_style_mismatch)
    assert not set(final_replies).intersection(hard_invalid)


def test_contact_style_check_runs_on_first_valid_candidate_after_hard_retry(
    client, monkeypatch
):
    """The style check is not limited to the first generation attempt."""
    cid = _seed_gold(client, "Style check after hard retry", TAME_PAIRS)
    assert client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "contact", "content": "寿司って行ったことある？"},
    ).status_code == 201
    hard_invalid = ["行ったことあります！", "ありますよ！", "よく行きます！"]
    hard_valid_style_mismatch = [
        "お寿司いいですね！",
        "美味しそうですね！",
        "お店によって違いそうですね！",
    ]

    class RetryThenStyleProvider:
        name = "fake"

        def __init__(self):
            self.calls = 0

        def generate(self, **_kwargs):
            self.calls += 1
            replies = (
                hard_invalid
                if self.calls <= 2
                else hard_valid_style_mismatch
            )
            return json.dumps({"replies": replies}, ensure_ascii=False)

        def available_models(self):
            return []

    provider = RetryThenStyleProvider()
    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: provider)
    monkeypatch.setattr(generation, "get_ai_config", lambda: {
        "provider": "fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })

    original_style_check = generation._contact_style_soft_repair_issues
    style_check_results = []

    def style_check(replies, *_args, **_kwargs):
        issues = original_style_check(replies, *_args, **_kwargs)
        style_check_results.append((list(replies), issues))
        return issues

    monkeypatch.setattr(generation, "_contact_style_soft_repair_issues", style_check)
    original_repair_improves = generation._contact_style_repair_improves
    repair_decisions = []

    def repair_improves(*args, **kwargs):
        improves = original_repair_improves(*args, **kwargs)
        repair_decisions.append(improves)
        return improves

    monkeypatch.setattr(generation, "_contact_style_repair_improves", repair_improves)
    original_validator = generation.validate_candidate_replies
    hard_validation_results = []

    def validate_and_capture(*args, **kwargs):
        violations = original_validator(*args, **kwargs)
        hard_validation_results.append((list(args[0]), violations))
        return violations

    monkeypatch.setattr(generation, "validate_candidate_replies", validate_and_capture)

    response = client.post(
        "/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3}
    )

    assert response.status_code == 200
    assert provider.calls == 4  # two rejected attempts, valid attempt, optional style pass
    assert sum(
        replies == hard_invalid
        and any("本人の経験を確認できる情報がありません" in issue for issue in issues)
        for replies, issues in hard_validation_results
    ) >= 2
    assert any(
        replies == hard_valid_style_mismatch and issues
        for replies, issues in style_check_results
    )
    assert repair_decisions and repair_decisions[-1] is False
    final_replies = response.json()["replies"]
    assert set(final_replies) == set(hard_valid_style_mismatch)
    assert not set(final_replies).intersection(hard_invalid)


def test_contact_style_checks_new_valid_candidate_after_style_repair_fails_hard_validation(
    client, monkeypatch
):
    """A later valid candidate gets checked even after a style repair was attempted."""
    cid = _seed_gold(client, "Style check after failed soft repair", TAME_PAIRS)
    assert client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "contact", "content": "寿司って行ったことある？"},
    ).status_code == 201
    first_style_mismatch = [
        "お寿司いいですね！",
        "美味しそうですね！",
        "お店によって違いそうですね！",
    ]
    hard_invalid = ["行ったことあります！", "ありますよ！", "よく行きます！"]
    later_style_mismatch = [
        "お寿司はいいですね！",
        "美味しそうです！",
        "お店によって違うんですね！",
    ]

    class ProviderWithFailedStyleRepair:
        name = "fake"

        def __init__(self):
            self.calls = 0

        def generate(self, **_kwargs):
            self.calls += 1
            replies = (
                first_style_mismatch
                if self.calls == 1
                else hard_invalid
                if self.calls == 2
                else later_style_mismatch
            )
            return json.dumps({"replies": replies}, ensure_ascii=False)

        def available_models(self):
            return []

    provider = ProviderWithFailedStyleRepair()
    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: provider)
    monkeypatch.setattr(generation, "get_ai_config", lambda: {
        "provider": "fake", "model": "fake-model", "api_key": "x",
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    })
    original_style_check = generation._contact_style_soft_repair_issues
    style_check_results = []

    def style_check(replies, *_args, **_kwargs):
        issues = original_style_check(replies, *_args, **_kwargs)
        style_check_results.append((list(replies), issues))
        return issues

    monkeypatch.setattr(generation, "_contact_style_soft_repair_issues", style_check)
    original_validator = generation.validate_candidate_replies
    hard_validation_results = []

    def validate_and_capture(*args, **kwargs):
        violations = original_validator(*args, **kwargs)
        hard_validation_results.append((list(args[0]), violations))
        return violations

    monkeypatch.setattr(generation, "validate_candidate_replies", validate_and_capture)

    response = client.post(
        "/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3}
    )

    assert response.status_code == 200
    assert provider.calls == 3
    assert any(
        replies == first_style_mismatch and issues
        for replies, issues in style_check_results
    )
    assert any(
        replies == later_style_mismatch and issues
        for replies, issues in style_check_results
    )
    assert any(
        replies == hard_invalid
        and any("本人の経験を確認できる情報がありません" in issue for issue in issues)
        for replies, issues in hard_validation_results
    )
    assert set(response.json()["replies"]) == set(later_style_mismatch)
    assert not set(response.json()["replies"]).intersection(hard_invalid)


def test_contact_style_soft_repair_rechecks_and_retries_remaining_style_mismatch(
    client, monkeypatch
):
    cid = _seed_gold(client, "Style retry", TAME_PAIRS)

    class ThreeStageProvider:
        name = "fake"

        def __init__(self):
            self.calls = 0

        def generate(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return '{"replies":["駅前のカフェいいな！","パンケーキ美味しそう！","ゆっくりできてよかったね！"]}'
            if self.calls == 2:
                return '{"replies":["パンケーキいいな笑","カフェ楽しそう！","ゆっくりできてよかったね！"]}'
            return '{"replies":["おつかれ笑","それいいな！","いいね笑"]}'

        def available_models(self):
            return []

    provider = ThreeStageProvider()
    monkeypatch.setattr(
        "app.routers.generation.factory.get_provider",
        lambda *args, **kwargs: provider,
    )
    monkeypatch.setattr(
        generation,
        "get_ai_config",
        lambda: {
            "provider": "fake",
            "model": "fake-model",
            "api_key": "x",
            "temperature": 0.8,
            "max_tokens": 512,
            "history_limit": 50,
        },
    )
    resolved_style_replies = ["おつかれ笑", "それいいな！", "いいね笑"]

    def fake_style_check(replies, *_args, **_kwargs):
        return (
            []
            if replies == resolved_style_replies
            else ["同一相手Goldの笑い傾向と候補群の差"]
        )

    monkeypatch.setattr(generation, "_contact_style_soft_repair_issues", fake_style_check)
    monkeypatch.setattr(
        generation,
        "_contact_style_repair_improves",
        lambda *_args, **_kwargs: True,
    )

    response = client.post(
        "/api/generate",
        json={"contact_id": cid, "condition": "", "candidates": 3},
    )

    assert response.status_code == 200
    assert provider.calls == 3
    assert set(response.json()["replies"]) == {
        "おつかれ笑",
        "それいいな！",
        "いいね笑",
    }


def test_contact_style_soft_repair_uses_true_median_for_even_candidate_count():
    mixed_contact = style.StyleProfile(
        sample_count=8,
        char_median=80,
        keigo_ratio=0.2,
        hybrid_ratio=0.4,
        tame_ratio=0.4,
    )

    issues = _style_repair_issues(
        ["短い返信です", "これは少し長めの返信ですが基準より短いです"],
        mixed_contact,
        same_contact_gold_median=80,
        global_gold_median=50,
    )

    assert any("今回の返信候補が短め" in issue for issue in issues)


def test_contact_style_soft_repair_uses_small_reliable_length_delta_without_fixed_chars():
    profile = style.StyleProfile(sample_count=8, char_median=60)

    issues = _style_repair_issues(
        ["いいですね！", "そうなんですね！", "素敵ですね！"],
        profile,
        same_contact_gold_median=60,
        global_gold_median=55,
    )

    length_issue = next(issue for issue in issues if "今回の返信候補が短め" in issue)
    assert "Goldの文量分布も参考に、今回の話題に必要な内容を返してください" in length_issue
    assert "少なくとも2案" not in length_issue
    assert "固定の文字数" in length_issue


def test_contact_style_length_repair_accepts_one_naturally_fuller_candidate():
    profile = style.StyleProfile(
        sample_count=8,
        char_median=40,
        laugh_ratio=0.0,
        keigo_ratio=1.0,
        hybrid_ratio=0.0,
        tame_ratio=0.0,
    )
    previous = ["おつかれ！", "大変だったね", "ゆっくり休んでね"]
    candidate = [
        "おつかれ！",
        "大変だったね",
        "仕事で疲れるとしんどいよね！今日は少しでも気持ちが軽くなるといいね",
    ]
    context = generation._ContactStyleRepairContext(
        profile=profile,
        same_contact_gold_median=40,
        global_gold_median=40,
    )
    previous_issues = generation._contact_style_soft_repair_issues(previous, context)

    assert any("Goldの文量分布も参考に" in issue for issue in previous_issues)
    assert generation._contact_style_repair_improves(
        previous, candidate, context, previous_issues
    )


def test_contact_style_soft_repair_catches_moderately_short_replies_against_gold():
    profile = style.StyleProfile(sample_count=8, char_median=60)

    issues = _style_repair_issues(
        [
            "今日はのんびり過ごせそうですね！おいしいものも楽しめたならよかったです！",
            "落ち着いたカフェでゆっくりできて、いい時間を過ごせたみたいでよかったです！",
            "季節限定のパンケーキも雰囲気のいいカフェも、楽しめたようでなによりです！",
        ],
        profile,
        same_contact_gold_median=60,
        global_gold_median=50,
    )

    assert any("Goldより今回の返信候補が短め" in issue for issue in issues)
    assert any("Goldの文量分布も参考に、今回の話題に必要な内容を返してください" in issue for issue in issues)


def test_contact_style_soft_repair_preserves_gold_polite_register_in_mixed_profile():
    profile = style.StyleProfile(
        sample_count=6, keigo_ratio=0.7, hybrid_ratio=0.2, tame_ratio=0.1
    )

    issues = _style_repair_issues(
        ["いいなー！最高だよね", "それ楽しそう！いいね", "行ってみたいなぁ"],
        profile,
        same_contact_gold_median=75,
        global_gold_median=50,
    )

    assert any("候補全体の丁寧さ" in issue for issue in issues)
    assert not any("丁寧な語尾に" in issue for issue in issues)


def test_contact_style_tone_classifier_matches_gold_for_mixed_clause_replies():
    profile = style.StyleProfile(
        sample_count=8, keigo_ratio=0.2, hybrid_ratio=0.6, tame_ratio=0.2
    )
    mixed_reply = "カフェいいですね！落ち着くよね！"

    assert style._classify_tone_exclusive(mixed_reply) == "hybrid"
    assert generation._contact_style_tone_ratios([mixed_reply] * 3) == (0.0, 1.0)
    issues = _style_repair_issues(
        [mixed_reply] * 3,
        profile,
        same_contact_gold_median=None,
        global_gold_median=None,
    )
    assert not any(
        marker in issue for issue in issues for marker in ("丁寧さ", "Goldの会話調")
    )

    polite_imperative = "ゆっくり休んでくださいね"
    assert style._classify_tone_exclusive(polite_imperative) == "keigo"
    assert generation._contact_style_tone_ratios([polite_imperative]) == (1.0, 0.0)
    assert style._classify_tone_exclusive("おつかれ笑") == "hybrid"


def test_explicit_tone_does_not_suppress_contact_laugh_style():
    profile = style.StyleProfile(
        sample_count=8, keigo_ratio=0.7, hybrid_ratio=0.2, tame_ratio=0.1,
        laugh_ratio=0.6,
    )
    issues = _style_repair_issues(
        ["お疲れ様です", "大変でしたね", "ゆっくりしてください"],
        profile,
        same_contact_gold_median=None,
        global_gold_median=None,
        explicit_tone="keigo",
    )
    assert any("『笑』や『w』" in issue for issue in issues)
    assert not any("丁寧さ" in issue or "会話調の返信" in issue for issue in issues)


def test_contact_style_soft_repair_matches_contact_laugh_frequency_without_overdoing_it():
    assert style.compute_style_metrics(["weekend", "wonderful", "work"]).laugh_ratio == 0
    assert style.compute_style_metrics(["いいねw", "最高WWW", "楽しい笑"]).laugh_ratio == 1

    frequent_laughs = style.StyleProfile(sample_count=8, laugh_ratio=0.68)
    no_laugh_replies = _style_repair_issues(
        ["パンケーキ美味しそう！", "ゆっくりできてよかったね！", "いい時間だったね！"],
        frequent_laughs,
        same_contact_gold_median=None,
        global_gold_median=None,
    )
    assert any("『笑』や『w』" in issue and "候補数を整える目的" in issue for issue in no_laugh_replies)

    moderate_laughs = style.StyleProfile(sample_count=8, laugh_ratio=0.66)
    all_laugh_replies = _style_repair_issues(
        ["いいですね笑", "楽しそうですね笑", "よかったですね笑"],
        moderate_laughs,
        same_contact_gold_median=None,
        global_gold_median=None,
    )
    assert any("候補すべてにあります" in issue and "候補数を整えるため" in issue for issue in all_laugh_replies)

    no_laugh_gold = style.StyleProfile(sample_count=8, laugh_ratio=0.0)
    emoji_only_replies = _style_repair_issues(
        ["いいですね😊", "すてきですね✨", "よかったです💕"],
        no_laugh_gold,
        same_contact_gold_median=None,
        global_gold_median=None,
    )
    assert not any("『笑』や『w』" in issue for issue in emoji_only_replies)

    emoji_instead_of_laugh = _style_repair_issues(
        ["いいですね😊", "すてきですね✨", "よかったです💕"],
        moderate_laughs,
        same_contact_gold_median=None,
        global_gold_median=None,
    )
    assert any("『笑』や『w』を含む返信" in issue for issue in emoji_instead_of_laugh)

    no_laughs = _style_repair_issues(
        ["weekend cafe sounds nice", "wonderful atmosphere", "work sounds busy"],
        no_laugh_gold,
        same_contact_gold_median=None,
        global_gold_median=None,
    )
    assert not any("『笑』や『w』" in issue for issue in no_laughs)


def test_contact_length_repair_strength_tracks_relative_gold_gap():
    profile = style.StyleProfile(sample_count=8, laugh_ratio=0.5)
    short_replies = ["いいですね！", "よかったですね！", "楽しそうですね！"]

    strongly_long_contact = _style_repair_issues(
        short_replies,
        profile,
        same_contact_gold_median=75,
        global_gold_median=50,
    )
    moderately_long_contact = _style_repair_issues(
        ["x" * 42, "y" * 42, "z" * 42],
        profile,
        same_contact_gold_median=64,
        global_gold_median=50,
    )

    strong_length_issue = next(issue for issue in strongly_long_contact if "Goldより今回の返信候補が短め" in issue)
    moderate_length_issue = next(issue for issue in moderately_long_contact if "Goldより今回の返信候補が短め" in issue)
    assert "候補群と相手別Goldの文量差が大きく" in strong_length_issue
    assert "Goldの文量分布も参考に、今回の話題に必要な内容を返してください" in strong_length_issue
    assert "少なくとも2案" not in strong_length_issue
    assert "Goldの文量分布も参考に" in moderate_length_issue


def test_contact_style_retry_requires_issue_category_to_improve_not_only_count():
    initial = generation._contact_style_issue_categories(
        [
            "同一相手Goldの笑い傾向と候補群の差",
            "同一相手Goldより今回の返信候補が短めです。",
        ]
    )
    resolved_laugh_but_same_count = generation._contact_style_issue_categories(
        [
            "同一相手Goldより今回の返信候補が短めです。",
            "同一相手Goldでは会話調の返信も十分に使われています。",
        ]
    )
    fewer_issues = generation._contact_style_issue_categories(
        ["同一相手Goldより今回の返信候補が短めです。"]
    )

    assert not resolved_laugh_but_same_count < initial
    assert fewer_issues < initial


def test_contact_style_followup_accepts_partial_metric_improvement_without_tradeoff():
    profile = style.StyleProfile(sample_count=8, laugh_ratio=0.67)
    previous = ["パンケーキいいですね！", "雰囲気いいですね！", "楽しそうですね！"]
    improved = [
        "パンケーキ美味しそうですね！季節限定って特別感があっていいですね笑",
        "落ち着いた雰囲気のお店って、友達とゆっくり過ごすのによさそうですね笑",
        "久しぶりにゆっくりできてよかったですね！",
    ]
    issues = _style_repair_issues(
        previous,
        profile,
        same_contact_gold_median=75,
        global_gold_median=50,
    )

    assert _style_repair_improves(
        previous,
        improved,
        profile,
        same_contact_gold_median=75,
        global_gold_median=50,
        previous_issues=issues,
    )


def test_contact_style_followup_rejects_a_new_style_regression():
    profile = style.StyleProfile(
        sample_count=8,
        laugh_ratio=0.67,
        keigo_ratio=0.2,
    )
    previous = ["パンケーキいいですね！笑", "雰囲気いいですね！", "楽しそうですね！"]
    regressed = [
        "パンケーキ美味しそうですね！季節限定って特別感ありますね！",
        "落ち着いた雰囲気のお店って、ゆっくり過ごすのによさそうですね！",
        "久しぶりにゆっくりできてよかったね！",
    ]
    issues = _style_repair_issues(
        previous,
        profile,
        same_contact_gold_median=75,
        global_gold_median=50,
    )

    assert not _style_repair_improves(
        previous,
        regressed,
        profile,
        same_contact_gold_median=75,
        global_gold_median=50,
        previous_issues=issues,
    )


def test_contact_style_ratio_improvement_cannot_override_naturalness_regression(monkeypatch):
    profile = style.StyleProfile(
        sample_count=8, keigo_ratio=0.2, hybrid_ratio=0.8, tame_ratio=0.0
    )
    previous = ["お疲れ様です"] * 3
    candidate = ["おつかれ"] * 3
    context = generation._ContactStyleRepairContext(
        profile=profile, counterpart_message="カフェ行ってきた"
    )
    previous_issues = generation._contact_style_soft_repair_issues(previous, context)

    assert previous_issues
    assert generation._contact_style_conversational_score_pair(
        previous, candidate, profile
    )[1] < generation._contact_style_conversational_score_pair(
        previous, candidate, profile
    )[0]
    monkeypatch.setattr(
        generation.naturalness,
        "evaluate_candidate_naturalness",
        lambda reply, **_kwargs: {"score": 0.8 if reply in previous else 0.4},
    )
    assert not generation._contact_style_repair_improves(
        previous, candidate, context, previous_issues
    )


def test_contact_style_length_repair_accepts_material_gain_with_tiny_naturalness_delta(monkeypatch):
    profile = style.StyleProfile(
        sample_count=8,
        char_median=40,
        laugh_ratio=0.0,
        keigo_ratio=1.0,
        hybrid_ratio=0.0,
        tame_ratio=0.0,
    )
    previous = ["お仕事お疲れ様です！", "大変ですね！", "お疲れ様です！"]
    candidate = [
        "お仕事お疲れ様です！",
        "大変でしたね！今日はゆっくり休めるといいですね！",
        "お疲れ様です！少しでも気持ちが楽になるといいですね！",
    ]
    context = generation._ContactStyleRepairContext(
        profile=profile,
        same_contact_gold_median=40,
        global_gold_median=40,
        counterpart_message="仕事で疲れた",
    )
    previous_issues = generation._contact_style_soft_repair_issues(previous, context)
    assert any("返信候補が短め" in issue for issue in previous_issues)

    monkeypatch.setattr(
        generation.naturalness,
        "evaluate_candidate_naturalness",
        lambda reply, **_kwargs: {
            "score": 0.8 if reply in previous else 0.78,
        },
    )

    assert generation._contact_style_repair_improves(
        previous, candidate, context, previous_issues
    )


def test_contact_length_repair_prompt_resolves_substantive_status_share_against_brevity():
    guidance = generation._repair_length_guidance(
        frozenset({"contact_length"}), candidates=3
    )

    assert "内容のある状態共有を入力の短さだけで一言の労いに縮めず" in guidance
    assert "内容に沿う短い回答・反応が自然に成立するなら短文を保ってください" in guidance
    assert "質問や説明による水増しは避けてください" in guidance
    assert "短くするのは挨拶" not in guidance


def test_contact_style_repair_rejects_worse_single_reply_even_if_mean_improves(monkeypatch):
    profile = style.StyleProfile(
        sample_count=8, keigo_ratio=0.2, hybrid_ratio=0.8, tame_ratio=0.0
    )
    previous = ["お疲れ様です"] * 3
    candidate = ["おつかれ！", "いいね！", "無関係な質問ですか？"]
    context = generation._ContactStyleRepairContext(
        profile=profile, counterpart_message="カフェ行ってきた"
    )
    previous_issues = generation._contact_style_soft_repair_issues(previous, context)
    scores = {"お疲れ様です": 0.6, "おつかれ！": 0.9, "いいね！": 0.9, "無関係な質問ですか？": 0.5}
    monkeypatch.setattr(
        generation.naturalness,
        "evaluate_candidate_naturalness",
        lambda reply, **_kwargs: {"score": scores[reply]},
    )

    assert generation._contact_style_naturalness_score(candidate, "カフェ行ってきた") > (
        generation._contact_style_naturalness_score(previous, "カフェ行ってきた")
    )
    assert not generation._contact_style_repair_improves(
        previous, candidate, context, previous_issues
    )


@pytest.mark.parametrize("candidate_length", [120, 1600])
def test_contact_style_followup_rejects_extreme_overlength_as_improvement(candidate_length):
    profile = style.StyleProfile(sample_count=8, laugh_ratio=0.67)
    previous = ["パンケーキいいですね！", "雰囲気いいですね！", "楽しそうですね！"]
    extreme = ["x" * candidate_length] * 3
    issues = _style_repair_issues(
        previous,
        profile,
        same_contact_gold_median=75,
        global_gold_median=50,
    )

    assert not _style_repair_improves(
        previous,
        extreme,
        profile,
        same_contact_gold_median=75,
        global_gold_median=50,
        previous_issues=issues,
    )


def test_contact_style_soft_repair_detects_counterpart_summary_echo():
    incoming = (
        "この前、友達と駅前のカフェに行って、季節限定のパンケーキを食べたよ！"
        "雰囲気も落ち着いてて、久しぶりにゆっくりできた笑"
    )
    issues = _style_repair_issues(
        [
            "駅前のカフェなんだね！パンケーキ食べてゆっくり過ごせたんだね",
            "パンケーキ美味しそう！",
            "季節限定っていいね！",
        ],
        style.StyleProfile(sample_count=8),
        same_contact_gold_median=None,
        global_gold_median=None,
        counterpart_message=incoming,
    )

    assert any("相手の発言を要約・言い換え" in issue for issue in issues)

    repair = generation._build_repair_messages(
        [{"role": "user", "content": incoming}],
        '{"replies":["駅前のカフェなんだね！","パンケーキ美味しそう！","季節限定っていいね！"]}',
        ["候補に相手の発言を要約・言い換えただけの部分があります。"],
        3,
    )
    assert "相手別スタイル修正" in repair[-1]["content"]
    assert "相手の発言を要約・言い換えただけにせず" in repair[-1]["content"]


def test_contact_style_repair_prompt_prioritizes_contact_mismatch_without_weak_generic_preservation():
    messages = generation._build_repair_messages(
        [{"role": "user", "content": "カフェに行ってきた"}],
        '{"replies":["カフェ行ってたんですね！","カフェいいですね！","カフェいいですね！"]}',
        [
            "同一相手Goldでは会話調の返信も十分に使われています。候補のうち会話調の文全体を含む案が不足しています。",
            "同一相手Goldより今回の返信候補が短めです。この相手はGlobal Goldよりかなり長めの傾向です。",
        ],
        3,
    )
    repair_prompt = messages[-1]["content"]

    assert "相手別スタイル修正" in repair_prompt
    assert "候補群が一般的な敬語だけなら、相手別適応ができていません" in repair_prompt
    assert "今回の会話に自然になじむ本人Gold由来の距離感" in repair_prompt
    assert "今回の返信群は相手別Goldの文量分布より短いため" not in repair_prompt
    assert "Goldの文量分布は参考にとどめ" in repair_prompt
    assert "内容のある状態共有を入力の短さだけで一言の労いに縮めず" in repair_prompt
    assert "異なる2つの反応" not in repair_prompt
    assert "質問や説明を追加して長さを作らず" in repair_prompt
    assert "会話にない行動や結果を推測しない" in repair_prompt
    assert "自然に違いを作れる場合は反応の焦点を変えてもよい" in repair_prompt
    assert "問題ない部分はそのまま残し" not in repair_prompt

    length_only_messages = generation._build_repair_messages(
        [{"role": "user", "content": "仕事で疲れた"}],
        '{"replies":["おつかれさまです","ゆっくりしてくださいね","大変でしたね"]}',
        ["同一相手Goldより今回の返信候補が短めです。"],
        3,
    )
    assert "会話調が不足している場合" not in length_only_messages[-1]["content"]
    assert "少なくとも2案" not in length_only_messages[-1]["content"]
    assert "自然に違いを作れる場合は反応の焦点を変えてもよい" in length_only_messages[-1]["content"]

    laugh_only_messages = generation._build_repair_messages(
        [{"role": "user", "content": "カフェ楽しかった"}],
        '{"replies":["カフェよかったですね笑","楽しそうですね笑","いいですね笑"]}',
        ["同一相手Goldでは『笑』や『w』を含む返信も自然に使われています。"],
        3,
    )
    laugh_only_prompt = laugh_only_messages[-1]["content"]
    assert "会話調が不足している場合" not in laugh_only_prompt
    assert "少し厚く" not in laugh_only_prompt
    assert "違う自然な反応の焦点" not in laugh_only_prompt
    assert "口調・文量のずれ" not in laugh_only_prompt
    assert "質問を追加して長さを作らない" not in laugh_only_prompt

    two_candidate_messages = generation._build_repair_messages(
        [{"role": "user", "content": "カフェに行ってきた"}],
        '{"replies":["いいですね","よかったですね"]}',
        ["JSON形式が不正です"],
        2,
    )
    assert '"replies": ["案1", "案2"]' in two_candidate_messages[-1]["content"]
    assert '"案1", "案2", "案3"' not in two_candidate_messages[-1]["content"]

    two_candidate_style_messages = generation._build_repair_messages(
        [{"role": "user", "content": "カフェに行ってきた"}],
        '{"replies":["いいですね","よかったですね"]}',
        ["同一相手Goldより今回の返信候補が短めです。"],
        2,
    )
    assert "Goldの文量分布は参考にとどめ" in two_candidate_style_messages[-1]["content"]
    assert "他の2案" not in two_candidate_style_messages[-1]["content"]

    single_candidate_style_messages = generation._build_repair_messages(
        [{"role": "user", "content": "カフェに行ってきた"}],
        '{"replies":["いいですね"]}',
        ["同一相手Goldより今回の返信候補が短めです。"],
        1,
    )
    assert "Goldの文量分布は参考にとどめ" in single_candidate_style_messages[-1]["content"]
    assert "他の候補" not in single_candidate_style_messages[-1]["content"]


def test_contact_style_repair_prompt_does_not_enforce_register_quota():
    base_messages = [{"role": "user", "content": "今週は忙しかった"}]
    raw_output = '{"replies":["お疲れさまです","大変でしたね","ゆっくりしてください"]}'
    issues = ["同一相手Goldでは会話調の返信も十分に使われています。"]

    repair_prompt = generation._build_repair_messages(
        base_messages,
        raw_output,
        issues,
        3,
    )[-1]["content"]

    assert "今回の会話に自然になじむ本人Gold由来の距離感を候補群へ反映" in repair_prompt
    assert "候補数に合わせた比率固定は避け" in repair_prompt
    assert "少なくとも" not in repair_prompt


def test_generation_prompt_varies_reaction_focus_without_forcing_questions(client):
    cid = _seed_gold(client, "反応の幅", TAME_PAIRS[:5])

    ctx = generation._build_context(cid, "", "", "normal")
    system_prompt = ctx["system_prompt"]

    assert "短い入力だけを理由に機械的に一言へ縮めたり、Goldの文量へ無理に合わせたりしない" in system_prompt
    assert "短くするのは挨拶" not in system_prompt
    assert "逆質問は情報の確認や会話上の明確な目的がある場合だけ" in system_prompt
    assert "状態共有への質問も、直近の文脈に沿い" in system_prompt
    assert "（Step 18-R4 追記）" not in system_prompt


def test_normal_prompt_does_not_force_every_option_to_start_with_empathy(client):
    cid = _seed_gold(client, "Reaction opening", TAME_PAIRS[:5])
    response = client.post(
        f"/api/contacts/{cid}/messages",
        json={"sender": "contact", "content": "仕事で疲れた"},
    )
    assert response.status_code == 201

    system_prompt = generation._build_context(cid, "", "", "normal")["system_prompt"]

    assert "短い入力だけを理由に機械的に一言へ縮めたり、Goldの文量へ無理に合わせたりしない" in system_prompt
    assert "まずこのメッセージ内容に対する反応・共感から返信を始めること" not in system_prompt
    emotional_share_policy = prompt._INTENT_POLICIES["emotional_share"]
    assert "質問は曖昧さの解消や会話上の明確な意味がある場合に限り" in emotional_share_policy
    assert "明確に助言・意見を求めている場合に限る" not in emotional_share_policy
    assert "内容に沿う感想・気遣い・自然な願いなどを返すかは本人Goldと文脈から選ぶ" in emotional_share_policy
    assert "本人Goldと会話に合う反応を選ぶ" in system_prompt
    assert "3案とも短くなることも許可する" not in system_prompt
    assert "返信量は会話内容を満たす範囲で決め、本人Goldの文量は参考にとどめる" in system_prompt
    question_policy = prompt._INTENT_POLICIES["question"]
    assert "回答を先に返したうえで" in question_policy
    assert "会話上の意味があり、相手が答えやすい" in question_policy
    assert "回答の後に逆質問・確認質問を付け足さない" not in question_policy


def test_generation_prompt_does_not_force_one_sentence_for_tiredness_with_contact_gold():
    messages = prompt.build_initial_generation_messages(
        system_prompt="style system",
        chat_history_text="相手: 仕事で疲れた",
        candidates=3,
        mode="followup",
        contact_style_instruction=(
            "本人Goldはこの相手に対して長めで、敬語と会話調が混在する。"
        ),
    )
    user_instruction = messages[-1]["content"]

    assert "一文だけ返してください" not in user_instruction
    assert "確認できる内容が少ない場合も、架空の内容や無関係な質問・自己開示で水増しせず" in user_instruction
    assert "確認できる内容が少ない場合は、質問や自己開示を無理に足さず短く返してください" not in user_instruction
    assert "Goldの文量は目安であり、内容への適切な返答を優先" in user_instruction
    assert "原因や勤務状況を推測したり" in user_instruction
    assert "労いや共感を毎回の書き出しにせず" in user_instruction


def test_contact_style_soft_repair_and_ranking_use_recency_blended_gold(monkeypatch):
    style_profiles = {
        "same_contact_all_gold_profile": style.StyleProfile(
            sample_count=8,
            char_median=120,
            keigo_ratio=0.8,
            hybrid_ratio=0.1,
            tame_ratio=0.1,
        ),
        "same_contact_blended_gold_profile": style.StyleProfile(
            sample_count=8,
            char_median=40,
            keigo_ratio=0.1,
            hybrid_ratio=0.4,
            tame_ratio=0.5,
        ),
        "other_contact_gold_profile": style.StyleProfile(
            sample_count=12, char_median=50
        ),
        "same_contact_gold_samples": 8,
    }

    profile = generation._contact_style_blended_profile(style_profiles)
    same_contact_median, global_median = (
        generation._contact_style_length_medians(style_profiles)
    )

    assert profile is style_profiles["same_contact_blended_gold_profile"]
    assert same_contact_median == 40
    assert global_median == 50
    issues = _style_repair_issues(
        ["本当にお疲れ様です！"] * 3,
        profile,
        same_contact_gold_median=same_contact_median,
        global_gold_median=global_median,
    )
    assert any("Goldでは会話調の返信" in issue for issue in issues)
    assert any("同一相手Goldより今回の返信候補が短め" in issue for issue in issues)

    received_profiles = []

    def fake_contact_length_fit(_reply, _contact_id, *, profile):
        received_profiles.append(profile)
        return 0.7

    monkeypatch.setattr(
        generation.learning.contrast,
        "contact_length_fit",
        fake_contact_length_fit,
    )
    scored = [{"reply": "返信文", "final": 0.5}]
    generation._apply_contact_length_nudge(scored, profile, contact_id=1)
    assert received_profiles == [profile]


def test_single_contact_gold_does_not_replace_global_gold_style(client):
    _seed_gold(client, "全体の丁寧な相手", KEIGO_PAIRS)
    cid = _seed_gold(client, "Goldが1件の相手", TAME_PAIRS[:1])

    profile = style.compute_hierarchical_profile(cid)

    assert profile["hierarchy_tier"] == "global_manual_gold"
    assert profile["active_profile"].keigo_ratio > profile["active_profile"].tame_ratio


@pytest.mark.parametrize("gold_count", (1, 2))
def test_isolated_sparse_contact_gold_does_not_define_global_style(client, gold_count):
    cid = _seed_gold(client, "孤立した少数Gold", TAME_PAIRS[:gold_count])

    profile = style.compute_hierarchical_profile(cid)

    assert profile["hierarchy_tier"] == "sparse_manual_gold_fallback"
    assert profile["same_contact_gold_samples"] == gold_count
    assert profile["active_profile"].sample_count == 0
    assert profile["active_profile"].tame_ratio < 0.5
    assert profile["active_profile"].laugh_ratio == pytest.approx(0.4)


def test_sparse_contact_gold_is_not_replaced_by_other_contacts_silver(client):
    cid = _seed_gold(client, "少数Goldの対象", TAME_PAIRS[:1])
    other_cid = client.post(
        "/api/contacts", json={"name": "Silverのみの相手", "profile": ""}
    ).json()["id"]
    for index in range(3):
        client.post(
            f"/api/contacts/{other_cid}/messages",
            json={"sender": "contact", "content": f"連絡です {index}"},
        )
        response = client.post(
            f"/api/contacts/{other_cid}/messages",
            json={
                "sender": "self",
                "content": "こちらこそありがとうございます。よろしくお願いいたします。",
                "source": "legacy_unknown",
            },
        )
        conn = database.get_conn()
        try:
            conn.execute(
                "UPDATE messages SET source = 'generated' WHERE id = ?",
                (response.json()["id"],),
            )
            conn.commit()
        finally:
            conn.close()

    profile = style.compute_hierarchical_profile(cid)
    other_contact_silver = [
        pair
        for pair in corpus.extract_reply_pairs()
        if pair.contact_id == other_cid and pair.label == "silver"
    ]

    assert profile["hierarchy_tier"] == "sparse_manual_gold_fallback"
    assert profile["other_contact_gold_profile"].sample_count == 0
    assert len(other_contact_silver) == 3
    assert profile["active_profile"].sample_count == 0


def test_same_contact_gold_adapts_without_fully_replacing_global_gold(client):
    _seed_gold(client, "全体の丁寧な相手", KEIGO_PAIRS)
    cid = _seed_gold(client, "Goldが3件の相手", TAME_PAIRS[:3])

    profile = style.compute_hierarchical_profile(cid)
    active = profile["active_profile"]
    other_contact_gold = profile["other_contact_gold_profile"]
    contact_gold = profile["same_contact_recent_gold_profile"]

    assert profile["hierarchy_tier"] == "same_contact_recent_manual_gold"
    assert other_contact_gold.tame_ratio < active.tame_ratio < contact_gold.tame_ratio
    assert contact_gold.tame_ratio - active.tame_ratio >= 0.1


def test_same_contact_gold_is_not_counted_twice_in_global_prior(client):
    _seed_gold(client, "全体の丁寧な相手", KEIGO_PAIRS)
    cid = _seed_gold(client, "局所適応の対象", TAME_PAIRS[:3])

    profile = style.compute_hierarchical_profile(cid)

    assert profile["active_profile"].tame_ratio == pytest.approx(3 / 8, abs=0.01)


@pytest.mark.parametrize("gold_count", (1, 2, 3, 4))
def test_sparse_same_contact_gold_is_not_injected_as_priority_imitation_examples(client, gold_count):
    _seed_gold(client, "丁寧な全体Gold", KEIGO_PAIRS)
    cid = _seed_gold(client, "少数の砕けた同一相手Gold", TAME_PAIRS[:gold_count])
    _seed_accepted_generation(cid)

    ctx = generation._build_context(cid, "", "", "normal")

    assert "【SAME-CONTACT RECENT GOLD REPLIES】" not in ctx["system_prompt"]
    if gold_count >= 3:
        assert "【RECENT ACCEPTED】" in ctx["system_prompt"]
    else:
        assert "【RECENT ACCEPTED】" not in ctx["system_prompt"]
    if gold_count < 3:
        assert ctx["pieces"]["style_profile"]["hierarchy_tier"] == "global_manual_gold"
        assert ctx["pieces"]["style_profile"]["active_profile"].keigo_ratio > ctx["pieces"]["style_profile"]["active_profile"].tame_ratio


def test_accepted_replies_remain_available_when_contact_has_no_manual_gold(client):
    cid = client.post("/api/contacts", json={"name": "Goldなし採用例", "profile": ""}).json()["id"]
    _seed_accepted_generation(cid)

    ctx = generation._build_context(cid, "", "", "normal")

    assert "【RECENT ACCEPTED】" in ctx["system_prompt"]


def test_accepted_replies_do_not_override_global_gold_without_local_gold(client):
    _seed_gold(client, "Global Goldのみ", KEIGO_PAIRS)
    cid = client.post("/api/contacts", json={"name": "local Goldなし", "profile": ""}).json()["id"]
    _seed_accepted_generation(cid, "いいね笑")

    ctx = generation._build_context(cid, "", "", "normal")

    assert "【RECENT ACCEPTED】" not in ctx["system_prompt"]
    assert ctx["pieces"]["style_profile"]["gold_samples"] == len(KEIGO_PAIRS)
    assert ctx["pieces"]["style_profile"]["hierarchy_tier"] == "global_manual_gold"


def test_five_same_contact_gold_examples_can_be_injected_as_structural_references(client):
    cid = _seed_gold(client, "十分な同一相手Gold", TAME_PAIRS[:5])

    ctx = generation._build_context(cid, "", "", "normal")

    assert "【SAME-CONTACT RECENT GOLD REPLIES】" in ctx["system_prompt"]
    assert "いいなー笑" in ctx["system_prompt"]
    assert "本人の返信テンポ・構成・距離感を知る補助資料" in ctx["system_prompt"]
    assert "最優先の模倣元" not in ctx["system_prompt"]


def test_same_contact_bronze_does_not_override_global_manual_gold(client):
    _seed_gold(client, "Global Gold", KEIGO_PAIRS)
    cid = _seed_non_gold(client, "Bronzeのみの相手", TAME_PAIRS[:3])

    profile = style.compute_hierarchical_profile(cid)

    assert profile["hierarchy_tier"] == "global_manual_gold"
    assert profile["active_profile"].keigo_ratio > profile["active_profile"].tame_ratio


def test_same_contact_silver_does_not_override_any_manual_gold(client):
    cid = client.post("/api/contacts", json={"name": "GoldとSilver", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "丁寧な連絡です"})
    client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "ありがとうございます。よろしくお願いいたします。", "source": "manual",
    })
    for index in range(3):
        client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": f"今日は暇{index}"})
        client.post(f"/api/contacts/{cid}/messages", json={
            "sender": "self", "content": "いいね笑", "source": "legacy_unknown",
        })

    profile = style.compute_hierarchical_profile(cid)

    assert profile["same_contact_gold_samples"] == 1
    assert profile["hierarchy_tier"] == "sparse_manual_gold_fallback"
    assert profile["active_profile"].keigo_ratio > profile["active_profile"].tame_ratio


def test_same_contact_silver_does_not_replace_few_available_gold(client):
    cid = client.post("/api/contacts", json={"name": "少数GoldとSilver", "profile": ""}).json()["id"]
    gold_pairs = [
        ("丁寧な連絡をいただきました", "ありがとうございます。よろしくお願いいたします。"),
        ("次回もお願いします", "承知いたしました。次回もよろしくお願いいたします。"),
    ]
    for contact_msg, self_msg in gold_pairs:
        client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": contact_msg})
        client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": self_msg, "source": "manual"})
    for index in range(3):
        client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": f"今日は楽しかったね {index}"})
        response = client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "いいね笑", "source": "legacy_unknown"})
        conn = database.get_conn()
        try:
            conn.execute("UPDATE messages SET source = 'generated' WHERE id = ?", (response.json()["id"],))
            conn.commit()
        finally:
            conn.close()

    profile = style.compute_hierarchical_profile(cid)
    silver_profile = style.compute_style_metrics(["いいね笑"] * 3)

    assert profile["same_contact_gold_samples"] == 2
    assert profile["gold_samples"] < 5
    assert profile["hierarchy_tier"] != "same_contact_sent_silver"
    assert profile["active_profile"].tame_ratio < silver_profile.tame_ratio


@pytest.mark.parametrize("gold_count", (1, 2, 3, 4))
def test_same_contact_silver_does_not_override_sparse_global_manual_gold(client, gold_count):
    _seed_gold(client, "別相手の手入力Gold", KEIGO_PAIRS[:gold_count])
    cid = client.post("/api/contacts", json={"name": "GoldなしSilverあり", "profile": ""}).json()["id"]
    for index in range(3):
        client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": f"今日は楽しかったね {index}"})
        response = client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "いいね笑", "source": "legacy_unknown"})
        conn = database.get_conn()
        try:
            conn.execute("UPDATE messages SET source = 'generated' WHERE id = ?", (response.json()["id"],))
            conn.commit()
        finally:
            conn.close()

    profile = style.compute_hierarchical_profile(cid)
    silver_profile = style.compute_style_metrics(["いいね笑"] * 3)

    assert profile["same_contact_gold_samples"] == 0
    assert profile["gold_samples"] == gold_count
    assert profile["hierarchy_tier"] == "sparse_manual_gold_fallback"
    assert profile["active_profile"].keigo_ratio > profile["active_profile"].tame_ratio
    assert profile["active_profile"].tame_ratio < silver_profile.tame_ratio


def test_same_contact_silver_is_fallback_only_when_no_manual_gold_exists(client):
    cid = client.post("/api/contacts", json={"name": "GoldなしSilverあり", "profile": ""}).json()["id"]
    for index in range(3):
        client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": f"今日は楽しかったね {index}"})
        response = client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "いいね笑", "source": "legacy_unknown"})
        conn = database.get_conn()
        try:
            conn.execute("UPDATE messages SET source = 'generated' WHERE id = ?", (response.json()["id"],))
            conn.commit()
        finally:
            conn.close()

    profile = style.compute_hierarchical_profile(cid)

    assert profile["gold_samples"] == 0
    assert profile["hierarchy_tier"] == "same_contact_sent_silver"


def test_recent_gold_gradually_influences_all_contact_gold(client):
    old_keigo = [(f"以前の丁寧な発言{index}", "ありがとうございます。よろしくお願いいたします。") for index in range(10)]
    recent_tame = [(f"最近の砕けた発言{index}", "いいね笑") for index in range(5)]
    cid = _seed_gold(client, "最近傾向が変化した相手", old_keigo + recent_tame)

    profile = style.compute_hierarchical_profile(cid)
    all_gold = profile["same_contact_all_gold_profile"]
    blended_contact = profile["same_contact_blended_gold_profile"]
    recent = profile["same_contact_recent_gold_profile"]

    assert 0 < profile["contact_recency_weight"] <= 0.5
    assert all_gold.tame_ratio < blended_contact.tame_ratio < recent.tame_ratio


def test_recent_gold_emoji_preferences_blend_without_an_abrupt_switch(client):
    older_gold = [
        (f"以前の返信 {index}", "ありがとう😄")
        for index in range(7)
    ]
    recent_gold = [
        (f"最近の返信 {index}", "ありがとう🐱")
        for index in range(5)
    ]
    cid = _seed_gold(client, "最近の絵文字傾向", older_gold + recent_gold)

    profile = style.compute_hierarchical_profile(cid)

    assert profile["contact_recency_weight"] == pytest.approx(7 / 17)
    assert profile["same_contact_all_gold_profile"].frequent_emojis == ["😄", "🐱"]
    assert profile["same_contact_recent_gold_profile"].frequent_emojis == ["🐱"]
    assert profile["same_contact_blended_gold_profile"].frequent_emojis == ["🐱", "😄"]
    assert profile["active_profile"].frequent_emojis == ["🐱", "😄"]


def test_learned_policy_describes_mixed_tone_without_forcing_casual(client):
    mixed = style.compute_style_metrics([
        "ありがとうございます！",
        "よろしくお願いいたします。",
        "いいね！",
        "そうだね",
        "いいですね笑",
    ])
    profile = style.to_learned_policy_prompt({
        "active_profile": mixed,
        "hierarchy_tier": "same_contact_recent_manual_gold",
        "same_contact_gold_samples": 5,
        "contact_adaptation_weight": 0.5,
    })

    assert "丁寧な表現と砕けた表現が混在" in profile
    assert "親しみやすいカジュアル口調が中心" not in profile
    assert "Global Goldへ段階的に反映" in profile
    assert "相手の呼称は『〇〇さん』" not in profile


def test_learned_policy_does_not_override_contact_length_with_short_reply_bias():
    profile = style.to_learned_policy_prompt({
        "active_profile": style.StyleProfile(
            sample_count=6,
            char_median=109,
            char_p25=72,
            char_p75=151,
        ),
        "hierarchy_tier": "same_contact_recent_manual_gold",
        "same_contact_gold_samples": 6,
        "contact_adaptation_weight": 0.55,
    })

    assert "中央値109文字" in profile
    assert "短さを一律に優先せず" in profile
    assert "短い相槌・一言反応を優先すること" not in profile


def test_relationship_summary_preserves_reliably_longer_contact_style(client):
    _seed_gold(client, "短いGlobal", [
        (f"入力{i}", "いいね！") for i in range(6)
    ])
    long_replies = [
        (f"相手の入力{i}", "それは大変だったね！今日は無理せずゆっくり休んで、明日は少しでも楽になるといいね！"
         "落ち着いたらまた話そう！")
        for i in range(6)
    ]
    cid = _seed_gold(client, "比較的長く返す相手", long_replies)

    summary = style.build_relationship_summary(cid)

    assert "Global Goldよりかなり長めに返す傾向がある" in summary
    assert "Goldの文量分布も参考に" in summary
    assert "1案だけ" not in summary
    assert "候補間で自然な違いが作れる場合だけ" in summary
    assert "確認できない行動や結果を足さず" in summary
    assert "質問で長さを作らない" in summary
    assert "状態や気持ちの共有には短い労いだけで毎回終えず" in summary


def test_automatic_contact_tone_uses_confident_manual_gold_only(client):
    casual_id = _seed_gold(client, "砕けた相手", TAME_PAIRS)
    polite_id = _seed_gold(client, "丁寧な相手", KEIGO_PAIRS)
    mixed_pairs = [
        ("映画を見た", "映画いいですね！"),
        ("カフェへ行った", "カフェいいな、ゆっくりできそう"),
        ("新作を見つけた", "それ気になる！"),
        ("天気いいね", "ほんとだね！"),
        ("週末は休みです", "よかったですね、ゆっくりできそう"),
    ]
    mixed_id = _seed_gold(client, "混在の相手", mixed_pairs)
    few_id = _seed_gold(client, "少数Gold", TAME_PAIRS[:1])

    # Contact Gold is a soft style signal; only the explicit tone control locks register.
    assert style.infer_contact_tone(style.compute_hierarchical_profile(casual_id)) == ""
    assert style.infer_contact_tone(style.compute_hierarchical_profile(polite_id)) == ""
    assert style.infer_contact_tone(style.compute_hierarchical_profile(mixed_id)) == ""
    assert style.infer_contact_tone(style.compute_hierarchical_profile(few_id)) == ""
    assert style.infer_contact_tone(style.compute_hierarchical_profile(casual_id), requested_tone="keigo") == "keigo"
