# Business OS architecture decision records

Seven decisions were accepted as part of the Business OS v2 deep-review adoption
(`../V2_VERDICT_RECORD.md`). They are recorded here rather than inside the
verdict record because they outlive it: the verdict record classifies a
particular review, while these describe how the Business OS is built from now
on.

Every one of the seven is Accepted. None was deferred, and none was accepted
"in principle" — where a decision could not be made cleanly, the open question
is written into the ADR itself rather than left as an absence.

Owners below are recorded by surface rather than by name, because the mission
that produced them ran without a team roster. Substituting named owners is the
first thing the product owner should do with this directory; an ADR with a
surface for an owner is a decision nobody is accountable for.

| ADR | Decision | Owner (surface) |
| --- | --- | --- |
| [0001](0001-canonical-commerce-entity-graph.md) | Canonical commerce entity graph | Commerce data model |
| [0002](0002-adaptive-business-shell.md) | One adaptive BusinessShell | Business OS mobile |
| [0003](0003-state-and-error-standard.md) | Unified state and error standard | Business OS mobile |
| [0004](0004-seller-eligibility-and-entitlements.md) | Seller eligibility and entitlements | Seller platform |
| [0005](0005-campaign-hierarchy.md) | Campaign hierarchy and edit classification | Advertising |
| [0006](0006-roles-and-permissions.md) | Roles and permissions | Seller platform |
| [0007](0007-reconciliation-and-background-jobs.md) | Reconciliation and background-job standard | Platform services |

## Relationship to the section missions

These ADRs amend the section missions (Store, Marketplace, Advertising, Orders,
Messages, Payments, Insights, Events, Verification) rather than replacing them.
Where an ADR and a mission disagree about a money rule or a verification rule,
the mission wins — those rules were only ever strengthened by the review, never
relaxed, and no ADR here is permitted to be the mechanism by which one gets
loosened.

Where an ADR and a mission disagree about anything else, the ADR wins, because
the whole reason these exist is that nine surfaces each answered the same
structural question differently.
