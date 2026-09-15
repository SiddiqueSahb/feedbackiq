"""product taxonomy 2.0.0 alongside the research taxonomy

Installs the 13 customer-facing product categories and retires the 24 discovered research
categories **without deleting them**.

Why they stay: analysis results produced before Milestone 6 reference those rows by id. A
`DELETE` would either orphan that history or cascade it away (`analysis_results.category_id`
is `ON DELETE SET NULL`), which would quietly rewrite what a customer was previously told.
They are marked instead:

    source     'default'    -> 'discovered'   they came from BERTopic, not from product design
    is_active  true         -> false          not offered for new analysis

`is_active` existed from Milestone 4 and was unused; this is what it is for. The API's
category analytics reads `is_active` to decide what to offer, while historical results
continue to resolve to whichever row produced them.

The 13 product rows are listed explicitly rather than imported from core/taxonomy.py, for
the same reason migration 0002 lists its key/name pairs: a migration must keep doing the
same thing years later, whatever the application code has become.

Revision ID: 0003
Revises: 0002
Created: 2026-09-14 23:55:00.000000+00:00

"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Product taxonomy 2.0.0: (key, name, description, exemplars). Must match
# src/feedbackiq/core/product_categories.json at the version this migration was written.
PRODUCT_CATEGORIES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        'product_quality_and_performance',
        'Product Quality & Performance',
        'The product, food or drink is defective, stale, poor quality, or performs worse than it should.',
        ('stopped working after two weeks', 'the food was cold and tasteless',
         'poor build quality for the price'),
    ),
    (
        'product_not_as_described',
        'Not As Described',
        'The item received is a different item, colour or size from the one that was ordered.',
        ('the colour is nothing like the photo', 'they sent a different model than I ordered',
         'sizing runs completely wrong'),
    ),
    (
        'delivery_and_fulfilment',
        'Delivery & Fulfilment',
        'A delivery or parcel arrived late, never arrived, was damaged in transit, or contained missing or wrong items.',
        ('the parcel arrived a week late', 'items were missing from the order',
         'the courier lost the delivery'),
    ),
    (
        'wait_times_and_delays',
        'Wait Times & Delays',
        'Customers waited too long to be served, seated, answered or processed.',
        ('waited forty minutes for a table', 'queued for over an hour',
         'nobody came to take our order'),
    ),
    (
        'service_quality',
        'Service Quality',
        'Staff were rude, unprofessional, careless or unhelpful in person.',
        ('the staff were rude and dismissive', 'nobody seemed to care about the problem',
         'unprofessional behaviour from the team'),
    ),
    (
        'support_responsiveness',
        'Support Responsiveness',
        'Customer support failed to respond, follow up or resolve the issue after it was raised.',
        ('no reply to three emails', 'support closed my ticket without fixing anything',
         'promised a callback that never came'),
    ),
    (
        'billing_and_payments',
        'Billing & Payments',
        'Charges, invoices or payments were wrong, duplicated, unexpected or failed to process.',
        ('charged me twice for the same order', 'hidden fees appeared on the invoice',
         'the payment would not go through'),
    ),
    (
        'pricing_and_value',
        'Pricing & Value',
        'The price is considered too high, unfair or poor value for what was received.',
        ('far too expensive for what you get', 'not worth the money',
         'prices went up with no warning'),
    ),
    (
        'refunds_and_returns',
        'Refunds & Returns',
        'A refund has not been paid back, or a return, exchange or cancellation was refused, delayed or mishandled.',
        ('still waiting for my refund', 'they refused to accept the return',
         'cancellation was ignored'),
    ),
    (
        'account_and_access',
        'Account & Access',
        'Customers cannot sign in, register, recover access or reach their own account.',
        ('cannot log in to my account', 'password reset email never arrives',
         'locked out with no way back in'),
    ),
    (
        'app_and_technical_issues',
        'App & Technical Issues',
        'An app, website or online service crashes, shows an error, fails to load, or will not complete an action.',
        ('the app crashes every time I open it', 'the website throws an error at checkout',
         'keeps disconnecting from the network'),
    ),
    (
        'booking_and_scheduling',
        'Booking & Scheduling',
        'A booking, appointment, reservation or scheduled journey was cancelled, changed or could not be made.',
        ('my flight was cancelled two hours before departure',
         'the appointment was moved three times',
         'no availability despite the confirmation'),
    ),
    (
        'facilities_and_environment',
        'Facilities & Environment',
        'The premises were unclean, uncomfortable, unsafe or poorly maintained.',
        ('the toilets were filthy', 'far too noisy to hold a conversation',
         'broken seating and peeling paint'),
    ),
)

# The research keys retired by this migration (taxonomy 1.1.0, from migration 0002).
RESEARCH_KEYS: tuple[str, ...] = (
    'service_and_wait_time_delays', 'salon_service_failures',
    'product_taste_and_aroma_failures', 'visual_quality_and_performance_failures',
    'hair_quality_and_performance_failures', 'travel_disruption_issues',
    'product_performance_failures', 'audio_performance_failures',
    'financial_institution_service_failures', 'tablet_accessory_quality_failures',
    'product_color_representation_failures', 'facial_protection_and_safety_failures',
    'home_network_connectivity_issues', 'product_fit_and_sizing_issues',
    'order_fulfillment_delays', 'spray_bottle_continuous_spray_dryer_diffuser_fit_dryer',
    'accessory_attachment_and_fastening_failures', 'mounting_and_installation_problems',
    'gps_device_performance_failures', 'laptop_cooling_system_failures',
    'mirror_performance_failures', 'customer_service_apology_failures',
    'digital_platform_performance_failures', 'direct_messaging_access_issues',
)


def upgrade() -> None:
    connection = op.get_bind()

    # 1. Retire the research categories. Kept, not deleted: stored results point at them.
    connection.execute(
        sa.text(
            "UPDATE categories SET source = 'discovered', is_active = false "
            " WHERE organisation_id IS NULL AND key = ANY(:keys)"
        ),
        {"keys": list(RESEARCH_KEYS)},
    )

    # 2. Install the product taxonomy as the active global default. ON CONFLICT DO NOTHING
    #    so a database already seeded by a newer application version is left alone.
    for key, name, description, exemplars in PRODUCT_CATEGORIES:
        connection.execute(
            sa.text(
                "INSERT INTO categories "
                "  (id, organisation_id, key, name, description, exemplars, kind, source, is_active) "
                "VALUES (:id, NULL, :key, :name, :description, :exemplars, 'complaint', 'default', true) "
                "ON CONFLICT (key) WHERE organisation_id IS NULL DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "key": key,
                "name": name,
                "description": description,
                "exemplars": list(exemplars),
            },
        )


def downgrade() -> None:
    connection = op.get_bind()

    # Remove the product categories. Any analysis_results row referencing one has its
    # category_id set to NULL by the existing ON DELETE SET NULL - the result itself,
    # its sentiment and its run versions all survive.
    connection.execute(
        sa.text(
            "DELETE FROM categories WHERE organisation_id IS NULL AND key = ANY(:keys)"
        ),
        {"keys": [key for key, _, _, _ in PRODUCT_CATEGORIES]},
    )

    # Put the research taxonomy back the way migration 0002 left it.
    connection.execute(
        sa.text(
            "UPDATE categories SET source = 'default', is_active = true "
            " WHERE organisation_id IS NULL AND key = ANY(:keys)"
        ),
        {"keys": list(RESEARCH_KEYS)},
    )
