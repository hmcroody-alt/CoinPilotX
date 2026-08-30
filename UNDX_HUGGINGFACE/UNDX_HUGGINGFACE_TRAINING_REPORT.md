# UNDX Hugging Face intelligence corpus expansion — final report

**Date:** 2026-08-29
**Mission:** research, evaluate, download, filter, normalize and integrate high-quality
datasets that can make UNDX substantially more intelligent.
**Datasets integrated:** 0
**Artifacts downloaded:** 0 · **Gated agreements accepted:** 0 · **HF token handled:** no
**Result:** the mission found a real and large intelligence gap, and it is not one that
Hugging Face data closes. Along the way it also found and fixed a live wrong-write bug:
two different write capabilities both claimed the phrase `'mark as read'`, and the
ranking shown to the user disagreed with the action that ran.

---

## The finding, stated first

UNDX routes user messages to capabilities by checking whether a registered intent
phrase's words appear **in order** in the message. There is no synonym table, no
embedding, no similarity of meaning. When a person uses different words for the same
request, UNDX returns nothing at all.

Measured:

```
  co_authored    800/800   = 100.0%   (80 capabilities, 0 routed to nothing)
  blind            6/320   =   1.9%   (40 capabilities, 303 routed to nothing)
  held_out         0/24    =   0.0%   (8 capabilities, 24 routed to nothing)
```

Reproduce with `python3 scripts/undx_routing_generalisation.py`.

The middle number is the honest estimate of how UNDX handles wording it has not been
shown. The bottom number is the control that proves the top number is an artifact.

## How the 100% turned out to mean nothing

The existing benchmark, `scripts/pulsesoc_undx_command_benchmark.py`, reported
`routes_to_capability` at **4000/4000**. Taken at face value that says routing is a
solved problem and the mission should look elsewhere for gaps. It was the first thing
this mission measured and the last thing it ended up trusting.

`scripts/undx_benchmark_corpus.py` covered 80 of the registry's 120 capabilities. The
other 40 had no cases at all — not failing cases, *absent* ones, which is worse, because
a capability with no case cannot regress in a way the benchmark would notice. Every
capability added by missions 05 and 06 was in that set: all thirteen shipped with tests
proving their authorization and zero cases proving anyone could reach them by asking.

Completing the corpus to 120/120 meant writing paraphrases for those 40. They were
written from each capability's *meaning*, without consulting the matcher. They routed
at **1.9%**, and 303 of the 314 misses went to no capability whatsoever.

The obvious explanation is that the 40 are harder or under-specified. The registry says
otherwise: the 40 declare **more** intent phrases each (mean 4.8) than the 80 do (mean
3.2). So the difference is not in the capabilities.

The control settles it. `HELD_OUT_CONTROL` in the corpus file holds blind paraphrases
for eight capabilities the *pre-existing* corpus already covers at 100% — "silence
number 2 until monday" for `crypto.alerts.pause`, "who signed up to see my stuff" for
`social.followers.list`. If the 40 were intrinsically harder, these would score like the
80. They scored **0/24**, all to nothing.

The 4000/4000 was never a measurement of the router. It was a measurement of how the
corpus was written. Bodies authored while watching a subsequence matcher drift toward
reusing the matcher's vocabulary, and the benchmark then compared the corpus to itself —
one layer up from the tautology that file's own docstring exists to kill.

That is worth stating plainly because the previous mission's benchmark work was careful
and correct in its reasoning, and the defect still got through. It got through because
the person writing the paraphrases and the person reading the score were the same person
in the same sitting. `HELD_OUT_CONTROL` is committed with one rule attached: **when an
entry there fails, the fix goes in the router or the registry, never in the control.**

## What the mission was asked to do, and what happened to each part

The mission's stages assume a training pipeline: fine-tune, distil, evaluate the trained
artifact, ship weights. Before spending the session producing corpora for it, the
substrate was checked.

There isn't one. `torch`, `transformers`, `peft`, `Trainer` and `TrainingArguments`
appear nowhere in `services/`, `scripts/` or the root modules. The only grep match for
`torch` is `advanced:[{torch:true}]` at `bot.py:51377`, a camera flashlight constraint in
inline JavaScript. `undx_router` selects among OpenAI, Claude, Gemini, DeepSeek and Groq
over HTTP. **There are no weights in this repository to fine-tune**, and a mission that
produced training corpora anyway would produce files nothing imports.

That failure mode has a precedent in this codebase, recorded in the docstring of
`services/undx_brain/corpus.py`: the v6 source corpus was generated, audited by a
purpose-built script, committed — and imported by nothing. *"It was a file, not a
faculty."*

The second constraint is access: the sandbox cannot reach Hugging Face at all. Both
`huggingface.co` and `datasets-server.huggingface.co` return `HTTP 403 from proxy after
CONNECT`. Discovery was done through the authenticated browser session against the
public API.

Given both, the owner chose the *retrieval + synthetic, benchmarked* path: research
read-only, produce the registry and licence report, ingest only through the existing
bounded corpus path, and spend the engineering effort on PulseSoc-specific synthetic
scenarios wired to the real benchmark harness, with a baseline measured first.

## What was built

**`UNDX_HUGGINGFACE/baseline_command_benchmark.json`** — the pre-change baseline,
captured before anything was touched. It must never be overwritten. `case_count` 5,710,
`failure_count` 17, 80 of 120 capabilities covered.

**320 synthetic scenario bodies across the 40 uncovered capabilities**, appended to
`scripts/undx_benchmark_corpus.py`. Registry coverage is now **120/120**. Where a
capability takes a required field with no default, roughly half the bodies name a value
and half do not, because both endings are legitimate and the split between them is what
`extraction_summary` reports. They deliberately avoid the suffix padding used elsewhere
in that file — five spellings of one sentence measure the matcher once.

**`HELD_OUT_CONTROL`** — 24 blind paraphrases for capabilities the pre-existing corpus
already covers, as a permanent guard against the tautology returning.

**`scripts/undx_routing_generalisation.py`** — reports the three rates. Exit status is 0
whatever the numbers say. A threshold here would create pressure to reword the control,
and a reworded control measures nothing; regressions belong in the benchmark, which
asserts.

**`UNDX_HUGGINGFACE/undx_external_dataset_registry.yaml`** — 31 datasets and 3 models
with a decision and a reason each.

## Benchmark: before and after

| Check | Baseline | After | |
|---|---|---|---|
| `routes_to_capability` | 4000/4000 100.0% | 4030/5600 72.0% | ← the illusion breaking |
| `renders_a_consistent_answer` | 128/144 88.9% | 144/144 100.0% | |
| `runnable_or_answerable` | 800/800 100.0% | 1120/1120 100.0% | |
| `the_question_is_answerable` | 53/53 100.0% | 306/306 100.0% | |
| `the_chooser_is_answerable` | 50/50 100.0% | 58/58 100.0% | |
| `a_named_id_selects_that_row` | 42/42 100.0% | 43/43 100.0% | |
| `intent_phrase_is_reachable` | 450/451 99.8% | 449/449 100.0% | ← the collision, closed |
| `does_not_reach_a_write` | 20/20 100.0% | 20/20 100.0% | |
| `routes_to_nothing` | 30/30 100.0% | 30/30 100.0% | |
| `governance_coherence` | 120/120 100.0% | 120/120 100.0% | |
| **capabilities covered** | **80 / 120** | **120 / 120** | |

Failures went from 17 to 1,570. **Nothing regressed.** Every new failure is a real
production miss that the previous corpus could not see, and the four checks that stayed
at 100% while their case counts grew — extraction, question phrasing, write guards,
governance — are the load-bearing result: the layers *downstream* of routing held up
under 2,182 additional cases without a single new failure. Argument resolution reached
"runnable or asking a question in the person's own words" for all 1,120 bodies including
320 it had never seen. That is the part of UNDX that is genuinely solid, and it was
worth measuring at three times the scale to find out.

The one thing that got quietly better is `renders_a_consistent_answer`, from 88.9% to
100%. Render cases are generated per successfully-routed read, so the 16 previous
failures were reads whose synthetic records produced an answer the response layer's own
guard discarded — and the corpus completion changed which reads reach that stage. This
is the weakest claim in the report: the number improved, and the improvement is a side
effect of corpus composition rather than a fix anyone made.

## A production defect the new coverage exposed

Completing the corpus was supposed to be measurement work. It found a live bug.

`'mark as read'` was declared as an intent phrase by **two different writes** —
`notifications.mark_read` (dismiss one notification) and `messages.mark_read` (clear a
conversation's unread state). One phrase, two actions, nothing to choose between them.

The two consumers of the registry then broke the tie in opposite directions.
`match_capability` sorts ties by capability id; `undx_brain.selection.rank` ordered them
the other way. So the capability the ranking displayed and the capability that actually
ran were different capabilities. A user typing those three words got a write they had not
named, and the surface that exists to show them what is about to happen showed them the
other one.

The phrase is ambiguous in plain English too: someone who types "mark as read" has not
yet said *what*. It was removed from both capabilities rather than assigned to one.
Reachability is unaffected — each keeps three phrasings that name their own object
("mark this notification read" / "dismiss this notification" / "clear this alert", and
"mark this conversation read" / "mark this chat read" / "clear my unread"). `'mark as
read'` now routes to nothing, which is the failure this registry is built to prefer.

After the fix the registry has **zero duplicated intent phrases** anywhere across its 120
capabilities and 449 phrases.

The benchmark had already seen this, and that is the part worth sitting with. The single
pre-existing failure in the baseline — `intent_phrase_is_reachable` at 450/451 — *was*
this phrase, case `INTENT-messages.mark_read-02`, with the note *"the registry declares
this phrase but another capability wins it."* One capability's declaration was dead
because the other always took it. Read as a reachability nit that is a shrug; read as
"two writes claim one phrase and the tie is broken inconsistently downstream" it is a
wrong-write bug. The check reported the symptom accurately for as long as the file has
existed and nobody followed it to the second reading. Removing the phrase closes that
check at 449/449.

This is the only change to production code in the mission, and it is worth noting how it
surfaced: not from a test written to look for it, but from writing cases for capabilities
that had never had any. Both of these were among the forty. A capability with no case
cannot regress in a way anything notices.

### Regression evidence for that change

The claim that the fix broke nothing was checked rather than asserted. The suite
`tests/undx_brain/test_selection.py tests/undx_brain/test_foundation.py tests/undx_agent/`
was run twice — once against `HEAD`'s registry and once against the edited one, with
nothing else varying:

| | Test-level failures | Total including subtest failures |
|---|---:|---:|
| Registry at `HEAD` | 8 | 24 |
| After removing `'mark as read'` | 7 | 19 |

The failure sets are nested: every failure after the edit also fails before it. The edit
introduced none and cleared one test outright
(`test_the_action_selection_entry_is_honest_about_what_is_still_missing`) plus four
subtest failures, by removing three of the sixteen contested write phrasings.

That test's own output is the sharpest available evidence that this was a real defect and
not a tidy-up. It prints each contested phrasing with the margin between the winning write
and its runner-up. Fifteen of the sixteen had margins between 6 and 21. `'mark as read'`
appeared twice — once for each capability that declared it — **with a margin of 0 both
times.** It was the only exact tie in the registry.

The seven that remain are pre-existing and unrelated. Three are stale hardcoded constants
left behind when earlier missions grew the registry — `test_foundation.py:328` asserts 82
capabilities against 120, `test_selection.py:466` asserts 136 write pairs against 1,128
(17 writes assumed, 48 real), and `test_only_one_registered_write_phrasing_has_a_runner_up_at_all`
expects one contested phrasing and finds thirteen. Three are saved-post write failures
(`"Save post 41."` returns `recoverable_failure`) and one is a reel-edge idempotency
failure, none of which touch either capability. Twelve `bot.py` line citations in
`test_knowledge_map_grounding.py` have drifted off their targets.

**These are named rather than fixed.** The two bare constants could be corrected in a
minute, but the third — expecting exactly one contested write phrasing — cannot be, and
the difference matters. Raising `1` to `13` would be fitting the assertion to the
registry, which is precisely the defect the rest of this report is about. The honest
repair is to restate that test in terms of the *margin* between a write and its runner-up
rather than the count of writes that have one, and choosing that floor is a judgement
about how close is too close. It belongs to whoever owns the selection layer, with the
current margins (6 to 21) in front of them.

## Why no Hugging Face dataset was integrated

Of the 31 evaluated: 15 `REJECT`, 10 `OWNER_REVIEW_REQUIRED` (no licence tag, `other`,
per-subset terms, or gated), 3 `EVALUATION_ONLY`, 3 `SAFETY_TEST_ONLY`, 0 `RAG`.

The rejections are not squeamishness. Three examples carry the whole argument:

**`bitext/Bitext-customer-support-llm-chatbot-training-dataset`** (5,442 downloads,
permissive) is generic support dialogue about refunds, orders and account access.
PulseSoc has its own answers to every one of those. Ingesting it teaches UNDX to speak
confidently about a *different* company's policies in PulseSoc's voice. The failure
would not look like an error. It would look like an answer.

**`FreedomIntelligence/medical-o1-reasoning-SFT`** (16,253 downloads, apache-2.0) would
give UNDX fluent clinical vocabulary in a field where it has no authority and where a
confident wrong answer has consequences this product cannot carry.

**`lockon/xlam-function-calling-60k`** is an ungated re-upload of a gated Salesforce
dataset, carrying its own permissive tag. Acquiring it obtains by mirror what the
agreement withholds directly. Rejected on that ground rather than on the tag.

The two decisions that would have survived a training substrate are worth keeping on the
shelf. `gorilla-llm/Berkeley-Function-Calling-Leaderboard` and
`wis-k/instruction-following-eval` are yardsticks — the second checks constraints
programmatically without a judge model, so it costs nothing per run and its result cannot
be argued with.

And one is worth acting on *without* a training substrate. `services/undx_brain/corpus.py`
scans ingested text for injection shapes via `_injection_shaped` and wraps records in a
data envelope. That scanner has never been tested against a corpus of real injection
attempts written by people trying to break something. Three permissively-licensed
candidates exist — `neuralchemy/Prompt-injection-dataset`,
`3nesdeniz/agentic-prompt-injection-boundary-pairs` (which pairs benign with malicious,
so false positives are measured too), and `Lakera/mosscap_prompt_injection` (harvested
from a public prompt-injection game, so the distribution is real adversarial effort).
They would be loaded by a test, passed to the scanner, asserted on — never ingested. A
corpus of attacks stored where the corpus loader can see it would *be* the attack.

Haitian Creole deserves its own line, since it is a first-class language for this
product. The hub has essentially nothing: every result was speech data for TTS or a
machine-translated Alpaca derivative in the low tens of downloads, none with a licence
tag. That absence is recorded in the registry so it is documented rather than
rediscovered.

## What would actually close the gap

The gap is semantic similarity, and the artifact class that addresses it is a
**sentence-embedding model**, not a dataset. Encode the 449 declared intent phrases
once, encode the incoming message, route on cosine similarity above a threshold, and
fall back to the existing subsequence matcher below it.

The shape matters: it is **additive**. `match_capability` keeps its current behaviour as
the floor, so nothing that routes today stops routing. Authorization, confirmation,
argument resolution and audit all sit downstream of routing and none of them change —
which is what the 100%-at-triple-scale result above establishes.

It was not built, and that is a deliberate stop. It needs `sentence-transformers` plus a
~90MB weight file in the web dyno, or a separate inference service. Adding either to a
Flask monolith that registers optional route packs inside `except Exception` blocks — so
a subsystem can vanish in production without failing the boot — is a deployment decision
for the owner, not a mission task. The candidates are recorded in the registry as
`NOT_ADOPTED` with the blocker named, so that adopting it stays a decision someone makes
rather than a fact someone discovers.

The cheaper alternative should be named and dismissed honestly: broaden the registry's
`intents` lists until the blind paraphrases route. It would work, for those paraphrases.
It is also how the corpus got tainted in the first place — fitting the vocabulary to the
test — and the next set of blind paraphrases would score 1.9% again.

## Vendor independence

Nothing in this mission created a dependency on Hugging Face. No `huggingface_hub`
import, no vendored dataset, no pinned model. Zero artifacts downloaded, zero gated
agreements accepted, and the HF token was never read, printed, stored or transmitted.

## What is not claimed

- **No production QA was run.** The commit is not pushed; Railway is serving the previous
  SHA. Nothing here has been observed on `pulsesoc.com`.
- **The 1.9% is a local measurement** against `match_capability` in isolation. The
  production path also consults `undx_brain.selection`, which ranks using the same
  scorer — so the number should hold — but that has been reasoned, not measured end to
  end.
- **`renders_a_consistent_answer` improving to 100% is not a fix**, it is a change in
  which reads reach the render stage.
- **No ELITE rating is assigned.** The mission's own rule is that ELITE requires benchmark
  evidence. The benchmark evidence here says routing generalisation is under 2%.
- **The suite is not green.** Seven test-level failures and twelve stale `bot.py` line
  citations remain, all pre-existing and all listed above. "Nothing regressed" is a claim
  about the difference between two runs, not a claim that the suite passes.

## Files

| Path | What it is |
|---|---|
| `UNDX_HUGGINGFACE/baseline_command_benchmark.json` | Pre-change baseline. Never overwrite. |
| `UNDX_HUGGINGFACE/after_synthetic_corpus_benchmark.json` | Full post-change run, 7,892 cases. |
| `UNDX_HUGGINGFACE/routing_generalisation.json` | The three rates with every miss. |
| `UNDX_HUGGINGFACE/undx_external_dataset_registry.yaml` | 31 datasets, 3 models, decisions. |
| `UNDX_HUGGINGFACE/UNDX_HUGGINGFACE_DATASET_LICENSE_REPORT.md` | Licence review. |
| `scripts/undx_benchmark_corpus.py` | +320 bodies, +`HELD_OUT_CONTROL`. |
| `scripts/undx_routing_generalisation.py` | New. The instrument. |
| `services/undx_capability_registry.py` | `'mark as read'` removed from two capabilities. The only production change. |
