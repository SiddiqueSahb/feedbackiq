"""
The engine's vocabulary: what goes in, what comes out.

These are plain frozen dataclasses on purpose. They carry no database identity, no
HTTP concepts and no provider details, so the same objects can be produced by a
script, an API request or (later) a background worker, and stored by whatever
persistence layer arrives in a future milestone.

    Customer/API  ->  AnalyticsEngine  ->  BatchAnalysis (these types)  ->  persistence

Nothing in this module imports FastAPI, pandas, torch or a database driver.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

# The three classes the fine-tuned model was trained on.
SentimentLabel = Literal["positive", "neutral", "negative"]

# Whether an individual record came through the pipeline intact.
ItemStatus = Literal["ok", "failed"]


# ---------------------------------------------------------------- inputs


@dataclass(frozen=True)
class FeedbackItem:
    """One piece of customer feedback to analyse.

    `id` is the caller's identifier - a row number, a CSV line, a database UUID
    later. The engine only echoes it back, so results can be matched to inputs.
    """

    id: str
    text: str

    def __post_init__(self) -> None:
        if not str(self.text).strip():
            raise ValueError(f"feedback {self.id!r} has no text")


@dataclass(frozen=True)
class Category:
    """A category the categoriser may assign.

    `description` is what the zero-shot model actually compares against (the NLI
    hypothesis), which is why it is required rather than optional; `exemplars`
    sharpen the embedding shortlist. Categories are passed *into* the engine, so a
    customer's own taxonomy needs no code change and no retraining.
    """

    id: str
    name: str
    description: str
    exemplars: tuple[str, ...] = ()


# ---------------------------------------------------------------- stage outputs


@dataclass(frozen=True)
class SentimentPrediction:
    label: str
    confidence: float
    scores: dict[str, float]
    model_version: str


@dataclass(frozen=True)
class CategoryMatch:
    """A scored category. `category_id` is None for the "unclassified" outcome."""

    category_id: str | None
    name: str
    score: float
    description: str = ""


@dataclass(frozen=True)
class Evidence:
    """A piece of feedback retrieved as supporting evidence."""

    id: str
    text: str
    score: float
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Insight:
    """The LLM's reading of one item, with the evidence it was given."""

    summary: str
    keywords: tuple[str, ...]
    business_insight: str
    severity: str
    priority: str
    department: str
    executive_summary: str
    evidence_ids: tuple[str, ...] = ()
    prompt_version: str = ""
    model_version: str = ""


@dataclass(frozen=True)
class UsageStats:
    """What the external LLM cost us. Zero when no LLM was called."""

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # Calls the provider rejected for rate limiting and we retried.
    retries: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def plus(self, other: "UsageStats") -> "UsageStats":
        return UsageStats(
            llm_calls=self.llm_calls + other.llm_calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            retries=self.retries + other.retries,
        )


# ---------------------------------------------------------------- results


@dataclass(frozen=True)
class ItemAnalysis:
    """The result for one input item, in the same position as the input.

    A failed item still appears, with `status="failed"` and `error` set, so a caller
    can always match results to inputs one-for-one.
    """

    feedback_id: str
    status: ItemStatus = "ok"
    sentiment: SentimentPrediction | None = None
    category: CategoryMatch | None = None
    candidate_categories: tuple[CategoryMatch, ...] = ()
    is_unclassified: bool = False
    # Set when the sentiment gate skipped categorisation, e.g.
    # "sentiment 'positive' is not gated for complaint categories".
    categorisation_skipped: str | None = None
    evidence: tuple[Evidence, ...] = ()
    insight: Insight | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def with_error(self, message: str) -> "ItemAnalysis":
        return replace(self, status="failed", error=message)


@dataclass(frozen=True)
class BatchAnalysis:
    """Results for a batch, in input order, plus what it cost and how it was produced."""

    results: tuple[ItemAnalysis, ...]
    usage: UsageStats = field(default_factory=UsageStats)
    versions: dict[str, object] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.results)

    @property
    def succeeded(self) -> tuple[ItemAnalysis, ...]:
        return tuple(r for r in self.results if r.ok)

    @property
    def failed(self) -> tuple[ItemAnalysis, ...]:
        return tuple(r for r in self.results if not r.ok)


@dataclass(frozen=True)
class GroundedAnswer:
    """An answer that either cites its evidence or explains why it refused."""

    answer: str
    evidence: tuple[Evidence, ...] = ()
    grounded: bool = True
    # "out_of_scope" or "no_evidence" when grounded is False.
    refusal_reason: str | None = None
    usage: UsageStats = field(default_factory=UsageStats)
    prompt_version: str = ""
    model_version: str = ""
