# Taxonomy: product review

> Written during [Milestone 5B](milestone-05b.md), following the finding recorded as
> limitation 4 of [Milestone 5A](milestone-05a.md). **This document changes nothing.** It
> classifies the current taxonomy and recommends what a product taxonomy should look like;
> renaming anything is a separate, approved decision.

## The question

FeedbackIQ ships a default taxonomy of 24 complaint categories. They came from the
dissertation's discovery pipeline — BERTopic per platform, then a cross-platform merge, over
a corpus that is roughly 70% Yelp reviews with the remainder Amazon products and airline
tweets. That made them a *finding*: evidence of what the corpus contained.

A SaaS default taxonomy has a different job. It is the first thing a new customer sees, before
they have defined anything themselves, and it has to be recognisable to a business that has
never read the dissertation.

Those two jobs do not produce the same list.

## The 24 categories, classified

**Legend** — ✅ usable as a customer-facing default · ⚠️ recognisable but narrow or oddly
worded · ❌ research artefact, not a business category.

| Key (stable) | Current name | Verdict |
|---|---|---|
| `service_and_wait_time_delays` | Service and Wait Time Delays | ✅ |
| `order_fulfillment_delays` | Order Fulfillment Delays | ✅ |
| `product_performance_failures` | Product Performance Failures | ✅ |
| `travel_disruption_issues` | Travel Disruption Issues | ✅ |
| `product_fit_and_sizing_issues` | Product Fit and Sizing Issues | ✅ |
| `mounting_and_installation_problems` | Mounting and Installation Problems | ✅ |
| `home_network_connectivity_issues` | Home Network Connectivity Issues | ✅ |
| `digital_platform_performance_failures` | Digital Platform Performance Failures | ✅ |
| `customer_service_apology_failures` | Customer Service Apology Failures | ⚠️ an odd framing of "how the complaint was handled" |
| `product_taste_and_aroma_failures` | Product Taste and Aroma Failures | ⚠️ food and drink only |
| `visual_quality_and_performance_failures` | Visual Quality and Performance Failures | ⚠️ "visual" conflates screens and appearance |
| `product_color_representation_failures` | Product Color Representation Failures | ⚠️ narrow: "not the colour shown online" |
| `accessory_attachment_and_fastening_failures` | Accessory Attachment and Fastening Failures | ⚠️ narrow |
| `audio_performance_failures` | Audio Performance Failures | ⚠️ product-specific |
| `financial_institution_service_failures` | Financial Institution Service Failures | ⚠️ industry-specific |
| `salon_service_failures` | Salon Service Failures | ❌ one Yelp vertical |
| `hair_quality_and_performance_failures` | Hair Quality and Performance Failures | ❌ one product niche |
| `facial_protection_and_safety_failures` | Facial Protection and Safety Failures | ❌ pandemic-era Amazon niche |
| `tablet_accessory_quality_failures` | Tablet Accessory Quality Failures | ❌ one product niche |
| `gps_device_performance_failures` | GPS Device Performance Failures | ❌ one product niche |
| `laptop_cooling_system_failures` | Laptop Cooling System Failures | ❌ one product niche |
| `mirror_performance_failures` | Mirror Performance Failures | ❌ one product niche |
| `direct_messaging_access_issues` | Direct Messaging Access Issues | ❌ an artefact of airline tweets |
| `spray_bottle_continuous_spray_dryer_diffuser_fit_dryer` | Spray Bottle, Continuous Spray, Dryer Diffuser, Fit Dryer | ❌ **not a name at all** — an unmerged BERTopic term list |

**Tally: 8 usable, 7 workable but narrow, 9 research artefacts.**

## What this means

Three problems, in order of severity.

1. **One entry is not a category.** `spray_bottle_continuous_spray_dryer_diffuser_fit_dryer`
   is a list of topic terms that the merge step never turned into a label. Any customer who
   sees it loses confidence in everything next to it.
2. **A third of the taxonomy is single-vertical.** A B2B software company offered "Salon
   Service Failures" and "Mirror Performance Failures" learns nothing. These categories will
   almost never match their feedback, and the ones that would match — billing, onboarding,
   account access, performance, documentation — are missing entirely.
3. **Nothing covers the obvious.** There is no billing/pricing category, no refunds or
   returns category, no account/login category, no data-privacy category. The *retired*
   7-category fallback actually covered pricing and refunds; the discovered 24 does not.

The cause is not a modelling error. It is that the corpus decided the taxonomy, and the
corpus was chosen for research reasons — three public datasets with labelled sentiment — not
because it resembled a prospective customer's feedback.

## Recommendation

**Yes: the product taxonomy should eventually differ from the dissertation taxonomy.** They
answer different questions, and both should exist.

| | Dissertation taxonomy | Product default taxonomy |
|---|---|---|
| Purpose | what this corpus contains | a useful starting point for a new customer |
| Source | BERTopic discovery + merge | designed, then validated against real feedback |
| Changes | frozen — it is published evidence | versioned and revisable |
| Lives in | `data/processed/` (research output) | `core/default_categories.json` |

A sensible target is **10–14 broad, industry-neutral categories** — for example: service
quality, delivery and fulfilment, product quality, product performance, pricing and value,
billing and charges, refunds and returns, account and access, usability, reliability and
outages, support responsiveness, documentation — each with a description written as an NLI
hypothesis and a few exemplars, in the same file format.

Crucially, that is now a **cheap change that costs no stored data**, because Milestone 5B
made the key the identity:

```text
category_key = stable identity   ──▶  stored results point here
category_name = customer label   ──▶  free to reword at any time
```

Renaming *is already safe*. Splitting or merging categories is not, and needs a mapping
decision per affected key.

## Proposed sequence (not done here)

1. **Rename the one broken entry.** `spray_bottle_continuous_spray_dryer_diffuser_fit_dryer`
   → "Hair Styling Tool Failures", keeping the key. Taxonomy `1.2.0`. Safe: no stored result
   changes, because results reference the key.
2. **Design the product default set** (10–14 categories) as taxonomy `2.0.0`, with new keys.
3. **Measure before adopting it.** Re-run the production benchmark and the unclassified share
   on the same sample. A broader taxonomy will change the "Unclassified / Emerging Complaint"
   rate, and that number is a product signal — it must be understood, not discovered later.
4. **Keep the discovered 24 available** as a named alternative taxonomy, so the dissertation
   remains reproducible.
5. **Only then** consider per-organisation taxonomies (the schema already supports them:
   `categories.organisation_id`).

## Why nothing changed in this milestone

Renaming categories is a product decision with a measurable effect on categorisation
quality, and the brief for 5B was explicit that names must not change here. The correctness
problem — the engine and the seed disagreeing, and a silent 7-category fallback — was fixed
in 5A. The *suitability* problem is this document, and it needs approval plus a benchmark
run, not a quiet edit.
