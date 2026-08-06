#!/usr/bin/env python3
"""Verify pulsesoc-tokens.css: self-consistency + coverage of legacy vars."""
import re,glob,json
tok=open('static/css/pulsesoc-tokens.css',encoding='utf-8').read()
defined={m.group(1) for m in re.finditer(r'^\s*(--[a-z0-9-]+)\s*:',tok,re.M)}
refs={m.group(1) for m in re.finditer(r'var\((--[a-z0-9-]+)',tok)}
dangling=sorted(refs-defined)
# balance check
print("braces balanced:", tok.count('{')==tok.count('}'), f"({tok.count('{')} open / {tok.count('}')} close)")
print("tokens defined :",len(defined))
print("internal refs  :",len(refs))
print("DANGLING refs  :",len(dangling), dangling or '')
# legacy coverage
legacy={}
for f in glob.glob('static/css/*.css'):
    if 'pulsesoc-tokens' in f: continue
    t=open(f,encoding='utf-8',errors='replace').read()
    for m in re.finditer(r'var\((--[a-z0-9-]+)',t): legacy.setdefault(m.group(1),set()).add(f.split('/')[-1])
for f in glob.glob('templates/*.html'):
    t=open(f,encoding='utf-8',errors='replace').read()
    for m in re.finditer(r'var\((--[a-z0-9-]+)',t): legacy.setdefault(m.group(1),set()).add(f.split('/')[-1])
covered=[v for v in legacy if v in defined]
uncov=sorted(v for v in legacy if v not in defined)
print()
print("legacy vars CONSUMED across css+templates:",len(legacy))
print("  covered by token layer :",len(covered))
print("  NOT covered            :",len(uncov))
weight=sorted(((len(legacy[v]),v) for v in uncov),reverse=True)[:20]
print()
print("highest-impact uncovered vars (by file count):")
for n,v in weight: print(f"   {v:<34} used in {n} file(s)")
json.dump({'defined':sorted(defined),'dangling':dangling,
           'legacy_consumed':len(legacy),'covered':len(covered),
           'uncovered':uncov},open('reports/web_parity/token_verify.json','w'),indent=2)
