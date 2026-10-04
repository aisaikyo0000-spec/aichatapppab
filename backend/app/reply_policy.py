"""返信方針の軽量推定器（Step 10・決定論）。

相手メッセージから、適切な応答行為・質問必要性・返信量目安を推定する。
1つに無理やり分類できない場合は複数ラベルを許容する。
固定ルールではなく目安であり、最終判断は会話履歴・Gold 実績が優先する。

本モジュールは評価・ベンチマーク層とテストから利用する。
生成プロンプトへの直接注入は行わない（§22: 意図別方針・長さ区分と重複するため）。
"""
from __future__ import annotations

import re

# 応答行為ラベル
REPLY_INTENTS = (
    "reaction",
    "acknowledgement",
    "empathy",
    "question",
    "answer",
    "joke",
    "agreement",
    "disagreement",
    "topic_continuation",
    "closing",
    "mixed",
)

_QUESTION_MARK = re.compile(r"[？?]")
_CLOSING = re.compile(r"おやすみ|またね|バイバイ|ばいばい|じゃあね|お疲れ様でした|失礼します")
_GREETING = re.compile(r"^(?:おはよう|こんにちは|こんばんは|はじめまして)[！!。]?$")
_EMOTIONAL = re.compile(
    r"しんど|つら|辛い|悲し|嫌な|嫌だっ|落ち込|最悪|凹|へこ|悩|怒られ|泣|疲れ|眠い|やば"
)
_AGREE = re.compile(r"そうだね|そうだよ|たしかに|確かに|だよね|わかる|分かる|いいね")
_DISAGREE = re.compile(r"でも|だけど|いや|ちがう|違う|そうかな")
_JOKE = re.compile(r"笑|ｗ|w{2,}|草|ウケる|わろた|まじか")
INFORMATIONAL_QUESTION = re.compile(r"どこ|いつ|なに|何時|誰|どれ|どっち|どう|ですか|ますか")


def estimate_reply_intents(contact_text: str, counterpart_intent: str = "report") -> list[str]:
    """応答行為ラベルを複数許容で推定する。空入力は acknowledgement のみ。"""
    t = (contact_text or "").strip()
    if not t:
        return ["acknowledgement"]
    labels: list[str] = []
    if _CLOSING.search(t):
        labels.append("closing")
    if _GREETING.match(t):
        labels.append("acknowledgement")
    if counterpart_intent in ("question", "answer_required"):
        labels.append("answer")
    if counterpart_intent == "invitation":
        labels.extend(["answer", "agreement"])
    if _QUESTION_MARK.search(t) and counterpart_intent not in ("question", "answer_required"):
        labels.append("question")
    if _EMOTIONAL.search(t):
        labels.extend(["empathy", "reaction"])
    elif len(t) <= 20:
        labels.append("reaction")
    if _AGREE.search(t):
        labels.append("agreement")
    if _DISAGREE.search(t):
        labels.append("disagreement")
    if _JOKE.search(t):
        labels.append("joke")
    if not labels or (set(labels) <= {"reaction", "acknowledgement"} and len(t) > 20):
        labels.append("topic_continuation")
    if "closing" in labels and len(labels) > 1:
        labels = ["closing"]
    unique = sorted(set(labels), key=REPLY_INTENTS.index)
    if len(unique) >= 3:
        unique.append("mixed")
    return unique


def question_necessity(
    contact_text: str,
    counterpart_intent: str = "report",
    has_unresolved_question: bool = False,
    gold_question_rate: float | None = None,
) -> str:
    """質問必要性を needed / optional / unnecessary で判定する。

    Gold（同一相手の質問率）が低い場合は optional を unnecessary へ一段階下げる。
    ただし回答が必要な場合は下げない。
    """
    t = (contact_text or "").strip()
    if counterpart_intent in ("question", "answer_required", "invitation") or has_unresolved_question:
        return "needed"
    if not t or _CLOSING.search(t) or _GREETING.match(t):
        return "unnecessary"
    if counterpart_intent in ("reaction", "emotional_share") and len(t) <= 20:
        level = "unnecessary"
    else:
        level = "optional"
    if level == "optional" and gold_question_rate is not None and gold_question_rate < 0.25:
        return "unnecessary"
    return level


def response_length_target(
    contact_text: str,
    counterpart_intent: str = "report",
    gold_median_len: float | None = None,
) -> str:
    """返信量目安を very_short / short / medium / long で推定する（固定制限なし）。

    Gold 中央値が短い場合は上限側へ補正する（下限は設けない）。
    """
    n = len((contact_text or "").strip())
    if n <= 10:
        level = "very_short"
    elif n <= 30:
        level = "short"
    elif n > 80 or counterpart_intent == "emotional_share":
        level = "long"
    else:
        level = "medium"
    order = ["very_short", "short", "medium", "long"]
    if gold_median_len is not None and gold_median_len <= 20:
        level = order[min(order.index(level), order.index("short"))]
    return level
