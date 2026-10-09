"""Compare lexical, local-dense, and rank-fusion retrieval on synthetic probes.

This diagnostic downloads only public model weights. Probe text is synthetic;
the tool never reads the user's conversation corpus or sends text to a service.
Run with ``python scripts/benchmark_embedding_retrieval.py`` after installing
the optional ``sentence-transformers`` package.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any, Callable, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.learning import corpus, retrieval
from scripts.benchmark_retrieval_quality import (
    RetrievalCase,
    evaluate_ranked_results,
    synthetic_dataset,
)

DEFAULT_MODEL = "intfloat/multilingual-e5-small"
DEFAULT_REVISION = "5697a65b0a002a92fe8c4fc9d495303ffff9c7d2"


def _make_pair(
    pair_id: str,
    contact_id: int,
    contact_text: str,
    self_text: str,
    *,
    phase: corpus.ConversationPhase = "ongoing",
    label: corpus.PairLabel = "gold",
) -> corpus.ReplyPair:
    return corpus.ReplyPair(
        pair_id=pair_id,
        contact_id=contact_id,
        contact_turn=corpus.Turn(contact_id, "contact", [], contact_text, ""),
        self_turn=corpus.Turn(contact_id, "self", [], self_text, ""),
        context_turns=[],
        phase=phase,
        label=label,
        source="manual" if label == "gold" else "generated",
        excluded=False,
    )


def extended_synthetic_dataset() -> tuple[list[corpus.ReplyPair], list[RetrievalCase]]:
    """Add short paraphrases and intent probes to the original diagnostic set."""
    pairs, cases = synthetic_dataset()
    cases = [
        replace(case, query_text="来月から陶芸を始めるんだ") if case.case_id == "no_match" else case
        for case in cases
    ]
    pairs.extend([
        _make_pair("soft_decline_gold", 77, "今週末は会える？", "今週は予定があって難しいけど、また誘ってね", phase="scheduling"),
        _make_pair("compliment_gold", 88, "その服すごく似合ってるね", "ありがとう！そう言ってもらえるとうれしい"),
        _make_pair("travel_experience_gold", 99, "旅行先で海を見たよ", "いいね、どこに行ったの？"),
    ])
    cases.extend([
        RetrievalCase(
            "fatigue_paraphrase_no_overlap",
            "今日はくたくた",
            frozenset({"work_support_gold"}),
            11,
            "ongoing",
            ("paraphrase", "support_role", "no_lexical_overlap"),
        ),
        RetrievalCase(
            "soft_decline_intent_no_overlap",
            "ちょっと今週は厳しいかも",
            frozenset({"soft_decline_gold"}),
            77,
            "scheduling",
            ("intent", "scheduling", "no_lexical_overlap"),
        ),
        RetrievalCase(
            "compliment_response_role",
            "その服かわいいね",
            frozenset({"compliment_gold"}),
            88,
            "ongoing",
            ("intent_role", "paraphrase"),
        ),
        RetrievalCase(
            "travel_topic_decoy",
            "旅先で海を見てきた",
            frozenset({"travel_experience_gold"}),
            99,
            "ongoing",
            ("paraphrase", "same_contact", "hard_negative"),
        ),
    ])
    return pairs, cases


def _encode_texts(model: Any, texts: Sequence[str], *, role: str) -> np.ndarray:
    """Encode E5 retrieval inputs with the model-card-required role prefixes."""
    prefixed = [f"{role}: {text}" for text in texts]
    vectors = model.encode(prefixed, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vectors, dtype=np.float32)


def rank_candidates(
    pairs: Sequence[corpus.ReplyPair],
    cases: Sequence[RetrievalCase],
    *,
    encode: Callable[[Sequence[str], str], np.ndarray],
    k: int = 4,
) -> dict[str, Any]:
    """Score three retrieval modes without mutating production retrieval."""
    if k < 1:
        raise ValueError("k must be at least 1")

    eligible = [pair for pair in pairs if not pair.excluded and pair.label != "negative"]
    # A reply pair is represented by the counterpart's turn. The reply text is
    # intentionally omitted so matching an answer phrase cannot masquerade as
    # evidence that its preceding message is relevant.
    passages = [pair.contact_turn.text for pair in eligible]
    passage_vectors = encode(passages, "passage") if passages else np.empty((0, 0))
    query_vectors = encode([case.query_text for case in cases], "query") if cases else np.empty((0, 0))

    rankings: dict[str, dict[str, list[str]]] = {"lexical": {}, "dense": {}, "hybrid_rrf": {}}
    full_rankings: dict[str, dict[str, list[str]]] = {"lexical": {}, "dense": {}, "hybrid_rrf": {}}
    detail: dict[str, dict[str, list[dict[str, Any]]]] = {name: {} for name in rankings}
    similarity_by_case: dict[str, dict[str, float]] = {}

    for case_index, case in enumerate(cases):
        lexical_scored = [
            (retrieval.score_pair_relevance(case.query_text, pair, case.contact_id, case.phase), pair)
            for pair in eligible
        ]
        lexical_sorted = sorted(
            ((score, pair) for score, pair in lexical_scored if score > 0),
            key=lambda item: item[0], reverse=True,
        )
        dense_scores = (
            passage_vectors @ query_vectors[case_index]
            if len(eligible) else np.empty((0,), dtype=np.float32)
        )
        dense_sorted = sorted(
            zip((float(score) for score in dense_scores), eligible),
            key=lambda item: item[0], reverse=True,
        )

        lexical_rank = {pair.pair_id: index for index, (_, pair) in enumerate(lexical_sorted, start=1)}
        dense_rank = {pair.pair_id: index for index, (_, pair) in enumerate(dense_sorted, start=1)}
        hybrid = []
        for pair in eligible:
            # Reciprocal rank fusion keeps the incomparable lexical and cosine
            # score scales separate. Metadata is intentionally not mixed into
            # this diagnostic: a same-contact/Gold bonus can swamp tiny RRF
            # rank differences and falsely make unrelated candidates look good.
            rrf = (1 / (60 + lexical_rank[pair.pair_id]) if pair.pair_id in lexical_rank else 0.0)
            rrf += 1 / (60 + dense_rank[pair.pair_id])
            hybrid.append((rrf, pair))
        hybrid.sort(key=lambda item: item[0], reverse=True)

        mode_items = {
            "lexical": [(score, pair) for score, pair in lexical_sorted],
            "dense": dense_sorted,
            "hybrid_rrf": hybrid,
        }
        similarity_by_case[case.case_id] = {pair.pair_id: round(score, 5) for score, pair in dense_sorted}
        for mode, items in mode_items.items():
            full_rankings[mode][case.case_id] = [pair.pair_id for _, pair in items]
            rankings[mode][case.case_id] = [pair.pair_id for _, pair in items]
            detail[mode][case.case_id] = [
                {"pair_id": pair.pair_id, "score": round(float(score), 5), "label": pair.label,
                 "phase": pair.phase, "is_same_contact": pair.contact_id == case.contact_id}
                for score, pair in items
            ]
            rankings[mode][case.case_id] = rankings[mode][case.case_id][:k]
            detail[mode][case.case_id] = detail[mode][case.case_id][:k]

    metrics = {
        mode: evaluate_ranked_results(cases, ranked, k=k)
        for mode, ranked in full_rankings.items()
    }
    no_relevant_cases = [case for case in cases if not case.relevant_pair_ids]
    no_hit_returns = {
        mode: sum(bool(rankings[mode][case.case_id]) for case in no_relevant_cases)
        for mode in rankings
    }
    return {
        "k": k,
        "metrics": metrics,
        "no_relevant_query_count": len(no_relevant_cases),
        "no_relevant_query_return_count": no_hit_returns,
        "rankings": rankings,
        "complete_rankings": full_rankings,
        "details": detail,
        "dense_cosine_by_case": similarity_by_case,
    }


def _load_model(model_id: str, revision: str) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise SystemExit(
            "Optional dependency missing: install sentence-transformers in the backend environment."
        ) from exc
    return SentenceTransformer(model_id, revision=revision, device="cpu")


def run_benchmark(model_id: str = DEFAULT_MODEL, revision: str = DEFAULT_REVISION, k: int = 4) -> dict[str, Any]:
    pairs, cases = extended_synthetic_dataset()
    model = _load_model(model_id, revision)

    def encode(texts: Sequence[str], role: str) -> np.ndarray:
        return _encode_texts(model, texts, role=role)

    comparison = rank_candidates(pairs, cases, encode=encode, k=k)
    case_summary = []
    for case in cases:
        case_summary.append({
            "case_id": case.case_id,
            "probe_tags": list(case.probe_tags),
            "relevant_pair_ids": sorted(case.relevant_pair_ids),
            "relevant_rank": {
                mode: next(
                    (index for index, pair_id in enumerate(comparison["complete_rankings"][mode][case.case_id], 1)
                     if pair_id in case.relevant_pair_ids),
                    None,
                )
                for mode in comparison["rankings"]
            },
            "top_ids": {mode: comparison["rankings"][mode][case.case_id][:3] for mode in comparison["rankings"]},
        })
    return {
        "benchmark": "synthetic_japanese_short_chat_extended_v1",
        "scope": "Synthetic-only local diagnostic; no user conversations or API calls.",
        "model": {"id": model_id, "revision": revision, "device": "cpu"},
        "methods": [
            "production lexical rank (includes existing quality/contact/phase weights)",
            "dense cosine rank",
            "RRF of those two ranks (no additional metadata bonus)",
        ],
        "case_summary": case_summary,
        **comparison,
        "limitations": [
            "Tiny synthetic labels do not establish usefulness on real conversations.",
            "Dense and hybrid no-hit results are reported, not hidden by a tuned threshold.",
            "Production lexical scores contact and reply text and includes existing quality/contact/phase weights; dense search encodes the counterpart turn only.",
            "No user corpus, prompts, API key, or external inference endpoint is accessed.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--full-details", action="store_true", help="include every ranked candidate and cosine score")
    args = parser.parse_args()
    result = run_benchmark(args.model_id, args.revision, args.k)
    if not args.full_details:
        for key in ("rankings", "complete_rankings", "details", "dense_cosine_by_case"):
            result.pop(key)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
