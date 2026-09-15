# The master change ledger

105 rows, every one classified, none open. Machine-readable in `ledger.json`
and `ledger.csv`, human-readable in `ledger.txt`.

| classification | rows |
|---|---|
| ALREADY_PRODUCTION | 90 |
| SUPERSEDED | 8 |
| OBSOLETE | 7 |
| **MISSING_FROM_MAIN** | **0** |

`MISSING_FROM_MAIN` being empty is the finding, not an absence of one. It is
the claim the rest of this mission has to earn, so it is worth being explicit
about what it does and does not mean. It means: of the work discovered outside
`origin/main` by the ref census, the dangling-commit sweep, the worktree audit
and the stash audit, nothing was found that is both absent from main and still
wanted. It does **not** yet mean production has it — that is a separate axis,
and it is why every row reads `NOT_OBSERVED` in `PRODUCTION_STATUS` until a
phase actually observes a running service.

## Two things the ledger refuses to do

**It does not re-decide anything.** Each phase answered its own question with
the right instrument: patch id for reachability, line-presence sampling for
re-shaped content, a capability matrix for withdrawn surfaces. Re-deriving a
verdict here from a one-line summary would launder a careful judgement into a
heuristic. So classifications are copied, and the `basis` field says which
instrument produced each one — a reader can tell `git cherry: no '+' lines`
from `47/60 distinctive lines present in main` from a human reading the diff,
and those deserve different amounts of trust.

**It does not let `origin/main` stand in for production.** Railway auto-deploys
`origin/main`, so for most rows the two move together. "Most" is not "always":
services lag independently, and a service that never restarted is running old
code no matter what the ref says. A ledger that quietly equated them would
report a clean bill of health for exactly the service most likely to be broken.

## The four rows that needed a human

The census flagged four refs as carrying content with no patch-id twin
upstream. Patch id is a strong instrument but a narrow one — it survives
cherry-pick, rebase and re-authoring, and it does not survive splitting,
folding, amending or re-implementation. A `+` therefore means *"no twin"*, not
*"missing"*, and each one had to be resolved by content.

### `claude/nostalgic-neumann-d9391f` (and its `origin/` twin) — OBSOLETE

This is the row that justifies the mission's insistence on content over names.

`origin/main` contains a commit whose subject is literally
**`merge: preserve claude/nostalgic-neumann-d9391f`**, and that merge really is
an ancestor of main. Any census that keyed on branch names, merge subjects, or
"is it merged?" would have marked this branch done and moved on.

It is not done. The merge (`7d8c9ffd`, 14:14) has second parent `6b771754`,
*"structured record HTTP surface with step-up reveal"*. The four unmerged
commits were written on top of that base and land after it:

```
9b108509  private facts truth layer — supersession, evidence, conflicts, review, read model, HTTP
0473b530  structural integrity sweep over the fact store
04164edd  idempotent migration and conservative legacy backfill
997903cf  governed UNDX answer context — disputed facts carry no value
```

The merge captured the branch's base and the branch kept going. This is
precisely *"a branch being 'merged' does NOT prove all its work is present"* —
met in the wild rather than taken on faith.

Those same four commits are the detached HEAD `997903cf` found in worktree
`loving-banach-1ed134` during the worktree audit. **The branch and the orphan
worktree are one finding, not two**, which is why two ledger rows resolve to
one document: [`phase5/worktrees.md`](../phase5/worktrees.md).

The verdict there is OBSOLETE, and it is a capability judgement rather than a
content one: `private_facts` is in `RETIRED_FEATURE_IDS`; `integrity` and
`structured_records` are in `RETIRED_ENGINE_MODULES`; and UNDX holds exactly
one Private Office capability, `private.people.list`, so the governed facts
context has no facts capability to govern. Porting it would have meant building
a guard rail on a road this product closed.

One thing did come out of it. `undx_context.py`'s invariant — *a disputed fact
carries no value, so the dishonest answer is unsayable* — is portable even
though the module is not, and asking it of the capability that **did** survive
found a real defect in shipping code: editing a contact's email left both
addresses `ACTIVE`, which made the person's timeline show a stale address as
current and handed the contradiction engine a false conflict. Fixed in
`ca6edd56`, with the test proven non-vacuous (4 assertions fail when the
`supersede_facts` call is removed).

### `claude/private-office-simplify` — SUPERSEDED

Five commits. Three (`0cb23702`, `291be457`, `dee12971`) are the same Private
Office withdrawal that was rebuilt on the pinned base as `d6248c30`. Two
(`b5f06a4f`, `0f639597`) were ported as `50a09228` and `e93b2da1`. All five
were re-authored onto a different base, which is exactly the case patch id
cannot see — hence five `+` lines for work that is entirely accounted for.

### `main` — SUPERSEDED, and a note on measuring yourself

The local `main` had drifted onto a parallel lineage. Its unique commits are
this mission's own Phase 1 work, rebuilt on pinned `d007e2ba` as `d6248c30`,
`aaf73b46` and `9b1862e1`.

The census recorded **2** unique commits; there are **3**. The third,
`a41f1b15`, *is the census commit* — it did not exist at the moment the census
ran. The count was correct when taken and is stale now. It is re-counted by
hand in the ledger with the reason attached, rather than being quietly
corrected or quietly left wrong, because an instrument that cannot observe its
own effect is a thing a later reader needs to know about.

## Guards

Two, both proven to fire rather than assumed to:

* A resolution naming a `work_id` that no row has aborts the build. Otherwise
  the census and the resolutions table could drift apart and leave a row open
  while the table looked complete.
* Any row still classified `NEEDS_CONTENT_CHECK` at the end aborts the build.
  Withdrawing a single resolution and re-running produces
  `unresolved rows: REF-origin-claude-nostalgic-neumann-d9391f` — so the clean
  run means something.
