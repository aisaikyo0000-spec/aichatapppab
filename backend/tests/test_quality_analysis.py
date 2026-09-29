"""Step 6: 生成品質分析のテスト。

分析対象データはDBへ直接投入する（生成ロジックに依存しない）。
"""
from __future__ import annotations

import json

from app import database, quality_analysis as qa
from app.ai import naturalness, prompt


def _add_eval(
    history_text="それはきついな",
    counterpart="今日バイト8時間だった",
    intent="report",
    nat=0.9,
    style=0.8,
    final=0.86,
    rating=None,
    feedback="",
    tags=None,
    batch_id=1,
    candidate_index=0,
):
    """history＋evaluation の1件分を投入し、evaluation行dictを返す。"""
    conn = database.get_conn()
    try:
        now = database.now_iso()
        cur_c = conn.execute(
            "INSERT INTO contacts (name, profile, created_at, updated_at)"
            " VALUES ('分析相手', '', ?, ?)",
            (now, now),
        )
        contact_id = cur_c.lastrowid
        cur = conn.execute(
            "INSERT INTO generation_history"
            " (contact_id, provider, model, generated_text, counterpart_message, created_at)"
            " VALUES (?, 'fake', 'fake-model', ?, ?, ?)",
            (contact_id, history_text, counterpart, now),
        )
        hid = cur.lastrowid
        batch_row = conn.execute(
            "SELECT id FROM generation_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if batch_row is None:
            cur_b = conn.execute(
                "INSERT INTO generation_batches (contact_id, outcome, created_at)"
                " VALUES (?, 'pending', ?)",
                (contact_id, now),
            )
            batch_id = cur_b.lastrowid
        conn.execute(
            "INSERT INTO generation_evaluations"
            " (generation_batch_id, history_id, candidate_index, counterpart_intent,"
            "  naturalness_score, style_score, final_score,"
            "  human_rating, human_feedback, feedback_tags, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch_id, hid, candidate_index, intent, nat, style, final,
                rating, feedback, json.dumps(tags or [], ensure_ascii=False), now, now,
            ),
        )
        conn.commit()
        return hid
    finally:
        conn.close()


def _seed_mixed():
    _add_eval("それはきついな", "今日バイト8時間だった", "report", 0.92, 0.85, 0.89,
              rating="good", feedback="自然", tags=["good", "natural"])
    _add_eval("それは大変だったね！どこで働いてるの？", "今日バイト8時間だった", "report",
              0.88, 0.80, 0.85, rating="bad", feedback="質問が多い", tags=["too_many_questions", "ai_like"])
    _add_eval("今日8時間バイトだったんだね", "今日バイト8時間だった", "report",
              0.55, 0.60, 0.57, rating="good", feedback="短くてよい", tags=["natural"])
    _add_eval("14時くらいで大丈夫", "明日何時にする？", "question", 0.76, 0.70, 0.74,
              rating="neutral", feedback="少し硬い", tags=["awkward"])
    _add_eval("最近映画見てる？", "今日バイト8時間だった", "report", 0.50, 0.55, 0.52,
              rating="bad", feedback="話題無視", tags=["irrelevant"])
    _add_eval("未評価の候補", "眠い", "reaction", 0.81, 0.79, 0.80)


def test_1_rating_summary(client):
    """Test 1: Good/Neutral/Bad の集計が正しい。"""
    _seed_mixed()
    from app import config
    rows = qa.load_evaluations(config.DB_PATH)
    s = qa.rating_summary(rows)
    assert s["total"] == 6
    assert s["good"]["count"] == 2
    assert s["neutral"]["count"] == 1
    assert s["bad"]["count"] == 2
    assert s["unrated"] == 1
    assert s["good"]["avg_naturalness"] == round((0.92 + 0.55) / 2, 3)
    assert s["bad"]["avg_final"] == round((0.85 + 0.52) / 2, 3)


def test_2_tag_summary(client):
    """Test 2: Tag集計が正しい。"""
    _seed_mixed()
    from app import config
    rows = qa.load_evaluations(config.DB_PATH)
    tags = qa.tag_analysis(rows)
    assert tags["too_many_questions"]["count"] == 1
    assert tags["too_many_questions"]["bad_rate"] == 1.0
    assert tags["natural"]["count"] == 2
    assert tags["ai_like"]["avg_naturalness"] == 0.88


def test_3_intent_summary(client):
    """Test 3: Intent別集計が正しい。"""
    _seed_mixed()
    from app import config
    rows = qa.load_evaluations(config.DB_PATH)
    intents = qa.intent_analysis(rows)
    assert intents["report"]["count"] == 4
    assert intents["report"]["bad_rate"] == round(2 / 4, 3)
    assert intents["question"]["count"] == 1
    assert intents["reaction"]["count"] == 1
    assert "too_many_questions" in intents["report"]["top_failure_tags"]


def test_4_high_score_bad_extraction(client):
    """Test 4: High Score + Bad を抽出できる。"""
    _seed_mixed()
    from app import config
    rows = qa.load_evaluations(config.DB_PATH)
    d = qa.find_disagreements(rows)
    assert len(d["high_naturalness_but_bad"]) == 1
    assert d["high_naturalness_but_bad"][0]["generated_text"] == "それは大変だったね！どこで働いてるの？"
    assert len(d["high_final_but_bad"]) == 1


def test_5_low_score_good_extraction(client):
    """Test 5: Low Score + Good を抽出できる。"""
    _seed_mixed()
    from app import config
    rows = qa.load_evaluations(config.DB_PATH)
    d = qa.find_disagreements(rows)
    assert len(d["low_naturalness_but_good"]) == 1
    assert d["low_naturalness_but_good"][0]["generated_text"] == "今日8時間バイトだったんだね"


def test_6_representative_cases(client):
    """Test 6: 代表ケース4分類が正しい。"""
    _seed_mixed()
    from app import config
    rows = qa.load_evaluations(config.DB_PATH)
    rep = qa.representative_cases(rows)
    assert len(rep["A_high_good"]) == 1  # 0.89 + good
    assert len(rep["B_high_bad"]) == 1  # 0.85 + bad
    assert len(rep["C_low_good"]) == 1  # 0.57 + good
    assert len(rep["D_low_bad"]) == 1  # 0.52 + bad
    assert rep["B_high_bad"][0]["generated_text"] == "それは大変だったね！どこで働いてるの？"


def test_7_empty_data_no_error(client, tmp_path):
    """Test 7: 評価データ0件でもエラーにならない。"""
    from app import config
    rows = qa.load_evaluations(config.DB_PATH)
    assert rows == []
    assert qa.rating_summary(rows)["total"] == 0
    assert qa.tag_analysis(rows) == {}
    assert qa.intent_analysis(rows) == {}
    assert qa.find_disagreements(rows) == {
        "high_naturalness_but_bad": [],
        "low_naturalness_but_good": [],
        "high_final_but_bad": [],
    }
    assert qa.representative_cases(rows) == {
        "A_high_good": [], "B_high_bad": [], "C_low_good": [], "D_low_bad": [],
    }
    written = qa.write_reports(rows, tmp_path / "reports")
    assert set(written) == {
        "summary.json", "tag_analysis.json", "intent_analysis.json",
        "disagreements.json", "representative_cases.json",
    }


def test_8_null_and_broken_tags(client):
    """Test 8: feedback_tags が NULL・不正JSONでもエラーにならない。"""
    assert qa.parse_tags(None) == []
    assert qa.parse_tags("") == []
    assert qa.parse_tags("not-json{{{") == []
    assert qa.parse_tags('["ai_like", 123, null]') == ["ai_like"]
    assert qa.parse_tags(["good", None]) == ["good"]


def test_9_unknown_tags_analyzed(client):
    """Test 9: 未知の feedback tag が存在しても分析できる。"""
    _add_eval("何か変な返信", "眠い", "reaction", 0.6, 0.6, 0.6,
              rating="bad", feedback="変", tags=["brand_new_tag_xyz"])
    from app import config
    rows = qa.load_evaluations(config.DB_PATH)
    tags = qa.tag_analysis(rows)
    assert tags["brand_new_tag_xyz"]["count"] == 1
    cands = qa.improvement_candidates(tags)
    assert any(c["tag"] == "brand_new_tag_xyz" for c in cands)


def test_10_generation_logic_unchanged():
    """Test 10: 既存の生成処理を変更していない（Naturalness 決定値・Intent 分類）。"""
    ledger = {"counterpart_intent": "report", "known_self_facts": [], "already_asked_questions": []}
    res = naturalness.evaluate_candidate_naturalness(
        "それはきついな", "今日バイト8時間だった", ledger, []
    )
    assert res["score"] == 0.925
    assert prompt.classify_counterpart_intent("今日バイト8時間だった") == "report"
    assert prompt.classify_counterpart_intent("明日何時にする？") == "question"
    assert naturalness.STYLE_WEIGHT == 0.40
    assert naturalness.NATURALNESS_WEIGHT == 0.60
