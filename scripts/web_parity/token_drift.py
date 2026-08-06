#!/usr/bin/env python3
"""Compare native design tokens against every web CSS custom property."""
import re,json,glob,colorsys
nat=dict(re.findall(r'(\w+):\s*"([^"]+)"',open('mobile-native/src/theme/colors.ts').read()))
web={}
for f in glob.glob('static/css/*.css'):
    for m in re.finditer(r'(--[a-z0-9-]+)\s*:\s*([^;]+);',open(f,encoding='utf-8',errors='replace').read()):
        web.setdefault(m.group(1).strip(),set()).add((m.group(2).strip(),f.split('/')[-1]))
def rgb(c):
    c=c.strip()
    if c.startswith('#'):
        c=c[1:]
        if len(c)==3: c=''.join(ch*2 for ch in c)
        if len(c)>=6: return tuple(int(c[i:i+2],16) for i in (0,2,4))
    m=re.match(r'rgba?\(([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)',c)
    if m: return tuple(int(float(g)) for g in m.groups()[:3])
    return None
def dist(a,b): return sum((x-y)**2 for x,y in zip(a,b))**.5
print("native tokens :",len(nat))
print("web css files :",len(glob.glob('static/css/*.css')))
print("web var names :",len(web))
multi={k:v for k,v in web.items() if len({x[0] for x in v})>1}
print("web vars defined with CONFLICTING values:",len(multi))
print()
print("=== nearest web equivalent for each native token ===")
print(f"{'NATIVE':<16}{'VALUE':<24}{'NEAREST WEB VAR':<26}{'VALUE':<22}{'ΔRGB'}")
report=[]
for k,v in nat.items():
    nv=rgb(v)
    if not nv: continue
    best=None
    for wk,ws in web.items():
        for wval,_ in ws:
            wv=rgb(wval)
            if not wv: continue
            d=dist(nv,wv)
            if best is None or d<best[0]: best=(d,wk,wval)
    if best:
        flag='  EXACT' if best[0]==0 else ('  drift' if best[0]<40 else '  MISMATCH')
        print(f"{k:<16}{v:<24}{best[1]:<26}{best[2][:20]:<22}{best[0]:>6.1f}{flag}")
        report.append({'native':k,'native_value':v,'nearest_web_var':best[1],
                       'web_value':best[2],'rgb_distance':round(best[0],1)})
json.dump({'native':nat,'web_var_count':len(web),
           'conflicting_web_vars':sorted(multi),'mapping':report},
          open('reports/web_parity/token_drift.json','w'),indent=2)
print()
print("sample conflicting web vars:")
for k in sorted(multi)[:8]:
    print(f"  {k}: "+" | ".join(sorted({x[0][:26] for x in multi[k]})[:3]))
