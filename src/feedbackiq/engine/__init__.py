"""
The FeedbackIQ analytics engine.

Feedback in, typed results out. The engine knows nothing about HTTP, databases,
organisations or customers - it is a library that any of those can use:

    Customer/API  ->  AnalyticsEngine  ->  BatchAnalysis  ->  (future) persistence

Typical use:

    from feedbackiq.engine import FeedbackItem, build_default_engine

    engine = build_default_engine()
    batch = engine.analyse_batch([FeedbackItem(id="1", text="The battery died.")])

    for result in batch.results:
        print(result.feedback_id, result.sentiment.label, result.category)

Everything the engine depends on is injectable - the sentiment model, the categoriser,
the retriever, the LLM - so tests run offline with fakes, and a later milestone can
supply organisation-scoped retrieval without changing the engine.
"""

from feedbackiq.engine.categorisation import (
    CategorisationOutcome,
    Categoriser,
    ZeroShotCategoriser,
    categories_from_dicts,
)
from feedbackiq.engine.defaults import (
    build_default_engine,
    build_sentiment_only_engine,
    default_categories,
)
from feedbackiq.engine.errors import (
    EngineError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    ModelUnavailableError,
    RetrievalError,
)
from feedbackiq.engine.grounded_qa import answer_question
from feedbackiq.engine.insights import InsightGenerator, ItemAnalysisOutput
from feedbackiq.engine.llm import LLMAdapter
from feedbackiq.engine.pipeline import AnalyticsEngine, gated_sentiments
from feedbackiq.engine.preprocessing import normalise_text
from feedbackiq.engine.retrieval import (
    CallableRetriever,
    CorpusRetriever,
    NullRetriever,
    Retriever,
    evidence_from_rows,
)
from feedbackiq.engine.sentiment import (
    BatchSentimentModel,
    SentimentModel,
    SentimentWithFallback,
    SingleCallSentimentModel,
)
from feedbackiq.engine.types import (
    BatchAnalysis,
    Category,
    CategoryMatch,
    Evidence,
    FeedbackItem,
    GroundedAnswer,
    Insight,
    ItemAnalysis,
    SentimentPrediction,
    UsageStats,
)
from feedbackiq.engine.versions import (
    ENGINE_VERSION,
    PROMPT_VERSIONS,
    manifest,
    sentiment_model_version,
)

__all__ = [
    # engine
    "AnalyticsEngine",
    "build_default_engine",
    "build_sentiment_only_engine",
    "gated_sentiments",
    "normalise_text",
    # types
    "FeedbackItem",
    "Category",
    "SentimentPrediction",
    "CategoryMatch",
    "Evidence",
    "Insight",
    "ItemAnalysis",
    "BatchAnalysis",
    "GroundedAnswer",
    "UsageStats",
    # components
    "SentimentModel",
    "BatchSentimentModel",
    "SentimentWithFallback",
    "SingleCallSentimentModel",
    "Categoriser",
    "ZeroShotCategoriser",
    "CategorisationOutcome",
    "categories_from_dicts",
    "default_categories",
    "Retriever",
    "NullRetriever",
    "CallableRetriever",
    "CorpusRetriever",
    "evidence_from_rows",
    "LLMAdapter",
    "InsightGenerator",
    "ItemAnalysisOutput",
    "answer_question",
    # versions
    "ENGINE_VERSION",
    "PROMPT_VERSIONS",
    "manifest",
    "sentiment_model_version",
    # errors
    "EngineError",
    "ModelUnavailableError",
    "RetrievalError",
    "LLMError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMResponseError",
]
