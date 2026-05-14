# Nightly refresh prompt — cs-partner-dashboard

Self-contained instructions for a fresh Claude session to refresh the
dashboard with the latest MCP delta and ship the result.

Paste this whole block into the scheduled Claude session (or run as a
one-liner pointing here: `Read docs/NIGHTLY_ROUTINE_PROMPT.md
and execute it.`).

---

## What you're doing

Refresh the CS Partner Dashboard with whatever new deals + broker state
appeared since the last run. The dashboard lives at
https://inesgamboasorensen.github.io/cs-partner-dashboard/ and is fed by
`cs-data/deals_2y.jsonl.gz` + `cs-data/broker_registry.json` in the same
repo. The pipeline is already built — your job is just to pull the delta,
re-merge, re-process, re-build the HTML, and commit + push.

Most nights the delta will be small (a handful of new deals + state
changes on existing ones). Some nights nothing changes and the commit
step gets skipped.

## Steps

### 1. Set up + safety check

```bash
cd /Users/inesgamboa/Desktop/cs-partner-dashboard
git remote -v   # MUST show inesgamboasorensen/cs-partner-dashboard, NOT m1-csai-tracker
git status      # should be clean — if not, stop and ping Inés
mkdir -p cs-data/_nightly_raw
rm -f cs-data/_nightly_raw/*
```

If `git remote` shows m1-csai-tracker, STOP. That's the stale repo. Do
not push. Report to Inés.

### 2. Pull MCP delta

Use `ToolSearch` to load these two MCP tools, then call them:

```
select:mcp__c1bdb2c1-1018-41fc-a6b7-0900cc38b92f__admin_deals,mcp__c1bdb2c1-1018-41fc-a6b7-0900cc38b92f__get_brokers
```

#### 2a. Brokers

```
get_brokers()  → save the inner `data` JSON to cs-data/_nightly_raw/get_brokers.json
```

#### 2b. Deals — paginate until caught up

Call `admin_deals(page=N, show=200)` starting at N=1. After each page:

1. Save the inner `data` JSON to `cs-data/_nightly_raw/admin_deals_page{N}.json`
2. Compare the deal IDs on that page against `cs-data/deals_2y.jsonl.gz`.
   If **all 200 IDs are already in the file**, you've caught up — stop.
3. Otherwise, increment N and call again.

**Safety stop**: cap at 5 pages (1000 deals). If you haven't caught up by
page 5, something is wrong — log a warning in the commit message and
proceed anyway. Inés will investigate.

Most nights pages 1–2 are enough.

### 3. Merge + process + build

```bash
cd /Users/inesgamboa/Desktop/cs-partner-dashboard
python3 cs-data/mcp_pull.py cs-data/_nightly_raw/
python3 cs-data/process.py
cp cs-data/dashboard_data.json cs-data/dashboard_data_embed.json
python3 cs-data/build_dashboard.py
cp cs-dashboard.html index.html
rm -rf cs-data/_nightly_raw/
```

### 4. Opportunistic enrichment — drain the backlog

If there's still a backlog of Closed/Renewal deals without `cost_percent`
(see `docs/BURST_ENRICHMENT_PROMPT.md` for the full
explanation), enrich up to 100 of them this run. Newest-first.

```bash
python3 - <<'PY'
import gzip, json
ids = []
with gzip.open('cs-data/deals_2y.jsonl.gz', 'rt') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        d = json.loads(line)
        if d.get('deal_type') not in ('Closed','Renewal'): continue
        cp = d.get('cost_percent')
        if cp and float(cp) > 0: continue
        ids.append(d['id'])
ids.sort(reverse=True)
ids = ids[:100]
with open('cs-data/_enrichment_pending.txt','w') as f:
    f.write('\n'.join(str(x) for x in ids))
print(f'will enrich {len(ids)} of newest Closed/Renewal deals')
PY
```

For each id in `cs-data/_enrichment_pending.txt`, call
`admin_deal_detail(id)` and append one JSONL line per result to
`cs-data/_enrichment_more.jsonl`:

```
{"id":<id>,"cost_percent":<steps[0].data.cost_percent>,"revenue":<steps[0].data.revenue>}
```

Fire 15–20 calls per message in parallel, extract values, and Bash-append
to the file. Don't let unparsed responses accumulate in your context.

After the enrichment calls finish:

```bash
python3 cs-data/apply_enrichment.py
python3 cs-data/process.py
cp cs-data/dashboard_data.json cs-data/dashboard_data_embed.json
python3 cs-data/build_dashboard.py
cp cs-dashboard.html index.html
rm -f cs-data/_enrichment_pending.txt
```

If a usage limit hits mid-enrichment, the partial file still has value —
the apply step is idempotent and re-runs only add what's new.

### 5. Commit + push (conditional)

```bash
cd /Users/inesgamboa/Desktop/cs-partner-dashboard
git add cs-data/deals_2y.jsonl.gz cs-data/broker_registry.json cs-dashboard.html index.html

# Skip commit if nothing changed (idempotent no-op guard)
if git diff --cached --quiet; then
  echo "no changes — skipping commit"
  exit 0
fi

# Capture some stats for the commit message
NEW_DEALS=$(python3 -c "
import json, subprocess
diff = subprocess.run(['git','diff','--cached','--stat','cs-data/deals_2y.jsonl.gz'], capture_output=True, text=True).stdout
print(diff.strip().split()[-3] if diff else '?')")

git commit -m "$(cat <<COMMIT
Nightly MCP refresh — $(date '+%Y-%m-%d')

[Summarise what changed: # deals merged, # brokers refreshed, # deals
enriched if applicable. Keep to 3 lines max.]

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
COMMIT
)"

git push origin main
```

### 6. Report back

One short paragraph: # new deals, # state changes on existing, # enriched
this run (if any), backlog remaining. Then end the session.

## Guardrails

- Verify the remote points at `cs-partner-dashboard` before any push.
- Never push --force. Never amend prior commits.
- If the merge step shows zero new deals AND zero enrichments, skip the
  commit (covered by the `git diff --cached --quiet` guard above).
- If `git diff` shows changes you don't recognise (e.g. unrelated files),
  STOP. Report to Inés instead of pushing.
- Don't touch anything under `cs-data/imports/` — that's where the BI
  team's historical export will live once delivered. The pipeline will be
  updated separately to merge it in.

## What this does NOT do

- Does not run the BI historical import (`cs-data/imports/` files) — that
  needs a one-time adapter, written when BI delivers.
- Does not call `/schedule`, set up cron, or modify settings.
- Does not log activities or value_events (those are user-driven via the
  dashboard UI).
