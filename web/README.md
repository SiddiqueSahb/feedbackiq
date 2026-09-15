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

## Browser tests against the real API

Playwright drives Chromium through the built app and the real FastAPI backend and PostgreSQL.
Nothing about authentication is faked: the cookie is the API's real HttpOnly session cookie.

```bash
# from the repository root: a disposable database, migrated, and the API on port 8000
docker compose exec postgres createdb -U feedbackiq feedbackiq_e2e          # once
DATABASE_URL=postgresql+psycopg://feedbackiq:feedbackiq@localhost:55432/feedbackiq_e2e alembic upgrade head
ENVIRONMENT=development \
DATABASE_URL=postgresql+psycopg://feedbackiq:feedbackiq@localhost:55432/feedbackiq_e2e \
  uvicorn feedbackiq.api.main:app --port 8000

# then
cd web
npx playwright install chromium      # once
npm run e2e                          # builds, serves with `vite preview`, runs e2e/
```

`ENVIRONMENT=development` keeps the session cookie non-`Secure`, because this runs over plain HTTP.
Every test registers fresh email addresses, so the database never needs resetting. To test an app
that is already running - the nginx container, say - set `E2E_BASE_URL=http://localhost:8080`.

## Run in Docker

```bash
docker compose up -d --build postgres backend web      # http://localhost:8080
docker compose run --rm backend alembic upgrade head   # first time, or after a new migration
```

The `web` image (`web/Dockerfile`) builds the app with Node and serves it with unprivileged nginx
(`web/nginx.conf`), which also forwards `/api` to the backend - the same single-origin shape as
development. nginx adds the security headers (Content-Security-Policy, `nosniff`, framing and
referrer policy) and allows uploads slightly above the API's 10 MB limit, so the API's own message
reaches the person uploading.

Compose runs the backend in production mode, so the session cookie is `Secure`. Chromium and Firefox
accept that on `http://localhost`; a deployed environment must serve HTTPS. Run the browser tests
against the container with `E2E_BASE_URL=http://localhost:8080 npm run e2e`.

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
