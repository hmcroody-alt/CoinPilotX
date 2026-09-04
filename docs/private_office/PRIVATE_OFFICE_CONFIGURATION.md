# Private Office — configuration and kill switches

Everything an operator can change about `services/private_office` without a
deploy. There is not much here on purpose: access is decided from the member's
resolved tier server-side, and configuration can only *subtract* from that.

## What decides access (and is not configurable)

Access is resolved in code, not in environment:

- `services/private_office/tiers.py` resolves the member's effective tier on the
  four-rung ladder `FREE < PREMIUM < PRIVATE < PRIVATE_OFFICE`, delegating grant
  ownership to `services/business_os/entitlements`.
- `services/private_office/feature_matrix.py` maps each feature to a minimum
  tier and an implementation state.
- `services/private_office/access.py` is the shared decision every surface reads.

There is no environment variable that grants a tier, unlocks a feature for a
member who has not been granted it, or bypasses the resolver. If you are looking
for one because a member cannot reach something they have paid for, the problem
is a grant, not a config value — check `GET /api/private-office/entitlement` and
the entitlement grant tables, not this document.

## Kill switches

A feature may declare a `flag_env` in the matrix. The flag is a runtime kill
switch: it can turn off a feature the member is otherwise entitled to, and it
can do nothing else. A switched-off feature reports `FEATURE_DISABLED`, which is
deliberately distinct from `NOT_ENTITLED` (which would prompt an upgrade, taking
money for something the member would not receive) and from `NOT_IMPLEMENTED`
(which would claim the feature was never built). The member is told the feature
is temporarily off, because that is what is true.

| Variable | Feature | Minimum tier | Effect when off |
| --- | --- | --- | --- |
| `PRIVATE_FACTS_ENABLED` | `private_facts` | `PRIVATE` | Fact capture and retrieval stop. `GET`/`POST /api/private-office/facts` refuse, the `private.facts.list` UNDX capability stops resolving, and the office lists the row as temporarily disabled. Stored facts are not deleted; re-enabling exposes exactly what was there before. |
| `CAPITAL_GRAPH_ENABLED` | `capital_graph` | `PRIVATE` | The Capital Graph view stops. `GET /api/private-office/capital-graph` and both `/api/private-office/entities/...` routes refuse, the `private.capital.graph` UNDX capability stops resolving, and the office lists the row as temporarily disabled. No node, edge or fact is deleted. |

These two are the features that are both `IMPLEMENTED` and carry a flag, which
is what makes their switches load-bearing — every other unavailable capability
is unavailable because the code does not exist yet, and no flag changes that.

They are deliberately **separate switches over one substrate**. The Capital
Graph reads the same private store the fact routes write to, so a single flag
would have been simpler. It would also mean that a problem in the traversal —
a slow query, a projection bug, a bad view — could only be answered by taking
fact capture down with it, and a member who cannot record anything during an
incident loses the window in which the information was in front of them.
Turning off a *view* and turning off a *store* are different decisions and the
operator gets to make them separately. Neither switch deletes a row, so both
are reversible in place.

## Polarity — absent means enabled

This is the part that surprises people, so it is stated twice: here and in
`.env.example`.

`feature_matrix._flag_enabled()` reads an unset or empty variable as *"not
overridden"* and therefore **enabled**. Only the literal values `1`, `true`,
`on`, `yes` (case-insensitive) enable. **Any other non-empty value disables** —
including `0`, `false`, `off`, `no`, and also including a typo such as `ture`.

The consequence is that the fail-safe direction differs at each end:

- **Forgetting** the variable leaves the feature **on**. A host that never sets
  it runs the feature normally, which is why production works today with the
  line blank.
- **Fat-fingering** the value turns the feature **off**. There is no "invalid,
  so ignore it" path; an unrecognised value is treated as an instruction to
  disable.

So deleting the line to bring a feature back works. Deleting the line expecting
a feature to *stay* off does not — that re-enables it.

This polarity is retained deliberately rather than inverted. Inverting it would
mean any host that had not yet been given the new variable would silently lose a
live, paid-for feature at the next deploy, which is a worse failure than the
asymmetry above. Both directions are pinned by tests in
`tests/private_office/test_private_facts_kill_switch.py`, and
`test_every_private_office_flag_is_documented_in_env_example` fails if a future
flag is added to the matrix without being documented in `.env.example`.

## Using a switch during an incident

1. Set the variable to `false` in the environment and restart the web dynos.
   Workers read the same matrix, so restart those too if the feature has a
   background path.
2. Confirm via `GET /api/admin/private-office/status`, which reports the
   resolved availability the server is actually serving rather than what the
   environment claims.
3. Confirm a member surface: the office listing should show the row as
   temporarily disabled, and the feature's route should refuse rather than 404.

A switch with collateral is worse than no switch, so
`test_the_switch_touches_nothing_else_in_the_office` asserts that flipping one
flag leaves every other row's answer byte-identical.

## Test gate

`scripts/private_office_test_gate.py` runs the suite per-file, as a directory,
repeated, and in shuffled module order, and requires all four to agree. The
suite's modules each mutate process globals at import time (`DATABASE_URL`, a
stub `bot` in `sys.modules`), which `tests/private_office/conftest.py`
neutralises per module; the gate is what proves that neutralisation still holds.
Run it before trusting a green directory run.
