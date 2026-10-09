"""Step 18: 相手別・関係性別の会話距離適応。

- build_relationship_summary: 観測特徴のみ（関係ラベルなし）、実績0件時は空文字
- contact_tone_fit: Gold 3件未満は中立 0.5、tame/keigo の適合を判定
- ranking への影響は軽微（±0.02）かつ無データ時は中立
"""
from __future__ import annotations

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
    ("今日暇だった", "おつかれ笑"),
    ("眠い", "わかる笑"),
    ("雨降ってきた", "ほんとそれ笑"),
    ("おつ", "おつおつ笑"),
    ("まじ", "まじか笑"),
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


def test_relationship_summary_keigo_contact(client):
    cid = _seed_gold(client, "Bさん", KEIGO_PAIRS)
    block = style.build_relationship_summary(cid)
    assert "丁寧" in block
    assert "5件" in block


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
    assert "丁寧な言い回しを軸に" in summary
    assert "少なくとも1案は自然な会話調" in summary
    assert "敬語語尾を避ける" in summary
    assert "笑や絵文字だけでは口調適応と見なさない" in summary


def test_relationship_summary_distinguishes_hybrid_and_more_casual_gold(client):
    hybrid_pairs = [
        ("映画見てきた", "映画いいですね！笑"),
        ("カフェ行った", "カフェいいですね！笑"),
        ("スイーツ食べた", "おいしそうですね！笑"),
        ("天気いいね", "過ごしやすそうですね！笑"),
        ("週末はゆっくりできそう", "よかったですね！ゆっくりできそう笑"),
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

    assert "丁寧な言い回しを軸に" in hybrid_summary
    assert "砕けた会話調がやや多い" in casual_summary


def test_relationship_summary_uses_softer_guidance_below_mixed_tone_threshold(client):
    mostly_polite_pairs = KEIGO_PAIRS[:3] + TAME_PAIRS[:3]
    cid = _seed_gold(client, "丁寧寄りの混合Gold", mostly_polite_pairs)

    summary = style.build_relationship_summary(cid)

    assert "丁寧さと砕け具合が混在" in summary
    assert "3案の少なくとも1案に" in summary
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

    assert ctx["effective_tone"] == "keigo"
    assert "明示トーン指定" not in ctx["system_prompt"]


def test_relationship_summary_uses_global_fallback_for_fewer_than_three_gold(client):
    cid = _seed_gold(client, "Cさん", TAME_PAIRS[:2])
    assert style.build_relationship_summary(cid) == ""


def test_relationship_summary_describes_contact_relative_message_length(client):
    _seed_gold(client, "短文のGlobal Gold", TAME_PAIRS)
    longer_pairs = [
        (f"話題{i}", "それは本当に大変でしたね！" + "今日は無理せずゆっくり休んでくださいね" * 2)
        for i in range(6)
    ]
    cid = _seed_gold(client, "長めの同一相手Gold", longer_pairs)

    summary = style.build_relationship_summary(cid)

    assert "Global Goldより相対的に長め" in summary
    assert "同一相手Goldの文量中央値" in summary
    assert "候補が3案の場合は、少なくとも2案に" in summary
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

    assert "3案なら笑い表現を2案程度に" in summary
    assert "かなり長め" in summary
    assert "候補が3案の場合は、少なくとも2案に" in summary
    assert "候補ごとに異なる反応の焦点" in summary


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


def test_initial_generation_repeats_same_contact_guidance_after_generic_short_reply_rule():
    relationship_summary = (
        "この相手のGoldはGlobalより長め。3案のうち1案は共感に具体的な反応を添える。"
    )

    messages = prompt.build_initial_generation_messages(
        system_prompt="contact style is available in system prompt",
        contact_style_instruction=relationship_summary,
    )

    user_instruction = messages[1]["content"]
    assert "会話にない相手の行動" in user_instruction
    assert "本人の好み・経験" in user_instruction
    assert "この相手に対する本人Goldの口調・文量を優先" in user_instruction
    assert "短文の一般方針だけで全案を短くしない" in user_instruction
    assert "1案だけを他の候補より自然に少し厚く" in user_instruction
    assert "固定文字数には合わせず" in user_instruction
    assert user_instruction.index(relationship_summary) > user_instruction.index(
        "相手の発言が短い場合は短い返信"
    )

    long_relationship_summary = "この相手はGlobal Goldよりかなり長めに返す傾向がある。"
    long_messages = prompt.build_initial_generation_messages(
        system_prompt="contact style is available in system prompt",
        contact_style_instruction=long_relationship_summary,
    )
    long_user_instruction = long_messages[1]["content"]
    assert "3案中少なくとも2案" in long_user_instruction
    assert "1案だけを他の2案より" not in long_user_instruction


def test_contact_style_soft_repair_detects_large_gold_mismatch_but_honors_explicit_tone():
    mixed_contact = style.StyleProfile(
        sample_count=8,
        char_median=70,
        keigo_ratio=0.2,
        hybrid_ratio=0.4,
        tame_ratio=0.4,
    )
    overly_similar_polite_replies = [
        "今週ずっとお仕事だったんですね！本当にお疲れ様です！",
        "今週はお仕事お疲れ様です！ゆっくり休んでくださいね！",
        "お仕事大変でしたね！週末はゆっくり過ごしてくださいね！",
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
    assert any("少し厚み" in issue for issue in issues)
    length_issue = next(issue for issue in issues if "少し厚み" in issue)
    assert "会話で確認できる事実" in length_issue
    assert "確認できない具体的な行動を補わない" in length_issue
    assert explicit_tone_issues == []


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


def test_contact_style_soft_repair_requires_two_conversational_options_for_mixed_gold():
    mixed_contact = style.StyleProfile(
        sample_count=8,
        keigo_ratio=0.2,
        hybrid_ratio=0.4,
        tame_ratio=0.4,
    )
    conversational = "カフェいいですね！落ち着くよね！"
    polite_only = "お仕事お疲れ様です！ゆっくり休んでくださいね！"

    two_conversational = _style_repair_issues(
        [conversational, conversational, polite_only],
        mixed_contact,
        same_contact_gold_median=None,
        global_gold_median=None,
    )
    one_conversational = _style_repair_issues(
        [conversational, polite_only, polite_only],
        mixed_contact,
        same_contact_gold_median=None,
        global_gold_median=None,
    )

    assert not any("Goldでは会話調の返信" in issue for issue in two_conversational)
    assert any("Goldでは会話調の返信" in issue for issue in one_conversational)


def test_contact_style_register_repair_count_tracks_hybrid_vs_casual_gold():
    hybrid_dominant = style.StyleProfile(
        sample_count=8, keigo_ratio=0.17, hybrid_ratio=0.51, tame_ratio=0.32
    )
    casual_dominant = style.StyleProfile(
        sample_count=8, keigo_ratio=0.19, hybrid_ratio=0.36, tame_ratio=0.46
    )
    conversational = "それいいね！"
    polite_only = "お仕事お疲れ様です！ゆっくり休んでくださいね！"

    hybrid_issues = _style_repair_issues(
        [conversational, polite_only, polite_only],
        hybrid_dominant,
        same_contact_gold_median=None,
        global_gold_median=None,
    )
    casual_issues = _style_repair_issues(
        [conversational, polite_only, polite_only],
        casual_dominant,
        same_contact_gold_median=None,
        global_gold_median=None,
    )

    assert not any("Goldでは会話調の返信" in issue for issue in hybrid_issues)
    assert any("Goldでは会話調の返信" in issue for issue in casual_issues)


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
                return '{"replies":["今日は疲れたね！","本当におつかれさま！","ゆっくり休んでね！"]}'
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
        "今日は疲れたね！",
        "本当におつかれさま！",
        "ゆっくり休んでね！",
    ]


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
                return '{"replies":["パンケーキいいな！","カフェ楽しそう！","ゆっくりできてよかったね！"]}'
            return '{"replies":["パンケーキいいな笑","カフェでゆっくりできて最高だね笑","季節限定のパンケーキ気になる！"]}'

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
    style_checks = 0

    def fake_style_check(replies, *_args, **_kwargs):
        nonlocal style_checks
        style_checks += 1
        return ["同一相手Goldの笑い傾向と候補群の差"] if style_checks < 3 else []

    monkeypatch.setattr(generation, "_contact_style_soft_repair_issues", fake_style_check)

    response = client.post(
        "/api/generate",
        json={"contact_id": cid, "condition": "", "candidates": 3},
    )

    assert response.status_code == 200
    assert provider.calls == 3
    assert set(response.json()["replies"]) == {
        "パンケーキいいな笑",
        "カフェでゆっくりできて最高だね笑",
        "季節限定のパンケーキ気になる！",
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

    assert any("少し厚み" in issue for issue in issues)


def test_contact_style_soft_repair_uses_small_reliable_length_delta_without_fixed_chars():
    profile = style.StyleProfile(sample_count=8, char_median=60)

    issues = _style_repair_issues(
        ["いいですね！", "そうなんですね！", "素敵ですね！"],
        profile,
        same_contact_gold_median=60,
        global_gold_median=55,
    )

    length_issue = next(issue for issue in issues if "少し厚み" in issue)
    assert "少なくとも2案で" in length_issue
    assert "固定の文字数" in length_issue


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
    assert any("少なくとも2案で" in issue for issue in issues)


def test_contact_style_soft_repair_preserves_gold_polite_register_in_mixed_profile():
    profile = style.StyleProfile(
        sample_count=6, keigo_ratio=0.17, hybrid_ratio=0.51, tame_ratio=0.32
    )

    issues = _style_repair_issues(
        ["いいなー！最高だよね", "それ楽しそう！いいね", "行ってみたいなぁ"],
        profile,
        same_contact_gold_median=75,
        global_gold_median=50,
    )

    assert any("丁寧な口調も一定数" in issue for issue in issues)


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
    assert any("『笑』や『w』" in issue and "3案中2案程度" in issue for issue in no_laugh_replies)

    moderate_laughs = style.StyleProfile(sample_count=8, laugh_ratio=0.66)
    all_laugh_replies = _style_repair_issues(
        ["いいですね笑", "楽しそうですね笑", "よかったですね笑"],
        moderate_laughs,
        same_contact_gold_median=None,
        global_gold_median=None,
    )
    assert any("候補すべてにあります" in issue and "3案中2案程度" in issue for issue in all_laugh_replies)

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
    assert "少なくとも2案" in strong_length_issue
    assert "話題への反応と、それに直接つながる別の感想・共感" in strong_length_issue
    assert "相手別Goldに近い文量" in moderate_length_issue


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
    previous = ["パンケーキいいですね！", "雰囲気いいですね！", "楽しそうですね！"]
    regressed = [
        "パンケーキ美味しそうだね！季節限定って特別感あっていいね笑",
        "落ち着いた雰囲気のお店って、友達とゆっくり過ごすのによさそうだね笑",
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
    assert "少なくとも2案" in repair_prompt
    assert "異なる2つの反応" in repair_prompt
    assert "異なる2つの反応" in repair_prompt
    assert "質問や説明を追加して長さを作らず" in repair_prompt
    assert "会話にない行動や結果を推測しない" in repair_prompt
    assert "違う自然な反応の焦点" in repair_prompt
    assert "問題ない部分はそのまま残し" not in repair_prompt

    length_only_messages = generation._build_repair_messages(
        [{"role": "user", "content": "仕事で疲れた"}],
        '{"replies":["おつかれさまです","ゆっくりしてくださいね","大変でしたね"]}',
        ["同一相手Goldより今回の返信候補が短めです。"],
        3,
    )
    assert "会話調が不足している場合" not in length_only_messages[-1]["content"]
    assert "違う自然な反応の焦点" in length_only_messages[-1]["content"]

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
    assert "1案だけを他の候補より少し厚く" in two_candidate_style_messages[-1]["content"]
    assert "他の2案" not in two_candidate_style_messages[-1]["content"]

    single_candidate_style_messages = generation._build_repair_messages(
        [{"role": "user", "content": "カフェに行ってきた"}],
        '{"replies":["いいですね"]}',
        ["同一相手Goldより今回の返信候補が短めです。"],
        1,
    )
    assert "今回の1案に" in single_candidate_style_messages[-1]["content"]
    assert "他の候補" not in single_candidate_style_messages[-1]["content"]


def test_contact_style_repair_prompt_uses_profile_specific_register_target():
    base_messages = [{"role": "user", "content": "今週は忙しかった"}]
    raw_output = '{"replies":["お疲れさまです","大変でしたね","ゆっくりしてください"]}'
    issues = ["同一相手Goldでは会話調の返信も十分に使われています。"]

    hybrid_prompt = generation._build_repair_messages(
        base_messages,
        raw_output,
        issues,
        3,
        minimum_conversational_replies=1,
    )[-1]["content"]
    casual_prompt = generation._build_repair_messages(
        base_messages,
        raw_output,
        issues,
        3,
        minimum_conversational_replies=2,
    )[-1]["content"]

    assert "少なくとも1案" in hybrid_prompt
    assert "少なくとも2案" in casual_prompt


def test_generation_prompt_varies_reaction_focus_without_forcing_questions(client):
    cid = _seed_gold(client, "反応の幅", TAME_PAIRS[:5])

    ctx = generation._build_context(cid, "", "", "normal")
    system_prompt = ctx["system_prompt"]

    assert "反応の焦点を変える" in system_prompt
    assert "同じ助言や締め方" in system_prompt
    assert "労いや共感だけで自然に成立する案" in system_prompt
    assert "質問を足さず" in system_prompt


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


@pytest.mark.parametrize("gold_count", (1, 2))
def test_sparse_same_contact_gold_is_not_injected_as_priority_imitation_examples(client, gold_count):
    _seed_gold(client, "丁寧な全体Gold", KEIGO_PAIRS)
    cid = _seed_gold(client, "少数の砕けた同一相手Gold", TAME_PAIRS[:gold_count])
    _seed_accepted_generation(cid)

    ctx = generation._build_context(cid, "", "", "normal")

    assert "【SAME-CONTACT RECENT GOLD REPLIES】" not in ctx["system_prompt"]
    assert "【RECENT ACCEPTED】" not in ctx["system_prompt"]
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


def test_three_same_contact_gold_examples_can_be_injected_as_priority_imitation_examples(client):
    cid = _seed_gold(client, "十分な同一相手Gold", TAME_PAIRS[:3])

    ctx = generation._build_context(cid, "", "", "normal")

    assert "【SAME-CONTACT RECENT GOLD REPLIES】" in ctx["system_prompt"]
    assert "おつかれ笑" in ctx["system_prompt"]


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
    assert "候補が3案の場合は、少なくとも2案に" in summary
    assert "異なる反応の焦点" in summary
    assert "確認できない行動や結果を足さず" in summary
    assert "質問で長さを作らない" in summary
    assert "毎回長くする必要もない" in summary


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

    assert style.infer_contact_tone(style.compute_hierarchical_profile(casual_id)) == "tame"
    assert style.infer_contact_tone(style.compute_hierarchical_profile(polite_id)) == "keigo"
    # Mixed Gold should stay a soft preference, not a hard hybrid tone lock.
    assert style.infer_contact_tone(style.compute_hierarchical_profile(mixed_id)) == ""
    assert style.infer_contact_tone(style.compute_hierarchical_profile(few_id)) == ""
    assert style.infer_contact_tone(style.compute_hierarchical_profile(casual_id), requested_tone="keigo") == "keigo"
