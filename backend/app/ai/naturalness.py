"""決定論的な候補自然性評価器（Naturalness Scorer v1 / Step 4）。

「本人の文体に似ているか」（style_score）とは別軸で、
「今回の会話に自然に合っているか」を評価する。外部LLMは使わない。

評価項目:
- relevance（相手直前発言への反応）
- question（質問過多の抑制。質問自体は減点しない）
- repetition（直近の自分の返信との繰り返し）
- echo（相手発言の単純言い換え）
- length（相手発言量に対する長さ適合。Hard Limit なし）
- disclosure（根拠のない自己開示）
- answer（質問・誘い・要回答への回答完全性）
- overreact（感情共有への過剰反応）

Intent 別に重みを変える。将来 LLM Judge へ置き換え可能なよう、
入出力インターフェース（evaluate_candidate_naturalness）を安定させる。
"""
from __future__ import annotations

import re
from typing import Any

# 最終スコアの重み（将来の評価結果で変更可能にするため定数化）
STYLE_WEIGHT = 0.40
NATURALNESS_WEIGHT = 0.60

# Intent 別の評価重み（合計 1.0 になるよう正規化して使用する）
_INTENT_WEIGHTS: dict[str, dict[str, float]] = {
    "report": {
        "relevance": 0.30, "question": 0.20, "echo": 0.20,
        "length": 0.15, "repetition": 0.10, "disclosure": 0.05,
    },
    "reaction": {
        "relevance": 0.30, "length": 0.25, "repetition": 0.25,
        "question": 0.10, "echo": 0.05, "disclosure": 0.05,
    },
    "question": {
        "relevance": 0.35, "answer": 0.35, "question": 0.05,
        "length": 0.10, "repetition": 0.05, "echo": 0.05, "disclosure": 0.05,
    },
    "invitation": {
        "relevance": 0.30, "answer": 0.25, "disclosure": 0.20,
        "question": 0.10, "length": 0.10, "repetition": 0.05,
    },
    "emotional_share": {
        "relevance": 0.30, "question": 0.20, "overreact": 0.20,
        "length": 0.10, "repetition": 0.10, "echo": 0.05, "disclosure": 0.05,
    },
    "answer_required": {
        "answer": 0.40, "relevance": 0.30, "question": 0.05,
        "length": 0.10, "repetition": 0.05, "echo": 0.05, "disclosure": 0.05,
    },
}

# 共感・相づちとして自然な反応語彙（relevance の加点材料）
_REACTION_LEXICON = (
    "きつい", "大変", "いい", "そう", "なるほど", "確か", "わかる", "分かる",
    "おつかれ", "お疲れ", "そっか", "へー", "ほんと", "本当", "よかった",
    "すご", "楽し", "残念", "了解", "OK", "よし", "眠い", "疲れ", "笑",
)
# 回答の具体性を示す手がかり（日時・決定・可否・場所）
_CONCRETE_PATTERN = re.compile(
    r"\d+\s*(?:時|分|日|月|曜)|大丈夫|OK|いいよ|だめ|無理|できる|できない|"
    r"行ける|行けない|空いてる|予定|駅|店|お店|そこ|ここ"
)
# 質問語尾（？なし行の検出用）
_QUESTION_ENDING = re.compile(r"(?:ですか|ますか|でしょうか|だろうか|のかな|かな|かい|だっけ|っけ)\s*$")
# 短いリアクション質問（情報取得質問とは別扱い）
_REACTION_QUESTION = re.compile(r"^(?:ほんと|そうなの|まじ|え|本当|そう)[？?]*$")
# 自己開示の主張パターン（エピソード・習慣系のみ。 bare な「好き」は対象外）
_DISCLOSURE_EPISODIC = re.compile(
    r"昨日|先週|先月|小さい頃|子供の頃|普段.{0,6}(?:行く|してる|する)|"
    r"よく.{0,6}(?:行く|食べる|見る|飲む|する)|最近.{0,6}(?:行った|食べた|見た|買った|した)"
)
_DISCLOSURE_SELF_MARKER = re.compile(r"自分|僕|俺|私|うち")
# Echo 判定で除去する薄い付加語尾
_ECHO_WRAPPERS = (
    "だったんだよね", "だったんだね", "なんだよね", "なんだね",
    "だったよね", "だよね", "だね", "ですね", "ますね",
    "お疲れ様", "おつかれ",
)


def _normalize(text: str) -> str:
    """比較用の正規化（空白・記号・笑いの除去）。"""
    t = (text or "").strip()
    t = re.sub(r"[\s　]+", "", t)
    t = re.sub(r"[！!？?。、．，・…〜～!?.]+", "", t)
    t = re.sub(r"(笑|ｗ|w|W)+", "", t)
    return t


def _extract_keywords(text: str) -> set[str]:
    """トピックキーワード（漢字2+・カタカナ2+・英字2+）を抽出する。"""
    t = (text or "")
    kata = re.findall(r"[ァ-ヶー]{2,}", t)
    kanji = re.findall(r"[一-鿿]{2,}", t)
    alpha = re.findall(r"[A-Za-z]{2,}", t)
    return set(kata + kanji + [a.lower() for a in alpha])


def _jaccard2(s1: str, s2: str) -> float:
    """2-gram Jaccard 類似度。"""
    n1 = {s1[i:i + 2] for i in range(len(s1) - 1)} if len(s1) >= 2 else ({s1} if s1 else set())
    n2 = {s2[i:i + 2] for i in range(len(s2) - 1)} if len(s2) >= 2 else ({s2} if s2 else set())
    if not n1 or not n2:
        return 1.0 if s1 == s2 else 0.0
    return len(n1 & n2) / len(n1 | n2)


def count_meaningful_questions(candidate: str) -> dict[str, int]:
    """質問数を数える。短いリアクション質問は別枠で数える。

    Returns: {"informative": int, "reaction": int}
    """
    informative = 0
    reaction = 0
    for line in (candidate or "").splitlines():
        line_s = line.strip()
        if not line_s:
            continue
        marks = len(re.findall(r"[？?]", line_s))
        if marks:
            # 短いリアクション質問か判定
            core = re.sub(r"[？?！!〜～ー]+$", "", line_s)
            if len(core) <= 8 and _REACTION_QUESTION.match(core):
                reaction += marks
            else:
                informative += marks
        elif _QUESTION_ENDING.search(line_s):
            informative += 1
    return {"informative": informative, "reaction": reaction}


def detect_repetition(candidate: str, recent_replies: list[str]) -> tuple[float, dict | None]:
    """直近の自分の返信との繰り返しを検出する。短い一般語のみの一致は強く罰しない。"""
    cand_norm = _normalize(candidate)
    if not cand_norm or not recent_replies:
        return 1.0, None
    is_short = len(cand_norm) <= 4
    worst = 1.0
    detail: dict | None = None
    for prev in recent_replies:
        prev_norm = _normalize(prev)
        if not prev_norm:
            continue
        if cand_norm == prev_norm:
            return (0.8 if is_short else 0.0), {"type": "exact", "with": prev[:30]}
        sim = _jaccard2(cand_norm, prev_norm)
        same_head = len(cand_norm) > 5 and cand_norm[:5] == prev_norm[:5]
        same_qform = (
            ("？" in candidate or "?" in candidate) and ("？" in prev or "?" in prev)
            and any(w in candidate and w in prev for w in
                    ("どこ", "いつ", "なに", "誰", "どれ", "どっち", "どう", "何"))
        )
        # 中程度の n-gram 一致だけでは減点しない（同じ話題・文体の語彙共有と区別できないため）。
        # 同じ冒頭・同じ質問形式を伴う場合のみ繰り返しとみなす。near-duplicate は例外。
        if sim >= 0.8:
            s = 0.8 if is_short else 0.3
            if s < worst:
                worst, detail = s, {"type": "near_duplicate", "similarity": round(sim, 2)}
        elif sim >= 0.5 and (same_head or same_qform):
            s = 0.9 if is_short else 0.6
            if s < worst:
                worst, detail = s, {"type": "similar", "similarity": round(sim, 2)}
        # 同じ冒頭（5文字）
        if same_head:
            s = 0.9 if is_short else 0.7
            if s < worst:
                worst, detail = s, {"type": "same_head", "head": cand_norm[:5]}
        # 同じ質問形式（共に疑問＋疑問詞の共有）
        if same_qform:
            s = 0.9 if is_short else 0.7
            if s < worst:
                worst, detail = s, {"type": "same_question_form"}
    return worst, detail


def detect_echo(candidate: str, counterpart_message: str) -> tuple[float, dict | None]:
    """相手発言の単純言い換え・大部分コピーを検出する。"""
    cand_norm = _normalize(candidate)
    cp_norm = _normalize(counterpart_message)
    if not cand_norm or not cp_norm:
        return 1.0, None
    # 完全一致
    if cand_norm == cp_norm:
        return 0.0, {"type": "exact_echo"}
    # 相手文の大部分を含む（候補が相手文の2.5倍未満の場合のみ）
    if cp_norm in cand_norm and len(cand_norm) < len(cp_norm) * 2.5:
        return 0.35, {"type": "contains_counterpart"}
    # 主要語句の並べ替え＋薄い付加のみ
    cp_kw = _extract_keywords(counterpart_message)
    cand_kw = _extract_keywords(candidate)
    if cp_kw and cp_kw <= cand_kw and len(cand_kw - cp_kw) <= 1:
        core = cand_norm
        for w in sorted(_ECHO_WRAPPERS, key=len, reverse=True):
            if core.endswith(w):
                core = core[: -len(w)]
                break
        if _jaccard2(core, cp_norm) >= 0.30 or core == cp_norm:
            return 0.35, {"type": "paraphrase_echo"}
    return 1.0, None


def evaluate_candidate_naturalness(
    candidate: str,
    counterpart_message: str = "",
    conversation_ledger: dict[str, Any] | None = None,
    recent_replies: list[str] | None = None,
    style_char_median: float | None = None,
) -> dict[str, Any]:
    """候補の自然性を決定論的に評価する。

    Returns: {"score": 0.0〜1.0, "penalties": [...], "signals": {...},
              "intent": str, "weights": {...}}
    penalties の各要素: {"key": 評価項目, "score": 項目スコア, "detail": ...}
    （score が低い項目のみ記録する。空リストは減点なしを意味する）
    """
    ledger = conversation_ledger or {}
    intent = (ledger.get("counterpart_intent") or "report").strip() or "report"
    if intent not in _INTENT_WEIGHTS:
        intent = "report"
    recent = [r for r in (recent_replies or []) if (r or "").strip()]
    cand = (candidate or "").strip()
    cp = (counterpart_message or "").strip()

    penalties: list[dict[str, Any]] = []
    signals: dict[str, Any] = {}
    sub: dict[str, float] = {}

    cp_kw = _extract_keywords(cp)
    cand_kw = _extract_keywords(cand)
    overlap = len(cp_kw & cand_kw) / len(cp_kw) if cp_kw else 0.0
    signals["keyword_overlap"] = round(overlap, 2)
    has_reaction_word = any(w in cand for w in _REACTION_LEXICON)
    has_concrete = bool(_CONCRETE_PATTERN.search(cand))
    signals["has_concrete_answer"] = has_concrete
    q = count_meaningful_questions(cand)
    signals["informative_questions"] = q["informative"]
    signals["reaction_questions"] = q["reaction"]

    # A. Relevance
    if intent in ("question", "answer_required", "invitation"):
        # 回答系: キーワード一致 or 具体的回答を評価（相づちだけでは不十分）
        rel = max(overlap, 0.8 if has_concrete else 0.0)
    else:
        rel = max(overlap, 0.75 if has_reaction_word else 0.3)
        if not cp_kw and not has_reaction_word:
            rel = 0.6  # 判断材料なし。罰しない
    sub["relevance"] = rel

    # B. Question Overuse（質問があるだけでは減点しない）
    eff_q = q["informative"] + 0.5 * q["reaction"]
    if intent in ("report", "reaction", "emotional_share"):
        if eff_q <= 0:
            q_score = 1.0
        elif eff_q <= 1:
            q_score = 0.85
        else:
            q_score = max(0.0, 0.85 - 0.30 * (eff_q - 1))
    else:
        # 回答系: 回答内容があれば質問併存も許容。回答なしの質問返しは減点
        answered = has_concrete or overlap > 0
        if eff_q <= 1:
            q_score = 1.0
        elif answered:
            q_score = max(0.4, 1.0 - 0.20 * (eff_q - 1))
        else:
            q_score = max(0.0, 1.0 - 0.35 * eff_q)
    sub["question"] = q_score

    # C. Repetition
    rep_score, rep_detail = detect_repetition(cand, recent)
    sub["repetition"] = rep_score

    # D. Echo
    echo_score, echo_detail = detect_echo(cand, cp)
    sub["echo"] = echo_score

    # E. Length Fit（傾向のみ。Hard Limit なし）
    cp_len = len(cp)
    cand_len = len(cand)
    ratio = cand_len / max(cp_len, 1)
    if cp_len <= 20:
        len_score = 1.0 if ratio <= 4 else (0.7 if ratio <= 8 else 0.4)
    elif cp_len <= 80:
        len_score = 1.0 if ratio <= 2 else (0.7 if ratio <= 4 else 0.4)
    else:
        len_score = 1.0 if ratio <= 1.5 else (0.8 if ratio <= 3 else 0.5)
    if style_char_median and cand_len <= style_char_median * 1.5 and len_score < 0.6:
        len_score = 0.6  # 本人の学習文量に収まる場合は緩和
    if intent in ("question", "answer_required") and has_concrete and len_score < 0.7:
        len_score = 0.7  # 回答に必要な長さは許容
    sub["length"] = len_score
    signals["length_ratio"] = round(ratio, 2)

    # F. Unsupported Self-Disclosure（事実で確認できる場合のみ減点）
    facts = [f for f in (ledger.get("known_self_facts") or []) if (f or "").strip()]
    facts_text = "\n".join(facts)
    if _DISCLOSURE_EPISODIC.search(cand) and _DISCLOSURE_SELF_MARKER.search(cand):
        if not facts_text:
            dis_score, dis_detail = 0.7, {"type": "unverifiable"}
        else:
            claim_kw = _extract_keywords(cand)
            fact_kw = _extract_keywords(facts_text)
            if claim_kw and (claim_kw & fact_kw):
                dis_score, dis_detail = 1.0, None
            else:
                dis_score, dis_detail = 0.2, {"type": "unsupported_self_disclosure"}
    else:
        dis_score, dis_detail = 1.0, None
    sub["disclosure"] = dis_score

    # Answer completeness（回答系 Intent のみ重みづけ。用途外では 1.0）
    if intent in ("question", "answer_required", "invitation"):
        is_deflect = eff_q >= 1 and not has_concrete and overlap == 0
        ans = 0.3 + 0.35 * (1.0 if has_concrete else 0.0) + 0.35 * min(1.0, overlap * 2)
        if is_deflect:
            ans = min(ans, 0.3)
        ans_score = round(min(1.0, ans), 3)
    else:
        ans_score = 1.0
    sub["answer"] = ans_score

    # Overreaction（emotional_share のみ重みづけ）
    if intent == "emotional_share":
        over = 1.0
        if eff_q >= 2:
            over = min(over, 0.5)
        if ratio > 4:
            over = min(over, 0.6)
        if len(re.findall(r"[！!]", cand)) >= 3:
            over = min(over, 0.7)
        over_score = over
    else:
        over_score = 1.0
    sub["overreact"] = over_score

    weights = _INTENT_WEIGHTS[intent]
    total_w = sum(weights.values())
    score = sum(sub[k] * w for k, w in weights.items()) / total_w

    for key in sorted(weights):
        if sub[key] < 1.0:
            penalties.append({
                "key": key,
                "score": round(sub[key], 3),
                "detail": {"repetition": rep_detail, "echo": echo_detail,
                           "disclosure": dis_detail}.get(key),
            })

    return {
        "score": round(score, 3),
        "penalties": penalties,
        "signals": signals,
        "intent": intent,
        "weights": {k: round(v / total_w, 3) for k, v in weights.items()},
    }


def combine_candidate_scores(style_score: float, naturalness_score: float) -> float:
    """style と naturalness を重みづけ結合する。"""
    return round(float(style_score) * STYLE_WEIGHT + float(naturalness_score) * NATURALNESS_WEIGHT, 3)
