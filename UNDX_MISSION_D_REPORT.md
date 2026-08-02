# Mission D — Biblical Wise Agency Architecture

Base commit: `e530ec86` (tip of `origin/release/undx-nexus-core-v4`).
Worked in an isolated worktree on `work/undx-mission-d` because a concurrent
process was checking branches out from under the main tree.

## What this mission was not allowed to do, and did not do

The mission mandates live validation — local backend, QA account, latest Xcode,
latest iPhone simulator, real tasks — and says plainly: *never claim intelligence
improvements from unit tests alone.*

**No part of this session ran on a simulator, a device, or a live backend.** This
is an unsupervised session with no ability to drive Xcode. So the honest reading
of everything below is:

* The **safety property** is proven by tests, and tests are the right instrument
  for it — the property is "these three files agree", which is a static fact
  about the repository, not a runtime behaviour.
* **No claim of increased intelligence is made.** Nothing here makes UNDX answer
  better. It makes one class of silent authority increase impossible to land
  without a human reading a diff. That is RULE_010's second half — *without
  sacrificing safety* — and nothing more.

Live validation of the surrounding runtime remains outstanding and is not
substituted for by this work.

## Stage 1 — What the prior research already settled

`UNDX_BIBLICAL_PRINCIPLES_RESEARCH.md` (1167 lines, Mission B) is the Stage 1
source. Its §13 records that all nine Tier 1 principles were already implemented
in UNDX independently — convergent, not derived. Its §12 warns that the document
constrains behaviour and must not be used to make architectural decisions.

That constraint shaped this mission. §14's candidate list has three entries;
items 1 and 2 were already done. **Item 3 — "receipt on authorization-scope
change (5.2)"** was the only live one, and it is a behavioural constraint on how
a boundary changes, not a new structure. It also directly serves RULE_003 and
RULE_004: nothing new was built, an existing record was made enforceable.

## Stage 2 — Subsystem classification

Classified against the mission's five verdicts. Only the load-bearing rows:

| Subsystem | Verdict | Evidence |
|---|---|---|
| Capability registry (`undx_capability_registry`) | **PASS** | Import-time validation refuses malformed capabilities; allowlist is structural |
| Policy engine (`undx_policy`) | **PASS** | Deterministic, no model in the decision path |
| Knowledge map (`undx_knowledge_map`) | **PASS** | One source, three derived views; `_live()` reads operational fields from the registry rather than restating them |
| Verification engine | **PASS** | `verified_fields` required per write; a field the verifier ignores fails at import |
| Bounded execution | **PASS** as of Mission C | Three caller-widenable limits closed at `e530ec86` |
| **Authorization boundary record** | **NEEDS_EVOLUTION** | Three independent records; nothing read more than one at a time |
| Registry honesty tests (`test_adversarial.RegistryHonesty`) | **PARTIAL** | Checks presence and route honesty; does not check cross-record agreement on risk, confirmation or scope |
| Live/simulator validation | **MISSING in this session** | Not attempted; see above |

Nothing was classified REDUNDANT. RULE_003 was not triggered — no duplicate
system was found and none was built.

## The finding

Where a capability's authority ends is written down **three times**:

1. `undx_capability_registry.CapabilitySpec` — risk, confirmation, permission
   scope, verifier, verified fields.
2. `undx_policy.PRODUCTION_TOOL_REGISTRY` — risk and a confirmation **boolean**,
   keyed by tool name.
3. `undx_knowledge_map` — authorization scope, authentication, feature flag.

Record 2 is not documentation. `undx_architecture.HIGH_IMPACT_TOOLS` is built
from its confirmation boolean, and the planner removes those names from the
allowlist it is offered. **A capability the registry classes as
`consequential_write, always` becomes reachable without confirmation if someone
flips a boolean in a different file.** That edit passed every test in the suite.

Empirically, all three records agree today: across 82 registered capabilities,
zero disagreements. Nothing enforced that. Three records of one boundary give
three chances to disagree, and a disagreement does not look like a bug — it looks
like permission.

Two things surfaced only because the check was written:

* **The vocabularies differ.** Record 2 says `high` where record 1 says
  `consequential_write`; record 3 says `membership_scoped` where record 1 says
  `self_account_only`. A translation had to be declared for any check to mean
  anything, and declaring it is what made the next point visible.
* **Record 2 cannot express `contextual` at all.** It holds a boolean; the
  registry holds three values. Seven capabilities marked as needing *situational*
  confirmation are recorded downstream as needing none, and are offered to the
  planner unguarded. That is now a pinned number rather than a consequence nobody
  had counted.

## Stage 3 — What was built

Deuteronomy 19:14 does not say "do not take your neighbour's field." It says do
not *move the marker*. The offence is committed when the boundary becomes
unreadable — before anything is taken. Applied here as three properties:

**Derive, don't restate.** `authorization_surface()` reads all three records and
produces one `AuthorizationBoundary` per capability.

**Fail closed on disagreement.** It raises `AuthorizationRecordConflict` rather
than resolving. Any resolution rule would be a fourth opinion about the boundary,
and the safe reading of "the records disagree" is that nobody currently knows
where the boundary is.

**Widening needs a receipt; narrowing does not.**
`tests/undx_agent/authorization_surface_baseline.py` records the 82 boundaries.
The drift test fails **only** on widening — risk lowered, confirmation weakened,
a capability dropping out of `HIGH_IMPACT_TOOLS`, scope changed, authentication
no longer required, a verifier or verified field dropped, a feature gate removed,
or a capability becoming newly reachable. Narrowing and deletion pass silently
and that asymmetry is itself pinned by a test. A check that fires on every change
teaches reviewers to regenerate the baseline without reading it, which costs more
safety than it buys — the §14 note flagged "receipt volume" as the risk, and this
is the threshold it asked for.

Findings name what got wider, not that something changed:

```
crypto.alerts.delete: risk lowered consequential_write -> reversible_write
crypto.alerts.delete: confirmation weakened always -> never
crypto.alerts.delete: dropped out of HIGH_IMPACT_TOOLS
crypto.alerts.delete: verifier dropped (crypto_alert_deleted)
```

### Files

| File | Change |
|---|---|
| `services/undx_capability_registry.py` | `AuthorizationBoundary`, `AuthorizationRecordConflict`, `authorization_surface()`, `surface_widenings()`, `authorization_baseline()`, declared vocabulary translations |
| `tests/undx_agent/authorization_surface_baseline.py` | New. 82 recorded boundaries |
| `tests/undx_agent/test_authorization_surface.py` | New. 8 tests |

Imports of the knowledge map are local to the function: the map imports the
registry, and that direction is correct — the map derives from the registry
rather than the reverse.

## Stage 4 — Verification

| Suite | Before | After |
|---|---|---|
| `tests/undx_brain` | 868 pass, 0 fail | 868 pass, 0 fail |
| `tests/undx_agent` | 790 pass, 0 fail | 798 pass, 0 fail |

Both measured in this worktree at `e530ec86` with `python3 -m unittest discover`
(pytest is unavailable and pip is proxy-blocked).

One correction to an earlier figure: a previous session recorded the agent suite
as *789 with 1 pre-existing failure*. That was measured while the main working
tree had been checked out to a different branch by a concurrent process, so it
was counting different code. At `e530ec86` the agent suite is clean.

The tests do not merely assert the current state. Four of them inject a
disagreement or a widening and assert it is caught, then restore — so the suite
demonstrates the mechanism fires, rather than only that today's data happens to
be consistent.

## Rules

* **RULE_003 (never duplicate)** — no new subsystem. The registry already owned
  the record; it now also owns the check.
* **RULE_004 (improve existing)** — `RegistryHonesty` was extended in kind, not
  replaced.
* **RULE_007 (remain governed)** — the new code adds no capability and no
  execution path. It can only refuse.
* **RULE_010 (intelligence up, safety not down)** — half satisfied, honestly.
  Safety up. Intelligence unchanged, and not claimed.

## Outstanding

Live simulator and device validation of the runtime. Not attempted here, not
substituted for, and still the gate on any claim about UNDX's behaviour rather
than its structure.
