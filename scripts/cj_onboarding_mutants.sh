#!/usr/bin/env bash
# Mutation check for the CJ onboarding + live-connection contract.
#
# Each mutant reverses one guarantee this mission is about. A mutant that still
# passes its test means the test asserts nothing, so PASS here means "the suite
# noticed", i.e. the mutated run FAILED. Every file is restored from a byte copy
# before the next mutant, and on any exit.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
API="$ROOT/mobile-native/src/api/dropshipping.ts"
SCREEN="$ROOT/mobile-native/src/screens/dropshipping/ConnectSupplierScreen.tsx"
CJ="$ROOT/services/business_os/suppliers/cj.py"
CONN="$ROOT/services/business_os/suppliers/connections.py"
BAK=$(mktemp -d)
for f in "$API" "$SCREEN" "$CJ" "$CONN"; do cp "$f" "$BAK/$(basename "$f")"; done
restore() { for f in "$API" "$SCREEN" "$CJ" "$CONN"; do cp "$BAK/$(basename "$f")" "$f"; done; }
trap 'restore; rm -rf "$BAK"' EXIT

FAILED=0
JEST_SPEC="src/screens/dropshipping/__tests__/DropshippingScreens.test.tsx"

# Replace a literal substring in a file. Fails loudly if it is not present, so a
# mutant can never silently pass by having mutated nothing.
mutate() {
  python3 - "$1" "$2" "$3" <<'PY'
import sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path).read()
if old not in text:
    sys.exit("MUTANT TARGET ABSENT in %s: %r" % (path, old[:70]))
open(path, "w").write(text.replace(old, new, 1))
PY
}

run() { # run <n> <description> <suite> <test-filter>
  local n="$1" desc="$2" suite="$3" filter="$4" out status
  if [ "$suite" = jest ]; then
    out=$(cd "$ROOT/mobile-native" && npx jest "$JEST_SPEC" -t "$filter" 2>&1)
    status=$?
    # A `-t` filter that matches nothing exits 0, which reads as a surviving
    # mutant's opposite: a killed one. Renaming a test would silently disarm
    # this whole file, so an empty selection is a hard error, not a pass.
    if ! echo "$out" | grep -qE 'Tests:.*[1-9][0-9]* (passed|failed)'; then
      echo "  MUTANT $n BROKEN   — no test matched -t '$filter'"
      FAILED=1
      restore
      return
    fi
  else
    out=$("$ROOT/.venv/bin/python" -m pytest "$filter" -q 2>&1)
    status=$?
  fi
  if [ "$status" -ne 0 ]; then
    echo "  MUTANT $n KILLED   — $desc"
  else
    echo "  MUTANT $n SURVIVED — $desc"
    FAILED=1
  fi
  restore
}

echo "CJ onboarding mutation check"
echo

# 1 — CJ's credential goes back to our internal category name.
mutate "$API" 'credentialName: "CJ API key",' 'credentialName: "Supplier access key",'
run 1 "credential renamed back to 'Supplier access key'" jest "name CJ gives it"

# 2 — the invented CJ menu path returns.
mutate "$API" '"Sign in to your CJ Dropshipping account.",' \
  '"Sign in to your CJ Dropshipping account.", "Account → API",'
run 2 "invented 'Account → API' path reintroduced" jest "invents no menu"

# 3 — the merchant is no longer told what happens to their key.
mutate "$SCREEN" '<Text style={styles.cardBody}>{provider.securityNote}</Text>' ''
run 3 "security note removed from the credential step" jest "never kept on the device"

# 4 — the CTA exposes the backend step again.
mutate "$API" 'connectCta: "Connect to CJ"' 'connectCta: "Find my shops"'
run 4 "CTA reverted to the backend's wording" jest "labels the action as connecting to CJ"

# 5 — the key becomes a visible, autocorrected field.
mutate "$SCREEN" '                secureTextEntry
' ''
run 5 "credential field no longer masked" jest "never renders the key back"

# 6 — the V2.0 auth payload key is wrong.
mutate "$CJ" '{"apiKey": api_key}' '{"api_key": api_key}'
run 6 "getAccessToken sends the wrong parameter name" pytest tests/business_os/test_cj_adapter.py

# 7 — settings identity is fetched but not compared.
mutate "$CONN" 'if not isinstance(identity, str) or identity != auth.open_id:' \
  'if not isinstance(identity, str):'
run 7 "settings openId no longer proves the account" pytest tests/business_os/test_cj_connections.py

# 8 — a stored credential is treated as a healthy connection.
mutate "$CONN" '        _verify(adapter, auth, row["external_shop_id"], sensitive_values=credentials.values())' \
  '        pass  # mutant: trust the stored credential'
run 8 "hydrate stops verifying against CJ" pytest tests/business_os/test_cj_connections.py

echo
if [ "$FAILED" -eq 0 ]; then echo "All 8 mutants killed."; else echo "SURVIVORS PRESENT — a guarantee is untested."; fi
exit "$FAILED"
