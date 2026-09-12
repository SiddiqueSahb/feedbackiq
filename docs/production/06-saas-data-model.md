# 06 — Future SaaS Data Model

> Part of the FeedbackIQ productionisation audit · 2026-09-10 · **Proposal only — nothing implemented.**
> Previous: [05 — Target architecture](05-target-architecture.md) · Next: [07 — Production roadmap](07-production-roadmap.md)

Today FeedbackIQ has **no database**. Data lives in one parquet file, two FAISS indexes, a pickle docstore and JSON/CSV files ([01 §9](01-current-system.md#9-database)). This document proposes the PostgreSQL model the product grows into. Tables arrive gradually (§6), not all at once.

---

## 1. The shape in one picture

```text
Organisation ───────────────────────────────────────────────────────────────┐
  │                                                                           │
  ├── Members ────────── User          (one user can belong to several orgs)  │
  ├── Invitations                                                             │
  ├── API keys                                                                │
  ├── Subscription                                                            │
  │                                                                           │
  ├── Data sources ── Import batches ── Feedback ──┬── Analysis results        │
  │                                                ├── Embedding               │
  │                                                └── (evidence for) Insights │
  ├── Categories        the organisation's own taxonomy                        │
  ├── Analysis runs     one run = one model version over a set of feedback     │
  ├── Insights                                                                 │
  ├── Reports                                                                  │
  ├── Jobs                                                                     │
  ├── Usage records                                                            │
  └── Audit logs ───────────────────────────────────────────────────────────────┘
```

**Rule:** everything under Organisation carries an `organisation_id`. Only `users` and their `sessions` are global, because one person can belong to several organisations.

---

## 2. Relationships

```text
users          1 ──── * organisation_members * ──── 1 organisations
organisations  1 ──── * data_sources  1 ──── * import_batches  1 ──── * feedback
feedback       1 ──── * analysis_results  * ──── 1 analysis_runs
analysis_results * ──── 0..1 categories
feedback       1 ──── 0..1 feedback_embeddings
insights       * ──── * feedback            (through an evidence list)
organisations  1 ──── * categories · jobs · insights · reports · usage_records
                         · audit_logs · invitations · api_keys
organisations  1 ──── 0..1 subscriptions
```

Read `1 ──── *` as "one on the left has many on the right".

---

## 3. Tables

**Conventions (all tables):** `id uuid primary key` · `created_at timestamptz not null default now()` · times in UTC · tenant tables have `organisation_id uuid not null references organisations(id) on delete cascade`.

### 3.1 Identity and organisations

**`organisations`**

| column | type | notes |
|---|---|---|
| id | uuid | |
| name | text not null | |
| slug | text not null unique | used in URLs if wanted |
| deleted_at | timestamptz null | soft delete window before a purge job hard-deletes |
| created_at | timestamptz | |

**`users`**

| column | type | notes |
|---|---|---|
| id | uuid | |
| email | text not null | unique index on `lower(email)` |
| password_hash | text null | argon2 hash; null if SSO is added later |
| full_name | text null | |
| email_verified_at | timestamptz null | |
| last_login_at | timestamptz null | |
| disabled_at | timestamptz null | |
| created_at | timestamptz | |

**`sessions`** (cookie-based sessions — recommended in [09 §16](09-learning-map.md#16-authentication-sessions-vs-jwt--m7))

| column | type | notes |
|---|---|---|
| id | uuid | |
| user_id | uuid not null → users | index |
| token_hash | text not null unique | store a hash, never the token itself |
| expires_at | timestamptz not null | |
| last_seen_at | timestamptz | |
| ip, user_agent | text | for the "active sessions" page and audit |
| created_at | timestamptz | |

**`organisation_members`**

| column | type | notes |
|---|---|---|
| organisation_id | uuid → organisations | primary key part 1 |
| user_id | uuid → users | primary key part 2; index on `user_id` |
| role | text not null | `check (role in ('owner','admin','analyst','viewer'))` |
| created_at | timestamptz | |

"Every organisation keeps at least one owner" is enforced in service code (it's awkward in SQL).

**`invitations`**

| column | type | notes |
|---|---|---|
| id, organisation_id | uuid | |
| email | text not null | |
| role | text not null | same check as members |
| token_hash | text not null unique | single-use |
| invited_by_user_id | uuid → users | |
| expires_at | timestamptz not null | |
| accepted_at, revoked_at | timestamptz null | |

Unique partial index on `(organisation_id, lower(email)) where accepted_at is null and revoked_at is null`, so there's one open invite per person.

**`api_keys`** (the successor to today's single `API_KEY`)

| column | type | notes |
|---|---|---|
| id, organisation_id | uuid | |
| name | text | "Zendesk sync" |
| key_prefix | text | first characters, shown in the UI to identify a key |
| key_hash | text not null unique | today's `secrets.compare_digest` idea, applied to a stored hash |
| role | text | what the key may do |
| created_by_user_id | uuid | |
| last_used_at, revoked_at | timestamptz null | |

### 3.2 Feedback

**`data_sources`** — replaces the hard-coded `platform` (`amazon | yelp | twitter_airline`)

| column | type | notes |
|---|---|---|
| id, organisation_id | uuid | |
| name | text not null | "App Store reviews", "Support tickets" · unique per org |
| kind | text not null | `csv_upload`, later `api`, `integration` |
| settings | jsonb not null default '{}' | integration settings (never secrets in plain text) |

**`import_batches`**

| column | type | notes |
|---|---|---|
| id, organisation_id, data_source_id | uuid | |
| uploaded_by_user_id | uuid null | |
| original_filename | text | |
| storage_key | text | e.g. `orgs/{org_id}/imports/{id}.csv` |
| status | text | `uploaded → validating → importing → analysing → completed / failed` |
| row_count, imported_count, duplicate_count, error_count | integer | |
| error_summary | jsonb | first N row errors with row numbers |
| completed_at | timestamptz null | |

Index `(organisation_id, created_at desc)`.

**`feedback`**

| column | type | today's equivalent | notes |
|---|---|---|---|
| id | uuid | — | |
| organisation_id | uuid not null | — | |
| data_source_id | uuid not null | `platform` | |
| import_batch_id | uuid null | — | |
| external_id | text null | `review_id` | the customer's own ID |
| text | text not null | `text` | original text as received |
| rating | numeric(3,1) null | `rating` | optional: most B2B feedback has none |
| feedback_at | timestamptz null | `date` | when the customer wrote it |
| language | text null | — | English only at launch |
| metadata | jsonb not null default '{}' | `product_category` | product, plan, region, … |
| content_hash | text not null | — | hash of normalised text for de-duplication |
| created_at | timestamptz | — | |

Constraints and indexes:
- `unique (organisation_id, data_source_id, external_id) where external_id is not null`
- `unique (organisation_id, id)` — lets other tables use composite foreign keys (§4)
- index `(organisation_id, feedback_at desc)`, index `(organisation_id, data_source_id)`
- the de-duplication rule (per source or per organisation) is decided with real imports in M5

**Deliberately not stored:** `cleaned_text` (the engine derives it) and `sentiment_label`. Today's `sentiment_label` is a *rating-derived label*, not an analysis result. See §5.

### 3.3 Analysis

**`categories`** — each organisation's taxonomy

| column | type | notes |
|---|---|---|
| id, organisation_id | uuid | |
| name | text not null | unique per org |
| description | text not null | **the NLI hypothesis**, exactly how `nlp/categoriser.py` uses it today |
| exemplars | text[] | used in the embedding shortlist text, as today |
| kind | text | `complaint` (today), later `praise`, `request` |
| source | text | `default` (copied from the dissertation taxonomy), `custom`, `discovered` |
| is_active | boolean default true | |
| updated_at | timestamptz | |

New organisations get a **copy** of the default set, so each can edit freely. Changing a category doesn't rewrite past results, because each run snapshots the categories it used (next table).

**`analysis_runs`**

| column | type | notes |
|---|---|---|
| id, organisation_id | uuid | |
| trigger | text | `import`, `reanalysis`, `manual` |
| import_batch_id | uuid null | |
| status | text | `queued`, `running`, `completed`, `failed` |
| model_versions | jsonb | `{"sentiment": "distilbert-finetuned-final@<checksum>", "categoriser": "deberta-v3-base-zeroshot-v2.0", "embedding": "all-MiniLM-L6-v2"}` |
| category_snapshot | jsonb | the category list used for this run |
| thresholds | jsonb | `{"category_confidence": 0.35}` |
| item_count, failed_count | integer | |
| started_at, finished_at | timestamptz null | |
| error | text null | internal only; not shown raw to users |
| created_by_user_id | uuid null | |

**`analysis_results`** — one row per feedback item per run

| column | type | today's equivalent |
|---|---|---|
| id, organisation_id | uuid | |
| feedback_id | uuid not null | |
| analysis_run_id | uuid not null | |
| sentiment_label | text | `SentimentResult.label` — `check in ('positive','neutral','negative')` |
| sentiment_confidence | real | `SentimentResult.confidence` |
| sentiment_scores | jsonb | `SentimentResult.scores` |
| category_id | uuid null | top category (null when the sentiment gate skips it) |
| category_confidence | real null | `categories[0].score` |
| is_unclassified | boolean | "Unclassified / Emerging Complaint" |
| candidate_categories | jsonb | top-3 with scores, as `/analyse` returns today |
| keywords | text[] | |
| status | text | `ok` / `failed` |
| is_current | boolean | the result shown on dashboards |
| created_at | timestamptz | |

Constraints and indexes:
- `unique (analysis_run_id, feedback_id)`
- composite foreign key `(organisation_id, feedback_id) → feedback (organisation_id, id)`
- partial unique index `(feedback_id) where is_current`
- index `(organisation_id, sentiment_label) where is_current`; index `(organisation_id, category_id) where is_current`

*Why keep older results?* Re-analysing with a new model version shouldn't silently rewrite history. You can compare versions before switching, which is the same habit the dissertation applied.

**`feedback_embeddings`** — replaces both FAISS indexes and the pickle docstore

| column | type | notes |
|---|---|---|
| feedback_id | uuid primary key → feedback | |
| organisation_id | uuid not null | |
| embedding | vector(384) | all-MiniLM-L6-v2, as today |
| model_name | text | |
| created_at | timestamptz | |

Index: HNSW on `embedding` (cosine) plus btree on `organisation_id`. The query shape keeps the tenant filter *inside* the search:

```sql
SELECT feedback_id, 1 - (embedding <=> :query) AS similarity
FROM feedback_embeddings
WHERE organisation_id = :org_id
ORDER BY embedding <=> :query
LIMIT 20;
```

Approximate indexes combined with a filter can lose recall for small tenants in a large table. Measure it in M12 with a labelled question set, using the approach of `evaluate/retrieval_check.py`.

**`insights`** — LLM outputs with their evidence

| column | type | notes |
|---|---|---|
| id, organisation_id | uuid | |
| kind | text | `item_analysis` (today's `/analyse` LLM fields), `category_summary`, `trend_alert`, `recommendation` |
| period_start, period_end | timestamptz null | |
| category_id | uuid null | |
| title, summary, recommendation | text | |
| severity, priority, department | text null | today's `ReviewAnalysisLLM` enums |
| evidence_feedback_ids | uuid[] not null | **traceability, the RQ4 property kept as a column** |
| model_name, prompt_version | text | |
| input_tokens, output_tokens | integer | also written to usage_records |

### 3.4 Operations

**`jobs`** — the simple background queue

| column | type | notes |
|---|---|---|
| id, organisation_id | uuid | |
| kind | text | `analyse_import`, `reanalyse`, `compute_insights`, `generate_report` |
| payload | jsonb | always includes IDs, never feedback text |
| status | text | `queued`, `running`, `succeeded`, `failed` |
| attempts, max_attempts | integer | default 0 / 3 |
| run_after | timestamptz | retry back-off |
| locked_by, locked_at | text, timestamptz | which worker holds it |
| last_error | text null | |
| finished_at | timestamptz null | |

Index `(status, run_after) where status = 'queued'`. A worker claims work with:

```sql
SELECT id FROM jobs
WHERE status = 'queued' AND run_after <= now()
ORDER BY created_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

**`reports`**

| column | type | notes |
|---|---|---|
| id, organisation_id | uuid | |
| title | text | |
| report_type | text | `executive_summary`, `category_breakdown`, `csv_export` |
| parameters | jsonb | date range, sources, categories |
| status | text | |
| storage_key | text null | `orgs/{org_id}/reports/{id}.pdf` |
| created_by_user_id | uuid | |
| completed_at | timestamptz null | |

**`usage_records`** — append-only

| column | type | notes |
|---|---|---|
| id, organisation_id | uuid | |
| metric | text | `feedback_analysed`, `llm_input_tokens`, `llm_output_tokens`, `questions_asked`, `reports_generated` |
| quantity | bigint | |
| occurred_at | timestamptz | |
| source_type, source_id | text, uuid | e.g. `analysis_run` + its id, which makes retries idempotent |
| user_id | uuid null | |

Index `(organisation_id, metric, occurred_at)`. Monthly total: `SUM(quantity) … WHERE occurred_at >= date_trunc('month', now())`.

**`subscriptions`** (M15)

| column | type |
|---|---|
| organisation_id | uuid primary key |
| plan | text (`free`, `team`, `business`) |
| status | text (`trialing`, `active`, `past_due`, `canceled`) |
| stripe_customer_id, stripe_subscription_id | text |
| current_period_start, current_period_end | timestamptz |
| updated_at | timestamptz |

Plan limits start as a Python dictionary in config, not a table.

**`audit_logs`** — append-only

| column | type | notes |
|---|---|---|
| id | uuid | |
| organisation_id | uuid null | null for events before an org is chosen (e.g. login) |
| actor_user_id, actor_api_key_id | uuid null | |
| action | text | `member.invited`, `import.started`, `category.updated`, `report.downloaded`, `api_key.created` |
| target_type, target_id | text, uuid | |
| metadata | jsonb | **no feedback text, no secrets** |
| ip, user_agent | text | |
| created_at | timestamptz | |

Index `(organisation_id, created_at desc)`. The application never updates or deletes audit rows.

---

## 4. Tenant isolation — how it should work

**Model:** one database, shared tables, `organisation_id` on every tenant row (often called the "pool" model). It is the simplest to operate and migrate, and the standard choice for early B2B SaaS.

### Layered defence

```text
Layer 1  Identity     session cookie or API key  →  user (or key)                   auth
Layer 2  Membership   org_id taken from the URL /orgs/{org_id}/…, checked against
                      organisation_members — NEVER trusted from a request body     auth
Layer 3  Permission   does this role allow this action?                             rbac
Layer 4  Query        every service function takes org_id and filters by it        services
Layer 5  Schema       NOT NULL organisation_id + composite foreign keys             database
Layer 6  Safety net   PostgreSQL Row-Level Security (added in M16)                 database
Layer 7  Proof        automated cross-tenant tests for every endpoint              tests
```

### Rules

1. **`organisation_id` comes from the authenticated membership**, never from a body or query parameter on its own.
2. **Return 404, not 403**, for another organisation's resources. Don't reveal that they exist.
3. **Every service function that touches tenant data takes `org_id` as a required parameter:**

   ```python
   def list_feedback(session: Session, org_id: UUID, *, limit: int = 50) -> list[Feedback]:
       stmt = (
           select(Feedback)
           .where(Feedback.organisation_id == org_id)
           .order_by(Feedback.feedback_at.desc())
           .limit(limit)
       )
       return list(session.scalars(stmt))
   ```

   There is no way to call it "for everyone", which makes forgetting the filter much harder.

4. **Composite foreign keys** let the database reject cross-tenant links, even if application code has a bug:

   ```sql
   ALTER TABLE analysis_results
     ADD FOREIGN KEY (organisation_id, feedback_id)
     REFERENCES feedback (organisation_id, id);
   ```

5. **Isolation outside the relational tables:**

   | Where | Rule |
   |---|---|
   | Vector search | `WHERE organisation_id = :org` inside the same SQL query. Today's single global FAISS index cannot do this safely. |
   | Object storage | Keys prefixed `orgs/{org_id}/…`; downloads authorised by the API, never public URLs. |
   | Jobs | Payload carries `org_id`; the worker re-loads data through the same scoped service functions. |
   | LLM prompts | Only ever contain one organisation's feedback. |
   | Caches | Keys include `org_id`. |
   | Logs | Include `org_id`; never feedback text. |

6. **Row-Level Security (later, M16) as a safety net:**

   ```sql
   ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
   CREATE POLICY tenant_isolation ON feedback
     USING (organisation_id = current_setting('app.current_org')::uuid);
   ```

   The application sets `SET LOCAL app.current_org = '…'` at the start of each transaction. RLS protects against a missed filter; it doesn't replace rules 1–5. With connection pooling, the setting must be transaction-scoped (`SET LOCAL`), or it can leak between requests.

---

## 5. How today's data maps

| Today (`reviews_unified.parquet`) | Future | Notes |
|---|---|---|
| `review_id` | `feedback.external_id` | |
| `platform` | `data_sources.name` | a demo organisation gets three sources |
| `product_category` | `feedback.metadata.product_category` | |
| `text` | `feedback.text` | |
| `cleaned_text` | not stored | derived by the engine |
| `rating` | `feedback.rating` | Twitter ratings (5/3/1) are synthetic, mapped from annotations |
| `sentiment_label` | **not** an analysis result | a rating-derived label; at most `metadata.dataset_label` for benchmarking |
| `date` | `feedback.feedback_at` | |

| Other artefact | Future |
|---|---|
| `complaint_categories_all_negative.json` (`category`, `description`, `exemplars`) | `categories` rows with `source='default'`. The `keywords`, `platforms`, `count` and `source_topic_ids` fields are provenance: keep them in a research file, not the table. |
| FAISS vectors | `feedback_embeddings` (re-encode, or bulk-load `review_embeddings.npy` only after verifying row order) |
| `/analyse` sentiment + categories | `analysis_results` |
| `/analyse` LLM fields | `insights` with `kind='item_analysis'` |
| `data/results/*` | stays in `research/`, not in the database |

---

## 6. When each table arrives

| Milestone | Tables |
|---|---|
| **M4** Database | `organisations` (one seeded demo org), `data_sources`, `import_batches`, `feedback`, `categories`, `analysis_runs`, `analysis_results`, `jobs` |
| **M5** Ingestion | uses the M4 tables + object storage keys |
| **M7** Authentication | `users`, `sessions`, `audit_logs` |
| **M8** Multi-tenancy | `organisation_members` + org scoping enforced everywhere |
| **M9** Roles | `invitations`, `api_keys` |
| **M12** Insights | `feedback_embeddings`, `insights` |
| **M13** Reports | `reports` |
| **M14** Usage | `usage_records` |
| **M15** Billing | `subscriptions` |

**Why `organisation_id` exists from M4, even with a single organisation:** adding a NOT NULL column and composite keys to tables that already hold data is the painful migration. Adding it on day one costs one column.

---

## 7. Data protection notes

- Customer feedback belongs to your customer and **may contain personal data** (names, emails, order numbers). Treat it as sensitive: no text in logs or audit metadata, encryption at rest (managed PostgreSQL default), TLS in transit.
- **Deleting an organisation** cascades through its rows (`on delete cascade`) and must also delete `orgs/{org_id}/` objects. Use a soft-delete window first, then a purge job.
- **Retention** settings per organisation can come later, but the schema supports them (`created_at` on everything).
- **LLM provider terms** (Groq today) must be checked before sending customer text. List the provider as a sub-processor.
- **Export per organisation** (CSV) supports portability requests.

## 8. Decisions left open deliberately

| Decision | When |
|---|---|
| De-duplication scope (per source or per organisation) | M5, with real files |
| How long to keep superseded `analysis_results` | Once data volumes are known |
| Multi-label categories (one feedback item mentioning several issues) | `candidate_categories` covers display now; add a join table if needed |
| Partitioning large tables by time | Only when measured to be necessary |
