"""
The committed OpenAPI document - web/openapi.json

The web app's TypeScript types are generated from this file, so if it drifts from the real API the
frontend compiles against shapes the backend no longer returns. This test is the backend half of
that guard; the CI `web` job checks that the generated types still match the file.
"""

from __future__ import annotations

import json
from pathlib import Path

from feedbackiq.api.main import app
from feedbackiq.api.openapi_export import render

DOCUMENT = Path(__file__).resolve().parents[2] / "web" / "openapi.json"

REGENERATE = (
    "web/openapi.json is out of date. Regenerate it and the frontend types:\n"
    "    python -m feedbackiq.api.openapi_export web/openapi.json\n"
    "    cd web && npm run api:types"
)


def test_the_committed_openapi_document_matches_the_api():
    assert DOCUMENT.exists(), REGENERATE
    assert json.loads(DOCUMENT.read_text(encoding="utf-8")) == app.openapi(), REGENERATE


def test_the_export_is_stable_so_the_file_only_changes_when_the_api_does():
    assert render() == render()
    assert render().endswith("\n")
