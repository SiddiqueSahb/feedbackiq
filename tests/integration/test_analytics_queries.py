"""
Reads and aggregates - feedbackiq.services.feedback, feedbackiq.services.analytics

Two things are being protected here.

**Correctness**: filters, paging and aggregates return what they claim, against a real
PostgreSQL with real rows.

**Where the work happens**: the aggregate endpoints must compute in SQL, not by fetching
rows into Python. That is asserted directly, by counting the statements a call issues -
the previous generation of this code read a 427 MB parquet file per request, and a test
that only checked the numbers would not have noticed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from feedbackiq.db.persistence import save_batch_analysis, save_feedback
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
from feedbackiq.services.feedback import (
    FeedbackFilters,
    InvalidFilter,
    get_feedback,
    list_feedback,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)

VERSIONS = {"engine": "1.0.0", "sentiment_model": "fake-1", "taxonomy_version": "2.0.0"}


def analysed_item(feedback_id, *, sentiment, key=None, name=None, score=0.8, unclassified=False):
    """One ItemAnalysis, as the engine would produce it."""
    category = None
    if key:
        category = CategoryMatch(category_id=key, name=name or key, score=score)
    elif unclassified:
        category = CategoryMatch(
            category_id=None, name="Unclassified / Emerging Complaint", score=0.2
        )

    return ItemAnalysis(
        feedback_id=str(feedback_id),
        sentiment=SentimentPrediction(
            label=sentiment, confidence=0.9, scores={sentiment: 0.9}, model_version="fake-1"
        ),
        category=category,
        is_unclassified=unclassified,
    )


@pytest.fixture()
def corpus(session, organisation, data_source, default_categories):
    """
    Nine pieces of feedback with known sentiment, category, rating, date and platform, plus
    one left unanalysed - because "not analysed yet" is a real state the API must show.
    """
    rows = save_feedback(
        session,
        organisation_id=organisation.id,
        data_source_id=data_source.id,
        items=[
            {"text": "waited forty minutes for a table", "rating": 1,
             "feedback_at": NOW, "metadata": {"platform": "yelp"}},
            {"text": "still queueing after an hour", "rating": 2,
             "feedback_at": NOW, "metadata": {"platform": "yelp"}},
            {"text": "charged me twice for one order", "rating": 1,
             "feedback_at": NOW - timedelta(days=1), "metadata": {"platform": "app store"}},
            {"text": "the parcel never arrived", "rating": 2,
             "feedback_at": NOW - timedelta(days=1), "metadata": {"platform": "app store"}},
            {"text": "the app crashes on login", "rating": 1,
             "feedback_at": NOW - timedelta(days=40), "metadata": {"platform": "app store"}},
            {"text": "staff were lovely and quick", "rating": 5,
             "feedback_at": NOW, "metadata": {"platform": "yelp"}},
            {"text": "good value overall", "rating": 4,
             "feedback_at": NOW, "metadata": {"platform": "yelp"}},
            {"text": "it was fine, nothing special", "rating": 3,
             "feedback_at": NOW, "metadata": {"platform": "yelp"}},
            {"text": "something vague and hard to place", "rating": 2,
             "feedback_at": NOW, "metadata": {"platform": "yelp"}},
            {"text": "not analysed yet", "rating": 3,
             "feedback_at": NOW, "metadata": {"platform": "yelp"}},
        ],
    )

    save_batch_analysis(
        session,
        organisation_id=organisation.id,
        analysis=BatchAnalysis(
            results=(
                analysed_item(rows[0].id, sentiment="negative",
                              key="wait_times_and_delays", name="Wait Times & Delays"),
                analysed_item(rows[1].id, sentiment="negative",
                              key="wait_times_and_delays", name="Wait Times & Delays"),
                analysed_item(rows[2].id, sentiment="negative",
                              key="billing_and_payments", name="Billing & Payments"),
                analysed_item(rows[3].id, sentiment="negative",
                              key="delivery_and_fulfilment", name="Delivery & Fulfilment"),
                analysed_item(rows[4].id, sentiment="negative",
                              key="app_and_technical_issues", name="App & Technical Issues"),
                analysed_item(rows[5].id, sentiment="positive"),
                analysed_item(rows[6].id, sentiment="positive"),
                analysed_item(rows[7].id, sentiment="neutral"),
                analysed_item(rows[8].id, sentiment="negative", unclassified=True),
            ),
            versions=VERSIONS,
        ),
    )
    session.commit()

    return rows


def count_statements(engine, function):
    """How many SQL statements one call issues."""
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        result = function()
    finally:
        event.remove(engine, "before_cursor_execute", record)

    return result, statements


# ---------------------------------------------------------------- listing


def test_listing_returns_a_page_with_its_total(session, organisation, corpus):
    page = list_feedback(session, organisation_id=organisation.id, page=1, page_size=4)

    assert len(page.items) == 4
    assert page.total == 10          # nine analysed plus the one that is not
    assert (page.page, page.page_size, page.pages) == (1, 4, 3)
    assert page.has_next is True


def test_paging_walks_the_whole_set_without_repeating(session, organisation, corpus):
    seen = []
    for number in (1, 2, 3):
        page = list_feedback(session, organisation_id=organisation.id, page=number, page_size=4)
        seen.extend(item["id"] for item in page.items)

    assert len(seen) == 10
    assert len(set(seen)) == 10       # a stable sort, so no row appears on two pages
    assert list_feedback(
        session, organisation_id=organisation.id, page=4, page_size=4
    ).items == []


def test_an_unanalysed_item_is_listed_with_null_analysis(session, organisation, corpus):
    page = list_feedback(
        session, organisation_id=organisation.id, filters=FeedbackFilters(analysed=False)
    )

    assert [item["text"] for item in page.items] == ["not analysed yet"]
    assert page.items[0]["sentiment"] is None
    assert page.items[0]["analysed"] is False


@pytest.mark.parametrize(
    "sentiment, expected", [("negative", 6), ("positive", 2), ("neutral", 1)]
)
def test_filtering_by_sentiment(session, organisation, corpus, sentiment, expected):
    page = list_feedback(
        session, organisation_id=organisation.id,
        filters=FeedbackFilters(sentiment=sentiment),
    )

    assert page.total == expected
    assert {item["sentiment"] for item in page.items} == {sentiment}


def test_filtering_by_category_uses_the_stable_key(session, organisation, corpus):
    page = list_feedback(
        session, organisation_id=organisation.id,
        filters=FeedbackFilters(category_key="wait_times_and_delays"),
    )

    assert page.total == 2
    assert {item["category_key"] for item in page.items} == {"wait_times_and_delays"}
    assert {item["category"] for item in page.items} == {"Wait Times & Delays"}


def test_filtering_for_unclassified_feedback(session, organisation, corpus):
    page = list_feedback(
        session, organisation_id=organisation.id,
        filters=FeedbackFilters(unclassified=True),
    )

    assert page.total == 1
    assert page.items[0]["is_unclassified"] is True


def test_filtering_by_platform_reads_the_upload_metadata(session, organisation, corpus):
    page = list_feedback(
        session, organisation_id=organisation.id,
        filters=FeedbackFilters(platform="app store"),
    )

    assert page.total == 3
    assert {item["metadata"]["platform"] for item in page.items} == {"app store"}


def test_filtering_by_date_range(session, organisation, corpus):
    page = list_feedback(
        session, organisation_id=organisation.id,
        filters=FeedbackFilters(date_from=NOW - timedelta(days=2), date_to=NOW),
    )

    assert page.total == 9           # excludes the item from forty days earlier


def test_filtering_by_rating(session, organisation, corpus):
    page = list_feedback(
        session, organisation_id=organisation.id,
        filters=FeedbackFilters(min_rating=4),
    )

    assert page.total == 2
    assert all(item["rating"] >= 4 for item in page.items)


def test_searching_matches_a_substring_case_insensitively(session, organisation, corpus):
    page = list_feedback(
        session, organisation_id=organisation.id, filters=FeedbackFilters(search="PARCEL")
    )

    assert [item["text"] for item in page.items] == ["the parcel never arrived"]


def test_search_wildcards_are_escaped(session, organisation, corpus):
    """A caller typing % must not match everything."""
    page = list_feedback(
        session, organisation_id=organisation.id, filters=FeedbackFilters(search="%%")
    )

    assert page.total == 0


def test_filters_combine(session, organisation, corpus):
    page = list_feedback(
        session,
        organisation_id=organisation.id,
        filters=FeedbackFilters(sentiment="negative", platform="app store", min_rating=1),
    )

    assert page.total == 3


def test_sorting_by_rating(session, organisation, corpus):
    page = list_feedback(
        session, organisation_id=organisation.id, sort="rating", descending=True, page_size=3
    )

    ratings = [item["rating"] for item in page.items]
    assert ratings == sorted(ratings, reverse=True)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"page": 0}, "page must be"),
        ({"page_size": 5000}, "exceeds the maximum"),
        ({"sort": "; drop table feedback"}, "Cannot sort by"),
    ],
)
def test_invalid_paging_and_sorting_are_rejected(session, organisation, kwargs, message):
    with pytest.raises(InvalidFilter, match=message):
        list_feedback(session, organisation_id=organisation.id, **kwargs)


@pytest.mark.parametrize(
    "filters, message",
    [
        (FeedbackFilters(sentiment="grumpy"), "Unknown sentiment"),
        (FeedbackFilters(search="x"), "two characters"),
        (FeedbackFilters(min_rating=9), "outside 1-5"),
        (
            FeedbackFilters(date_from=NOW, date_to=NOW - timedelta(days=1)),
            "date_from is after date_to",
        ),
    ],
)
def test_invalid_filters_are_rejected(session, organisation, filters, message):
    with pytest.raises(InvalidFilter, match=message):
        list_feedback(session, organisation_id=organisation.id, filters=filters)


# ---------------------------------------------------------------- detail


def test_the_detail_view_includes_provenance(session, organisation, corpus):
    item = get_feedback(
        session, organisation_id=organisation.id, feedback_id=corpus[0].id
    )

    assert item["text"] == "waited forty minutes for a table"
    assert item["sentiment"] == "negative"
    assert item["category_key"] == "wait_times_and_delays"
    assert item["sentiment_model_version"] == "fake-1"
    assert item["analysed_at"] is not None
    # Internal bookkeeping stays internal.
    assert "content_hash" not in item
    assert "data_source_id" not in item


def test_an_unknown_id_returns_none(session, organisation):
    import uuid

    assert get_feedback(
        session, organisation_id=organisation.id, feedback_id=uuid.uuid4()
    ) is None


# ---------------------------------------------------------------- aggregates


def test_the_summary_counts_and_percentages(session, organisation, corpus):
    result = summary(session, organisation_id=organisation.id)

    assert result["total_feedback"] == 10
    assert result["analysed"] == 9
    assert result["not_analysed"] == 1
    assert result["sentiment_counts"] == {"positive": 2, "neutral": 1, "negative": 6}
    # Percentages are of analysed feedback, so they sum to 100 rather than being diluted.
    assert sum(result["sentiment_percentages"].values()) == pytest.approx(100, abs=0.2)
    assert result["unclassified"] == 1
    assert result["average_rating"] == pytest.approx(2.4, abs=0.05)
    assert result["earliest_feedback_at"] is not None


def test_the_summary_is_one_sql_statement(session, db_engine, organisation, corpus):
    """The whole point of this layer: aggregation happens in PostgreSQL."""
    session.commit()   # nothing pending, so the count reflects the call alone

    result, statements = count_statements(
        db_engine, lambda: summary(session, organisation_id=organisation.id)
    )

    assert result["total_feedback"] == 10
    assert len(statements) == 1
    assert "count" in statements[0].lower()


def test_the_summary_respects_filters(session, organisation, corpus):
    result = summary(
        session, organisation_id=organisation.id,
        filters=FeedbackFilters(platform="app store"),
    )

    assert result["total_feedback"] == 3
    assert result["sentiment_counts"]["negative"] == 3


def test_the_trend_buckets_by_day_in_sql(session, db_engine, organisation, corpus):
    session.commit()

    rows, statements = count_statements(
        db_engine, lambda: sentiment_trend(session, organisation_id=organisation.id, interval="day")
    )

    assert len(statements) == 1
    assert "date_trunc" in statements[0].lower()
    # Three distinct dates in the corpus; periods with no feedback are absent.
    assert len(rows) == 3
    assert sum(row["feedback_count"] for row in rows) == 10
    assert [row["period"] for row in rows] == sorted(row["period"] for row in rows)


@pytest.mark.parametrize("interval, expected", [("day", 3), ("month", 2)])
def test_the_trend_supports_coarser_intervals(session, organisation, corpus, interval, expected):
    rows = sentiment_trend(session, organisation_id=organisation.id, interval=interval)

    assert len(rows) == expected


def test_an_unknown_trend_interval_is_rejected(session, organisation):
    with pytest.raises(InvalidFilter, match="Unknown interval"):
        sentiment_trend(session, organisation_id=organisation.id, interval="fortnight")


def test_the_category_breakdown_groups_by_key(session, db_engine, organisation, corpus):
    session.commit()

    rows, statements = count_statements(
        db_engine, lambda: category_breakdown(session, organisation_id=organisation.id)
    )

    assert len(statements) == 1
    by_key = {row["category_key"]: row for row in rows}

    assert by_key["wait_times_and_delays"]["count"] == 2
    assert by_key["wait_times_and_delays"]["sentiment_counts"]["negative"] == 2
    assert by_key["billing_and_payments"]["count"] == 1
    # Largest first.
    assert rows[0]["count"] >= rows[-1]["count"]
    # Percentages are of analysed feedback.
    assert sum(row["percentage"] for row in rows) == pytest.approx(100, abs=0.5)


def test_results_without_a_category_are_reported_not_hidden(session, organisation, corpus):
    """The unclassified bucket is the emerging-issue signal; dropping it would waste it."""
    rows = category_breakdown(session, organisation_id=organisation.id)

    uncategorised = [row for row in rows if row["category_key"] is None]

    assert len(uncategorised) == 1
    # One unclassified item plus the three positive/neutral ones the gate skipped.
    assert uncategorised[0]["count"] == 4


def test_available_categories_offers_the_active_taxonomy(session, organisation, default_categories):
    rows = available_categories(session, organisation_id=organisation.id)

    keys = {row["key"] for row in rows}
    assert "billing_and_payments" in keys
    assert len(rows) == 13
    assert all(row["description"] for row in rows)


def test_a_retired_research_category_is_not_offered(session, organisation, default_categories):
    """Migration 0003 marks them inactive; they stay in the table so old results resolve."""
    from feedbackiq.db.models import Category

    session.add(
        Category(
            organisation_id=None, key="salon_service_failures", name="Salon Service Failures",
            description="A retired research category.", source="discovered", is_active=False,
        )
    )
    session.flush()

    keys = {row["key"] for row in available_categories(session, organisation_id=organisation.id)}

    assert "salon_service_failures" not in keys


def test_an_organisations_own_category_shadows_the_default(session, organisation, default_categories):
    from feedbackiq.db.models import Category

    session.add(
        Category(
            organisation_id=organisation.id, key="billing_and_payments",
            name="Invoicing", description="What this customer calls billing.", source="custom",
        )
    )
    session.flush()

    rows = {row["key"]: row for row in available_categories(session, organisation_id=organisation.id)}

    assert rows["billing_and_payments"]["name"] == "Invoicing"
    assert rows["billing_and_payments"]["organisation_specific"] is True
