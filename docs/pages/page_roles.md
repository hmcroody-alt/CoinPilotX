# Page Roles & Governance

## Roles (closed set)

OWNER, ADMIN, MANAGER, CONTENT_MANAGER, ADVERTISING_MANAGER, MARKETPLACE_MANAGER,
ANALYST.

Capabilities come from the `PERMISSIONS` matrix in `services/pulsesoc_pages.py` —
routes check permissions, never role names, so tightening a capability is a one-line
change.

| Capability          | OWNER | ADMIN | MANAGER | CONTENT | ADS | MARKETPLACE | ANALYST |
|---------------------|-------|-------|---------|---------|-----|-------------|---------|
| view_analytics      | ✓     | ✓     | ✓       | ✓       | ✓   | ✓           | ✓       |
| create_content      | ✓     | ✓     | ✓       | ✓       |     |             |         |
| edit_page           | ✓     | ✓     | ✓       |         |     |             |         |
| manage_ads          | ✓     | ✓     |         |         | ✓   |             |         |
| manage_marketplace  | ✓     | ✓     |         |         |     | ✓           |         |
| manage_members      | ✓     | ✓     |         |         |     |             |         |
| manage_links        | ✓     | ✓     | ✓       |         |     |             |         |
| manage_status       | ✓     |       |         |         |     |             |         |
| transfer_ownership  | ✓     |       |         |         |     |             |         |

(Authoritative source: the code; this table is a reading aid.)

## Invites

`POST /api/pages/:id/members` invites by user id with a role from
`ASSIGNABLE_ROLES` — OWNER is deliberately absent from that tuple, so **invites and
role changes can never grant OWNER** regardless of who sends them. Invites expire
after 7 days (`INVITE_TTL_DAYS`); invitees accept explicitly; membership changes are
written to `page_audit_log`.

## Ownership transfer

`POST /api/pages/:id/transfer` requires: actor is OWNER, target is an existing
member, and the literal confirm phrase `TRANSFER` in the payload. The previous owner
is demoted to ADMIN (not removed), and the transfer is audited. There is exactly one
OWNER per page at all times.

## Audit

Role grants, changes, removals, status changes, and transfers all append to
`page_audit_log` with actor, target, and timestamp. The log is append-only.
