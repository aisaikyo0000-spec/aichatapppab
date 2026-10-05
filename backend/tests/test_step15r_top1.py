"""Step 15-R: Top-1 誤選択の改善テスト。

- 同点時の軽微 issue タイブレーク（スコア不変・排除ではない）
- 5件の flip 回帰固定（b13/b38/b53/b68/b70）
- 仕様の代表例（圧縮・Echo・質問・終了・話題逸脱・過剰反応）の確認
"""
from __future__ import annotations

from app.ai import naturalness, prompt


def _rank(cands, contact, intent="report"):
    ledger = {
        "counterpart_intent": intent,
        "known_self_facts": [],
        "already_asked_questions": [],
    }
    items = []
    for c in cands:
        n = naturalness.evaluate_candidate_naturalness(c, contact, ledger, [])["score"]
        f = round(0.4 * 1.0 + 0.6 * n, 3)
        items.append({"reply": c, "final": f,
                      "issues": naturalness.count_mild_issues(c, contact)})
    items.sort(key=lambda x: (x["final"], -x["issues"]), reverse=True)
    return [it["reply"] for it in items]


def test_tiebreak_prefers_fewer_issues():
    """同点時は軽微 issue の少ない方が上位（スコア自体は不変）。"""
    assert naturalness.count_mild_issues("そうなんですね！", "最近ランニング始めた") == 1
    assert naturalness.count_mild_issues("走りやすい季節になってきましたもんね！", "最近ランニング始めた") == 0
    # 単語禁止ではない（スコアに影響しない）
    res = naturalness.evaluate_candidate_naturalness(
        "そうなんですね！", "最近ランニング始めた",
        {"counterpart_intent": "report", "known_self_facts": [], "already_asked_questions": []},
        [],
    )
    assert res["score"] >= 0.80


def test_flip_b13():
    assert _rank(
        ["それは大変でしたね。\nゆっくり休んでくださいね。",
         "なんかそういう日ってありますよね。\n嫌なことは早く忘れちゃいましょ笑。",
         "それはしんどいですね。\nなにか嫌なことでもあったんですか？"],
        "なんか今日ついてない",
    )[0].startswith("なんかそういう日ってありますよね")


def test_flip_b38():
    assert _rank(
        ["なるほどです笑", "そういうことなんですね！", "やっとスッキリしました笑"],
        "そういうことね",
    )[0] == "そういうことなんですね！"


def test_flip_b53():
    assert _rank(
        ["そうなんですね。\n色々考えちゃいますよね。",
         "分かります。\nたまにそういう時ありますよね。",
         "お疲れ様です。\nあまり考えすぎないようにしてくださいね。"],
        "将来のこと考えちゃう",
    )[0].startswith("分かります")


def test_flip_b68():
    assert _rank(
        ["そうなんですね", "なるほどです", "ですね笑"],
        "そっか",
    )[0] == "ですね笑"


def test_flip_b70():
    assert _rank(
        ["そうなんですね！", "それはよかったです笑", "いい感じですね！"],
        "いい感じ",
    )[0] == "それはよかったです笑"


def test_reply_compression():
    """§14: 短い自然反応が過剰共感より上位。」"""
    assert _rank(
        ["それはきついな",
         "今日は疲れたんですね。かなり大変だったと思います。ゆっくり休んでくださいね。",
         "疲れたんだね"],
        "今日は疲れた",
    )[0] == "それはきついな"


def test_echo_vs_reaction():
    """§15: Echo より Reaction が上位。」"""
    assert _rank(
        ["今日映画見たんだね", "いいね", "映画見たんだね"],
        "今日映画見た",
    )[0] == "いいね"


def test_question_necessity_spot():
    """§16: 質問の有無だけで自動加点しない。」"""
    ranked = _rank(
        ["いいね", "どこのラーメン？", "今日ラーメン食べたんだね"],
        "今日ラーメン食べた",
    )
    assert ranked[0] == "いいね"


def test_closing_spot():
    """§17: 終了時は短い返答が上位。」"""
    assert _rank(
        ["おやすみ", "おやすみ！明日は何する予定？", "ゆっくり休んでね"],
        "そろそろ寝るね",
    )[0] == "おやすみ"


def test_topic_drift_spot():
    """§18: 話題逸脱より直接反応が上位。」"""
    assert _rank(
        ["それはきついな", "最近旅行行きたいんだよね", "仕事疲れたんだね"],
        "今日仕事疲れた",
    )[0] == "それはきついな"


def test_overreaction_spot():
    """§19: 過剰反応より短い労いが上位。」"""
    assert _rank(
        ["おつかれ", "本当に大変だったんですね。無理せずゆっくり休んでくださいね。", "疲れたんだね"],
        "ちょっと疲れた",
    )[0] == "おつかれ"
