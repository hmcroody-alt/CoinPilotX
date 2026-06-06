# PulseSoc Text Replacement Changes

Date: 2026-06-06

## Changed

- Rebranded public UI labels from CoinPilotXAI/CoinPilotX to Pulse in account, dashboard, search, support, SEO, offline, PWA, and selected app surfaces.
- Updated footer language to Pulse — Powered by CoinPilotXAI on affected public templates.
- Updated public-domain URLs in SEO metadata, sitemap, robots, llms.txt, schema helpers, transactional emails, alert links, Telegram/account links, and payment-support copy to https://pulsesoc.com.
- Updated default email sender names and addresses to PulseSoc identities in email defaults and .env.example.
- Updated PWA manifest name/short name/description to Pulse.
- Updated service-worker offline fallback copy to Pulse.
- Updated public SEO content titles from the old legal-company suffix to Pulse.

## Intentionally Kept

- CoinPilotXAI Inc. remains in legal disclaimers, privacy/terms operator language, organization legalName schema, safety notices, and admin/legal contexts.
- coinpilotx.app remains in compatibility and infrastructure areas where removing it could break production continuity.
- cdn.coinpilotx.app remains as the active CDN/storage delivery host.
- Route names such as /dashboard remain unchanged.

## Not Changed

- Auth callback URLs.
- Webhook URLs.
- Secrets or provider tokens.
- Railway custom domain settings.
- Mux webhook/playback settings.
- Stripe settings.
- OAuth settings.
- Forced domain redirect behavior.

## Risk Controls

- Replacement was not a single blind repository-wide rewrite.
- High-risk infrastructure references were classified and left in place.
- Public-facing metadata and user-facing copy were prioritized.
- Legal/company references were preserved where they describe the operator.

## Additional Fixes Found During Validation

- Restored live browser-preview responses to include the safe public playback manifest so clients can see HLS/WebRTC fallback state without exposing stream keys.
- Updated live pipeline audits to match the current preview-only browser publishing state before RTMP/Mux broadcast.
- Fixed `/pulse/messages-v2` unauthenticated access so it redirects cleanly to login instead of reaching a system notice.
