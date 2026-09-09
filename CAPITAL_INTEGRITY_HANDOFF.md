# Capital Graph — integrity slice, finish runbook

Status: **DONE — the runbook below has been executed.** `services/private_office/
integrity.py` and the three `tests/private_office/` files landed in `62d8ad5f`,
and the integrity route is live in `services/private_office_routes.py`. All four
paths are tracked; the branch is on `main`.

This file is kept for the section at the end, which lists the gaps the slice did
*not* close and is still accurate. Everything between here and there is a record
of how the slice was built and verified — read it as history, not as work to do.
It described itself as uncommitted because the sandbox running the verification
died on `no space left on device` before the last four steps; a later session
finished them, and this header is the correction.

## What was in the working tree

New:

- `services/private_office/integrity.py` — read-only structural diagnostics over
  the private graph. Six checks: `cross_owner_edges`, `orphan_edges`,
  `duplicate_node_identity`, `unknown_vocabulary`, `edges_into_retired_nodes`,
  `portfolio_projection_drift`. The first three are `severity: "invariant"`, the
  rest `"drift"`. It repairs nothing and says so in `basis.repair`.
- `tests/private_office/test_integrity.py` — ten stages plus a five-mutation
  battery.

Modified:

- `services/private_office_routes.py` — one import
  (`from services.private_office import integrity as po_integrity`) and one route
  block (`GET /api/private-office/capital-graph/integrity`). **This file also
  carries ~72 uncommitted lines belonging to the concurrent Private Facts
  mission.** See the staging note below — do not `git add` this path.
- `tests/private_office/test_capital_projection_routes.py` — the integrity path
  added to `PATHS`/`DATA_KEY`, an integrity read-stage block, and a
  405-per-verb check.
- `tests/private_office/test_private_write_boundary.py` — the
  `FAULT_INJECTION_TESTS` allowlist and `test_fault_injection_allowlist_is_bounded`.

## Two decisions a reviewer should look at first

**`integrity.py` reads tables directly, and that is the point.** It is the one
capital module that does. The owner-scoped accessors cannot see a cross-owner
leak by construction — they filter by owner before returning, so an edge that
escaped into another member's subgraph is invisible to them. A detector built on
them could only ever report health. The module's docstring carries this reasoning.

**The write-boundary guard now has a named hole.** `test_integrity.py` fabricates
corruption with raw SQL against `private_graph_nodes` and `private_graph_edges`,
which the boundary guard correctly flagged. The corruption cannot be built through
the writers, because preventing exactly these states is what the writers are for —
so a fixture restricted to the writers would produce a suite that passes
vacuously. The resolution was an explicit `FAULT_INJECTION_TESTS` allowlist beside
`WRITER_MODULES`, capped at two entries, with `may_write` checking package
membership first and exclusively so nothing under `services/` can ever be admitted
by that branch. `test_fault_injection_allowlist_is_bounded` then audits the list:
each entry must sit under `tests/`, must exist, and must actually contain write
offences, so an entry that stops needing the permission fails rather than lingers.

The alternative — loosening the regex or excluding `tests/` from the scan — would
have widened the guard silently for every future file. This widens it for one
named file, visibly.

## Evidence already collected

| Check | Result |
| --- | --- |
| `tests/private_office/test_integrity.py` | ALL STAGES PASSED, 83 checks; mutations A–E all caught |
| `tests/private_office/test_capital_projection_routes.py` | PASS — every check held |
| `tests/private_office/test_private_write_boundary.py` | PASS — every check held |
| Boot proof | 7 capital-graph rules, `/integrity` GET-only |
| `pytest tests/private_office` | 6 failed, 193 passed |

On that last line: `--ignore=tests/private_office/test_integrity.py` produces the
**identical** six failures, so this slice is not implicated. Five are the
concurrent mission's (unmapped provenance types, a missing
`business_os_ent_grants` table, the structured-record encryption key). The sixth,
`test_capital_projection_routes`, passes standalone and fails only in a directory
run with `no such table: portfolio_outbox` — cross-module DB pollution from the
per-module `_TMP_DB` idiom, not a defect in the route. Step 1 below pins that
formally.

## Remaining steps

Environment (the shell sandbox, once it is healthy again):

```sh
export PYV=/tmp/pyshim:/sessions/<session>/mnt/CoinPilotX/.venv/lib/python3.14/site-packages
```

`/tmp/pyshim` holds an `exceptiongroup` shim; pytest 3.14 needs it under
python3.10 and the package index is unreachable from the sandbox.

**1. Pin the regression baseline.** Build a clean tree and compare failure
identities, not counts. Copy in only the concurrent mission's eight files
(`facts.py`, `model.py`, `schema.py`, `telemetry.py`, `audit.py`, `records.py`,
`office.py`, `operations.py`) so the tree boots:

```sh
mkdir /tmp/regbase && git archive HEAD | tar -x -C /tmp/regbase
# copy the eight files, then:
cd /tmp/regbase && PYTHONPATH=$PYV:/tmp/regbase python3.10 -m pytest tests/private_office -q
```

Expect `test_capital_projection_routes` to fail there too. Redirect output to a
file — the full run exceeds the tool's output limit. Watch disk: this is what
filled it last time; `rm -rf` the tree immediately after.

**2. Realtime audio gate.** Required even though nothing here touches audio.

```sh
python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD
```

**3. Protection suite.** `python3 scripts/protection/run_protection_suite.py`.
`test_agora_token_generation` and `test_live_replay_auto_publish` fail identically
at pure HEAD; anything beyond those two is a regression.

**4. Commit locally. Do not push.** Three paths stage normally:

```sh
git add services/private_office/integrity.py \
        tests/private_office/test_integrity.py \
        tests/private_office/test_capital_projection_routes.py \
        tests/private_office/test_private_write_boundary.py
```

`services/private_office_routes.py` must **not** be staged with `git add`, because
that would sweep in the concurrent mission's lines. Build the intended blob —
HEAD content plus only the integrity import and route block — and stage it by
hash:

```sh
git hash-object -w /tmp/routes_intended.py          # -> $BLOB
git update-index --cacheinfo 100644,$BLOB,services/private_office_routes.py
```

Then verify before committing: `git diff --cached --stat` should show that file
at roughly 49 lines changed, and `git diff --cached -- services/private_office_routes.py`
should contain no `po_operations`, no `_attention_limit`, no attention queue.
Note that `.git/*.lock` cannot be `unlink`ed on the FUSE mount — use `mv` to clear
a stale lock.

Suggested subject: `feat(capital): private office capital graph integrity diagnostics`.

## Still open in the wider mission

Gap 6 (`history` read surface, bounded-hops graph read), gap 3 (node/edge
vocabulary to §5–6, no synonyms), gap 4 (facts→graph and operations→graph
projection beyond crypto), and gap 7 (native UI in `mobile-native/`). Gap 7 sits
outside the chosen "Read model + routes" slice.
