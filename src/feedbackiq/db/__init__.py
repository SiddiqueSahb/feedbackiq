"""
Persistence: the PostgreSQL schema and the functions that read and write it.

This package is the only place in FeedbackIQ that knows SQL exists. The analytics
engine (`feedbackiq.engine`) does not import it, and must not: the engine takes typed
inputs and returns typed results, and this layer stores them. There is a test that
proves it (`tests/integration/test_engine_db_boundary.py`).

    from feedbackiq.db.session import session_scope
    from feedbackiq.db.models import Organisation

Nothing here is wired into the API yet - see docs/production/milestone-04.md.
"""
