"""Step 18: 相手別・関係性別の会話距離適応。

- build_relationship_summary: 観測特徴のみ（関係ラベルなし）、実績0件時は空文字
- contact_tone_fit: Gold 3件未満は中立 0.5、tame/keigo の適合を判定
- ranking への影響は軽微（±0.02）かつ無データ時は中立
"""
from __future__ import annotations

import pytest

from app import database
from app.learning import contrast, corpus, style
from app.routers import generation


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
    assert "3案の少なくとも1案は敬語だけで終始させず" in summary


def test_relationship_summary_uses_global_fallback_for_fewer_than_three_gold(client):
    cid = _seed_gold(client, "Cさん", TAME_PAIRS[:2])
    assert style.build_relationship_summary(cid) == ""


def test_relationship_summary_describes_contact_relative_message_length(client):
    _seed_gold(client, "短文のGlobal Gold", TAME_PAIRS)
    cid = _seed_gold(client, "長めの同一相手Gold", KEIGO_PAIRS)

    summary = style.build_relationship_summary(cid)

    assert "Global Goldより相対的に長め" in summary
    assert "同一相手Goldの文量中央値" in summary


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

    assert "Global Goldより長めに返す傾向がある" in summary
    assert "話題が許す場合" in summary
    assert "毎回この文量にする必要はない" in summary


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
