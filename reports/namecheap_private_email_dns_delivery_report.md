# CoinPilotX Private Email DNS and Delivery Report

Generated: 2026-06-04 20:40 EDT

## Summary

Active DNS for `coinpilotx.app` is hosted in Cloudflare, not Namecheap Advanced DNS. The Cloudflare DNS records were updated for Namecheap Private Email while preserving existing Brevo authentication records and the existing DMARC policy.

## Evidence

- Before screenshot: `reports/dns-evidence/cloudflare-dns-before-2026-06-04.png`
- After screenshot: `reports/dns-evidence/cloudflare-dns-after-2026-06-04.png`

## DNS Changes Made

Removed these Cloudflare records:

| Type | Host | Value | Priority |
|---|---|---|---:|
| MX | `@` | `eforward1.registrar-servers.com` | 10 |
| MX | `@` | `eforward2.registrar-servers.com` | 10 |
| MX | `@` | `eforward3.registrar-servers.com` | 10 |
| MX | `@` | `eforward4.registrar-servers.com` | 15 |
| MX | `@` | `eforward5.registrar-servers.com` | 20 |
| TXT | `@` | `v=spf1 include:spf.efwd.registrar-servers.com ~all` | |

Added these Cloudflare records:

| Type | Host | Value | Priority |
|---|---|---|---:|
| MX | `@` | `mx1.privateemail.com` | 10 |
| MX | `@` | `mx2.privateemail.com` | 10 |
| TXT | `@` | `v=spf1 include:spf.privateemail.com include:spf.brevo.com ~all` | |

Kept these mail-related records:

| Type | Host | Value | Reason |
|---|---|---|---|
| CNAME | `brevo1._domainkey` | `b1.coinpilotx-app.dkim.brevo.com` | Existing Brevo DKIM; app appears to use Brevo for transactional mail. |
| CNAME | `brevo2._domainkey` | `b2.coinpilotx-app.dkim.brevo.com` | Existing Brevo DKIM; app appears to use Brevo for transactional mail. |
| TXT | `@` | `brevo-code:d05f15ab46c96eb17bdfe534e75b0712` | Existing Brevo domain verification. |
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com` | Existing safe monitor policy; kept as requested. |

## Verification

Cloudflare dashboard after reload showed:

- `MX @ 10 mx1.privateemail.com`
- `MX @ 10 mx2.privateemail.com`
- exactly one SPF record:
  `v=spf1 include:spf.privateemail.com include:spf.brevo.com ~all`
- no `eforward*.registrar-servers.com` MX records
- no old `spf.efwd.registrar-servers.com` SPF record

Public DNS-over-HTTPS verification:

| Resolver | MX Result | SPF Result |
|---|---|---|
| Cloudflare DoH | `10 mx1.privateemail.com`, `10 mx2.privateemail.com` | `v=spf1 include:spf.privateemail.com include:spf.brevo.com ~all` |
| Google DoH | `10 mx1.privateemail.com`, `10 mx2.privateemail.com` | `v=spf1 include:spf.privateemail.com include:spf.brevo.com ~all` |

Local terminal `dig` initially returned stale forwarding records because the local DNS path appeared to be cached or proxied. Public DNS-over-HTTPS checks confirmed the updated records.

## DKIM and Mailbox Status

Updated: 2026-06-04 20:49 EDT

Namecheap Private Email DKIM was retrieved from the Private Email panel and added to Cloudflare.

Namecheap-provided DKIM record:

| Type | Host | Value |
|---|---|---|
| TXT | `default._domainkey` | `v=DKIM1;k=rsa;p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA+jlQi5O6EONXp1MKKUdNj/enMBiWcI/dG2WJGv0fEJgm2ekaC9P94ttyCQIg6usFRptsuC1xOLqalFy2+gIck+alpnTzF66a5RpNLEP0/3WLnY/oB5NUx5mzh0K8NGaa6wx0ebuyLhdSdUQ4mJi/sQyDXdldZ2obkTc1toaMBULkI1s5zErpzF5dYJd+V+/qF8efhrBQ+ZJrJMBHEZ5LogYY3x6Fw4a2kE+fkktdRGumELfDmQgV2luc/7S8LM2x2fNKJYyD6dmGVXmQdKXVC0H++HfJPLboHZaPDFrKO7/YVY00Mg+uf+o1pMlTHOlc67p5Fetfp/RGhAofyz9hzQIDAQAB` |

Cloudflare now shows `13 DNS records`, including `TXT default._domainkey`.

Public DNS verification:

| Check | Result |
|---|---|
| `default._domainkey.coinpilotx.app TXT` via Cloudflare DoH | Published |
| `default._domainkey.coinpilotx.app TXT` via Google DoH | Published |
| DKIM key syntax | Valid 2048-bit RSA public key |

Evidence:

- Cloudflare DKIM screenshot: `reports/dns-evidence/cloudflare-dkim-after-2026-06-04.png`
- Namecheap DKIM screenshot: `reports/dns-evidence/namecheap-private-email-dkim-2026-06-04.png`

Mailbox status confirmed in Namecheap Private Email:

| Mailbox | Status |
|---|---|
| `support@coinpilotx.app` | On |
| `security@coinpilotx.app` | On |
| `noreply@coinpilotx.app` | On |

Namecheap still displayed the DNS setup warning immediately after the change. The panel says to allow up to 4 hours for DNS changes to take effect.

## Delivery Tests

Inbound tests to `support@coinpilotx.app`, `security@coinpilotx.app`, and `noreply@coinpilotx.app` were not completed because Gmail access was not available in the browser session.

Outbound test from `support@coinpilotx.app` was completed after mailbox webmail access became available.

| Test | Result |
|---|---|
| From | `support@coinpilotx.app` |
| To | `roodcher@gmail.com` |
| Subject | `CoinPilotX Private Email outbound test - 2026-06-04 20:52 EDT` |
| Body | `Outbound delivery test from support@coinpilotx.app via Namecheap Private Email webmail. Sent 2026-06-04 20:52 EDT. Please verify SPF, DKIM, and DMARC in Gmail message details.` |
| Webmail sent confirmation | Message appears in Private Email Sent folder at 8:53 PM. |

Evidence:

- Sent folder screenshot: `reports/dns-evidence/privateemail-outbound-sent-2026-06-04.png`

Gmail-side authentication details were not inspected because Gmail mailbox access was not available in the browser session. The recipient should open the delivered message in Gmail and check "Show original" / message details for SPF, DKIM, and DMARC pass results.

## Current Status

DNS is configured for Namecheap Private Email inbound delivery at Cloudflare. Once Namecheap Private Email recognizes the updated DNS and the mailboxes are active, inbound delivery should route to Namecheap Private Email instead of the old forwarding service.

Remaining blockers:

- Wait for Namecheap Private Email to re-check DNS, up to 4 hours per its panel.
- Send real Gmail inbound tests.
- Verify the outbound test in Gmail message details for SPF/DKIM/DMARC pass.
