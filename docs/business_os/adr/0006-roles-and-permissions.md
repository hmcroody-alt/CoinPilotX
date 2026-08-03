# ADR-0006 — Roles and permissions

Status: Accepted
Owner: Seller platform
Date: 2026-08-01

## Context

The Business OS assumes one person. Every surface that a business owner can
reach, anyone who can reach the business can reach — including Payments, payout
methods, and the Verification Center, which displays identity document status
and request detail.

That assumption is wrong for any business with staff, and it is specifically
dangerous for verification. Identity documents belong to a named individual, not
to the business, and a staff member who can answer customer messages has no
reason to see them. The current build does not distinguish the two people
because it has no concept of a second person at all.

This is a spec gap rather than a deviation — no mission described roles, so none
was violated. But it is the gap with the sharpest edge, because the failure mode
is a privacy incident rather than a bad layout.

## Decision

Two roles in the first cut, deliberately minimal.

**Owner** — full access, including money and verification. There is at least one
and it cannot be removed while it is the last one.

**Admin** — everything except money actions and verification documents.

Money actions and verification are enforced at the server, not by hiding
buttons. A hidden button is a UI convenience; the check that matters is the one
the API makes when the request arrives, and it is made regardless of what the
client believed. This mirrors the Payments mission's existing position and does
not relax it.

Verification documents are excluded from Admin visibility entirely. Not
redacted, not summarised — excluded. An Admin may see that verification is
complete, because that fact affects their work; they may not see the request
detail, the document status list, or anything derived from a document. This is a
compliance property and it is not negotiable down in a later phase in exchange
for convenience.

Denied access renders as ADR-0003's Restricted state, naming that a permission
is required and who to ask, and never implying the data failed to load. A
permission denial dressed as an error teaches staff to retry, which is the wrong
behaviour to teach.

Every money action and every verification action writes an audit event naming
the acting user, per ADR-0007. "The business did this" is not an adequate record
when more than one person can act as the business.

## Consequences

Every money and verification endpoint gains an authorisation check, and each one
needs a test that the check fails closed. Fail-closed is the requirement: an
unresolvable role is denied, not defaulted, for the same reason ADR-0004's
unknown entitlement is not defaulted.

The Verification Center's repair under Tier 0.3 should land role-awareness at
the same time it lands status-awareness, since both are rewrites of the same
render logic and doing them separately means touching it twice.

Two roles is knowingly coarse. Real businesses want a staff role that can answer
messages and fulfil orders but cannot edit listings or spend on advertising.
That is a third role and it is deliberately out of the first cut, because
shipping two roles that are correctly enforced is worth more than shipping five
that are enforced only in the UI.

## Open question

Whether an Owner can grant another user Owner, or whether that requires a
support path. Self-service is what businesses expect; it is also the step that
turns a compromised Admin account into a compromised business. The safer first
cut is that Owner is granted only through a flow that re-verifies the granting
Owner, but that flow does not exist yet and inventing it here would be deciding
a security design in an architecture record.
