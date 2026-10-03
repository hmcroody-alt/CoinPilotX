import html, json, re, time, urllib.request

UA = 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
urls = [l.strip() for l in open('product_urls.txt') if l.strip()]
rows = []
for u in urls:
    req = urllib.request.Request(u, headers={'User-Agent': UA})
    try:
        body = urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'replace')
    except Exception as e:
        rows.append({'url': u, 'err': str(e)}); continue
    r = {'url': u, 'bytes': len(body)}
    m = re.search(r"data-mkt-variants='([^']*)'", body)
    if m:
        try:
            v = json.loads(html.unescape(m.group(1)))
            r['nvar'] = len(v)
            r['vprices'] = sorted({x.get('price') for x in v})
            r['vavail'] = sorted({x.get('available') for x in v})
            r['vstock'] = sorted({x.get('stock_label') for x in v})
        except Exception as e:
            r['varerr'] = str(e)
    else:
        r['nvar'] = 0
    # JSON-LD offer
    for blk in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S):
        if '"Offer"' in blk:
            try:
                g = json.loads(blk)
                nodes = g.get('@graph', [g]) if isinstance(g, dict) else g
                for n in nodes:
                    if isinstance(n, dict) and n.get('@type') == 'Product':
                        o = n.get('offers') or {}
                        r['ld_price'] = o.get('price'); r['ld_cur'] = o.get('priceCurrency')
                        r['ld_avail'] = (o.get('availability') or '').rsplit('/', 1)[-1]
                        r['ld_name'] = (n.get('name') or '')[:50]
                        r['ld_desc_len'] = len(n.get('description') or '')
                        r['ld_img'] = len(n.get('image') or []) if isinstance(n.get('image'), list) else (1 if n.get('image') else 0)
                        r['ld_sku'] = n.get('sku')
                        r['ld_keys'] = sorted(n.keys())
            except Exception as e:
                r['lderr'] = str(e)
    r['imgs'] = len(set(re.findall(r'<img[^>]*src=["\']([^"\']+)', body)))
    r['prodlinks'] = len({x for x in re.findall(r'href=["\'][^"\']*?/pulse/marketplace/(\d+)', body) if x not in u})
    r['visible_price'] = sorted(set(re.findall(r'data-mkt-price[^>]*>\s*([^<]{1,20})', body)))
    rows.append(r)
    time.sleep(0.7)
json.dump(rows, open('catalog.json', 'w'), indent=1)
print('rows', len(rows))
