"""
Apply enrichment (cost_percent + revenue) onto cs-data/deals_2y.jsonl.

Reads enrichment JSONL files (one {"id":..., "cost_percent":..., "revenue":...}
per line) and merges them into the production deals_2y.jsonl in place.

Inputs (all that exist are merged):
  cs-data/_enrichment.jsonl
  cs-data/_enrichment_part_*.jsonl
  cs-data/_enrichment_more*.jsonl   (future incremental runs)

Output:
  cs-data/deals_2y.jsonl              (rewritten with cost_percent + revenue
                                       set on matching deal records)

Idempotent — running twice doesn't double-apply.

Usage:
  python3 cs-data/apply_enrichment.py
"""
import json, os, glob

# This script lives at cs-data/apply_enrichment.py, so HERE is cs-data/.
HERE = os.path.dirname(os.path.abspath(__file__))
CSDATA = HERE
DEALS = os.path.join(CSDATA, 'deals_2y.jsonl')

def collect_enrichment():
    by_id = {}
    patterns = [
        '_enrichment.jsonl',
        '_enrichment_part_*.jsonl',
        '_enrichment_more*.jsonl',
    ]
    for pat in patterns:
        for path in sorted(glob.glob(os.path.join(CSDATA, pat))):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    did = e.get('id')
                    if did is None: continue
                    by_id[did] = {'cost_percent': e.get('cost_percent'),
                                  'revenue': e.get('revenue')}
    return by_id

def main():
    enrichment = collect_enrichment()
    print(f'collected enrichment for {len(enrichment):,} deal IDs')

    deals = []
    with open(DEALS) as f:
        for line in f:
            line = line.strip()
            if line: deals.append(json.loads(line))
    print(f'loaded {len(deals):,} deals from production')

    applied = 0
    for d in deals:
        e = enrichment.get(d['id'])
        if e and e.get('cost_percent') is not None:
            d['cost_percent'] = e['cost_percent']
            if e.get('revenue') is not None:
                d['revenue'] = e['revenue']
            applied += 1

    with open(DEALS, 'w') as f:
        for d in sorted(deals, key=lambda x: -x['id']):
            f.write(json.dumps(d, ensure_ascii=False) + '\n')

    print(f'applied enrichment to {applied:,} deals → {DEALS}')
    print(f'size: {os.path.getsize(DEALS):,} bytes')

if __name__ == '__main__':
    main()
