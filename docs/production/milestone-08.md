# Milestone 8 — Frontend Foundation

**Status:** complete, pending review
**Preceded by:** [Milestone 7](milestone-07.md) · **Contract consumed:** [Milestone 7 §16](milestone-07.md#16-frontend-contract-part-22)

---

## 0. In one picture

```text
browser ──▶ http://localhost:8080  (web container: unprivileged nginx)
              ├── /          the built React app (static files)
              └── /api/*     proxied to backend:8000, Host header kept
                                   │
                                   ▼
                     FastAPI (Milestone 7 authentication, organisation scope)
                                   │
                                   ▼
                     PostgreSQL ◀── worker (analysis with the real models)
```

The browser only ever talks to one origin. The backend's HttpOnly session cookie is therefore
first-party, no CORS is involved, and the app never holds a token. Every access decision stays in the
API; the frontend only renders what the API returns.

| Commit | What |
|---|---|
| `ca1f1d3` Make the OpenAPI enum order independent of import order | backend fix found by part 2 |
| `5983398` Scaffold the web app with generated API types | part 2 |
| `ee84f68` Add sign-in, registration and session handling to the web app | part 3 |
| `fd33786` Containerise the web app behind nginx on the API's origin | part 4 |
| `03a0859` Report each import's analysis status in import summaries | part 5, approved backend change |
| `b404552` Return the readable message when an upload is refused | part 5, backend fix |
| `0af4eeb` Tell the route guards when a session ends mid-use | part 5, fix to part 3 |
| `a553d10` Add CSV upload and import history to the web app | part 5 |
| `5d14e40` Add headline figures to the dashboard | part 6, slice A |
| `631fb61` Add the sentiment trend and category breakdown to the dashboard | part 6, slice B |
| (this document's commit) Document Milestone 8 | part 7 |

`frontend/` — the Streamlit internal tool — is unchanged and still uses the research routes with the
API key. The customer app is `web/`.

---

## 1. Stack, and what was rejected

| Concern | Choice |
|---|---|
| App | **Vite 8 + React 19 + TypeScript 5.9**, a static single-page app |
| Routing | React Router 8 |
| API data | TanStack Query 5 — loading, errors, cache invalidation, polling |
| API types | **generated** from FastAPI's OpenAPI document with `openapi-typescript` (build-time only) |
| Styling | CSS Modules over one design-token file (`src/styles/tokens.css`) |
| Components | thirteen small in-house ones (Button, ButtonLink, TextField, Alert, Card, Badge, StatTile, EmptyState, PageHeader, FullPage, Spinner, Icon, Logo) |
| Charts | hand-written SVG and CSS (§8) |
| Tests | Vitest + Testing Library; Playwright against the real API |

Every dependency is pinned exactly in `package.json`, with `package-lock.json` committed.
`npm audit` reports 0 vulnerabilities, re-checked at the end of the milestone with every
dependency installed (Playwright included), for runtime and development dependencies alike.

**Rejected:**

- **Next.js.** Server rendering and server-side fetching add a Node server in production and invite
  re-checking sessions outside FastAPI, which would duplicate authentication. An authenticated
  dashboard gains nothing from server rendering. (A marketing site can be a separate project.)
- **TypeScript 7.0.** The newest release, but typescript-eslint requires `<6.1` and openapi-typescript
  requires `5.x`. 5.9.3 is the newest that the toolchain supports.
- **A UI kit (MUI, shadcn/Tailwind).** The generic look the brief asked to avoid, and a large
  surface to learn. The components needed are few and small.
- **A chart library (Recharts).** Two modest charts did not justify the dependency or the risk of
  fighting the strict Content-Security-Policy (§9).

## 2. Structure

```text
web/
  Dockerfile, nginx.conf           the container (§9)
  openapi.json                     the API's OpenAPI document, committed
  playwright.config.ts, e2e/       browser tests against the real API (§10)
  src/
    main.tsx                       fonts, tokens, <App/>
    app/                           App.tsx (routes), AppShell, queryClient.ts (what a 401 means)
    api/                           client.ts (the one fetch wrapper), schema.d.ts (generated), types.ts,
                                   auth.ts, imports.ts, analytics.ts
    auth/                          session.ts (hooks), sessionCache.ts, guards.tsx, nextPath.ts
    imports/                       uploader, upload result, history, formatting
    dashboard/                     stat tiles, trend chart, category breakdown, chart arithmetic
    pages/                         Login, Register, Dashboard, Imports, NoOrganisation, NotFound
    components/                    the in-house components
    styles/                        tokens.css, global.css
```

**Generated types.** `python -m feedbackiq.api.openapi_export web/openapi.json` writes the document;
`npm run api:types` turns it into `src/api/schema.d.ts`; `src/api/types.ts` gives friendly aliases.
A backend shape change becomes a TypeScript error wherever the frontend relies on it. Drift fails in
both directions: `tests/api/test_openapi_document.py` compares the committed document with the API,
and `npm run api:check` compares the types with the document.

## 3. Routes

| Route | Shown when | Content |
|---|---|---|
| `/login`, `/register` | signed out (`PublicOnly`) | one-card forms; `?next=` returns the person where they were going |
| `/dashboard` | signed in with an organisation (`RequireAuth`) | headline figures, sentiment trend, categories |
| `/imports` | signed in with an organisation | upload, file format, import history |
| `/` | — | redirects to `/dashboard` |
| (no organisation) | signed in, `organisation: null` | an explanation and sign-out, instead of pages the API would refuse |
| `*` | — | not found |

`RequireAuth` renders, in order: a loading screen; a **retryable error** if `/auth/me` cannot be
answered (not a sign-in page — the person may well be signed in); a redirect to `/login?next=…`; the
no-organisation page; the app.

## 4. Authentication and session handling

- **The token never reaches JavaScript.** The API sets an HttpOnly cookie; the browser attaches it to
  same-origin requests by itself (`credentials: "same-origin"`, stated explicitly in `api/client.ts`).
  Nothing is written to `localStorage` or `sessionStorage` — asserted in Vitest (a `Storage.setItem`
  spy) and in Playwright (`document.cookie`, both storages and the rendered HTML never contain the
  session).
- **`GET /api/v1/auth/me` is the only source of auth state**, held under the query key `["session"]`:
  the user and organisation the API reported, or `null`.
- **Signing in or registering** records the returned user and drops every other cached response.
  **Signing out** calls the API, then does the same with `null`, so one organisation's data never stays
  in memory for the next person on a shared computer.
- **Any 401 from a data request means the server ended the session** — expired, signed out elsewhere,
  account disabled. The query and mutation caches' `onError` hooks mark the app signed out, and the
  guard sends the person to sign in with `next` set. Sign-in, registration and sign-out are marked as
  credential checks, so a wrong password is not mistaken for an ended session.
- **`next` is accepted only as a path on this app** (`auth/nextPath.ts`): `//evil.example`,
  `https://…`, `/\evil` and similar all fall back to `/dashboard`, and it never points back at
  `/login` or `/register`.
- **No organisation id is ever sent.** The organisation shown is the one `/auth/me` reported.

**A bug found and fixed on the way (`0af4eeb`).** The first version of "end the session" cleared the
whole cache and then set the session to `null`. Clearing removed the cache entry the route guards were
subscribed to, so `null` landed on a new entry nobody watched: after a 401 mid-session, the refused
page stayed on screen. It went unnoticed because the unit test read the cache value, the sign-out test
navigated explicitly, and the Playwright test reloaded the page. It surfaced as a failing imports-page
test, was confirmed by inspecting what rendered, and was fixed by writing the session first and then
removing the other queries. A test now subscribes the way a component does; putting the old order
back made exactly that test and two page tests fail. Playwright covers it in the browser: a session
revoked from another device takes effect on the next upload, without a reload.

## 5. CORS, CSRF and the cookie, aligned with Milestone 7

- **Same origin, so no CORS** in normal use. Compose still sets `ALLOWED_ORIGINS=http://localhost:8080`
  on the backend, the Milestone 7 configuration for a known browser origin: credentialed CORS is then
  allowed only for that origin (verified with a preflight in Docker).
- **CSRF** stays the Milestone 7 design: `SameSite=Lax` plus the backend's origin check. nginx and the
  Vite proxy both keep the browser's `Host` header, so the backend's own-origin comparison matches the
  `Origin` the browser sends; a POST from another origin is refused (verified in Docker).
- **The `Secure` flag.** Compose runs the backend in production mode, so the cookie is `Secure`.
  Chromium accepts `Secure` cookies on `http://localhost`, and the browser tests pass through nginx
  with it. Other browsers were not tested locally; a real deployment must serve HTTPS anyway. The
  Vite dev server and CI use development mode (not `Secure`) over plain HTTP.

## 6. Imports

- Choose or drop a CSV. The browser checks only that it is a `.csv`, not empty and within 10 MB, so an
  obvious mistake is reported before an upload; the API judges columns, rows and duplicates.
- The result says what happened in plain words: "Imported 3 of 4 rows", how many were skipped or
  already imported, and the first ten row problems by line and column; "No rows could be imported";
  or "This file was already imported". A refused file shows the API's message.
- The history table shows rows received, imported and skipped, and an analysis badge (Queued,
  Analysing, Analysed, Analysis failed, Nothing imported). It polls every 4 seconds **only while** an
  analysis is queued or running.
- A "File format" card lists the column names the API accepts (read from `ingestion/csv_reader.py`).

**The approved backend change (`03a0859`).** Import summaries gain `job_id` and `analysis_status`,
so the history can show progress after a reload, not only straight after an upload. A job refers to
its import only through its JSON payload, so `db/jobs.py::latest_import_jobs` fetches the newest
`analyse_import` job per import in **one query**, scoped to the organisation as well as the ids —
another organisation's job is never attached, even one whose payload names the import. The change only
adds fields. Twelve integration tests cover it, including the forged-payload case and a statement count
that is the same for one import and six.

**Two backend defects found while building the page:**

- `ca1f1d3` — **the OpenAPI document changed with import order.** `typing` caches
  `Literal[...] | None` by value and treats Literals with the same values in a different order as
  equal; the research routes wrote the sentiment Literal in a different order from the engine, so the
  enum order depended on which module a process imported first. The committed `openapi.json` then
  matched in some test runs and not others. Both declarations now use the engine's `SentimentLabel`,
  and `tests/unit/test_literal_ordering.py` scans the source (with `ast`, so import order cannot
  affect it) for any two Literals with the same values in different orders.
- `b404552` — **refused uploads said "[IngestionError] No feedback text column found…"**, because the
  route returned `str(exc)` and `FeedBackError.__str__` prefixes the class name. It now returns the
  message.

## 7. The dashboard

Built in two slices, not all at once.

**Headline figures (slice A).** Five stat tiles — Feedback, Negative, Positive, Unclassified, Average
rating — straight from `GET /api/v1/analytics/summary`. Shares are of analysed feedback, as the API
computes them. States: an upload invitation when there is no feedback; "Analysis in progress" with
dashes instead of a misleading 0% while feedback waits (polling every 10 seconds only while it does);
a dash for a missing rating; loading and retryable errors. No period-over-period delta is shown,
because the API has no comparison period to compute one from.

**Sentiment over time and complaint categories (slice B).** Each card loads, fails and retries on its
own, and both poll with the summary while feedback waits.

## 8. Charts: form, colour and a visual review

The dataviz guidance was followed as a procedure, not a style.

- **Form first.** Single headline values are stat tiles, not one-bar charts. Sentiment over time is a
  stacked column per period; categories are one series over unordered categories, so one colour, no
  legend, and the exact value as text beside each bar.
- **Colour measured, not chosen.** The original sentiment tokens **failed** the palette validator:
  teal and slate were too close (colour-vision-deficiency distance 4.5, normal-vision 9.3, teal below
  the chroma floor). Sentiment is a polarity, so it is now a diverging scale with a grey midpoint:

  | Role | Colour | Measured (adjacent, on white) |
  |---|---|---|
  | negative | `#eb6834` | negative↔neutral: CVD ΔE 16.8, normal ΔE 22.0 |
  | neutral | `#c3c2b7` | below 3:1 by design → legend, text values and table view required (all present) |
  | positive | `#2a78d6` | neutral↔positive: clear of both floors |

  Orange rather than red keeps negative sentiment apart from the reserved error red; the category
  bars use violet `#4a3aa7` because blue already means "positive" on the same page. Colours are from
  the reference palette; only the light surface was validated (there is no dark mode).
- **Marks.** Columns ≤ 24px with a 2px surface gap between segments and a 4px rounded top; hairline
  solid gridlines; tick labels in text colour, never series colour; proportional digits on stat-tile
  values and tabular digits only in columns.
- **Interaction.** A readout on hover, or with the arrow keys / Home / End once the chart has focus;
  "Show table" gives every value, so nothing is reachable only by hovering.

**Rendering it and looking at it** found four defects that every automated test had passed over. 48
demo rows were analysed by the real worker and the dashboard was screenshotted at desktop and phone
widths:

1. the readout was centred over the column it described — it now sits beside it, on whichever side
   has room (tested with `data-side`);
2. in the table view numbers sat left-aligned under right-aligned headers — `.table td` outranked
   `.numeric`; fixed at the right specificity;
3. on a phone the chart scaled its labels to a few pixels — it now keeps a 640px minimum width and
   scrolls sideways inside its card;
4. "latest feedback 19 Sept 2026, 01:00" showed an invented time for a date-only value — dates are now
   shown without a time, in UTC (as the trend's periods are).

A fifth was found by writing the test first: count axes could get fractional ticks (0.25 for a maximum
of 1, 2.5 for a maximum of 9). Steps are now always whole numbers.

## 9. Containerisation

`web/Dockerfile` builds in two stages from the project root, like the other images: `node:24-alpine`
runs `npm ci --ignore-scripts` and the type-checked production build; `nginxinc/nginx-unprivileged:1.29-alpine`
serves it as uid 101. The final image is about 22 MB and contains no Node, source, `node_modules`,
source maps or `.env` — and no secret, because the app has no configuration of its own.

`web/nginx.conf`:

- `/api/` → `backend:8000` with the browser's `Host` header;
- unknown paths fall back to `index.html` (deep links), while a missing `/assets/` file is a real 404;
- hashed assets are cached for a year, `index.html` never;
- `client_max_body_size 11m`, just above the API's 10 MB limit, so the API's readable 413 reaches the
  person and nginx still stops anything larger;
- on every response, including proxied API responses: a **Content-Security-Policy** limited to the
  same origin (the build has no inline script or style), `nosniff`, `X-Frame-Options: DENY`, a
  referrer policy and a permissions policy; nginx's version is not advertised. HSTS is left to
  whatever terminates TLS.

Compose adds `web` on `localhost:8080`, waiting for a healthy backend. CI's Docker job builds the web
image too.

## 10. Tests

| Suite | Before M8 | After M8 |
|---|---|---|
| `pytest` (unit + API) | 508 passed, 2 xfailed | **513 passed, 2 xfailed** |
| `pytest tests/integration` | 261 passed | **273 passed** |
| Vitest (web) | — | **117 passed** (11 files) |
| Playwright (web, real API) | — | **15 passed** (auth 7, imports 5, dashboard 3) |
| flake8 · ESLint · `tsc` · `api:check` | — | clean |

- **Vitest** replaces `fetch` at the network boundary and nothing else: the guards, session hooks,
  query cache and components under test are the real ones.
- **Playwright** drives Chromium through the built app, the real FastAPI backend and PostgreSQL.
  Nothing about authentication is faked. Every spec fails if Chromium reports a
  Content-Security-Policy violation (only meaningful behind nginx).
- **CI** gains a `web` job (install, lint, type-check, generated types up to date, unit tests, build)
  and an `e2e` job (PostgreSQL service, migrated API under uvicorn, Playwright Chromium, report and API
  log kept on failure). The Docker job builds the web image.
- **Limits of CI.** The worker needs the ML models, which CI does not have, so browser tests stop at
  "Queued" and "0 analysed"; analysis completing is verified in Docker (§11).

**Checked that the important tests can fail**, by breaking the thing on purpose and reverting:

| Guard | Broken how | Result |
|---|---|---|
| CSP watcher | a container with `font-src 'none'` | the test failed with "the page broke its Content-Security-Policy" and nothing else |
| session subscription test | the old clear-then-set order | exactly that test and the two imports-page "session ended" tests failed |
| whole-number ticks | the test written before the fix | failed for a maximum of 1 against the old code |

**Test-side corrections made on the way, none weakening a test:** Playwright role names match as
substrings unless `exact` (five early false failures); a statement-count test once listened on the
wrong engine (`get_engine()` and `get_engine(None)` are separate `lru_cache` entries) and passed with
zero statements on both sides — it now listens on the `Engine` class and asserts statements were seen;
fakes were extended when the dashboard began making requests the older tests did not answer;
en-GB compact numbers are `12.8k`, not `12.8K`.

## 11. Docker verification

Recorded on 15 September 2026 against the images built from commit `631fb61`. The backend ran in
production mode, so the cookie was `Secure`, and the web container sat in front of it with nginx
enforcing the CSP. **16 checks, 0 failed.**

| Stage | Check | Result |
|---|---|---|
| stack | PostgreSQL healthy; schema at `0006 (head)`; backend and web healthy | pass |
| stack | no worker container running while the browser tests run (their specs expect "Queued") | pass |
| image | the bundle nginx serves (`/assets/index-WiDgCezh.js`) is the one built from this commit | pass |
| browser | **Playwright 15/15 through nginx** (7.1 s): real API, real cookie, CSP watcher active | pass |
| analysis | register an organisation through nginx → 201 | pass |
| analysis | upload a 48-row CSV through nginx → 48 imported, analysis `queued` | pass |
| analysis | worker container with the real models, `--drain` → 7 jobs in 114 s (this upload plus the ones Playwright left queued) | pass |
| analysis | import list now shows `succeeded`; summary counts 48 analysed | pass |
| analysis | weekly trend has 8 periods; the category breakdown has rows | pass |
| dashboard | Chromium signs in through nginx and screenshots the analysed dashboard at 1280 px and 390 px | pass |
| dashboard | no Content-Security-Policy violation | pass |
| dashboard | no console error except the expected 401 from `/api/v1/auth/me` (the signed-out check before signing in) | pass |

The analysed dashboard showed 48 feedback items, 66.7% negative (32), 18.8% positive (9), 8.3%
unclassified, an average rating of 2.3, eight weekly columns, and eight complaint categories led
by Billing & Payments (13, 27.1%) with 13 not categorised. The screenshots were reviewed by eye. On a phone the tiles and
category bars stack, and the trend chart scrolls sideways inside its card, as designed.

Afterwards compose's `web`, `backend` and `postgres` were stopped, with the volume kept, and the
local development database was restarted.

## 12. Security review

| Property | How it holds |
|---|---|
| no token in JavaScript | HttpOnly cookie; no Authorization header or storage anywhere; tested in Vitest and Playwright |
| no client-chosen tenant | no request carries an organisation id; the API decides from the session |
| no access decision in the frontend | guards only choose what to render; every customer route is refused by the API without a session (Milestone 7 suites) |
| data cleared between people | signing in, out, or losing the session drops every cached response |
| open redirect | `next` accepted only as a same-origin path |
| XSS surface | React escapes all rendered text; no `dangerouslySetInnerHTML`; CSP forbids inline and foreign scripts |
| clickjacking | `X-Frame-Options: DENY` and `frame-ancestors 'none'` |
| upload abuse | 11 MB ceiling at nginx, 10 MB and 50,000 rows at the API |
| supply chain | exact pins, lock file, `npm ci --ignore-scripts` in the image, `npm audit` 0 findings at the end of the milestone |

## 13. Release prerequisites — before any public exposure

These are **not** done, and each must be done before the authentication endpoints or the app are
exposed publicly in production:

1. **Rate limiting and account lockout** on `POST /api/v1/auth/login` (per IP and per email),
   `POST /api/v1/auth/register` (per IP) and uploads (per organisation). Milestone 7 made sign-in
   errors reveal nothing; it does not slow a password-guessing attempt. It needs a shared store once
   more than one API container runs, so it belongs with the deployment work.
2. **HTTPS end to end, with HSTS** at the TLS terminator; the cookie is already `Secure` in
   production.
3. **`ALLOWED_ORIGINS` set to the real web origin**, and uvicorn trusting the proxy's forwarded headers
   (`--proxy-headers` with the proxy's address) behind a TLS-terminating load balancer.
4. **An audit log** of sign-ins, failures and uploads.
5. Registration still reveals whether an email is registered (409); email verification closes that.

## 14. Known limitations

| # | Limitation |
|---|---|
| 1 | **Trend periods with no feedback are absent**, as the API returns them, and columns are spaced evenly, so a gap in time is not visible as a gap on the axis. |
| 2 | **The dashboard is all-time**: no date-range filter yet. |
| 3 | **No feedback explorer page**, although `GET /api/v1/feedback` exists. |
| 4 | **On a phone the trend scrolls sideways with no visual hint** that it can. |
| 5 | **No dark mode**; the chart palette was validated on the light surface only. |
| 6 | **No organisation switcher** for a user in several organisations (Milestone 7 limitation). |
| 7 | **Browser tests in CI cannot see analysis complete** (no models); verified in Docker instead. |
| 8 | **Chromium only** in the automated browser tests. |
| 9 | **The console shows one 401 on a signed-out first load** — the `/auth/me` check itself; harmless, and logged by the browser whatever the app does. |
| 10 | The Streamlit tool remains for research routes; it cannot use customer data. |

## 15. How to run

```bash
# development
docker compose up -d postgres && alembic upgrade head
uvicorn feedbackiq.api.main:app --reload --port 8000
cd web && npm ci && npm run dev                       # http://localhost:5173

# checks
cd web && npm run lint && npm run typecheck && npm run api:check && npm test && npm run build
npm run e2e                                           # needs the API; see web/README.md

# as it ships
docker compose up -d --build postgres backend web     # http://localhost:8080
docker compose run --rm backend alembic upgrade head
docker compose up -d worker                           # analysis (needs models/)
```

## 16. Recommended next milestone

**The release prerequisites in §13, with staging deployment** (roadmap M10): rate limiting and lockout,
HTTPS and proxy configuration, and an automated deploy of web + backend + worker + migrations to a
staging environment — so the product can be shown to a first design partner safely. The dashboard's
next increments (a date-range filter, a feedback explorer) can follow once there is somewhere real to
use them.
