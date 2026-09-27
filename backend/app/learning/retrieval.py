"""2段階リトリーバルモジュール（Retrieval & Ranking v3）。

Phase一致、トピック類似度、Gold/Silver品質重み、同一相手ボーナスにより、
現在の会話に最も適した高品質な返信ペア（Positive Pairs）を厳選する。
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

from . import corpus, contrast

# 一般的なストップワード
STOP_WORDS = {
    "こと", "もの", "ため", "よう", "そう", "これ", "それ", "あれ", "どこ", "なに", "なん",
    "です", "ます", "でした", "ました", "ですね", "ですか", "ある", "いる", "する", "なる",
    "いい", "思う", "自分", "相手", "今日", "昨日", "明日", "最近", "今回", "はじめ", "初め",
    "よろしく", "お願い", "はじめまして", "初めまして", "さん", "笑", "w",
}


def _extract_ngrams(text: str, n: int = 2) -> set[str]:
    """テキストから文字 n-gram を抽出する。"""
    cleaned = re.sub(r"[\s\d\W_]+", "", text.lower())
    if len(cleaned) < n:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def _extract_topic_keywords(text: str) -> set[str]:
    """テキストから意味のあるトピックキーワードを抽出する。"""
    tokens = re.findall(r"[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}|[\u30a1-\u30f6]{2,}", text)
    return {t for t in tokens if t not in STOP_WORDS and len(t) >= 2}


def score_pair_relevance(
    query_text: str,
    pair: corpus.ReplyPair,
    target_contact_id: int | None,
    target_phase: corpus.ConversationPhase,
) -> float:
    """クエリに対するペアの関連度スコア（0.0〜2.0）を算出する。"""
    if pair.excluded or pair.label == "negative":
        return -1.0

    pair_context = f"{pair.contact_turn.text} {pair.self_turn.text}"

    # 1. 文字 bi-gram Jaccard 類似度
    q_ngrams = _extract_ngrams(query_text, 2)
    p_ngrams = _extract_ngrams(pair_context, 2)
    if not q_ngrams or not p_ngrams:
        jaccard = 0.0
    else:
        inter = len(q_ngrams & p_ngrams)
        union = len(q_ngrams | p_ngrams)
        jaccard = inter / union if union > 0 else 0.0

    # 2. トピックキーワード一致ボーナス
    q_topics = _extract_topic_keywords(query_text)
    p_topics = _extract_topic_keywords(pair_context)
    topic_matches = len(q_topics & p_topics)
    topic_bonus = min(0.5, topic_matches * 0.25)

    # 3. Source 品質重み
    source_weight = 1.0 if pair.label == "gold" else (0.6 if pair.label == "silver" else 0.3)

    # 4. Same-contact ボーナス
    contact_bonus = 0.2 if target_contact_id and pair.contact_id == target_contact_id else 0.0

    # 5. Phase 一致 / 不一致
    phase_modifier = 0.15 if pair.phase == target_phase else -0.20

    base_score = (jaccard * 0.4) + topic_bonus + (source_weight * 0.3) + contact_bonus + phase_modifier
    return max(0.0, base_score)


def retrieve_relevant_pairs(
    query_text: str,
    contact_id: int | None,
    current_phase: corpus.ConversationPhase = "ongoing",
    limit: int = 4,
) -> list[dict[str, Any]]:
    """現在のクエリ・相手・フェーズに最も合致する Positive Reply Pairs を検索する。"""
    all_pairs = corpus.extract_reply_pairs()
    candidates: list[tuple[float, corpus.ReplyPair]] = []

    for p in all_pairs:
        # Leave-one-out: 直近かつ同一のクエリテキスト自体は除外
        if query_text.strip() and query_text.strip() in p.contact_turn.text:
            continue
        score = score_pair_relevance(query_text, p, contact_id, current_phase)
        if score > 0:
            candidates.append((score, p))

    # スコア降順ソート
    candidates.sort(key=lambda x: x[0], reverse=True)

    results: list[dict[str, Any]] = []
    seen_texts: set[str] = set()

    for score, p in candidates:
        if len(results) >= limit:
            break
        self_clean = p.self_turn.text.strip()
        if self_clean in seen_texts:
            continue
        seen_texts.add(self_clean)

        results.append({
            "pair_id": p.pair_id,
            "contact_id": p.contact_id,
            "score": round(score, 3),
            "label": p.label,
            "phase": p.phase,
            "source": p.source,
            "contact_text": p.contact_turn.text.strip(),
            "self_text": self_clean,
            "is_same_contact": (p.contact_id == contact_id) if contact_id else False,
        })

    return results


def to_positive_pairs_prompt_block(retrieved_pairs: list[dict[str, Any]]) -> str:
    """検索された正例ペアからプロンプト用のブロックを生成する。"""
    if not retrieved_pairs:
        return ""

    lines = [
        "【POSITIVE REPLY PAIRS】（会話実績から検索されたユーザー本人の好例返信）",
        "過去の類似会話でユーザーが実際に送信した返信です。最優先の文体・展開の教師として参照してください:",
    ]
    for i, p in enumerate(retrieved_pairs, 1):
        scope = "同相手" if p["is_same_contact"] else "他相手"
        lines.append(
            f"実例{i} [{p['label'].upper()} / {scope} / Phase: {p['phase']}]:\n"
            f"  相手: 「{p['contact_text'][:80]}」\n"
            f"  自分: 「{p['self_text']}」"
        )
    return "\n".join(lines)
