"""
Taxonomy consolidation - scripts/discover_categories.py (merge_topics, taxonomy_coherence)

Protects the fix for a lexical-collision bug: "battery charging" and "fee charged"
share the word "charge" but must NOT be merged, because merging is driven by
embedding distance, not shared words.

Ported from tests/test_merge_topics.py (a manual check script, still runnable).
BERTopic, langdetect, Groq and spaCy are replaced with stub modules only while the
script is loaded, so nothing heavy is imported and the stubs don't leak into other tests.
"""

import copy
import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def stub_modules() -> dict:
    bertopic = types.ModuleType("bertopic")
    representation = types.ModuleType("bertopic.representation")
    representation.KeyBERTInspired = type("KeyBERTInspired", (), {})
    bertopic.representation = representation

    langdetect = types.ModuleType("langdetect")
    langdetect.detect = lambda text: "en"
    langdetect.LangDetectException = type("LangDetectException", (Exception,), {})
    langdetect.DetectorFactory = type("DetectorFactory", (), {"seed": None})

    groq = types.ModuleType("groq")
    groq.Groq = type("Groq", (), {"__init__": lambda self, api_key=None: None})

    class Token:
        pos_ = "NOUN"  # every word counts as a noun, as in the original check

    spacy = types.ModuleType("spacy")
    spacy.load = lambda *args, **kwargs: (lambda text: [Token() for _ in text.split()])

    return {
        "bertopic": bertopic,
        "bertopic.representation": representation,
        "langdetect": langdetect,
        "groq": groq,
        "spacy": spacy,
    }


@pytest.fixture
def discover_categories(monkeypatch):
    for name, module in stub_modules().items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.chdir(PROJECT_ROOT)

    # scripts/ is not a package, so load the script by file path.
    path = PROJECT_ROOT / "scripts" / "discover_categories.py"
    spec = importlib.util.spec_from_file_location("discover_categories_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unit(vector):
    vector = np.array(vector, dtype=float)
    return (vector / np.linalg.norm(vector)).tolist()


RAW_TOPICS = [
    # Topics 0 and 1: the same battery complaint discovered twice -> should merge.
    {"topic_id": 0, "platform": "amazon", "label": "Battery Charging Power",
     "keywords": ["battery", "charging", "power", "drain"], "count": 500,
     "centroid": unit([1.0, 0.05, 0.0, 0.0]),
     "example_docs": ["The battery stopped charging after one week.", "Battery drains too fast."]},
    {"topic_id": 1, "platform": "amazon", "label": "Battery Charge Life",
     "keywords": ["battery", "charge", "life", "short"], "count": 300,
     "centroid": unit([0.95, 0.02, 0.0, 0.0]),
     "example_docs": ["Battery life is way shorter than advertised."]},
    {"topic_id": 2, "platform": "twitter_airline", "label": "Flight Delay Schedule",
     "keywords": ["flight", "delay", "schedule", "cancel"], "count": 400,
     "centroid": unit([0.0, 1.0, 0.05, 0.0]),
     "example_docs": ["My flight was delayed 6 hours with no explanation."]},
    # Topic 3: shares the word "charge" with topics 0/1 but is a fee complaint -> must NOT merge.
    {"topic_id": 3, "platform": "twitter_airline", "label": "Fee Charge Refund",
     "keywords": ["fee", "charge", "refund", "money"], "count": 200,
     "centroid": unit([0.0, 0.0, 1.0, 0.05]),
     "example_docs": ["They charged me a fee they never disclosed and refused a refund."]},
    {"topic_id": 4, "platform": "yelp", "label": "Store Hours Closed",
     "keywords": ["store", "hours", "closed", "open"], "count": 150,
     "centroid": unit([0.0, 0.0, 0.05, 1.0]),
     "example_docs": ["Store was closed during posted business hours."]},
    {"topic_id": 5, "platform": "amazon", "label": "Package Late Damaged",
     "keywords": ["package", "damaged", "late", "arrived"], "count": 350,
     "centroid": unit([0.0, 0.4, -0.9, 0.0]),
     "example_docs": ["My package arrived damaged and three weeks late."]},
]


def merged_clusters(discover_categories):
    return discover_categories.merge_topics(copy.deepcopy(RAW_TOPICS), target_categories=5)


def topic_ids(cluster) -> set:
    return {topic["topic_id"] for topic in cluster["source_topic_ids"]}


def test_same_complaint_topics_are_merged_and_counts_added(discover_categories):
    battery = [c for c in merged_clusters(discover_categories) if {0, 1} <= topic_ids(c)]
    assert len(battery) == 1
    assert battery[0]["count"] == 800


def test_shared_word_does_not_cause_a_merge(discover_categories):
    fee = [c for c in merged_clusters(discover_categories) if 3 in topic_ids(c)]
    assert len(fee) == 1
    assert topic_ids(fee[0]) == {3}


def test_merge_produces_the_requested_number_of_categories(discover_categories):
    assert len(merged_clusters(discover_categories)) == 5


def test_taxonomy_coherence_is_a_cosine_similarity(discover_categories):
    coherence = discover_categories.taxonomy_coherence(merged_clusters(discover_categories))
    assert -1.0 <= coherence <= 1.0
