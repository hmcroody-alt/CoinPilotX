#!/bin/bash
# Mutation battery for the listing-readiness engine.
#
# Each mutant is a plausible wrong version of the engine -- the kind a future
# edit could introduce. A mutant that SURVIVES the test file names a rule no
# test defends. Every mutant is ast.parse-verified before it counts: a mutant
# that does not compile proves nothing at all.
set -u
cd "$(dirname "$0")/.."

SRC="services/business_os/marketplace/listing_readiness.py"
TESTS="tests/business_os/test_listing_readiness.py"
BAK="$(mktemp)"
cp "$SRC" "$BAK"
trap 'cp "$BAK" "$SRC"; rm -f "$BAK"; echo; echo "[restored $SRC]"' EXIT

run_mutant() {
  local name="$1"; shift
  cp "$BAK" "$SRC"
  "$@"
  if ! .venv/bin/python3 -c "import ast,sys; ast.parse(open('$SRC').read())" 2>/dev/null; then
    echo "=== $name: MALFORMED -- not evidence, fix the mutation ==="
    return
  fi
  if diff -q "$BAK" "$SRC" >/dev/null; then
    echo "=== $name: NO-OP -- the edit did not apply ==="
    return
  fi
  local out
  out=$(.venv/bin/python3 -m pytest "$TESTS" -q 2>&1 | tail -20)
  if echo "$out" | grep -q "failed"; then
    echo "=== $name: CAUGHT ==="
    echo "$out" | grep "^FAILED" | sed 's/^/    /'
  else
    echo "=== $name: SURVIVED  <-- no test defends this rule ==="
    echo "$out" | tail -2 | sed 's/^/    /'
  fi
}

py() { .venv/bin/python3 - "$SRC" "$@"; }

# A: unknown becomes empty -- the client's own bug, ported to the server.
run_mutant "A unknown folds into zero" .venv/bin/python3 -c "
import re,sys
p='$SRC'; s=open(p).read()
s=s.replace('''    if raw is None:
        return [UNKNOWN_INVENTORY]
    if isinstance(raw, str) and raw.strip() == \"\":
        return [UNKNOWN_INVENTORY]''','''    if raw is None or _text(raw) == \"\":
        return [UNKNOWN_INVENTORY]''')
open(p,'w').write(s)"

# B: unknown stops failing closed for checkout.
run_mutant "B unknown no longer blocks checkout" .venv/bin/python3 -c "
p='$SRC'; s=open(p).read()
s=s.replace('''    RESTRICTED_PRODUCT, OUT_OF_STOCK,''','''    RESTRICTED_PRODUCT, OUT_OF_STOCK,  # UNKNOWN_INVENTORY removed''')
s=s.replace('''    UNKNOWN_INVENTORY,
})''','''})''')
open(p,'w').write(s)"

# C: any non-empty label counts as a price -- \"Request access\" becomes money.
run_mutant "C a wordy label counts as a price" .venv/bin/python3 -c "
p='$SRC'; s=open(p).read()
s=s.replace('''    label = _text(price_label)
    return any(ch.isdigit() for ch in label)''','''    return bool(_text(price_label))''')
open(p,'w').write(s)"

# D: the threshold drifts, as the client's copy did.
run_mutant "D threshold drifts to 10" .venv/bin/python3 -c "
p='$SRC'; s=open(p).read()
s=s.replace('LOW_STOCK_THRESHOLD = 5','LOW_STOCK_THRESHOLD = 10')
open(p,'w').write(s)"

# E: a shared fault acquires a second name.
run_mutant "E shared code renamed" .venv/bin/python3 -c "
p='$SRC'; s=open(p).read()
s=s.replace('MISSING_PRICE = \"MISSING_PRICE\"','MISSING_PRICE = \"NO_PRICE\"')
open(p,'w').write(s)"

# F: stock blocks publication -- unpublishing a live listing over a restock.
run_mutant "F stock blocks publication" .venv/bin/python3 -c "
p='$SRC'; s=open(p).read()
s=s.replace('    warnings.extend(_stock_codes(listing))','    blockers.extend(_stock_codes(listing))')
open(p,'w').write(s)"

# G: media must come from attached rows -- NO_VALID_MEDIA under a visible photo.
run_mutant "G media requires attached rows" .venv/bin/python3 -c "
p='$SRC'; s=open(p).read()
s=s.replace('''    has_media = bool(media) or bool(_text(listing.get(\"cover_image_url\"))
                                    or _text(listing.get(\"media_url\")))''','''    has_media = bool(media)''')
open(p,'w').write(s)"

# H: a stockless product type starts reporting stock.
run_mutant "H a course can run out of stock" .venv/bin/python3 -c "
p='$SRC'; s=open(p).read()
s=s.replace('''    if not _tracks_stock(listing):
        return []''','''    pass''')
open(p,'w').write(s)"

# I: checkout_ready stops requiring publishable.
run_mutant "I checkout_ready ignores publishable" .venv/bin/python3 -c "
p='$SRC'; s=open(p).read()
s=s.replace('''    checkout_ready = publishable and not any(''','''    checkout_ready = not any(''')
open(p,'w').write(s)"

# J: only the first blocker is reported.
run_mutant "J only the first blocker is reported" .venv/bin/python3 -c "
p='$SRC'; s=open(p).read()
s=s.replace('''        \"blockers\": blockers,''','''        \"blockers\": blockers[:1],''')
open(p,'w').write(s)"

# --- mutants aimed at the type logic that shipped wrong once already ---------

# K: delivery_type consulted again. It is TEXT DEFAULT 'digital' on every row,
# so reading it called the entire store stockless and the inventory half of the
# verdict silently did nothing. This is the bug, restored.
run_mutant "K delivery_type consulted again" .venv/bin/python3 -c "
p='$SRC'; s=open(p).read()
s=s.replace('''        listing.get(\"listing_type\"), listing.get(\"product_type\"))''','''        listing.get(\"delivery_type\"), listing.get(\"product_type\"))''')
open(p,'w').write(s)"

# L: physical joins the stockless list, so nothing ever reports stock.
run_mutant "L physical becomes stockless" .venv/bin/python3 -c "
p='$SRC'; s=open(p).read()
s=s.replace('''STOCKLESS_LISTING_TYPES = (\"digital\", \"service\", \"event\", \"booking\")''','''STOCKLESS_LISTING_TYPES = (\"digital\", \"service\", \"event\", \"booking\", \"physical\")''')
open(p,'w').write(s)"

# M: the legacy vocabulary is dropped, so every course acquires an inventory
# state and loses its checkout.
run_mutant "M legacy stockless types dropped" .venv/bin/python3 -c "
p='$SRC'; s=open(p).read()
s=s.replace('''    if _text(listing.get(\"product_type\")).lower() in LEGACY_STOCKLESS_PRODUCT_TYPES:
        return False''','''    pass''')
open(p,'w').write(s)"
