from __future__ import annotations

from backend.app.learning import corpus
from scripts.benchmark_retrieval_quality import RetrievalCase, evaluate_ranked_results, run_synthetic_benchmark


def _pair(
    pair_id: str,
    contact_text: str,
    self_text: str,
    *,
    label: str = "gold",
    contact_id: int = 1,
    phase: str = "ongoing",
    excluded: bool = False,
) -> corpus.ReplyPair:
    return corpus.ReplyPair(
        pair_id=pair_id,
        contact_id=contact_id,
        contact_turn=corpus.Turn(contact_id, "contact", [], contact_text, ""),
        self_turn=corpus.Turn(contact_id, "self", [], self_text, ""),
        context_turns=[],
        phase=phase,
        label=label,
        source="manual",
        excluded=excluded,
    )


def test_rank_metrics_average_recall_and_mrr() -> None:
    cases = [
        RetrievalCase("found-first", "query", {"a", "b"}),
        RetrievalCase("found-second", "query", {"c"}),
        RetrievalCase("miss", "query", {"d"}),
    ]

    metrics = evaluate_ranked_results(
        cases,
        {"found-first": ["a", "b", "x"], "found-second": ["c", "x"], "miss": ["x"]},
        k=2,
    )

    assert metrics["recall_at_k"] == 2 / 3
    assert metrics["mrr"] == (1 + 1 + 0) / 3
    assert metrics["queries_with_hit"] == 2


def test_metrics_are_zero_for_empty_rankings_and_empty_benchmark() -> None:
    case = RetrievalCase("no-hit", "unrelated", {"gold"})

    metrics = evaluate_ranked_results([case], {"no-hit": []}, k=4)
    assert metrics["recall_at_k"] == 0
    assert metrics["mrr"] == 0
    assert metrics["queries_with_hit"] == 0

    empty = evaluate_ranked_results([], {}, k=4)
    assert empty["recall_at_k"] == 0
    assert empty["mrr"] == 0


def test_benchmark_excludes_negative_and_excluded_pairs_and_has_safe_no_hit() -> None:
    pairs = [
        _pair("gold-cafe", "昨日カフェに行ったよ", "いいね、どんなカフェだった？"),
        _pair("negative-cafe", "昨日カフェに行ったよ", "それは最悪だったね", label="negative"),
        _pair("excluded-cafe", "昨日カフェに行ったよ", "いいね！", excluded=True),
    ]
    result = run_synthetic_benchmark(
        pairs=pairs,
        cases=[
            RetrievalCase("cafe_paraphrase", "昨日カフェに行ったよ", {"gold-cafe"}, 1, "ongoing"),
            RetrievalCase("no_match", "宇宙旅行が楽しみ", frozenset(), 1, "scheduling"),
        ],
        k=4,
    )

    assert result["negative_leakage_count"] == 0
    assert "negative-cafe" not in result["ranked_ids"]["cafe_paraphrase"]
    assert "excluded-cafe" not in result["ranked_ids"]["cafe_paraphrase"]
    assert result["no_relevant_query_count"] == 1
    assert result["no_relevant_query_return_count"] == 0
    assert result["empty_corpus_fallback"]["ranked_ids"] == []
    assert result["empty_corpus_fallback"]["prompt_block"] == ""
