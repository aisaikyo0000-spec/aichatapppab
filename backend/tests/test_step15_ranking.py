"""Step 15: Candidate Ranking の測定と優先順位の固定テスト。

方針:
- 重み変更は行わない（sweep で有意差なし・seed 依存のみ）。現行式を固定する
- Top-1 が平均より自然であることを測定する
- 長文優遇・短文罰・質問必須が ranking にないことを確認する
"""
from __future__ import annotations

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
    bad = ["とのことですがいいですね！", "ほかにも好きなものありますか？", "いいですね！"]
    assert generation.validate_candidate_replies(bad, 3) != []


def test_gold_similarity_aspects():
    """Gold 類似が多面的（文長・改行・語尾等の統計を持つ）。"""
    prof = style.compute_style_metrics(["おつかれさまです\n今日も一日お疲れ様でした", "いいですね！\n楽しそう"])
    assert prof.char_median > 0 and prof.line_median > 0
    assert prof.sample_count == 2


def test_same_contact_priority_maintained(client):
    """Same-contact 階層が維持されること。」"""
    cid = client.post("/api/contacts", json={"name": "階層相手", "profile": ""}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "カフェ好き？"})
    client.post(f"/api/contacts/{cid}/messages", json={
        "sender": "self", "content": "好きだよ！", "source": "manual",
    })
    prof = style.compute_hierarchical_profile(cid, "getting_to_know")
    assert prof["hierarchy_tier"] == "same_contact_recent_manual_gold"


def test_counterpart_not_copied():
    """相手の口調コピーを強制しない（温度感適応＋コピー禁止の両立）。"""
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text="相手: 今日まじ疲れた",
    )
    assert "オウム返し" in sysp
    assert "温度感" in sysp


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
