# FeedbackIQ web app

The customer-facing app (Milestone 8): a static React + TypeScript single-page app built with
Vite. It talks only to the FeedbackIQ API, and only through its own origin at `/api`.

`frontend/` is a different thing — the internal Streamlit tool — and is unchanged.

## Develop

```bash
# the API, from the repository root
docker compose up -d postgres && alembic upgrade head
uvicorn feedbackiq.api.main:app --reload --port 8000

# the app
cd web
npm ci
npm run dev            # http://localhost:5173 - /api is proxied to localhost:8000
```

Set `FEEDBACKIQ_API_URL` to point the dev proxy at a different backend.

## Check

```bash
npm run lint           # ESLint
npm run typecheck      # tsc --noEmit
npm test               # Vitest + Testing Library
npm run build          # type-check, then a production build in dist/
```

## API types

`src/api/schema.d.ts` is generated from the backend's OpenAPI document and never edited by hand.
After changing a request or response shape in the backend:

```bash
python -m feedbackiq.api.openapi_export web/openapi.json   # from the repository root
cd web && npm run api:types
```

CI fails if either file is stale: pytest compares `openapi.json` with the API, and
`npm run api:check` compares `schema.d.ts` with `openapi.json`.

## Rules that keep authentication safe

- **Same origin only.** The app calls `/api/...` on its own origin; a proxy (Vite in
  development, nginx in the container) forwards to the backend. The HttpOnly session cookie is
  therefore first-party, and no CORS is involved.
- **No tokens in JavaScript.** Nothing auth-related goes in `localStorage`, `sessionStorage` or
  app state. The browser holds the cookie; the app only knows what `GET /api/v1/auth/me` returns.
- **No organisation ids from the client.** No request names an organisation; the API decides it
  from the session.
- **No access decisions in the frontend.** Routes are hidden or shown for usability; the API is
  what refuses.
