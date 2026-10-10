"""Small, deterministic baseline benchmark for the existing lexical RAG.

The bundled examples are synthetic probes, not user data and not a claim about
real-world Japanese retrieval quality. No model, network, or embedding service
is used. Run with ``python scripts/benchmark_retrieval_quality.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence
from unittest.mock import patch

# Support both module and direct-path execution from arbitrary working folders.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.learning import corpus, retrieval


@dataclass(frozen=True)
class RetrievalCase:
    """A synthetic query and manually labeled relevant reply-pair IDs."""

    case_id: str
    query_text: str
    relevant_pair_ids: frozenset[str]
    contact_id: int | None = None
    phase: corpus.ConversationPhase = "ongoing"
    probe_tags: tuple[str, ...] = ()


def _turn(contact_id: int, sender: str, text: str) -> corpus.Turn:
    return corpus.Turn(
        contact_id=contact_id,
        sender=sender,  # type: ignore[arg-type]
        messages=[],
        text=text,
        created_at="",
    )


def _pair(
    pair_id: str,
    contact_id: int,
    contact_text: str,
    self_text: str,
    *,
    label: corpus.PairLabel = "gold",
    phase: corpus.ConversationPhase = "ongoing",
    excluded: bool = False,
) -> corpus.ReplyPair:
    return corpus.ReplyPair(
        pair_id=pair_id,
        contact_id=contact_id,
        contact_turn=_turn(contact_id, "contact", contact_text),
        self_turn=_turn(contact_id, "self", self_text),
        context_turns=[],
        phase=phase,
        label=label,
        source="manual" if label == "gold" else "generated",
        excluded=excluded,
    )


def synthetic_dataset() -> tuple[list[corpus.ReplyPair], list[RetrievalCase]]:
    """Return a deliberately small labeled probe set spanning key retrieval axes."""
    pairs = [
        _pair("cafe_visit_gold", 11, "昨日カフェに行ったよ", "いいね、どんなお店だった？"),
        _pair("cafe_preference_gold", 11, "カフェって好き？", "好きだよ、落ち着くところがいいよね"),
        _pair("cafe_schedule_gold", 22, "今週末カフェ行かない？", "ぜひ！土曜なら空いてるよ", phase="scheduling"),
        _pair("cafe_decoy_silver", 33, "カフェでコーヒー飲んだ", "コーヒー好きなんだよね", label="silver"),
        _pair("work_support_gold", 11, "仕事で疲れちゃった", "おつかれ！今日はゆっくり休んでね"),
        _pair("work_question_gold", 44, "仕事って何してるの？", "事務の仕事をしてるよ"),
        _pair("opening_gold", 55, "はじめまして！よろしくね", "はじめまして、マッチありがとう！", phase="opening"),
        _pair("cafe_negative", 11, "昨日カフェに行ったよ", "それは最悪だったね", label="negative"),
        _pair("cafe_excluded", 11, "昨日カフェに行ったよ", "カフェカフェカフェ", excluded=True),
        _pair("stale_bronze", 66, "山の景色がきれいだった", "山を歩くの気持ちいいよね", label="bronze", phase="ongoing"),
    ]
    cases = [
        RetrievalCase(
            "cafe_paraphrase",
            "昨日、喫茶店でのんびりしたよ",
            frozenset({"cafe_visit_gold"}),
            11,
            "ongoing",
            ("paraphrase", "same_contact", "gold"),
        ),
        RetrievalCase(
            "cafe_preference_intent",
            "カフェは好き？",
            frozenset({"cafe_preference_gold"}),
            11,
            "ongoing",
            ("intent", "same_contact", "gold"),
        ),
        RetrievalCase(
            "cafe_scheduling_phase",
            "週末にカフェ行こうよ",
            frozenset({"cafe_schedule_gold"}),
            22,
            "scheduling",
            ("phase", "same_contact", "gold"),
        ),
        RetrievalCase(
            "work_support_role",
            "仕事でへとへと",
            frozenset({"work_support_gold"}),
            11,
            "ongoing",
            ("paraphrase", "intent_role", "same_contact"),
        ),
        RetrievalCase(
            "opening_phase",
            "はじめまして、マッチありがとう！",
            frozenset({"opening_gold"}),
            55,
            "opening",
            ("phase", "gold"),
        ),
        RetrievalCase(
            "no_match",
            "宇宙旅行が楽しみ",
            frozenset(),
            11,
            "scheduling",
            ("no_hit", "safe_fallback"),
        ),
    ]
    return pairs, cases


def evaluate_ranked_results(
    cases: Sequence[RetrievalCase],
    ranked_ids: Mapping[str, Sequence[str]],
    *,
    k: int,
) -> dict[str, int | float]:
    """Compute macro Recall@k and MRR; empty relevance sets are excluded."""
    if k < 1:
        raise ValueError("k must be at least 1")
    scored_cases = [case for case in cases if case.relevant_pair_ids]
    if not scored_cases:
        return {"recall_at_k": 0.0, "mrr": 0.0, "queries_with_hit": 0, "query_count": 0}

    recall_sum = 0.0
    reciprocal_rank_sum = 0.0
    queries_with_hit = 0
    for case in scored_cases:
        ranking = list(ranked_ids.get(case.case_id, ()))
        hits_in_k = len(set(ranking[:k]) & case.relevant_pair_ids)
        recall_sum += hits_in_k / len(case.relevant_pair_ids)
        first_relevant_rank = next(
            (index for index, pair_id in enumerate(ranking, start=1) if pair_id in case.relevant_pair_ids),
            None,
        )
        if first_relevant_rank is not None:
            queries_with_hit += 1
            reciprocal_rank_sum += 1 / first_relevant_rank

    count = len(scored_cases)
    return {
        "recall_at_k": recall_sum / count,
        "mrr": reciprocal_rank_sum / count,
        "queries_with_hit": queries_with_hit,
        "query_count": count,
    }


def run_synthetic_benchmark(
    *,
    pairs: Sequence[corpus.ReplyPair] | None = None,
    cases: Sequence[RetrievalCase] | None = None,
    k: int = 4,
) -> dict[str, Any]:
    """Evaluate the production lexical ranker against a local synthetic corpus."""
    default_pairs, default_cases = synthetic_dataset()
    benchmark_pairs = list(default_pairs if pairs is None else pairs)
    benchmark_cases = list(default_cases if cases is None else cases)
    if k < 1:
        raise ValueError("k must be at least 1")

    ranked_results: dict[str, list[dict[str, Any]]] = {}
    ranked_ids: dict[str, list[str]] = {}
    fallback_blocks: dict[str, str] = {}
    negative_leakage_count = 0
    retrieved_count = 0

    # Inject only the local probe corpus while exercising the real retrieval
    # entry point. The patch is scoped and restored even if scoring raises.
    with patch.object(retrieval.corpus, "extract_reply_pairs", return_value=benchmark_pairs):
        for case in benchmark_cases:
            results = retrieval.retrieve_relevant_pairs(
                case.query_text,
                case.contact_id,
                current_phase=case.phase,
                limit=k,
            )
            ranked_results[case.case_id] = results
            ranked_ids[case.case_id] = [str(item["pair_id"]) for item in results]
            fallback_blocks[case.case_id] = retrieval.to_positive_pairs_prompt_block(results)
            retrieved_count += len(results)
            negative_leakage_count += sum(
                item.get("label") == "negative" or item.get("excluded", False)
                for item in results
            )

    # Verify the actual empty-corpus path independently of the synthetic
    # retrieval results. This checks graceful no-example prompt construction;
    # it does not imply that the scorer abstains on unrelated eligible pairs.
    with patch.object(retrieval.corpus, "extract_reply_pairs", return_value=[]):
        empty_results = retrieval.retrieve_relevant_pairs(
            "未登録の話題です",
            contact_id=None,
            current_phase="ongoing",
            limit=k,
        )
    empty_fallback = {
        "ranked_ids": [str(item["pair_id"]) for item in empty_results],
        "prompt_block": retrieval.to_positive_pairs_prompt_block(empty_results),
    }
    no_relevant_queries = [case for case in benchmark_cases if not case.relevant_pair_ids]
    no_relevant_query_return_count = sum(bool(ranked_ids[case.case_id]) for case in no_relevant_queries)

    return {
        "benchmark": "synthetic_japanese_short_chat_v1",
        "scope": "Synthetic diagnostic only; not representative of real users or sendability.",
        "retrieval_method": "existing lexical character-bigram/topic/metadata scorer",
        "k": k,
        **evaluate_ranked_results(benchmark_cases, ranked_ids, k=k),
        "negative_leakage_count": int(negative_leakage_count),
        "negative_leakage_rate": negative_leakage_count / retrieved_count if retrieved_count else 0.0,
        "no_relevant_query_count": len(no_relevant_queries),
        "no_relevant_query_return_count": no_relevant_query_return_count,
        "empty_corpus_fallback": empty_fallback,
        "ranked_ids": ranked_ids,
        "ranked_results": ranked_results,
        "fallback_blocks": fallback_blocks,
        "cases": [
            {
                "case_id": case.case_id,
                "query_text": case.query_text,
                "relevant_pair_ids": sorted(case.relevant_pair_ids),
                "probe_tags": list(case.probe_tags),
                "hit": bool(set(ranked_ids[case.case_id]) & case.relevant_pair_ids),
            }
            for case in benchmark_cases
        ],
        "limitations": [
            "Small hand-authored synthetic set; labels are probe expectations, not user judgments.",
            "No embedding comparison, human relevance study, or API generation is performed.",
            "A successful synthetic score is not evidence of sendable replies or production-wide quality.",
        ],
    }


def main() -> None:
    print(json.dumps(run_synthetic_benchmark(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
