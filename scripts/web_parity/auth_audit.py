import ast, json, re, sys
src=open('/tmp/bot_snapshot.py',encoding='utf-8',errors='replace').read()
tree=ast.parse(src)
def dn(n):
    if isinstance(n,ast.Call): n=n.func
    p=[]
    while isinstance(n,ast.Attribute): p.append(n.attr); n=n.value
    if isinstance(n,ast.Name): p.append(n.id)
    return '.'.join(reversed(p))
DIRECT={'require_account','require_login','require_admin','require_business',
        'require_premium','require_engineer','current_user','get_current_user',
        'current_account','get_current_account','account_from_session'}
funcs={n.name:n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
callmap={}
direct=set()
for name,n in funcs.items():
    cs=set()
    for s in ast.walk(n):
        if isinstance(s,ast.Call):
            b=dn(s).split('.')[-1]
            if b in DIRECT or re.search(r'current_user|current_account|_state_for_current',b):
                direct.add(name)
            cs.add(b)
    callmap[name]=cs
memo={}
def guarded(name,depth=0):
    if name in direct: return True
    if depth>3 or name not in callmap: return False
    key=(name,depth)
    if key in memo: return memo[key]
    memo[key]=False
    r=any(c!=name and guarded(c,depth+1) for c in callmap[name])
    memo[key]=r
    return r
rows=json.load(open('reports/web_parity/route_table.json'))
pages={}
for r in rows:
    if r['surface']=='page': pages.setdefault(r['handler'],[]).append(r['path'])
g=[h for h in pages if guarded(h)]; u=sorted(h for h in pages if not guarded(h))
out={'page_handlers':len(pages),'auth_enforced':len(g),'no_auth_detected':len(u),
     'unguarded':{h:pages[h] for h in u}}
json.dump(out,open('reports/web_parity/auth_audit.json','w'),indent=2)
print("page handlers      :",len(pages))
print("auth enforced (any):",len(g))
print("no auth detected   :",len(u))
