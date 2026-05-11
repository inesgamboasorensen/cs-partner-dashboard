#!/usr/bin/env python3
"""
CS Strategic Allocation — Data Processor

Reads raw deal records from cs-data/deals_2y.jsonl and produces
cs-data/dashboard_data.json with broker + inmobiliaria lifecycle metrics.

Designed to run headless (cron / scheduled task). Idempotent.
"""
import json, os, sys, math
from datetime import datetime, date, timedelta
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_PATH = os.path.join(HERE, 'deals_2y.jsonl')
REG_PATH = os.path.join(HERE, 'broker_registry.json')
OUT_PATH = os.path.join(HERE, 'dashboard_data.json')

# --- Config ---
TODAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
CUTOFF_DAYS = 730  # 2 years
TEST_ACCOUNTS = {'MoradaUno - Atención Directa'}

# --- Revenue model (MoradaUno authoritative) ---
# Pago Total (what client pays) = renta × base_rate × (1 + IVA)
# Net revenue (to MoradaUno, without IVA) = renta × base_rate
# base_rate: per user spec. M3=30%, M6=40%, M12=60%, M Legal=25% for Protección de Renta.
# Enriched deals with real cost_percent stored in deal_rates.json are preferred when clearly with-IVA.
PRICING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pricing.json')
PRICING = {'rates': {}, 'default_rate': 0.30, 'iva': 0.16}
try:
    with open(PRICING_PATH) as _pf: PRICING = json.load(_pf)
except Exception: pass
IVA = PRICING.get('iva', 0.16)

def to_num(v):
    if v is None: return 0
    try: return float(v)
    except: return 0

def base_rate(deal):
    """Return base rate (without IVA) for deal. Prefer pricing.json; fallback to family default."""
    p = deal.get('product_name') or ''
    sp = deal.get('sub_product_name') or ''
    key = f"{p}|{sp}"
    r = PRICING.get('rates', {}).get(key)
    if r is not None: return r, 'fallback'
    # Fallback by product family (base rates, without IVA)
    product_defaults = {'Guarantee': 0.30, 'Screening': 0.08, 'Legal': 0.03}
    return product_defaults.get(p, PRICING.get('default_rate', 0.30)), 'default'

def revenue_of(deal):
    """Pago Total (lo que el cliente le paga a MoradaUno) por deal.

    En MoradaUno, `agreement.cost_percent` es el rate FINAL aplicado a la renta para calcular
    el Pago Total. Ejemplos observados:
      - Nuevo M3: cost_percent=0.348 (= 0.30 base + 16% IVA)
      - Renewal M3: cost_percent=0.27 (rate final de renovación, ya incluye IVA implícito)
    Por lo tanto: Pago Total = value × cost_percent cuando cost_percent está presente.

    Si no hay cost_percent enriquecido, usamos tabla base × (1 + IVA) como estimación.
    """
    v = to_num(deal.get('value'))
    if v <= 0: return 0
    cp = to_num(deal.get('cost_percent'))
    if cp > 0:
        # cost_percent enriquecido es el rate final del agreement — Pago Total directo
        return v * cp
    # Fallback estimado: base rate × (1 + IVA)
    br, _ = base_rate(deal)
    return v * br * (1 + IVA)

def net_revenue_of(deal):
    """Net revenue (sin IVA) to MoradaUno = Pago Total / (1+IVA)"""
    return revenue_of(deal) / (1 + IVA)

def parse_dt(s):
    if not s: return None
    try:
        return datetime.fromisoformat(s.replace(' 23:59:59', ''))
    except:
        try: return datetime.fromisoformat(s)
        except: return None

def lifecycle_stage(days_since_last, tenure_months, deals_90d, trend_pct, deals_per_month=0):
    """Classify broker lifecycle stage.

    Priority order (cada uno sobreescribe los siguientes):
      1. Churned       — > 190 días sin actividad
      2. At-Risk       — 60-90 días sin actividad
      3. Onboarding    — tenure < 3 meses
      4. Declining     — tenure 6+ meses con trend < -20%
      5. Mature-Healthy — tenure 12+ meses, trend ≥ 0, 2+ deals L90D
      6. Growing       — tenure 3-12 meses con 2+ deals L90D
      7. Active-Low    — default: cliente activo con baja productividad (<0.5 rentas/mes)
    """
    if days_since_last > 190: return 'Churned'
    if 60 <= days_since_last <= 90: return 'At-Risk'
    if tenure_months < 3: return 'Onboarding'
    if tenure_months >= 6 and (trend_pct or 0) < -20: return 'Declining'
    if tenure_months >= 12 and (trend_pct or 0) >= 0 and deals_90d >= 2: return 'Mature-Healthy'
    if 3 <= tenure_months < 12 and deals_90d >= 2: return 'Growing'
    return 'Active-Low'

def risk_signals(b):
    """Returns (risk_level, primary_signal, all_signals_list).

    Definición de riesgo (3 señales — tiered ALTO/MEDIO):
      1. Churn por inactividad — days_since_last > 180 y 2+ deals históricos (ALTO)
                                  days_since_last > 60 (MEDIO)
      2. Caída de uso severa    — trend_pct ≤ -50% comparando 180d vs 180d previos (ALTO)
                                  trend_pct ≤ -20% (MEDIO)
      3. Renovación baja        — tasa copita < 30% con 3+ contratos past-grace (ALTO)
                                  tasa copita < 50% (MEDIO)
    """
    signals = []
    d = b['days_since_last']; nd = b['deals_total']
    # Renewal-specific — use COPITA rate for retention signal (accurate, parent_id based)
    rr = b.get('renewal_rate_copita') if b.get('renewal_rate_copita') is not None else b.get('renewal_rate')
    expired_total = b.get('contracts_expired', 0)
    copitas = b.get('contracts_expired_copitas', b.get('contracts_expired_renewed', 0))

    # --- 1. Churn por inactividad ---
    if d > 180 and nd >= 2:
        signals.append(('ALTO', f'Churned · {d}d sin actividad'))
    elif d > 60 and nd >= 2:
        signals.append(('MEDIO', f'Sin actividad {d}d'))

    # --- 2. Caída de uso severa (180d vs prior 180d, per-day rate) ---
    recent_180 = b.get('deals_90d', 0) + b.get('deals_90_180', 0)
    prior_180 = b.get('deals_180_365', 0)
    tr_180 = None
    if prior_180 > 0:
        tr_180 = round((recent_180 / 180.0 - prior_180 / 185.0) / (prior_180 / 185.0) * 100)
    elif recent_180 > 0:
        tr_180 = 100  # brand-new activity, can't compare
    if tr_180 is not None and tr_180 <= -50:
        signals.append(('ALTO', f'Uso cayó {tr_180}% (180d)'))
    elif tr_180 is not None and tr_180 <= -20:
        signals.append(('MEDIO', f'Uso cayó {tr_180}% (180d)'))

    # --- 3. Renovación baja (tasa copita de contratos past-grace) ---
    if rr is not None and expired_total >= 3:
        if rr < 30:   signals.append(('ALTO',  f'Renovación baja {rr}% ({copitas}/{expired_total})'))
        elif rr < 50: signals.append(('MEDIO', f'Renovación baja {rr}%'))

    if not signals: return 'BAJO', 'Healthy', []
    signals.sort(key=lambda x: (0 if x[0]=='ALTO' else 1, x[1]))
    level = signals[0][0]
    return level, signals[0][1], [s[1] for s in signals]

def process():
    if not os.path.exists(RAW_PATH):
        print(f'ERROR: {RAW_PATH} not found', file=sys.stderr); sys.exit(1)

    deals = []
    with open(RAW_PATH) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                d = json.loads(line)
                if d.get('broker_name') and d['broker_name'] not in TEST_ACCOUNTS:
                    deals.append(d)
            except: pass

    print(f'Loaded {len(deals)} deals')

    # Load canonical broker registry (source of truth for tenure + org data)
    registry = {}
    if os.path.exists(REG_PATH):
        try:
            with open(REG_PATH) as f: registry = json.load(f)
            print(f'Loaded registry: {len(registry)} brokers')
        except Exception as e:
            print(f'Registry load error: {e}')

    # GLOBAL renewed_parent_ids — COPITA (confirmación):
    #   A past-grace contract "initiated renewal" if ANY child deal (Closed or Renewal) has
    #   parent_id pointing back to it. Matching is cross-broker so that renovations handled
    #   by a different broker in the same inmobiliaria still count.
    #
    # IMPORTANTE — tres tipos de "hijos" que cuentan como copita iniciada:
    #   1. deal_type='Renewal' con parent_id — renovación explícita (~649 en los datos)
    #   2. deal_type='Closed' con parent_id — renovación registrada como contrato nuevo (~539)
    #      Ejemplo: lease Nasser Mohamed tiene 13496→37307→… donde 37307 es Closed con
    #      parent=13496 — es la renovación del año 2.
    #   3. deal_type='Cancelled' con parent_id — copita iniciada pero cancelada (~29)
    #      Cuenta para "copita" (cliente intentó renovar) pero NO para "cierre".
    #
    # DATA GAP: Cuando un Renewal llega a pipefy_phase_name='Completed', MoradaUno limpia su
    # parent_id a 0. Para esos orphans usamos fallback por (tenant_name, address_key) con
    # ventana de 3-18 meses entre end_dates para recuperar el padre.
    GLOBAL_RENEWED_PARENTS_COPITA = {
        d.get('parent_id')
        for d in deals
        if d.get('deal_type') in ('Closed', 'Renewal', 'Cancelled')
        and d.get('parent_id')
    }
    parents_from_pid = len(GLOBAL_RENEWED_PARENTS_COPITA)

    # Fallback matcher for orphan Completed Renewals
    # Addresses are often formatted differently between the Completed Renewal and its parent
    # (e.g. "Ámsterdam 253" vs "Amsterdam 253 Departamento 1.7"). We use a prefix/subset
    # match on the street-name+number portion. Combined with tenant name and an end_date
    # sanity window (parent must end 3–18 months before the Renewal's end_date), this
    # recovers the majority of orphan parents for backfilled brokers.
    import unicodedata, re
    def _norm_txt(s):
        if not s: return ''
        s = unicodedata.normalize('NFKD', s).encode('ascii','ignore').decode('ascii')
        s = re.sub(r'\s+', ' ', s).strip().lower()
        return s
    def _addr_key(s):
        # Grab the first "word number" pair (street name + number), tolerating extra tokens
        s = _norm_txt(s)
        # Remove punctuation (except digits)
        s = re.sub(r'[^\w\s]', ' ', s)
        # Grab up to first 3 tokens (handles "Víctor Hugo 46" vs "Amsterdam 253")
        tokens = s.split()
        # Find index of first number
        num_idx = next((i for i,t in enumerate(tokens) if re.match(r'^\d+$', t)), -1)
        if num_idx < 1: return s[:30]  # fallback
        return ' '.join(tokens[:num_idx+1])  # street_name(s) + first number

    # Index by (tenant, addr_key)
    index = defaultdict(list)
    for d in deals:
        if d.get('deal_type') not in ('Closed', 'Renewal'): continue
        if d.get('product_name') != 'Guarantee': continue
        t = _norm_txt(d.get('tenant_name'))
        k = _addr_key(d.get('address'))
        if t and k:
            index[(t, k)].append(d)

    recovered = 0
    for d in deals:
        if d.get('deal_type') != 'Renewal': continue
        if d.get('pipefy_phase_name') != 'Completed': continue
        if d.get('parent_id'): continue
        key = (_norm_txt(d.get('tenant_name')), _addr_key(d.get('address')))
        if not key[0] or not key[1]: continue
        this_end = parse_dt(d.get('end_date'))
        if not this_end: continue
        cands = []
        for c in index.get(key, []):
            if c['id'] == d['id']: continue
            c_end = parse_dt(c.get('end_date'))
            if not c_end or c_end > this_end: continue
            months_diff = (this_end - c_end).days / 30.0
            # Expect predecessor to end 3–18 months before (typical lease ≈ 12 months)
            if 3 <= months_diff <= 18:
                cands.append((c_end, c))
        if cands:
            cands.sort(key=lambda x: x[0], reverse=True)  # most recent predecessor
            GLOBAL_RENEWED_PARENTS_COPITA.add(cands[0][1]['id'])
            recovered += 1
    GLOBAL_RENEWED_PARENTS = GLOBAL_RENEWED_PARENTS_COPITA  # legacy alias
    print(f'Global renewed parents (copita): {len(GLOBAL_RENEWED_PARENTS_COPITA)} '
          f'({parents_from_pid} via parent_id + {recovered} via tenant+address fallback)')

    # Group by broker
    by_broker = defaultdict(list)
    for d in deals:
        by_broker[d['broker_name']].append(d)

    brokers = []
    for broker, ds in by_broker.items():
        ds_sorted = sorted(ds, key=lambda x: x['created'])
        first_observed = parse_dt(ds_sorted[0]['created'])
        last = parse_dt(ds_sorted[-1]['created'])

        # Canonical tenure: prefer broker.created from registry
        reg = registry.get(broker, {})
        canonical_created = reg.get('created')
        if canonical_created:
            first = parse_dt(canonical_created) or first_observed
            tenure_source = 'registry'
        else:
            first = first_observed
            tenure_source = 'sampled'

        tenure_days = max(0, (last - first).days)
        tenure_months = max(0, (TODAY - first).days // 30)
        days_since_last = (TODAY - last).days

        deals_total = len(ds)
        open_ct = sum(1 for d in ds if d.get('deal_type') == 'Open')
        renewal_ct = sum(1 for d in ds if d.get('deal_type') == 'Renewal')
        cancelled_ct = sum(1 for d in ds if d.get('deal_type') == 'Cancelled')
        closed_ct = sum(1 for d in ds if d.get('deal_type') == 'Closed')

        # Deals in progress (actively closing right now) — solo NUEVAS rentas en pipeline activo.
        # NO incluye renovaciones ni contratos ya cerrados. Filtro por pipefy_phase_name:
        ACTIVE_PHASES = {'Tenant Profile', 'Screening', 'Rent Confirmation', 'Contracts', 'Signing', 'Closing'}
        # Excluye: None (sin fase, zombie), KAM S0 (pre-qualification), Post signing (cerrando),
        # Legal - Contracts, Zombieland (stale), Completed.
        def is_in_progress(d):
            if d.get('deal_type') != 'Open': return False
            return d.get('pipefy_phase_name') in ACTIVE_PHASES
        in_progress = [d for d in ds if is_in_progress(d)]
        in_progress_count = len(in_progress)
        in_progress_value = sum(to_num(d.get('value')) for d in in_progress)
        in_progress_details = [{
            'id': d['id'],
            'created': d['created'][:10],
            'status': d.get('deal_status') or '',
            'value': to_num(d.get('value')),
            'product': d.get('product_name') or '',
            'tenant': d.get('tenant_name') or d.get('tname') or '',
            'end_date': (d.get('end_date') or '')[:10],
        } for d in sorted(in_progress, key=lambda x:x['created'], reverse=True)[:10]]

        deals_30d = sum(1 for d in ds if (TODAY - parse_dt(d['created'])).days <= 30)
        deals_90d = sum(1 for d in ds if (TODAY - parse_dt(d['created'])).days <= 90)
        deals_90_180 = sum(1 for d in ds if 90 < (TODAY - parse_dt(d['created'])).days <= 180)
        deals_180_365 = sum(1 for d in ds if 180 < (TODAY - parse_dt(d['created'])).days <= 365)
        deals_365_plus = sum(1 for d in ds if (TODAY - parse_dt(d['created'])).days > 365)

        # New windows for the L6M-vs-prior-L6M trend (smoother, less noisy than 90/90 split).
        # L6M = last 180 days. Prior L6M = days 180-360 (the 180 days right before the L6M window).
        # Symmetric windows so the % change is honest without rate normalization.
        deals_l6m = sum(1 for d in ds if (TODAY - parse_dt(d['created'])).days <= 180)
        deals_prior_6m = sum(1 for d in ds if 180 < (TODAY - parse_dt(d['created'])).days <= 360)

        active_now = deals_90d > 0

        # Quarterly buckets (last 8 quarters) — NEW RENTS ONLY (exclude Renewals and Cancelled)
        def stack_key(d):
            """Key for stacked chart. Guarantee → sub-product (M3/M6/M12/M Legal). Else → product."""
            p = d.get('product_name') or 'Unknown'
            sp = d.get('sub_product_name') or ''
            if p == 'Guarantee':
                return sp or 'Guarantee (other)'
            return p  # Screening, Legal
        quarters = defaultdict(int)
        rev_quarters = defaultdict(int)
        quarters_by_product = defaultdict(lambda: defaultdict(int))  # q -> stack_key -> count
        for d in ds:
            dt = parse_dt(d['created'])
            if dt:
                q = f"{dt.year}Q{(dt.month-1)//3+1}"
                # Nuevas rentas = Closed o Open (excluye Renewal y Cancelled)
                if d.get('deal_type') in ('Closed', 'Open'):
                    quarters[q] += 1
                    rev_quarters[q] += revenue_of(d)
                    quarters_by_product[q][stack_key(d)] += 1

        # Trend: recent 90d vs prior 90d rate (legacy — kept for backwards compatibility)
        trend_pct = None
        rate_recent = deals_90d / 90.0
        rate_prior = deals_90_180 / 90.0
        if rate_prior > 0:
            trend_pct = round((rate_recent - rate_prior) / rate_prior * 100)
        elif rate_recent > 0:
            trend_pct = 100  # brand new activity

        # PRIMARY trend (per CS feedback May-2026): L6M vs prior L6M — symmetric windows
        # dampen week-to-week noise without rate-normalization gymnastics. Standard
        # business definition: "is this account doing more or less than 6 months ago".
        trend_l6m_pct = None
        if deals_prior_6m > 0:
            trend_l6m_pct = round((deals_l6m - deals_prior_6m) / deals_prior_6m * 100)
        elif deals_l6m > 0:
            trend_l6m_pct = 100  # brand-new activity, no prior period to compare

        # Low-volume guard: when the broker has very few deals overall, percent-change
        # metrics like trend become noisy / misleading. Flagging it lets the UI gate
        # the display ("bajo vol." tooltip).
        low_volume = (deals_l6m + deals_prior_6m) < 3

        # Revenue trend (Q vs Q-1)
        q_keys = sorted(quarters.keys())
        rev_trend_pct = None
        if len(q_keys) >= 2:
            last_q = rev_quarters[q_keys[-1]]
            prev_q = rev_quarters[q_keys[-2]]
            if prev_q > 0:
                rev_trend_pct = round((last_q - prev_q) / prev_q * 100)

        # --- Renewal logic (real MoradaUno model) ---
        # Only Guarantee Closed/Renewal contracts renew. Renewal window opens 60d before end_date.
        #
        # TWO COMPLEMENTARY RATES:
        # 1) COPITA (retención): % de contratos past-grace donde SE INICIÓ una renovación.
        #    Match via parent_id. Pregunta: ¿el cliente regresó? (intención de quedarse)
        # 2) CIERRE (funnel operativo): % de los propios deals Renewal que llegaron a Completed.
        #    Pregunta: ¿M1 logra llevar las renovaciones iniciadas hasta la firma? (ejecución)
        # Se presentan juntas porque copita alto + cierre bajo revela leak operacional;
        # copita bajo revela pérdida de cliente.
        copita_parents = GLOBAL_RENEWED_PARENTS_COPITA

        # --- Bucket contracts by end_date position (for copita rate + expiring list) ---
        pg_copita = 0       # past-grace with copita initiated (from any broker)
        pg_perdido = 0      # past-grace with nothing (no Renewal deal ever)
        gr_copita = 0       # grace with copita initiated
        gr_pendiente = 0    # grace without copita (RESCATE URGENTE)
        wo_copita = 0       # window-open with copita initiated
        wo_pendiente = 0    # window-open without copita (ACCIONABLE)
        up_copita = 0
        up_pendiente = 0

        expiring_list = []

        for d in ds:
            # A Guarantee contract can be Closed (new) or Renewal (renewal of a previous contract).
            # Both types have their own end_date and can themselves be renewed again (renewal chain).
            if d.get('deal_type') not in ('Closed', 'Renewal'): continue
            if d.get('product_name') != 'Guarantee': continue
            if d.get('renewal_available') == 0: continue  # customer opted out
            end = parse_dt(d.get('end_date'))
            if not end: continue
            days_to = (end - TODAY).days
            cid = d.get('id')
            has_copita = cid in copita_parents

            if days_to < -30:
                if has_copita: pg_copita += 1
                else: pg_perdido += 1
            elif -30 <= days_to <= 0:
                if has_copita: gr_copita += 1
                else:
                    gr_pendiente += 1
                    expiring_list.append({'id': cid, 'end_date': d['end_date'][:10], 'days_to_end': days_to, 'status': 'grace', 'sub_product': d.get('sub_product_name') or '', 'value': to_num(d.get('value'))})
            elif 0 < days_to <= 60:
                if has_copita: wo_copita += 1
                else:
                    wo_pendiente += 1
                    expiring_list.append({'id': cid, 'end_date': d['end_date'][:10], 'days_to_end': days_to, 'status': 'window_open', 'sub_product': d.get('sub_product_name') or '', 'value': to_num(d.get('value'))})
            elif 60 < days_to <= 180:
                if has_copita: up_copita += 1
                else:
                    up_pendiente += 1
                    expiring_list.append({'id': cid, 'end_date': d['end_date'][:10], 'days_to_end': days_to, 'status': 'upcoming', 'sub_product': d.get('sub_product_name') or '', 'value': to_num(d.get('value'))})

        # --- COPITA rate (past-grace only): % of expired contracts where renewal was initiated ---
        denom_pg = pg_copita + pg_perdido   # total past-grace
        renewal_rate_copita = int(pg_copita / denom_pg * 100) if denom_pg > 0 else None

        # --- CIERRE rate (funnel): % of this broker's own Renewal deals that reached Completed ---
        own_renewals = [d for d in ds if d.get('deal_type') == 'Renewal']
        own_renewals_completed = [d for d in own_renewals if d.get('pipefy_phase_name') == 'Completed']
        own_renewals_inflight = [d for d in own_renewals if d.get('pipefy_phase_name') != 'Completed']
        renewal_cierres_own = len(own_renewals_completed)
        renewal_inflight_own = len(own_renewals_inflight)
        renewal_total_own = len(own_renewals)
        renewal_rate_cierre = int(renewal_cierres_own / renewal_total_own * 100) if renewal_total_own > 0 else None

        # Totals for UI
        total_expired = denom_pg
        total_copitas_pg = pg_copita
        total_perdidos_pg = pg_perdido

        # Grace and actionable views
        contracts_grace_period = gr_pendiente           # pending rescue (no copita)
        contracts_grace_copita = gr_copita              # grace with copita initiated
        contracts_in_renewal_window = wo_pendiente      # actionable now
        contracts_upcoming_180d = up_pendiente + up_copita

        # Legacy fields (kept for compatibility) — aligned to COPITA (the accurate rate)
        contracts_expired_renewed = pg_copita           # renewals with parent match (copita)
        contracts_expired_lost = pg_perdido
        contracts_grace_renewed = gr_copita
        renewal_rate = renewal_rate_copita              # primary rate = copita (retention)

        # Sort expiring list: grace first (most urgent), then window_open, then upcoming
        status_order = {'grace': 0, 'window_open': 1, 'upcoming': 2}
        expiring_list.sort(key=lambda x: (status_order.get(x['status'], 9), x['days_to_end']))
        expiring_list = expiring_list[:15]  # cap display

        # --- Quarterly renewal availability ---
        # For each Guarantee Closed/Renewal contract, bucket by end_date quarter.
        # Only contracts that ALREADY hit their renewal window (end_date <= today) count
        # as "disponibles para renovar" (cierres_renewal_available).
        # Each bucket is classified as copita / inquilino_salio / grace (add up to disponibles).
        renewal_available_quarters = defaultdict(lambda: {'copita': 0, 'inquilino_salio': 0, 'grace': 0})
        for d in ds:
            if d.get('deal_type') not in ('Closed', 'Renewal'): continue
            if d.get('product_name') != 'Guarantee': continue
            if d.get('renewal_available') == 0: continue
            end = parse_dt(d.get('end_date'))
            if not end: continue
            days_to = (end - TODAY).days
            # Only past or grace contracts (not yet-open window or upcoming) — those haven't
            # reached a definitive outcome yet and would be misleading on a historical chart.
            if days_to > 0:
                continue
            q_key = f"{end.year}Q{(end.month - 1)//3 + 1}"
            cid = d.get('id')
            has_copita = cid in copita_parents
            if has_copita:
                renewal_available_quarters[q_key]['copita'] += 1
            elif days_to < -30:
                renewal_available_quarters[q_key]['inquilino_salio'] += 1
            else:
                renewal_available_quarters[q_key]['grace'] += 1

        # Cap to last 8 populated quarters
        q_keys_avail = sorted(renewal_available_quarters.keys())[-8:]
        renewal_available_by_q = [
            {
                'q': q,
                'copita': renewal_available_quarters[q]['copita'],
                'inquilino_salio': renewal_available_quarters[q]['inquilino_salio'],
                'grace': renewal_available_quarters[q]['grace'],
                'total': renewal_available_quarters[q]['copita'] + renewal_available_quarters[q]['inquilino_salio'] + renewal_available_quarters[q]['grace'],
            }
            for q in q_keys_avail
        ]

        # --- Quarterly renewal funnel ---
        # Group own Renewal deals by creation quarter; count how many reached Completed.
        # Uses the broker's OWN Renewal deals (not the past-grace parent contracts), because
        # parent_id is cleared when a Renewal reaches Completed (data gap).
        renewal_funnel_quarters = defaultdict(lambda: {'copitas': 0, 'cerrados': 0})
        for d in ds:
            if d.get('deal_type') != 'Renewal': continue
            created = parse_dt(d.get('created'))
            if not created: continue
            q_key = f"{created.year}Q{(created.month - 1)//3 + 1}"
            renewal_funnel_quarters[q_key]['copitas'] += 1
            if d.get('pipefy_phase_name') == 'Completed':
                renewal_funnel_quarters[q_key]['cerrados'] += 1
        q_keys_funnel = sorted(renewal_funnel_quarters.keys())[-8:]
        renewal_funnel_by_q = [
            {
                'q': q,
                'copitas': renewal_funnel_quarters[q]['copitas'],
                'cerrados': renewal_funnel_quarters[q]['cerrados'],
                'cierre_pct': int(renewal_funnel_quarters[q]['cerrados'] / renewal_funnel_quarters[q]['copitas'] * 100) if renewal_funnel_quarters[q]['copitas'] > 0 else 0,
            }
            for q in q_keys_funnel
        ]

        # Legacy compatibility fields (used elsewhere in codebase)
        contracts_expired = contracts_expired_renewed + contracts_expired_lost
        contracts_expiring_90d = contracts_in_renewal_window + contracts_grace_period

        # Product mix + avg ticket per product
        product_counts = Counter(d.get('product_name') or 'Unknown' for d in ds)
        product_tickets = defaultdict(list)
        for d in ds:
            if to_num(d.get('value')) > 0:
                product_tickets[d.get('product_name') or 'Unknown'].append(to_num(d.get('value')))
        product_mix = []
        for p, cnt in product_counts.most_common():
            tickets = product_tickets.get(p, [])
            avg_t = int(sum(tickets)/len(tickets)) if tickets else 0
            product_mix.append({'product': p, 'count': cnt, 'avg_ticket': avg_t})

        # Revenue (MoradaUno's Pago Total = value × cost_percent)
        revenue_deals = [d for d in ds if d.get('deal_type') != 'Cancelled']
        total_revenue = sum(revenue_of(d) for d in revenue_deals)
        enriched_deals = sum(1 for d in revenue_deals if to_num(d.get('cost_percent'))>0 or to_num(d.get('revenue'))>0)
        revenue_quality = 'actual' if enriched_deals == len(revenue_deals) and len(revenue_deals)>0 else ('partial' if enriched_deals > 0 else 'estimated')
        avg_deal_revenue = int(total_revenue / max(1, deals_total - cancelled_ct))

        # === LAST 12 MONTHS (primary operational window) ===
        l12m_deals_list = [d for d in revenue_deals if (TODAY - parse_dt(d['created'])).days <= 365]
        deals_l12m = len(l12m_deals_list)
        revenue_l12m = sum(revenue_of(d) for d in l12m_deals_list)
        # Product mix L12M
        l12m_by_product = Counter(d.get('product_name') or 'Unknown' for d in l12m_deals_list)
        avg_deal_revenue_l12m = int(revenue_l12m / max(1, deals_l12m))
        # Monthly revenue rate — honest window (L12M)
        revenue_per_month = int(revenue_l12m / 12) if deals_l12m > 0 else 0
        # L12M enrichment quality
        l12m_enriched = sum(1 for d in l12m_deals_list if to_num(d.get('cost_percent'))>0 or to_num(d.get('revenue'))>0)
        revenue_quality_l12m = 'actual' if l12m_enriched == deals_l12m and deals_l12m>0 else ('partial' if l12m_enriched>0 else 'estimated')

        # Ticket stats
        tickets = [to_num(d.get('value')) for d in ds if to_num(d.get('value')) > 0]
        tickets.sort()
        avg_ticket = int(sum(tickets)/len(tickets)) if tickets else 0
        median_ticket = tickets[len(tickets)//2] if tickets else 0
        min_ticket = tickets[0] if tickets else 0
        max_ticket = tickets[-1] if tickets else 0
        p25 = tickets[len(tickets)//4] if len(tickets)>=4 else min_ticket
        p75 = tickets[3*len(tickets)//4] if len(tickets)>=4 else max_ticket

        # Deal frequency (per month of tenure, excluding onboarding first 30 days)
        active_days = max(1, tenure_days)
        deals_per_month = round(deals_total / (active_days/30.0), 1) if active_days > 30 else deals_total

        # Segment — prefer canonical org from registry
        company = reg.get('org_name') or ds_sorted[-1].get('broker_company') or ''
        segment = 'Inmobiliaria' if company else 'Independiente'
        agent = ds_sorted[-1].get('broker_agent_name') or ''
        phone = reg.get('phone') or ds_sorted[-1].get('broker_phone') or ''
        email = reg.get('email') or ''
        city = reg.get('city') or ''
        state = reg.get('state') or ''
        broker_id = reg.get('id') or 0
        org_id = reg.get('org_id') or 0
        org_perfil = reg.get('org_perfil') or ''
        org_created = reg.get('org_created') or ''
        renewal_participation = reg.get('renewal_participation')
        renewal_path = reg.get('renewal_path') or ''
        last_login = reg.get('last_login') or ''

        broker_obj = {
            'id': (broker + '|' + company).replace('"', ''),
            'broker': broker,
            'broker_id': broker_id,
            'company': company,
            'org_id': org_id,
            'org_perfil': org_perfil,
            'org_created': org_created[:10] if org_created else '',
            'phone': phone,
            'email': email,
            'agent': agent,
            'city': city,
            'state': state,
            'segment': segment,
            'renewal_participation': renewal_participation,
            'renewal_path': renewal_path,
            'last_login': last_login[:10] if last_login else '',
            # Lifecycle timing — canonical first_active vs observed first_deal
            'first_active': (canonical_created or ds_sorted[0]['created'])[:10],
            'first_deal_observed': ds_sorted[0]['created'][:10],
            'last_deal': ds_sorted[-1]['created'][:10],
            'tenure_source': tenure_source,  # 'registry' | 'sampled'
            'tenure_months': tenure_months,
            'tenure_days': tenure_days,
            'days_since_last': days_since_last,
            'active_now': active_now,
            # Activity volume
            'deals_total': deals_total,
            'deals_per_month': deals_per_month,
            'deals_30d': deals_30d,
            'deals_90d': deals_90d,
            'deals_90_180': deals_90_180,
            'deals_180_365': deals_180_365,
            'deals_365_plus': deals_365_plus,
            'deals_l6m': deals_l6m,
            'deals_prior_6m': deals_prior_6m,
            'trend_pct': trend_pct,             # legacy 90d/90d
            'trend_l6m_pct': trend_l6m_pct,     # primary L6M vs prior L6M
            'low_volume': low_volume,
            'rev_trend_pct': rev_trend_pct,
            # Deal type breakdown
            'open_count': open_ct,
            'renewal_count': renewal_ct,
            'closed_count': closed_ct,
            # Deals closing right now
            'in_progress_count': in_progress_count,
            'in_progress_value': int(in_progress_value),
            'in_progress_details': in_progress_details,
            # Retention (real MoradaUno model — only Guarantee contracts renew)
            #
            # TWO RATES (different questions, computed independently):
            #   COPITA (retención): past-grace contracts with renewal initiated (parent_id match).
            #                       Pregunta: ¿regresó el cliente?
            #   CIERRE (funnel):     this broker's own Renewal deals that reached Completed.
            #                       Pregunta: ¿logramos cerrar las renovaciones que iniciamos?
            # Data gap: MoradaUno's API clears parent_id when a Renewal reaches Completed, so we
            # can't match Completed Renewals back to specific parents. The broker-level funnel
            # (cierre rate) is the best proxy for operational renewal execution.
            'renewal_rate_copita': renewal_rate_copita,           # % past-grace con renovación iniciada
            'renewal_rate_cierre': renewal_rate_cierre,           # % Renewals propios que cerraron (funnel)
            'contracts_expired': contracts_expired,               # total past-grace
            'contracts_expired_copitas': total_copitas_pg,        # past-grace: renovación iniciada
            'contracts_expired_lost': total_perdidos_pg,          # past-grace: ni siquiera copita
            # Funnel counts (broker's own renewal deals)
            'renewal_total_own': renewal_total_own,               # total Renewals este broker
            'renewal_cierres_own': renewal_cierres_own,           # Completed
            'renewal_inflight_own': renewal_inflight_own,         # en proceso
            # Legacy aliases
            'renewal_rate': renewal_rate,                         # = copita (primary)
            'contracts_expiring_90d': contracts_expiring_90d,     # legacy (window + grace)
            'contracts_expired_renewed': contracts_expired_renewed,  # = contracts_expired_copitas
            'contracts_expired_cerrados': total_copitas_pg,       # legacy UI alias (= copitas)
            # Actionable buckets (not yet past-grace)
            'contracts_in_renewal_window': contracts_in_renewal_window,  # 0-60d to expire, sin copita — ACCIONABLE
            'contracts_grace_period': contracts_grace_period,     # expired 0-30d, sin copita — RESCATE URGENTE
            'contracts_grace_copita': contracts_grace_copita,     # expired 0-30d, copita iniciada
            'contracts_grace_renewed': contracts_grace_renewed,   # = contracts_grace_copita
            'contracts_upcoming_180d': contracts_upcoming_180d,   # 60-180d out — plan ahead
            'expiring_list': expiring_list,                       # details for modal
            # Quarterly retention charts (reuso en modal del asesor)
            'renewal_available_quarters': renewal_available_by_q, # stacked: copita + inquilino_salio + grace por Q (end_date)
            'renewal_funnel_quarters': renewal_funnel_by_q,       # por Q (Renewal.created): copitas, cerrados, cierre_pct
            # Revenue (Pago Total MoradaUno)
            'total_revenue': int(total_revenue),   # full 2y window (for reference)
            'avg_deal_revenue': avg_deal_revenue,
            'revenue_per_month': revenue_per_month,
            'revenue_quality': revenue_quality,    # actual|partial|estimated over 2y
            'enriched_deals': enriched_deals,
            # L12M (primary)
            'deals_l12m': deals_l12m,
            'revenue_l12m': int(revenue_l12m),
            'avg_deal_revenue_l12m': avg_deal_revenue_l12m,
            'l12m_enriched': l12m_enriched,
            'revenue_quality_l12m': revenue_quality_l12m,
            'l12m_product_mix': [{'product': p, 'count': c} for p, c in l12m_by_product.most_common()],
            # Ticket distribution
            'avg_ticket': avg_ticket,
            'median_ticket': median_ticket,
            'min_ticket': min_ticket,
            'max_ticket': max_ticket,
            'p25_ticket': p25,
            'p75_ticket': p75,
            # Product mix
            'product_mix': product_mix,
            # Quarterly series (last 8Q) with product mix
            'quarters': [{'q': q, 'deals': quarters[q], 'revenue': int(rev_quarters[q]), 'by_product': dict(quarters_by_product[q])} for q in q_keys[-8:]],
        }

        # Risk + lifecycle
        risk, primary, all_signals = risk_signals(broker_obj)
        broker_obj['risk'] = risk
        broker_obj['primary_signal'] = primary
        broker_obj['signals'] = all_signals
        broker_obj['lifecycle'] = lifecycle_stage(days_since_last, tenure_months, deals_90d, trend_pct, deals_per_month)

        brokers.append(broker_obj)

    # Sort brokers: ALTO first, then by revenue
    risk_order = {'ALTO':0,'MEDIO':1,'BAJO':2}
    brokers.sort(key=lambda b: (risk_order[b['risk']], -b['total_revenue']))

    # --- Inmobiliaria rollup (group by company, excludes Independientes) ---
    by_company = defaultdict(list)
    for b in brokers:
        if b['company']:
            by_company[b['company']].append(b)

    inmobiliarias = []
    for company, bs in by_company.items():
        all_products = Counter()
        total_tickets = []
        revenue_qs = defaultdict(int)
        deal_qs = defaultdict(int)
        quarters_by_product_agg = defaultdict(lambda: defaultdict(int))  # q -> product -> count
        for b in bs:
            for pm in b['product_mix']:
                all_products[pm['product']] += pm['count']
            for q in b['quarters']:
                revenue_qs[q['q']] += q['revenue']
                deal_qs[q['q']] += q['deals']
                for prod, cnt in (q.get('by_product') or {}).items():
                    quarters_by_product_agg[q['q']][prod] += cnt

        all_deals_total = sum(b['deals_total'] for b in bs)
        all_renewal = sum(b['renewal_count'] for b in bs)
        all_expired = sum(b['contracts_expired'] for b in bs)
        all_expiring = sum(b['contracts_expiring_90d'] for b in bs)
        inmo_copitas = sum(b.get('contracts_expired_copitas', 0) for b in bs)
        inmo_lost = sum(b.get('contracts_expired_lost', 0) for b in bs)
        inmo_renewals_total_own = sum(b.get('renewal_total_own', 0) for b in bs)
        inmo_renewals_cierres_own = sum(b.get('renewal_cierres_own', 0) for b in bs)
        inmo_renewals_inflight_own = sum(b.get('renewal_inflight_own', 0) for b in bs)
        inmo_window = sum(b.get('contracts_in_renewal_window', 0) for b in bs)
        inmo_grace = sum(b.get('contracts_grace_period', 0) for b in bs)
        inmo_grace_renewed = sum(b.get('contracts_grace_renewed', 0) for b in bs)
        inmo_upcoming = sum(b.get('contracts_upcoming_180d', 0) for b in bs)
        all_in_progress = sum(b['in_progress_count'] for b in bs)
        all_in_progress_value = sum(b['in_progress_value'] for b in bs)
        active_brokers = sum(1 for b in bs if b['active_now'])
        churned_brokers = sum(1 for b in bs if b['days_since_last'] > 90)
        deals_90d_all = sum(b['deals_90d'] for b in bs)
        deals_90_180_all = sum(b['deals_90_180'] for b in bs)
        deals_l6m_all = sum(b.get('deals_l6m', 0) for b in bs)
        deals_prior_6m_all = sum(b.get('deals_prior_6m', 0) for b in bs)
        rate_recent = deals_90d_all / 90.0 if deals_90d_all else 0
        rate_prior = deals_90_180_all / 90.0 if deals_90_180_all else 0
        trend_pct = None
        if rate_prior > 0: trend_pct = round((rate_recent - rate_prior) / rate_prior * 100)
        elif rate_recent > 0: trend_pct = 100
        # L6M-vs-prior-L6M trend (same symmetric definition as broker level)
        trend_l6m_pct = None
        if deals_prior_6m_all > 0:
            trend_l6m_pct = round((deals_l6m_all - deals_prior_6m_all) / deals_prior_6m_all * 100)
        elif deals_l6m_all > 0:
            trend_l6m_pct = 100
        low_volume_inmo = (deals_l6m_all + deals_prior_6m_all) < 3

        oldest_first = min((b['first_active'] for b in bs), default='')
        newest_last = max((b['last_deal'] for b in bs), default='')
        days_since = min((b['days_since_last'] for b in bs), default=999)
        tenure_months = max((b['tenure_months'] for b in bs), default=0)

        total_revenue = sum(b['total_revenue'] for b in bs)
        # avg_ticket = weighted mean of broker rent values (what the tenant pays).
        # KEEP for backwards compatibility, but the UI now reads avg_deal_revenue —
        # the *MoradaUno* revenue per deal (Pago Total ÷ deals). The user feedback
        # was that "Ticket promedio" was showing the rent value, not MoradaUno's cut.
        avg_ticket = int(sum(b['avg_ticket'] * b['deals_total'] for b in bs) / max(1, all_deals_total))
        # Effective non-cancelled deal count for revenue-per-deal math
        deals_for_rev = sum(max(0, b['deals_total'] - b.get('renewal_count', 0) * 0) for b in bs)  # rev_deals already excludes Cancelled inside revenue_of
        avg_deal_revenue = int(total_revenue / max(1, all_deals_total)) if all_deals_total else 0
        # Inmo renewal rates (same definitions as per-broker, rolled up)
        inmo_renewal_denom = inmo_copitas + inmo_lost   # past-grace total
        renewal_rate_copita = int(inmo_copitas / inmo_renewal_denom * 100) if inmo_renewal_denom else None
        renewal_rate_cierre = int(inmo_renewals_cierres_own / inmo_renewals_total_own * 100) if inmo_renewals_total_own else None
        renewal_rate = renewal_rate_copita  # primary = copita

        # Inmobiliaria risk (worst of its brokers)
        alto = sum(1 for b in bs if b['risk']=='ALTO')
        medio = sum(1 for b in bs if b['risk']=='MEDIO')
        bajo = sum(1 for b in bs if b['risk']=='BAJO')
        if alto > len(bs)/3: inmo_risk = 'ALTO'
        elif alto + medio > len(bs)/2: inmo_risk = 'MEDIO'
        else: inmo_risk = 'BAJO'

        # Penetration: brokers active in 90d / total brokers
        penetration = int(active_brokers / len(bs) * 100) if bs else 0

        q_keys_all = sorted(revenue_qs.keys())
        quarters = [{'q': q, 'deals': deal_qs[q], 'revenue': int(revenue_qs[q]), 'by_product': dict(quarters_by_product_agg[q])} for q in q_keys_all[-8:]]

        # L12M rollup for inmobiliaria
        inmo_deals_l12m = sum(b['deals_l12m'] for b in bs)
        inmo_revenue_l12m = sum(b['revenue_l12m'] for b in bs)

        # Lifecycle distribution
        lc_dist = Counter(b['lifecycle'] for b in bs)

        inmo = {
            'id': 'inmo:' + company,
            'company': company,
            'brokers_total': len(bs),
            'brokers_active_90d': active_brokers,
            'brokers_churned': churned_brokers,
            'penetration': penetration,
            'risk': inmo_risk,
            'risk_dist': {'ALTO': alto, 'MEDIO': medio, 'BAJO': bajo},
            'lifecycle_dist': dict(lc_dist),
            'first_deal': oldest_first,
            'last_deal': newest_last,
            'tenure_months': tenure_months,
            'days_since_last': days_since,
            'deals_total': all_deals_total,
            'deals_90d': deals_90d_all,
            'deals_90_180': deals_90_180_all,
            'deals_l6m': deals_l6m_all,
            'deals_prior_6m': deals_prior_6m_all,
            'trend_pct': trend_pct,                 # legacy 90/90
            'trend_l6m_pct': trend_l6m_pct,         # primary L6M vs prior L6M
            'low_volume': low_volume_inmo,
            'avg_deal_revenue': avg_deal_revenue,   # MoradaUno revenue per deal
            'renewal_count': all_renewal,
            'renewal_rate': renewal_rate,
            'renewal_rate_copita': renewal_rate_copita,
            'renewal_rate_cierre': renewal_rate_cierre,
            'contracts_expired': all_expired,
            'contracts_expired_copitas': inmo_copitas,
            'contracts_expired_lost': inmo_lost,
            'renewal_total_own': inmo_renewals_total_own,
            'renewal_cierres_own': inmo_renewals_cierres_own,
            'renewal_inflight_own': inmo_renewals_inflight_own,
            'contracts_expiring_90d': all_expiring,
            'contracts_expired_renewed': inmo_copitas,   # legacy alias
            'contracts_expired_cerrados': inmo_copitas,  # legacy UI alias
            'contracts_in_renewal_window': inmo_window,
            'contracts_grace_period': inmo_grace,
            'contracts_grace_renewed': inmo_grace_renewed,
            'contracts_upcoming_180d': inmo_upcoming,
            'in_progress_count': all_in_progress,
            'in_progress_value': all_in_progress_value,
            'total_revenue': total_revenue,   # full 2y
            'deals_l12m': inmo_deals_l12m,
            'revenue_l12m': inmo_revenue_l12m,
            'avg_ticket': avg_ticket,
            'product_mix': [{'product': p, 'count': c} for p, c in all_products.most_common()],
            'quarters': quarters,
            'top_brokers': [{'broker': b['broker'], 'revenue': b['total_revenue'], 'deals': b['deals_total'], 'risk': b['risk']} for b in sorted(bs, key=lambda x: -x['total_revenue'])[:5]],
        }
        inmobiliarias.append(inmo)

    inmobiliarias.sort(key=lambda i: -i['total_revenue'])

    # --- Meta ---
    meta = {
        'generated_at': TODAY.strftime('%Y-%m-%d %H:%M'),
        'total_deals_processed': len(deals),
        'total_brokers': len(brokers),
        'total_inmobiliarias': len(inmobiliarias),
        'date_range': {
            'from': min(d['created'] for d in deals)[:10],
            'to': max(d['created'] for d in deals)[:10]
        },
        'risk_breakdown': {
            'ALTO': sum(1 for b in brokers if b['risk']=='ALTO'),
            'MEDIO': sum(1 for b in brokers if b['risk']=='MEDIO'),
            'BAJO': sum(1 for b in brokers if b['risk']=='BAJO'),
        },
        'lifecycle_breakdown': dict(Counter(b['lifecycle'] for b in brokers)),
        'revenue_total': sum(b['total_revenue'] for b in brokers),
        'revenue_at_risk': sum(b['total_revenue'] for b in brokers if b['risk']=='ALTO'),
    }

    out = {'meta': meta, 'brokers': brokers, 'inmobiliarias': inmobiliarias}
    with open(OUT_PATH, 'w') as f:
        json.dump(out, f, separators=(',',':'))
    print(f'Wrote {OUT_PATH}')
    print(f'  Brokers: {len(brokers)} ({meta["risk_breakdown"]})')
    print(f'  Inmobiliarias: {len(inmobiliarias)}')
    print(f'  Revenue total: ${meta["revenue_total"]:,.0f}')
    print(f'  Revenue at risk: ${meta["revenue_at_risk"]:,.0f}')
    return out

if __name__ == '__main__':
    process()
