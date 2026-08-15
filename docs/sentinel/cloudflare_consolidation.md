# PULSESOC SECURITY VENDOR CONSOLIDATION REPORT

**Assessment date:** 2026-08-14  
**Scope:** authenticated Cloudflare account and `coinpilotx.app`; Sentinel is
prepared but external intelligence remains fail-closed and disabled by default.

## A. Cloudflare capabilities already available

The account has one visible full-DNS zone, `coinpilotx.app`, on the **Pro**
plan. Its Security Overview reports that bot traffic, web-application exploit,
DDoS, API-abuse, client-side-abuse, and fraud-protection detections are all
running. In the measured seven-day view, Cloudflare mitigated 26.5% of 25.71k
requests. This is detection/mitigation telemetry, not evidence that every
request or attack is covered.

Available in the dashboard today:

| Capability | Access | Current state / safe use |
| --- | --- | --- |
| DDoS protection, managed WAF, security analytics | INCLUDED (Pro) | Detection categories are running; inspect-only analytics is available. |
| Bot controls / AI crawler controls | INCLUDED/LIMITED | AI-training bots are blocked on all pages. AI Labyrinth and Precursor are off. |
| Rate limiting / custom security rules | INCLUDED/LIMITED | Available, but rule creation or changes can affect production traffic. |
| Turnstile | INCLUDED | Available account-wide; no widget was created by this assessment. |
| Security Insights / infrastructure inventory | INCLUDED | Available; the zone has not yet run an on-demand scan. |
| Log Explorer, analytics, Trace beta, Logpush | INCLUDED/LIMITED | Read-only exploration is available. No new export destination was created. |
| Workers Observability / account and zone analytics | INCLUDED/LIMITED | Available for Cloudflare-hosted workloads and edge visibility; no Worker is attached to this zone. |
| Account audit log and notifications | INCLUDED | Available for administrative evidence and simple alerts. |
| Zero Trust / Access / Gateway | CLOUDFLARE ONE | Product entry point exists; entitlement, seats, IdP configuration, and protected-app inventory have not been established. |
| API Shield, Advanced Bot Management, Account Takeover Protection | ENTERPRISE / entitlement dependent | Do not assume Pro includes these advanced enforcement products. |
| Cloudforce One Threat Intelligence | ADD-ON | Official Cloudflare documentation requires a Cloudforce One subscription; it is not shown as entitled in this account. |

## B. Cloudflare features we should enable

| State | Recommendation | Why / guardrail |
| --- | --- | --- |
| Already configured | Keep managed WAF/DDoS/bot/API/fraud detections running and keep AI-training crawler blocking on all pages. | Existing protection is producing mitigations; no policy was changed. |
| Safe to enable after owner review | Run the Security Insights on-demand scan. | Read-only assessment, but review findings before accepting any remediation. |
| Safe to prepare | Create a Turnstile migration plan for signup, login recovery, Marketplace checkout intent creation, and high-cost AI endpoints. | Server-side token validation and staged measurement first; no blanket blocking. |
| Requires owner approval | Enable Precursor, AI Labyrinth, new WAF/rate-limit rules, Logpush destinations, or notification webhooks. | These alter traffic, disclose telemetry to a destination, or create operational noise. |
| Requires purchase / entitlement | Cloudforce One, API Shield/advanced bot features, or Zero Trust seats beyond available coverage. | Do not purchase, upgrade, or start a trial without owner approval. |

No Cloudflare settings, alert destinations, API tokens, subscriptions, or
traffic behavior were changed in this mission.

## C. Cloudflare consolidation matrix

Replacement is an engineering estimate for the stated capability, not a
marketing claim. “Current access” describes the authenticated account, not a
future quote or a trial.

| Vendor / capability | Cloudflare equivalent | Current access | Replacement level | Decision |
| --- | --- | --- | ---: | --- |
| Cloudflare threat intelligence | Security Analytics, WAF/DDoS/bot signals; Cloudforce One for analyst TI, passive DNS, WHOIS/history | Pro + Cloudforce One ADD-ON | 100% for edge signals; 25% for global TI without add-on | REPLACE WITH CLOUDFLARE for edge telemetry; DEFER Cloudforce One |
| Sentry / Datadog | Log Explorer, Logpush, edge analytics, Workers Observability, RUM | INCLUDED/LIMITED | 35% | CLOUDFARE + VENDOR |
| Stripe Radar | WAF, Turnstile, bot/rate signals, request geography | INCLUDED/LIMITED | 30% | CLOUDFARE + VENDOR |
| GitHub Security | None for repository code, dependency alerts, secret scanning, provenance | UNAVAILABLE | 0% | KEEP VENDOR |
| OSV.dev | No equivalent package/version vulnerability matcher | UNAVAILABLE | 0% | FREE SOURCE — KEEP |
| NVD | No authoritative CVE database | UNAVAILABLE | 0% | FREE SOURCE — KEEP |
| CISA KEV | No authoritative actively-exploited catalog | UNAVAILABLE | 0% | FREE SOURCE — KEEP |
| Fingerprint device intelligence | Bot/JS/browser integrity and Access posture, but no consumer stable device graph | LIMITED / CLOUDFLARE ONE | 35% | DEFER VENDOR |
| MaxMind GeoIP | Request country/ASN/IP context at the edge | INCLUDED/LIMITED | 75% | DEFER VENDOR |
| MaxMind minFraud | Pre-payment bot/network context only | LIMITED | 20% | PILOT VENDOR only if Radar + Cloudflare leave a measured gap |
| VirusTotal Enterprise/Premium | Cloudforce One overlaps IP/domain/URL/infrastructure intelligence | ADD-ON | 45% overall; 0% for file hashes/multi-engine verdicts | DEFER VENDOR; retain narrow lookup-only option |
| PagerDuty | Notifications, webhooks, security alert visibility | INCLUDED/LIMITED | 25% | DEFER VENDOR for single-owner alerts; KEEP when real on-call is needed |
| Okta Identity Threat Protection | Access policies, MFA enforcement, device posture, Gateway | CLOUDFLARE ONE | 55% | DEFER VENDOR for early privileged access |
| Okta Privileged Access | Access/Tunnels/service auth can protect administrative HTTP/SSH/RDP paths | CLOUDFLARE ONE | 50% | DEFER VENDOR; not a full PAM replacement |

## D. Final minimal vendor list

### Cloudflare Pro

Use as the primary **network and edge-security layer**: DDoS, managed WAF,
basic bot and API abuse signals, rate limiting, Turnstile, security analytics,
audit evidence, and simple notifications.

### Sentry

Use with Cloudflare for application exceptions, native/mobile errors, stack
traces, releases, backend traces, and debugging. **Decision: Cloudflare +
Sentry**, not Datadog initially. Cloudflare sees the edge well; it is not a
substitute for application stack traces or mobile crash correlation.

### Stripe Radar

Keep for payment-network and card-specific fraud intelligence, chargeback and
payment relationship signals. Cloudflare is the pre-payment friction and
network-context layer; it must not be used as a replacement for Radar.

### GitHub Security

Keep for Dependabot, code scanning, secret scanning, supply-chain alerts, and
artifact attestations. Cloudflare has no equivalent source-control security
plane.

### OSV, NVD, CISA KEV

Keep as free sources. OSV is the package/version matcher; NVD enriches CVEs;
CISA KEV expresses known exploitation. They are complementary, not duplicate
purchases.

## E. Vendors we do not need to buy now

- **Datadog:** do not buy alongside Sentry initially.
- **Cloudforce One:** do not buy now. Pro edge telemetry is useful today, but
  the remaining unique TI does not yet justify an unapproved subscription.
- **MaxMind GeoIP:** defer; use Cloudflare request geography/ASN first.
- **Fingerprint:** defer. Cloudflare helps with bot/browser context but lacks a
  stable consumer device/account identifier; buy only after a measured
  multi-account or ATO gap. No native SDK is to be installed.
- **MaxMind minFraud:** defer unless post-Radar evidence shows commerce-risk
  lift that Cloudflare + Radar cannot provide.
- **VirusTotal Premium:** defer. No automatic upload is permitted; file hash
  and multi-engine lookup is the only potentially distinct need.
- **PagerDuty:** defer for a single owner receiving direct Sentinel/Cloudflare
  notices. Revisit when rotations, acknowledgement, escalation policy, and
  incident ownership exist.
- **Okta ITP/PAM:** defer while no protected privileged-app inventory or PAM
  workflow has been demonstrated.

## F–H. Buy now, pilot, and free sources

**Buy now:** no additional vendor is justified solely by this assessment.
Sentry and GitHub Advanced Security should be approved only if their current
plans do not already include the selected capabilities; Stripe Radar should be
kept/enabled under the existing payment arrangement rather than treated as a
new Cloudflare replacement.

**Pilot later:** Fingerprint only for proven persistent-device/ATO gaps;
minFraud only for measured transaction-risk lift; VirusTotal only for approved
lookup-only file hash or deep IOC triage; PagerDuty only for a real on-call
program; Cloudforce One Core only when global passive DNS/WHOIS/analyst TI is
required frequently enough to replace paid IOC lookups.

**Free:** OSV.dev, NVD, and CISA KEV. Keep all three in Sentinel’s existing
provider architecture.

## I. Cloudforce One decision

### DO NOT BUY

Cloudforce One is Cloudflare’s separate threat-intelligence platform and
offers global TI, including passive-DNS investigation, through dashboard and
REST API. It could reduce future IP/domain/URL intelligence spending, but it
does not replace file-hash/multi-engine malware verdicts, GitHub security,
Stripe Radar, application observability, or on-call management. Current
incident volume and Pro telemetry do not show a need for Core, much less
Premier. Reassess only after 60–90 days of Sentinel triage metrics.

## J–O. Architecture decisions

| Area | Decision | Boundary |
| --- | --- | --- |
| Observability | **Cloudflare + Sentry** | Cloudflare owns edge/request telemetry; Sentry owns application/mobile errors, traces, releases. |
| Fraud | **Cloudflare + Stripe Radar** | Cloudflare supplies pre-payment bot/network context; Radar remains the payment decision authority. |
| Device intelligence | **Cloudflare only initially** | No stable consumer device identifier; pilot Fingerprint only after measured need. |
| Malware / IOC | **Cloudflare first; VirusTotal lookup-only on escalation** | Cloudflare covers edge/IP/domain context. VT retains hash/multi-engine depth; never upload private content. |
| Identity / privileged access | **Cloudflare Zero Trust pilot before Okta** | Access can enforce IdP MFA, WebAuthn/security keys, posture, service auth and protected admin endpoints. It is not a full IdP, ITP, or PAM suite. |
| Incident escalation | **Cloudflare notifications + Sentinel initially** | Insufficient for schedules, acknowledgements, rotations, retries, and escalation chains; use PagerDuty when those requirements start. |

## P. Sentinel API architecture and de-duplication

```
security event / incident
        |
        v
Sentinel policy, cache, budget, circuit breaker
        |
        +-- Cloudflare: edge event / IP / domain / ASN context first
        +-- OSV -> NVD -> CISA KEV: package -> CVE -> known exploitation
        +-- Stripe Radar: payment-native decision and feedback
        +-- Sentry: application error / trace evidence
        +-- GitHub Security: repository findings
        +-- Fingerprint / minFraud / VirusTotal: only a documented gap or escalation
```

The existing Sentinel contract already implements the important guardrails:
per-indicator Cloudflare enrichment, provider budgets and circuit breakers,
master/per-provider fail-closed switches, and VirusTotal lookup-only without a
file-upload capability. Preserve that design. Cloudflare signals are advisory:
hosting, VPN, proxy, bot, or ASN context is not proof of malice and cannot
directly block, ban, or decline a payment.

### Minimum-privilege Cloudflare token plan

Create only after owner authorization, directly into the approved secret store:

- **Name:** `PulseSoc Sentinel Security Intelligence`
- **Scope:** the `coinpilotx.app` zone and this Cloudflare account only.
- **Baseline read-only permissions:** `Zone:Analytics Read`, `Zone:Logs Read`
  where available, `Account:Account Analytics Read`, `Account:Audit Logs Read`,
  `Access:Organizations Read`, `Access:Device Posture Read`, and only the
  specific Cloudforce One / Security Center read permission shown by the token
  builder if that paid product is later approved.
- **Explicitly exclude:** DNS Write, Workers Write, WAF/Firewall Write,
  Account Administration, Billing, User Management, and all destructive
  permissions.
- **Expiry:** 90 days for a pilot; rotate before expiry. Record token name,
  scope, permission list, expiration, and safe token ID only—never its secret.

Cloudflare API use by Sentinel should be read-only and event/indicator scoped:
security-event/analytics evidence, audit evidence, Zero Trust logs if Access
is adopted, and Cloudforce One IP/domain/ASN/passive-DNS/WHOIS only after its
subscription and endpoint permissions are verified. Do not query every
visitor, export raw visitor data, or make Sentinel a Cloudflare rule writer.

## Q. Estimated vendor reduction

`Original planned paid/external vendors: 11 paid/vendor categories plus 3 free feeds`

`Recommended paid/external vendors after consolidation: 4 core categories (Cloudflare, Sentry, Stripe Radar, GitHub Security) plus 3 free feeds`

`Avoided/deferred vendors: 7 paid/vendor categories (Datadog, Cloudforce One, Fingerprint, MaxMind GeoIP, minFraud, VirusTotal Premium, PagerDuty, and Okta are deferred; the count treats MaxMind as one vendor family)`

The reduction avoids overlapping network/IP/geography telemetry while keeping
the distinct planes Cloudflare cannot credibly replace: payment-network fraud,
application debugging, source security, and public vulnerability intelligence.

## R. Owner actions required

1. Approve the Security Insights on-demand scan if its results should be
   collected; review each suggested remediation before any change.
2. Approve a staged Turnstile rollout plan and the exact endpoints before any
   traffic-facing enforcement is enabled.
3. Authorize creation of the read-only Sentinel API token only when a secret
   store destination is ready; do not reveal the token in chat, logs, or this
   repository.
4. Confirm whether Sentry and GitHub security capabilities are already covered
   by existing plans before any purchase decision.
5. Approve a Cloudflare Zero Trust privileged-access pilot only after listing
   the admin endpoints, identities, IdP, and break-glass procedure.
