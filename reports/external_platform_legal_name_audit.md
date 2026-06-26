# External Platform Legal Name Audit

Correct legal company name: `CoinPlotXAI Inc.`

This report separates safe display/legal text from operational identifiers. No external production resource was renamed during this codebase correction because provider-side changes can affect billing, deployments, login, app builds, push delivery, DNS, or webhooks.

| Platform | Current name/reference found | Recommended correction | Risk level | Changed now | Approval needed | Why |
|---|---|---|---:|---|---|---|
| Railway | Project/service names and environment labels may still contain CoinPilotX/CoinPilotXAI-derived names | Only update legal display text if Railway supports a non-operational display field | High | No | Yes | Service/project renames can affect deployment operations, private networking, docs, and team workflows. |
| Cloudflare / DNS | Domains, DNS records, cache routes, R2 buckets may use existing CoinPilotX/CoinPilotXAI identifiers | Do not rename operational DNS/R2 identifiers without a migration plan | High | No | Yes | DNS, buckets, cache, and Worker routes can break production traffic. |
| Brevo | Source default folder display text was corrected to `CoinPlotXAI Inc.` | Review sender profile and template body text in Brevo UI | Medium | Source only | Yes for provider UI | Provider-side sender/template changes need authenticated review and should not change API keys or webhook settings. |
| Stripe | Local billing/legal display text corrected where exact legal phrase existed | Update legal business/invoice display name only if Stripe supports safe display edit | High | No external change | Yes | Stripe products, prices, webhooks, connected accounts, and historical invoices must not be renamed blindly. |
| Firebase | Firebase project/app IDs may use existing identifiers | Do not rename project/app IDs; update only public legal display text if present | High | No | Yes | Project IDs, app IDs, sender IDs, bundle IDs, and push credentials are operational. |
| Expo / EAS | Local store metadata docs corrected; project slugs/IDs not changed | Keep slugs/IDs stable unless app identity migration is planned | High | Local docs only | Yes | Expo app identifiers affect builds, credentials, OTA, and submissions. |
| Apple Developer / App Store Connect | Local App Store metadata docs corrected | Update review notes/company copy only after checking App Store Connect fields | Medium | Local docs only | Yes | Seller/legal entity and metadata edits should be made deliberately and verified before resubmission. |
| Google Play Console | Local Play Store metadata docs corrected | Update public app/legal copy in console if editable without changing package identity | Medium | Local docs only | Yes | Package names and app identity must not change; store text can be updated with review. |
| GitHub | Repository/project names remain unchanged | Do not rename repository unless explicitly approved | High | No | Yes | Repo rename can break remotes, CI, webhooks, deploy integrations, docs, and GitHub auth workflows. |
| LiveKit | No safe local legal display field identified | Leave operational room/server identifiers untouched | High | No | Yes | Live service identifiers and webhooks may affect streaming. |
| Mux | No safe local legal display field identified | Leave playback/signing/webhook identifiers untouched | High | No | Yes | Media processing and webhook integrations are production-sensitive. |
| PostHog / Analytics | Analytics/cache identifiers such as `coinpilotxai_session_id` remain untouched | Rename only with a backward-compatible analytics migration | Medium | No | Yes | Changing keys can split metrics and break session continuity. |
| Email/SMS/Push Providers | Local exact legal copy corrected where present | Review provider dashboards for sender/display text only | Medium | Source only | Yes | Sender identities, templates, and verified domains should be updated through provider UI with delivery checks. |
| Cloudflare R2 | Bucket/storage names untouched | Do not rename bucket names without storage migration | High | No | Yes | Bucket renames can break uploads/media delivery. |

## Recommended External Next Steps

1. Review provider dashboards one at a time and update only customer-facing legal display fields to `CoinPlotXAI Inc.`.
2. Do not change identifiers that appear in URLs, credentials, API routes, webhook endpoints, DNS, bundle IDs, or package names.
3. After each external text-only update, verify login, billing, push, uploads, and app review flows.
