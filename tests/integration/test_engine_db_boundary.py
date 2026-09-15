"""
The boundary between the analytics engine and the database.

The rule this file enforces: **the engine does not know the database exists.** It takes
typed inputs and returns typed results; persistence stores them. Written down as tests
because a boundary that is only a convention erodes the first time someone finds it
convenient to run a query inside the pipeline.

    feedbackiq.engine  ──▶  typed results  ──▶  feedbackiq.db  ──▶  PostgreSQL
         (no SQL)                                  (all the SQL)

Three things are checked: no engine module imports a database library, importing the
engine in a fresh interpreter loads no database library, and the engine produces a full
result with no database available at all - which is also how the dissertation scripts and
the current API still run.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

import feedbackiq.engine
from feedbackiq.db.models import AnalysisResult
from feedbackiq.db.persistence import save_batch_analysis, save_feedback
from feedbackiq.engine.categorisation import CategorisationOutcome
from feedbackiq.engine.pipeline import AnalyticsEngine
from feedbackiq.engine.types import Category, CategoryMatch, FeedbackItem, SentimentPrediction

ENGINE_DIR = Path(feedbackiq.engine.__file__).parent

# Anything that implies the engine is reaching for storage or a transport of its own.
FORBIDDEN_IMPORTS = ("sqlalchemy", "alembic", "psycopg", "feedbackiq.db", "fastapi")


class FakeSentiment:
    """The same shape as the real model, without loading one."""

    def predict_batch(self, texts):
        return [
            SentimentPrediction(
                label="negative",
                confidence=0.95,
                scores={"negative": 0.95, "neutral": 0.03, "positive": 0.02},
                model_version="fake-sentiment-1",
            )
            for _ in texts
        ]


class FakeCategoriser:
    def categorise_batch(self, texts, categories):
        match = CategoryMatch(
            category_id="wait_times_and_delays",
            name="Wait Times & Delays",
            score=0.77,
        )
        return [CategorisationOutcome(top=match, candidates=(match,)) for _ in texts]


# ---------------------------------------------------------------- static boundary


def test_no_engine_module_imports_a_database_library():
    offenders: list[str] = []

    for path in sorted(ENGINE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue

            for name in names:
                if any(name == bad or name.startswith(f"{bad}.") for bad in FORBIDDEN_IMPORTS):
                    offenders.append(f"{path.name}:{node.lineno} imports {name}")

    assert offenders == [], "the engine must not depend on persistence or HTTP: " + "; ".join(offenders)


def test_importing_the_engine_does_not_load_a_database_driver():
    """A run-time check to go with the static one: a lazy import inside a function would
    slip past the AST scan, but not past this."""
    code = (
        "import sys; import feedbackiq.engine; "
        "print(','.join(m for m in ('sqlalchemy', 'psycopg', 'alembic') if m in sys.modules))"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=300
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "", f"the engine loaded: {completed.stdout.strip()}"


# ---------------------------------------------------------------- runtime boundary


def test_the_engine_runs_with_no_database_configured(monkeypatch):
    """The dissertation scripts, the benchmark and today's API all run this way."""
    from feedbackiq.core.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", "")

    engine = AnalyticsEngine(sentiment=FakeSentiment(), categoriser=FakeCategoriser())

    analysis = engine.analyse_batch(
        [FeedbackItem(id="row-1", text="waited forty minutes")],
        categories=(),
    )

    assert analysis.results[0].sentiment.label == "negative"
    assert analysis.versions["engine"]


@pytest.mark.integration
def test_engine_output_can_be_stored_without_the_engine_knowing_how(
    session, organisation, data_source, default_categories
):
    """
    The hand-off, end to end: feedback is saved, the engine analyses it, the persistence
    layer stores the result. The engine is given the row ids and echoes them back - the
    only thing it ever knows about the database.
    """
    rows = save_feedback(
        session,
        organisation_id=organisation.id,
        data_source_id=data_source.id,
        texts=["waited forty minutes for a table"],
    )

    engine = AnalyticsEngine(
        sentiment=FakeSentiment(),
        categoriser=FakeCategoriser(),
        default_categories=(
            # A one-category taxonomy is enough here; the real one will come from the
            # database, which is the point of the engine taking categories as an argument.
            Category(
                # A product-taxonomy key, so persistence can resolve it against the seeded
                # categories - which is the hand-off this test exists to prove.
                id="wait_times_and_delays",
                name="Wait Times & Delays",
                description="Customers wait too long to be served.",
            ),
        ),
    )

    analysis = engine.analyse_batch([FeedbackItem(id=str(rows[0].id), text=rows[0].text)])

    save_batch_analysis(session, organisation_id=organisation.id, analysis=analysis)
    session.commit()

    stored = session.scalar(select(AnalysisResult))
    assert stored.feedback_id == rows[0].id
    assert stored.sentiment_label == "negative"
    assert stored.category_id is not None      # resolved by name against the taxonomy
