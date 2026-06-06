# PulseSoc Final External Account QA

Generated: 2026-06-06

## Completed

- Public DNS rechecked.
- Namecheap PulseSoc mailboxes confirmed active.
- Brevo login completed through existing browser session.
- `pulsesoc.com` added to Brevo.
- Brevo authentication records added to Namecheap.
- `pulsesoc.com` authenticated in Brevo.
- PulseSoc Brevo senders added and verified.
- `coinpilotx.app` Brevo domain preserved.
- Railway domain readiness inspected.
- Safe app email defaults updated to PulseSoc addresses.
- Production `coinpilotx.app` web routing was not changed.

## Not completed

- Gmail inbound tests: Gmail login required.
- PulseSoc webmail inbox/outbound tests: PulseSoc mailbox login required.
- Railway custom-domain attachment: explicit approval required.
- PulseSoc web routing cutover: depends on Railway domain attachment and DNS target records.

## Final status table

| Requirement | Status |
| --- | --- |
| MX for `pulsesoc.com` | Pass |
| Exactly one SPF | Pass |
| SPF includes Private Email | Pass |
| SPF includes Brevo | Not changed; Brevo did not require SPF and Namecheap Private Email auto-SPF is not safely mergeable without mail-setting conversion |
| Namecheap DKIM | Pass |
| Brevo DKIM | Pass |
| DMARC monitor policy | Saved and Brevo-accepted; public DNS pending |
| `support@pulsesoc.com` mailbox | Exists |
| `security@pulsesoc.com` mailbox | Exists |
| `noreply@pulsesoc.com` mailbox | Exists |
| Brevo PulseSoc domain | Authenticated |
| Brevo PulseSoc senders | Verified |
| Railway `coinpilotx.app` | Present and online in Railway |
| Railway `pulsesoc.com` | Pending approval |
| PulseSoc HTTPS | Not ready |
| App email defaults | Updated |
| Secrets exposed | No |

## Approval needed

To continue into production web routing, approve this specific action:

Add `pulsesoc.com` and `www.pulsesoc.com` as Railway custom domains for the `CoinPilotX` service, then update the two Namecheap web records using Railway's required DNS targets while preserving all mail records.
