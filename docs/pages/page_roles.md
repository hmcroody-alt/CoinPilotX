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

## The advertised permission is the enforced one

Every section on the management screen names the permission it needs, and the client
renders the tile only when the caller holds it. That name is a *claim about a route*,
and a claim can be wrong in both directions: `request_verification` advertised
`manage_status` while enforcing `edit_page`, so a MANAGER was shown no Verification
tile and could still submit a claim in the page's name through the API. The screen was
the only thing stopping them, which is to say nothing was.

`SECTION_ENTRY_POINTS` in `tests/pages/test_page_os.py` maps every section key to the
function that section opens, and the test drives *every page type × every one of the
seven roles × every section*, asserting that "was this tile offered" and "does the
route admit this caller" give the same answer. A companion exhaustiveness test fails
if a section is offered with no entry point named, so naming what a new section opens
is part of adding one. (`payments` is the one exception, recorded in
`SECTIONS_OWNED_ELSEWHERE`: it opens BusinessOsPayments, and payouts are the payments
domain's to guard.)

This closes the class, not the instance. The verification drift was one symptom; the
test would have caught any of the thirteen.

## Invites

`POST /api/pages/:id/members` invites by user id with a role from
`ASSIGNABLE_ROLES` — OWNER is deliberately absent from that tuple, so **invites and
role changes can never grant OWNER** regardless of who sends them. Invites expire
after 7 days (`INVITE_TTL_DAYS`); invitees accept explicitly; membership changes are
written to `page_audit_log`.

### An invite is a live grant, and can be taken back

`role_for()` answers the **authorization** question and is deliberately active-only:
an invitee holds no permission until they accept, and must never be answered as
though they did.

`_seat()` answers a different question — the **administration** one, *is there a row
here for somebody to act on* — and includes `invited`. `change_role` and
`remove_member` ask `_seat`, not `role_for`. They used to ask `role_for`, which meant
an invite sent to the wrong handle could not be corrected or withdrawn: it simply sat
there until its TTL ran out, and until then it was a valid claim on the page.

Withdrawing sets the row to `removed` and nulls `invite_token`/`invite_expires_at`,
so the token in the invitee's inbox stops working immediately (`accept_invite`
already refuses a non-`invited` row). The audit action is `invite_revoked` rather
than `member_removed` — an offer taken back is not access taken away, and the log
should not have to guess which happened.

The two acts share one route, so the native copy carries the distinction:
"Withdraw the invite to X" versus "Remove X from the team". Pinned in
`PageTeamScreen.test.tsx` under *withdrawing an invite*.

## Ownership transfer

`POST /api/pages/:id/transfer` requires: actor is OWNER, target is an existing
member, and the literal confirm phrase `TRANSFER` in the payload. The previous owner
is demoted to ADMIN (not removed), and the transfer is audited. There is exactly one
OWNER per page at all times.

## Audit

Role grants, changes, removals, status changes, and transfers all append to
`page_audit_log` with actor, target, and timestamp. The log is append-only.
