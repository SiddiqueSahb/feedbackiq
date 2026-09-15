"""
The versioned customer API.

    /api/v1/feedback              list, filter, paginate
    /api/v1/feedback/{id}         one item with its current analysis
    /api/v1/analytics/summary     totals, sentiment split, average rating
    /api/v1/analytics/trend       volume over time
    /api/v1/analytics/categories  counts and sentiment per category
    /api/v1/categories            the categories this organisation may be assigned
    /api/v1/imports               recent imports
    /api/v1/imports/{id}          one import
    /api/v1/jobs/{id}             one background job

Versioned because this is the first API a frontend will build against, and a frontend that
cannot rely on a shape cannot be maintained. The Milestone 5B routes under `/api/imports`
and the dissertation's `/api/*` routes are left exactly as they are: breaking them would buy
nothing, and the Streamlit tool still uses them.

Three rules hold across every route here:

* **Organisation scope comes from the server**, via `api.deps.resolve_organisation_id`, never
  from anything the caller sends.
* **Sessions open inside the handler**, after the API key has been checked.
* **Filtering and aggregation happen in PostgreSQL** (`services/feedback.py`,
  `services/analytics.py`), not in Python.
"""

from fastapi import APIRouter

from feedbackiq.api.v1 import analytics, feedback, imports

router = APIRouter()

router.include_router(feedback.router)
router.include_router(analytics.router)
router.include_router(imports.router)

__all__ = ["router"]
