"""Step 14 §32: Golden Conversation Cases（15件の回帰固定）。

代表的な会話パターンの振る舞い（バリデーション通過・期待順位・最低スコア）を固定する。
入力は既存 fixture を再利用し、新規の正解捏造はしない。
"""
from __future__ import annotations

from app.ai import naturalness, prompt
from app.routers.generation import validate_candidate_replies

# (case_id, contact, candidates, expect_first, min_top_score)
GOLDEN_CASES = [
    ("short-reaction", "今日バイト8時間だった",
     ["それはきついな", "それは大変だったね！どこで働いてるの？", "今日8時間バイトだったんだね。お疲れ様！"], 0, 0.80),
    ("short-message", "眠い",
     ["それは眠そう", "眠いんだね！最近忙しいの？何時まで起きてたの？", "眠いんだね"], 0, 0.80),
    ("tired", "今日めっちゃ疲れた",
     ["おつかれさま", "お疲れさまです！今日はかなり大変だったんですね。どんなことがあったんですか？", "今日めっちゃ疲れたんだね"], 0, 0.80),
    ("question", "明日何時にする？",
     ["14時くらいで大丈夫", "明日はいい天気だね！何する？", "今日は疲れたな"], 0, 0.60),
    ("empathy", "今日ちょっと嫌なことあってさ",
     ["そっか", "嫌なことがあったんだね！何があったの？誰に何されたの？いつ？", "今日ちょっと嫌なことがあったんだね"], 0, 0.80),
    ("answer", "明日は14時集合ね",
     ["わかった、14時ね", "明日14時に集合することを理解したよ！場所はどこにする？持ち物は？何時に家を出る？", "明日は14時集合なんだね"], 0, 0.60),
    ("invitation", "今度一緒に行こうよ",
     ["行こう", "一緒に行こうと誘ってくれてありがとう！ぜひ行きたいな。いつがいいかな？どこに行く？", "一緒に行くんだね"], 0, 0.60),
    ("ramen", "今日ラーメン食べた",
     ["いいな", "どこのラーメン？何ラーメンだったの？", "今日ラーメンを食べたんだね"], 0, 0.80),
    ("greeting", "おはよう",
     ["おはよう", "おはようと言ってくれてありがとう！今日は何するの？どこ行くの？", "おはようなんだね"], 0, 0.80),
    ("closing", "そろそろ寝るね",
     ["おやすみ", "そろそろ寝るんだね！明日は何するの？", "寝るんだね"], 0, 0.60),
    ("long-report", "今日は朝から会議が3つあって昼も食べ損ねた。夕方やっと落ち着いたよ。",
     ["おつかれさま", "会議が3つあったんだね！昼を食べ損ねたんだね！夕方落ち着いたんだね！", "会議3つはきついね"], 0, 0.60),
    ("ambiguous", "あれどうなった？",
     ["どれのこと？", "あれがどうなったか確認するね！いつ頃わかる？", "あれがどうなったんだね"], 0, 0.40),
    ("work-done", "仕事終わった",
     ["おつかれさまです", "仕事が終わったんだね！今日は忙しかったの？何時に終わったの？", "おつかれ"], 0, 0.60),
    ("hobby", "最近映画見てる",
     ["いいね", "映画を見てるんだね！何を見たの？どこで見たの？誰と見たの？", "最近映画を見てるんだね"], 0, 0.80),
    ("plan", "明日早いんだよね",
     ["早く寝なね", "明日早いんだね！何時に起きるの？何があるの？大丈夫？", "明日早いんだね"], 0, 0.75),
]


def _ledger_for(contact: str) -> dict:
    return prompt.build_conversation_state_ledger(
        [{"sender": "contact", "content": contact}], ""
    )


def test_golden_validation_and_ranking():
    """Golden 15件: バリデーション通過・期待順位・最低スコアを固定。」"""
    failures = []
    for case_id, contact, cands, expect_first, min_score in GOLDEN_CASES:
        ledger = _ledger_for(contact)
        violations = validate_candidate_replies(cands, 3)
        scored = [
            naturalness.evaluate_candidate_naturalness(c, contact, ledger, [])["score"]
            for c in cands
        ]
        order = sorted(range(3), key=lambda i: scored[i], reverse=True)
        if violations != [] or order[0] != expect_first or scored[order[0]] < min_score:
            failures.append(case_id)
    assert failures == []


def test_golden_no_question_ban():
    """Golden: 質問候補自体は禁止されない（必要時の質問可）。"""
    res = naturalness.evaluate_candidate_naturalness(
        "14時がいいな。何時に集まる？", "明日何時にする？",
        {"counterpart_intent": "question", "known_self_facts": [], "already_asked_questions": []},
        [],
    )
    assert res["score"] >= 0.60
