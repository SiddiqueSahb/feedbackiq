"""
Shared setup for the FeedbackIQ test suite.

pytest loads this file before any test module, which lets it make the suite:
  * offline     - no Hugging Face downloads, no Groq calls
  * predictable - a known API key, whatever a local .env file contains
  * quiet       - logging goes to stdout only, never to a file

Settings are read from the environment before `feedbackiq.core.config` is imported
anywhere: environment variables take priority over a local .env file.
"""

import os

os.environ["API_KEY"] = "test-api-key"
os.environ["ENVIRONMENT"] = "development"
os.environ["GROQ_API_KEY"] = ""
# Empty means stdout only. Without this, a developer's .env could point the suite
# at a real log file.
os.environ["LOG_FILE"] = ""
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# These files live in tests/ but are manual scripts, not pytest tests. Some load the
# ~1 GB FAISS index or wait for keyboard input when imported, so `pytest tests/` must
# never collect them. They still run directly, e.g.
# `python tests/test_categoriser_shortlist.py`.
collect_ignore = [
    "debug_embedding.py",
    "test_categoriser_shortlist.py",
    "test_faiss.py",
    "test_merge_topics.py",
    "test_rag.py",
    "test_retriever.py",
    "test_semantic_search.py",
]
