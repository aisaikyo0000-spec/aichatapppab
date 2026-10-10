"""Step 15: Candidate Ranking の測定と優先順位の固定テスト。

方針:
- 重み変更は行わない（sweep で有意差なし・seed 依存のみ）。現行式を固定する
- Top-1 が平均より自然であることを測定する
- 長文優遇・短文罰・質問必須が ranking にないことを確認する
"""
from __future__ import annotations

from datetime import datetime

from app.ai import naturalness, prompt
from app.learning import style
from app.routers import generation


def _ledger(intent="report"):
    return {
        "counterpart_intent": intent,
        "known_self_facts": [],
        "already_asked_questions": [],
    }


def test_formula_weights():
    """現行式: final = 0.40*style + 0.60*nat + 0.1*(human_fit-0.5) + 0.06*(sent_sim-0.5)。"""
    assert naturalness.STYLE_WEIGHT == 0.40
    assert naturalness.NATURALNESS_WEIGHT == 0.60
    assert naturalness.combine_candidate_scores(0.5, 0.5) == 0.5
    assert naturalness.combine_candidate_scores(1.0, 1.0) == 1.0


def test_no_length_bias():
    """長い候補が自動的に高評価にならないこと。」"""
    short = naturalness.evaluate_candidate_naturalness(
        "いいね", "今日ラーメン食べた", _ledger("report"), []
    )
    long_verbose = naturalness.evaluate_candidate_naturalness(
        "今日ラーメンを食べたんだね！どこのラーメン？何ラーメンだったの？辛さはどうだった？",
        "今日ラーメン食べた", _ledger("report"), [],
    )
    assert short["score"] > long_verbose["score"]


def test_no_short_penalty_no_question_mandate():
    """短文・質問なしを理由に低評価しないこと。」"""
    res = naturalness.evaluate_candidate_naturalness("おつかれ", "今日疲れた", _ledger("report"), [])
    assert res["score"] >= 0.80
    assert not any(p["key"] == "question" and p["score"] < 0.80 for p in res["penalties"])


def test_priority_hard_over_feedback():
    """Hard correctness が最優先（validation が ranking より先）。"""
    bad = ["とのことですがいいですね！", "いいですね！", "いいですね！"]
    assert generation.validate_candidate_replies(bad, 3) != []


def test_contextual_transition_words_are_not_blanket_rejected():
    """接続語だけで話題逸脱と判定しない。文脈評価は生成・品質評価側で行う。"""
    replies = ["ほかにも行ったことある？", "それ気になる！", "楽しそう！"]
    assert generation.validate_candidate_replies(replies, 3) == []
    assert generation.sanitize_reply_text(replies[0]) == replies[0]


def test_natural_loanwords_are_not_rewritten_or_banned():
    replies = [
        "久しぶりにリフレッシュできた気がする！",
        "温泉を求めて旅行してる！",
        "それ最高ですね！",
    ]
    assert [generation.sanitize_reply_text(reply) for reply in replies] == replies
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
    )
    assert "カタカナ語の禁止" not in sysp


def test_sanitizer_preserves_reporting_clauses_without_changing_their_meaning():
    replies = [
        "温泉を求めて旅行しているとのことですが、楽しそうですね！",
        "プロフィールにはカフェ好きと書かれていましたので、気になりました！",
        "旅行に行ったと拝見しました！",
        "何かおすすめある？",
        "なにかおすすめある？",
        "今日は暑くなってきたね！",
        "寒くなってきたね！",
    ]

    sanitized = [
        generation.sanitize_reply_text(
            reply,
            current_datetime=datetime(2026, 1, 10) if "暑くなって" in reply else datetime(2026, 7, 10),
        )
        for reply in replies
    ]
    assert sanitized == replies
    seasonal_validation = generation.validate_candidate_replies(
        ["今日は寒くなってきたね！", "おつかれ！", "いいね！"],
        3,
        current_datetime=datetime(2026, 7, 10),
    )
    assert not any("季節外れ" in violation for violation in seasonal_validation)


def test_gold_similarity_aspects():
    """Gold 類似が多面的（文長・改行・語尾等の統計を持つ）。"""
    prof = style.compute_style_metrics(["おつかれさまです\n今日も一日お疲れ様でした", "いいですね！\n楽しそう"])
    assert prof.char_median > 0 and prof.line_median > 0
    assert prof.sample_count == 2


def test_learned_style_profile_tracks_japanese_comma_frequency():
    no_comma = style.compute_style_metrics(["そうなんだいいですね！", "楽しそう！"])
    with_comma = style.compute_style_metrics(["そうなんだ、いいですね！", "楽しそう！"])

    assert no_comma.comma_ratio == 0.0
    assert with_comma.comma_ratio == 0.5


def test_candidate_style_score_prefers_the_observed_comma_pattern():
    profile = {
        "weighted_profile": {
            "sample_count": 4,
            "char_p25": 1,
            "char_p75": 80,
            "char_median": 15,
            "sent_median": 1,
            "primary_tone": "hybrid",
            "hybrid_ratio": 1.0,
            "keigo_ratio": 0.0,
            "tame_ratio": 0.0,
            "period_ratio": 0.0,
            "comma_ratio": 0.0,
            "warai_ratio": 0.0,
            "avg_emojis": 0.0,
            "q_ratio": 0.0,
        }
    }

    no_comma_score, _ = generation.score_candidate_style("そうなんだいいですね！", profile)
    comma_score, _ = generation.score_candidate_style("そうなんだ、いいですね！", profile)

    assert no_comma_score > comma_score


def test_single_same_contact_gold_uses_global_fallback(client):
    """少数の同一相手Goldで全体の本人文体を置き換えない。"""
    global_cid = client.post("/api/contacts", json={"name": "全体相手", "profile": ""}).json()["id"]
    for idx in range(5):
        client.post(f"/api/contacts/{global_cid}/messages", json={"sender": "contact", "content": f"丁寧な発言{idx}"})
        client.post(f"/api/contacts/{global_cid}/messages", json={"sender": "self", "content": "ありがとうございます。よろしくお願いいたします。"})

    cid = client.post("/api/contacts", json={"name": "階層相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェ好き？"})
    client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "好きだよ！", "source": "manual",
    })
    prof = style.compute_hierarchical_profile(cid, "getting_to_know")
    assert prof["hierarchy_tier"] == "global_manual_gold"
    assert prof["active_profile"].keigo_ratio > prof["active_profile"].tame_ratio


def test_counterpart_not_copied():
    """本人 Gold がない相手には既存の Global fallback を保つ。"""
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: 今日まじ疲れた",
    )
    assert "オウム返し" in sysp
    assert "温度感" in sysp
    assert "20〜30%" not in sysp
    assert "相手の文体は模倣せず" in sysp
    assert "温度感を距離感の補助情報として扱う" in sysp
    assert "本人のGold実例とGlobalの本人文体" not in sysp


def test_sparse_same_contact_gold_stays_advisory():
    """3件の同一相手Goldは弱い参考情報で、距離感などを切り替えない。"""
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: 今日まじ疲れた",
        same_contact_gold_samples=3,
        same_contact_reply_style_block="本人Goldの文量中央値（観測値）: 24文字",
        counterpart_style_block="短文中心の相手",
    )
    assert "20〜30%" not in sysp
    assert "同一相手Goldはまだ少数のため弱い参考情報" in sysp
    assert "この傾向だけで距離感・口調・文量を切り替えない" in sysp


def test_closing_short_first():
    """Closing: 短い終了返信が質問付きより上位。」"""
    Janet = naturalness.evaluate_candidate_naturalness(
        "おやすみ", "そろそろ寝るね", _ledger("report"), []
    )
    with_q = naturalness.evaluate_candidate_naturalness(
        "おやすみ！明日は何する予定？", "そろそろ寝るね", _ledger("report"), []
    )
    assert Janet["score"] > with_q["score"]


def test_top1_better_than_average():
    """Top-1 測定: 代表 fixture で Top-1 の issue 率が全体平均以下。」"""
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "scripts"))
    from compare_before_after import detect_issues

    from step7_representative_cases import load_step7_representative_cases

    top1_echo = top1_q = 0
    all_echo = all_q = 0
    n_top = n_all = 0
    for case in load_step7_representative_cases():
        contact = case["contact"]
        ledger = prompt.build_conversation_state_ledger(
            [{"sender": "contact", "content": contact}], ""
        )
        scored = [
            naturalness.evaluate_candidate_naturalness(c, contact, ledger, [])["score"]
            for c in case["candidates"]
        ]
        order = sorted(range(3), key=lambda i: scored[i], reverse=True)
        top = case["candidates"][order[0]]
        iss = detect_issues(top, contact)
        n_top += 1
        top1_echo += 1 if iss["echo"] else 0
        top1_q += 1 if iss["too_many_questions"] else 0
        for c in case["candidates"]:
            iss2 = detect_issues(c, contact)
            n_all += 1
            all_echo += 1 if iss2["echo"] else 0
            all_q += 1 if iss2["too_many_questions"] else 0
    assert top1_echo / n_top <= all_echo / n_all
    assert top1_q / n_top <= all_q / n_all
