# Milestone 7 — Authentication, Organisation Membership & Tenant Enforcement

**Status:** complete, pending review
**Preceded by:** [Milestone 6](milestone-06.md) · **Roadmap:** [07 — M7](07-production-roadmap.md#m7--authentication)

---

## 0. In one picture

```text
browser ── HttpOnly cookie ──▶ get_current_user          no or malformed cookie → 401, no database
                                    │
                                    ▼
                               resolve_session           ONE SQL statement:
                                    │                    session ⋈ user ⋈ organisation ⋈ membership
                                    │                    unknown / expired / disabled → 401
                                    ▼
                               get_current_organisation  no live membership → 403
                                    │
                                    ▼
                    handler(context.organisation_id)  ──▶  service(organisation_id=…)  ──▶  WHERE organisation_id = …
```

Before this milestone every request acted for one seeded development organisation, chosen by
`api/deps.py::resolve_organisation_id`. That function is gone. The organisation now comes from
the signed-in user's session, confirmed against a membership on every request, and from nowhere
else.

| Commit | Part(s) |
|---|---|
| `3608d57` Add the users table and argon2id password hashing | 2, 6 |
| `070b06b` Make the migration tests independent of test order | (latent bug found on the way) |
| `30d052d` Add organisation memberships with owner and member roles | 3, 13, 15 |
| `2f7ccb0` Add server-side sessions, registration and sign-in | 5, 6, 8, 9 |
| `221e4f3` Add the sign-in API and the current-user dependencies | 7, 10, 17, 18 |
| `714916b` Scope customer data to the signed-in user's organisation | 11, 12, 20 |
| `78960e0` Prove tenant isolation over HTTP, end to end | 14 |
| (this document's commit) Document Milestone 7 | 16, 19, 21, 22, 24 |

**Roadmap numbering.** This milestone's brief combined roadmap M7 (authentication), the core of
roadmap M8 (membership and enforced organisation scope) and a sliver of M9 (two roles). It calls
the frontend "M8". Invitations, more roles and per-organisation API keys remain for later.

---

## 1. Authentication architecture

Four layers, each with one job, in the modular monolith that already exists:

| Layer | File | Responsibility | Knows about |
|---|---|---|---|
| Credential rules | `auth/credentials.py` | email normalisation, password policy, argon2id hash/verify | nothing — pure functions |
| Session tokens | `auth/session_tokens.py` | generate a token, hash it, recognise a malformed one | nothing — pure functions |
| Accounts & sessions | `services/auth.py` | register, authenticate, start/resolve/end a session | the database |
| HTTP | `api/deps.py`, `api/v1/auth.py` | cookie in → `AuthContext` out; the four auth routes | FastAPI |

Tables (migrations `0004`–`0006`):

```text
users 1 ──── * organisation_memberships * ──── 1 organisations 1 ──── * (all tenant data, unchanged)
  1
  │
  * user_sessions ──(organisation_id, SET NULL)──▶ organisations
```

`users` and `user_sessions` are **global**: a person is not tenant data, and one person may
belong to several organisations. Every existing tenant table, `organisation_id` column and
composite foreign key is untouched — authentication decides *which* organisation a request may
use; the tenant tables still say *whose* each row is.

## 2. Why server-side sessions

The brief asked for a deliberate choice. Evaluated for *this* system — one backend, one
PostgreSQL, a browser frontend next:

| | Server-side session (chosen) | Signed token (JWT) |
|---|---|---|
| Sign out / revoke a stolen login | delete a row; effective on the next request | not possible before expiry without adding a deny-list — i.e. a session table |
| Disable a user | their sessions stop at once (checked in the lookup) | tokens keep working until they expire |
| Secrets to manage | **none** — the token is random, only its hash is stored | a signing key: generate, store, rotate, never leak |
| Where the browser keeps it | `HttpOnly` cookie; page JavaScript cannot read it | often `localStorage` (readable by any injected script), or a cookie anyway |
| Cost per request | one indexed query (`token_hash` is unique) | none — its one real advantage |
| That advantage matters when | — | many independent services must verify identity without a shared database |

FeedbackIQ has no second service, so JWT's advantage buys nothing, and every drawback is
real. **Rejected:** JWT (above); Starlette's `SessionMiddleware` (a signed *client-side* cookie —
no server-side revocation and it needs a secret); a managed provider such as Auth0 or Clerk
(no SSO requirement yet, an external dependency and cost, and it would hide exactly what this
milestone is meant to make understandable). The audit had recommended the same
([09 §16](09-learning-map.md#16-authentication-sessions-vs-jwt--m7)).

How a token works (`auth/session_tokens.py`): `secrets.token_urlsafe(32)` — 256 random bits,
43 URL-safe characters — goes in the cookie; `sha256(token)` goes in `user_sessions.token_hash`.
SHA-256 rather than argon2 because the token is random, not human-chosen: there is nothing to
brute-force, and a fast hash keeps the lookup a plain equality. Someone who reads the table
holds nothing that works as a cookie.

## 3. Password hashing

- **argon2id** through **argon2-cffi 25.1.0** — the reference binding for the Password Hashing
  Competition winner and OWASP's first recommendation. No cryptography is written by hand.
  The one new dependency of the milestone, declared in `pyproject.toml` **and**
  `requirements.txt` (the images install from the latter).
- Parameters: the library defaults, RFC 9106's low-memory profile — 3 iterations, 64 MiB,
  4 lanes, a random 16-byte salt per hash. They are written into every hash string, so
  raising them later locks nobody out: `needs_rehash` is checked after each successful sign-in
  and the hash is upgraded then (tested with a deliberately weak hash).
- Verification returns `False` for a wrong password *and* for a corrupt or unrecognised hash —
  the answer is "not signed in" either way, never a 500.
- **Policy:** 12–128 characters, not only spaces, not the email address. **No composition rules**
  ("one digit, one symbol") — NIST SP 800-63B advises against them. The upper bound stops a
  megabyte-long "password" costing a hash per attempt. Messages describe the rule and never
  repeat the password.
- **Rejected:** `passlib` (unmaintained), `bcrypt` (sound, but caps its input at 72 bytes and is
  not memory-hard; argon2id is the current first recommendation), `pwdlib` (a wrapper around the
  same argon2-cffi — an extra dependency for nothing).

## 4. User model

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | primary key |
| `email` | varchar(254) not null | stored normalised; **unique** (`uq_users_email`) |
| `password_hash` | varchar(255) not null | argon2id string; there is no password column |
| `is_active` | boolean not null, default true | false = cannot sign in, existing sessions stop |
| `created_at`, `updated_at` | timestamptz not null | `updated_at` moves on every change |

**Email identity is case-insensitive in the database, not only in code.**
`ck_users_email_normalised` requires `email = lower(btrim(email))`; with the unique constraint,
"Ana@x.com" and "ana@x.com" cannot become two accounts even if a future code path forgets to
normalise. A unique index on `lower(email)` was the alternative; the CHECK keeps one spelling in
the table so every lookup is a simple equality.

Validation is a shape check (one `@`, a dotted domain, no spaces, ≤ 254 characters). Proof that an
address is real needs email verification, which is out of scope. `email-validator` was not added.

## 5. Organisation membership

| Column | Notes |
|---|---|
| `id`, `created_at` | |
| `user_id` → users | `ON DELETE CASCADE` |
| `organisation_id` → organisations | `ON DELETE CASCADE` |
| `role` | `CHECK (role in ('owner', 'member'))`, no default |

- `uq_organisation_memberships_user_id_organisation_id` — one membership per person per
  organisation, so there is never a question of which role applies. It leads with `user_id`,
  which is the lookup every authenticated request makes.
- `ix_organisation_memberships_organisation_id` — "who belongs to this organisation?".
- Deleting either side removes the membership and never the other side (both directions tested).
- A user with no membership is a valid account: signed in, with nothing to reach.
- The schema already allows several organisations per user; see §7 for how one is chosen.

## 6. Roles

| Role | Can do today | Cannot do today |
|---|---|---|
| `owner` | everything a member can | — |
| `member` | read feedback, analytics, categories, imports and jobs; upload CSVs | — |
| *(no membership)* | sign in, `GET /auth/me` | every customer route → 403 |

**Owner and member have identical data access today.** No owner-only action exists yet: the
brief scoped owner privileges to membership management "where implemented", and invitations and
member management are not in this milestone. Registration makes the registrant `owner`, so the
distinction is recorded correctly for when those routes arrive.

Roles are `MEMBERSHIP_ROLES = ("owner", "member")` plus a CHECK built from that tuple — the same
pattern as every status column. Adding `admin` or `viewer` is one value and a migration; a PostgreSQL
enum was rejected because changing one needs its own locking migration. No permission framework
was built: with two roles and no role-restricted route, one would be abstraction without a use.

## 7. Organisation resolution

1. **Sign-in** (`start_session`) stores the session with an `organisation_id`: the user's
   earliest membership in an organisation that is not soft-deleted. With one organisation per
   registration that is simply theirs. A user in several organisations currently always gets the
   earliest; an organisation switcher would change *which row is picked*, not how access is checked.
2. **Every request** (`resolve_session`) looks up the session by `token_hash` and, in the same
   statement, outer-joins the organisation (`deleted_at IS NULL`) and the membership for
   *that user in that organisation*. The organisation reaches the context **only if the membership
   still exists**. One SQL statement — asserted by a test — because every request pays for it.
3. **`get_current_organisation`** returns 403 when the context has no organisation.

The session's `organisation_id` is therefore **a choice, not a permission**. Tested consequences:

| Situation | Result |
|---|---|
| membership removed mid-session | 403 on the next request |
| organisation soft-deleted | 403 |
| organisation hard-deleted | session's `organisation_id` becomes NULL (`SET NULL`) → 403; user stays signed in |
| session row edited to point at another organisation | 403 — **never** that organisation's data |
| user disabled | 401 on the next request |

The request contributes nothing but the cookie.

## 8. Tenant enforcement

Every customer route, and where its organisation comes from:

| Route | Authentication | Organisation |
|---|---|---|
| `GET /api/v1/feedback`, `/feedback/{id}` | session | `context.organisation_id` → `services/feedback.py` |
| `GET /api/v1/analytics/summary`, `/trend`, `/categories` | session | → `services/analytics.py` |
| `GET /api/v1/categories` | session | → `available_categories` |
| `GET /api/v1/imports`, `/imports/{id}`, `/jobs/{id}` | session | → persistence / job queue |
| **`POST /api/v1/imports`** (new) | session | → `accept_upload` → `import_csv(organisation_id=…)` |
| `POST /api/imports`, `GET /api/imports/{id}`, `GET /api/jobs/{id}` | **session (was API key)** | same helpers as v1 |
| `/api/v1/auth/register`, `/login`, `/logout` | none (public by design) | registration creates a new one |
| `GET /api/v1/auth/me` | session | reported, not used to query |
| research routes (`/api/sentiment`, `/search`, `/rag`, `/analytics`, `/evaluation`) | API key (unchanged) | none — the research corpus |
| worker | n/a | the job row's `organisation_id` (unchanged) |

How it is enforced — one boundary, in depth:

1. **Router level.** `main.py` mounts the v1 data router and the ingestion router with
   `dependencies=[Depends(get_current_organisation)]`, so a new route is protected without
   remembering to be (the same idea as the API key before).
2. **Handler level.** Each handler declares `context: AuthContext = Depends(get_current_organisation)`
   and passes `context.organisation_id` to its blocking helper. FastAPI resolves the dependency
   once per request. No handler reads an organisation id from anything else.
3. **Service level (Milestone 6, unchanged).** Every service function requires `organisation_id`
   and filters by it in SQL.
4. **Schema level (Milestone 4, unchanged).** `NOT NULL organisation_id` and composite foreign keys.
5. **Tests.** §13.

**Ingestion has no default owner any more.** `import_csv` requires `organisation_id`;
`_resolve_organisation` no longer falls back to the seeded `dev` organisation, and
`DEV_ORGANISATION_SLUG` is deleted. The seed still creates `dev` for local use; it has no members
and receives nothing. A CSV column, form field or header naming an organisation is ignored (tested
against both upload routes).

## 9. API changes

**Intentional breaking change:** `/api/v1/*` customer routes and the ingestion routes
(`/api/imports`, `/api/jobs`) now require a signed-in user. **The API key no longer opens them.**
Streamlit calls none of these routes, so the internal tool is unaffected; its research routes keep
the key.

New routes:

| Method | Path | Success | Errors |
|---|---|---|---|
| POST | `/api/v1/auth/register` | 201 + cookie, `CurrentUser` | 422 invalid detail or unknown field · 409 email taken |
| POST | `/api/v1/auth/login` | 200 + cookie, `CurrentUser` | 401 "Incorrect email or password." · 403 disabled · 422 |
| POST | `/api/v1/auth/logout` | 204, cookie cleared — always | 403 disallowed origin |
| GET | `/api/v1/auth/me` | 200 `CurrentUser` | 401 |
| POST | `/api/v1/imports` | 201 `ImportAccepted` | 401 · 403 · 413 · 422 |

Other changes: request bodies for register/login **forbid unknown fields** (`organisation_id` or
`role` in a body is a 422, not silently ignored); CORS allows credentials only with an explicit
origin list; `import_csv`'s signature requires `organisation_id`; the OpenAPI document declares the
session cookie as a security scheme.

Status codes (Part 18):

| Code | When |
|---|---|
| 401 | not signed in — no cookie, malformed, unknown, expired, revoked, or disabled user. **One message** for all. |
| 403 | signed in but no organisation · disabled account *after the correct password* · POST from a disallowed origin |
| 404 | an id that is not in the caller's organisation — identical body to an id that does not exist |
| 409 | registration with an email that has an account |
| 413 | upload over `MAX_UPLOAD_BYTES` |
| 422 | invalid details or parameters |
| 500 | fixed message only ("Could not sign in.") — never exception text |

## 10. Security decisions

- **The token is never in a response body** — only `Set-Cookie`. The cookie is `HttpOnly`,
  `SameSite=Lax`, `Path=/`, `Max-Age` = the session lifetime, and `Secure` in production.
- **CSRF.** `SameSite=Lax` stops a browser attaching the cookie to another site's POST. As a second,
  independent check, `check_origin` refuses POST/PUT/PATCH/DELETE whose `Origin` is neither in
  `ALLOWED_ORIGINS` nor the API's own. No `Origin` (a non-browser client) passes; with
  `ALLOWED_ORIGINS="*"` there is nothing to compare, so only SameSite applies. A CSRF token was not
  added: with SameSite and an Origin check it would be a third layer with real frontend cost.
- **Account enumeration at sign-in.** Unknown email and wrong password return the same 401 and
  message, and cost comparable time (`verify_against_dummy_hash` spends an argon2 verification when
  there is no user). "Disabled" is revealed only after the correct password.
- **FastAPI's 422 echoes input.** Its standard validation error copies the submitted value into
  `input` — for a sign-in form, the password. Under `/api/v1/auth` that field is removed; every other
  route keeps the standard shape (tested both ways).
- **Logging.** No password, token or email address is logged; a refused sign-in logs "Sign-in
  refused." Model reprs print ids, never email or token hash.
- **Before the database.** A missing or malformed cookie is refused without a query (tested by
  counting statements), so garbage cookies cost nothing.
- **Rate limiting (Part 19): not implemented.** There is no rate-limiting infrastructure, and adding
  an in-process limiter would be wrong the moment a second API container runs. Targets for when it
  lands (with staging, roadmap M10, before any public exposure): `POST /auth/login` (per IP and per
  email), `POST /auth/register` (per IP), `POST /imports` (per organisation). What M7 does guarantee:
  sign-in errors reveal nothing about which part was wrong.

## 11. Secrets and configuration

**There is no authentication secret.** Nothing is signed; a session is valid because its row
exists. New settings, all optional:

| Setting | Default | Meaning |
|---|---|---|
| `SESSION_TTL_HOURS` | `24` | absolute sign-in lifetime |
| `SESSION_COOKIE_NAME` | `feedbackiq_session` | cookie name |
| `SESSION_COOKIE_SECURE` | unset → `ENVIRONMENT == production` | the `Secure` flag |
| `ALLOWED_ORIGINS` | `*` | now also: credentials allowed only with an explicit list; the Origin check's allow-list |

Removed: `DEV_ORGANISATION_SLUG`. Unchanged: `API_KEY` is still required in production — for the
research routes. Documented in `.env.example`, `README.md` and `DEPLOY.md`. Nothing new is baked into
the image (§12).

Development vs production (Part 17), stated plainly:

| | Development (`http://localhost`) | Production |
|---|---|---|
| `Secure` cookie | off — a Secure cookie is dropped over plain HTTP | **on**; the API must be served over HTTPS |
| `ALLOWED_ORIGINS` | `*` — no credentialed cross-origin calls, no Origin check | the frontend's URL(s) — required for a browser frontend on another origin |
| Transport | plain HTTP. **Local development is not production-secure**: the cookie crosses the loopback in clear text | TLS terminated in front of the API |

`docker compose` runs `ENVIRONMENT=production` over local HTTP; §12 shows the cookie really is
`Secure` there, and the verification sends it explicitly rather than pretending HTTP is secure.

## 12. Docker verification

Run on 2026-09-15 against a freshly built `feedbackiq-backend` image (the `requirements.txt`
change rebuilt the dependency layer), with compose's PostgreSQL, the backend in its production
configuration (`ENVIRONMENT=production`, `.env` supplying only `API_KEY`, `GROQ_API_KEY`,
`ENVIRONMENT`) and the worker in its own container. Every request went over plain local HTTP with
the cookie sent explicitly — the cookie really is `Secure` there, and HTTP was not treated as secure.

**Image — no secret baked in**

| Check | Result |
|---|---|
| `/app/.env` in the image | absent |
| `API_KEY`, `GROQ_API_KEY`, `SESSION_*`, `AUTH_*`, `DATABASE_URL` in the image environment | none |
| credentials in `docker image history` | none |
| `argon2-cffi` in the image | 25.1.0 (reached the image through `requirements.txt`) |

**Database** — an empty compose volume migrated `0001 → 0006` inside the container; `alembic current`
= `0006 (head)`; the seed found the 13 categories already installed by `0003`.

**Authentication and organisation-scoped data**

| Step | Expected | Result |
|---|---|---|
| `GET /api/v1/feedback` with no cookie | 401 | 401 |
| customer route with only the API key (v1 and `/api/jobs`) | 401 | 401 |
| research route with the API key | 200 | 200 |
| malformed cookie | 401 | 401 |
| register Ana / Acme Ltd | 201, owner | 201, owner |
| `Set-Cookie` | HttpOnly, SameSite=lax, **Secure** | all three |
| token in the response body | absent | absent (43-char token) |
| `/auth/me` with the cookie | Ana | Ana |
| login: wrong password / unknown email | 401, same message | 401, `Incorrect email or password.` for both |
| login: right password | 200 | 200 |
| upload 3 rows via `POST /api/v1/imports` | stored for Acme | 3 rows, Acme's organisation id |
| worker `--drain` in its container (real models) | job succeeds | `succeeded`; summary 3; 3 analysed |
| register Ben / Globex Inc | different organisation | different |
| Ben: feedback and summary with Acme's id in a header and query | 0 | 0 and 0 |
| Ben: Acme's job, v1 import, older import route | 404 | 404, 404, 404 |
| Ben uploads via `/api/imports` naming Acme in a CSV column, form field and header | stored for Globex | Globex's organisation id |
| Acme's feedback after Ben's upload | still 3 | 3 |
| logout, then replay Ana's token | 204, then 401 | 204, 401 |
| backend log: password, either token, either email address | absent | absent |
| backend log: the production `ALLOWED_ORIGINS='*'` warning | present (compose sets no origins) | present, once |

**One false failure, recorded honestly.** The first run reported two failures — "login with an unknown
email" and "login with the right password" returned **422**. The cause was the verification script,
not the application: macOS's `/bin/bash` 3.2 brace-expands a JSON literal containing a comma when it is
written inside `"$( … )"`, so curl sent a fragment of JSON and FastAPI rightly refused it. Reproduced
in isolation, then both checks were re-run against the same container with the bodies built in
variables first: unknown email 401, wrong password 401 with the identical message, right password 200,
no password in the log. The application's own sign-in behaviour is also covered by
`tests/api/test_auth_api.py` and `tests/integration/test_auth_http.py`.

Afterwards compose's backend and PostgreSQL were stopped (volume kept) and the development database
container was restarted.

## 13. Tests

| Suite | Before M7 | After M7 |
|---|---|---|
| `pytest` (unit + API, offline) | 382 passed, 2 xfailed | **508 passed, 2 xfailed** |
| `pytest tests/integration` (PostgreSQL) | 158 passed | **261 passed** |
| flake8 `--select=E9,F` | clean | clean |

New test files and what each protects:

| File | Protects |
|---|---|
| `tests/unit/test_credentials.py` | email shape and normalisation, the password policy, argon2id hashing (salted, not plaintext), verify/rehash, no password in messages |
| `tests/unit/test_session_tokens.py` | tokens are well-formed and unique; the stored value is a one-way hash; malformed values recognised |
| `tests/integration/test_users.py` | unique, normalised email enforced by the database; no plaintext; defaults |
| `tests/integration/test_memberships.py` | roles, unknown/missing roles, duplicates, several organisations, no organisation, both delete directions |
| `tests/integration/test_auth_service.py` | registration (always a new organisation, atomic, duplicate email), sign-in (uniform failure, disabled, rehash), sessions (hash only, expiry, one statement, no query for malformed, membership re-check, soft delete, forged session organisation, sign-out) |
| `tests/api/test_auth_api.py` | the HTTP contract: cookie attributes, no token in bodies, 401/403/409/422/500 mapping, unknown fields refused, no password echo, Origin check, OpenAPI |
| `tests/integration/test_auth_http.py` | register → me → logout end to end; replayed token dead; disabled; expired; nothing sensitive stored |
| `tests/integration/test_tenant_isolation_http.py` | **Part 14** — two companies through the real app; see below |

**Cross-tenant suite (Part 14).** Two organisations register through the API and upload their own
feedback (one via `/api/v1/imports`, one via `/api/imports`), with analysis stored for both. Then, in
both directions: no customer route (12 GET paths) returns the other organisation's id, import id,
job id, feedback ids or text — with forged organisation ids in the query and headers; another
tenant's ids 404 exactly like random ids; counts and aggregates are the caller's own; uploads naming
the other organisation (CSV column, form field, headers) land with the uploader; a member sees what
the owner sees and nothing more; no membership, a forged session organisation, a removed membership
and a soft-deleted organisation get 403; no, malformed, expired, replayed and disabled sessions get
401 — even with the API key.

**Checked that it can fail.** With the tenant filter removed from the feedback query
(`services/feedback.py`), 10 of those tests and 4 of the Milestone 6 isolation tests failed. The
mutation was reverted.

Existing tests changed, and why none was weakened:

- `test_every_api_route_requires_a_key` → `test_every_api_route_refuses_a_caller_with_no_credentials`,
  with an explicit `PUBLIC_ROUTES` allow-list (health, register, login, logout) and a test that fails
  on a stale entry. Every other `/api` route must still return 401.
- The OpenAPI test counted 9 v1 paths; it now counts 9 data paths **and** 4 auth paths.
- `tests/api/test_v1_api.py` and `test_imports_api.py` sign in through
  `app.dependency_overrides[get_current_organisation]`; every existing assertion is kept, fakes accept
  the new `organisation_id` argument, and new tests add 401-with-API-key, 403-no-organisation and
  forged-organisation checks per route.
- Two integration tests asserted the dev-organisation fallback *worked*. That behaviour was removed on
  purpose, so they were replaced by tests that an organisation is always required and that a seeded
  `dev` organisation is not a catch-all.
- `tests/integration/test_tenant_isolation.py` is **unchanged**.

**A latent bug found on the way.** `test_migrations.py` computed its expected tables from
`Base.metadata` without importing the models; it passed only when an earlier test module happened to
import them. Run alone it failed — confirmed at `55d0fac`, before any M7 change. Fixed in its own
commit.

## 14. Known limitations

| # | Limitation |
|---|---|
| 1 | **No rate limiting or lockout** on sign-in, registration or upload (§10). Needed before public exposure. |
| 2 | **Registration reveals whether an email is registered** (409). Hiding it properly needs an email-verification flow ("check your inbox" either way). |
| 3 | **No email verification, password reset or password change.** Out of scope by the brief. |
| 4 | **No invitations or member management.** A second person can only be added to an organisation directly in the database. Owner and member have identical access. |
| 5 | **No organisation switcher.** A user in several organisations acts for the earliest membership. |
| 6 | **Absolute expiry only** — no idle timeout, no "sign out everywhere" route (the query is trivial), no rotation on privilege change. |
| 7 | **Expired session rows are never deleted.** They are inert (every lookup filters `expires_at`); a cleanup belongs with a scheduler. |
| 8 | **No audit log** of sign-ins or failures (the roadmap listed `audit_logs` under M7). |
| 9 | **FastAPI reads a multipart body before dependencies run**, so an unauthenticated upload is parsed before its 401 (as with the API key before). |
| 10 | **A slug collision** — same generated base and the same 24-bit random suffix — would surface as a 500. |
| 11 | **CSRF with `ALLOWED_ORIGINS="*"`** relies on `SameSite=Lax` alone. Production must set explicit origins. |
| 12 | **Import and job responses still include `organisation_id`** — the caller's own, already shown by `/auth/me`. |
| 13 | **No breached-password check** and no row-level security yet (roadmap M16). |
| 14 | The seeded `dev` organisation has no users; to explore customer routes locally, register. |

## 15. Future improvements and the next milestone

In the order they become necessary:

1. **Frontend (the brief's M8)** consuming the contract in §16. Everything it needs exists: sign-up,
   sign-in, `/auth/me`, organisation-scoped data and upload.
2. **Rate limiting and an audit log** — with the staging deployment (roadmap M10), before any real
   customer can reach the API.
3. **Invitations, member management, owner-only actions and more roles** (roadmap M9): the schema,
   the role CHECK and `get_current_organisation` are the extension points; a `require_role("owner")`
   dependency is the first addition.
4. **Email verification and password reset**, which also fixes limitation 2.
5. An organisation switcher; session cleanup on a schedule; row-level security (M16).

**Recommended Milestone 8:** the frontend foundation the brief names — a React/Next.js app that
signs up, signs in, uploads a CSV and shows the organisation's dashboard through this API. Rate
limiting should be scheduled before that frontend is exposed publicly.

---

## 16. Frontend contract (Part 22)

What a React/Next.js frontend needs to know. No frontend was built.

**Deployment shape.** Serve the frontend and the API on the **same site** — e.g.
`app.example.com` and `api.example.com`, or the API behind the frontend's own domain at `/api`. Then
`SameSite=Lax` cookies flow normally. Set `ALLOWED_ORIGINS` to the frontend's exact origin.

**Every request:** `fetch(url, { credentials: "include" })` (or axios `withCredentials: true`). The
browser attaches the cookie; the frontend never sees or stores a token — **nothing goes in
`localStorage`**.

**Sign-up / sign-in:**

```text
POST /api/v1/auth/register  {"email", "password", "organisation_name"}   → 201 CurrentUser + Set-Cookie
POST /api/v1/auth/login     {"email", "password"}                        → 200 CurrentUser + Set-Cookie
```

**Authentication state:** on app load call `GET /api/v1/auth/me`.

```json
{ "email": "ana@acme.example",
  "organisation": { "id": "9b1c…", "name": "Acme Ltd", "role": "owner" } }
```

- 200 → signed in. `organisation: null` → signed in with no organisation; show an explanation
  rather than a dashboard (every data route will return 403).
- 401 → signed out; show the login page.

**Organisation context:** display `organisation.name` and `role`. **Never send an organisation id** —
no route accepts one; the server always uses the session's organisation.

**Sign out:** `POST /api/v1/auth/logout` → 204; then clear client state and route to login. Safe to
call when already signed out.

**Expected errors:**

| Status | Where | Frontend behaviour |
|---|---|---|
| 401 | any data route, `/auth/me` | session ended or expired → go to login |
| 401 | `/auth/login` | show `detail` ("Incorrect email or password.") |
| 403 | `/auth/login` | show `detail` ("This account has been disabled.") |
| 403 | data routes | "You are not a member of an organisation." |
| 403 | any POST | the page's origin is not in `ALLOWED_ORIGINS` — a deployment misconfiguration |
| 409 | `/auth/register` | "An account with this email already exists." — offer sign-in |
| 422 | register/login | `detail` is a string (a broken rule, e.g. password length) **or** FastAPI's list of field errors |
| 404 | detail routes | not found — including anything belonging to another organisation |

Uploads: `POST /api/v1/imports` as `multipart/form-data` with field `file`; the response's `job_id`
is polled at `GET /api/v1/jobs/{job_id}` until `status` is `succeeded` or `failed`.

## 17. How to run and verify

```bash
pip install -e ".[dev]"               # installs argon2-cffi
docker compose up -d postgres
alembic upgrade head                   # 0004 users, 0005 memberships, 0006 user sessions
pytest                                 # 508 passed, 2 xfailed
pytest tests/integration               # 261 passed
uvicorn feedbackiq.api.main:app --reload --port 8000
```

By hand (development, plain HTTP, so the cookie is not `Secure`):

```bash
curl -si -c jar -X POST localhost:8000/api/v1/auth/register -H 'content-type: application/json' \
  -d '{"email":"ana@acme.example","password":"correct horse battery staple","organisation_name":"Acme Ltd"}'
curl -s  -b jar localhost:8000/api/v1/auth/me
printf 'text,rating\nWaited forty minutes for a table,1\nCharged twice for one order,1\n' > feedback.csv
curl -s  -b jar -F file=@feedback.csv localhost:8000/api/v1/imports
python -m feedbackiq.worker --drain
curl -s  -b jar localhost:8000/api/v1/analytics/summary
curl -si localhost:8000/api/v1/feedback                      # 401 without the cookie
```
