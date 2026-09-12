"""initial schema

The first persistence layer: organisations and the data that belongs to them.

    organisations
      ├─ data_sources ─ import_batches ─ feedback
      ├─ categories            (organisation_id NULL = the shared default taxonomy)
      ├─ analysis_runs ─ analysis_results ─ insights
      └─ jobs

Generated with `alembic revision --autogenerate` from db/models.py and then reviewed.
Worth knowing when reading it:

  * `uq_feedback_org_source_external`, `uq_categories_default_name` and
    `uq_analysis_results_current_feedback` are PARTIAL unique indexes - the
    `postgresql_where` argument is the point of them, not an optimisation.
  * `fk_analysis_results_org_feedback` is a composite foreign key to
    `feedback (organisation_id, id)`. It is what makes it impossible for one
    organisation's analysis to reference another organisation's feedback.
  * `feedback.metadata` is a column name, not SQLAlchemy's `MetaData`; the model
    attribute is called `meta` because `metadata` is reserved.

Revision ID: 0001
Revises:
Created: 2026-09-12 21:15:07.893825+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tables are created parents-first, so every foreign key has something to point at.
    op.create_table('organisations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organisations')),
    sa.UniqueConstraint('slug', name=op.f('uq_organisations_slug'))
    )
    op.create_table('categories',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('organisation_id', sa.Uuid(), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('exemplars', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("source in ('default', 'custom', 'discovered')", name=op.f('ck_categories_source_valid')),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], name=op.f('fk_categories_organisation_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_categories')),
    sa.UniqueConstraint('organisation_id', 'name', name='uq_categories_organisation_id_name')
    )
    op.create_index('ix_categories_organisation_id', 'categories', ['organisation_id'], unique=False)
    op.create_index('uq_categories_default_name', 'categories', ['name'], unique=True, postgresql_where=sa.text('organisation_id IS NULL'))
    op.create_table('data_sources',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('organisation_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=50), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], name=op.f('fk_data_sources_organisation_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_data_sources')),
    sa.UniqueConstraint('organisation_id', 'name', name='uq_data_sources_org_name')
    )
    op.create_index('ix_data_sources_organisation_id', 'data_sources', ['organisation_id'], unique=False)
    op.create_table('jobs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('organisation_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=50), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('run_after', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('locked_by', sa.String(length=100), nullable=True),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status in ('queued', 'running', 'succeeded', 'failed')", name=op.f('ck_jobs_status_valid')),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], name=op.f('fk_jobs_organisation_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    op.create_index('ix_jobs_organisation_id', 'jobs', ['organisation_id'], unique=False)
    op.create_index('ix_jobs_status_run_after', 'jobs', ['status', 'run_after'], unique=False)
    op.create_table('import_batches',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('organisation_id', sa.Uuid(), nullable=False),
    sa.Column('data_source_id', sa.Uuid(), nullable=False),
    sa.Column('original_filename', sa.String(length=500), nullable=True),
    sa.Column('storage_reference', sa.String(length=1000), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('imported_count', sa.Integer(), nullable=False),
    sa.Column('failed_count', sa.Integer(), nullable=False),
    sa.Column('error_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status in ('pending', 'importing', 'completed', 'failed')", name=op.f('ck_import_batches_status_valid')),
    sa.ForeignKeyConstraint(['data_source_id'], ['data_sources.id'], name=op.f('fk_import_batches_data_source_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], name=op.f('fk_import_batches_organisation_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_batches'))
    )
    op.create_index('ix_import_batches_organisation_id_created_at', 'import_batches', ['organisation_id', 'created_at'], unique=False)
    op.create_table('analysis_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('organisation_id', sa.Uuid(), nullable=False),
    sa.Column('import_batch_id', sa.Uuid(), nullable=True),
    sa.Column('trigger', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('engine_version', sa.String(length=50), nullable=False),
    sa.Column('model_versions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('prompt_versions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('thresholds', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('item_count', sa.Integer(), nullable=False),
    sa.Column('succeeded_count', sa.Integer(), nullable=False),
    sa.Column('failed_count', sa.Integer(), nullable=False),
    sa.Column('llm_calls', sa.Integer(), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('llm_retries', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status in ('queued', 'running', 'completed', 'failed')", name=op.f('ck_analysis_runs_status_valid')),
    sa.ForeignKeyConstraint(['import_batch_id'], ['import_batches.id'], name=op.f('fk_analysis_runs_import_batch_id'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], name=op.f('fk_analysis_runs_organisation_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_analysis_runs'))
    )
    op.create_index('ix_analysis_runs_import_batch_id', 'analysis_runs', ['import_batch_id'], unique=False)
    op.create_index('ix_analysis_runs_organisation_id_created_at', 'analysis_runs', ['organisation_id', 'created_at'], unique=False)
    op.create_table('feedback',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('organisation_id', sa.Uuid(), nullable=False),
    sa.Column('data_source_id', sa.Uuid(), nullable=False),
    sa.Column('import_batch_id', sa.Uuid(), nullable=True),
    sa.Column('external_id', sa.String(length=200), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('rating', sa.Numeric(precision=3, scale=1), nullable=True),
    sa.Column('language', sa.String(length=10), nullable=True),
    sa.Column('feedback_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['data_source_id'], ['data_sources.id'], name=op.f('fk_feedback_data_source_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['import_batch_id'], ['import_batches.id'], name=op.f('fk_feedback_import_batch_id'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], name=op.f('fk_feedback_organisation_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_feedback')),
    sa.UniqueConstraint('organisation_id', 'id', name='uq_feedback_organisation_id_id')
    )
    op.create_index('ix_feedback_import_batch_id', 'feedback', ['import_batch_id'], unique=False)
    op.create_index('ix_feedback_organisation_id_content_hash', 'feedback', ['organisation_id', 'content_hash'], unique=False)
    op.create_index('ix_feedback_organisation_id_feedback_at', 'feedback', ['organisation_id', 'feedback_at'], unique=False)
    op.create_index('uq_feedback_org_source_external', 'feedback', ['organisation_id', 'data_source_id', 'external_id'], unique=True, postgresql_where=sa.text('external_id IS NOT NULL'))
    op.create_table('analysis_results',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('organisation_id', sa.Uuid(), nullable=False),
    sa.Column('feedback_id', sa.Uuid(), nullable=False),
    sa.Column('analysis_run_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('sentiment_label', sa.String(length=20), nullable=True),
    sa.Column('sentiment_confidence', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('sentiment_scores', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('sentiment_model_version', sa.String(length=120), nullable=True),
    sa.Column('category_id', sa.Uuid(), nullable=True),
    sa.Column('category_confidence', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('is_unclassified', sa.Boolean(), nullable=False),
    sa.Column('categorisation_skipped', sa.Text(), nullable=True),
    sa.Column('candidate_categories', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status in ('ok', 'failed')", name=op.f('ck_analysis_results_status_valid')),
    sa.ForeignKeyConstraint(['analysis_run_id'], ['analysis_runs.id'], name=op.f('fk_analysis_results_analysis_run_id'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_analysis_results_category_id'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organisation_id', 'feedback_id'], ['feedback.organisation_id', 'feedback.id'], name='fk_analysis_results_org_feedback', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], name=op.f('fk_analysis_results_organisation_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_analysis_results')),
    sa.UniqueConstraint('analysis_run_id', 'feedback_id', name='uq_analysis_results_run_feedback')
    )
    op.create_index('ix_analysis_results_organisation_id_category_id', 'analysis_results', ['organisation_id', 'category_id'], unique=False)
    op.create_index('ix_analysis_results_organisation_id_sentiment_label', 'analysis_results', ['organisation_id', 'sentiment_label'], unique=False)
    op.create_index('uq_analysis_results_current_feedback', 'analysis_results', ['feedback_id'], unique=True, postgresql_where=sa.text('is_current'))
    op.create_table('insights',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('organisation_id', sa.Uuid(), nullable=False),
    sa.Column('analysis_result_id', sa.Uuid(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('business_insight', sa.Text(), nullable=False),
    sa.Column('executive_summary', sa.Text(), nullable=False),
    sa.Column('keywords', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=True),
    sa.Column('priority', sa.String(length=20), nullable=True),
    sa.Column('department', sa.String(length=50), nullable=True),
    sa.Column('evidence_ids', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('prompt_version', sa.String(length=80), nullable=True),
    sa.Column('model_version', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['analysis_result_id'], ['analysis_results.id'], name=op.f('fk_insights_analysis_result_id'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], name=op.f('fk_insights_organisation_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_insights')),
    sa.UniqueConstraint('analysis_result_id', name=op.f('uq_insights_analysis_result_id'))
    )
    op.create_index('ix_insights_organisation_id_severity', 'insights', ['organisation_id', 'severity'], unique=False)


def downgrade() -> None:
    # The reverse order: children first, so nothing is dropped while still referenced.
    # Tested (tests/integration/test_migrations.py) - a downgrade nobody has run is a
    # rollback plan nobody has.
    op.drop_index('ix_insights_organisation_id_severity', table_name='insights')
    op.drop_table('insights')
    op.drop_index('uq_analysis_results_current_feedback', table_name='analysis_results', postgresql_where=sa.text('is_current'))
    op.drop_index('ix_analysis_results_organisation_id_sentiment_label', table_name='analysis_results')
    op.drop_index('ix_analysis_results_organisation_id_category_id', table_name='analysis_results')
    op.drop_table('analysis_results')
    op.drop_index('uq_feedback_org_source_external', table_name='feedback', postgresql_where=sa.text('external_id IS NOT NULL'))
    op.drop_index('ix_feedback_organisation_id_feedback_at', table_name='feedback')
    op.drop_index('ix_feedback_organisation_id_content_hash', table_name='feedback')
    op.drop_index('ix_feedback_import_batch_id', table_name='feedback')
    op.drop_table('feedback')
    op.drop_index('ix_analysis_runs_organisation_id_created_at', table_name='analysis_runs')
    op.drop_index('ix_analysis_runs_import_batch_id', table_name='analysis_runs')
    op.drop_table('analysis_runs')
    op.drop_index('ix_import_batches_organisation_id_created_at', table_name='import_batches')
    op.drop_table('import_batches')
    op.drop_index('ix_jobs_status_run_after', table_name='jobs')
    op.drop_index('ix_jobs_organisation_id', table_name='jobs')
    op.drop_table('jobs')
    op.drop_index('ix_data_sources_organisation_id', table_name='data_sources')
    op.drop_table('data_sources')
    op.drop_index('uq_categories_default_name', table_name='categories', postgresql_where=sa.text('organisation_id IS NULL'))
    op.drop_index('ix_categories_organisation_id', table_name='categories')
    op.drop_table('categories')
    op.drop_table('organisations')
