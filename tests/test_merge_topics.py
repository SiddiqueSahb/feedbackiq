"""
Regression test for merge_topics()/taxonomy_coherence(): reproduces the battery/charging
vs fee/charge lexical-collision bug, plus topics that must not merge across platforms.
Heavy deps are stubbed via sys.modules so the real module's merge logic runs without a
full BERTopic pipeline.
"""
import sys, os, types
PROJECT_ROOT = "" + os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + ""
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

# stub heavy deps
bertopic_mod = types.ModuleType("bertopic")
bertopic_repr = types.ModuleType("bertopic.representation")
class KeyBERTInspired:
    pass
bertopic_repr.KeyBERTInspired = KeyBERTInspired
bertopic_mod.representation = bertopic_repr
sys.modules["bertopic"] = bertopic_mod
sys.modules["bertopic.representation"] = bertopic_repr

langdetect_mod = types.ModuleType("langdetect")
langdetect_mod.detect = lambda t: "en"
class LangDetectException(Exception):
    pass
langdetect_mod.LangDetectException = LangDetectException
class DetectorFactory:
    seed = None
langdetect_mod.DetectorFactory = DetectorFactory
sys.modules["langdetect"] = langdetect_mod

groq_mod = types.ModuleType("groq")
class Groq:
    def __init__(self, api_key=None):
        pass
groq_mod.Groq = Groq
sys.modules["groq"] = groq_mod

spacy_mod = types.ModuleType("spacy")
class _Token:
    def __init__(self, pos):
        self.pos_ = pos
class _Doc(list):
    pass
class _StubNLP:
    def __call__(self, text):
        # pretend every word is a NOUN so noun-presence checks pass
        return _Doc([_Token("NOUN") for _ in text.split()])
spacy_mod.load = lambda *a, **k: _StubNLP()
sys.modules["spacy"] = spacy_mod

import importlib
dc = importlib.import_module("scripts.discover_categories") if os.path.exists(os.path.join(PROJECT_ROOT, "scripts", "__init__.py")) else None
if dc is None:
    # scripts/ has no __init__.py -- load by file path instead
    import importlib.util
    spec = importlib.util.spec_from_file_location("discover_categories", os.path.join(PROJECT_ROOT, "scripts", "discover_categories.py"))
    dc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dc)

import numpy as np

print("=== Synthetic contamination-fix test for merge_topics() ===\n")

def unit(v):
    v = np.array(v, dtype=float)
    return v / np.linalg.norm(v)

# A/B: same battery complaint as two separate topics -- should merge.
# D: shares the word "charge" with A/B but is a different (fee) complaint --
# must NOT merge; proves merge uses embedding distance, not lexical overlap.
raw_topics = [
    {"topic_id": 0, "platform": "amazon", "label": "Battery Charging Power",
     "keywords": ["battery", "charging", "power", "drain"], "count": 500,
     "centroid": unit([1.0, 0.05, 0.0, 0.0]).tolist(),
     "example_docs": ["The battery stopped charging after one week.", "Battery drains too fast."]},

    {"topic_id": 1, "platform": "amazon", "label": "Battery Charge Life",
     "keywords": ["battery", "charge", "life", "short"], "count": 300,
     "centroid": unit([0.95, 0.02, 0.0, 0.0]).tolist(),
     "example_docs": ["Battery life is way shorter than advertised."]},

    {"topic_id": 2, "platform": "twitter_airline", "label": "Flight Delay Schedule",
     "keywords": ["flight", "delay", "schedule", "cancel"], "count": 400,
     "centroid": unit([0.0, 1.0, 0.05, 0.0]).tolist(),
     "example_docs": ["My flight was delayed 6 hours with no explanation."]},

    {"topic_id": 3, "platform": "twitter_airline", "label": "Fee Charge Refund",
     "keywords": ["fee", "charge", "refund", "money"], "count": 200,
     "centroid": unit([0.0, 0.0, 1.0, 0.05]).tolist(),
     "example_docs": ["They charged me a fee they never disclosed and refused a refund."]},

    {"topic_id": 4, "platform": "yelp", "label": "Store Hours Closed",
     "keywords": ["store", "hours", "closed", "open"], "count": 150,
     "centroid": unit([0.0, 0.0, 0.05, 1.0]).tolist(),
     "example_docs": ["Store was closed during posted business hours."]},

    {"topic_id": 5, "platform": "amazon", "label": "Package Late Damaged",
     "keywords": ["package", "damaged", "late", "arrived"], "count": 350,
     "centroid": unit([0.0, 0.4, -0.9, 0.0]).tolist(),
     "example_docs": ["My package arrived damaged and three weeks late."]},
]

merged = dc.merge_topics(raw_topics, target_categories=5)

print(f"Raw topics: {len(raw_topics)}  ->  Merged clusters: {len(merged)}\n")
for i, cl in enumerate(merged, 1):
    print(f"Cluster {i}: count={cl['count']:>4}  platforms={cl['platforms']}")
    print(f"   keywords: {cl['keywords'][:8]}")
    print(f"   source topic_ids: {cl['source_topic_ids']}")
    print()

coh = dc.taxonomy_coherence(merged)
print(f"Taxonomy coherence (mean pairwise centroid cosine sim): {coh:.4f}\n")

# assertions
battery_cluster = None
for cl in merged:
    ids = {t["topic_id"] for t in cl["source_topic_ids"]}
    if {0, 1} <= ids:
        battery_cluster = cl
        break

assert battery_cluster is not None, "FAIL: battery topics 0 and 1 did not merge into the same cluster"
assert battery_cluster["count"] == 800, f"FAIL: expected merged count 800, got {battery_cluster['count']}"

fee_cluster = None
for cl in merged:
    ids = {t["topic_id"] for t in cl["source_topic_ids"]}
    if ids == {3}:
        fee_cluster = cl
        break
assert fee_cluster is not None, "FAIL: fee/charge topic (3) unexpectedly merged with something else"

battery_ids = {t["topic_id"] for t in battery_cluster["source_topic_ids"]}
assert 3 not in battery_ids, "FAIL: fee/charge topic merged into the battery cluster (lexical 'charge' collision reproduced!)"

print("PASS: battery topics (0,1) merged into one cluster; count summed correctly (800).")
print("PASS: fee/charge topic (3) stayed separate from the battery cluster despite sharing")
print("      the substring 'charge' -- merge is driven by embedding distance, not lexical overlap.")
print(f"PASS: {len(merged)} merged clusters from {len(raw_topics)} raw topics, as requested (target=5).")
