# Data request — cs-partner-dashboard complete backfill

**Audience**: MoradaUno engineering / CTO.
**Asker**: Inés Gamboa (PM, CS Partner Dashboard).
**Why this is being asked**: the dashboard at https://inesgamboasorensen.github.io/cs-partner-dashboard/ is currently fed by the MoradaUno MCP, which caps at the most-recent ~10,000 deals (about 80 days of current volume). That makes trend, lifecycle, and risk calculations directionally wrong for any broker who was active before Feb 2026. Several fields (PIC, Plaza, full org details) are also missing for the majority of records. To fix this, we need a one-time historical export of the data below. Once we have it, the existing nightly MCP delta will keep things fresh.

**Deliverable format**: 5 JSONL files (one record per line), described below. CSV is also fine if easier on your side — but we need the same fields and the same record-per-line shape. Drop them into the repo at `cs-data/imports/` or share via a private S3 / Drive link.

---

## 1. `deals.jsonl` — full historical deals

**Goal**: every deal MoradaUno has ever had, with state as of the export moment. The CS Partner Dashboard needs the full history to compute tenure, trend, lifecycle, retention, and revenue accurately.

**Scale expectation**: probably 50K–200K records (we only know the last 80 days = ~10K). One record per line.

**Required fields per deal**:

| field | type | notes / why we need it |
|---|---|---|
| `id` | int | primary key, used to dedupe and merge with MCP delta |
| `created` | ISO datetime | when the deal was registered; drives every time-window metric |
| `end_date` | ISO datetime | contract end; drives renewal-window / past-grace classification |
| `deal_type` | string | `Open` / `Closed` / `Renewal` / `Cancelled` |
| `deal_status` | string | human-readable status |
| `pipefy_phase_name` | string | `Tenant Profile` / `Contracts` / `Completed` / `Zombieland` / etc. |
| `pipefy_status` | string | display tag |
| `parent_id` | int (nullable) | **critical** — points back to the deal a Renewal/Closed-renewal-of-old is renewing; used for retention math. We've already documented the data gap where this gets cleared when a Renewal reaches Completed — please preserve `parent_id` even on Completed Renewals if possible |
| `renewal_available` | int (0/1) | did the customer opt-in to renewal flow |
| `product` | int | numeric id |
| `product_name` | string | `Guarantee` / `Screening` / `Legal` |
| `sub_product` | int | numeric id |
| `sub_product_name` | string | `M3` / `M6` / `M12` / `M Legal` / etc. |
| `value` | number | monthly rent (MXN) |
| `cost_percent` | number | **the rate** applied to value to compute Pago Total. Currently only exposed via per-deal admin_deal_detail.steps[0].data. We need it at the top level of every deal in the export. |
| `revenue` | number | Pago Total (value × cost_percent). Same — currently only in the nested response. |
| `contract_id` | string | external A-number |
| `contract_type` | string | `Normal` / `Renewal` |
| `date_signature` | ISO datetime (nullable) | |
| `address` | string | property address |
| `tenant_name` | string | primary tenant |
| `tenants_resume` | string | `Tenant\|\|<name>,Roomie\|\|<name>,Cosigner\|\|<name>` — pipe-delimited |
| `landlord_name` | string (nullable) | |
| `moral_company_name` | string (nullable) | if landlord is a company |
| `broker_id` | int | **add this** — currently the deal carries `broker_name` (string) but no FK to the brokers table. Having `broker_id` would let us merge perfectly against the brokers export below. |
| `broker_name` | string | for display + as a fallback key |
| `broker_company` | string (nullable) | what this deal recorded as the broker's inmobiliaria at the time of the deal |
| `broker_phone` | string | |
| `broker_pic` | int (0/1) | **PIC at the time of the deal** — was the broker classified as ICP when this deal was logged |
| `broker_perfil` | string | `PIC` / `NO PIC` (string version) |
| `broker_diy_landlord` | int (0/1) | |
| `broker_status` | string | `New Broker` / `Recurring Broker` |
| `broker_agent_name` | string | KAM assigned |
| `broker_agent_renewal_name` | string | renewal KAM |
| `broker_hs_owner` | string | HubSpot owner |
| `broker_hs_responsibe` | string | HubSpot secondary |
| `deal_agent_name` | string | KAM who handled this specific deal |
| `office_id` | int | numeric id |
| `office_name` | string | `CDMX` / `GDL` / `Queretaro` / `Tijuana` / etc. — **may or may not be the same as Plaza, please confirm** |
| `plaza` | string | **Plaza / sales territory — we currently can't see this anywhere in the MCP output. If `office_name` IS the Plaza, say so explicitly. If Plaza is something else (a finer or coarser grouping), please include it as a separate field.** |
| `kamolio` | string (nullable) | |
| `lawyer` | string (nullable) | |
| `tickets_total` | int (nullable) | support ticket count |
| `ticket_delay` | int | |
| `tenant_pendings_total` | int (nullable) | |
| `tenant_pendings_resolved` | int (nullable) | |
| `pipefy_id` | int (nullable) | external id |
| `pipefy_deleted` | int (0/1, nullable) | tombstone flag |
| `insurance_id` | int | |
| `insurance` | int (0/1) | |
| `days_to_end` | int (nullable) | days until end_date as of export — we can recompute, but include if cheap |
| `priority` | int | |
| `confirmed_at_renewal` | ISO datetime (nullable) | |

---

## 2. `brokers.jsonl` — full broker / asesor list

**Goal**: every broker who has ever transacted, with their canonical identity + commercial classification + assignment.

**Scale expectation**: probably 3,000–10,000 records (dashboard currently shows 3,530 brokers from deal data alone, MCP returns only 1,000).

**Required fields per broker**:

| field | type | notes |
|---|---|---|
| `id` | int | primary key |
| `name` | string | broker's full name (canonical) |
| `email` | string | |
| `phone` | string | |
| `created` | ISO datetime | when the broker signed up — **critical for tenure**, currently only available for the 1,000 brokers MCP returns |
| `from` | string | acquisition source (`Hubspot` / `BA` / etc.) |
| `pic` | int (0/1) | **broker-level PIC classification** — not just per-deal. The MCP currently only puts perfil on the organization, not the broker, for ~92% of brokers |
| `plaza` | string | **broker's assigned Plaza / sales territory** |
| `city` | string | currently mostly empty in the MCP response — needs to be populated |
| `state` | string | same |
| `organization_id` | int (nullable, 0 = independent) | FK to organizations table |
| `agent_id` | int | KAM assigned (`broker_agent_name` resolved) |
| `agent_renewal_id` | int | renewal KAM |
| `role_id` | int | what role this broker plays in their org (asesor, dueño, etc.) |
| `onboarding` | int (0/1) | onboarding flow completed |
| `renewal_participation` | nullable | currently mostly null — what is this field's expected domain? |
| `renewal_path` | string (nullable) | same |
| `last_login` | ISO datetime (nullable) | drives "is this account still alive in our product" |
| `diy_landlord` | int (0/1) | |
| `kam_considerations` | string | CS-team free-text notes — must be preserved when overwriting |
| `renewal_considerations` | string | same |

---

## 3. `organizations.jsonl` — full inmobiliaria / company list

**Goal**: every company a broker has ever been associated with, with commercial + assignment metadata.

**Scale expectation**: probably 1,500–5,000 records (dashboard currently shows 1,327 inmobiliarias).

**Required fields per organization**:

| field | type | notes |
|---|---|---|
| `id` | int | primary key |
| `name` | string | canonical company name |
| `slug` | string | external id |
| `created` | ISO datetime | when the org was first registered — drives org-level tenure |
| `perfil` | string | `PIC` / `NO PIC` — **today this is the source of broker PIC for the 8% of brokers whose org is populated. If broker.pic and organization.perfil can disagree, we need both** |
| `size` | int | broker count or property count? Please clarify which |
| `hs_size` | int | HubSpot-reported size |
| `hs_size_category` | string | |
| `hs_kam_assigned` | int | KAM owner id |
| `hs_acquisition_channel` | string | `Inbound` / `Outbound` / `Inbound (Outbound)` |
| `hs_company_size` | string | |
| `hs_deal_number` | string | |
| `hs_ticket_avg` | string | |
| `hs_listing_publish` | string | size proxy |
| `comission_agreement` | string | `WITHOUT_AGREEMENT` / `<plan>` / etc. |
| `plaza` | string | **organization-level Plaza, if it differs from per-broker Plaza** |
| `city` | string | |
| `state` | string | |
| `comercial_agent` | int | sales rep id |
| `pod_id` | int | internal grouping |
| `legal_agent` | int | |
| `litigation_agent` | int | |
| `kam_renewal_id` | int (nullable) | renewal KAM owner |
| `renewal_centralized` | int (0/1) | does this org route through centralized renewals |
| `feature_activated` | mixed (nullable) | what feature flags are on |
| `moral_person` | int (0/1) | is the org a corporate entity |
| `makes_commercial_invoice` | int (0/1) | |
| `tax_regime` | string | |
| `kam_considerations` | string | already in the MCP `organization` object — pass through |
| `renewal_considerations` | string | same |
| `legal_considerations` | string | same |
| `legal_signature_considerations` | string | same |
| `screening_considerations` | string | same |
| `comercial_status` | int | |

---

## 4. `nps.jsonl` — NPS survey responses

**Goal**: surface customer satisfaction in the dashboard alongside the
activity / revenue signals. Right now we have no NPS data plumbed in — broker
sentiment is inferred from second-order proxies (renewal rate, churn,
in-progress velocity). With actual NPS we can flag accounts whose activity
looks fine but whose sentiment is dropping, before they churn.

**Scope question**: who do you survey?
- Brokers (their view of MoradaUno-as-a-tool) — most useful for this dashboard
- Landlords (after a deal closes)
- Tenants (after screening or at lease end)

If you only survey one of these, send that. If you survey all three, send all
three — we'll start with broker NPS in the UI and add the others as we go.

**Required fields per response**:

| field | type | notes |
|---|---|---|
| `id` | int | response id |
| `respondent_type` | string | `broker` / `landlord` / `tenant` |
| `respondent_id` | int | FK into the corresponding entity table |
| `broker_id` | int (nullable) | even for landlord/tenant responses, attribute to the broker whose deal generated the survey — drives broker-level rollup |
| `organization_id` | int (nullable) | derived from broker_id, but include if cheap |
| `deal_id` | int (nullable) | if the survey is tied to a specific deal (post-screening, post-signing, post-renewal) |
| `survey_type` | string | `post_screening` / `post_signing` / `post_renewal` / `quarterly` / etc. — whatever taxonomy you use |
| `score` | int | 0–10 |
| `category` | string | `promoter` (9-10) / `passive` (7-8) / `detractor` (0-6) — we can derive but include if you already store it |
| `comment` | string (nullable) | free-text response; surfaced in the broker drawer |
| `created` | ISO datetime | when the response came in |
| `sent_at` | ISO datetime (nullable) | when the survey was sent, in case there's lag |
| `channel` | string (nullable) | how the survey was delivered (`email` / `whatsapp` / `in-app`) |

**Dashboard mental model**:

- **Broker worklist**: a small NPS pill column showing latest score, color-coded
  (green = promoter, gray = passive, red = detractor). Brokers without any NPS
  response show a neutral "no data" pill.
- **Broker drawer**: latest score + date + comment, plus a trend mini-chart of
  past 4–8 responses.
- **Inmobiliaria rollup**: org-level NPS = mean of latest scores across its
  brokers, with promoter/passive/detractor counts.
- **Risk signal extension** (future): an NPS-based ALTO trigger — e.g., last
  response is detractor AND it's been > 30 days since they transacted.

**Open questions on NPS**:
- Survey cadence: is there a fixed quarterly survey, or only event-triggered ones?
- Response-rate visibility: do you also store "survey sent but no response" so
  we can compute response rates? Not strictly needed but useful context.
- Pre-2026 history: how far back does your NPS data go? If shallow, that's OK
  — we'll just label brokers with no historical NPS as "no data".

---

## 5. `plazas.jsonl` (or just a definition note) — Plaza catalog

**Open question**: what exactly is Plaza?
- Is it the same as `office_name` (CDMX / GDL / Queretaro / Tijuana / etc.)?
- Is it a finer subdivision (e.g., CDMX has multiple Plazas)?
- Is it a CRM-level field that's defined per-broker but not per-deal?
- Who owns the source of truth — Hubspot, the admin DB, manual assignment?

Please send a one-page note explaining the Plaza model, plus a JSONL listing the canonical Plazas (id, name, region/state). If Plaza === office, just say so and we'll alias the existing `office_name` field.

---

## What the dashboard does with all of this — quick mental model

So engineering can sanity-check that we're not missing anything:

- **Per-broker output**: groups deals by `broker_name`, computes `tenure_months` (from `broker.created` if present, else first observed deal), counts deals in time windows (`deals_30d`, `_90d`, `_90_180`, `_180_365`, `_365_plus`, `_l6m`, `_prior_6m`), computes per-product mix, computes per-quarter revenue series (last 8 Q), computes renewal-rate-copita (past-grace contracts with a child Renewal/Closed/Cancelled deal via `parent_id`) and renewal-rate-cierre (this broker's own Renewal deals that reached `pipefy_phase_name=Completed`), and rolls up margin via a tiered `value × is_renewal × product_name` model.
- **Per-inmobiliaria output**: rolls up the brokers in each `org_id`/`broker_company` group, summing volume / revenue / margin and aggregating risk/lifecycle distributions.
- **Display dimensions**: PIC, Plaza, segment (Inmobiliaria vs Independiente), risk (ALTO/MEDIO/BAJO), lifecycle stage, KAM assignment.

The trend / lifecycle / risk computations all depend on having complete deal coverage going back at least 360 days. That's the part that's currently broken because of the 10K MCP cap.

---

## Open questions for the CTO

1. **Plaza source-of-truth** — see §4 above.
2. **PIC granularity** — is the broker the unit of PIC classification, or the organization, or both with possible disagreement? The dashboard currently shows PIC per-broker.
3. **`parent_id` after Completed** — please confirm whether the export can preserve `parent_id` on Completed Renewals (the MCP clears it; we work around it with a fragile address+tenant heuristic and would love to drop that).
4. **`broker_id` on the deal record** — confirm this can be added; currently we match by `broker_name` string which is fragile.
5. **One-shot vs ongoing access** — best case is a one-time export now + admin REST API access from a script that runs in a cron (the MCP-via-Claude path is fine for now but eats Claude usage). The memory note from the May-11 CTO chat says the direct admin REST API exists — please grant access with a service-account credential we can store in CI / a Cloud Run job.
6. **Volume estimate** — roughly how many deals total are in the system? That tells us file size to expect.
7. **Refresh cadence after the one-shot** — if we get direct admin REST access, we can re-pull the whole dataset nightly. If we stay on MCP, we can only delta the last ~80 days. Preference?

---

## Where to drop the files when ready

- `cs-data/imports/deals.jsonl`
- `cs-data/imports/brokers.jsonl`
- `cs-data/imports/organizations.jsonl`
- `cs-data/imports/nps.jsonl`
- `cs-data/imports/plazas.jsonl` (or a `PLAZA_NOTE.md`)

The repo is at `/Users/inesgamboa/Desktop/cs-partner-dashboard`. Anything in `cs-data/imports/` will be merged into the production pipeline files by a tiny adapter script (`cs-data/import_full_history.py`, to be written once the spec is locked).

If a single export is too big to ship as one file, splitting by year (`deals_2021.jsonl`, `deals_2022.jsonl`, etc.) is fine — the adapter will concatenate.
