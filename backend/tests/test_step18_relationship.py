"""Step 18: 相手別・関係性別の会話距離適応。

- build_relationship_summary: 観測特徴のみ（関係ラベルなし）、実績0件時は空文字
- contact_tone_fit: Gold 3件未満は中立 0.5、tame/keigo の適合を判定
- ranking への影響は軽微（±0.02）かつ無データ時は中立
"""
from __future__ import annotations

from app.learning import contrast, style


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


def test_relationship_summary_uses_global_fallback_for_fewer_than_three_gold(client):
    cid = _seed_gold(client, "Cさん", TAME_PAIRS[:2])
    assert style.build_relationship_summary(cid) == ""


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


def test_single_contact_gold_does_not_replace_global_gold_style(client):
    _seed_gold(client, "全体の丁寧な相手", KEIGO_PAIRS)
    cid = _seed_gold(client, "Goldが1件の相手", TAME_PAIRS[:1])

    profile = style.compute_hierarchical_profile(cid)

    assert profile["hierarchy_tier"] == "global_manual_gold"
    assert profile["active_profile"].keigo_ratio > profile["active_profile"].tame_ratio


def test_same_contact_gold_adapts_without_fully_replacing_global_gold(client):
    _seed_gold(client, "全体の丁寧な相手", KEIGO_PAIRS)
    cid = _seed_gold(client, "Goldが3件の相手", TAME_PAIRS[:3])

    profile = style.compute_hierarchical_profile(cid)
    active = profile["active_profile"]
    global_gold = profile["gold_profile"]
    contact_gold = profile["same_contact_recent_gold_profile"]

    assert profile["hierarchy_tier"] == "same_contact_recent_manual_gold"
    assert global_gold.tame_ratio < active.tame_ratio < contact_gold.tame_ratio
    assert contact_gold.tame_ratio - active.tame_ratio >= 0.1


def test_same_contact_bronze_does_not_override_global_manual_gold(client):
    _seed_gold(client, "Global Gold", KEIGO_PAIRS)
    cid = _seed_non_gold(client, "Bronzeのみの相手", TAME_PAIRS[:3])

    profile = style.compute_hierarchical_profile(cid)

    assert profile["hierarchy_tier"] == "global_manual_gold"
    assert profile["active_profile"].keigo_ratio > profile["active_profile"].tame_ratio
