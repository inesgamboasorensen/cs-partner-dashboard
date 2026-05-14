"""
One-time adapter: merge BI historical CSV export into deals_2y.jsonl.gz.

CSV-wins for the ids it contains (richer fields: cost_percent + revenue +
broker_id at top level). Current jsonl wins for ids newer than CSV cutoff
(post-export nightly deltas). For overlapping ids, preserves the few
fields that exist only in current jsonl (promo, renewal_pipefy_phase_name).

Usage:
  python3 cs-data/import_bi_history.py cs-data/imports/<csv>
"""
import csv, gzip, json, os, sys, shutil
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DEALS = os.path.join(HERE, 'deals_2y.jsonl.gz')

DATE_FIELDS = {'created','end_date','date_signature','confirmed_at_renewal'}

INT_FIELDS = {
    'id','parent_id','renewal_available','product','sub_product','broker_id',
    'broker_diy_landlord','broker_pic','office_id','tickets_total','ticket_delay',
    'tenant_pendings_total','tenant_pendings_resolved','pipefy_id','pipefy_deleted',
    'insurance_id','insurance','days_to_end','priority',
}
FLOAT_FIELDS = {'value','cost_percent','revenue'}
PRESERVE_FROM_CURRENT = ['promo','renewal_pipefy_phase_name']

def coerce(field, raw):
    if raw is None: return None
    s = raw.strip()
    if s == '': return None
    if field in INT_FIELDS:
        try: return int(s)
        except ValueError:
            try: return int(float(s))
            except ValueError: return None
    if field in FLOAT_FIELDS:
        try: return float(s)
        except ValueError: return None
    if field in DATE_FIELDS:
        try:
            return datetime.strptime(s, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            return s
    return s

def load_csv(path):
    out = {}
    with open(path, newline='') as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                did = int(row['id'])
            except (ValueError, TypeError, KeyError):
                continue
            rec = {k: coerce(k, v) for k, v in row.items()}
            out[did] = rec
    return out

def load_jsonl(path):
    out = {}
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            d = json.loads(line)
            out[d['id']] = d
    return out

def main():
    if len(sys.argv) < 2:
        print('usage: import_bi_history.py <csv path>'); sys.exit(2)
    csv_path = sys.argv[1]
    print(f'reading CSV: {csv_path}')
    csv_recs = load_csv(csv_path)
    print(f'  {len(csv_recs):,} rows')
    print(f'reading current: {DEALS}')
    cur = load_jsonl(DEALS)
    print(f'  {len(cur):,} records')

    # Backup current
    bak = DEALS + '.pre_bi_import.bak'
    shutil.copy(DEALS, bak)
    print(f'backup: {bak}')

    merged = {}
    only_in_current = 0
    overlap = 0
    only_in_csv = 0
    for did, rec in csv_recs.items():
        if did in cur:
            overlap += 1
            for k in PRESERVE_FROM_CURRENT:
                v = cur[did].get(k)
                if v not in (None, '', 0):
                    rec[k] = v
        else:
            only_in_csv += 1
        merged[did] = rec
    for did, rec in cur.items():
        if did not in csv_recs:
            only_in_current += 1
            merged[did] = rec

    print(f'merged: overlap={overlap:,} only_in_csv={only_in_csv:,} only_in_current={only_in_current:,}')
    print(f'total: {len(merged):,}')

    opener = gzip.open if DEALS.endswith('.gz') else open
    with opener(DEALS, 'wt') as f:
        for did in sorted(merged.keys(), reverse=True):
            f.write(json.dumps(merged[did], ensure_ascii=False) + '\n')
    print(f'wrote {DEALS} ({os.path.getsize(DEALS):,} bytes)')

if __name__ == '__main__':
    main()
