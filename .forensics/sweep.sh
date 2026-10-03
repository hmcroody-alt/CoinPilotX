#!/bin/bash
# Validate every sitemap URL: status, redirect, robots directive, canonical self-agreement.
# Paced at 0.4s, single-threaded, read-only GETs. 148 URLs.
UA='Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
cd /Users/hmcherie/Desktop/cpx-searchforensics/.forensics
printf 'STATUS\tROBOTS\tCANON_AGREE\tBYTES\tURL\n' > sweep.tsv
while IFS= read -r u; do
  [ -z "$u" ] && continue
  body=$(mktemp); hdr=$(mktemp)
  code=$(curl -s -m 25 -A "$UA" -o "$body" -D "$hdr" -w '%{http_code}' "$u")
  bytes=$(wc -c < "$body" | tr -d ' ')
  rm_=$(grep -o -i "<meta[^>]*name=['\"]robots['\"][^>]*>" "$body" | head -1 | grep -o -i "content=['\"][^'\"]*['\"]" | sed "s/[Cc]ontent=['\"]//;s/['\"]$//")
  rh=$(grep -i '^x-robots-tag:' "$hdr" | head -1 | sed 's/^[^:]*: *//' | tr -d '\r')
  robots="${rm_:-${rh:-NONE}}"
  can=$(grep -o -i "<link[^>]*rel=['\"]canonical['\"][^>]*>" "$body" | head -1 | grep -o -i "href=['\"][^'\"]*['\"]" | sed "s/[Hh]ref=['\"]//;s/['\"]$//")
  if [ -z "$can" ]; then agree=NO_CANONICAL
  elif [ "$can" = "$u" ]; then agree=SELF
  else agree="CROSS->$can"; fi
  printf '%s\t%s\t%s\t%s\t%s\n' "$code" "$robots" "$agree" "$bytes" "$u" >> sweep.tsv
  rm -f "$body" "$hdr"
  sleep 0.4
done < all_sitemap_urls.txt
echo "SWEEP COMPLETE: $(( $(wc -l < sweep.tsv) - 1 )) urls"
