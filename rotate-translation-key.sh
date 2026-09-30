#!/bin/bash
# Installs a freshly-minted Google service-account key into Railway.
#
# The key is never printed, never echoed, never placed in argv, and never
# written anywhere new. It goes from the file you downloaded, through a pipe,
# into Railway. Nothing about it is displayed at any point.
#
# Usage:  ./rotate-translation-key.sh
set -euo pipefail

LEAKED_KID="3fa125c045a1ee865cb639fa976302d2ccc8d815"
SA_EMAIL="pulsesoc-translation-runtime@project-f9702633-2ab4-4677-84b.iam.gserviceaccount.com"
VAR="GOOGLE_CLOUD_TRANSLATION_CREDENTIALS_JSON"
SERVICES=("CoinPilotX" "email_worker")

cd /Users/hmcherie/Desktop/CoinPilotX

# --- find the key you just downloaded ---------------------------------------
KEY_FILE="$(find "$HOME/Downloads" -maxdepth 1 -name '*.json' -mmin -120 -print0 2>/dev/null \
            | xargs -0 ls -t 2>/dev/null | head -1 || true)"

if [ -z "$KEY_FILE" ]; then
  echo "FAIL: no .json downloaded in the last 2 hours."
  echo "      Create the key first: GCP Console -> Add key -> Create new key -> JSON."
  exit 1
fi
echo "Found: $KEY_FILE"

# --- make sure it is the RIGHT key, without printing it ----------------------
# Reads only the two non-secret identifier fields. The private key itself is
# never read into a variable.
eval "$(python3 - "$KEY_FILE" <<'PY'
import json, sys, shlex
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    print(f"EMAIL=''; KID=''; ERR={shlex.quote(str(e))}"); raise SystemExit
print(f"EMAIL={shlex.quote(str(d.get('client_email','')))}")
print(f"KID={shlex.quote(str(d.get('private_key_id','')))}")
print("ERR=''")
PY
)"

if [ -n "$ERR" ];            then echo "FAIL: not valid JSON ($ERR)"; exit 1; fi
if [ "$EMAIL" != "$SA_EMAIL" ]; then
  echo "FAIL: wrong service account."
  echo "      got:      $EMAIL"
  echo "      expected: $SA_EMAIL"
  exit 1
fi
if [ "$KID" = "$LEAKED_KID" ]; then
  echo "FAIL: this IS the leaked key ($LEAKED_KID). Create a NEW one."
  exit 1
fi

echo "Verified: correct service account, new key id ${KID:0:12}..."
echo

# --- install ----------------------------------------------------------------
for svc in "${SERVICES[@]}"; do
  printf 'Setting %s on %s ... ' "$VAR" "$svc"
  if railway variable set "$VAR" --stdin --service "$svc" < "$KEY_FILE" >/dev/null 2>&1; then
    echo "ok"
  else
    echo "FAILED"
    echo "  Retry manually:  railway variable set $VAR --stdin --service '$svc' < '$KEY_FILE'"
    exit 1
  fi
done

echo
echo "Both services updated. Railway is redeploying."
echo
echo "NEXT:"
echo "  1. Tell Claude it is done - it will verify the fingerprint flipped on both."
echo "  2. Only AFTER that passes, delete key $LEAKED_KID in the GCP console."
echo "  3. Then:  rm '$KEY_FILE'"
