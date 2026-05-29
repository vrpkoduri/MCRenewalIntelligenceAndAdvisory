# SPRINT 0 — Foundation & Ingestion

**Goal:** Stand up the lakehouse and land the raw funded book, governed. Nothing more.
**Read first:** `CLAUDE.md` (principles, guardrails, tech stack).

---

## Objective

Get the complete Salesforce funded book into a governed **silver** layer — raw landed in bronze, cleaned and typed in silver — with Unity Catalog governance and a scheduled refresh. This is the data foundation everything else reads from. No intelligence is built in this sprint.

---

## Prerequisites (gates)

- **G2 (clock math)** — resolved; not needed until S2.
- **G3 (rung rules)** — resolved; not needed until S3.
- **G1 (data audit)** — must confirm the source object/field names below and the 5 Must-capture gaps. Start with this against the live instance.
- **G4 (environment + data-rights)** — Databricks workspace, Unity Catalog metastore, repo, and confirmation that Morgan Cash holds rights to use funded-book data this way.

---

## In scope

1. **Repo scaffolding** — DAB project per `CLAUDE.md` §5; commit `CLAUDE.md`; `docs/` links to the three companion documents.
2. **Lakeflow Connect ingestion → bronze** (managed CDC) for these Salesforce objects:
   - **Opportunity** (one row per advance)
   - **Merchant** (parent object — needed in S1, ingest now)
   - **Offer / Selected Offer** (deal economics) \u2014 terms come from the **Selected Offer**; also land the full Offers list as `mri.silver.offers` (raw) for later use
   - **Opportunity Field History** (Stage transitions + timestamps — the event source)
3. **Silver transform** — clean and type the deal records; select and rename to the canonical schema (mapping below); apply the data-quality rules (below). Silver grain = **one row per Opportunity (advance)**. Do NOT resolve merchant identity yet (that's S1) — but carry the raw `Merchant` lookup id through.
4. **Unity Catalog** — register bronze/silver under `mri.*`; confirm lineage is captured.
5. **Scheduled refresh** — a Databricks workflow running the bronze→silver pipeline daily.
6. **Reconciliation + data-quality tests** — see exit criteria.

---

## Out of scope (do NOT build)

- Identity resolution / canonical merchant (S1).
- Any derived features, burden, or the amortization clock (S2).
- Rung classification, state machine, event log (S3).
- Offer Engine, prediction, comms, app (S5+).
- Any recomputation of balance/paydown — silver only *carries* the static terms; it does NOT compute live values yet.

---

## Bronze → Silver field map (Opportunity + Offer → `mri.silver.deals`)

> **Source of deal economics:** read the funded terms from the Opportunity's **Selected Offer** (the single winning offer), NOT from the Offers *list*. The Offers list (5\u20137 rows per deal) is shopping history \u2014 useful later for declines/approvals and funder relationships, but never the source of deal terms. Carry the list separately as `mri.silver.offers` (raw) for later sprints.

> **No closure status exists.** `Funded` is the only deal status in Salesforce \u2014 a deal is never marked paid-off/closed/defaulted. Therefore **open-vs-closed is a COMPUTED value**, derived from the clock in S2, not a field. Sprint 0 only lands the terms; it does NOT compute closure. But be aware the downstream chain is: clock (Appendix A) determines paydown \u2192 open/closed \u2192 feeds the lifecycle gate (Appendix B). Closure has three states once computed: closed-clean (reached 100% on schedule, no default note), closed-default (default indicated in Notes), active (<100%). A defaulted deal will *compute* to ~100% on schedule (see Starr) \u2014 so paydown alone never means "cleanly closed"; the Notes default-cause is required to separate the two.

Use real Salesforce field names on the left. Apply DQ rules in the notes. Verdict legend: **Have** = ingest directly; **Carry** = pass through for later sprints; **Distrust** = ingest to a `_sf_stored_*` column for checkpoint only, never surface.

| Silver column | Type | Salesforce source | Verdict | Notes / DQ rule |
|---|---|---|---|---|
| `opportunity_id` | string | Opportunity Id | Have | PK |
| `merchant_sf_id` | string | Merchant (lookup) | Carry | raw parent id; identity resolution in S1 |
| `opportunity_name` | string | Opportunity Name | Have | |
| `stage` | string | Stage | Have | filter funded book on Stage = Funded |
| `deal_type` | enum | Type | Have | New Business / Renewal / Buyout — trust as renewal flag |
| `funder` | string | Funder(s) | Have | may be multi-value; keep raw + parsed |
| `funded_amount` | decimal | Funded Amount | Have | |
| `factor_rate` | decimal | Rate | Have | e.g. 1.459 |
| `payback_amount` | decimal | Payback Amount | Have | cross-check: ≈ funded_amount × factor_rate (RTR) |
| `payment_amount` | decimal | Payment Amount | Have | fixed-ACH |
| `num_payments` | int | Number of Payments | Have | |
| `payment_frequency` | enum | Frequency | Have | Daily / Weekly |
| `funded_date` | date | Funded Date | Have | funding anchor; run date-sanity check vs created_date |
| `created_date` | timestamp | Date Created | Have | flag if funded_date > created_date (contradiction) |
| `days_in_stage` | int | Days in Stage | Carry | unreliable on migrated records; carry, don't rely |
| `state_of_incorporation` | string | State of Incorporation | Have | blank = missing (governing-state proxy) |
| `months_in_business` | int | Months in Business | Have | **0/blank = MISSING**; prefer deriving from business_start_date |
| `business_start_date` | date | Business Start Date | Have | preferred source for tenure |
| `fico` | int | FICO | Have | **0/blank = MISSING** |
| `position_at_funding` | int | Position | Carry | static, non-mandatory; blank = unknown (NOT zero) |
| `total_house_commission` | decimal | Total House Commission | Carry | |
| `max_approved_amount` | decimal | Maximum Approved Amount | Carry | |
| `num_approvals` | int | Number of Approvals | Carry | |
| `num_declines` | int | Number of Declines | Carry | |
| `notes` | string | Notes | Carry | free-text; may hold default cause (e.g. "Defaulted — $250 clawback") |
| `send_renewal_notices` | bool | Send Renewal Notices | Carry | repurpose later as consent toggle |
| `contact_name` | string | Contact | Carry | PII |
| `mobile` | string | Mobile | Carry | PII; consent before any use |
| `email` | string | Email | Carry | PII |
| `application_submission_id` | string | Application Submission | Carry | |
| `_sf_stored_remaining_balance` | decimal | Remaining Balance | **Distrust** | checkpoint only — NEVER surface; recompute in S2 |
| `_sf_stored_percentage_paid` | decimal | Percentage Paid | **Distrust** | checkpoint only — NEVER surface |
| `_sf_stored_est_renewal_date` | date | Estimated Renewal Date | **Distrust** | checkpoint only — replace in S2 |

Also land `mri.silver.field_history` from Opportunity Field History: `opportunity_id`, `field`, `old_value`, `new_value`, `changed_at`, `changed_by`. (Event source for S1/S3.)

---

## Data-quality rules (apply in silver)

1. **0/blank = missing** for `months_in_business`, `fico`, and any revenue field. Emit a nullable typed value + a `*_is_missing` boolean rather than storing 0.
2. **Date sanity:** if `funded_date > created_date` or `funded_date` is implausibly old for an active deal, set `date_sanity_flag = true`. Do not drop the row — flag it.
3. **RTR cross-check:** compute `rtr_check = funded_amount * factor_rate`; flag if `abs(rtr_check - payback_amount)` exceeds a small tolerance. (Diagnostic only; do not overwrite.)
4. Preserve raw bronze untouched; all cleaning happens bronze→silver.

---

## Definition of Done (exit criteria)

- [ ] Every funded Opportunity is queryable in `mri.silver.deals`; **row count reconciles** to the Salesforce funded book (Stage = Funded) within an explained tolerance.
- [ ] Schema documented; all columns typed per the map; `_sf_stored_*` columns isolated and clearly marked do-not-surface.
- [ ] `mri.silver.field_history` populated.
- [ ] DQ flags (`*_is_missing`, `date_sanity_flag`, RTR cross-check) computed and queryable; counts reported (how many deals carry each flag).
- [ ] Unity Catalog governs `mri.bronze.*` and `mri.silver.*` with lineage visible.
- [ ] Scheduled daily refresh runs end-to-end successfully.
- [ ] The four validation merchants (Starr, One Big Promotion, Tom Snell, Wolf) are present in silver and spot-checked by hand against the screenshots.

---

## Tests to write

- **Reconciliation:** `count(silver.deals where stage=Funded)` == Salesforce funded count (± explained).
- **RTR integrity:** on a sample, `funded_amount * factor_rate ≈ payback_amount`.
- **Schema/type:** every mapped column present and correctly typed.
- **DQ:** rows with `months_in_business = 0` carry `months_in_business_is_missing = true`; date contradictions carry `date_sanity_flag`.
- **No-surface guard:** a test asserting no downstream silver view exposes `_sf_stored_*` columns.

---

## Gotchas (learned from real deals)

- Stored balance/% paid are wrong on ~every deal — that's expected; it's WHY they're `_sf_stored_*` checkpoint-only.
- `funded_date` and `created_date` can contradict (migration artifacts). Anchor on `funded_date`, flag contradictions.
- `Funder(s)` and some fields are multi-value — keep raw and a parsed form.
- A "renewal" (Type=Renewal) pays off the prior position, so a renewed merchant may show ONE active position even though they re-upped — relevant in S1/S3, not S0, but don't let it surprise you when eyeballing Wolf.
