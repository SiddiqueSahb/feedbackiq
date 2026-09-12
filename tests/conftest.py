"""
Shared setup for the FeedbackIQ test suite.

pytest loads this file before any test module, which lets it make the suite:
  * offline     - no Hugging Face downloads, no Groq calls
  * predictable - a known API key, whatever a local .env file contains
  * clean       - log output goes to a temporary file, not logs/feedbackanalytics.log
"""

import os
import tempfile

# These must be set before config.py is imported anywhere. pydantic-settings
# reads the environment when `settings = Settings()` runs, and environment
# variables take priority over values in a local .env file.
os.environ["API_KEY"] = "test-api-key"
os.environ["ENVIRONMENT"] = "development"
os.environ["GROQ_API_KEY"] = ""
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import logger as project_logger  # noqa: E402

# logger.get_logger() reads LOG_FILE whenever it creates a file handler, so
# pointing it at a temporary file keeps test runs out of the real log.
project_logger.LOG_FILE = os.path.join(
    tempfile.mkdtemp(prefix="feedbackiq-tests-"), "tests.log"
)

# These files live in tests/ but are manual scripts, not pytest tests. Some
# load the ~1 GB FAISS index or wait for keyboard input when imported, so
# `pytest tests/` must never collect them. They still run directly, e.g.
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
