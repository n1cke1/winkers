"""Curate description targets for CHP using the graph: importance, not count."""
import json
from collections import Counter, defaultdict

PATH = 'C:/Development/CHP model web/.winkers/graph.json'
g = json.load(open(PATH, encoding='utf-8'))
fns = g['functions']

# Skip auth + patches per user direction
SKIP_FILES = {'auth_db.py', 'patches/ticket_service_runner.py'}
def skip(fn):
    if fn['file'] in SKIP_FILES:
        return True
    if fn['name'].lower() in ('login', 'logout', 'register', 'verify_2fa', 'check_password',
                              'hash_password', 'gen_2fa', 'get_user', 'create_user'):
        return True
    if fn['name'].startswith('__') and fn['name'].endswith('__'):
        return True
    if fn.get('kind') == 'lambda':
        return True
    if (fn.get('lines') or 0) <= 3:
        return True
    return False

# In/out degree
in_deg = Counter()
out_deg = Counter()
for e in g['call_edges']:
    in_deg[e['target_fn']] += 1
    out_deg[e['source_fn']] += 1

# 1. Routes (entry points) — non-auth
routes = [(fid, fn) for fid, fn in fns.items() if fn.get('route') and not skip(fn)]
routes_filtered = [(fid, fn) for fid, fn in routes
                   if not any(x in (fn.get('route') or '').lower()
                              for x in ('login', 'logout', '2fa', 'auth', 'register', 'user'))]

# 2. Most-called (high in_deg) — non-trivial, non-auth
most_called = sorted(
    [(fid, fn, in_deg[fid]) for fid, fn in fns.items()
     if not skip(fn) and in_deg[fid] >= 2],
    key=lambda x: -x[2],
)

# 3. Orchestrators (high out_deg) — non-trivial, non-auth, non-route (routes already covered)
orchestrators = sorted(
    [(fid, fn, out_deg[fid]) for fid, fn in fns.items()
     if not skip(fn) and not fn.get('route') and out_deg[fid] >= 4],
    key=lambda x: -x[2],
)

# 4. Domain core files we identified as blind spots
DOMAIN_BLIND = {
    'engine/svg_builder.py',       # UI render
    'engine/turbine_factory.py',   # zone-intent says critical
    'engine/linear_model.py',      # LinearCoeffs lives here
    'engine/scenarios.py',         # apply_scenario
    'engine/climate.py',           # calc_monthly_loads
}
domain_fns = defaultdict(list)
for fid, fn in fns.items():
    if fn['file'] in DOMAIN_BLIND and not skip(fn):
        domain_fns[fn['file']].append((fid, fn, in_deg[fid]))

print("=" * 70)
print(f"Non-auth routes: {len(routes_filtered)}")
for fid, fn in sorted(routes_filtered, key=lambda x: -in_deg[x[0]]):
    method = fn.get('http_method', 'GET')
    callers = in_deg[fid]
    lines = fn.get('lines', 0)
    print(f"  [{method:4}] {fn.get('route'):28} -> {fid:55s}  callers={callers}  lines={lines}")

print()
print("=" * 70)
print(f"Most-called non-trivial (in_deg >= 2):")
for fid, fn, deg in most_called[:25]:
    print(f"  in={deg:3d}  {fid:60s}  lines={fn.get('lines',0)}")

print()
print("=" * 70)
print(f"Orchestrators (out_deg >= 4, non-route):")
for fid, fn, deg in orchestrators[:15]:
    print(f"  out={deg:3d}  {fid:60s}  lines={fn.get('lines',0)}")

print()
print("=" * 70)
print("Domain blind-spot files (every non-trivial fn):")
for f in sorted(DOMAIN_BLIND):
    print(f"\n  {f}:")
    for fid, fn, indg in sorted(domain_fns[f], key=lambda x: -x[2]):
        print(f"    in={indg:2d}  {fid:60s}  lines={fn.get('lines',0)}")
