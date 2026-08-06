#!/usr/bin/env python3
"""Extract native screens, their nav routes, and the backend endpoints they call."""
import json,os,re,subprocess
from collections import defaultdict
ROOT='mobile-native/src'
def files(pat):
    return subprocess.check_output(['find',ROOT,'-name',pat]).decode().split('\n')
# 1. endpoints referenced anywhere in native source
ep_by_file=defaultdict(set)
EP=re.compile(r"['\"`](/api/[A-Za-z0-9_\-/${}.:<>]+)['\"`]")
allf=[f for f in subprocess.check_output(['find',ROOT,'-name','*.ts','-o','-name','*.tsx']).decode().split('\n') if f]
for f in allf:
    try: t=open(f,encoding='utf-8',errors='replace').read()
    except: continue
    for m in EP.finditer(t): ep_by_file[f].add(m.group(1))
# 2. api module -> endpoints
api_eps={os.path.basename(f)[:-3]:sorted(v) for f,v in ep_by_file.items() if '/api/' in f}
# 3. screens -> which api modules they import
screens={}
for f in allf:
    b=os.path.basename(f)
    if not b.endswith('Screen.tsx'): continue
    t=open(f,encoding='utf-8',errors='replace').read()
    mods=set(re.findall(r"from\s+['\"][^'\"]*api/([A-Za-z0-9_]+)['\"]",t))
    eps=set(ep_by_file.get(f,()))
    for m in mods: eps.update(api_eps.get(m,()))
    screens[b[:-4]]={'file':f,'api_modules':sorted(mods),'endpoints':sorted(eps)}
# 4. navigator route names
navs=defaultdict(list)
for f in allf:
    if 'nav' not in f.lower() and 'Navigator' not in f: continue
    t=open(f,encoding='utf-8',errors='replace').read()
    for m in re.finditer(r'name=\{?["\']([A-Za-z0-9_]+)["\']\}?\s+component=\{([A-Za-z0-9_]+)\}',t):
        navs[m.group(2)].append(m.group(1))
for s in screens: screens[s]['nav_names']=sorted(set(navs.get(s,[])))
json.dump({'screens':screens,'api_modules':api_eps},open('reports/web_parity/native_map.json','w'),indent=2)
tot=len({e for s in screens.values() for e in s['endpoints']})
print("native screens        :",len(screens))
print("api modules           :",len(api_eps))
print("distinct endpoints ref:",tot)
print("screens with nav name :",sum(1 for s in screens.values() if s['nav_names']))
print("screens with 0 endpts :",sum(1 for s in screens.values() if not s['endpoints']))
