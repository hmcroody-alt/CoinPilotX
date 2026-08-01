# UNDX — Limit-Escalation Audit

**Mission A, Part 5.** Scope: every ceiling in the UNDX subsystem — every number that
decides how much reasoning, how many calls, how much context or how many drafts a turn
may spend — examined for whether it can grow at runtime, whether it is enforced, and
whether anything reads it at all.

## The question, and the answer

A limit escalates when something at runtime can make it larger than the value it was
resolved at. This part concluded that **nothing in the UNDX subsystem does this**, and that
conclusion was drawn from a grep sweep for `+=` and `*=` against bound names. Mission C
read the same files completely and **falsified it**: three limits could be widened past
their configured value by an in-process caller, in a shape no augmented-assignment sweep
can see — `max(FLOOR, min(caller_value, CONSTANT))` with the *configured* value simply
absent from the `min`. See `UNDX_BOUNDED_EXECUTION_AUDIT.md`. All three are now closed, and
the paragraph below is retained as written so the error is legible rather than tidied away.

`Budget` is a frozen dataclass. `Ledger` has no `release`, `reset`, `refund` or `extend`;
its counters are read-only properties and it spends without refunding. `admit()` refuses
an over-budget plan rather than truncating it. Attention's `_clamp` only narrows.
`knowledge.retrieve` clamps configuration to `MAX_RESULTS` and `MAX_CONTEXT_CHARS`, which
are module constants an environment cannot reach. (That last sentence was true of the
record limit and false of the character limit, which is exactly the defect Mission C
found: the two neighbouring branches did not say the same thing.)

What the audit found instead were four ceilings that did not *escalate* so much as fail
to mean anything: two that nothing read, one that changed the answer without saying so,
and one that disagreed with a second measurement of the same thing. `bounds.py` opens by
naming this exact defect — *"A ceiling nobody reads is a comment"* — for four variables it
then fixed. Two more were still sitting in the catalogue with no reader.

And one gap that is not a defect in any existing ceiling: there was no way to say *which
kind of turn this is*. `bounds.budget()` reads the environment and nothing else, so the
ceiling applied to a turn that is about to change something the person owns was whatever
the ceiling for a research turn happened to be set to. That is what `PROFILES` is for.

## Findings

| Location | Ceiling | Escalates? | Risk | Action |
|---|---|---|---|---|
| `undx_brain/bounds.py:87` `Budget` | The four planner numbers | **No.** Frozen dataclass | — | Unchanged. Pinned by `test_the_budget_cannot_be_edited_after_it_is_resolved`. |
| `undx_brain/bounds.py:215` `Ledger` | Spend against the budget | **No.** Monotonic; no refund path exists | — | Unchanged. Pinned by a test asserting no `release`/`reset`/`refund`/`extend`/`grant` method appears. |
| `undx_brain/bounds.py:154` `admit()` | Plan length | **No.** Refuses rather than truncates | — | Unchanged. This is the correct shape and the docstring already says why. |
| `undx_brain/bounds.py:134` `budget()` | Resolves from the environment only | **No** — but it is the *only* constructor | **Medium.** One set of numbers for every kind of turn. A write turn and a research turn got the same six steps and eight calls, so the affordance that cannot be taken back ran under the ceiling set for the one that can. | **`PROFILES` + `profile()` added.** Four fixed shapes; configuration may lower any number in them and raise none. |
| `undx_brain/knowledge.py:312` `_limits` `UNDX_KNOWLEDGE_MAX_RESULTS` | Records retrieved | **No.** Clamped to `MAX_RESULTS` | — | Unchanged. |
| `undx_brain/knowledge.py:313` `_limits` `UNDX_KNOWLEDGE_MAX_CONTEXT_CHARS` | Retrieval character budget | **No.** Clamped to `MAX_CONTEXT_CHARS` | — | Unchanged. |
| `undx_brain/config.py:193` `UNDX_SOURCE_CORPUS_MAX_CONTEXT_RECORDS` | Declared: *"a hard ceiling on records that may enter a single model prompt, **independent of what retrieval asks for**"* | **No — nothing read it** | **High.** The one variable described as the thing that stops the corpus becoming one enormous prompt had no reader anywhere in `services/`. Its only appearance in the codebase was its own declaration. Any value an operator set had no effect. | **Fixed.** Read in `knowledge._limits` as a second independent `min`, and applied to the explicit `limit=` argument as well — an in-process caller is retrieval asking, and the flag says it holds independent of what retrieval asks for. A note records when it is the binding ceiling. |
| `undx_brain/config.py:359` `UNDX_RESPONSE_MAX_REGENERATIONS` | Declared: *"how many times a response failing the factuality check may be regenerated"* | **No — nothing read it** | **Medium, and the declaration was worse than the silence.** Declared default `1`, maximum `3`. `render()` can build up to twenty drafts — five lead framings in the widest branch against four clause orderings — so *every value the flag was permitted to hold* described behaviour narrower than what shipped. (This figure originally read "forty-four", from "eleven framings"; both numbers were asserted without being counted, and Mission C counted them.) Wiring it in as declared would have cut UNDX off after its first rejected draft in every deployment — a silent narrowing wearing a fix's clothes. | **Fixed, and the declaration corrected.** Default and maximum raised to `64`, above the whole search space, so reading it changes nothing until somebody deliberately lowers it. `render()` now counts *rejected* drafts against it and falls to the honest boundary when it is spent. |
| `undx_brain/corpus.py:834` `prompt_block` char budget | Characters of corpus in a prompt | **No** — but it truncated silently | **High.** `break`, then return the surviving lines. No count, no marker, no note. A shortened block is not a smaller answer; it is a different corpus presented as the whole one, and the reader that acts on it is a model. | **Fixed.** An omission line is rendered *inside* the fence naming the count and saying the view is incomplete. Once one record is dropped all later ones are too, rather than letting a short record slip in behind a long one — relevance order is what makes "the first N" defensible. |
| `undx_brain/knowledge.py:433` retrieval cost model | `len(path) + len(summary) + 40` | **No** — but it disagreed with the renderer | **High.** `prompt_block` costs the fully rendered line: path, category, trust level, `[STALE]` marker, summary, and `envelope.neutralise` expansion. The estimate ran **low**, so retrieval declared records *kept* — and therefore omitted them from `Retrieval.withheld` — that `prompt_block` then dropped. The caller believed it had passed a set it had not passed, with nothing recording the difference. That is the shape of a confident answer on partial data: no wrong fact anywhere, just a silence where the caveat belonged. | **Fixed.** `corpus.render_line(record)` exported as the single rendering; retrieval costs `len(render_line(record))`. `withheld` is now a true account, and `prompt_block`'s own truncation is unreachable for records that came from `retrieve` — belt and braces preserved. |
| `undx_response_intelligence.py:65` `MAX_RENDER_ATTEMPTS` | Candidate drafts | **Yes**, as shipped — `render(attempts=...)` consumed the parameter with no `min` against the constant, so the constant was a default and not a ceiling. Missed here; found by Mission C | Low. Bounded in practice by the 20-draft search space, and every extra candidate had already passed `validate_consistency`. A discipline defect rather than a live hazard | **Fixed.** `attempt_budget = max(1, min(int(attempts), MAX_RENDER_ATTEMPTS))`. |
| `undx_response_intelligence.py:61` `MAX_EXPLANATION_CHARS` | Answer length | **No.** Module constant | — | No change. |
| `undx_brain/attention.py` `_clamp` | Salience bounds | **No.** Narrows only | — | No change. |

## Bounded policy profiles

`PROFILES` in `bounds.py` names four shapes. Each is a **fixed maximum**: `profile(name,
env=...)` takes `min(profile, environment)` on every numeric field and `profile and
environment` on `multi_step`.

| Profile | steps | tool calls | retries | timeout | multi-step |
|---|---|---|---|---|---|
| `write` | 1 | 2 | 0 | 30s | no |
| `read` | 2 | 4 | 1 | 60s | no |
| `explain` | 3 | 6 | 1 | 90s | yes |
| `research` | 6 | 8 | 1 | 120s | yes |

The asymmetry is the whole point, and it is the same asymmetry that lets the completion
conjunction in Part 4 run unflagged: an operator who tightens `UNDX_PLANNER_MAX_TOOL_CALLS`
tightens every profile, and an operator who widens it widens nothing beyond the profile's
own maximum. No value of `UNDX_BRAIN_REASONING_ENABLED` turns multi-step reasoning on for
the `write` profile, because `multi_step=False` there is a ceiling like every other field.
A misconfiguration, or an environment somebody influenced, can make UNDX do less than a
profile allows and can never make it do more.

`write` is deliberately the tightest and deliberately single-step — not because writes are
slow, but because every extra step is another place for a plan to decide the goal changed,
and the operation at the end of it is the one that cannot be taken back.

An unknown profile name resolves to `read` and records that it did. `read` rather than
`research`: an unknown name is a typo or a bug, and the safe reading of "I don't know what
kind of turn this is" is the narrow one.

`ledger_for(profile_name=...)` is opt-in and defaults to `None`, so every existing call
site keeps the environment-resolved budget it already had. Passing a name is the visible
act of choosing a narrower shape; it cannot be used to choose a wider one.

## Open finding, recorded rather than fixed

Eight flags are declared in `config.CATALOG`, described in the present tense, and read by
nothing in `services/`:

`UNDX_AGENT_FAIL_CLOSED`, `UNDX_AGENT_REQUIRE_AUDIT`, `UNDX_AGENT_REQUIRE_VERIFICATION`,
`UNDX_RESPONSE_FACTUALITY_CHECK`, `UNDX_BRAIN_METRICS_ENABLED`,
`UNDX_BRAIN_RESPONSE_ENABLED`, `UNDX_BRAIN_SKILLS_ENABLED`,
`UNDX_DEGRADATION_TRACKING_ENABLED`.

None is a ceiling, so none is in Part 5's scope, and none is a live hazard. The first four
are declared fail-closed and the behaviour they describe is unconditionally on, so they are
inert in the safe direction — an operator setting them to `0` gets the strict behaviour
anyway. That is a flag that lies, never a flag that opens something. The other four gate
features whose call sites do not exist yet.

They are **not** wired. Wiring eight switches to turn a test green would be building
systems so an audit looks productive, which is what this mission opens by warning against.
What the audit owes instead is that the list cannot grow quietly, and
`test_the_set_of_unread_flags_has_not_grown` pins it exactly — a new unread flag fails on
the day it is added, and wiring one of the eight also fails, deliberately, so the
improvement is recorded rather than absorbed.

## Drift tests

`tests/undx_brain/test_bounded_policy_profiles.py` — 31 tests in five classes:

`AProfileMaximumCannotBeRaised` runs every profile against an environment asking for 200
steps, 200 calls, 200 retries and reasoning on, and asserts no field exceeds the declared
shape; asserts `write` stays single-step across four spellings of the reasoning flag;
asserts configuration can still *lower* a profile; asserts `PROFILES` is immutable at
runtime; asserts a profiled ledger enforces the profile and an unprofiled one is byte-for-
byte the budget it always was.

`TruncationIsDisclosed` asserts the omission notice appears when records are dropped,
counts them correctly, does **not** appear when nothing was dropped (a caveat on every
answer teaches the reader to skip it), stays inside the untrusted fence, is not triggered
by quarantine exclusions — a different reason, worth telling apart — and cannot be forged
by a record whose summary contains a closing tag.

`OneCostModelForOneLine` asserts every record retrieval kept appears in the block, across
five character limits, and that the old expression `len(record.path) + len(summary)` does
not reappear in `knowledge.py`.

`ADeclaredCeilingHasAReader` asserts the corpus ceiling binds retrieval, records itself
when binding, cannot be raised by an in-process caller, and that `0` means no corpus in
prompts; asserts the regeneration flag has a reader and that its declared default and
maximum both equal the non-narrowing sentinel; asserts **every** numeric flag in the
catalogue is read somewhere in `services/`.

`TheAuditFoundNoEscalatingBound` pins the negative result: the frozen budget, the absent
refund methods, and an AST walk over every module in `services/` that fails on augmented
assignment to any bound. **This class is named for a result that was wrong.** It pins three
real properties and none of them is the claim in its name — an AST walk for `+=` cannot see
an omission from a `min`, which is the shape all three escalating limits actually had. The
class is worth keeping and its name is a warning: a test that passes is evidence about what
it checks, not about what it is called.

## Suite state after the change

`tests/undx_brain` 868 passed (837 before, +31). `tests/undx_agent` 781 passed
(unchanged). No regressions.

## Superseded in part

Mission C (`UNDX_BOUNDED_EXECUTION_AUDIT.md`) supersedes this document's headline and three
of its rows. Read them together; this one is not withdrawn, because how the error was made
is part of the record.
