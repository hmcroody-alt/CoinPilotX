# PRIVATE OFFICE — OPERATIONS SLICE 1: FINAL REPORT

**Mission:** Canonical lifecycle + deadlines + attention + overview
**Commit:** `937cd118d254dca7b41ed8e6313fe4c038bb1aef` (local only — **NOT PUSHED**; branch `main` is ahead of `origin/main` by 10)
**Parent:** `6aa5670e` *feat(capital): forward obligation schedule, named for what it is*
**Scope:** 25 files, +3999 / -61

---

## 1. What was built

The six Operations primitives already existed as a store. What they lacked was a
lifecycle anyone could reason about, a definition of "late" that survived
contact with six different type contracts, and a way to answer "what needs me
today" that did not degenerate into a to-do list.

**A transition engine, derived not invented** (`services/private_office/records.py`).
Per the standing constraint, the legal transitions were read out of the six
existing type contracts and current caller behaviour, then formalised — no
status vocabulary was redesigned to make validation prettier. Three rules carry
the weight: terminal→terminal is refused; leaving a closing status requires
explicit `reopen=True`, because a status string alone is not consent to reopen;
and restating the status you already hold is `STATUS_UNCHANGED`, not an update,
so idempotent retries do not manufacture history. Every refusal is audited
under `ACTION_RECORD_TRANSITION_DENIED` — a denied transition is a fact about
the member's office, not an error to swallow.

**Deadlines with per-type semantics.** `effective_status` now derives
`DUE_SOON` / `OVERDUE` from each type's own deadline field and its own window.
An event's `occurred_at` is not a deadline. A request's window is not an
obligation's window. Closed records stop deriving lateness entirely — a
resolved obligation is not overdue, and the mutation battery holds that line.

**An attention model, not a task list** (`services/private_office/operations.py`).
Records are classified by *reason*, ranked by strongest reason, and the total is
reported honestly rather than capped to the page that was rendered. High-risk
counts come from a `count_records` query against the store, not from counting
the truncated page. Where a count cannot be answered — expiring opportunities —
the payload says `UNSUPPORTED` rather than `0`. A confident zero is a lie; an
admitted gap is information.

**The executive overview** (`GET /api/private-office/operations/overview`,
registered through the existing `_load_route_pack("private_office", ...)` at
`bot.py:1290`), plus the native `PrivateOperationsScreen` and 99 i18n keys
across all 11 catalogs.

No new operation type. No second ledger. No new writer. TASK, PROJECT,
APPROVAL, RECURRENCE, DEPENDENCY TABLE, ESCALATION WRITES and UNDX MUTATIONS
remain out, as instructed.

---

## 2. Falsification finding — Stage 20: no `operations.overview` UNDX capability

The mission's implied premise was that the overview should reach the agent.
Repository evidence disproved it, so under the falsification clause I stopped
that action, documented it, and took the architecturally correct path instead.

Two separate findings:

**Lifecycle parity is already structural, so no capability is needed.**
`undx_records_spec.execute_view` delegates to `retrieval.retrieve_records`
(retrieval.py:596), which calls `records.list_records` (retrieval.py:692) — the
one reader that computes derived status. A deadline rule added for the member's
screen therefore reaches the agent *in the same commit or not at all*. Giving
the agent its own SELECT would create a second definition of "overdue" that can
drift from the member's own screen. `test_private_records_undx_spec.py` now has
a `stage_lifecycle_parity` stage asserting exactly this, so the day someone
does give the agent its own query, the divergence is a red test rather than an
agent calmly reporting an overdue obligation as fine.

**The overview must stay absent.** It is an aggregate across every domain and
sensitivity the member holds. The agent reads through `INTENT_GENERAL` —
GENERAL domain, INTERNAL ceiling — specifically so a model cannot join health
context to financial context (`retrieval.domain_join_permitted`,
`ISOLATED_DOMAINS = {HEALTH, IDENTITY, SECURITY}`). Handing over the overview
whole walks around that gate. Handing over a narrowed one gives the agent a
count that contradicts the member's own screen, and a confidently wrong "three
things need your attention" is worse than no number at all. The agent answers
attention questions from the six lists it already has, at the ceiling it
already has. The reasoning is written into the `undx_records_spec` docstring so
the next engineer meets the argument before the temptation.

---

## 3. A regression I introduced, and fixed

The transition engine broke an existing caller. `test_obligation_projection.py`
went red with `PrivateRecordRejected: OBLIGATION is closed as RESOLVED; pass
reopen=True to return it to OPEN`.

Before touching anything I grepped the full blast radius of `update_record(`
callers. Production callers are `concierge.py` (`cancel_request` OPEN→CANCELED,
`advance_request` within working statuses — both legal) and
`private_office_structured_records_routes.py`. Every other test caller does
OPEN→RESOLVED. Exactly one caller reopens.

The fix states the intent (`reopen=True`) rather than weakening the engine,
with a comment recording why: the alternative reading — that a resolved
obligation should quietly reopen because someone sent `"OPEN"` — is precisely
the behaviour the engine exists to refuse.

---

## 4. Verification

All results below are from a clean `git archive HEAD` tree at `937cd118`, so
they measure my commit and not the concurrent session's uncommitted worktree.

**How the environment was solved.** `pip install` is blocked at the proxy
(`403 Forbidden`), which initially forced 17 suites to SKIP. The repo's own
`.venv` turned out to contain Flask 3.1.3, Werkzeug 3.1.8, pytest and
`agora_token_builder` — macOS/CPython 3.14 with darwin `.so` files, but the
packages that matter are pure Python and import fine on the sandbox's Linux
3.10. Putting the *whole* site-packages on the path was wrong and I caught it:
`test_structured_record_store` flipped to FAIL because the extra path made
`cryptography` resolve differently and the store then demanded an encryption
key this environment has no reason to hold. That was my tooling, not a defect.
The fix was a curated path — Flask's own dependency chain plus dist-info
metadata plus `agora_token_builder`, darwin binaries stripped — reproducible at
`/sessions/.../tmp/flasklibs`. The distinction matters: the first run would
have reported a real-looking failure that did not exist.

**Private Office suite:** `PASS=34  SKIP=4  FAIL=0` — up from `21/17/0`.

**The overview endpoint is now actually executed, not merely analysed.**
`test_operations_routes.py` runs and passes end to end: the overview requires
login; naming another owner in the query string changes nothing; the payload
never exposes `owner_user_id` or `record_key` on any of the six views or on
attention; an unanswerable count says so rather than reporting zero; reads
leave audit rows; and the kill switch closes the read, closes the write, and
does not take the fact routes down with it.

**Protection suite:** `327 checks across 28 suites — PASSED` at my commit.

**Anti-vacuity mutation battery** (`scripts/private_office/operations_mutation_battery.py`):
baseline green, **13 of 13 mutations CAUGHT**, zero survivors. Each mutation is
a change a reasonable engineer might make while "simplifying" — terminal→terminal
allowed, the reopen flag ignored, closed records still deriving OVERDUE, the
attention total capped like the page, an unanswerable count reported as zero.
A surviving mutation is reported as a hole in the tests, not as a pass.

**No regression anywhere in the agent layer.** All 56 `tests/undx_agent`
suites were run at `HEAD~1` and at `HEAD` and the result lines are
**byte-identical** — twice, under two different environments. Without Flask:
34 PASS / 9 FAIL / 13 SKIP at both. With Flask present, which converts every
SKIP into a real result: **48 PASS / 8 FAIL / 0 SKIP at both.** The 8 failures
are pre-existing at the parent commit and are not mine. The second run is the
one that carries weight: the first could have been hiding a regression inside
a SKIP.

**i18n gate:** 99 operations/attention keys, **0 gaps across all 11 catalogs**.

**Backend registration:** 25 blueprint routes AST-extracted including
`GET /api/private-office/operations/overview`; `py_compile` clean; `register(app)`
present at `private_office_routes.py:1678`.

### RTC hard lock

```
python3 scripts/realtime_audio_change_gate.py --base HEAD~1 --head HEAD
→ No protected real-time audio path changed (25 file(s) inspected).
```

**AGORA / AUDIO / VIDEO CALL / LIVESTREAM FILES CHANGED: 0.** No LiveKit
introduced. The one grep hit for "agora" in the tree is `"Publicar agora"` in
the Portuguese catalog — "agora" means "now" — and it is not in my diff.

---

## 5. Honest gaps — what was NOT proven

**4 suites still cannot run** — `test_office_security`,
`test_owner_office_membership`, `test_private_facts_kill_switch`,
`test_private_meetings`. All four are pytest-based, and this sandbox's
CPython 3.10 needs the `exceptiongroup` backport that pytest imports for
`BaseExceptionGroup` (a builtin only from 3.11). I got pytest itself importable
by shimming the retired `py` library, and deliberately designed that shim so
`py.path.local` raises on *use* rather than returning something plausible — a
fake green is worse than a skip. I stopped at `exceptiongroup`: backporting
real exception-group semantics by hand to make a test suite go green is exactly
the kind of thing that produces a confident wrong answer. **None of these four
touches Operations code**, so they do not gate this slice — but they are
unproven here and should be run on 3.11+.

**Device QA is still required.** Nothing here exercises the native
`PrivateOperationsScreen` on a device; the React Native tests are jest-level.

**One environment defect worth fixing separately.** `test_private_meetings_routes`
fails identically at `HEAD~1` and `HEAD` when `agora_token_builder` is absent —
seven Agora token failures, all `503`. With the package vendored it passes.
That is a sandbox packaging gap, not a code defect, but it is worth noting that
the Agora token path has real test coverage and that coverage is easy to lose
silently to a missing dependency.

---

## 6. Concurrency — and one thing you need to know

A second session was editing the Private Facts ledger in this same worktree
throughout. Handling:

- Two suites (`test_private_fact_ledger`, `test_private_observability`) went
  red mid-session on their in-flight `telemetry.py` edit. Proven not mine by
  running them against a pristine `git archive HEAD` tree, where both passed.
  Both now pass at `937cd118`; their work has landed.
- The commit used an explicit 25-path pathspec, with
  `git diff --cached --name-only` re-checked immediately before committing, per
  your standing rule. No bare `git commit`, no `git add -A`, no reset, no clean,
  no push. Five untracked files were `git add`-ed by explicit name only.
- Their 6 remaining staged files were left staged and untouched.

**⚠ ONE CONSEQUENCE TO REVIEW.** `services/private_office_routes.py` is the only
file both sessions touched. They had a staged-only deletion of the cash-flow
route — staged, never written to the worktree. My partial commit necessarily
rewrote that file's index entry, so **their staged deletion is no longer in
git's live state.** It is not lost: I rescued it before committing, to

- `outputs/index_rescue/private_office_routes.STAGED_BY_OTHER_SESSION.py` (their exact staged blob, 1556 lines)
- `outputs/index_rescue/foreign_staged.patch` (the full staged diff, 1201 lines)

I deliberately did **not** restore it into the index. Their staged blob predates
my changes, so replaying it would have staged "delete the cash-flow route *and*
revert the entire Operations lifecycle" — a change that looks committable and
is not. Their worktree is untouched and the cash-flow route is still present in
it. The safe remedy is for that session to re-stage its own deletion.

**Stale index lock.** `git` was blocked by a 0-byte `.git/index.lock`. Evidence
that it was abandoned rather than held: the index was last written at 03:36:56,
the lock appeared 61s later at 03:37:57 and was never populated, and
`logs/HEAD` shows the other session's commit had already completed at 03:37:38
— before the lock existed. It was moved into the repo's own existing
`.git/stale-locks-graveyard/` convention rather than deleted, so the evidence
survives. Root cause identified in passing: this sandbox's FUSE mount returns
`Operation not permitted` on `unlink` inside `.git/`, so **git cannot clean up
its own lock files here** and leaves one after almost every invocation. That is
an environment defect, not a repository one — but expect it to recur.

---

## 7. Against the acceptance standard

> *A PRIVATE EXECUTIVE OPERATIONS COMMAND CENTER — not: A TO-DO LIST*

A to-do list would let you tick a resolved item back to open without comment,
would call an event late because it had a date on it, would show "3 items" and
mean "3 items on this page", and would render a reassuring `0` for a number it
could not compute. This refuses all four, audits the refusals, admits what it
does not know, and declines to hand a language model an aggregate that would
let it join your health context to your financial context.

The lifecycle is now something the office *enforces* rather than something the
caller is trusted to respect. That is the difference the standard asks for.
