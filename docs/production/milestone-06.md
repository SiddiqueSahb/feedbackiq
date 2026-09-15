# Milestone 6 — Product Taxonomy + Analytics API Foundation

**Status:** complete, pending review
**Preceded by:** [Milestone 5B](milestone-05b.md) · **Companion:** [Taxonomy product review](taxonomy-product-review.md)

---

## 1. Product taxonomy rationale

Milestone 5B established that the dissertation's 24 discovered categories are not a
customer-facing taxonomy. This milestone designed one: **13 industry-neutral categories,
taxonomy `product-13` version 2.0.0**.

| Key | Name |
|---|---|
| `product_quality_and_performance` | Product Quality & Performance |
| `product_not_as_described` | Not As Described |
| `delivery_and_fulfilment` | Delivery & Fulfilment |
| `wait_times_and_delays` | Wait Times & Delays |
| `service_quality` | Service Quality |
| `support_responsiveness` | Support Responsiveness |
| `billing_and_payments` | Billing & Payments |
| `pricing_and_value` | Pricing & Value |
| `refunds_and_returns` | Refunds & Returns |
| `account_and_access` | Account & Access |
| `app_and_technical_issues` | App & Technical Issues |
| `booking_and_scheduling` | Booking & Scheduling |
| `facilities_and_environment` | Facilities & Environment |

Each absorbs research categories, fills a measured gap, or both:

- **Absorbed:** the seven "… Performance Failures" variants (audio, visual, hair, tablet,
  GPS, laptop cooling, mirror) collapse into *Product Quality & Performance*; *Salon Service
  Failures* and *Customer Service Apology Failures* into *Service Quality* and *Support
  Responsiveness*; *Travel Disruption Issues* into *Booking & Scheduling*; *Product Colour
  Representation* and *Product Fit and Sizing* into *Not As Described*.
- **Gaps filled:** *Billing & Payments*, *Refunds & Returns*, *Account & Access* — the
  research set had none of them, which is why "charged me twice" landed in *Product
  Performance Failures* and "the app crashes when I log in" was unclassified.
- **Deliberately absent:** any "Other" bucket. The 0.35 threshold already produces
  "Unclassified / Emerging Complaint", and an Other category would absorb weak matches and
  destroy that signal.

---

## 2. Evidence used

Only what the repository actually contains:

| Evidence | What it showed |
|---|---|
| `complaint_categories_all_negative.json` assignment counts | 130,767 assignments, **56% in one category**; 9 categories under 0.5%; 15 of 24 single-platform |
| `data/results/category_discovery/evaluation_summary.csv` | the dissertation's own 450-review evaluation: 21 buckets, largest 127, **smallest 1**, mean coherence 0.6334 |
| `category_frequency.csv` | **124 of 450 (27.6%) unclassified** on that sample |
| `sentiment_by_category.csv` | **7 of 21 "complaint" categories were mostly positive reviews** (pre-sentiment-gate) |
| `representative_reviews.csv` | *Laptop Cooling System Failures* assigned to a **positive Yelp review about a restaurant**; *Tablet Accessory Quality Failures* to a Nikon charger |
| `manual_validation.csv` | right columns for ground truth, **0 of 100 rows filled in** |
| corpus composition | 449,858 Yelp / 178,883 Amazon / 13,951 airline; `product_category` has 4 values, mostly empty |
| description embeddings (MiniLM) | worst confusable pair 0.720 (*Visual* ↔ *Audio Performance Failures*) |

---

## 3. Taxonomy version

`taxonomy_id: product-13`, `taxonomy_version: 2.0.0`, with a changelog in the file. The
version reaches every stored result through the engine manifest — `taxonomy_id`,
`taxonomy_version` and `taxonomy_source` (`default` or `caller`) land in
`analysis_runs.model_versions`, so any result can answer which taxonomy produced it.

`core/taxonomy.py` stays dependency-free (standard library only, no `core.config` import), so
the taxonomy can still be verified in a bare container.

---

## 4. Research vs product taxonomy

```text
core/default_categories.json   complaint-24  1.1.0   load_dissertation_taxonomy()   research
core/product_categories.json   product-13    2.0.0   load_default_taxonomy()        product default
```

The research taxonomy is **retained, not replaced**:

- the file still ships and still loads;
- its 24 rows stay in `categories`, marked `source='discovered'`, `is_active=false`;
- historical `analysis_results` keep resolving to those rows.

Deleting them was rejected: `analysis_results.category_id` is `ON DELETE SET NULL`, so a
delete would have silently blanked the category on every pre-Milestone-6 result.
`is_active` — added in Milestone 4 and unused until now — is what separates "offered for new
analysis" from "still referenced by history".

---

## 5. Benchmark methodology

**Accuracy cannot be measured, and no proxy is presented as if it could be.** There is no
category-labelled data in this repository: the categories were discovered by BERTopic, and
`manual_validation.csv` is an empty template (0 of 100 rows). The corpus carries a
rating-derived `sentiment_label` and a 4-value `product_category`, neither of which labels a
complaint theme.

What was measured instead, with the real models (DistilBERT + DeBERTa NLI + MiniLM):

1. **Coverage** — unclassified rate at the 0.35 threshold on a **199-review stratified sample**
   of negative/neutral corpus reviews (seed 20260914; 139 Yelp / 55 Amazon / 5 airline), the
   population the sentiment gate actually sends to the categoriser.
2. **Distribution** — how many categories are ever chosen, and how large the biggest bucket is.
3. **Confidence** — mean top score.
4. **Distinctness** — pairwise cosine similarity of the description embeddings (the
   descriptions *are* the NLI hypotheses, so confusable descriptions mean confusable output).
5. **Face validity** — 16 canonical business complaints, reported as judgement, not accuracy.

The sentiment benchmark (`tests/benchmark/`) is untouched: it has no taxonomy dependency, so
its metric floor still stands.

---

## 6. Benchmark results

Same sample, same models, same threshold:

| | Research 1.1.0 | Product 2.0.0 |
|---|---|---|
| Unclassified | 17.6% (35/199) | **17.1% (34/199)** |
| Categories ever chosen | 17 of 24 — **7 never** | **13 of 13 — none unused** |
| Largest single category | **37.7%** | **20.1%** |
| Mean top score | 0.533 | **0.572** |
| Worst description pair | 0.720 | **0.613** |
| Pairs above 0.70 | 1 | **0** |

**The decisive result is distribution, not coverage.** Coverage is a wash. What changes is
that the research taxonomy leaves seven categories permanently empty and funnels 38% of
everything into *Product Performance Failures*, while the product taxonomy uses all thirteen
with its largest bucket at half that.

Face validity, research → product:

| Complaint | Research | Product |
|---|---|---|
| charged me twice, no refund | *Product Performance Failures* 0.39 | **Refunds & Returns** 0.47 |
| app crashes when I log in | **unclassified** 0.26 | **App & Technical Issues** 0.62 |
| cannot reset my password | *Product Fit and Sizing* 0.46 | **Support Responsiveness** 0.73 |
| flight cancelled two hours before | **unclassified** 0.26 | **Booking & Scheduling** 0.97 |
| toilets filthy, floor sticky | *Product Taste and Aroma* 0.40 | **Facilities & Environment** 0.90 |
| invoice has unmentioned fees | **unclassified** 0.33 | **Billing & Payments** 0.54 |
| parcel a week late, box crushed | *Order Fulfillment Delays* 0.43 | **Delivery & Fulfilment** 0.44 |
| still waiting for my refund | *Product Performance Failures* 0.40 | **unclassified** 0.28 |

**Three iterations, reported honestly.** The first product draft scored 20.1% unclassified.
Rewording five descriptions cut that to 5.0% but made *Not As Described* absorb **56.8%** of
the sample — worse, not better, and it stole the parcel-late case. Narrowing that one
description to "a different item, colour or size from the one ordered" produced the figures
above. A fourth iteration was declined: the one remaining miss (refund-not-paid, 0.28) is
documented rather than chased.

---

## 7. API design

`/api/v1`, nine routes, added without touching the existing 25:

| Method | Path |
|---|---|
| GET | `/api/v1/feedback` — page, filter, sort |
| GET | `/api/v1/feedback/{id}` — detail with provenance |
| GET | `/api/v1/analytics/summary` |
| GET | `/api/v1/analytics/trend?interval=day\|week\|month` |
| GET | `/api/v1/analytics/categories` |
| GET | `/api/v1/categories` |
| GET | `/api/v1/imports` |
| GET | `/api/v1/imports/{id}` |
| GET | `/api/v1/jobs/{id}` |

Filters: `sentiment`, `category_key`, `unclassified`, `analysed`, `platform`, `search`,
`date_from`, `date_to`, `min_rating`, `max_rating`, `page`, `page_size` (default 50, cap 200),
`sort` (whitelisted), `order`.

Design notes:
- **Versioned** because this is the first API a frontend will be written against.
- **Sessions open inside handlers**, after the API key check — never as a FastAPI dependency,
  or an unauthenticated request would touch the database and `tests/api` would need PostgreSQL.
- Blocking work runs through `asyncio.to_thread`, as everywhere else.
- `sort` is a **whitelist**, not a passed-through column name.
- v1 has its own schemas (`api/v1/schemas.py`); the dissertation-era shapes in `api/schemas.py`
  exist to keep Streamlit working and should be free to diverge.
- Errors: 401/403 auth, 422 invalid filter or malformed id, 404 unknown id, 500 with a fixed
  message and nothing internal.

---

## 8. Organisation scoping

```text
request → api/deps.py::resolve_organisation_id(session) → organisation_id → every WHERE clause
```

One function decides the tenant, for both `/api/v1` and the Milestone 5B upload routes. It
ignores everything the caller sends — no query parameter, header or CSV column can influence
it. Until Milestone 7 it resolves the seeded development organisation.

Every service function takes `organisation_id` as a **required keyword argument**, so a query
that could be written without a tenant scope does not typecheck as complete. `_scoped_query`
in `services/feedback.py` is the single place the scope is applied for listing, counting and
detail, so the three cannot drift apart.

---

## 9. Tenant-isolation tests

`tests/integration/test_tenant_isolation.py` builds two organisations with their own feedback,
analysis, imports and jobs, then asks every entry point for one tenant's data while scoped to
the other:

| Entry point | Asserted |
|---|---|
| feedback list | only own rows; totals 2 and 1 |
| feedback filter | searching the other tenant's exact text returns 0 |
| feedback detail | the other tenant's valid id reads as absent (`None` → 404) |
| analytics summary / trend / categories | counts only own rows |
| available categories | a custom category is invisible to the other organisation |
| import list / detail | only own imports |
| job detail | the other tenant's job id reads as absent |
| database constraint | the Milestone 4 composite FK still rejects a cross-organisation result |
| unknown organisation | returns **empty, never everything** — the failure mode of a missing WHERE |

---

## 10. Analytics queries

All aggregation is `GROUP BY` in PostgreSQL:

- **summary** — one statement, conditional aggregates (`count(...) FILTER`), so three sentiment
  counts cost one round trip;
- **trend** — `date_trunc(interval, coalesce(feedback_at, created_at))`, so feedback with no
  date of its own is grouped by ingestion time and the trend still adds up to the summary;
- **category breakdown** — grouped by the stable key, largest first, with the no-category
  bucket reported as its own row rather than dropped;
- percentages are of **analysed** feedback, so they are not diluted by rows the worker has not
  reached.

Tests assert the **statement count**, not just the numbers: `summary`, `trend` and
`category_breakdown` are each exactly one statement. The predecessor
(`services/analytics_service.py`) read a 427 MB parquet file into pandas per request; a test
that only checked totals would not have noticed the difference.

---

## 11. Stale-job recovery

Milestone 5B's limitation 2, closed. `services/maintenance.py`:

```text
running + locked_at older than STALE_JOB_MINUTES (30) ──┬──▶ queued   (attempts remain)
                                                        └──▶ failed   (attempts spent)
```

Reclaimed jobs go through the same `mark_failed` path as an honest failure, so retry limits
still apply and a job that kills its worker every time stops instead of cycling forever. The
recorded message names the worker that held it and the threshold, and contains no traceback.
`find_stale_jobs` locks with `FOR UPDATE SKIP LOCKED` so two sweeps cannot fight.

Run on worker startup (a restarted worker recovers its own abandoned work) or by hand:
`python -m feedbackiq.worker --reclaim`. Deliberately **not** on a timer inside the worker
loop — a periodic sweep in every process is how two workers reclaim the same job, and there is
no scheduler here to own it.

---

## 12. Indexes added, and why none were

**No new indexes.** Measured at 40,000 feedback rows (20,000 analysed for the tenant under
test, a second organisation holding the rest, tables `ANALYZE`d):

| Query | Time | Plan |
|---|---|---|
| list page 1, newest first | 14.6 ms | parallel seq scan + hash join |
| filter by sentiment | 0.12 ms | index scan on `pk_feedback` |
| filter by category key | 0.15 ms | **bitmap index scan on `ix_analysis_results_organisation_id_category_id`** |
| substring search | 7.5 ms | bitmap scan via `ix_feedback_organisation_id_feedback_at` |
| summary aggregate | 8.5 ms | seq scan (reads every tenant row by definition) |
| trend by day | 9.0 ms | seq scan + `date_trunc` grouping |
| category breakdown | 8.6 ms | seq scan + hash aggregate |

The sequential scans are the planner's correct choice: an aggregate over a tenant's whole
dataset touches every row, so a scan beats an index walk. The existing Milestone 4 indexes
already engage wherever there is selectivity. Adding indexes here would cost write throughput
on the ingestion path and buy nothing measurable — Part 11's instruction was to add them only
when justified, and at this volume they are not.

**The one to watch:** substring search is `ILIKE`, which degrades linearly. A trigram
(`pg_trgm`) or full-text index is the honest answer when volume justifies it; it is recorded
as a limitation rather than added speculatively.

---

## 13. Performance observations

| | |
|---|---|
| v1 endpoint queries at 40k rows | 0.12–14.6 ms |
| summary / trend / breakdown | **1 SQL statement each** |
| feedback list | 2 statements (one COUNT, one page) |
| ingestion (unchanged) | 8,519 rows/s, 14 statements for 5,487 rows |
| taxonomy evaluation | 199 reviews × 2 taxonomies with real models, minutes |

Counting in SQL rather than measuring a fetched list is what makes the pager work at volume;
the tests assert it.

---

## 14. Known limitations

| # | Limitation |
|---|---|
| 1 | **No authentication.** One shared API key; every caller acts as the development organisation. `resolve_organisation_id` is the single stand-in. Milestone 7. |
| 2 | **Search is `ILIKE` substring matching**, not ranked full-text. 7.5 ms at 40k rows and linear; needs `pg_trgm` or `tsvector` at real volume. |
| 3 | **One canonical scenario still misses**: refund-not-paid scores 0.28 against *Refunds & Returns* and comes back unclassified. |
| 4 | **Category accuracy is unmeasured and unmeasurable here** — no labelled data exists. Coverage, distribution and distinctness are proxies, and are labelled as such. |
| 5 | **`spray_bottle_…` was not renamed.** It is retired and inactive, so no customer sees it. |
| 6 | **Per-organisation taxonomies are unbuilt.** The schema supports them and an organisation's key shadows a default, but nothing creates them. |
| 7 | **The trend has no gap filling and a 400-point cap.** Empty periods are absent; presentation is the caller's job. |
| 8 | **`platform` lives in `feedback.metadata`** (JSONB), so filtering it is not indexed. |
| 9 | **No stale-job scheduler.** Recovery runs at worker startup or by hand, not on a timer. |
| 10 | **`services/analytics_service.py` (pandas/parquet) still exists** and still backs the old `/api/analytics/*` routes that Streamlit uses. Two analytics implementations coexist until Streamlit is replaced. |
| 11 | **The 13 categories are designed, not validated against customer data.** No real customer feedback has been categorised with them yet. |

---

## 15. Verification

| Check | Result |
|---|---|
| `pytest` | **378 passed, 2 xfailed** (was 330/2) |
| `pytest tests/integration` | **158 passed** (was 97) |
| flake8 `--select=E9,F` | clean |
| Migration `0003` | dev database retires 24 / installs 13; fresh database installs 13; downgrade → re-upgrade; `alembic check` clean; `exemplars` round-trips as `text[]` |
| Migration ↔ packaged taxonomy | identical (asserted by a test) |
| API surface | 25 existing routes unchanged, **9 new v1 routes**, 34 total |
| OpenAPI | all nine v1 paths documented with summaries, descriptions and parameters (asserted) |
| Docker | migrate 0001→0003 in-container, upload → worker → job `succeeded` (5 analysed), every v1 endpoint correct, all filters correct, 422/401/404 rejections correct, reaper requeued a 2-hour-stale job |
| Sentiment benchmark | untouched — no taxonomy dependency |

A detail worth stating plainly: in the Docker run the seed reported `categories_created: 0`
because migration `0003` had already installed the 13. That is the seed being idempotent, not
a failure.

---

## 16. Recommended Milestone 7

**Authentication and organisation membership** (roadmap
[M7](07-production-roadmap.md#m7--authentication)). It is now the single thing standing between
this and a usable multi-tenant product: the data model, the queries, the API and the isolation
tests are all organisation-scoped already, and exactly one function —
`api/deps.py::resolve_organisation_id` — needs replacing with a real user identity. The
isolation suite becomes the regression net for it.

Worth folding in: a `pg_trgm` index for search (limitation 2) if real feedback volume arrives
first, and retiring `services/analytics_service.py` once Streamlit no longer needs it.

**Awaiting approval before starting.**
