# PulseSoc Brevo Verification

Generated: 2026-06-06

## Result

Brevo now shows both domains authenticated:

- `pulsesoc.com`
- `coinpilotx.app`

## PulseSoc records requested by Brevo

| Purpose | Type | Host |
| --- | --- | --- |
| Brevo verification | TXT | `@` |
| DKIM 1 | CNAME | `brevo1._domainkey` |
| DKIM 2 | CNAME | `brevo2._domainkey` |
| DMARC | TXT | `_dmarc` |

## PulseSoc senders

The following senders were added and verified in Brevo:

- `noreply@pulsesoc.com`
- `support@pulsesoc.com`
- `security@pulsesoc.com`

Existing `coinpilotx.app` sender/domain configuration was preserved.

## Public DNS caveat

The Brevo DKIM CNAME records are publicly resolving. The Brevo root TXT and DMARC TXT were saved in Namecheap, and Brevo accepted the domain as authenticated, but those TXT records were not visible in authoritative/public DNS during the final check window.

## Evidence

- `reports/pulsesoc-evidence/brevo-domains-before-2026-06-06.png`
- `reports/pulsesoc-evidence/brevo-pulsesoc-required-records-2026-06-06.png`
- `reports/pulsesoc-evidence/brevo-pulsesoc-authenticated-2026-06-06.png`
- `reports/pulsesoc-evidence/brevo-domains-after-2026-06-06.png`
- `reports/pulsesoc-evidence/brevo-pulsesoc-senders-verified-2026-06-06.png`
