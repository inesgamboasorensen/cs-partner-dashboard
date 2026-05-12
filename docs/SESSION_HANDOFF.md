# Session handoff — cs-partner-dashboard

Last updated: 2026-05-12. Read this first to resume work without re-deriving context.

## Where the work lives

- **Working repo**: `/Users/inesgamboa/Desktop/cs-partner-dashboard` (this one)
- **Stale repo, DO NOT push there**: `m1-csai-tracker`. Always `git remote -v` before any push.
- **Live site**: https://inesgamboasorensen.github.io/cs-partner-dashboard/ (GitHub Pages, auto-redeploys on push to `main`)

## What's shipped (recent commits)

| Commit | What |
|---|---|
| `a4a5027` | Dashboard header gets a 🔄 Refrescar datos button + copy-command modal. Click → modal with paste-this one-liner for a Claude Code session that does the actual refresh |
| `2a8e206` | Moved durable docs/scripts out of gitignored `cs-data/_staging/` into tracked paths (`docs/`, `cs-data/apply_enrichment.py`) so a remote Claude session can find them |
| `d611724` | Round 3: Desempeño por dueño table rebuilt with Lifecycle distribution mini-bar + directional Productividad % + Methodology copy |
| `cf87df0` | Round 2: manual risk override (drawer dropdown), activity cooldown (30d demote, 180d high-value re-promote), context-aware Urgente tooltip (rescate vs valor) |
| `2f8d6ba` | Round 1: Estable→En Mantenimiento, Vol histórico = Closed+Renewal, drawer accordions closed, Eventos inmo column gone |
| `ea7cbda` | Partial cost_percent enrichment of newest 561 deals |
| `52d1676` | `cs-data/mcp_pull.py` — reusable MCP → pipeline merger |
| `3abccb2` | Full MCP backfill — dataset jumped from 3,939 → 12,365 deals |

## Pipeline shape

```
Claude session (manual or scheduled)
  → MCP admin_deals + get_brokers
  → cs-data/_nightly_raw/ (gitignored)
  → python3 cs-data/mcp_pull.py cs-data/_nightly_raw/
  → python3 cs-data/process.py
  → python3 cs-data/build_dashboard.py
  → git commit + push to main
  → GitHub Pages redeploys (~2 min)
```

Optional enrichment pass: `admin_deal_detail` per Closed/Renewal deal lacking `cost_percent` → `python3 cs-data/apply_enrichment.py`.

## Open work (in priority order)

1. **Verify MCP works in CI with API key** (~5 min). Write a tiny GitHub Actions workflow that runs `claude --print "list mcp servers"` with `ANTHROPIC_API_KEY`. If MoradaUno appears → can build full CF Worker + GH Action button (~1.5h) and replace the modal-paste UX with one-click refresh. If not → modal-paste is the permanent flow.
2. **Drain enrichment backlog**. ~6,991 Closed/Renewal deals still lacking `cost_percent`. See `docs/BURST_ENRICHMENT_PROMPT.md` for the bursting recipe. Each burst hits the daily Claude.ai quota wall after ~500–1,200 deals. Resets at 8:30pm America/Mexico_City.
3. **Import BI historical data when it arrives**. Spec at `docs/DATA_REQUEST_SPEC.md`, fillable template at `docs/data_request_template.xlsx`. BI will only deliver historical (one-shot) — ongoing refresh stays MCP-driven. Need to write `cs-data/import_full_history.py` to merge BI's `cs-data/imports/*.jsonl` into the existing pipeline files.

## Constraints to remember

- **MCP-only access** to MoradaUno. CTO will not grant admin REST API access. Every refresh requires a Claude session.
- **MCP returns last ~10,000 deals** (cap). Older history requires the BI one-shot.
- **Completeness bias**: trend/lifecycle/risk skewed upward for brokers active before Feb 2026 (incomplete prior-window). Activity, margin, and recent-activity metrics are trustworthy.
- **Vol histórico** = `realized_deals_count` = `closed_count + renewal_count` (NOT all deals). Don't revert.
- **Estable** is renamed to **En Mantenimiento** everywhere user-facing. A v6 localStorage migration handles legacy values; don't break it.
- **`<details class="sec">` drawer accordions start closed** by default. Don't add `open` back.
- **`docs/NIGHTLY_ROUTINE_PROMPT.md`** is the runbook a fresh Claude session uses for refresh.

## How to resume

Open Claude Code in this repo and paste:

```
Read docs/SESSION_HANDOFF.md to get context, then ask me what to tackle next.
```

Or, to just trigger a data refresh:

```
Read docs/NIGHTLY_ROUTINE_PROMPT.md and execute it.
```
