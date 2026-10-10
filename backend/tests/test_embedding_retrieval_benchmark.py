from __future__ import annotations

import numpy as np

from backend.app.learning import corpus
from backend.app.learning import retrieval
from scripts.benchmark_embedding_retrieval import _encode_texts, rank_candidates
from scripts.benchmark_retrieval_quality import RetrievalCase


def _pair(
    pair_id: str,
    text: str,
    *,
    label: str = "gold",
    excluded: bool = False,
    contact_id: int = 1,
    self_text: str | None = None,
) -> corpus.ReplyPair:
    turn = corpus.Turn(contact_id, "contact", [], text, "")
    reply = corpus.Turn(contact_id, "self", [], self_text or f"reply to {text}", "")
    return corpus.ReplyPair(
        pair_id=pair_id, contact_id=contact_id, contact_turn=turn, self_turn=reply,
        context_turns=[], phase="ongoing", label=label, source="manual", excluded=excluded,
    )


def test_encode_adds_query_and_passage_prefixes() -> None:
    class FakeModel:
        def encode(self, texts, **kwargs):
            assert texts == ["query: q", "query: r"]
            assert kwargs["normalize_embeddings"] is True
            return [[1.0, 0.0], [0.0, 1.0]]

    result = _encode_texts(FakeModel(), ["q", "r"], role="query")
    assert result.shape == (2, 2)


def test_rank_candidates_compares_modes_and_filters_negative_or_excluded() -> None:
    pairs = [
        _pair("alpha", "alpha conversation"),
        _pair("beta", "beta conversation"),
        _pair("negative", "alpha reply trap", label="negative"),
        _pair("excluded", "alpha excluded", excluded=True),
    ]
    cases = [
        RetrievalCase("alpha-query", "alpha", frozenset({"alpha"}), 1, "ongoing"),
        RetrievalCase("no-hit", "unrelated topic", frozenset(), 999, "scheduling"),
    ]
    vectors = {
        "alpha": np.array([1.0, 0.0], dtype=np.float32),
        "beta": np.array([0.0, 1.0], dtype=np.float32),
        "unrelated": np.array([-1.0, 0.0], dtype=np.float32),
    }

    def encode(texts, role):
        return np.stack([vectors["unrelated" if "unrelated" in text else "alpha" if "alpha" in text else "beta"] for text in texts])

    result = rank_candidates(pairs, cases, encode=encode, k=2)

    assert set(result["metrics"]) == {"lexical", "dense", "hybrid_rrf"}
    assert result["rankings"]["dense"]["alpha-query"][0] == "alpha"
    assert result["metrics"]["dense"]["queries_with_hit"] == 1
    assert all("negative" not in ids and "excluded" not in ids for mode in result["rankings"].values() for ids in mode.values())
    assert result["no_relevant_query_return_count"]["dense"] == 1


def test_hybrid_rrf_does_not_add_another_contact_gold_or_phase_bonus(monkeypatch) -> None:
    relevant = _pair("relevant", "カフェって好き？", contact_id=1, self_text="うん、好きだよ")
    same_contact_decoy = _pair(
        "same-contact-decoy", "カフェの近くで働いてる", contact_id=99, self_text="休みの日は何してる？",
    )
    other_decoy = _pair("other-decoy", "カフェで仕事したよ", contact_id=2)
    case = RetrievalCase("preference", "カフェは好き？", {"relevant"}, 99, "ongoing")
    vectors = {
        "query": np.array([1.0, 0.0], dtype=np.float32),
        "same-contact-decoy": np.array([1.0, 0.0], dtype=np.float32),
        "relevant": np.array([0.8, 0.6], dtype=np.float32),
        "other-decoy": np.array([0.0, 1.0], dtype=np.float32),
    }

    def encode(texts, role):
        if role == "query":
            return np.stack([vectors["query"] for _ in texts])
        return np.stack([
            vectors["same-contact-decoy"] if "近く" in text
            else vectors["relevant"] if "好き？" in text
            else vectors["other-decoy"]
            for text in texts
        ])

    lexical_scores = {"relevant": 3.0, "other-decoy": 2.0, "same-contact-decoy": 1.0}
    monkeypatch.setattr(
        retrieval,
        "score_pair_relevance",
        lambda query, pair, contact_id, phase: lexical_scores[pair.pair_id],
    )

    result = rank_candidates([relevant, same_contact_decoy, other_decoy], [case], encode=encode, k=3)

    # The patched scorer supplies crossed ranks so this isolates fusion: a
    # second same-contact+Gold+phase bonus would reverse the tiny RRF advantage.
    assert result["rankings"]["hybrid_rrf"]["preference"][0] == "relevant", result["rankings"]
    assert result["details"]["hybrid_rrf"]["preference"][0]["score"] == round(1 / 61 + 1 / 62, 5)


def test_mrr_uses_complete_ranking_when_relevant_item_is_below_k() -> None:
    pairs = [_pair("first", "first"), _pair("second", "second"), _pair("target", "target")]
    case = RetrievalCase("deep-hit", "query", {"target"})
    vectors = {
        "query": np.array([1.0, 0.0], dtype=np.float32),
        "first": np.array([1.0, 0.0], dtype=np.float32),
        "second": np.array([0.8, 0.6], dtype=np.float32),
        "target": np.array([0.0, 1.0], dtype=np.float32),
    }

    def encode(texts, role):
        if role == "query":
            return np.stack([vectors["query"] for _ in texts])
        return np.stack([vectors[next(key for key in ("first", "second", "target") if key in text)] for text in texts])

    result = rank_candidates(pairs, [case], encode=encode, k=1)

    assert result["rankings"]["dense"]["deep-hit"] == ["first"]
    assert result["metrics"]["dense"]["recall_at_k"] == 0
    assert result["metrics"]["dense"]["mrr"] == 1 / 3
