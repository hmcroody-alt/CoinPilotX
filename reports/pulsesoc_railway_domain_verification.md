# PulseSoc Railway Domain Verification

Generated: 2026-06-06

## Railway state

- Project/service inspected: `CoinPilotX`
- Connected branch: `main`
- Auto-deploy: enabled for GitHub pushes to `main`
- Existing production domain preserved: `coinpilotx.app`
- Existing public port preserved: `8080`
- Existing domain note preserved: Cloudflare proxy detected
- Added custom domains:
  - `pulsesoc.com`
  - `www.pulsesoc.com`

Railway currently shows both PulseSoc domains attached to port `8080` with status `Waiting for DNS update`.

## Exact Railway DNS records

For `pulsesoc.com`, Railway required:

| Type | Name | Value |
| --- | --- | --- |
| CNAME | `@` | `5nkht5ni.up.railway.app` |
| TXT | `_railway-verify` | `railway-verify=fd46d525d0328aa67af5ea3aced20d35b42adf5a2e95e8607f190cf593151b19` |

For `www.pulsesoc.com`, Railway required:

| Type | Name | Value |
| --- | --- | --- |
| CNAME | `www` | `mmdv57l0.up.railway.app` |
| TXT | `_railway-verify.www` | `railway-verify=2e61ca08494087353dff59b32288e2dd5a7a042c5c07b48c00b9eaa6b044d725` |

## Namecheap changes

Added only the Railway-required records above in Namecheap Advanced DNS. Existing email and sender-authentication records were preserved, including:

- Private Email mail setting
- Private Email MX handling
- Private Email DKIM
- Brevo verification TXT
- Brevo DKIM CNAME records
- DMARC TXT

No nameservers were changed. No production secrets were viewed or changed. No auth callback settings were changed.

## Evidence

- `reports/pulsesoc-evidence/railway-before-domain-attach-2026-06-06.png`
- `reports/pulsesoc-evidence/railway-pulsesoc-root-dns-records-2026-06-06.png`
- `reports/pulsesoc-evidence/railway-after-www-attach-2026-06-06.png`
- `reports/pulsesoc-evidence/railway-www-dns-records-2026-06-06.png`
- `reports/pulsesoc-evidence/namecheap-before-railway-domain-records-2026-06-06.png`
- `reports/pulsesoc-evidence/namecheap-after-railway-domain-records-2026-06-06.png`
- `reports/pulsesoc-evidence/namecheap-reloaded-railway-records-2026-06-06.png`
- `reports/pulsesoc-evidence/namecheap-expanded-railway-records-2026-06-06.png`
- `reports/pulsesoc-evidence/railway-after-namecheap-dns-save-2026-06-06.png`
- `reports/pulsesoc-evidence/coinpilotx-app-browser-check-2026-06-06.png`
- `reports/pulsesoc-evidence/coinpilotx-routes-browser-check-2026-06-06.png`

## DNS and HTTPS validation

Immediate public and authoritative DNS checks still returned no live answers for the new Railway records during the validation window.

Rechecked on 2026-06-06 after the branding migration:

- `NS pulsesoc.com` resolves to Namecheap PremiumDNS: `pdns1.registrar-servers.com` and `pdns2.registrar-servers.com`.
- `pulsesoc.com` did not return an A/CNAME answer from local DNS, Google DNS, or Cloudflare DNS during the recheck.
- `www.pulsesoc.com` did not return a CNAME answer during the recheck.
- Railway verification TXT records did not return public answers during the recheck.
- `curl` could not resolve `pulsesoc.com` or `www.pulsesoc.com`, so HTTPS/SSL could not be validated yet.
- In-app browser attempts to open PulseSoc URLs failed before page load; this is consistent with DNS/SSL still pending.

Checked:

- `CNAME pulsesoc.com`
- `CNAME www.pulsesoc.com`
- `TXT _railway-verify.pulsesoc.com`
- `TXT _railway-verify.www.pulsesoc.com`
- `MX pulsesoc.com`

Result:

- PulseSoc Railway records are saved in Namecheap UI and visible after reload.
- Namecheap authoritative nameservers had not yet published the new CNAME/TXT answers.
- `pulsesoc.com` HTTPS was not ready yet.
- `www.pulsesoc.com` HTTPS returned an SSL/SNI error while DNS/SSL provisioning was pending.
- `coinpilotx.app` responded through Cloudflare; command-line checks received a Cloudflare challenge page, but in-app browser checks loaded the live app.
- Browser route checks passed for `coinpilotx.app`, `/pulse`, `/pulse/reels`, `/pulse/videos`, `/pulse/premium`, and `/pulse/messages-v2`.

## Current validation status

| Check | Status |
| --- | --- |
| `coinpilotx.app` domain preserved | Pass |
| `coinpilotx.app` app loads in browser | Pass |
| Existing Pulse routes load in browser | Pass |
| Railway `pulsesoc.com` attached | Pass |
| Railway `www.pulsesoc.com` attached | Pass |
| Namecheap Railway records saved | Pass |
| Namecheap mail records preserved | Pass |
| Railway DNS verification | Pending DNS propagation |
| PulseSoc HTTPS certificate | Pending Railway DNS verification |
| `https://pulsesoc.com` loads app | Pending DNS/SSL |
| `https://www.pulsesoc.com` redirects/loads | Pending DNS/SSL |

## Latest Browser Route QA

- `https://coinpilotx.app/` loaded in the in-app browser.
- `https://coinpilotx.app/pulse` loaded.
- `https://coinpilotx.app/pulse/reels` loaded.
- `https://coinpilotx.app/pulse/videos` loaded.
- `https://coinpilotx.app/pulse/premium` loaded.
- `https://coinpilotx.app/pulse/messages-v2` loaded while authenticated.
- `https://coinpilotx.app/privacy`, `/terms`, and `/support` loaded.
- `https://pulsesoc.com` and `https://www.pulsesoc.com` remain pending public DNS/SSL readiness.

## Follow-up

Recheck DNS propagation and Railway status after Namecheap authoritative nameservers publish the saved records. Once Railway verifies DNS, wait for SSL provisioning, then verify:

- `https://pulsesoc.com`
- `https://www.pulsesoc.com`
- `https://coinpilotx.app`
- `/`
- `/pulse`
- `/pulse/reels`
- `/pulse/videos`
- `/pulse/premium`
- `/pulse/messages-v2`
- `/login`
- `/logout`
- `/register`

## Stop conditions preserved

No domains were removed, no DNS records outside the Railway-required records were changed, no nameservers were changed, no production secrets were touched, no tokens were rotated, no Git history was rewritten, and no auth callback behavior was changed.
