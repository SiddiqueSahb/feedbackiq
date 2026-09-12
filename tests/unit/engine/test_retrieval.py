"""
Retrieval injection - feedbackiq.engine.retrieval

Protects the seam a future SaaS milestone needs: the engine asks a `Retriever` for
evidence and does not know where it comes from. Today that is the dissertation corpus;
later it will be one organisation's own feedback, with no change inside the engine.

Also protects a rule that matters for trust: a retriever that *fails* must not look
like a retriever that found nothing.
"""

import pytest

from feedbackiq.core.config import settings
from feedbackiq.engine import (
    CallableRetriever,
    CorpusRetriever,
    Evidence,
    NullRetriever,
    Retriever,
    evidence_from_rows,
)
from feedbackiq.engine.errors import RetrievalError

ROWS = [
    {"review_id": "r1", "text": "Battery died after five days.", "similarity_score": 0.82,
     "platform": "amazon", "rating": 1.0, "sentiment_label": "negative"},
    {"review_id": "r2", "text": "Charger stopped working.", "similarity_score": 0.40,
     "platform": "amazon", "rating": 2.0, "sentiment_label": "negative"},
    {"review_id": "r3", "text": "Unrelated review about parking.", "similarity_score": 0.11,
     "platform": "yelp", "rating": 3.0, "sentiment_label": "neutral"},
]


# protocol conformance

@pytest.mark.parametrize(
    "retriever",
    [NullRetriever(), CallableRetriever(lambda q, n: []), CorpusRetriever(lambda **kw: [])],
    ids=["null", "callable", "corpus"],
)
def test_every_implementation_satisfies_the_retriever_protocol(retriever):
    assert isinstance(retriever, Retriever)


def test_the_default_retriever_finds_nothing_rather_than_reaching_for_a_corpus():
    assert NullRetriever().search("battery", 5) == ()


def test_a_callable_can_be_used_as_a_retriever():
    calls = []

    def search(query, limit):
        calls.append((query, limit))
        return [Evidence(id="e1", text="same problem", score=0.7)]

    found = CallableRetriever(search).search("battery died", 3)

    assert calls == [("battery died", 3)]
    assert [(e.id, e.score) for e in found] == [("e1", 0.7)]


# mapping rows to evidence

def test_rows_become_evidence_with_metadata_preserved():
    evidence = evidence_from_rows(ROWS)

    assert [e.id for e in evidence] == ["r1", "r2", "r3"]
    assert evidence[0].text == "Battery died after five days."
    assert evidence[0].score == 0.82
    assert evidence[0].metadata == {"platform": "amazon", "rating": 1.0, "sentiment_label": "negative"}


def test_weak_rows_are_dropped_at_the_threshold():
    evidence = evidence_from_rows(ROWS, threshold=0.35)

    assert [e.id for e in evidence] == ["r1", "r2"]  # 0.11 excluded


def test_the_threshold_is_inclusive():
    rows = [{"review_id": "edge", "text": "x", "similarity_score": 0.35}]

    assert [e.id for e in evidence_from_rows(rows, threshold=0.35)] == ["edge"]


def test_a_missing_score_is_treated_as_zero():
    rows = [{"review_id": "no-score", "text": "x"}]

    assert evidence_from_rows(rows, threshold=0.1) == ()
    assert [e.id for e in evidence_from_rows(rows)] == ["no-score"]


# the corpus adapter

def test_corpus_retriever_applies_the_evidence_threshold_by_default():
    retriever = CorpusRetriever(lambda **kwargs: ROWS)

    found = retriever.search("battery", 5)

    assert retriever.threshold == settings.ANALYSE_SIMILARITY_THRESHOLD == 0.35
    assert [e.id for e in found] == ["r1", "r2"]


def test_corpus_retriever_passes_the_query_limit_and_optional_sentiment_filter():
    seen = {}

    def search(**kwargs):
        seen.update(kwargs)
        return []

    CorpusRetriever(search, sentiment_filter="negative").search("battery died", 7)

    assert seen == {"query": "battery died", "top_k": 7, "sentiment_filter": "negative"}


def test_corpus_retriever_omits_the_filter_when_none_is_set():
    seen = {}

    def search(**kwargs):
        seen.update(kwargs)
        return []

    CorpusRetriever(search).search("battery died", 5)

    assert "sentiment_filter" not in seen


def test_a_failing_search_raises_instead_of_pretending_nothing_matched():
    def broken(**kwargs):
        raise FileNotFoundError("data/embeddings/reviews.faiss")

    with pytest.raises(RetrievalError) as raised:
        CorpusRetriever(broken).search("battery", 5)

    assert "FileNotFoundError" in str(raised.value)
