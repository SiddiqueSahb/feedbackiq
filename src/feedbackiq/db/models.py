"""
The database schema, designed around what the analytics engine actually produces.

Ownership classification (documented per table below, and in milestone-04.md):

  TENANT-OWNED    belongs to one organisation; `organisation_id` is NOT NULL and every
                  lookup index leads with it: data_sources, import_batches, feedback,
                  analysis_runs, analysis_results, insights, jobs, and organisation-owned
                  categories
  GLOBAL/REFERENCE shared across organisations: the seeded default taxonomy
                  (`categories.organisation_id IS NULL`)
  SYSTEM/INTERNAL the application's own bookkeeping: `jobs` (and, later, migrations'
                  own alembic_version table)
  IDENTITY        people, not customer data, and deliberately global: `users` (Milestone 7).
                  One person may belong to several organisations, so a user row carries
                  no organisation_id.

Everything a tenant-isolation layer needs is here - the column, the foreign keys, and
composite keys that stop one organisation's row referencing another's.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from feedbackiq.db.base import Base, created_at_column, uuid_primary_key

# Status vocabularies. Kept as CHECK constraints rather than PostgreSQL ENUM types:
# adding a value to an enum needs its own migration and locks, while a CHECK is a
# one-line change - and these lists will grow as the product does.
IMPORT_STATUSES = ("pending", "importing", "completed", "failed")
RUN_STATUSES = ("queued", "running", "completed", "failed")
RESULT_STATUSES = ("ok", "failed")
JOB_STATUSES = ("queued", "running", "succeeded", "failed")
CATEGORY_SOURCES = ("default", "custom", "discovered")
# Deliberately small (Milestone 7). owner: everything a member can do, plus managing the
# organisation's membership once that exists. member: use the organisation's data. More
# roles (admin, viewer) are one value here plus a migration.
MEMBERSHIP_ROLES = ("owner", "member")


def _one_of(column: str, allowed: tuple[str, ...]) -> str:
    """
    SQL for `column in ('a', 'b')`, built from the tuples above.

    Writing the CHECK by hand as well as the tuple would let the two drift apart - the
    constraint would allow a value the code rejects, or vice versa.
    """
    values = ", ".join(f"'{value}'" for value in allowed)
    return f"{column} in ({values})"


class Organisation(Base):
    """
    The tenant root. TENANT-OWNED data hangs off this table.

    One development organisation is seeded for now (`seed.py`); nothing in the schema
    assumes there will only ever be one.
    """

    __tablename__ = "organisations"

    id: Mapped[uuid.UUID] = uuid_primary_key()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    # Soft delete: offboarding should stop access immediately but keep the data
    # recoverable until a purge job runs. Nothing reads it yet.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()

    data_sources: Mapped[list["DataSource"]] = relationship(
        back_populates="organisation", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Organisation {self.slug}>"


class User(Base):
    """
    A person who can sign in. IDENTITY - global, not tenant-owned.

    No `organisation_id`: which organisations a user may act for is a separate fact, held by
    memberships, because one person can belong to more than one.

    Two constraints make email identity case-insensitive in the database itself rather than
    only in application code: the address must be stored already normalised (trimmed and
    lower-case - see auth/credentials.py), and it must be unique. Together, "Ana@x.com" and
    "ana@x.com" cannot become two accounts even if a future code path forgets to normalise.

    `password_hash` is an argon2id hash string. The password itself is never stored.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint("email = lower(btrim(email))", name="email_normalised"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    # 254: the longest address that can receive mail (RFC 5321).
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # A disabled user cannot sign in, and their existing sessions stop working. Disabling
    # rather than deleting keeps who-did-what history intact.
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        # The id, not the email: reprs end up in logs and tracebacks.
        return f"<User {self.id}>"


class OrganisationMembership(Base):
    """
    Which organisations a user may act for, and in what role. The bridge between IDENTITY
    and TENANT-OWNED data.

        users  1 ── * organisation_memberships * ── 1  organisations

    **This row is the only thing that makes an organisation's data reachable by a signed-in
    user.** An organisation id sent by a client - in a URL, query, body, header or CSV - is
    never a substitute for it.

    Roles are a CHECK constraint (MEMBERSHIP_ROLES) rather than a PostgreSQL enum, like the
    status columns, so a later milestone can add one with a one-line migration.

    Deleting a user or an organisation removes its memberships and leaves the other side
    alone. "An organisation always keeps an owner" is a rule for service code (it is awkward
    in SQL); nothing removes memberships yet.
    """

    __tablename__ = "organisation_memberships"
    __table_args__ = (
        # One membership per person per organisation. Leads with user_id, so it also serves
        # the lookup every authenticated request makes: "what may this user act for?"
        UniqueConstraint(
            "user_id", "organisation_id",
            name="uq_organisation_memberships_user_id_organisation_id",
        ),
        CheckConstraint(_one_of("role", MEMBERSHIP_ROLES), name="role_valid"),
        # "Who belongs to this organisation?" - the other direction.
        Index("ix_organisation_memberships_organisation_id", "organisation_id"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    # No default: a membership is created with a role chosen on purpose.
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:
        return f"<OrganisationMembership user={self.user_id} org={self.organisation_id} {self.role}>"


class UserSession(Base):
    """
    A signed-in browser. IDENTITY - global, like `users`.

    Server-side sessions: the cookie carries a random token, and this row is what makes that
    token mean something. Signing out, or cutting off a stolen session, is deleting the row -
    something a self-contained token such as a JWT cannot offer before it expires.

    Only `token_hash` is stored, never the token (auth/session_tokens.py).

    Named `user_sessions`, not `sessions`, so it is never confused with a SQLAlchemy Session.

    `organisation_id` is which organisation this sign-in acts for, chosen at sign-in from the
    user's memberships. **It is a choice, not a permission**: every request re-checks it
    against `organisation_memberships`, so removing a membership takes effect on the next
    request. NULL for a user with no organisation, and set to NULL if the organisation is
    deleted - the user stays signed in, with nothing to reach.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
        # "sign this user out everywhere", and the cascade when a user is deleted.
        Index("ix_user_sessions_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="SET NULL")
    )
    # SHA-256 hex of the cookie token.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:
        # Never the token hash: reprs end up in logs.
        return f"<UserSession {self.id} user={self.user_id}>"


class DataSource(Base):
    """
    Where a piece of feedback came from. TENANT-OWNED.

    Replaces the dissertation's hard-coded `platform` column (amazon/yelp/
    twitter_airline), which is meaningless for a customer's own feedback. `kind` says how
    it arrives; `settings` holds per-source configuration for future integrations. No
    integration is implemented in this milestone - `csv_upload` is all the current
    workflow needs.
    """

    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("organisation_id", "name", name="uq_data_sources_org_name"),
        Index("ix_data_sources_organisation_id", "organisation_id"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # csv_upload today; api / review_platform / support_system later.
    kind: Mapped[str] = mapped_column(String(50), nullable=False, default="csv_upload")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = created_at_column()

    organisation: Mapped[Organisation] = relationship(back_populates="data_sources")

    def __repr__(self) -> str:
        return f"<DataSource {self.name} ({self.kind})>"


class ImportBatch(Base):
    """
    One upload, so a customer's data can be traced back to where it came from.
    TENANT-OWNED.

    Counts are stored rather than computed because they describe what happened at import
    time - rows the customer sent, rows accepted, rows rejected - which a later COUNT(*)
    over `feedback` cannot reconstruct after de-duplication.

    No background processing exists yet: a batch is created and completed in the same
    call. The columns are what a future worker will update.
    """

    __tablename__ = "import_batches"
    __table_args__ = (
        CheckConstraint(_one_of("status", IMPORT_STATUSES), name="status_valid"),
        Index("ix_import_batches_organisation_id_created_at", "organisation_id", "created_at"),
        # "has this organisation already uploaded this exact file?" - the batch-level half
        # of duplicate handling. An index, not a unique constraint: re-uploading the same
        # file is a legitimate thing to do, so the service decides what to do about it
        # rather than the database refusing the insert.
        Index("ix_import_batches_organisation_id_source_hash", "organisation_id", "source_hash"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False
    )
    # What the customer uploaded, and where it was put. `storage_reference` is a path or
    # object key - the file itself never goes in the database.
    original_filename: Mapped[str | None] = mapped_column(String(500))
    storage_reference: Mapped[str | None] = mapped_column(String(1000))
    # SHA-256 of the uploaded bytes, so an identical re-upload is recognisable. NULL for a
    # batch that did not come from a file.
    source_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Row-level problems, e.g. [{"row": 12, "error": "no text"}]. JSONB because the shape
    # varies and nothing queries inside it.
    error_summary: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<ImportBatch {self.original_filename} {self.status}>"


class Feedback(Base):
    """
    One piece of customer feedback, as received. TENANT-OWNED.

    This is the engine's `FeedbackItem` plus the context a product needs: who it belongs
    to, where it came from, when the customer wrote it.

    Deliberately NOT stored:
      * `cleaned_text` - derived, and the engine decided (Milestone 3) to serve the
        original text; storing a derivative invites the two drifting apart
      * model predictions - those are analysis results, not input, and the dissertation's
        `sentiment_label` column was a rating-derived label, not a prediction
      * author names, emails or handles - personal data the product does not need. A
        caller who must identify a reviewer puts an opaque reference in `metadata`.

    `content_hash` is a hash of the normalised text, so a re-upload of the same file can
    be detected without comparing long strings.
    """

    __tablename__ = "feedback"
    __table_args__ = (
        # One row per source-provided identifier, per organisation. Partial, because most
        # feedback (a pasted CSV row) has no external id and NULLs must not collide.
        Index(
            "uq_feedback_org_source_external",
            "organisation_id", "data_source_id", "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        # Lets other tenant tables point at feedback with a composite foreign key, so the
        # database itself refuses a cross-organisation reference.
        UniqueConstraint("organisation_id", "id", name="uq_feedback_organisation_id_id"),
        # The dashboard's main access pattern: one organisation's feedback, newest first.
        Index("ix_feedback_organisation_id_feedback_at", "organisation_id", "feedback_at"),
        Index("ix_feedback_organisation_id_content_hash", "organisation_id", "content_hash"),
        Index("ix_feedback_import_batch_id", "import_batch_id"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False
    )
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL")
    )
    # The customer's own identifier for this item, when they have one.
    external_id: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional: plenty of feedback (support tickets, NPS comments) has no rating.
    rating: Mapped[float | None] = mapped_column(Numeric(3, 1))
    language: Mapped[str | None] = mapped_column(String(10))
    # When the customer wrote it, as opposed to when we ingested it.
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Product, plan, region, segment - whatever the customer sends. JSONB because the
    # keys differ per organisation and a column per key is not possible.
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Ingestion time. `created_at` elsewhere; named the same here for consistency.
    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:
        return f"<Feedback {self.id} {self.text[:30]!r}>"


class Category(Base):
    """
    A category the categoriser may assign. GLOBAL/REFERENCE or TENANT-OWNED.

      organisation_id IS NULL      the seeded default taxonomy (the dissertation's 24),
                                   shared and read-only for customers
      organisation_id IS NOT NULL  an organisation's own taxonomy

    That single nullable column is the whole "configurable taxonomies later" story: an
    organisation copies or writes its own rows, the engine receives whichever set the
    caller passes, and no schema change is needed. No category-management UI exists yet.

    `description` is the NLI hypothesis the zero-shot model compares against, which is
    why it is NOT NULL - it is functional, not documentation. `exemplars` sharpen the
    embedding shortlist.
    """

    __tablename__ = "categories"
    __table_args__ = (
        # The stable identity, unique in the same two scopes as the name: per organisation,
        # and among the global defaults (where organisation_id IS NULL treats NULLs as
        # distinct, so a partial index is needed as well).
        UniqueConstraint("organisation_id", "key", name="uq_categories_organisation_id_key"),
        Index(
            "uq_categories_default_key",
            "key",
            unique=True,
            postgresql_where=text("organisation_id IS NULL"),
        ),
        # Unique per organisation ...
        UniqueConstraint("organisation_id", "name", name="uq_categories_organisation_id_name"),
        # ... and unique among the global defaults, which the constraint above cannot
        # enforce because PostgreSQL treats NULLs as distinct.
        Index(
            "uq_categories_default_name",
            "name",
            unique=True,
            postgresql_where=text("organisation_id IS NULL"),
        ),
        CheckConstraint(_one_of("source", CATEGORY_SOURCES), name="source_valid"),
        Index("ix_categories_organisation_id", "organisation_id"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    # NULL = global default taxonomy. See the class docstring.
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE")
    )
    # The stable identity: lower_snake_case, machine-readable, never regenerated when the
    # display name changes. Stored results refer to a category by row id, and a caller's
    # taxonomy refers to it by this key - so "Delivery Issues" can become
    # "Shipping & Delivery" without orphaning a single result. `name` is the label.
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    exemplars: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    # complaint today; praise / request later, if a taxonomy needs them.
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="complaint")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="default")
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    def __repr__(self) -> str:
        scope = "default" if self.organisation_id is None else "org"
        return f"<Category {self.name} ({scope})>"


class AnalysisRun(Base):
    """
    One processing operation over a group of feedback. TENANT-OWNED.

        Import Batch  ->  Analysis Run  ->  many Analysis Results

    The version columns come straight from the engine's own manifest
    (`BatchAnalysis.versions`, Milestone 3) - no second versioning scheme is invented
    here. `engine_version` gets its own column because "show me everything produced by
    engine 1.0.0" is a question worth an index; the rest stay as JSONB because they are
    read as a block when explaining a result.

    Usage is normalised into four integers rather than JSONB: these are the numbers a
    later usage-metering milestone must SUM per organisation and per month.
    """

    __tablename__ = "analysis_runs"
    __table_args__ = (
        CheckConstraint(_one_of("status", RUN_STATUSES), name="status_valid"),
        Index("ix_analysis_runs_organisation_id_created_at", "organisation_id", "created_at"),
        Index("ix_analysis_runs_import_batch_id", "import_batch_id"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL")
    )
    # import | manual | reanalysis - why this run happened.
    trigger: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")

    engine_version: Mapped[str] = mapped_column(String(50), nullable=False)
    model_versions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    prompt_versions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    thresholds: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # UsageStats, flattened - what metering and cost control will read.
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why the run itself failed (as opposed to an individual record).
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()

    results: Mapped[list["AnalysisResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<AnalysisRun {self.id} {self.status} ({self.item_count} items)>"


class AnalysisResult(Base):
    """
    What the engine concluded about one piece of feedback. TENANT-OWNED.

    This is `ItemAnalysis` persisted. Normalisation follows what will be queried:

      columns   sentiment label and confidence, category and its confidence,
                `is_unclassified`, status - these are filtered, grouped and aggregated by
                every dashboard question ("negative feedback per category this month")
      JSONB     `sentiment_scores` (three floats read as a block) and
                `candidate_categories` (the near-misses, an audit trail nothing filters on)
      not kept  `ItemAnalysis.evidence` - retrieval output, high volume and low value once
                the answer exists. What the LLM actually used is recorded on the insight
                (`Insight.evidence_ids`), which is the traceability that matters.

    `is_current` marks the result a dashboard should read, so re-analysing with a newer
    model adds a row instead of overwriting history.
    """

    __tablename__ = "analysis_results"
    __table_args__ = (
        # One result per feedback item per run.
        UniqueConstraint("analysis_run_id", "feedback_id", name="uq_analysis_results_run_feedback"),
        # The database refuses a result pointing at another organisation's feedback, even
        # if application code has a bug.
        ForeignKeyConstraint(
            ["organisation_id", "feedback_id"],
            ["feedback.organisation_id", "feedback.id"],
            name="fk_analysis_results_org_feedback",
            ondelete="CASCADE",
        ),
        CheckConstraint(_one_of("status", RESULT_STATUSES), name="status_valid"),
        # Exactly one current result per feedback item.
        Index(
            "uq_analysis_results_current_feedback",
            "feedback_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        Index(
            "ix_analysis_results_organisation_id_sentiment_label",
            "organisation_id", "sentiment_label",
        ),
        Index("ix_analysis_results_organisation_id_category_id", "organisation_id", "category_id"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    feedback_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")
    error: Mapped[str | None] = mapped_column(Text)

    sentiment_label: Mapped[str | None] = mapped_column(String(20))
    sentiment_confidence: Mapped[float | None] = mapped_column(Numeric(6, 4))
    sentiment_scores: Mapped[dict | None] = mapped_column(JSONB)
    sentiment_model_version: Mapped[str | None] = mapped_column(String(120))

    # NULL when the sentiment gate skipped categorisation, when the result is
    # unclassified, or when no taxonomy was supplied.
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )
    category_confidence: Mapped[float | None] = mapped_column(Numeric(6, 4))
    is_unclassified: Mapped[bool] = mapped_column(nullable=False, default=False)
    # The engine's explanation, e.g. "sentiment 'positive' is not categorised".
    categorisation_skipped: Mapped[str | None] = mapped_column(Text)
    candidate_categories: Mapped[list | None] = mapped_column(JSONB)

    is_current: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_column()

    run: Mapped[AnalysisRun] = relationship(back_populates="results")
    insight: Mapped["Insight | None"] = relationship(
        back_populates="result", cascade="all, delete-orphan", uselist=False
    )

    def __repr__(self) -> str:
        return f"<AnalysisResult {self.feedback_id} {self.sentiment_label}>"


class Insight(Base):
    """
    The LLM's reading of one result. TENANT-OWNED.

    A separate table rather than more columns on `analysis_results`, for three reasons:
    it is optional (bulk analysis runs without an LLM, so most results have none), it is
    generated separately and can be regenerated, and it carries its own prompt and model
    versions. Keeping it apart leaves the result row narrow for the aggregate queries
    that run constantly.

    `severity`, `priority` and `department` get columns because triage filters on them.
    `evidence_ids` records what the model was actually shown - the traceability property
    the dissertation measured (RQ4).
    """

    __tablename__ = "insights"
    __table_args__ = (
        Index("ix_insights_organisation_id_severity", "organisation_id", "severity"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    analysis_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_results.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    business_insight: Mapped[str] = mapped_column(Text, nullable=False)
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    severity: Mapped[str | None] = mapped_column(String(20))
    priority: Mapped[str | None] = mapped_column(String(20))
    department: Mapped[str | None] = mapped_column(String(50))

    # The feedback the model was given as evidence. Plain text ids, because evidence may
    # later come from a source that is not a `feedback` row.
    evidence_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    prompt_version: Mapped[str | None] = mapped_column(String(80))
    model_version: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = created_at_column()

    result: Mapped[AnalysisResult] = relationship(back_populates="insight")

    def __repr__(self) -> str:
        return f"<Insight {self.severity} {self.summary[:30]!r}>"


class Job(Base):
    """
    Work to be done later. SYSTEM/INTERNAL, but scoped to an organisation.

    Deliberately the simplest thing that could work: a table, a status, a retry counter.
    **No queue, no broker, no worker is implemented in this milestone** - this is the
    persistence model a future worker will use.

    What that worker will do (Milestone 5): claim a `queued` row with
    `SELECT ... FOR UPDATE SKIP LOCKED`, run the engine over the import batch named in
    `payload`, write `analysis_results`, and mark the row `succeeded` or `failed`. Keeping
    the queue in PostgreSQL means one fewer moving part than Redis or a broker, and it is
    transactional with the data it describes.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_one_of("status", JOB_STATUSES), name="status_valid"),
        # How a worker will find its next piece of work.
        Index("ix_jobs_status_run_after", "status", "run_after"),
        Index("ix_jobs_organisation_id", "organisation_id"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    # analyse_import | reanalyse | compute_insights ...
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    # Identifiers only - never feedback text, which would duplicate customer data here.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<Job {self.kind} {self.status}>"
