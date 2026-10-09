"""階層的スタイルプロファイル学習モジュール（Hierarchical Style Profile v3.1）。

Priority順序:
1. 同一相手の手入力GoldをGlobal Goldへ段階的に反映
2. Global manual Gold
3. 同一相手のsent Silver（Global Goldが不足する場合のみ）
4. phase / global profile
"""
from __future__ import annotations

from dataclasses import dataclass, field
import dataclasses
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
LAUGH_MARKER_PATTERN = re.compile(r"笑|(?<![A-Za-z])[wW]+(?![A-Za-z])")


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
    clauses = [
        clause.strip()
        for clause in re.split(r"[。.!！?？\n]+", text or "")
        if clause.strip()
    ] or [text or ""]
    tone_clauses = [LAUGH_MARKER_PATTERN.sub("", clause).strip() for clause in clauses]
    formal_pattern = re.compile(
        r"(?:でございます|ございました|ませんでした|ません|ください|お願いします|"
        r"です|ます|でした|ました|ですね|ですか|でしょうか|ますよ)"
        r"(?:よ)?(?:ね)?(?=$|[\s笑w])"
    )
    casual_pattern = re.compile(
        r"(?:ー|〜|っ|だね|だよ|よね|じゃん|かも|かな|行こう|しよう|"
        r"楽しそう|いいな|まじ|ほんと|そうなんだ)(?:\s|$)"
    )
    has_formal = any(formal_pattern.search(clause) for clause in tone_clauses)
    has_casual = any(
        casual_pattern.search(clause)
        or (not formal_pattern.search(clause) and re.search(r"(?:ね|よ)$", clause))
        for clause in tone_clauses
    )

    if has_formal and has_casual:
        return "hybrid"
    elif has_formal:
        return "keigo"
    elif has_casual:
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
    laugh_count = sum(1 for t in texts if LAUGH_MARKER_PATTERN.search(t))

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
    other_contact_texts = [
        p.self_turn.text
        for p in valid_pairs
        if not contact_id or p.contact_id != contact_id
    ]
    # Same-contact Gold is excluded from the prior used to blend that contact back in.
    other_contact_gold_texts = [
        p.self_turn.text
        for p in valid_pairs
        if p.label == "gold" and (not contact_id or p.contact_id != contact_id)
    ]

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
    other_contact_gold_prof = compute_style_metrics(other_contact_gold_texts)
    same_contact_recent_gold_prof = compute_style_metrics(same_contact_recent_gold_texts)
    same_contact_all_gold_prof = compute_style_metrics(same_contact_gold_texts)
    recent_evidence_weight = (
        min(0.5, (same_contact_all_gold_prof.sample_count - 5) / (same_contact_all_gold_prof.sample_count + 5))
        if same_contact_all_gold_prof.sample_count > 5 else 0.0
    )
    same_contact_blended_gold_prof = _blend_style_profiles(
        same_contact_all_gold_prof,
        same_contact_recent_gold_prof,
        recent_evidence_weight,
        same_contact_all_gold_prof.sample_count,
    )
    contact_prof = compute_style_metrics(contact_texts)
    phase_prof = compute_style_metrics(phase_texts)
    recent_prof = compute_style_metrics(recent_texts)

    # 3件未満のsame-contact Goldでは局所適応せず、確認済みGoldだけを安全なfallbackにする。
    # Goldが1件でもある間は、送信済みSilverを含むphase/global styleへ置き換えない。
    # 十分な件数のGoldは局所履歴で置き換えず、Global Goldへ件数に応じて混ぜる。
    same_contact_gold_prof_count = same_contact_all_gold_prof.sample_count
    if same_contact_gold_prof_count >= 3:
        local_weight = min(0.75, same_contact_gold_prof_count / (same_contact_gold_prof_count + 5))
        gold_prior_prof = other_contact_gold_prof if other_contact_gold_prof.sample_count else gold_prof
        active_prof = _blend_style_profiles(gold_prior_prof, same_contact_blended_gold_prof, local_weight, same_contact_gold_prof_count)
        hierarchy_tier = "same_contact_recent_manual_gold"
    elif other_contact_gold_prof.sample_count >= 5:
        active_prof = other_contact_gold_prof
        hierarchy_tier = "global_manual_gold"
    elif other_contact_gold_prof.sample_count:
        active_prof = other_contact_gold_prof
        hierarchy_tier = "sparse_manual_gold_fallback"
    elif other_contact_texts and not gold_prof.sample_count:
        active_prof = compute_style_metrics(other_contact_texts)
        hierarchy_tier = "global"
    elif gold_prof.sample_count:
        # A contact's first one or two Gold replies are not enough to establish
        # either its local style or a user-wide prior. Keep the neutral profile
        # instead of letting those replies define the global fallback.
        active_prof = compute_style_metrics([])
        hierarchy_tier = "sparse_manual_gold_fallback"
    elif (
        not same_contact_gold_prof_count
        and len(same_contact_silver_texts) >= 3
    ):
        active_prof = compute_style_metrics(same_contact_silver_texts)
        hierarchy_tier = "same_contact_sent_silver"
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
        "other_contact_gold_profile": other_contact_gold_prof,
        "same_contact_recent_gold_profile": same_contact_recent_gold_prof,
        "same_contact_all_gold_profile": same_contact_all_gold_prof,
        "same_contact_blended_gold_profile": same_contact_blended_gold_prof,
        "contact_recency_weight": recent_evidence_weight,
        "contact_adaptation_weight": (
            min(0.75, same_contact_all_gold_prof.sample_count / (same_contact_all_gold_prof.sample_count + 5))
            if same_contact_all_gold_prof.sample_count >= 3 else 0.0
        ),
        "contact_profile": contact_prof,
        "phase_profile": phase_prof,
        "recent_profile": recent_prof,
        "total_self_messages": len(all_self_texts),
        "gold_samples": len(gold_texts),
        "contact_samples": len(contact_texts),
        "same_contact_gold_samples": len(same_contact_gold_texts),
    }


def _blend_style_profiles(base: StyleProfile, local: StyleProfile, weight: float, sample_count: int) -> StyleProfile:
    """Blend local evidence into the global Gold prior without an abrupt style switch."""
    weight = max(0.0, min(0.75, weight))
    base_values = dataclasses.asdict(base)
    local_values = dataclasses.asdict(local)
    blended: dict[str, Any] = {}
    for item in dataclasses.fields(StyleProfile):
        name = item.name
        if name == "sample_count":
            blended[name] = sample_count
        elif name == "frequent_emojis":
            if not base.sample_count:
                blended[name] = local_values[name]
            elif not local.sample_count or weight <= 0:
                blended[name] = base_values[name]
            else:
                blended[name] = _blend_ranked_emojis(
                    base_values[name], local_values[name], weight
                )
        elif name == "first_person":
            blended[name] = base_values[name]
        else:
            value = base_values[name] * (1 - weight) + local_values[name] * weight
            blended[name] = round(value) if isinstance(base_values[name], int) else round(value, 2)
    return StyleProfile(**blended)


def _blend_ranked_emojis(base: list[str], local: list[str], weight: float) -> list[str]:
    """Merge ranked emoji preferences so newer signals enter without replacing all history."""
    scores: dict[str, float] = {}
    stable_order: dict[str, int] = {}
    for preferences, source_weight in ((base, 1 - weight), (local, weight)):
        for rank, emoji in enumerate(preferences):
            if emoji not in scores:
                scores[emoji] = 0.0
                stable_order[emoji] = len(stable_order)
            scores[emoji] += source_weight / (rank + 1)

    return sorted(scores, key=lambda emoji: (-scores[emoji], stable_order[emoji]))[:5]


def infer_contact_tone(hierarchical: dict[str, Any], requested_tone: str = "") -> str:
    """Only an explicit user selection locks register; learned tone stays advisory."""
    del hierarchical  # Gold-derived style is applied through the learned style prompt.
    return requested_tone


def to_learned_policy_prompt(
    hierarchical: dict[str, Any], *, suppress_question_rate: bool = False
) -> str:
    """階層プロファイルからプロンプト用ポリシーブロックを生成する。"""
    p: StyleProfile = hierarchical.get("active_profile", StyleProfile())
    tier = hierarchical.get("hierarchy_tier", "global")
    sample_count = p.sample_count
    same_contact_gold_samples = int(hierarchical.get("same_contact_gold_samples", 0) or 0)

    # 少数比率でも一方に決めつけず、混在傾向をそのまま表す。
    if p.keigo_ratio >= 0.65:
        tone_desc = f"丁寧な会話敬語が中心（敬語率{int(p.keigo_ratio*100)}%）"
    elif p.tame_ratio >= 0.65:
        tone_desc = f"親しみやすいカジュアル口調が中心（タメ口率{int(p.tame_ratio*100)}%）"
    elif p.hybrid_ratio >= 0.45:
        tone_desc = f"丁寧さと砕けた語尾が混ざる会話調（混合率{int(p.hybrid_ratio*100)}%）"
    else:
        tone_desc = (
            "丁寧な表現と砕けた表現が混在する会話調"
            f"（敬語{int(p.keigo_ratio*100)}%・混合{int(p.hybrid_ratio*100)}%・タメ口{int(p.tame_ratio*100)}%）"
        )

    punct_desc = "句点『。』はほぼ使わず" if p.period_ratio < 0.2 else "適度に句点『。』を使用し"
    laugh_desc = f"『笑』の使用率約{int(p.laugh_ratio*100)}%"
    emoji_desc = f"1通あたり平均{p.emoji_avg_count}個（頻出: {' '.join(p.frequent_emojis)}）"
    contact_adaptation = ""
    contact_length_guidance = ""
    if tier == "same_contact_recent_manual_gold":
        contact_adaptation = (
            f"- 同一相手Gold: {hierarchical.get('same_contact_gold_samples', 0)}件の本人手入力傾向を"
            f"Global Goldへ段階的に反映（重み{hierarchical.get('contact_adaptation_weight', 0):.2f}）。\n"
        )
        if same_contact_gold_samples >= 5:
            contact_length_guidance = (
                " 同一相手Goldが十分ある場合は短さを一律に優先せず、"
                "今回の内容に必要な返信量をその文量分布に合わせる。"
            )

    return (
        f"【LEARNED USER RESPONSE POLICY】\n【USER LEARNED STYLE PROFILE】（採用階層: {tier} / 学習サンプル数: {sample_count}件）\n"
        f"ユーザー本人が実際に送信してきたメッセージ実績から抽出した文体・構造ポリシーです。\n"
        f"{contact_adaptation}"
        f"- 口調・トーン: {tone_desc}\n"
        f"- 文量・構成: 1通あたり中央値{p.char_median}文字（IQR: {p.char_p25}〜{p.char_p75}文字）、中央値{p.line_median}行・{p.sent_median}文。{contact_length_guidance}\n"
        f"- 記号・絵文字: {punct_desc}、『！』を多用。{laugh_desc}。絵文字は{emoji_desc}\n"
        f"- 会話構造: 相手の発言への自然な反応を最優先し、質問・深掘り・自己開示は会話上の意味があり相手が答えやすい場合に使う。会話を続けるためだけの質問はしない。質問なし・短い返信も正常だが、返信量は本人実績の文量分布と現在の内容から決める。一人称は『{p.first_person}』。相手の名前が履歴や設定で確認でき、呼ぶのが自然な場合だけ名前を使い、未確認の名前や呼びかけは作らない。\n"
        f"- 避ける表現: 会話の相手に向けた返信として形式的すぎる引用・説明口調。特定の語句を一律に禁止せず、会話とのつながりを見て自然な表現を選ぶ。\n"
        f"- 基本姿勢: 固定された画一ルールではなく、本人の実際の実績スタイル・テンポを最上位の正解として反映すること"
    )


def build_same_contact_gold_pairs_block(contact_id: int, limit: int = 10) -> str:
    """同じ相手に対するユーザーの直近手入力返信（Goldペア）の原文ブロックを構築する。"""
    gold_pairs = corpus.extract_same_contact_manual_gold_pairs(contact_id, limit=limit)
    if not gold_pairs:
        return ""

    lines = [f"【SAME-CONTACT RECENT GOLD REPLIES】（この相手へユーザーが実際に手入力した直近の返信実例）"]
    lines.append(
        "※実例は言葉や内容を写すためではなく、本人の返信テンポ・構成・距離感を知る補助資料です。"
        "今回の文脈と統合済みスタイル傾向を優先し、表現をそのまま再利用しないこと:"
    )
    for idx, p in enumerate(gold_pairs, start=1):
        lines.append(f"[実例 {idx}]\n相手: {p.contact_turn.text}\n自分（手入力正解）: {p.self_turn.text}")
    return "\n".join(lines)


def build_relationship_summary(
    contact_id: int | None,
    requested_tone: str = "",
    *,
    suppress_question_guidance: bool = False,
) -> str:
    """同一相手への返信距離感サマリー（Step 18）。観測特徴のみを記述し、関係ラベルは付けない。

    Same-contact Gold（手入力実績）から本人がその相手へ返すときの距離感・温度感・
    フォーマル度・文量・質問率を観測し、短い抽象ブロックとして返す。相手の文体を
    コピーするためのものではなく、本人の返信特徴を学習するためのもの。
    実績0件時は空文字（Global へ fallback）。少数時は参考程度と明記する。
    """
    if not contact_id:
        return ""
    gold_pairs = corpus.extract_same_contact_manual_gold_pairs(contact_id, limit=10)
    texts = [p.self_turn.text for p in gold_pairs if not p.excluded]
    if len(texts) < 3:
        return ""
    n = len(texts)
    hierarchy = compute_hierarchical_profile(contact_id)
    prof: StyleProfile = hierarchy["same_contact_blended_gold_profile"]
    global_gold: StyleProfile = hierarchy["other_contact_gold_profile"]

    # フォーマル度（観測のみ）
    if prof.tame_ratio >= 0.75:
        formality = "砕けた"
    elif prof.keigo_ratio >= 0.75:
        formality = "丁寧"
    else:
        formality = "丁寧さと砕け具合が混在"
    tone_balance_guidance = ""
    if n >= 5 and prof.hybrid_ratio + prof.tame_ratio >= 0.45 and prof.hybrid_ratio >= prof.tame_ratio + 0.08:
        tone_balance_guidance = (
            "自動口調では本人Goldの混合スタイルを基本にし、一般的な敬語だけの表現へ一律に寄せない。"
            "内容や場面に合う範囲で丁寧さと本人らしい会話調を混ぜる。比率や候補数は固定しない。"
        )
    elif n >= 5 and prof.hybrid_ratio + prof.tame_ratio >= 0.45 and prof.tame_ratio >= prof.hybrid_ratio + 0.08:
        tone_balance_guidance = (
            "自動口調では本人Goldの砕けた会話調を基本にし、一般的な丁寧語だけへ一律に寄せない。"
            "内容や場面に合う丁寧さは保ちつつ、無理な口調変化や比率・候補数の固定は避ける。"
        )
    # 温度感（笑・絵文字・感嘆符の観測値から。名前による固定なし）
    warm_score = prof.laugh_ratio + min(prof.emoji_avg_count, 2.0) / 2.0 + prof.exclamation_ratio
    if warm_score >= 1.2:
        warmth = "とても温かい"
    elif warm_score >= 0.7:
        warmth = "温かい"
    elif warm_score <= 0.2:
        warmth = "そっけない"
    else:
        warmth = "普通"
    # 文量（中央値・行数）
    length_delta = prof.char_median - global_gold.char_median if global_gold.sample_count else 0
    if length_delta >= 5:
        brevity = "Global Goldより相対的に長め"
    elif length_delta <= -5:
        brevity = "Global Goldより相対的に短め"
    else:
        brevity = "Global Goldと同程度"
    length_guidance = ""
    if n >= 5:
        length_guidance = (
            "本人Goldの文量分布を今回の返信量に反映する。相手の発言内容とGoldの返信例から必要な厚みを選び、"
            "状態や気持ちの共有には短い労いだけで毎回終えず、今回の内容に沿う感想・共感も検討する。"
            "短い挨拶・受領・終了は簡潔でよい。確認できない行動や結果を足さず、相手の発言を言い換えて水増ししたりしない。質問で長さを作らない。"
            "固定文字数・Gold語句のコピーはしない。"
        )
    if n >= 5 and 0.45 <= prof.laugh_ratio < 0.85:
        length_guidance += (
            "笑い表現の頻度は候補全体の参考にとどめ、Goldの頻度感を自然な範囲で反映する。"
            "文脈に合わない案や候補数の調整目的で笑を足さない。"
        )
    if n >= 6 and global_gold.sample_count >= 5 and length_delta >= 5:
        relative_length = (
            "かなり長め"
            if prof.char_median >= global_gold.char_median * 1.4
            else "長め"
        )
        length_guidance += (
            f"Global Goldより{relative_length}に返す傾向がある。Goldの文量分布も参考にする。今回の内容に必要な返信量を選ぶ。"
        )
    if length_guidance:
        length_guidance += "候補間で自然な違いが作れる場合だけ反応の焦点を変え、似た案が自然なら無理に変えない。"
    confidence = f"（この相手の手入力Gold {n}件をGlobal Goldに段階的に反映）"
    tone_guidance = ""
    explicit_tone_label = {"keigo": "敬語", "hybrid": "ハイブリッド", "tame": "タメ口"}.get(
        requested_tone
    )
    if explicit_tone_label:
        tone_balance_guidance = ""
        tone_guidance = (
            f"今回の明示トーン指定（{explicit_tone_label}）を最優先し、"
            "相手別の混在傾向を理由に別の口調を混ぜない。"
        )
    elif formality == "丁寧さと砕け具合が混在":
        tone_guidance = (
            f"丁寧・混合・砕けた文体の実績比率は{int(prof.keigo_ratio * 100)}%・{int(prof.hybrid_ratio * 100)}%・{int(prof.tame_ratio * 100)}%。"
            "この比率は候補数の目標ではなく、今回の話題に自然な本人らしさを選ぶ参考にする。"
        )
        if n < 5:
            tone_guidance += (
                "同一相手Goldが3〜4件のため、これはまだ暫定的な傾向です。"
                "Global Goldの口調を基準にし、この少数例だけを理由に距離感を切り替えないでください。"
            )
        elif prof.hybrid_ratio + prof.tame_ratio >= 0.45 and prof.hybrid_ratio >= prof.tame_ratio + 0.08:
            tone_guidance += (
                "自動口調ではGoldの混合スタイルを基本にし、一般的な敬語だけの候補群へ一律に寄せない。"
                "場面に合う範囲で丁寧さと本人らしい会話調を混ぜる。無理な口調変更や比率の固定はしない。"
                "笑や絵文字だけでは口調適応と見なさない。"
            )
        elif prof.hybrid_ratio + prof.tame_ratio >= 0.45 and prof.tame_ratio >= prof.hybrid_ratio + 0.08:
            tone_guidance += (
                "自動口調ではGoldの砕けた会話調を基本にし、候補群を一律の敬語にそろえない。"
                "内容に合う丁寧さは保ち、無理な口調変更や比率の固定はしない。笑や絵文字だけでは口調適応と見なさない。"
            )
        elif prof.hybrid_ratio + prof.tame_ratio >= 0.45:
            tone_guidance += (
                "自動口調ではGoldにある丁寧さと会話調の混ざり方を基本にする。"
                "候補群を一般的な敬語だけにそろえず、特定の案数や比率は目標にしない。"
                "笑や絵文字だけでは口調適応と見なさない。"
            )
        else:
            tone_guidance += (
                "Goldに会話調の実績がある場合は、今回の話題に合う範囲でその距離感も候補に反映する。"
            )
        tone_guidance += (
            "事務連絡や深刻な話題など砕けると不自然な場面では無理に崩さず、"
            "同じ丁寧さの言い換えだけで3案を埋めない。"
        )
    question_guidance = "質問は会話上必要な場合だけ使う。"
    return (
        f"＜この相手への返信距離感＞{confidence}\n"
        f"- 距離感: {formality}・{warmth}（笑い{'多め' if prof.laugh_ratio >= 0.3 else '少なめ'}・{brevity}）。"
        f"同一相手Goldの文量中央値は{prof.char_median}字、Global Goldは{global_gold.char_median}字。{length_guidance}"
        f"{tone_guidance}{tone_balance_guidance}返信の長さはこの差も参考にしつつ、現在の会話内容に合う範囲で決めること。"
        f"本人のGold実例と現在の会話内容を優先し、{question_guidance}"
    )
