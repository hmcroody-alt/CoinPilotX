#!/usr/bin/env python3
"""Locate every <head> emission and shell builder in bot.py -> token adoption worklist."""
import re,json,ast
src=open('/tmp/bot_snapshot.py',encoding='utf-8',errors='replace').read()
lines=src.splitlines()
tree=ast.parse(src)
# map line -> enclosing function
owner={}
for n in ast.walk(tree):
    if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
        end=getattr(n,'end_lineno',n.lineno)
        for l in range(n.lineno,end+1): owner[l]=n.name
heads=[]
for i,l in enumerate(lines,1):
    if '<head>' in l or '<head ' in l:
        # does this head block already link a stylesheet?
        window='\n'.join(lines[i-1:i+40])
        css=re.findall(r'/static/css/([a-z0-9_-]+\.css)',window)
        heads.append({'line':i,'function':owner.get(i,'<module>'),
                      'stylesheets':sorted(set(css)),
                      'has_token_layer':'pulsesoc-tokens.css' in css})
shells=sorted({n.name for n in ast.walk(tree)
               if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))
               and re.search(r'_shell$|^.*_page_shell$',n.name)})
json.dump({'head_emissions':heads,'shell_builders':shells},
          open('reports/web_parity/shell_audit.json','w'),indent=2)
print("distinct <head> emissions :",len(heads))
print("already load token layer  :",sum(1 for h in heads if h['has_token_layer']))
print("emit NO stylesheet at all :",sum(1 for h in heads if not h['stylesheets']))
print("shell builder functions   :",len(shells))
print()
print(f"{'LINE':>7}  {'FUNCTION':<42} STYLESHEETS")
for h in heads:
    print(f"{h['line']:>7}  {h['function'][:42]:<42} {','.join(h['stylesheets']) or '(none)'}")
