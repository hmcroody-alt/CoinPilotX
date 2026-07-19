# Verification Badge System Report

Badges are backend controlled through `verification_badges`.

- Public rendering only uses rows where `status='approved'` and `revoked_at IS NULL`.
- Badge records include source track, approval actor, approval time, revocation actor, revocation time, revoke reason, and audit linkage.
- Revoked or suspended badges are removed from public display while preserving private history.
- Users have no route to self-assign badges.
- Admin approval creates badge rows only through the verification admin action endpoint.

Reusable rendering support is available in `pulsesoc_dashboard_centers.badge_renderer_html(...)`.
