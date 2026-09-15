"""
Write the API's OpenAPI document to a file.

    python -m feedbackiq.api.openapi_export web/openapi.json
    cd web && npm run api:types

The web app (Milestone 8) generates its TypeScript types from that file
(`web/src/api/schema.d.ts`). Committing both means a change to a request or response shape shows
up as a reviewable diff, and as a type error in the frontend wherever it matters.

Two tests keep them honest, each where the tools already are:

* `tests/api/test_openapi_document.py` fails if `web/openapi.json` no longer matches the API;
* the CI `web` job runs `npm run api:check`, which fails if `schema.d.ts` no longer matches
  `web/openapi.json` - without needing Python there.

Keys are sorted so the file only changes when the API does.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def render() -> str:
    """The OpenAPI document as stable, pretty-printed JSON."""
    from feedbackiq.api.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv

    if len(args) != 1:
        print("usage: python -m feedbackiq.api.openapi_export <output.json>", file=sys.stderr)
        return 2

    Path(args[0]).write_text(render(), encoding="utf-8")
    print(f"Wrote {args[0]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
