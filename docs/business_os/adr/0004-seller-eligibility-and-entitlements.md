# ADR-0004 — Seller eligibility and entitlements

Status: Accepted
Owner: Seller platform
Date: 2026-08-01
Amends: the Store, Marketplace, Advertising and Payments missions' independent
gating

## Context

Four surfaces each decide independently whether the seller may use them, and
they decide it in four different ways and at four different moments.

Advertising gates its unbacked preview surfaces behind an environment flag,
`ADS_POST_MODE_FLAG = "EXPO_PUBLIC_ADS_POST_MODE"`, read at call time. Store
infers whether the seller has a store by whether listings came back. Payments
has its own rules from the money mission. Marketplace selling is gated wherever
the marketplace happened to gate it.

The seller-visible consequence is that a seller can be simultaneously told they
have a store, that they may advertise, and that they cannot be paid — with no
screen able to explain how those three facts fit together, because no screen can
see the other two. The engineering consequence is that "may this seller do X" has
no single answer to test, so ADR-0003's Restricted state has nothing to read.

Nothing was violated here. No mission ever said there was one entitlement
source, so four surfaces reasonably built four.

## Decision

One entitlement source, consulted by every surface, answering a fixed set of
capability questions.

The capabilities are: operate a store, sell in the marketplace, advertise,
receive payouts, and manage business identity and verification. Each resolves to
granted, not yet eligible, blocked, or unknown — and "unknown" is a real answer
that renders as ADR-0003's Unavailable rather than being coerced to a default,
because defaulting an unknown entitlement to granted is how a seller reaches a
screen they should not have reached and defaulting it to denied is how a
legitimate seller is locked out by a network blip.

Each non-granted answer carries the reason and the remedy. "Not yet eligible"
names what the seller must do; "blocked" names that a decision has been made and
who to contact, without leaking the internal grounds. This is what ADR-0003's
Restricted state renders.

Entitlements are resolved server-side and are authoritative there. The client
caches the answer and treats a stale cache as ADR-0003's Ready-from-cache — it
may render a surface it believed was granted, but it may not complete a money
action on a cached entitlement. That last clause is a money rule and inherits the
Payments mission's strictness; this ADR does not relax it.

Environment flags stop being entitlement. `EXPO_PUBLIC_ADS_POST_MODE` and its
kin remain what they actually are — build-time switches for surfaces that are not
finished — and are recorded in the flag registry as such. A flag may hide an
unfinished feature. A flag may not decide whether a seller is allowed to use a
finished one.

## Consequences

`deriveStatus`'s guessing problem gets its second half solved. ADR-0001 lets a
store distinguish empty from paused; this ADR lets it distinguish both from
"this seller may not operate a store", which is the case the current code cannot
express at all.

The Advertising surface's gating becomes reviewable, because there is one place
where "may advertise" is decided rather than one flag read plus whatever each
screen inferred.

Every surface gains a dependency on an entitlement call, which is a new failure
mode. It is handled by the "unknown" answer rather than by a fallback default,
deliberately, because a fallback default is exactly the kind of quiet assumption
this ADR exists to remove.

## Open question

Whether verification status is an input to entitlements or a capability
alongside them. Treating it as an input means an unverified seller simply cannot
receive payouts and the entitlement says so; treating it as a peer means
surfaces have to combine two answers themselves, which reintroduces the
per-surface logic this ADR is removing. The input reading is preferred but has
not been checked against the Verification mission's own rules, which are stricter
than this ADR and win where they conflict.
