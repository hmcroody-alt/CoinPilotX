# Account Operating System Upgrade

## Scope

PulseSoc Account Command Center was upgraded from a set of simple entry points into an intelligent, backend-managed account operating layer. The internal LogiNexus standard guided the design, but the term remains absent from user-facing UI.

## User-Facing Subsystems

- Profile OS: profile completion, profile health, trust signals, change history, preview and profile management routes.
- Verification OS: review status, personal/business/creator/merchant verification state, timeline, appeal guidance, admin review queue linkage.
- Account Health OS: trust score, warnings, strikes, restrictions, appeals, recovery plan and admin health manager linkage.
- Security OS: password/2FA/session/device recommendations, suspicious activity signals and security timeline linkage.
- Settings OS: privacy, notification, message, feed, status, ads privacy, accessibility and experience tuning.
- Advanced Security OS: high-risk protection, recovery protection, 2FA/passkey readiness and hardening guidance.
- Identity Protection OS: impersonation, username/avatar similarity, badge protection and identity risk review.
- Session Intelligence OS: active sessions, session trust, suspicious session flags and session audit state.
- Device Intelligence OS: known devices, trusted devices, stale device and push-token-health status without exposing raw tokens.
- Security Timeline OS: login, password, profile, verification, device and admin event timeline.
- Threat Detection OS: suspicious login, device, profile impersonation and scam/fraud risk state.
- Login Analytics OS: login patterns, failed login count, new-device count and risk trend.

## Account Intelligence Panel

The dashboard-level account brain summarizes trust score, account score, security score, profile completion, verification status, risk level, active sessions, trusted devices, active alerts, recent security events and recommended next actions.

## Cross-Module Communication

The additive `account_system_events` table provides a shared event lane for profile, verification, account health, security, device, session, threat and login modules. Sensitive changes continue to record in `account_audit_logs`.

## Security

- No raw push tokens, database URLs, private keys, private documents, password hashes or internal notes are returned in user state payloads.
- Sensitive account operations are routed through authenticated endpoints and audited.
- Admin account surfaces are role-gated.
- User-facing pages show only owner-safe information.

## Compatibility

Existing account routes for profile, verification, account health, security and settings remain intact. New subsystem pages are additive and route through `/dashboard/account/<subsystem>`.
