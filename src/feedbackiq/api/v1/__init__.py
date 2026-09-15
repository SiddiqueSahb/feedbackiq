"""
The versioned customer API.

    /api/v1/auth/...              sign up, in and out; who is signed in  (api/v1/auth.py)

    /api/v1/feedback              list, filter, paginate
    /api/v1/feedback/{id}         one item with its current analysis
    /api/v1/analytics/summary     totals, sentiment split, average rating
    /api/v1/analytics/trend       volume over time
    /api/v1/analytics/categories  counts and sentiment per category
    /api/v1/categories            the categories this organisation may be assigned
    /api/v1/imports               upload a CSV (POST); recent imports (GET)
    /api/v1/imports/{id}          one import
    /api/v1/jobs/{id}             one background job

Versioned because this is the first API a frontend will build against, and a frontend that
cannot rely on a shape cannot be maintained. The dissertation's `/api/*` research routes keep
their API key and are unchanged.

Four rules hold across every customer-data route here:

* **A signed-in user is required** (Milestone 7). `main.py` mounts this router behind
  `api.deps.get_current_organisation`, so a new route is protected without remembering to be.
* **Organisation scope comes from that user's session and membership** - the `context`
  argument each handler receives - never from anything the caller sends.
* **Sessions open inside the handler**, after authentication has succeeded.
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
