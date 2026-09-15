"""
Tenant isolation across every read added in Milestone 6.

Authentication does not exist yet, so nothing here logs in. What is being proved is the
property underneath authentication: **every query that touches customer data is scoped to an
organisation, and asking for another organisation's row returns nothing rather than its
contents.** A missed `WHERE organisation_id = ...` is the defining SaaS data leak, and it is
invisible in a single-tenant test.

Organisation A and organisation B both have feedback, analysis, imports and jobs. Every
entry point is then asked for A's data while scoped to B, and vice versa.
"""

from __future__ import annotations

import uuid

import pytest

from feedbackiq.db import jobs as job_queue
from feedbackiq.db.persistence import (
    get_import_batch,
    list_import_batches,
    save_batch_analysis,
)
from feedbackiq.engine.types import (
    BatchAnalysis,
    CategoryMatch,
    ItemAnalysis,
    SentimentPrediction,
)
from feedbackiq.services.analytics import (
    available_categories,
    category_breakdown,
    sentiment_trend,
    summary,
)
from feedbackiq.services.feedback import FeedbackFilters, get_feedback, list_feedback
from feedbackiq.services.imports import import_csv

pytestmark = pytest.mark.integration

A_CSV = (
    b"text,external_id\n"
    b"organisation A private complaint,A-1\n"
    b"organisation A second private complaint,A-2\n"
)
B_CSV = b"text,external_id\norganisation B private complaint,B-1\n"


def analyse(session, organisation_id, rows, *, key="billing_and_payments"):
    save_batch_analysis(
        session,
        organisation_id=organisation_id,
        analysis=BatchAnalysis(
            results=tuple(
                ItemAnalysis(
                    feedback_id=str(row.id),
                    sentiment=SentimentPrediction(
                        label="negative", confidence=0.9, scores={"negative": 0.9},
                        model_version="fake-1",
                    ),
                    category=CategoryMatch(category_id=key, name="Billing & Payments", score=0.8),
                )
                for row in rows
            ),
            versions={"engine": "1.0.0"},
        ),
    )


@pytest.fixture()
def two_tenants(session, organisation, other_organisation, default_categories):
    """Both organisations with feedback, analysis, an import and a job."""
    a_import = import_csv(session, raw=A_CSV, organisation_id=organisation.id)
    b_import = import_csv(session, raw=B_CSV, organisation_id=other_organisation.id)
    session.flush()

    from feedbackiq.db.persistence import feedback_for_import_batch

    a_rows = feedback_for_import_batch(
        session, organisation_id=organisation.id, import_batch_id=a_import.batch.id
    )
    b_rows = feedback_for_import_batch(
        session, organisation_id=other_organisation.id, import_batch_id=b_import.batch.id
    )

    analyse(session, organisation.id, a_rows)
    analyse(session, other_organisation.id, b_rows)
    session.commit()

    return {
        "a": {"org": organisation, "import": a_import.batch, "job": a_import.job, "rows": a_rows},
        "b": {"org": other_organisation, "import": b_import.batch, "job": b_import.job, "rows": b_rows},
    }


# ---------------------------------------------------------------- feedback


def test_a_listing_returns_only_its_own_organisations_feedback(session, two_tenants):
    a, b = two_tenants["a"], two_tenants["b"]

    a_page = list_feedback(session, organisation_id=a["org"].id)
    b_page = list_feedback(session, organisation_id=b["org"].id)

    assert a_page.total == 2
    assert b_page.total == 1
    assert all("organisation A" in item["text"] for item in a_page.items)
    assert all("organisation B" in item["text"] for item in b_page.items)


def test_a_filter_cannot_reach_across_organisations(session, two_tenants):
    """Searching for the other tenant's exact text returns nothing."""
    a = two_tenants["a"]

    page = list_feedback(
        session,
        organisation_id=a["org"].id,
        filters=FeedbackFilters(search="organisation B private"),
    )

    assert page.total == 0


def test_feedback_detail_refuses_another_organisations_id(session, two_tenants):
    """The id is valid and the row exists - it just is not theirs, and must read as absent."""
    a, b = two_tenants["a"], two_tenants["b"]
    their_id = b["rows"][0].id

    assert get_feedback(session, organisation_id=b["org"].id, feedback_id=their_id) is not None
    assert get_feedback(session, organisation_id=a["org"].id, feedback_id=their_id) is None


# ---------------------------------------------------------------- analytics


def test_the_summary_counts_only_its_own_organisation(session, two_tenants):
    a, b = two_tenants["a"], two_tenants["b"]

    assert summary(session, organisation_id=a["org"].id)["total_feedback"] == 2
    assert summary(session, organisation_id=b["org"].id)["total_feedback"] == 1


def test_the_trend_counts_only_its_own_organisation(session, two_tenants):
    a, b = two_tenants["a"], two_tenants["b"]

    a_total = sum(row["feedback_count"] for row in sentiment_trend(session, organisation_id=a["org"].id))
    b_total = sum(row["feedback_count"] for row in sentiment_trend(session, organisation_id=b["org"].id))

    assert (a_total, b_total) == (2, 1)


def test_the_category_breakdown_counts_only_its_own_organisation(session, two_tenants):
    a, b = two_tenants["a"], two_tenants["b"]

    a_rows = category_breakdown(session, organisation_id=a["org"].id)
    b_rows = category_breakdown(session, organisation_id=b["org"].id)

    assert sum(row["count"] for row in a_rows) == 2
    assert sum(row["count"] for row in b_rows) == 1


def test_a_custom_category_is_not_offered_to_another_organisation(session, two_tenants):
    from feedbackiq.db.models import Category

    a, b = two_tenants["a"], two_tenants["b"]
    session.add(
        Category(
            organisation_id=a["org"].id, key="a_private_theme", name="A Private Theme",
            description="Only organisation A defines this.", source="custom",
        )
    )
    session.flush()

    a_keys = {row["key"] for row in available_categories(session, organisation_id=a["org"].id)}
    b_keys = {row["key"] for row in available_categories(session, organisation_id=b["org"].id)}

    assert "a_private_theme" in a_keys
    assert "a_private_theme" not in b_keys


# ---------------------------------------------------------------- imports and jobs


def test_an_import_listing_shows_only_its_own_organisations_imports(session, two_tenants):
    a, b = two_tenants["a"], two_tenants["b"]

    a_batches = list_import_batches(session, organisation_id=a["org"].id)
    b_batches = list_import_batches(session, organisation_id=b["org"].id)

    assert [batch.id for batch in a_batches] == [a["import"].id]
    assert [batch.id for batch in b_batches] == [b["import"].id]


def test_import_detail_refuses_another_organisations_id(session, two_tenants):
    a, b = two_tenants["a"], two_tenants["b"]

    assert get_import_batch(
        session, organisation_id=b["org"].id, import_batch_id=b["import"].id
    ) is not None
    assert get_import_batch(
        session, organisation_id=a["org"].id, import_batch_id=b["import"].id
    ) is None


def test_job_detail_refuses_another_organisations_id(session, two_tenants):
    a, b = two_tenants["a"], two_tenants["b"]

    assert job_queue.get_job(
        session, b["job"].id, organisation_id=b["org"].id
    ) is not None
    assert job_queue.get_job(
        session, b["job"].id, organisation_id=a["org"].id
    ) is None


# ---------------------------------------------------------------- the database itself


def test_the_database_still_refuses_a_cross_organisation_result(session, two_tenants):
    """
    The Milestone 4 guarantee, re-checked now that more code writes results: the composite
    foreign key means application logic is not the only thing standing between tenants.
    """
    from sqlalchemy.exc import IntegrityError

    a, b = two_tenants["a"], two_tenants["b"]

    with pytest.raises(IntegrityError):
        analyse(session, a["org"].id, [b["rows"][0]])
        session.flush()


def test_an_unknown_organisation_simply_has_no_data(session, two_tenants):
    """A scope that matches nothing returns empty, never everything - the failure mode of a
    missing WHERE clause."""
    nobody = uuid.uuid4()

    assert list_feedback(session, organisation_id=nobody).total == 0
    assert summary(session, organisation_id=nobody)["total_feedback"] == 0
    assert sentiment_trend(session, organisation_id=nobody) == []
    assert category_breakdown(session, organisation_id=nobody) == []
    assert list_import_batches(session, organisation_id=nobody) == []
