"""
Tenant ownership and the constraints that enforce it - feedbackiq.db.models

No authentication exists yet (that is a later milestone), so the guarantees tested here
are the *database's*: every piece of customer data carries an organisation, and the schema
refuses a row that would mix two organisations together even if application code has a
bug. These are the tests that make the eventual tenant-isolation layer verifiable rather
than hopeful.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from feedbackiq.db.models import AnalysisResult, AnalysisRun, Category, Feedback
from feedbackiq.db.persistence import create_data_source, save_feedback

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------- ownership


def test_feedback_is_owned_by_one_organisation(session, organisation, data_source):
    [row] = save_feedback(
        session,
        organisation_id=organisation.id,
        data_source_id=data_source.id,
        texts=["the app crashes on launch"],
    )
    session.commit()

    stored = session.get(Feedback, row.id)
    assert stored.organisation_id == organisation.id
    assert stored.data_source_id == data_source.id
    assert stored.content_hash  # hashed for duplicate detection


def test_feedback_cannot_exist_without_an_organisation(session, data_source):
    session.add(
        Feedback(
            organisation_id=None,
            data_source_id=data_source.id,
            text="orphan",
            content_hash="x" * 64,
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_feedback_cannot_reference_a_nonexistent_organisation(session, data_source):
    session.add(
        Feedback(
            organisation_id=uuid.uuid4(),   # no such organisation
            data_source_id=data_source.id,
            text="ghost tenant",
            content_hash="x" * 64,
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_one_organisations_data_is_invisible_in_the_others_query(
    session, organisation, other_organisation, data_source
):
    """The access pattern every dashboard query will use: filter by organisation_id."""
    their_source = create_data_source(
        session, organisation_id=other_organisation.id, name="CSV upload"
    )
    save_feedback(
        session, organisation_id=organisation.id, data_source_id=data_source.id,
        texts=["ours"],
    )
    save_feedback(
        session, organisation_id=other_organisation.id, data_source_id=their_source.id,
        texts=["theirs"],
    )
    session.commit()

    ours = session.scalars(
        select(Feedback.text).where(Feedback.organisation_id == organisation.id)
    ).all()

    assert ours == ["ours"]


def test_a_result_cannot_point_at_another_organisations_feedback(
    session, organisation, other_organisation, data_source
):
    """
    The composite foreign key `fk_analysis_results_org_feedback` in action.

    A plain `feedback_id` column would have accepted this row, and one tenant would be
    looking at another tenant's analysis. The database refuses it instead.
    """
    [their_feedback] = save_feedback(
        session,
        organisation_id=other_organisation.id,
        data_source_id=create_data_source(
            session, organisation_id=other_organisation.id, name="CSV upload"
        ).id,
        texts=["their complaint"],
    )

    run = AnalysisRun(organisation_id=organisation.id, engine_version="1.0.0", status="completed")
    session.add(run)
    session.flush()

    session.add(
        AnalysisResult(
            organisation_id=organisation.id,      # our organisation ...
            feedback_id=their_feedback.id,        # ... their feedback
            analysis_run_id=run.id,
            sentiment_label="negative",
        )
    )

    with pytest.raises(IntegrityError) as error:
        session.flush()

    assert "fk_analysis_results_org_feedback" in str(error.value)


# ---------------------------------------------------------------- duplicates


def test_the_same_external_id_cannot_be_imported_twice(session, organisation, data_source):
    save_feedback(
        session, organisation_id=organisation.id, data_source_id=data_source.id,
        items=[{"text": "first", "external_id": "REVIEW-1"}],
    )
    session.commit()

    # save_feedback flushes, so this is where the partial unique index bites.
    with pytest.raises(IntegrityError):
        save_feedback(
            session, organisation_id=organisation.id, data_source_id=data_source.id,
            items=[{"text": "again", "external_id": "REVIEW-1"}],
        )


def test_two_organisations_may_use_the_same_external_id(
    session, organisation, other_organisation, data_source
):
    their_source = create_data_source(
        session, organisation_id=other_organisation.id, name="CSV upload"
    )

    save_feedback(
        session, organisation_id=organisation.id, data_source_id=data_source.id,
        items=[{"text": "ours", "external_id": "REVIEW-1"}],
    )
    save_feedback(
        session, organisation_id=other_organisation.id, data_source_id=their_source.id,
        items=[{"text": "theirs", "external_id": "REVIEW-1"}],
    )

    session.commit()   # the unique index is per organisation, so this is fine

    assert session.scalars(select(Feedback.external_id)).all() == ["REVIEW-1", "REVIEW-1"]


def test_feedback_without_an_external_id_is_never_a_duplicate(session, organisation, data_source):
    """Why the index is partial: most feedback has no external id, and NULLs must not
    collide with each other."""
    save_feedback(
        session, organisation_id=organisation.id, data_source_id=data_source.id,
        texts=["a pasted row", "another pasted row", "a third"],
    )

    session.commit()

    assert len(session.scalars(select(Feedback.id)).all()) == 3


# ---------------------------------------------------------------- the taxonomy


def test_a_default_category_name_is_unique_across_the_defaults(session):
    # Distinct keys on purpose, so the *name* constraint is what fails. With the same key
    # this test would pass for the wrong reason.
    session.add_all(
        [
            Category(organisation_id=None, key="billing_one", name="Billing",
                     description="Charged wrongly."),
            Category(organisation_id=None, key="billing_two", name="Billing",
                     description="A duplicate."),
        ]
    )

    with pytest.raises(IntegrityError) as error:
        session.flush()

    assert "uq_categories_default_name" in str(error.value)


def test_a_default_category_key_is_unique_across_the_defaults(session):
    """The stable identity has to be unique, or two categories are the same category."""
    session.add_all(
        [
            Category(organisation_id=None, key="billing", name="Billing",
                     description="Charged wrongly."),
            Category(organisation_id=None, key="billing", name="Billing & Charges",
                     description="A different label, the same key."),
        ]
    )

    with pytest.raises(IntegrityError) as error:
        session.flush()

    assert "uq_categories_default_key" in str(error.value)


def test_an_organisation_may_name_its_own_category_after_a_default(session, organisation):
    """How organisation-specific taxonomies will work: the same name in a different scope
    is a different category, and the organisation's own row is the one that wins."""
    session.add_all(
        [
            Category(organisation_id=None, key="billing", name="Billing",
                     description="The default."),
            Category(
                organisation_id=organisation.id,
                key="billing",
                name="Billing",
                description="How this customer defines billing complaints.",
                source="custom",
            ),
        ]
    )

    session.commit()

    scoped = session.scalars(
        select(Category.description).where(Category.organisation_id == organisation.id)
    ).all()
    assert scoped == ["How this customer defines billing complaints."]


def test_an_organisation_cannot_have_two_categories_with_one_name(session, organisation):
    session.add_all(
        [
            Category(organisation_id=organisation.id, key="billing_one", name="Billing",
                     description="One."),
            Category(organisation_id=organisation.id, key="billing_two", name="Billing",
                     description="Two."),
        ]
    )

    with pytest.raises(IntegrityError) as error:
        session.flush()

    assert "uq_categories_organisation_id_name" in str(error.value)


def test_an_unknown_category_source_is_rejected(session):
    session.add(
        Category(
            organisation_id=None,
            key="nonsense",
            name="Nonsense",
            description="A category with an invalid source.",
            source="invented",
        )
    )

    with pytest.raises(IntegrityError) as error:
        session.flush()

    assert "ck_categories_source_valid" in str(error.value)
