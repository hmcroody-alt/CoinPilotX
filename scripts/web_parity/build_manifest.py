#!/usr/bin/env python3
"""Join backend routes + native screens + web pages into the canonical parity manifest."""
import json,re,csv
from collections import defaultdict,Counter
routes=json.load(open('reports/web_parity/route_table.json'))
native=json.load(open('reports/web_parity/native_map.json'))
auth=json.load(open('reports/web_parity/auth_audit.json'))
backend={r['path'] for r in routes}
def norm(p): return re.sub(r'<[^>]+>','*',p).rstrip('/')
backend_norm={norm(p) for p in backend}
# --- endpoint reachability: does every native endpoint exist in backend? ---
nat_eps={e for s in native['screens'].values() for e in s['endpoints']}
missing=sorted(e for e in nat_eps if norm(re.sub(r'\$\{[^}]+\}','*',e)) not in backend_norm)
# --- per product area rollup ---
area_web=Counter(r['product_area'] for r in routes if r['surface']=='page')
area_api=Counter(r['product_area'] for r in routes if r['surface']=='api')
def area_of(ep):
    seg=ep.strip('/').split('/'); 
    return seg[1] if len(seg)>1 else 'root'
nat_area=Counter(area_of(e) for e in nat_eps)
AREAS=['pulse','business-os','arena','marketplace','messages','reels','live','undx',
       'account','payments','alerts','dashboard','admin','mobile','push','crypto']
rows=[]
for a in AREAS:
    napi=sum(v for k,v in nat_area.items() if k==a)
    bapi=sum(1 for r in routes if r['surface']=='api' and r['path'].startswith('/api/'+a))
    web=sum(1 for r in routes if r['surface']=='page' and r['path'].startswith('/'+a))
    if a=='marketplace': web=sum(1 for r in routes if r['surface']=='page' and 'marketplace' in r['path'])
    if a=='messages': web=sum(1 for r in routes if r['surface']=='page' and ('message' in r['path'] or 'chat' in r['path']))
    if a=='live': web=sum(1 for r in routes if r['surface']=='page' and '/live' in r['path'])
    if a=='undx': web=sum(1 for r in routes if r['surface']=='page' and 'undx' in r['path'])
    if napi and not web: cls='BACKEND+NATIVE_ONLY'
    elif napi and web: cls='PARTIAL'
    elif bapi and not web and not napi: cls='BACKEND_ONLY'
    elif web and not napi: cls='WEB_ONLY'
    else: cls='REVIEW'
    rows.append({'product_area':a,'backend_api_routes':bapi,'native_endpoints_used':napi,
                 'web_page_routes':web,'classification':cls})
with open('reports/web_parity/parity_matrix.csv','w',newline='') as fh:
    w=csv.DictWriter(fh,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
json.dump({'rows':rows,'native_endpoints_not_in_backend':missing,
           'native_endpoint_count':len(nat_eps)},
          open('reports/web_parity/parity_matrix.json','w'),indent=2)
print(f"{'AREA':<14}{'BACKEND':>8}{'NATIVE':>8}{'WEB':>6}  CLASS")
for r in rows:
    print(f"{r['product_area']:<14}{r['backend_api_routes']:>8}{r['native_endpoints_used']:>8}{r['web_page_routes']:>6}  {r['classification']}")
print()
print(f"native endpoints referenced : {len(nat_eps)}")
print(f"NOT found in backend routes : {len(missing)}")
for m in missing[:15]: print("   ",m)
