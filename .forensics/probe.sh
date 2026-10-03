#!/bin/bash
# Agent 1 search forensics: bounded anonymous Googlebot probe of public surfaces.
# Read-only. 1s pacing. Records status/redirect/canonical/robots/title/size.
UA='Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
OUT=/Users/hmcherie/Desktop/cpx-searchforensics/.forensics/bodies
mkdir -p "$OUT"
printf 'PATH\tSTATUS\tREDIR\tBYTES\tROBOTS\tCANONICAL\tTITLE\n'
i=0
while IFS= read -r p; do
  [ -z "$p" ] && continue
  i=$((i+1))
  f="$OUT/b$i.html"
  code=$(curl -s -m 25 -A "$UA" -o "$f" -D "$OUT/h$i.txt" -w '%{http_code}' "https://pulsesoc.com$p")
  loc=$(grep -i '^location:' "$OUT/h$i.txt" | head -1 | sed 's/^[Ll]ocation: *//' | tr -d '\r')
  bytes=$(wc -c < "$f" | tr -d ' ')
  # robots can come from header or meta; meta may use single OR double quotes
  rh=$(grep -i '^x-robots-tag:' "$OUT/h$i.txt" | head -1 | sed 's/^[^:]*: *//' | tr -d '\r')
  rm=$(grep -o -i "<meta[^>]*name=['\"]robots['\"][^>]*>" "$f" | head -1 | grep -o -i "content=['\"][^'\"]*['\"]" | sed "s/[Cc]ontent=['\"]//;s/['\"]$//")
  robots="${rh:+hdr:$rh }${rm:+meta:$rm}"
  can=$(grep -o -i "<link[^>]*rel=['\"]canonical['\"][^>]*>" "$f" | head -1 | grep -o -i "href=['\"][^'\"]*['\"]" | sed "s/[Hh]ref=['\"]//;s/['\"]$//")
  title=$(grep -o -i '<title>[^<]*</title>' "$f" | head -1 | sed 's/<[^>]*>//g' | cut -c1-58)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$p" "$code" "${loc:--}" "$bytes" "${robots:--}" "${can:--}" "${title:--}"
  sleep 1
done
