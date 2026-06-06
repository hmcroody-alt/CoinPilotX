# PulseSoc Railway Domain Readiness

Generated: 2026-06-06

## Current Railway state

- Project: `CoinPilotX`
- Web service: `CoinPilotX`
- Current public domain: `coinpilotx.app`
- Port: `8080`
- Current domain note: Cloudflare proxy detected
- PulseSoc domains attached: none

## Not changed

I did not add `pulsesoc.com` or `www.pulsesoc.com` to Railway because the mission calls for approval before this step.

## Exact next action after approval

1. Open Railway service settings for the `CoinPilotX` service.
2. Use `Custom Domain`.
3. Add `pulsesoc.com`.
4. Copy the DNS target Railway provides.
5. Add `www.pulsesoc.com` if Railway permits a second custom domain for the same service.
6. Copy the DNS target Railway provides.
7. Update only the Namecheap web routing records after taking a fresh before screenshot.
8. Wait for Railway HTTPS certificate provisioning.

## DNS records likely to change

Current records that would be replaced after Railway provides targets:

- `@` URL redirect to Namecheap forwarding
- `www` CNAME to Namecheap parking

Mail records should not be changed during web cutover.

## Validation after approval/cutover

- `https://pulsesoc.com`
- `https://www.pulsesoc.com`
- `https://coinpilotx.app`
- `/pulse`
- `/pulse/videos`
- `/login`
- `/register`
- `/logout`
