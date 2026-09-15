# Phase 5 — worktrees, and the lineage no branch census could see

19 worktrees. Zero uncommitted source work anywhere. **One detached HEAD
carrying 13,204 lines that no ref points at**, and one real defect found inside
it in a capability that is still shipping.

## Uncommitted work: none

Every worktree is clean except `~/Desktop/cpx-prefetch-iso`, whose two entries
are `dd/` and `dd-sim/` — DerivedData from the warm iOS build rig, build output
rather than source. The main checkout's only untracked files are this mission's
own reports and scripts.

`CLAUDE.md` still describes the main checkout as dirty with modified `bot.py`,
`services/pulse_ai_service.py`, `undx_worker.py` and others. That is stale;
those changes landed. Noted for the documentation bucket, not acted on here.

## Why this phase is not redundant with the branch census

Phase 1 walked `refs/heads/` and `refs/remotes/`. A worktree on a **detached
HEAD** appears in neither. Checking every worktree HEAD for patch-id containment
found three carrying unique content:

| HEAD | worktree | unique | disposition |
|---|---|---|---|
| `a41f1b15` | main checkout, local `main` | 3 | SUPERSEDED — this mission's own Phase 1 commits, rebuilt on pinned `origin/main` as `d6248c30`, `aaf73b46`, `9b1862e1` |
| `0f639597` | `fervent-bose-488f38` | 5 | 3 duplicate the withdrawal work; 2 ported already (`50a09228`, `e93b2da1`) |
| **`997903cf`** | **`loving-banach-1ed134`** | **4** | **see below** |

The first two were already known. `997903cf` was not, and nothing named it.

## `997903cf` — a Private Office fact-truth layer

Four commits from 2026-09-05, 13,204 insertions across 31 files, built on
`feat(private-office): structured record HTTP surface with step-up reveal`. The
content probe scores it 2/60, 3/60, 3/60 and 8/60 against main: **genuinely
absent**, not folded in under another shape.

It would be a mistake to port it. It would also have been a mistake not to read
it. Both, for the same reason: it is a substantial, careful implementation of a
surface this product withdrew.

### Capability matrix

`private_facts` is in `RETIRED_FEATURE_IDS`. `integrity` and
`structured_records` are in `RETIRED_ENGINE_MODULES`. That settles most of it.

| component | lines | serves | status |
|---|---|---|---|
| `private_office_facts_routes.py` | 563 | `private.facts.list` → `private_facts` | **OBSOLETE** — retired surface |
| `read_model.py` | 656 | facts Details/Evidence/History/Related UI | **OBSOLETE** — no screen |
| `review.py` | 326 | facts review queue UI | **OBSOLETE** — no screen |
| `integrity.py` | +415 | `integrity`, a retired engine | **OBSOLETE** |
| `undx_context.py` | 468 | governed UNDX answer context over facts | **OBSOLETE** — no caller (below) |
| `migration.py` | 437 | legacy backfill of the facts table | **DEFERRED** — table is live; no evidence of damage |
| `PRIVATE_FACTS_SUPER_FOUNDATION_MAP.md` | 511 | forensic inventory | historical record |
| `facts.py` `contradictions.py` `model.py` `evidence.py` `schema.py` | +1,700 | substrate for the above | **OBSOLETE** with their consumers |

`undx_context.py` deserves its own line because it is the best thing in the
lineage and it still has no home. Its invariant — *a disputed fact carries no
value, so the dishonest answer is unsayable* — is exactly right. But on this
lineage UNDX has **one** Private Office capability, `private.people.list` →
`relationship_intelligence`. There is no facts capability for a governed facts
context to govern. Porting 468 lines to defend a read path that does not exist
would be building the guard rail on the road we closed.

**But the invariant is portable even when the module is not**, and chasing it
into the capability that *did* survive is what found the defect below.

## The defect: an edit left two live claims

Relationship Intelligence stores contact details as facts. So the question
`undx_context.py` asks about the facts surface can be asked about the people
directory: can it state as settled something the store disagrees with itself
about?

It could. `record_fact` links a predecessor **only when the caller names one**,
and `_set_field` in `relationships.py` did not:

```python
facts_mod.record_fact(cur, ..., fact_type=fact_type, value=value, ...)
return True
```

Editing a contact's email therefore left *both* addresses `ACTIVE` on the same
person, neither superseded. Proven on the real route rather than argued:

```
--- after changing the email
    {'id': 4, 'typed_value': 'dana@example.com',     'lifecycle_state': 'ACTIVE', 'superseded_by_id': 0}
    {'id': 7, 'typed_value': 'dana.new@example.com', 'lifecycle_state': 'ACTIVE', 'superseded_by_id': 0}
ACTIVE email facts on this one person: 2
```

What makes this worth the trouble is that **the screen looks correct**.
`_contact_details` takes the newest row per fact type, so the directory shows
the new address and nothing is visibly wrong. The damage is underneath: the
person's own timeline renders every ACTIVE fact, so the stale address shows as
current; and `detect_conflicts` sees two live claims about one field and reports
a genuine contradiction where there was only an edit.

### The fix

`supersede_facts` already existed on main, and its docstring had already made
the argument, about projections:

> the old quantity is not a second opinion to weigh against the new one; it is
> the previous state of the same ledger, and leaving both ACTIVE would hand the
> contradiction engine a conflict that is really just time passing.

A member correcting a contact's phone number is that shape. `_set_field` now
supersedes, scoped to one owner, one subject, one fact type, keeping the row it
just wrote. Nothing is deleted — the predecessor stays, marked `SUPERSEDED` with
a back-pointer, which is what the timeline is built out of.

```
    {'id': 4, 'lifecycle_state': 'SUPERSEDED', 'superseded_by_id': 7}
    {'id': 7, 'lifecycle_state': 'ACTIVE',     'superseded_by_id': 0}
```

### Anti-vacuity

`stage_edit_supersedes` asserts three independent things — the count of live
claims, the link between them, and the survival of the old row — because a
"fix" that deleted the predecessor would satisfy the first two and violate
"never delete a member's data". With the `supersede_facts` call removed, **4
assertions fail**. Suite: 368 passed, 0 failed.

## Disposition

Nothing from `997903cf` is ported. The worktree is not deleted — it stays as the
record of a withdrawn design, and this file is the reason it can be left alone
without anyone wondering later what was in it. One invariant was carried out of
it into the capability that survived, and it found a real bug.
