"""階層的スタイルプロファイル学習モジュール（Hierarchical Style Profile v3.1）。

Priority順序:
1. same_contact_recent_manual_gold (最優先: 直近の同じ相手への手入力Gold)
2. same_contact_all_manual_gold (同じ相手の全手入力Gold)
3. same_contact_sent_silver (同じ相手の送信Silver)
4. global_manual_gold (他相手も含めた全手入力Gold)
5. global_profile (ベースライン)
"""
from __future__ import annotations

from dataclasses import dataclass, field
import statistics
import re
from typing import Any, Literal

from . import corpus

EMOJI_PATTERN = re.compile(
    r"[\U0001F300-\U0001F5FF"
    r"\U0001F600-\U0001F64F"
    r"\U0001F680-\U0001F6FF"
    r"\U0001F700-\U0001F77F"
    r"\U0001F780-\U0001F7FF"
    r"\U0001F800-\U0001F8FF"
    r"\U0001F900-\U0001F9FF"
    r"\U0001FA00-\U0001FA6F"
    r"\U0001FA70-\U0001FAFF"
    r"\U00002600-\U000026FF"
    r"\U00002700-\U000027BF"
    r"\U0001F1E6-\U0001F1FF"
    r"]"
)


def _split_sentences(text: str) -> list[str]:
    """1文単位でテキストを分割する。"""
    raw_lines = text.splitlines()
    sentences: list[str] = []
    for line in raw_lines:
        line_s = line.strip()
        if not line_s:
            continue
        parts = [p.strip() for p in re.split(r"(?<=[。！？!?])(?!(?:と|って|なんて|とか|そう|など|よ|ね|[」』）\)]))\s*", line_s) if p.strip()]
        if parts:
            sentences.extend(parts)
        else:
            sentences.append(line_s)
    return sentences


def _classify_tone_exclusive(text: str) -> Literal["keigo", "hybrid", "tame"]:
    """メッセージ単体を keigo / hybrid / tame の3値に排他的判定する。"""
    has_desu_masu = bool(re.search(r"(?:です|ます|でした|ました|ですね|ですか|でしょうか|ますよ)(?:[！!？?\s]|$)", text))
    has_casual_tokens = bool(re.search(r"(?:笑|w|ー|〜|っ|だね|だよ|よね|じゃん|かも|かな)(?:[！!？?\s]|$)", text))
    has_plain_tame = bool(re.search(r"(?:だね|だよ|行こう|しよう|楽しそう|いいな|まじ|ほんと|そうなんだ)(?:[！!？?\s]|$)", text))

    if has_desu_masu and has_casual_tokens:
        return "hybrid"
    elif has_desu_masu:
        return "keigo"
    elif has_plain_tame or has_casual_tokens:
        return "tame"
    return "hybrid"


@dataclass
class StyleProfile:
    """単一スコープのスタイル統計プロファイル。"""
    sample_count: int = 0
    # 文量統計 (median, P25, P75)
    char_median: int = 60
    char_p25: int = 40
    char_p75: int = 80
    line_median: int = 2
    line_p25: int = 1
    line_p75: int = 3
    sent_median: int = 2
    sent_p25: int = 1
    sent_p75: int = 3
    # トーン比率 (排他的判定)
    keigo_ratio: float = 0.2
    hybrid_ratio: float = 0.7
    tame_ratio: float = 0.1
    # 記号・絵文字・笑
    period_ratio: float = 0.05
    exclamation_ratio: float = 0.8
    question_ratio: float = 0.5
    question_free_ratio: float = 0.5
    laugh_ratio: float = 0.4
    emoji_avg_count: float = 0.6
    frequent_emojis: list[str] = field(default_factory=lambda: ["😊", "🚗", "😢"])
    # 構造・呼称
    name_call_ratio: float = 0.4
    first_person: str = "僕"


def compute_style_metrics(texts: list[str]) -> StyleProfile:
    """メッセージテキスト群から精密なスタイル統計を算出する。"""
    if not texts:
        return StyleProfile(sample_count=0)

    n = len(texts)
    char_lens = [len(t.strip()) for t in texts]
    line_counts = [len([l for l in t.splitlines() if l.strip()]) or 1 for t in texts]
    sent_counts = [len(_split_sentences(t)) or 1 for t in texts]

    def _percentile(data: list[int], p: float) -> int:
        if not data:
            return 0
        sorted_d = sorted(data)
        k = (len(sorted_d) - 1) * p
        f = int(k)
        c = min(f + 1, len(sorted_d) - 1)
        d0 = sorted_d[f] * (c - k)
        d1 = sorted_d[c] * (k - f)
        return int(round(d0 + d1))

    char_median = int(statistics.median(char_lens))
    char_p25 = _percentile(char_lens, 0.25)
    char_p75 = _percentile(char_lens, 0.75)

    line_median = int(statistics.median(line_counts))
    line_p25 = _percentile(line_counts, 0.25)
    line_p75 = _percentile(line_counts, 0.75)

    sent_median = int(statistics.median(sent_counts))
    sent_p25 = _percentile(sent_counts, 0.25)
    sent_p75 = _percentile(sent_counts, 0.75)

    # トーン比率
    tones = [_classify_tone_exclusive(t) for t in texts]
    keigo_count = sum(1 for t in tones if t == "keigo")
    hybrid_count = sum(1 for t in tones if t == "hybrid")
    tame_count = sum(1 for t in tones if t == "tame")

    # 記号・絵文字
    period_count = sum(1 for t in texts if "。" in t)
    excl_count = sum(1 for t in texts if "！" in t or "!" in t)
    q_count = sum(1 for t in texts if "？" in t or "?" in t)
    laugh_count = sum(1 for t in texts if "笑" in t or "w" in t)

    all_emojis: list[str] = []
    emoji_totals = 0
    for t in texts:
        emojis = EMOJI_PATTERN.findall(t)
        all_emojis.extend(emojis)
        emoji_totals += len(emojis)

    # 頻出絵文字
    from collections import Counter
    emoji_counts = Counter(all_emojis)
    top_emojis = [e for e, _ in emoji_counts.most_common(5)] or ["😊"]

    # 呼称・一人称
    name_call_count = sum(1 for t in texts if "さん" in t)
    boku_count = sum(1 for t in texts if "僕" in t)
    ore_count = sum(1 for t in texts if "俺" in t)
    jibun_count = sum(1 for t in texts if "自分" in t)
    watashi_count = sum(1 for t in texts if "私" in t)

    primary_fp = "僕"
    if ore_count > boku_count and ore_count > jibun_count:
        primary_fp = "俺"
    elif jibun_count > boku_count and jibun_count > ore_count:
        primary_fp = "自分"

    return StyleProfile(
        sample_count=n,
        char_median=char_median,
        char_p25=char_p25,
        char_p75=char_p75,
        line_median=line_median,
        line_p25=line_p25,
        line_p75=line_p75,
        sent_median=sent_median,
        sent_p25=sent_p25,
        sent_p75=sent_p75,
        keigo_ratio=round(keigo_count / n, 2),
        hybrid_ratio=round(hybrid_count / n, 2),
        tame_ratio=round(tame_count / n, 2),
        period_ratio=round(period_count / n, 2),
        exclamation_ratio=round(excl_count / n, 2),
        question_ratio=round(q_count / n, 2),
        question_free_ratio=round(1.0 - (q_count / n), 2),
        laugh_ratio=round(laugh_count / n, 2),
        emoji_avg_count=round(emoji_totals / n, 1),
        frequent_emojis=top_emojis,
        name_call_ratio=round(name_call_count / n, 2),
        first_person=primary_fp,
    )


def compute_hierarchical_profile(
    contact_id: int | None,
    current_phase: corpus.ConversationPhase = "ongoing",
) -> dict[str, Any]:
    """階層的スタイルプロファイルを構築し、合成ポリシーを生成する。"""
    all_pairs = corpus.extract_reply_pairs()
    valid_pairs = [p for p in all_pairs if not p.excluded]

    # 全selfメッセージテキスト
    all_self_texts = [p.self_turn.text for p in valid_pairs]
    # 全 manual Gold
    gold_texts = [p.self_turn.text for p in valid_pairs if p.label == "gold"]

    # same-contact の manual Gold
    same_contact_gold_pairs = [p for p in valid_pairs if contact_id and p.contact_id == contact_id and p.label == "gold"]
    same_contact_gold_texts = [p.self_turn.text for p in same_contact_gold_pairs]
    # same-contact の直近 manual Gold（直近5件）
    same_contact_recent_gold_texts = same_contact_gold_texts[-5:] if same_contact_gold_texts else []

    # same-contact の sent Silver
    same_contact_silver_texts = [p.self_turn.text for p in valid_pairs if contact_id and p.contact_id == contact_id and p.label == "silver"]

    # same-contact の全発信（legacy含む）
    contact_texts = [p.self_turn.text for p in valid_pairs if contact_id and p.contact_id == contact_id]
    # same-phase のみ
    phase_texts = [p.self_turn.text for p in valid_pairs if p.phase == current_phase]
    # 直近30件
    recent_texts = all_self_texts[-30:] if len(all_self_texts) > 30 else all_self_texts

    global_prof = compute_style_metrics(all_self_texts)
    gold_prof = compute_style_metrics(gold_texts)
    same_contact_recent_gold_prof = compute_style_metrics(same_contact_recent_gold_texts)
    same_contact_all_gold_prof = compute_style_metrics(same_contact_gold_texts)
    contact_prof = compute_style_metrics(contact_texts)
    phase_prof = compute_style_metrics(phase_texts)
    recent_prof = compute_style_metrics(recent_texts)

    # 優先順位の厳格化:
    # 1. same_contact_recent_manual_gold (1件以上あれば最優先)
    # 2. same_contact_all_manual_gold (1件以上あれば優先)
    # 3. same_contact_sent_silver (1件以上あれば)
    # 4. global_manual_gold (5件以上あれば)
    # 5. global_profile
    if same_contact_recent_gold_prof.sample_count >= 1:
        active_prof = same_contact_recent_gold_prof
        hierarchy_tier = "same_contact_recent_manual_gold"
    elif same_contact_all_gold_prof.sample_count >= 1:
        active_prof = same_contact_all_gold_prof
        hierarchy_tier = "same_contact_all_manual_gold"
    elif contact_prof.sample_count >= 3:
        active_prof = contact_prof
        hierarchy_tier = "contact_specific"
    elif gold_prof.sample_count >= 5:
        active_prof = gold_prof
        hierarchy_tier = "global_manual_gold"
    elif phase_prof.sample_count >= 5:
        active_prof = phase_prof
        hierarchy_tier = "phase_specific"
    else:
        active_prof = global_prof
        hierarchy_tier = "global"

    return {
        "active_profile": active_prof,
        "hierarchy_tier": hierarchy_tier,
        "global_profile": global_prof,
        "gold_profile": gold_prof,
        "same_contact_recent_gold_profile": same_contact_recent_gold_prof,
        "same_contact_all_gold_profile": same_contact_all_gold_prof,
        "contact_profile": contact_prof,
        "phase_profile": phase_prof,
        "recent_profile": recent_prof,
        "total_self_messages": len(all_self_texts),
        "gold_samples": len(gold_texts),
        "contact_samples": len(contact_texts),
        "same_contact_gold_samples": len(same_contact_gold_texts),
    }


def to_learned_policy_prompt(hierarchical: dict[str, Any]) -> str:
    """階層プロファイルからプロンプト用ポリシーブロックを生成する。"""
    p: StyleProfile = hierarchical.get("active_profile", StyleProfile())
    tier = hierarchical.get("hierarchy_tier", "global")
    sample_count = p.sample_count

    # トーン判定の要約
    if p.hybrid_ratio >= 0.5:
        tone_desc = f"丁寧な敬語をベースに『笑』やフランクな語尾を交えるハイブリッド調（ハイブリッド率{int(p.hybrid_ratio*100)}%）"
    elif p.keigo_ratio >= 0.5:
        tone_desc = f"丁寧な会話敬語ベース（敬語率{int(p.keigo_ratio*100)}%）"
    else:
        tone_desc = f"親しみやすいカジュアル口調（タメ口率{int(p.tame_ratio*100)}%）"

    punct_desc = "句点『。』はほぼ使わず" if p.period_ratio < 0.2 else "適度に句点『。』を使用し"
    laugh_desc = f"『笑』の使用率約{int(p.laugh_ratio*100)}%"
    q_desc = f"質問で終える割合 約{int(p.question_ratio*100)}%（質問なし返信 約{int(p.question_free_ratio*100)}%）"
    emoji_desc = f"1通あたり平均{p.emoji_avg_count}個（頻出: {' '.join(p.frequent_emojis)}）"

    return (
        f"【LEARNED USER RESPONSE POLICY】\n【USER LEARNED STYLE PROFILE】（採用階層: {tier} / 学習サンプル数: {sample_count}件）\n"
        f"ユーザー本人が実際に送信してきたメッセージ実績から抽出した文体・構造ポリシーです。\n"
        f"- 口調・トーン: {tone_desc}\n"
        f"- 文量・構成: 1通あたり中央値{p.char_median}文字（IQR: {p.char_p25}〜{p.char_p75}文字）、中央値{p.line_median}行・{p.sent_median}文\n"
        f"- 記号・絵文字: {punct_desc}、『！』を多用。{laugh_desc}。絵文字は{emoji_desc}\n"
        f"- 会話構造: 相手の発言への自然な反応を最優先し、必要な場合だけ質問・深掘り・自己開示を行う。質問しない返信・短い返信も正常。説明的な長文より、実績にある短い相槌・一言反応を優先すること。{q_desc}。一人称は『{p.first_person}』、相手の呼称は『〇〇さん』\n"
        f"- 禁止表現（実績ゼロの機械的AI表現）: 『〜とのこと』『〜と拝見』や、『ほかに』『ほかにも』『〜以外』『〇〇もいいですけど』等の話題逃げ・並列質問は本人の手入力実績に一切存在しないため完全禁止。\n"
        f"- 基本姿勢: 固定された画一ルールではなく、本人の実際の実績スタイル・テンポを最上位の正解として反映すること"
    )


def build_same_contact_gold_pairs_block(contact_id: int, limit: int = 10) -> str:
    """同じ相手に対するユーザーの直近手入力返信（Goldペア）の原文ブロックを構築する。"""
    gold_pairs = corpus.extract_same_contact_manual_gold_pairs(contact_id, limit=limit)
    if not gold_pairs:
        return ""

    lines = [f"【SAME-CONTACT RECENT GOLD REPLIES】（この相手へユーザーが実際に手入力した直近の返信実例）"]
    lines.append("※以下の語尾、改行テンポ、笑の入れ方、リアクションのニュアンスを最優先の模倣元とすること:")
    for idx, p in enumerate(gold_pairs, start=1):
        lines.append(f"[実例 {idx}]\n相手: {p.contact_turn.text}\n自分（手入力正解）: {p.self_turn.text}")
    return "\n".join(lines)
