# CS Partner Dashboard — MoradaUno

Interactive CS Strategic Allocation dashboard for MoradaUno. Helps 2 Customer Success Managers prioritize 1,520 hrs of operational capacity across ~1,867 brokers from 836 inmobiliarias.

## Open the dashboard

Open `cs-dashboard.html` in any browser. Self-contained — no server, no build step, no dependencies.

## Pipeline to rebuild

The dashboard is generated from MoradaUno MCP data + pricing rules. Steps:

```bash
cd cs-data
python3 process.py              # reads deals_2y.jsonl, writes dashboard_data.json
python3 -c "import json; d=json.load(open('dashboard_data.json')); json.dump(d, open('dashboard_data_embed.json','w'), separators=(',',':'))"
python3 build_dashboard.py      # embeds JSON into template → ../cs-dashboard.html
```

## Repo structure

```
cs-partner-dashboard/
├── cs-dashboard.html            ← main deliverable (open this)
├── CS_DASHBOARD.md              ← product notes
└── cs-data/
    ├── process.py               ← main data processor (risk, lifecycle, renewals, revenue)
    ├── build_dashboard.py       ← injects JSON into template
    ├── dashboard_template.html  ← HTML shell with chart fns and styles
    ├── pricing.json             ← base rates per sub-product (M3 30%, M6 40%, etc.)
    ├── broker_registry.json     ← canonical source for broker.created (tenure anchor)
    ├── deal_rates.json          ← enriched cost_percent/revenue from get_deal_detail
    ├── deals_2y.jsonl           ← 2-year deals snapshot (trimmed schema)
    └── raw_deals.jsonl          ← full raw MCP payload (superset of deals_2y)
```

## Key concepts

- **Revenue L12M** = `Σ value × cost_percent` for non-Cancelled deals in last 365 days. `cost_percent` is the final applied rate (already IVA-inclusive).
- **Tasa de copita** = % of past-grace Guarantee contracts with a child deal (Closed/Renewal/Cancelled) with `parent_id` pointing back. Measures retention.
- **Tasa de cierre** = % of broker's own Renewal deals that reached `pipefy_phase_name='Completed'`. Measures funnel execution.
- **Risk model** (3 signals, ALTO/MEDIO tiers):
  - Churn por inactividad (days_since_last > 180 / > 60)
  - Caída de uso severa (trend_pct ≤ -50% / ≤ -20%)
  - Renovación baja (copita < 30% / < 50%, 3+ past-grace required)
- **Lifecycle**: Onboarding, Growing, Mature-Healthy, Active-Low, Declining, At-Risk, Churned

Full documentation in the 📖 Metodología tab of the dashboard.

## Data flow

1. MoradaUno MCP `admin_deals search_string=<name>` returns broker's full history
2. Merged into `deals_2y.jsonl` (trimmed to 2-year sliding window)
3. `process.py` computes per-broker + per-inmobiliaria metrics
4. `build_dashboard.py` injects the embedded JSON into the template

## Known gaps

- Most brokers have ~170 deals missing from pre-2024 (outside 2Y window) — daily task progressively backfills
- MoradaUno's API clears `parent_id=0` when Renewal reaches `Completed`, breaking lineage. Tenant+address fallback recovers what's in-window.

## Commit conventions

Descriptive messages explaining *why* the change was made, not just *what*.
