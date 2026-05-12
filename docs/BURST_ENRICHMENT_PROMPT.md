# Burst-enrichment session prompt

Self-contained instructions for a fresh Claude session to enrich the
remaining Closed+Renewal deals in `cs-partner-dashboard/cs-data/deals_2y.jsonl`
that don't yet have a populated `cost_percent` field.

Paste this whole block into the scheduled Claude session.

---

You are running a one-shot burst-enrichment job for cs-partner-dashboard.
Memory should explain the context (look up `project_cs_partner_dashboard` and
`project_cs_dashboard_open_questions`). Read it if you need orientation.

## What you're doing

`cs-data/deals_2y.jsonl` has ~12,000 deal records. About 7,500 Closed/Renewal
deals lack accurate `cost_percent` and `revenue` fields. The pipeline
(`process.py`) falls back to an estimated rate when those are missing — we want
real numbers from `admin_deal_detail` instead. We enriched the newest 561 in a
prior session; everything older still needs enrichment.

## Steps

### 1. Set up

```bash
cd /Users/inesgamboa/Desktop/cs-partner-dashboard
git remote -v   # MUST show inesgamboasorensen/cs-partner-dashboard, NOT m1-csai-tracker
git status      # should be clean
```

### 2. Build the work list (newest unenriched first)

```bash
python3 - <<'EOF'
import json
ids = []
with open('cs-data/deals_2y.jsonl') as f:
    for line in f:
        d = json.loads(line.strip()) if line.strip() else None
        if not d: continue
        if d.get('deal_type') not in ('Closed','Renewal'): continue
        cp = d.get('cost_percent')
        if cp and float(cp) > 0: continue
        ids.append(d['id'])
ids.sort(reverse=True)
with open('cs-data/_enrichment_todo.txt','w') as f:
    f.write('\n'.join(str(x) for x in ids) + '\n')
print(f'pending: {len(ids):,}')
EOF
```

### 3. Split into 8 chunks of ~500

```bash
N=$(wc -l < cs-data/_enrichment_todo.txt)
CHUNK=$(( (N + 7) / 8 ))    # round up so 8 chunks cover everything
echo "splitting $N IDs into 8 chunks of $CHUNK"
for i in 1 2 3 4 5 6 7 8; do
  START=$(( (i-1)*CHUNK + 1 ))
  END=$((  i*CHUNK ))
  sed -n "${START},${END}p" cs-data/_enrichment_todo.txt > cs-data/_enrich_chunk_$i.txt
done
wc -l cs-data/_enrich_chunk_*.txt
```

### 4. Spawn 8 parallel sub-agents (single message, 8 Agent calls)

Use `general-purpose` subagent type. Same prompt template for each — only the
chunk file path and the output file path differ:

```
Enrich MoradaUno deals via the MCP admin_deal_detail tool.

Your chunk:  /Users/inesgamboa/Desktop/cs-partner-dashboard/cs-data/_enrich_chunk_{i}.txt
Output:      /Users/inesgamboa/Desktop/cs-partner-dashboard/cs-data/_enrichment_part_{i}.jsonl

The tool's schema is deferred. First call ToolSearch with query
`select:mcp__c1bdb2c1-1018-41fc-a6b7-0900cc38b92f__admin_deal_detail` to load it.
Tool takes one parameter `id` (number). Response.data.steps[0].data contains
`cost_percent` (number) and `revenue` (number) — extract those.

For each ID in your chunk: call admin_deal_detail(id). Extract cost_percent
and revenue from data.steps[0].data. If steps is missing/empty or steps[0].data
is missing, skip and continue.

Output: append one JSONL line per enriched deal:
  {"id":<id>,"cost_percent":<cp>,"revenue":<rev>}

Efficiency: fire 15-20 admin_deal_detail calls in parallel per message,
extract and Bash-append to disk immediately, then move to next batch.
Do NOT accumulate all responses inline. Don't load any tool schemas
you don't need.

Guardrails: do NOT run process.py, build_dashboard.py, git add, git commit,
or git push. Do NOT modify any other files. Just write the output JSONL.

When you finish or hit a usage limit: report in under 80 words — count
enriched, count remaining unprocessed in your chunk, output file size.
```

Run all 8 agents with `run_in_background: true`. Set a chapter mark
("Burst enrichment") so it's easy to find in the transcript later.

### 5. Wait for completion notifications

You'll get one notification per agent. Don't poll or read the agent task
files. When all 8 are done, proceed to step 6 even if some hit usage limits
— the merge step is idempotent.

### 6. Apply, process, build, commit, push

```bash
cd /Users/inesgamboa/Desktop/cs-partner-dashboard
python3 cs-data/apply_enrichment.py
python3 cs-data/process.py
cp cs-data/dashboard_data.json cs-data/dashboard_data_embed.json
python3 cs-data/build_dashboard.py
cp cs-dashboard.html index.html

# clean up transient files (already gitignored, but tidy)
rm -f cs-data/_enrichment_part_*.jsonl cs-data/_enrich_chunk_*.txt cs-data/_enrichment_todo.txt

git status --short  # sanity
git add cs-data/deals_2y.jsonl cs-dashboard.html index.html
# broker_registry may or may not have changed — only add if it did
git diff --cached --quiet cs-data/broker_registry.json || git add cs-data/broker_registry.json

git commit -m "$(cat <<COMMIT
Burst-enrich N more deals with cost_percent + revenue

[fill N with the actual count from process.py output]

Closes the bulk of the enrichment backlog from the prior partial run.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
COMMIT
)"

git push origin main
```

### 7. Report back

One short paragraph: how many enriched, the new revenue_quality breakdown
(`actual` / `partial` / `estimated`), commit hash, and how many deals
remain unenriched if any. Then end the session.

## Guardrails for the whole burst session

- Verify the remote points at `cs-partner-dashboard` before any push.
- Never push --force. Never amend prior commits.
- If the merge step shows zero new enrichments, skip the commit (no-op guard).
- If something fails halfway, the partial enrichment files on disk are still
  good and will get picked up on the next run — don't roll anything back.
