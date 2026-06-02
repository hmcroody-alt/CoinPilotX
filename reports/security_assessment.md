# Security Assessment

Generated: 2026-05-31

## Confirmed

- Production CSP header exists on public responses.
- Protected Pulse routes redirect to login in production.
- `/api/pulse/search` returns 401 when unauthenticated in production.
- Local chat security audit passed.
- Local visibility audit passed.
- Local media integrity audit confirms no recent raw local paths in media URLs.
- Upload signature verification now blocks spoofed image/video/document payloads.
- Stream keys are masked in live destination audits.

## Security Risks

P0:
- Private production media leakage cannot be fully disproven without authenticated production media checks and R2 bucket policy inspection.

P1:
- TURN credentials, if added, must be short-lived or scoped; do not hardcode long-lived TURN secrets in frontend code.

P1:
- `user_id=0` active posts weaken actor attribution. This is an integrity and moderation auditability risk.

P2:
- Admin route volume is high. Continue route-level permission audits for all `/admin` and `/api/admin` surfaces.

P2:
- Upload validation is stronger now, but production should also use malware scanning or asynchronous quarantine for high-risk document/file uploads.

## Required Next Proof

1. Production authenticated route matrix with regular user, premium user, admin, and anonymous sessions.
2. R2 bucket policy inspection.
3. Signed/private media leak scan.
4. Admin permission matrix audit.
5. CSRF/session cookie review in production browser.

