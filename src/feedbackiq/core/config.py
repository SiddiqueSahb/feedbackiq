"""
All of FeedbackIQ's configuration, in one place.

Values come from the environment first, then from a local `.env` file, then from the
defaults below. Every default is the value the code already used before Milestone 2:
configuration controls behaviour here, it does not change it.

Paths are stored relative to the project root and exposed as absolute `Path`
properties (see `core/paths.py`), so the app behaves the same whether it is started
from the repository root, from elsewhere, or inside the container.

Usage:
    from feedbackiq.core.config import settings
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from feedbackiq.core import paths


class Settings(BaseSettings):
    # ---------------------------------------------------------------- environment

    # "development" (default) or "production". In production the API refuses to
    # start on the development API key - see api/deps.py.
    ENVIRONMENT: str = "development"

    # ---------------------------------------------------------------- data & model locations
    # Relative to the project root. Use the *_dir / *_file properties below to read
    # them; an absolute value in the environment (e.g. a mounted volume) also works.

    DATA_PATH: str = "data/processed/reviews_unified.parquet"
    MODEL_PATH: str = "models/distilbert-finetuned-final"
    INDEX_PATH: str = "data/embeddings/reviews.faiss"
    EMBED_PATH: str = "data/embeddings/review_embeddings.npy"
    LANGCHAIN_INDEX_PATH: str = "data/embeddings/langchain_index"

    # Previously hard-coded inside the modules that read them.
    CLASSICAL_MODEL_DIR: str = "models/classical"          # was nlp/classical_models.py
    TAXONOMY_DIR: str = "data/processed"                    # was nlp/categoriser.py
    RESULTS_DIR: str = "data/results"                       # was services/evaluation_service.py
    KEYWORDS_PATH: str = "data/results/keywords/top_keywords.json"  # was services/analytics_service.py

    # ---------------------------------------------------------------- experiment tracking
    # Used by the offline training/evaluation scripts, not by the running app.
    MLFLOW_TRACKING_URI: str = "mlruns"
    MLFLOW_EXPERIMENT: str = "Feedback_Sentiment"

    # ---------------------------------------------------------------- models

    ROBERTA_MODEL: str = "cardiffnlp/twitter-roberta-base-sentiment-latest"
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    # Token limit for the fine-tuned sentiment model. 128 is what it was trained with.
    MAX_SEQ_LENGTH: int = 128

    # Zero-shot complaint categorisation. DeBERTa-v3 outperforms BART-large-mnli on
    # zero-shot benchmarks, especially with larger label sets; the BART baseline used
    # in the dissertation comparison is no longer referenced by any code.
    ZEROSHOT_MODEL: str = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
    CATEGORY_SHORTLIST_K: int = 6          # candidates the embedding stage passes to the NLI reranker
    CATEGORY_CONFIDENCE_THRESHOLD: float = 0.35  # below this: "Unclassified / Emerging Complaint"
    CATEGORY_MAX_CHARS: int = 400          # review text given to the categoriser

    # ---------------------------------------------------------------- retrieval & generation
    # These were constants in rag/pipeline.py and nlp/summariser.py. Same values,
    # now configurable. 0.35 was chosen by hand and never validated (see RESULTS.md);
    # it is kept exactly as it was.

    SIMILARITY_THRESHOLD: float = 0.35          # minimum relevance to keep a retrieved review
    ANALYSE_SIMILARITY_THRESHOLD: float = 0.35  # minimum to treat a review as evidence in the analysis prompt
    RAG_TOP_K: int = 5                          # reviews passed to the generator
    RAG_FETCH_K: int = 20                       # candidate pool
    RAG_FETCH_K_WITH_FILTER: int = 50           # wider pool when a platform filter narrows the search
    RAG_MMR_LAMBDA: float = 0.5                 # MMR relevance/diversity balance
    MAX_REVIEW_CHARS: int = 1000                # cap per review entering the prompt
    MAX_LIVE_RETRIES: int = 2                   # retries on a provider rate limit
    RETRY_BASE_WAIT: float = 2.0                # seconds, when the provider doesn't say how long

    # ---------------------------------------------------------------- LLM provider

    GROQ_API_KEY: str = ""      # no default: a missing key should fail loudly, not fall back
    USE_GROQ: bool = True
    # openai/gpt-oss-20b fails with_structured_output() (tool_use_failed);
    # nlp/summariser._recover_from_failed_tool_call() works around it.
    # NOTE: the dissertation results were produced with llama-3.1-8b-instant.
    GROQ_MODEL: str = "openai/gpt-oss-20b"
    LLM_TEMPERATURE: float = 0.2
    LLM_TIMEOUT: int = 30       # seconds; keeps a stuck call inside the frontend's timeout

    # ---------------------------------------------------------------- API

    # Required in the x-api-key header on every /api/* route except health.
    API_KEY: str = "dev-key-feedbackiq"   # override in production; the API refuses to boot on this value

    # Comma-separated CORS origins. "*" is fine locally; set the real frontend URL in production.
    ALLOWED_ORIGINS: str = "*"

    # ---------------------------------------------------------------- logging

    # Level for the application's log output (stdout).
    LOG_LEVEL: str = "WARNING"

    # Optional log file. Empty (the default) means stdout only, which is what a
    # container wants. Set a path to also write a rotating file.
    LOG_FILE: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # The shared .env also carries frontend-only settings such as API_URL.
        extra="ignore",
    )

    # ---------------------------------------------------------------- resolved paths

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() == "production"

    @property
    def data_file(self) -> Path:
        """The unified review corpus (parquet)."""
        return paths.resolve(self.DATA_PATH)

    @property
    def model_dir(self) -> Path:
        """The fine-tuned DistilBERT directory."""
        return paths.resolve(self.MODEL_PATH)

    @property
    def index_file(self) -> Path:
        """The FAISS index used by semantic search."""
        return paths.resolve(self.INDEX_PATH)

    @property
    def embed_file(self) -> Path:
        """The embedding matrix produced by scripts/build_index.py."""
        return paths.resolve(self.EMBED_PATH)

    @property
    def langchain_index_dir(self) -> Path:
        """The LangChain FAISS store (index + docstore) used by RAG."""
        return paths.resolve(self.LANGCHAIN_INDEX_PATH)

    @property
    def classical_model_dir(self) -> Path:
        """Pickled TF-IDF vectoriser and classical classifiers."""
        return paths.resolve(self.CLASSICAL_MODEL_DIR)

    @property
    def taxonomy_dir(self) -> Path:
        """Where discover_categories.py writes the complaint taxonomy."""
        return paths.resolve(self.TAXONOMY_DIR)

    @property
    def results_dir(self) -> Path:
        """Evaluation results read by the API and the dashboard."""
        return paths.resolve(self.RESULTS_DIR)

    @property
    def keywords_file(self) -> Path:
        """Precomputed dashboard keywords (scripts/precompute_keywords.py)."""
        return paths.resolve(self.KEYWORDS_PATH)


settings = Settings()
