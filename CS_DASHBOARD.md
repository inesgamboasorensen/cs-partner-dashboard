# CS Strategic Allocation Dashboard

Internal tool for MoradaUno Customer Success. Tracks broker lifecycle, revenue health, and churn risk across the active broker portfolio.

---

## Overview

The dashboard gives CS a daily-refreshed view of every broker and inmobiliaria that has closed deals in the last 2 years. It classifies each broker by **lifecycle stage** and **risk level**, and surfaces actionable renewal and churn signals.

**Live file:** `cs-dashboard.html` (project root) — open locally in Chrome or deploy as a static page.

---

## File Structure

```
Apps - Claude/
├── cs-dashboard.html             ← Built output (open in browser)
├── CS_DASHBOARD.md               ← This file
└── cs-data/
    ├── raw_deals.jsonl           ← All ingested deals (append-only, deduplicated on rebuild)
    ├── deals_2y.jsonl            ← Pruned to 2-year rolling window (rebuilt each run)
    ├── broker_registry.json      ← Canonical broker metadata from get_brokers API
    ├── deal_rates.json           ← Per-deal enriched cost_percent from get_deal_detail API
    ├── pricing.json              ← Base rates (without IVA) by product/sub-product
    ├── process.py                ← Reads deals_2y.jsonl → dashboard_data.json
    ├── build_dashboard.py        ← Embeds dashboard_data_embed.json into template → cs-dashboard.html
    ├── dashboard_template.html   ← HTML/JS template (source of truth for UI)
    ├── dashboard_data.json       ← Full processed dataset (not embedded)
    └── dashboard_data_embed.json ← Filtered/minified version for embedding
```

---

## Data Pipeline

Each daily run executes these steps in order:

### A — Pull newest deals
- Reads max deal ID from `deals_2y.jsonl`
- Pages through `admin_deals` (show: 100) until it reaches already-seen IDs (max 20 pages)
- Appends new deals to `raw_deals.jsonl` (excluding "MoradaUno - Atención Directa")

### B — Per-broker backfill (fixes false churn)
- Targets up to 40 brokers classified as `Churned` or `At-Risk`, or `ALTO` risk with few deals
- Skips brokers with `last_deal_observed > today − 60d`
- Calls `admin_deals` with `search_string: <broker_name>` to fill gaps in activity history
- Appends results to `raw_deals.jsonl`

### C — Registry enrichment (canonical tenure)
- Targets up to 20 brokers with `tenure_source: 'sampled'` and high revenue or ALTO risk
- Calls `get_brokers` with broker name filter
- Merges canonical fields (created date, org info, KAM notes, renewal path) into `broker_registry.json`

### D — Deal revenue enrichment
- Targets up to 30 deals without an exact `cost_percent`, prioritized by recency + broker importance
- Calls `get_deal_detail` per deal
- Stores `{value, cost_percent, revenue}` in `deal_rates.json` keyed by deal ID
- These enriched rates override the pricing table estimates in `process.py`

### E — Rebuild dataset
```bash
# Deduplicate + merge enriched rates + 2-year prune
python3 -c "<inline script from SKILL.md>"
```

### F — Reprocess + embed + build
```bash
cd cs-data && python3 process.py
# Filter to meaningful brokers/inmobiliarias
python3 -c "<inline filter script>"
cd cs-data && python3 build_dashboard.py
# Output: cs-dashboard.html
```

---

## Revenue Model

**Pago Total (client pays) = rent × base_rate × (1 + IVA)**
**Net revenue to MoradaUno = rent × base_rate** (without IVA)

`IVA = 16%`

Base rates are defined in `pricing.json`. Key rates:

| Product | Sub-product | Base rate |
|---------|-------------|-----------|
| Guarantee | M3 | 30% |
| Guarantee | M3 Light | 25% |
| Guarantee | M6 | 40% |
| Guarantee | M12 | 60% |
| Guarantee | M Legal | 25% |
| Screening | Normal | 7.7% |
| Screening | With RPP | 8.5% |
| Legal | Contracts | 3% |

When a deal has a real `cost_percent` in `deal_rates.json`, it takes priority over the table.

---

## Broker Classification

### Lifecycle Stages

| Stage | Condition |
|-------|-----------|
| **Churned** | Last deal > 180 days ago |
| **At-Risk** | Last deal 90–180 days ago |
| **Onboarding** | Tenure < 3 months |
| **Growing** | Tenure < 12 months, ≥2 deals in last 90d |
| **Mature-Healthy** | Tenure ≥ 12 months, trend ≥ 0%, ≥2 deals in 90d |
| **Declining** | Tenure ≥ 12 months, trend ≤ −20% |
| **Dormant** | Active but 0 deals in 90d |
| **Active-Low** | Everything else |

### Risk Levels

Risk is computed from signals and returns `ALTO`, `MEDIO`, or `BAJO`.

Key signals (in priority order):
- **ALTO**: Churned (>180d inactive, ≥2 total deals)
- **ALTO**: No activity for >90d
- **ALTO**: Usage dropped ≥50% (trend_pct)
- **ALTO**: Renewal rate <30% (with ≥3 expired sample)
- **ALTO**: Contracts expired without renewal in last 30d (grace period)
- **ALTO**: Cancel rate ≥50%
- **MEDIO**: Last activity 61–90d ago
- **MEDIO**: Usage dropped 20–49%
- **MEDIO**: Renewal rate 30–49%
- **MEDIO**: ≥3 contracts in renewal window

### Renewal Model

Only **Guarantee** contracts renew. The renewal window logic:
- **In-window**: 0–60 days to expiry, not yet renewed → actionable
- **Grace period**: expired in last 30 days, not renewed → still catchable
- **Upcoming**: 60–180 days to expiry → plan ahead

`renewal_rate = renewed / (renewed + lost)` — only counted once enough expired contracts exist.

---

## Broker Registry Fields

Stored in `broker_registry.json`, keyed by broker name:

| Field | Source |
|-------|--------|
| id, name, phone, email | get_brokers API |
| created | Canonical tenure start date |
| renewal_participation, renewal_path | get_brokers API |
| onboarding, last_login | get_brokers API |
| org_id, org_name, org_created | Organization metadata |
| org_perfil, org_size, org_comission | Organization profile |
| kam_considerations | KAM notes |
| renewal_considerations | Renewal notes |

`tenure_source` is set to `'registry'` when enriched from this file, vs `'sampled'` (derived from observed deal dates) when not.

---

## Scheduled Task

The daily refresh runs automatically via Claude scheduled task:
**`cs-dashboard-daily-refresh`** — runs each morning.

Task definition: `~/.claude/scheduled-tasks/cs-dashboard-daily-refresh/SKILL.md`

To run manually:
1. Open Claude Code
2. Run the scheduled task, or execute the pipeline steps in order (A → F above)
3. Open `cs-dashboard.html` in Chrome

---

## Dashboard Filters (embed)

Before embedding, the dataset is filtered to reduce file size:
- Brokers: `deals_total ≥ 2` OR `total_revenue > 10,000` OR `in_progress_count > 0`
- Inmobiliarias: `total_revenue > 0`

The full unfiltered data remains in `dashboard_data.json`.

---

## Current Stats (as of last run)

| Metric | Value |
|--------|-------|
| Total deals (2y) | ~3,576 |
| Total brokers | 1,867 |
| Total inmobiliarias | 836 |
| Revenue total | ~$23.6M MXN |
| Revenue at risk (ALTO) | ~$7.4M MXN |
| ALTO risk brokers | 654 |
| MEDIO risk brokers | 452 |
| Churned brokers | 586 |
| At-Risk brokers | 187 |
