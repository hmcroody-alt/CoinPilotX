# ADR-0005 — Campaign hierarchy and edit classification

Status: Accepted
Owner: Advertising
Date: 2026-08-01
Amends: the Advertising mission's flat campaign object. Does not amend any of
its money rules.

## Context

The Advertising mission specified a flat campaign — one object carrying budget,
targeting, creative and schedule together. That was a reasonable first cut and
it shipped working screens, but it cannot express the thing sellers actually do,
which is run one budget against several audiences with several creatives and
compare them.

It also cannot express what happens when a seller edits. Today every edit is the
same kind of edit. In reality some changes are free, some reset the delivery
system's learning and cost the seller money in re-learning, some require review
before they can run, and some are not permitted at all once spend has occurred.
A flat object with a save button tells the seller none of this, which means the
first time they discover that changing targeting restarted learning is after
their results got worse.

The review's recommendation is the standard three-level hierarchy plus an edit
taxonomy. It is right, and the edit taxonomy is the more valuable half.

## Decision

Three levels. **Campaign** owns objective, budget and schedule. **Ad group**
owns targeting and bidding, and belongs to one campaign. **Ad** owns creative and
destination, and belongs to one ad group. Spend and results roll up; a campaign's
numbers are the sum of its ad groups', and an ad group's are the sum of its ads'.

Every editable field carries a classification, and the classification is shown
to the seller *before* they commit the edit, not after.

**Safe** — no delivery consequence. Renaming, pausing an ad, adjusting a
schedule that has not started.

**Restart-learning** — permitted, but delivery re-optimises and early results
will be unrepresentative. Targeting changes, significant budget changes, bid
strategy changes. The seller is told this in plain language and told roughly how
long it lasts, because "your results will look worse for a bit and that is
expected" is the difference between a seller trusting the system and a seller
concluding it broke.

**Review-required** — the change cannot take effect until it has been reviewed.
Creative and destination changes. The prior version keeps running until the new
one is approved, and the seller can see both states.

**Locked** — not editable in place. Objective after spend has occurred, and
anything else that would retroactively change the meaning of money already
spent. The remedy is a new campaign, offered directly, not an error.

Classification is server-authoritative in behaviour and client-visible in copy —
the client never decides that an edit is safe, it renders the answer it is given.
The user-facing wording for all four never uses the word "authoritative" or any
of its neighbours, per the copy findings in the verdict record.

## Consequences

The Advertising mission's money rules are untouched. Spend caps, wallet
handling, confirmation before committing money, and the prohibition on the
client inventing financial figures all survive unchanged and remain stricter
than anything here.

Existing flat campaigns migrate to one campaign with one ad group and one ad,
which is lossless and lets the hierarchy land before any seller has to think
about it.

The manager screen gains a level of navigation it does not have, and
`AdsManagerScreen`'s account strip — which currently renders `{account.business_name
|| "Ad account"} · Ad account {account.id}`, producing "Ad account 8" — is
rewritten in the same pass, since it is being touched anyway and the raw ID
exposure is a Tier 0.5 item.

Reporting gets more expensive, because rolling up three levels is more work than
reading one. That cost is accepted; a seller who cannot compare two audiences
cannot improve, and improvement is the entire product.

## Open question

Whether budget lives only on the campaign or may also be set per ad group.
Campaign-only is simpler and is the right default; ad-group budgets are what
sophisticated sellers ask for next. Deferring the answer is safe because
campaign-only is a strict subset, but it should be answered before the edit
classification is written, since an ad-group budget would be a restart-learning
edit and the taxonomy should not be amended after sellers have learned it.
