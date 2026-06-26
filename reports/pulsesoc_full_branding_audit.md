# PulseSoc Full-System Branding Audit

Date: 2026-06-06

## Branding Rules Applied

- Public product/brand: Pulse
- Public domain: PulseSoc.com / https://pulsesoc.com
- Legal operator: CoinPlotXAI Inc.
- Public footer pattern: Pulse — Powered by CoinPilotXAI
- Public email addresses: support@pulsesoc.com, security@pulsesoc.com, noreply@pulsesoc.com

## Classification Summary

### A. Public-facing user text updated

- Public homepage, app shell, account/login/register shell, dashboard shell, support page, search page, SEO landing shell, offline pages, PWA manifests, public sitemap/robots/LLM metadata, email copy, selected Telegram/account links, alert copy, command-router labels, and public SEO content now lead with Pulse.
- Page titles, Open Graph site names, Twitter metadata, canonical URLs, sitemap URLs, and manifest names were shifted to Pulse and https://pulsesoc.com where migration-safe.

### B. Legal/company text kept

- Legal pages and legal disclaimers continue to identify CoinPlotXAI Inc. as the operator.
- Terms, privacy, billing/payment disclaimers, safety disclaimers, and organization legalName schema keep CoinPlotXAI Inc.
- Admin-only and legal/admin invitation text still uses CoinPlotXAI Inc. where appropriate.

### C. Infrastructure/internal config kept

- Existing coinpilotx.app support remains in compatibility areas.
- CDN references to cdn.coinpilotx.app were preserved because they are storage/delivery infrastructure, not public branding text.
- Existing old-domain host compatibility and tests were not removed.
- Auth callback behavior, webhook URLs, secrets, Railway settings, Mux settings, Stripe settings, OAuth behavior, and tokens were not changed.

### D. Email/contact text updated

- Default support/security/noreply sender addresses point to PulseSoc addresses.
- User-facing email templates now say Pulse/PulseSoc where appropriate.
- Old coinpilotx.app email addresses were not found in active source after the migration pass.

### E. SEO/meta/canonical updated carefully

- Public canonical URLs now prefer https://pulsesoc.com.
- sitemap.xml, robots.txt, llms.txt, public JSON-LD schema helpers, Open Graph URLs, and Twitter metadata were updated to the PulseSoc domain where safe.
- Private/noindex account pages were updated for display branding while preserving route behavior.

### F. Unknown/deferred

- Historical docs and audit fixtures may still mention old domains by design.
- CDN migration is deferred; changing cdn.coinpilotx.app would be an infrastructure change.
- Full redirect policy from coinpilotx.app to pulsesoc.com is not implemented because approval was not requested.

## Safety Notes

- No secrets were viewed, changed, rotated, or printed.
- No DNS records, Railway settings, Mux settings, Brevo settings, Stripe settings, or OAuth callback settings were changed during this branding pass.
- coinpilotx.app compatibility was intentionally preserved.

## Validation Completed

- Python compile passed for `bot.py`, `pulse_communications_v2`, `services`, `seo`, and `scripts`.
- JavaScript parse passed for non-vendor static JavaScript.
- Full platform audit passed.
- Site functional audit passed with expected protected-route warnings for restricted surfaces.
- Performance audit passed with existing polling warnings only.
- Mobile experience and mobile PWA audits passed.
- Account signup and account recovery audits passed.
- Local auth smoke test passed for `/`, `/login`, `/signup`, `/forgot-password`, `/privacy`, `/terms`, `/support`, `/pulse`, `/pulse/reels`, `/pulse/videos`, `/pulse/premium`, and `/pulse/messages-v2`.
- SEO/meta/email verification passed for PulseSoc canonical URLs, sitemap, robots, manifests, schema, and PulseSoc email defaults.
- `git diff --check` passed.

## Browser QA

- `coinpilotx.app` still loads in the in-app browser.
- Browser checks loaded `/pulse`, `/pulse/reels`, `/pulse/videos`, `/pulse/premium`, `/pulse/messages-v2`, `/privacy`, `/terms`, and `/support` on the existing production domain.
- Production still showed pre-deploy branding before this commit was pushed, which is expected until Railway deploys the new commit.
- `pulsesoc.com` and `www.pulsesoc.com` could not be browser-verified because public DNS/SSL was still not resolving during this check.
