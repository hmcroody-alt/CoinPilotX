# PulseSoc External Accounts QA

Generated: 2026-06-06

## Scope

This pass completed the remaining safe external-account setup for PulseSoc/CoinPilotX. I did not request, store, screenshot, or print passwords, 2FA codes, API keys, SMTP passwords, webhook secrets, Mux tokens, stream keys, private keys, or secret environment values.

## Completed changes

### Namecheap

- Confirmed `pulsesoc.com` uses Namecheap PremiumDNS.
- Confirmed active Namecheap Private Email mailboxes:
  - `support@pulsesoc.com`
  - `security@pulsesoc.com`
  - `noreply@pulsesoc.com`
- Added Brevo authentication records:
  - root TXT Brevo verification code
  - `brevo1._domainkey` CNAME
  - `brevo2._domainkey` CNAME
- Updated the single existing `_dmarc` TXT row to Brevo's monitor-only DMARC value.
- Preserved existing Private Email mail service and Namecheap Private Email DKIM.
- Did not delete DNS records.

### Brevo

- Added `pulsesoc.com` as a Brevo sender domain.
- Verified `pulsesoc.com` domain authentication in Brevo.
- Confirmed `coinpilotx.app` remains authenticated.
- Added and verified PulseSoc senders:
  - `noreply@pulsesoc.com`
  - `support@pulsesoc.com`
  - `security@pulsesoc.com`
- Preserved existing `coinpilotx.app` Brevo sender/domain records.

### Railway

- Confirmed the `CoinPilotX` web service is online and still attached to `coinpilotx.app` on port `8080`.
- Confirmed `pulsesoc.com` and `www.pulsesoc.com` are not attached.
- Did not add Railway domains because the mission explicitly requires approval before this step.

### App Config

- Updated non-secret app defaults and visible support/security email references from `coinpilotx.app` email addresses to PulseSoc email addresses.
- Preserved `coinpilotx.app` URLs and canonical app base URL defaults so existing production routing, auth callbacks, payment return URLs, and webhooks are not accidentally switched.

## Evidence

- `reports/pulsesoc-evidence/brevo-domains-before-2026-06-06.png`
- `reports/pulsesoc-evidence/brevo-pulsesoc-required-records-2026-06-06.png`
- `reports/pulsesoc-evidence/namecheap-before-brevo-records-2026-06-06.png`
- `reports/pulsesoc-evidence/namecheap-after-brevo-records-2026-06-06.png`
- `reports/pulsesoc-evidence/brevo-pulsesoc-authenticated-2026-06-06.png`
- `reports/pulsesoc-evidence/brevo-domains-after-2026-06-06.png`
- `reports/pulsesoc-evidence/brevo-pulsesoc-senders-verified-2026-06-06.png`

## Remaining blockers

1. Gmail is not logged in, so live Gmail-to-PulseSoc inbound tests could not be sent.
2. Private Email webmail is logged into an existing CoinPilotX mailbox, not the three PulseSoc mailboxes, so PulseSoc inbox/outbound tests require manual mailbox login.
3. Namecheap publishes the new Brevo DKIM CNAME records publicly, but the newly added root Brevo TXT and edited DMARC TXT were still not visible from authoritative DNS during the validation window.
4. Railway custom-domain attachment is pending explicit approval.

## Current readiness

| Area | Status |
| --- | --- |
| Namecheap ownership/DNS | Ready |
| Private Email mailboxes | Created and active |
| MX | Ready |
| SPF | Private Email SPF published; Brevo SPF merge is not safely editable while Private Email automatic mail settings are active |
| Private Email DKIM | Ready |
| Brevo DKIM | Added and publicly resolving |
| DMARC | Saved in Namecheap and accepted by Brevo, but not yet visible in public/authoritative DNS |
| Brevo domain | Authenticated |
| Brevo senders | Verified |
| Railway PulseSoc domains | Not attached |
| PulseSoc web routing | Not ready |
