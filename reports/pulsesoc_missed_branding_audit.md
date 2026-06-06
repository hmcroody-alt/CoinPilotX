# PulseSoc Missed Branding Audit

## Scope

Audited public-facing app text, page titles, metadata, manifests, Roast Battle, Pulse Live defaults, post titles, and visible Pulse UI fallbacks for old `CoinPilotXAI Pulse` style branding.

## Categories

### Legal/operator references kept

- `CoinPilotXAI Inc.` remains in privacy, terms, support, admin, payments, security, and legal disclaimers.
- `Pulse — Powered by CoinPilotXAI` remains valid footer/legal support text.

### Public-facing UI changed

- Roast Battle browser title and preview title now use `Pulse Roast Battle`.
- Roast Battle Pulse shell header now shows `Pulse`, not `CoinPilotXAI Pulse`.
- Pulse social shell and section shell titles now end with `| Pulse`.
- Public post pages now end with `| Pulse`.
- Pulse Live default titles now use `Pulse Live`.
- Default public post fallback now uses `Pulse Post`.
- Feed share metadata now uses `Pulse`.
- The Pulse disclaimer now starts with `Pulse is a community discussion space`.

### Internal/infrastructure references kept

- `coinpilotx.app` remains in compatibility redirects, CDN/R2 media references, reports, and production diagnostics.
- Admin, UNDX, Telegram bot, and legacy infrastructure references to `CoinPilotX`/`CoinPilotXAI` were not blindly replaced because they are operational or legal surfaces.

## Result

The confirmed public Roast Battle old-branding issue is fixed, and a new audit blocks the old public tokens from returning.
