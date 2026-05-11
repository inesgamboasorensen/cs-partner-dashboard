#!/usr/bin/env python3
"""
MCP → pipeline-shape merger for the nightly refresh.

Reads raw MCP responses (admin_deals + get_brokers) from an input directory,
merges with the current production files in cs-data/, and writes back.

Usage:
    python3 cs-data/mcp_pull.py <input_dir>

The input directory must contain:
  - One or more files matching admin_deals*.json — each holds the FULL MCP
    response payload (`{result, code, data: {page, show, deals: [...], ...}}`)
    or just the inner `{page, show, deals: [...]}`. The script accepts both.
  - Exactly one file matching get_brokers*.json — same shape: full payload
    or inner `{brokers: [...]}`.

Output (written to cs-data/, overwriting):
  - deals_2y.jsonl (merged: existing ∪ MCP, MCP wins for shared deal IDs)
  - broker_registry.json (rebuilt from MCP; manual notes preserved from
    existing if present)

The script is idempotent and side-effect-only on cs-data/. It does NOT run
process.py or build_dashboard.py — call those separately.
"""
import json, os, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
PROD_DEALS = os.path.join(HERE, 'deals_2y.jsonl')
PROD_REG   = os.path.join(HERE, 'broker_registry.json')


def _extract_payload(raw):
    """Accept either a tool-result wrapper, full MCP response, or just the inner data."""
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and 'text' in raw[0]:
        raw = json.loads(raw[0]['text'])
    if isinstance(raw, dict) and 'data' in raw and isinstance(raw['data'], dict):
        return raw['data']
    return raw


def merge_deals(input_dir):
    deal_files = sorted(glob.glob(os.path.join(input_dir, 'admin_deals*.json'))
                        + glob.glob(os.path.join(input_dir, 'admin_deals*.txt')))
    if not deal_files:
        print(f'WARN: no admin_deals*.json in {input_dir} — skipping deal merge')
        return 0, 0

    mcp_by_id = {}
    pages_seen = set()
    for fp in deal_files:
        with open(fp) as f:
            payload = _extract_payload(json.load(f))
        for d in payload.get('deals') or []:
            mcp_by_id[d['id']] = d
        p = payload.get('page')
        if p: pages_seen.add(p)

    existing = {}
    if os.path.exists(PROD_DEALS):
        with open(PROD_DEALS) as f:
            for line in f:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    existing[d['id']] = d

    overwritten = sum(1 for did in mcp_by_id if did in existing)
    new = len(mcp_by_id) - overwritten
    merged = {**existing, **mcp_by_id}

    with open(PROD_DEALS, 'w') as f:
        for d in sorted(merged.values(), key=lambda x: -x['id']):
            f.write(json.dumps(d, ensure_ascii=False) + '\n')
    print(f'deals: pages_seen={sorted(pages_seen) or "?"} '
          f'mcp={len(mcp_by_id)} existing={len(existing)} '
          f'refreshed={overwritten} new={new} total={len(merged)}')
    return overwritten, new


def merge_brokers(input_dir):
    files = sorted(glob.glob(os.path.join(input_dir, 'get_brokers*.json'))
                   + glob.glob(os.path.join(input_dir, 'get_brokers*.txt')))
    if not files:
        print(f'WARN: no get_brokers*.json in {input_dir} — skipping broker merge')
        return 0
    with open(files[-1]) as f:
        payload = _extract_payload(json.load(f))
    mcp_brokers = payload.get('brokers') or []

    existing_reg = {}
    if os.path.exists(PROD_REG):
        with open(PROD_REG) as f:
            existing_reg = json.load(f)

    out = {}
    org_pop = 0
    preserved = 0
    for b in mcp_brokers:
        name = b.get('name')
        if not name:
            continue
        org = b.get('organization') or {}
        if org: org_pop += 1
        entry = {
            'id':     b.get('id', 0),
            'name':   name,
            'phone':  b.get('phone') or '',
            'email':  b.get('email') or '',
            'created': b.get('created') or '',
            'from':   b.get('from') or '',
            'city':   b.get('city') or '',
            'state':  b.get('state') or '',
            'renewal_participation': b.get('renewal_participation'),
            'renewal_path':          b.get('renewal_path'),
            'onboarding':            b.get('onboarding', 0),
            'org_id':                b.get('organization_id') or 0,
            'org_name':              org.get('name') or '',
            'org_created':           org.get('created') or '',
            'org_perfil':            org.get('perfil') or '',
            'org_size':              org.get('size') or 0,
            'org_comission':         org.get('comission_agreement') or '',
            'last_login':            b.get('last_login') or '',
        }
        prev = existing_reg.get(name) or {}
        for k in ('kam_considerations', 'renewal_considerations'):
            v = prev.get(k)
            if v:
                entry[k] = v
                preserved += 1
        out[name] = entry

    with open(PROD_REG, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f'brokers: mcp={len(mcp_brokers)} unique={len(out)} '
          f'with_org={org_pop} manual_notes_preserved={preserved}')
    return len(out)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    input_dir = sys.argv[1]
    if not os.path.isdir(input_dir):
        print(f'ERROR: {input_dir} is not a directory')
        sys.exit(1)
    merge_deals(input_dir)
    merge_brokers(input_dir)


if __name__ == '__main__':
    main()
