# PulseSoc Domain Migration Readiness

Generated: 2026-06-06

## Executive status

PulseSoc is Brevo-ready and mailbox-created, but web routing is not ready.

`coinpilotx.app` remains the stable production domain. `pulsesoc.com` still points to Namecheap redirect/parking infrastructure and is not attached to Railway. This is intentional because Railway custom-domain creation and web DNS cutover require explicit approval.

## Current app domain state

- Railway web service: `CoinPilotX`
- Active public domain: `coinpilotx.app`
- Port: `8080`
- Deployment status seen in Railway: online/successful
- `coinpilotx.app` remains protected by Cloudflare.
- Public curl to `coinpilotx.app` and `/pulse` returned Cloudflare challenge HTTP `403`, which confirms Cloudflare is active but is not by itself an origin failure.

## PulseSoc web state

- `pulsesoc.com` DNS provider: Namecheap PremiumDNS
- `pulsesoc.com` root: Namecheap URL forwarding infrastructure
- `www.pulsesoc.com`: Namecheap parking CNAME
- HTTPS for `pulsesoc.com`/`www.pulsesoc.com`: not ready
- Railway custom domain: not attached
- Cloudflare zone: not present

## Recommended Railway path

When approved:

1. Add `pulsesoc.com` as a Railway custom domain on the `CoinPilotX` web service.
2. Add `www.pulsesoc.com` as a Railway custom domain if Railway supports both root and subdomain for the same service.
3. Capture Railway's required DNS target records.
4. Take a fresh Namecheap DNS screenshot.
5. Replace only the current web routing records:
   - root URL redirect
   - `www` parking CNAME
6. Leave all mail records untouched.
7. Wait for Railway certificate provisioning.
8. Validate root, `www`, and `coinpilotx.app`.

## Redirect plan

Target behavior:

- `https://pulsesoc.com` loads the public app or Pulse landing experience.
- `https://www.pulsesoc.com` redirects to `https://pulsesoc.com`.
- `https://coinpilotx.app` remains operational.

## Routes to validate after cutover

- `/`
- `/pulse`
- `/pulse/reels`
- `/pulse/videos`
- `/pulse/live`
- `/pulse/messages`
- `/pulse/premium`
- `/pulse/portfolio`
- `/login`
- `/logout`
- `/register`

## App config state

Safe email-only updates were made:

- support email references now point to `support@pulsesoc.com`
- security email references now point to `security@pulsesoc.com`
- no-reply defaults now point to `noreply@pulsesoc.com`

Preserved intentionally:

- `APP_BASE_URL=https://coinpilotx.app`
- payment/dashboard/account links on `coinpilotx.app`
- webhook/callback URL assumptions
- CDN/media URL assumptions

Do not switch canonical app URL until PulseSoc is attached, HTTPS is provisioned, and auth/payment/webhook callbacks have been tested.
