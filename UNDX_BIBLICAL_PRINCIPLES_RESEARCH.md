# Biblical Principles as Engineering Inspiration for UNDX

**A research pass. No UNDX code was changed to produce this document.**

Date: 1 August 2026
Scope: research only
Status: proposal material, not a decision

---

## The rule this document is written under

Every claim in this document takes the form:

> **This biblical principle can reasonably inspire this engineering approach.**

No claim in this document takes the form:

> ~~The Bible teaches software architecture.~~

That distinction is the whole discipline of the exercise and it is not a formality. The
first sentence is a claim about *me* — about what I found useful when looking for a way to
think about a design problem. The second is a claim about *the text*, and it is false. The
biblical writers were not writing about distributed systems, had no concept of one, and
would not recognise the questions this document asks. Any sentence in what follows that
drifts toward the second form is a defect, and section 16 exists so that such a drift can
be caught rather than admired.

A second discipline follows from the first. Because the inspiration runs from text to
engineering and never the reverse, **no engineering decision in UNDX may be justified by
appeal to scripture in a code comment, a commit message, a design document or a review.**
The engineering argument has to stand on engineering grounds. This document is a source of
*hypotheses*, and a hypothesis that cannot be defended without its inspiration has not
earned its way into the system.

---

## 1. What this document is, and what it is not

It is a catalogue of recurring patterns in the biblical texts that suggested — to me,
during this pass — a way of framing a design constraint that UNDX either already has or
plausibly should. It is followed by an audit of that catalogue: what it found, what it
failed to find, what it had to throw away, and where the texts argue *against* the design
UNDX currently has.

It is not devotional writing, not apologetics, not a claim that UNDX is or should be a
religious artefact, and not an argument that these principles are better than the ordinary
engineering literature that reaches the same conclusions. In almost every case the same
constraint is available from Lamport, from Nygard, from the safety-critical standards, or
from a post-mortem. Where that is true I say so, because a principle presented as though
it had no secular equivalent is being oversold.

**On the brief.** The per-entry template, the anti-hallucination rule quoted above, the
requirement that "principles rejected" be genuinely populated, and the closing question in
section 17 are carried through as specified. The seventeen section headings are my
reconstruction of the requested structure rather than a verbatim copy of it; if the
original list differed, the sections are re-orderable without disturbing the catalogue,
which is the part that carries the content.

---

## 2. Method: how a candidate was found, and the test it had to pass

Candidates were sought by **recurring pattern rather than by isolated verse**, and this was
the single most consequential methodological choice. A proof-text is cheap: the biblical
corpus is large enough that a verse can be found to gesture at almost any position, which
is precisely why verse-mining produces conclusions that were decided in advance. A pattern
that recurs across the legal codes, the narrative books, the wisdom literature and the
epistles — written centuries apart, in different genres, for different audiences — is doing
something else. It is telling you the concern was durable enough to survive re-statement by
people who disagreed with each other about a great deal.

Four tests were applied. A candidate had to pass all four.

**Test 1 — Recurrence.** Does the principle appear in at least two of the corpus's major
divisions, in a form that is recognisably the same concern rather than the same vocabulary?
The two-or-three-witnesses rule passes overwhelmingly. "Iron sharpens iron" appears once.

**Test 2 — Constraint.** Does the principle *forbid* something a designer might otherwise
do? A principle that permits everything constrains nothing. "Do good work" fails. "One
witness is insufficient to establish a matter" forbids single-source confirmation, which
is a real prohibition with a real cost.

**Test 3 — Falsifiable mapping.** Can the engineering translation be wrong? If the mapping
would survive any implementation, it is decoration. Section 16 turns this test into
something checkable rather than asserted.

**Test 4 — Independence from the analogy.** If the scriptural framing were deleted, would
the engineering recommendation still be defensible? It has to be. Where the answer is no,
the entry is in the rejected section, not the catalogue.

**On my own reliability as a source.** I am working from training data, not from an open
critical apparatus. Biblical citations here are, in my estimation, dependable at the level
of chapter and theme, and should be checked at the level of specific verse numbers before
anything in this document is quoted publicly. Historical-context claims are the weakest
material in the document — dating and composition history for most of these texts are
actively contested among specialists, and where I state a scholarly view I have tried to
mark it as contested rather than settled. Do not treat section 3 as authoritative.

---

## 3. Provenance and limits of the biblical sourcing

**Translation.** Wording here is paraphrase or very short public-domain phrasing. Nothing
is quoted at length from a modern copyrighted translation. Where a phrase matters to the
argument I have given the sense rather than a rendering, because the argument should not
turn on a translator's choice.

**Historical context, and its instability.** Each catalogue entry carries a context note.
These notes are the least reliable part of the document. Broadly: the legal material in
Exodus–Deuteronomy reached its present form over a long period and its relationship to
actual judicial practice in Iron Age Israel is disputed; the wisdom literature has clear
parallels in Egyptian and Mesopotamian instruction texts and was probably shaped in scribal
and court settings; the prophetic books were substantially edited in and after the exile;
the New Testament epistles are occasional letters to specific communities and generalising
from them is hazardous. Where an entry's *engineering* value depends on a contested
historical claim, that entry has been marked down in confidence.

**Ancient Near Eastern parallels matter and are stated where I know them.** Several of the
strongest entries below are not distinctively Israelite — boundary-marker protection,
honest-weights legislation and witnessed sealed contracts are all attested across the
region. This is a point *for* the principle rather than against it. A concern that shows up
independently in several unrelated legal cultures is more likely to be tracking something
real about how people cheat each other than one that appears in a single tradition.

---

## 4. The UNDX component list this maps onto

Applications below name components from the live Foundation registry, read out of
`services/undx_brain/foundation.py` rather than from memory. That registry currently
declares **48 responsibilities**, each marked `owned` (one place is responsible and can be
pointed at) or `partial` (something exists, and something is still missing). The full list,
verbatim from the source:

`canonical_identity`, `request_contract`, `authorization_scope`, `owner_scoped_reads`,
`capability_registry`, `argument_validation`, `policy_engine`, `feature_flags`,
`rollout_gating`, `governed_gateway`, `confirmation`, `idempotency`, `verification`,
`audit_receipts`, `failure_recovery`, `task_persistence`, `working_context`, `attention`,
`goal_understanding`, `planning`, `action_selection`, `prediction`, `reasoning`,
`specialist_domains`, `adversarial_check`, `metacognition`, `factuality_enforcement`,
`communication`, `native_context_validation`, `prompt_injection_boundary`, `deep_links`,
`product_knowledge`, `skill_lifecycle`, `degradation_tracking`, `provider_routing`,
`memory_isolation`, `memory_conversation`, `memory_preference`, `memory_task_state`,
`memory_fact`, `memory_relationship`, `memory_approval`, `memory_learning_event`,
`evidence_state_machine`, `trust_model`, `corpus_governance`, `qa_gating`, `homeostasis`.

Naming a component in an entry below means only that the principle bears on that
responsibility. It does not mean the component was built with the principle in mind — in
every Tier 1 case the component was built first, for ordinary engineering reasons, and the
correspondence was noticed afterwards. Section 13 keeps that distinction explicit, because
a document that let it blur would be claiming credit for the text that belongs to the
engineers.

---

## 5. Catalogue, Tier 1 — high confidence

Nine entries. Each recurs across at least three of the corpus's divisions, forbids
something specific, and maps onto a UNDX responsibility in a way that could have come out
differently.

### 5.1 One witness is not enough

**Biblical source, with historical context.** Deuteronomy 19:15 states the rule directly;
it is anticipated in Numbers 35:30 for capital cases and echoed at Deuteronomy 17:6. It is
then picked up, centuries later and in a completely different setting, as a procedure for
community discipline (Matthew 18:16), for apostolic self-defence (2 Corinthians 13:1), for
accusations against an elder (1 Timothy 5:19), and as a legal premise an argument can be
built on (Hebrews 10:28). The Deuteronomic legal material is usually dated by critical
scholars to the seventh century BCE or later in its present form, though the underlying
judicial customs are likely older; the point for our purposes is not the date but the
*range* — the rule survived transplantation from an agrarian tribal judiciary into a
first-century religious movement and then into a Greek-speaking diaspora epistle, and was
still recognisably the same rule.

**Principle.** A single testimony does not establish a matter. Corroboration must come from
a source that is genuinely independent of the first, and the requirement holds most
strongly where the consequence is irreversible.

**Human cognitive meaning.** People are systematically bad at discounting a confident
single report. The rule is a procedural patch for that: rather than asking a judge to
calibrate their credence correctly, it removes the option of acting on one account at all.
It substitutes a structural constraint for an act of good judgement, on the assumption that
good judgement is exactly what fails under pressure.

**Engineering translation.** The component that performs an action may not also be the
component that certifies it succeeded. Success must be established by a read that did not
share a code path, a cache, or an in-memory result with the write.

**UNDX application.** `verification` — the read-back is described in the Foundation
registry as "independent read-back proving the write landed, separate from its response",
which is this rule almost word for word. Also `adversarial_check` (self-challenge before a
claim is made) and `trust_model` (how well something is known, kept separate from what
happened). The natural next application, and the one the principle recommends most
strongly, is that the strictness of corroboration should scale with reversibility: an
irreversible write should demand a second witness that a reversible one does not.

**Confidence: high.** Recurrence is exceptional, the constraint is sharp, and the
engineering translation is already load-bearing in UNDX. Note honestly that the same
conclusion is standard practice — read-after-write verification is not a discovery — and
the value here is the framing of *independence* as the property that matters, which is the
part real systems most often get wrong by reading back through the cache they just wrote.

---

### 5.2 Do not move the boundary marker

**Biblical source, with historical context.** Deuteronomy 19:14 and 27:17 (the latter in a
list of curses on offences committed in secret), Proverbs 22:28 and 23:10, Job 24:2 and
Hosea 5:10. The concern is regionally attested well beyond Israel: Mesopotamian *kudurru*
boundary stones carry explicit curses against anyone who moves or defaces them, and
Egyptian boundary-marker offences appear in wisdom instruction. That independent
attestation matters — this is a problem several unrelated societies converged on, which
argues it is tracking a durable failure mode rather than a local legal fashion.

**Principle.** Boundaries are recorded, and altering the record is an offence distinct from
trespass. Deuteronomy 27:17's setting is the crucial detail: it appears among offences done
*in secret*, where the injured party cannot see the change happen.

**Human cognitive meaning.** Territory is not perceived directly; it is perceived through a
marker. Whoever controls the marker controls the perception, and can shift the reality
without anyone experiencing a moment of theft. The offence is invisible by construction,
which is why it needs a rule rather than a witness.

**Engineering translation.** Access boundaries must be recorded in a form separate from
the code that enforces them, and any change to that record must be as visible as — ideally
more visible than — a violation of it. A permission model that can be silently widened is
one that will be.

**UNDX application.** `authorization_scope`, `owner_scoped_reads`, `memory_isolation`. The
principle's specific recommendation is not about enforcement, which UNDX has, but about
*change detection*: a widening of scope should be an event with a receipt, not a diff
nobody diffed. Adjacent to `audit_receipts` and `qa_gating`.

**Confidence: high.** Recurrent, cross-culturally attested, constrains a real design
choice, and points at something UNDX does less completely than it does enforcement.

---

### 5.3 One standard of measure, applied against your own interest

**Biblical source, with historical context.** Leviticus 19:35–36, Deuteronomy 25:13–16
(which specifies the offence as *carrying two different weights in the bag* — the fraud is
in the possession of the second standard, before any transaction), Proverbs 11:1, 16:11,
20:10 and 20:23, Ezekiel 45:10, Micah 6:11, Amos 8:5. This is one of the most persistent
ethical concerns in the entire corpus, spanning law, wisdom and prophecy. Archaeology
supports the realism: excavated Judahite stone weights show meaningful variance, and
just-measure claims are a stock feature of ANE royal inscriptions, which suggests the
problem was both real and universally deplored in public.

**Principle.** The same standard applies regardless of which way it cuts. Holding a second
standard in reserve is itself the offence, whether or not it has yet been used.

**Human cognitive meaning.** Motivated reasoning is not usually experienced as cheating.
People adopt the standard that favours them and believe it. The rule attacks this by making
the *possession* of a selectable standard culpable, which is a much easier thing to check
than the honesty of a particular application.

**Engineering translation.** A system may not apply a laxer evidential standard to claims
that flatter it. Concretely: the confidence threshold for reporting success must not be
lower than the threshold for reporting failure, and the definition of "complete" must not
have a variant that gets used when the strict one would embarrass the run.

**UNDX application.** `factuality_enforcement` (what the response is permitted to assert,
given the evidence in hand), `degradation_tracking` (a partial read reported as partial all
the way to the response), `trust_model`. This is the sharpest entry in the catalogue for
diagnostic use: **the presence of two thresholds is the finding, before anyone examines
which is applied.** A grep for a second definition of success is a real, cheap audit.

**Confidence: high.** Strong recurrence, a genuinely unusual constraint — most engineering
guidance addresses calibration, not the possession of alternatives — and an immediately
actionable audit.

---

### 5.4 Count the cost before laying the foundation

**Biblical source, with historical context.** Luke 14:28–30, where the tower-builder who
cannot finish is mocked by onlookers; Luke 14:31–32 follows with a king estimating whether
ten thousand can meet twenty thousand. Proverbs 24:27 gives the agrarian form: prepare the
outside work and make it ready in the field, and afterward build the house. The Lukan
material is part of a discourse on the cost of discipleship, and using it as project
management advice is exactly the misreading this document is supposed to avoid — the point
is retained only because the *reasoning structure* it assumes is separable from its
subject, and because Proverbs 24:27 supplies the same structure with no theological load
at all.

**Principle.** Estimate completion before committing the first irreversible resource. The
humiliation in the parable attaches not to failure but to *visible* half-completion, which
is a specific claim about which failures are worst.

**Human cognitive meaning.** Sunk cost and optimism run together. Once the foundation is
laid, the estimate is no longer honest, because abandoning is now expensive. The estimate
must therefore happen while it is still free to be pessimistic.

**Engineering translation.** Predict the outcome of an operation before beginning it, and
in particular predict whether it can be *completed*, not merely started. Partial completion
of a multi-step operation is a worse outcome than clean refusal, and should be priced that
way.

**UNDX application.** `prediction` — "what would happen if this ran, worked out before it
runs" — and `planning` (bounded plan construction). It bears directly on the bounded plan
executor: the ceilings refuse rather than truncate, and this principle is an argument that
refusing is right, since a truncated plan is the half-built tower. Also `failure_recovery`,
which handles the case where the tower is already half built.

**Confidence: high on the principle, medium on the sourcing.** The recurrence is real but
thinner than 5.1–5.3, and the Lukan passage is being read against its subject matter. The
engineering conclusion stands entirely without it, which by Test 4 is the correct
relationship.

---

### 5.5 The watchman who must report what he sees

**Biblical source, with historical context.** Ezekiel 3:16–21 and, at greater length,
33:1–9. The image is of a lookout on a city wall: if he sees the sword coming and does not
sound the trumpet, the resulting deaths are charged to him; if he sounds it and is ignored,
he is clear. Ezekiel is exilic, addressed to a community that had already suffered the
catastrophe, which gives the passage its edge — it is written by and for people who know
what an unsounded alarm costs. Related: Isaiah 21:6–12, and the Habakkuk 2:1 watchpost.

**Principle.** The obligation is to report accurately, not to be believed. Discharging it
requires the warning to be *legible* — a trumpet, not a private note — and the reporter's
responsibility ends at legibility, not at persuasion.

**Human cognitive meaning.** Reporting bad news is socially costly, and the cost is paid
immediately while the benefit is diffuse and deniable. The passage removes the calculation
by making silence the only culpable option, and — importantly — by explicitly clearing the
watchman who is ignored. That second half is what makes the rule survivable.

**Engineering translation.** Degradation must propagate to the surface in a form the
consumer cannot miss, and the component that detects it is not responsible for what the
consumer does about it. A partial result silently returned as a complete one is the
unsounded trumpet.

**UNDX application.** `degradation_tracking` is the direct hit — the registry's own summary
is "a partial read is reported as partial, all the way to the response", and *all the way*
is the load-bearing phrase. Also `metacognition` (knowing what it does not know, and saying
so before it is asked) and `communication` (turning a settled outcome into words, deciding
nothing). The "deciding nothing" clause is the same separation the passage draws: the
watchman reports, the city decides.

**Confidence: high.** The mapping is unusually exact, including the part that is easy to
miss — the explicit discharge of responsibility once the warning is legible, which is what
stops "report everything" from becoming "escalate everything".

---

### 5.6 The sealed deed, the open copy, and the jar

**Biblical source, with historical context.** Jeremiah 32:9–15. Jeremiah buys a field
during the Babylonian siege of Jerusalem — a deliberately absurd purchase, made as a sign
that property would one day be bought and sold again. The procedural detail is remarkable:
he signs the deed, seals it, calls witnesses, weighs the silver, and produces *two*
documents — a sealed copy and an open one — which are given to Baruch and placed in an
earthenware vessel so that they may last a long time. Tablet-in-envelope practice and
sealed-plus-open document pairs are attested across the ancient Near East, including in
later Aramaic legal papyri; this is a description of real conveyancing procedure, not a
literary flourish. Compare Joshua 4 (twelve stones set up so that the reason can be
answered when children ask later), Exodus 24:4, Deuteronomy 31:24–26 (the book placed
beside the ark as a witness), and Esther 6:1 and Ezra 6:1–2, where a decisive turn of
events comes from *searching the archive*.

**Principle.** A record intended to survive is made in duplicate, one form tamper-evident
and one form readable, witnessed at creation, and stored durably. The archive is expected
to be queried by people who were not present.

**Human cognitive meaning.** Memory is reconstructive and interested. The parties to an
agreement will each remember it in their own favour, in good faith. Writing, sealing and
witnessing at the moment of the transaction is the only point at which the record is
cheaper to make honestly than dishonestly.

**Engineering translation.** An audit record must be written at the moment of the event, be
tamper-evident, be independently readable without breaking the tamper-evidence, and outlive
the process that wrote it. The sealed/open pair is the interesting part: verifiability and
readability are separate requirements and satisfying one does not satisfy the other.

**UNDX application.** `audit_receipts` ("a durable record of what was attempted, decided,
executed and verified"), `canonical_identity` (content hashes are the seal), `memory_approval`
(what was authorised, by whom, and whether it has been spent — the witnessed deed exactly),
and `task_persistence`. The specific recommendation: check that UNDX's receipts are readable
*without* recomputing the hash chain, since a log that can only be verified by a tool nobody
runs has the seal and not the open copy.

**Confidence: high.** The archival pattern recurs across law, narrative and prophecy; the
Jeremiah passage is procedurally detailed enough to constrain a design; and the
sealed-versus-open distinction is a real and frequently-missed engineering point.

---

### 5.7 Refuge before judgement, for the irreversible case

**Biblical source, with historical context.** Numbers 35:9–34, Deuteronomy 19:1–13, Joshua
20. Cities of refuge are designated so that a person who has killed without premeditation
can reach protection before the blood-avenger reaches them, and remain there until the
assembly judges the case. The institution presupposes a functioning kin-based vengeance
system and works by *interposing delay and process* into it rather than by abolishing it.
Whether the cities operated as described is debated; the design intent in the text is
unambiguous either way.

**Principle.** Where the consequence cannot be undone, the system inserts a protected pause
between the triggering event and the irreversible act, and the pause is a right rather than
a favour. Note what it is *not*: it is not a prohibition on the irreversible act, and the
deliberate killer is explicitly not protected.

**Human cognitive meaning.** Immediately after a triggering event is exactly when judgement
is worst and the impulse to act is strongest. Provision must be made in advance, because it
cannot be improvised in the moment by the people involved.

**Engineering translation.** Irreversible operations get a mandatory interposed step —
approval, a delay, a confirmation — that exists by default and is not at the discretion of
the caller who wants to proceed. The exemption structure matters too: the protection is
calibrated to intent, not applied uniformly, so a clearly-specified deliberate instruction
is treated differently from an ambiguous one.

**UNDX application.** `confirmation` ("human approval minted, redeemed once, and revocable
before the write"), `policy_engine` (risk classification), `failure_recovery`
(operations that failed after the point of no return are flagged, not lost). It also maps
neatly onto the goal layer's current behaviour: an unsettled repair request offers reads
and asks, rather than selecting the write the matcher would have chosen — a refuge for the
ambiguous case, with the explicit instruction still going through.

**Confidence: high.** The structure — protection by default, calibrated by intent, ending
in a judgement rather than in indefinite limbo — is more specific than "ask before
deleting", and the specificity is what makes it useful.

---

### 5.8 The rule for testing a claim that has not yet been tested

**Biblical source, with historical context.** Deuteronomy 18:21–22 poses the question
directly — how do you know a word was not spoken by the LORD? — and gives an empirical
answer: if it does not come to pass, it was not. Deuteronomy 13:1–3 adds the harder case,
where the sign *does* come to pass and the message is still to be rejected, which prevents
the first rule from being read as pure predictive accuracy. In the New Testament:
1 Thessalonians 5:21 (test everything, hold what is good), 1 John 4:1 (do not believe every
spirit but test them), and Acts 17:11, where a community is commended specifically for
checking the apostolic preaching against the texts rather than accepting it on authority.

**Principle.** Claims are checked against outcomes, and authority does not exempt a claim
from checking. The Deuteronomy 13 case adds the crucial refinement: a successful prediction
is *necessary* but not *sufficient*, because a source can be right about the observable and
wrong about what follows from it.

**Human cognitive meaning.** Deference is cognitively cheap and usually adaptive, which is
why it is exploitable. Institutionalising the check — making it the expected behaviour
rather than an accusation — is what keeps it from requiring courage each time.

**Engineering translation.** Predictions are recorded and scored against what actually
happened, model output is treated as a claim rather than as a result, and no source is
exempt from validation by virtue of being upstream, expensive, or authoritative. The
Deuteronomy 13 refinement translates to: passing a calibration check does not license
skipping the safety check.

**UNDX application.** `adversarial_check`, `metacognition`, `prediction` (scored against
outcomes), `memory_learning_event` (what happened, kept so a correction has something to
attach to), and `corpus_governance` (the source corpus ingested as bounded,
provenance-carrying, **untrusted** data). Also `prompt_injection_boundary`: 1 John's "do not
believe every spirit" is, structurally, the instruction that observed content is data and
not command.

**Confidence: high.** Recurrent across law, gospel-era narrative and epistle; the two-part
structure (test by outcome, but outcome alone does not vindicate) is genuinely more subtle
than most engineering framings of the same idea.

---

### 5.9 A limit that is kept when it is inconvenient

**Biblical source, with historical context.** The sabbath command appears in both decalogue
versions with *different* justifications — Exodus 20:8–11 grounds it in creation, and
Deuteronomy 5:12–15 grounds it in the memory of slavery in Egypt. That divergence is
informative: two traditions preserved the same practice while disagreeing about why. The
principle then scales up structurally — Exodus 23:10–12 and Leviticus 25 extend it to the
land in the seventh year and to the jubilee in the fiftieth, with Leviticus 25:20–22
explicitly raising the obvious objection ("what shall we eat in the seventh year?") rather
than pretending the cost is not real. Exodus 34:21 adds the sharpest detail: the rest holds
*even in ploughing time and in harvest* — that is, precisely when stopping is most
expensive.

**Principle.** A capacity limit is defined in advance, is not a function of current demand,
and is honoured specifically when honouring it costs something. A limit that yields under
load is not a limit.

**Human cognitive meaning.** Under load, every individual decision to continue looks
locally correct, and the aggregate is exhaustion. The only defence is a limit set when not
under load and removed from in-the-moment discretion — which is why the text ties it to
calendar rather than to condition.

**Engineering translation.** Ceilings — on reasoning depth, plan length, retries, context
size, spend — are configured ahead of time, enforced against the actual load rather than
relaxed by it, and cause a refusal rather than a silent degradation. An emergency override
that is available to the code path under pressure is not a ceiling.

**UNDX application.** `homeostasis` ("staying within its own limits, and noticing when it is
not"), the bounded reasoning ceilings that refuse rather than truncate, `working_context`
(the bounded set of things held in mind for one request), and `feature_flags` /
`rollout_gating` as limits that default closed. The Exodus 34:21 detail is the audit
question: **is there any code path that raises a ceiling because the current request needs
it?** If so, that is the harvest-time exception the text specifically forecloses.

**Confidence: high.** Exceptional recurrence including a doublet with divergent rationales,
an explicit acknowledgement of cost in Leviticus 25, and a specific, checkable audit
question that most systems fail.

---

## 6. Catalogue, Tier 2 — moderate confidence

These pass all four tests but more weakly: recurrence is narrower, or the engineering
translation is more of a stretch, or the conclusion is so standard that the framing adds
little. They are worth having; they are not worth arguing about.

### 6.1 Escalation by weight of matter

**Biblical source, with historical context.** Exodus 18:13–26. Moses is judging every
dispute personally and Jethro tells him it is not good — he will wear out. The remedy is a
tiered judiciary of rulers over thousands, hundreds, fifties and tens, with the instruction
that every great matter comes to Moses and every small matter is judged locally.
Deuteronomy 1:9–18 retells it with Moses as the initiator. The passage is unusual in the
corpus for being explicitly about *organisational load* and for having the advice come from
an outsider.

**Principle.** Route by consequence, not by uniform policy. Most decisions are handled at
the lowest competent level; the exceptional ones are escalated by a rule stated in advance,
not by the judgement of whoever is holding the case.

**Human cognitive meaning.** A single reviewer of everything becomes a rubber stamp — not
through laziness but through volume. Attention is finite, and a process that demands
uniform scrutiny gets uniform inattention.

**Engineering translation.** Human confirmation is spent on the operations that warrant it.
Confirming everything and confirming nothing converge on the same behaviour, because a user
who is asked to approve every read stops reading the prompt.

**UNDX application.** `policy_engine` (deterministic allow/deny and risk classification),
`confirmation`, `action_selection`. The specific recommendation is a warning rather than a
feature: watch confirmation frequency as a metric, because a rising rate degrades the
quality of every individual approval, including the ones that matter.

**Confidence: moderate.** The passage is a single narrative episode with one retelling —
recurrence is thin. The insight about approval fatigue is real but is well-covered in the
usability literature, so the biblical framing is decoration on a known result.

---

### 6.2 Specification first, and conformance reported against it

**Biblical source, with historical context.** Exodus 25–31 gives the tabernacle
instructions in detail; Exodus 35–40 narrates the construction. The striking feature is
the closing sequence, where the refrain *as the LORD commanded Moses* recurs many times in
Exodus 39–40 as each element is checked off. Compare 1 Kings 6–7 for the temple and Ezekiel
40–43 for the visionary one. Critical scholarship generally assigns this material to a
priestly source with a strong interest in cultic exactness, quite possibly written in or
after the exile when the building itself was gone — which, if so, makes the passage a
*written specification for something that could not be inspected*, an interesting condition
for a document to be composed under.

**Principle.** The specification precedes construction, and completion is reported by
explicit item-by-item conformance to it rather than by a general assurance that the work
was done.

**Human cognitive meaning.** "It is finished" is a summary judgement, and summary
judgements are where wishful thinking enters. The refrain form forces the builder to make
the claim once per item, which is much harder to make carelessly.

**Engineering translation.** Capabilities are declared with required fields, types and
choices before anything executes; conformance is checked per field rather than asserted for
the whole; and the "done" claim is a conjunction of specific checks rather than an
atomic assertion.

**UNDX application.** `request_contract`, `argument_validation`, `capability_registry`,
`qa_gating`. This is essentially already the design, which is why it is Tier 2 rather than
Tier 1 — it is a description of UNDX rather than a proposal for it.

**Confidence: moderate.** Recurrence is decent, the mapping is clean, but nothing follows
from it that UNDX has not already done, and Test 2 (constraint) is only weakly satisfied
because contract-first design is the default assumption of the whole industry.

---

### 6.3 Restitution defined per offence, exceeding the loss

**Biblical source, with historical context.** Exodus 22:1–15 sets out restitution schedules
that vary by what was taken and how — fivefold for an ox, fourfold for a sheep, double for
goods found in the thief's possession. Leviticus 6:1–5 and Numbers 5:5–8 add restitution
plus a fifth for property held by deceit, and require it to be paid *to the wronged party*.
Luke 19:8 has Zacchaeus volunteer fourfold. The graduated schedules have clear parallels in
other ANE law collections, though the specific ratios differ, which suggests the practice
of tariffed restitution was widespread and the numbers were local.

**Principle.** Undo is defined in advance and per offence type, not improvised afterwards,
and restoring the original state is not sufficient — the remedy exceeds the loss because
the disruption itself is a harm.

**Human cognitive meaning.** After a harm, the parties cannot agree on what would make it
right, and the injured party's estimate rises with the negotiation. A published tariff
removes the negotiation.

**Engineering translation.** The undo path for each capability is specified when the
capability is registered, not discovered during an incident. And "we restored your data" is
an incomplete remedy: the interruption is itself part of what was lost.

**UNDX application.** `capability_registry` (which already carries an undo graph),
`failure_recovery`, `idempotency`. The "exceeding the loss" half maps less to code than to
incident response, and I flag it as the weaker half.

**Confidence: moderate.** Good recurrence and a genuinely useful framing of undo-as-part-of-
the-spec. Marked down because the "more than restoration" element does not translate into
anything a system can execute on its own.

---

### 6.4 The edges of the field are not harvested

**Biblical source, with historical context.** Leviticus 19:9–10, 23:22 and Deuteronomy
24:19–21 require harvesters to leave the field's corners, the forgotten sheaf and the
fallen grapes for the poor and the resident alien. Ruth 2 shows the practice in narrative
form, with Boaz extending it beyond the legal minimum. The laws are notable for being
framed as a restriction on the *owner's* right of collection rather than as a transfer or a
charitable donation.

**Principle.** A bounded, standing right of access is granted to a non-owner, defined by
the owner's restraint rather than by the non-owner's request, and it does not confer any
broader claim on the field.

**Human cognitive meaning.** Access designed as a favour must be asked for each time, which
is degrading and unreliable. Access designed as a boundary on the owner is predictable and
does not require the weaker party to negotiate.

**Engineering translation.** Cross-account visibility should be modelled as a narrow
standing rule attached to the resource, not as a permission checked at the requester. The
distinction is real: rules attached to the resource can be enumerated and audited; checks
scattered at call sites cannot.

**UNDX application.** `memory_relationship` ("edges between entities the owner can see,
each carrying its own access policy" — the policy travelling with the edge is exactly this
shape), `owner_scoped_reads`, `authorization_scope`.

**Confidence: moderate.** Solid recurrence in law plus narrative attestation, and the
resource-attached framing is a real architectural choice. Marked down because the mapping
requires some interpretive work and a critic could reasonably call it a stretch.

---

### 6.5 Answering before listening

**Biblical source, with historical context.** Proverbs 18:13 — answering a matter before
hearing it is folly and shame. Proverbs 18:17 supplies the mechanism: the first to state
his case seems right until the other comes and examines him. Proverbs 20:25 warns about
declaring something committed and only afterwards reconsidering. James 1:19 gives the
compressed New Testament form. Wisdom literature of this kind has close Egyptian parallels
(the instruction genre), and the courtroom framing of 18:17 suggests a setting in which
these were practical rules for people who judged disputes.

**Principle.** Comprehension precedes response, and the first plausible reading of a
situation is systematically over-trusted because nothing has yet contradicted it.

**Human cognitive meaning.** This is anchoring, described accurately about 2,500 years
early. The first account occupies the interpretive frame and everything after is assessed
as a deviation from it.

**Engineering translation.** Do not resolve a request to an operation before establishing
what the request is for. A matcher that returns its best-scoring candidate has answered
before hearing, because the score is computed over vocabulary and the request has not been
understood.

**UNDX application.** `goal_understanding` — this is the layer's founding argument, and the
example the module's own documentation uses ("fix my alert" scoring a match on delete) is
Proverbs 18:17 with a capability registry. Also `attention` and `working_context`. The
recent `Shape.EXPLAIN` work is a second instance of the same failure: "explain my alerts"
and "show my alerts" produced identical goals, so the system answered before distinguishing
what was asked.

**Confidence: moderate to high on the principle, moderate on the sourcing.** The cognitive
observation is excellent and the UNDX mapping is exact. Held at Tier 2 only because the
recurrence is concentrated in the wisdom literature, and because this is anchoring — a
thoroughly documented modern result that needs no ancient warrant.

---

### 6.6 Release at a fixed interval

**Biblical source, with historical context.** Deuteronomy 15:1–11 mandates the release of
debts every seven years, and anticipates the obvious gaming — it explicitly warns against
refusing a loan because the year of release is near. Leviticus 25:8–17 extends it to the
jubilee return of property. Whether either was practised at scale is genuinely doubtful and
much debated; the Deuteronomy passage's defensive tone suggests its author expected
evasion.

**Principle.** Certain obligations and holdings expire on a schedule rather than on request,
and the schedule is known in advance to everyone.

**Human cognitive meaning.** Indefinite accumulation is the default because each individual
retention is justifiable. Only a clock that runs independently of any particular case can
stop it.

**Engineering translation.** Retention is time-bounded by default, with the exceptions
enumerated rather than the retentions enumerated. The polarity is the whole point: listing
what is kept forever is tractable, and listing what should be deleted is not.

**UNDX application.** `memory_conversation`, `memory_fact`, `memory_learning_event`, and
`memory_preference` — the registry describes preferences as "the only class held
indefinitely", which is already the right polarity. The proposal is to make the expiry of
the other classes an enforced schedule rather than a described intention.

**Confidence: moderate.** The principle is sound and the polarity insight is worth having.
Marked down because the historical practice is doubtful and because data-retention policy
is a legal and regulatory field with far better authorities than this one.

---

## 7. Catalogue, Tier 3 — low confidence, recorded but not recommended

Offered for completeness and to show where the line was drawn. I would not build on these.

**7.1 Naming as identity.** Genesis 2:19–20 (the naming of the animals), and the covenant
renamings of Genesis 17 and 32:28. Suggests `canonical_identity`: a stable name is the
precondition of reference. **Low** — the observation is true and utterly generic; every
identifier scheme in history satisfies it, so it constrains nothing (fails Test 2).

**7.2 Sowing and reaping.** Galatians 6:7, Hosea 8:7, Proverbs 22:8, Job 4:8. Suggests
`prediction`: actions have proportionate downstream consequences. **Low** — recurrence is
strong but the content is a general causal principle, and the engineering translation would
apply to any system whatsoever.

**7.3 Systematic narrowing.** Joshua 7:16–18, where the culprit is found by casting through
tribe, then clan, then household, then man. Suggests a bisection procedure for fault
localisation. **Low** — the procedure is real and the narrative genuinely describes a
logarithmic search, but the story's actual subject is collective guilt and its outcome is
an execution, so building an engineering lesson on it means quietly discarding everything
the passage is about. Recorded as an interesting formal coincidence, not a principle.

**7.4 Restraint in speech.** Proverbs 10:19, 17:27–28, 21:23; Ecclesiastes 5:2; James
3:1–12. Suggests `communication`: say less, and do not assert beyond what is known.
**Low-to-moderate** — recurrence is very strong, but the engineering translation collapses
into `factuality_enforcement` (5.3) without adding anything, so it is a duplicate rather
than an entry.

**7.5 The lost sheep.** Ezekiel 34:1–6 (the shepherds who did not search), Luke 15:3–7,
Matthew 18:12–14. Suggests `failure_recovery`: the failed minority is not written off
against aggregate success. **Low-to-moderate** — the Ezekiel form is a genuine indictment of
exactly the metric-driven reasoning that lets a 99.9% success rate hide a permanently
broken 0.1%, which is a real and useful point. Held at Tier 3 because the gospel passages
are about divine pursuit of persons, and using them for error budgets is the sentimental
misappropriation this exercise is most at risk of. If the point is wanted, take it from
Ezekiel 34 and leave Luke 15 alone.

---

## 8. Principles rejected, and why

A research pass that rejects nothing has not applied its own stated test. Eleven candidates
were considered and discarded. Several were attractive, and two survived several hours
before failing.

**8.1 Genesis 1 as declarative construction.** *Candidate:* the creation account's
"let there be" pattern models declarative specification — state the desired end state, let
the system realise it. *Rejected under Test 2 (constraint).* It forbids nothing. Any system
that initialises anything can claim the analogy, and no design decision would have gone
differently. This is the purest form of the failure this document is guarding against: it
sounds profound and is empty.

**8.2 The Trinity as three-tier architecture.** *Candidate:* a three-in-one structure as a
model for coherent subsystems under a single interface. *Rejected under Tests 2, 3 and 4.*
The correspondence is numerical, the doctrine is a centuries-long theological argument
rather than a design pattern, and the mapping predicts nothing whatsoever about how the
tiers should relate. It would also be a genuine misuse of a contested doctrine to decorate
an engineering diagram.

**8.3 Babel as an argument about system topology.** *Candidate:* Genesis 11 as a warning
against a single point of coordination — or, equally, against fragmentation. *Rejected
under Test 3 (falsifiability).* That "or equally" is the whole objection. The passage has
been read as a warning against centralisation *and* against the confusion of tongues, and a
text that supports opposite architectural conclusions supports neither. The narrative's own
subject is presumption, not topology.

**8.4 Eden's single prohibition as default-deny.** *Candidate:* one clear boundary in an
otherwise permitted space as a model for security policy. *Rejected because it argues the
opposite of UNDX's actual posture, and doing so honestly matters.* Eden is default-allow
with a single denial. UNDX is default-deny with enumerated permissions, and `feature_flags`
"defaulting closed" is a deliberate choice made for good reasons. The text does not support
the design we have. Including it and quietly reversing its polarity would have been the
single most dishonest thing in this document.

**8.5 The armour of God as a security-control taxonomy.** *Candidate:* Ephesians 6:13–17
mapped onto layered defence — helmet, breastplate, shield, sword. *Rejected under Test 3.*
The assignments are arbitrary and interchangeable; nothing determines which control is the
shield. Six items and six controls is not a correspondence, it is a coincidence of counting.

**8.6 Noah's ark as specification-driven design.** *Candidate:* Genesis 6:14–16's explicit
dimensions as a precedent for building to spec. *Rejected as redundant and strictly weaker
than 6.2.* The ark passage gives a specification but no conformance report; the tabernacle
material gives both, and the conformance refrain is the entire engineering value. Keeping
both would have padded the catalogue with a worse version of an entry already present.

**8.7 Casting lots as randomised selection.** *Candidate:* Proverbs 16:33 (the lot is cast
into the lap, but the decision is from the LORD) and Acts 1:26 as warrant for randomised
rollout, A/B assignment or load balancing. *Rejected under Test 4.* This one was tempting
because the surface vocabulary matches so well. But the text's subject is divine
sovereignty over apparent chance — it is making a claim about *what randomness is not*.
Recruiting it to justify statistical sampling means quoting a passage in direct opposition
to its own argument. That is the specific failure mode this document exists to avoid, and
it is the entry I am most glad to have caught.

**8.8 The seven-day week as sprint cadence.** *Candidate:* creation-week structure as
iteration timeboxing. *Rejected under Tests 2 and 4.* The durable principle in this
material is the limit (5.9), not the period. Reading it as cadence takes the least
important feature — the number seven — and discards the part that constrains anything.

**8.9 "Iron sharpens iron" as code review.** *Candidate:* Proverbs 27:17 as warrant for
peer review. *Rejected under Test 1 (recurrence) and Test 2.* It appears once, and the
engineering claim it supports — that collaboration improves work — is claimed by every
practice ever proposed. Compare 5.1, where the same general territory produces an actual
prohibition.

**8.10 "Ask and it shall be given" as request semantics.** *Candidate:* Matthew 7:7 as a
model for request/response. *Rejected as wordplay.* Listed because it illustrates the
failure mode at its most obvious, and because the more sophisticated rejections above are
the same error wearing better clothes.

**8.11 Jacob and Laban's flocks as adversarial optimisation.** *Candidate:* Genesis 30:31–43
as a study in gaming a specification — Jacob is given the oddly-marked animals and
manipulates the outcome. *Rejected under Test 4, narrowly.* The observation is real and
sharp: a reward specification that is technically satisfiable in unintended ways will be
satisfied that way, which is specification gaming exactly. But the mechanism in the text is
a folk belief about prenatal influence, so the passage does not actually describe the
phenomenon it appears to. Building on it would mean citing a text for a mechanism it does
not contain. The engineering point is worth making from Goodhart's law, where it belongs.

**Pattern in the rejections.** Nine of the eleven fail for one of two reasons: the analogy
constrains nothing (8.1, 8.5, 8.8, 8.9, 8.10), or it requires reading the text against its
own subject (8.3, 8.4, 8.7, 8.11). The first failure produces documents that feel wise and
change no decisions. The second produces documents that are actively dishonest about their
sources. Everything in sections 5 and 6 should be re-checked against those two failure
modes specifically.

---

## 9. Where the texts argue against UNDX's current design

A catalogue that only confirmed existing choices would be suspect, so this section is
mandatory rather than optional. Three cases.

**9.1 Default-allow versus default-deny.** As set out in 8.4, the Eden structure is
permission-by-default with a narrow prohibition, and the same shape recurs — the created
order is declared good and then bounded, rather than being closed and then opened. UNDX is
the reverse: `feature_flags` and `rollout_gating` default closed, `policy_engine` denies
unless allowed. **UNDX is right and the analogy is wrong**, for a reason the analogy itself
cannot supply: an agent that can act on somebody's account is not a garden, and the cost
asymmetry between a wrongly-refused read and a wrongly-permitted write is not remotely
symmetric. Recorded here to show that where text and design conflict, the text loses.

**9.2 The scale of the escalation hierarchy.** Jethro's structure (6.1) escalates *by
volume* through several layers before reaching the top. UNDX's confirmation model is
essentially two-level — either the policy engine decides or a human is asked. The text
suggests intermediate tiers. I do not recommend adding them: intermediate automated
approvers would multiply the number of components that can authorise a write, which
conflicts with the `governed_gateway` principle of a single path from intent to outcome.
Noting the disagreement rather than resolving it in the text's favour.

**9.3 Indefinite refuge versus bounded pause.** In Numbers 35 the manslayer remains in the
city of refuge until the death of the high priest — an *unbounded* wait, terminated by an
external event with no schedule. UNDX's confirmations expire on a timer. The text's model
would be a pending write that waits indefinitely for approval, which is operationally
untenable and would accumulate unbounded state. UNDX is right again; the ancient model
assumes a social context that keeps the case alive, and a request queue has no such thing.

---

## 10. Recurring patterns versus isolated verses: what the distribution shows

The brief asked for recurring patterns over isolated verses, and the distribution of what
survived is itself a finding.

**Every Tier 1 entry is about restraint, verification, or record-keeping. None is about
capability.** The corpus, read for engineering guidance, has a great deal to say about what
a powerful actor should refrain from doing and how a claim should be checked, and nearly
nothing to say about how to make a system do more. This is not neutral — it means the
exercise systematically favours the safety-and-governance half of UNDX (the `owned`
responsibilities, mostly) and offers almost nothing to the cognitive half (`planning`,
`reasoning`, `specialist_domains`, `provider_routing`). Anyone using this document to set
priorities should know it has a thumb on the scale.

**The strongest entries cluster in legal material, not in wisdom or narrative.** Entries
5.1, 5.2, 5.3, 5.7 and 5.9 are legislative. This makes sense: legal codes are the genre
whose actual purpose is specifying constraints on behaviour under adversarial conditions,
which is the same job as a policy engine. The wisdom literature produced the best
*cognitive* observation (6.5, anchoring) but weaker engineering constraints, and the
narrative material produced the weakest entries overall, since narratives are about
particular people and generalising from them is exactly what 8.11 got wrong.

**Cross-cultural attestation tracked engineering value closely.** The three entries with
the clearest ANE parallels — boundary markers, honest weights, witnessed sealed deeds — are
among the strongest in the catalogue. That is what Test 1 was really measuring: a concern
that several unrelated legal cultures independently legislated against is tracking a
durable way people defraud each other, and a system that mediates between parties will meet
the same behaviours.

**The single-verse candidates almost all failed.** Of the entries built on one passage
(6.1, 6.4 partially, 7.1, 7.3, 8.9), only one reached Tier 2 and none reached Tier 1. The
recurrence requirement did real work rather than acting as a formality.

---

## 11. UNDX components with a strong scriptural analogue

Twenty-one of the forty-eight responsibilities are touched by a Tier 1 or Tier 2 entry:

| Component | Entry | Ownership in registry |
|---|---|---|
| `verification` | 5.1 | owned |
| `adversarial_check` | 5.1, 5.8 | owned |
| `trust_model` | 5.1, 5.3 | owned |
| `authorization_scope` | 5.2, 6.4 | owned |
| `owner_scoped_reads` | 5.2, 6.4 | owned |
| `memory_isolation` | 5.2 | partial |
| `factuality_enforcement` | 5.3 | owned |
| `degradation_tracking` | 5.3, 5.5 | owned |
| `prediction` | 5.4, 5.8 | partial |
| `planning` | 5.4 | partial |
| `metacognition` | 5.5, 5.8 | partial |
| `communication` | 5.5 | owned |
| `audit_receipts` | 5.6 | owned |
| `canonical_identity` | 5.6 | owned |
| `memory_approval` | 5.6 | owned |
| `confirmation` | 5.7, 6.1 | owned |
| `policy_engine` | 5.7, 6.1 | owned |
| `failure_recovery` | 5.7, 6.3 | owned |
| `corpus_governance` | 5.8 | owned |
| `prompt_injection_boundary` | 5.8 | partial |
| `homeostasis` | 5.9 | partial |

Plus, from Tier 2: `capability_registry`, `request_contract`, `argument_validation`,
`qa_gating`, `idempotency`, `memory_relationship`, `goal_understanding`, `attention`,
`working_context`, `memory_conversation`, `memory_fact`, `memory_learning_event`,
`memory_preference`, `action_selection`, `feature_flags`, `rollout_gating`.

The concentration in `owned` components is worth noticing. The principles land hardest on
the parts of UNDX that are already finished, which limits their practical value — a
principle that ratifies completed work does not tell you what to build next.

## 12. UNDX components with no scriptural analogue, and what that absence means

Eleven responsibilities attracted nothing at any tier: `governed_gateway`, `task_persistence`,
`reasoning`, `specialist_domains`, `native_context_validation`, `deep_links`,
`product_knowledge`, `skill_lifecycle`, `provider_routing`, `evidence_state_machine`,
`memory_task_state`.

They fall into two groups, and the difference is instructive.

The first group is **irreducibly modern**: `provider_routing`, `deep_links`,
`native_context_validation`, `product_knowledge`. These are about the specific technical
situation of a mobile application talking to model providers. No ancient text bears on
them, and the absence tells us nothing except that the ancient world lacked mobile
applications.

The second group is more interesting, because these are **general engineering concerns that
the corpus simply does not address**: `governed_gateway` (a single path from intent to
outcome), `evidence_state_machine` (explicit states with legal transitions),
`skill_lifecycle` (a progression from discovered to available). These are all concerned
with *the architecture of a process* rather than with the ethics of an actor. The corpus
addresses agents, obligations, and evidence; it does not address funnels, state machines or
pipelines, because those are ways of organising a mechanism and its authors were not
organising mechanisms. This is the clearest boundary of the exercise: the biblical material
is a source of constraints on behaviour, and it has essentially nothing to offer about
structure.

Which means: **do not use this document for architectural decisions.** Use it, if at all,
for decisions about what the system is permitted to claim and permitted to do.

## 13. Already implemented versus new work

Honest accounting, because it determines whether this exercise was worth the hours.

**Already implemented, and not because of this document (9 of 9 Tier 1 entries).** Every
Tier 1 principle corresponds to something UNDX already has, built for ordinary engineering
reasons before this research existed. Independent read-back verification, owner-scoped
reads, degradation propagation, bounded ceilings that refuse, confirmation before
irreversible writes, durable audit receipts, untrusted corpus handling — all of it predates
this pass. The correspondences were noticed afterwards. **The catalogue confirmed existing
decisions; it did not generate any of them.**

**Proposals that are genuinely new, all small (4).**

1. *Scale corroboration strictness to reversibility* (5.1). Verification currently applies a
   uniform read-back. The principle argues an irreversible write should demand more.
   Cost: moderate. Value: real but not urgent.
2. *Make scope-widening an audited event* (5.2). Enforcement is solid; changes to the
   permission record are not themselves receipts. Cost: low. Value: high, and this is the
   one I would actually do.
3. *Audit for a second definition of success* (5.3). Grep-level exercise: does any code path
   define "complete" or "verified" more leniently than the strict definition? Cost: an
   afternoon. Value: unknown until run, which is precisely why it should be run.
4. *Audit for load-responsive ceilings* (5.9). Does any path raise a limit because the
   current request needs it? Cost: an afternoon. Value: high if anything is found.

**Verdict on the exercise.** Two cheap audits and one small feature, against a full research
pass. Judged as a source of new engineering work, the return is poor and I should say so
plainly. Judged as an independent cross-check on a governance model — a completely
unrelated tradition of thought about constraining powerful actors, arriving at
substantially the same constraints UNDX arrived at — the convergence is worth something,
though it is worth less than it feels like, because I was looking for the convergence and
found what I looked for.

## 14. Candidate changes, ranked, with honest costs

1. **Audit for a second definition of success** (5.3). Half a day. Finds a real class of
   defect or proves its absence; either outcome is worth having. Zero risk.
2. **Audit for load-responsive ceilings** (5.9). Half a day. Same shape. Zero risk.
3. **Receipt on authorization-scope change** (5.2). A few days. Risk: receipt volume; needs
   a threshold so that ordinary flag evaluation does not drown the log.
4. **Reversibility-scaled verification strictness** (5.1). A week or more. Risk: real. It
   makes irreversible operations slower and more failure-prone at exactly the moment the
   user is waiting, and a stricter check that times out is worse than a weaker one that
   completes. Would need its own design pass.
5. **Enforced retention schedules on non-preference memory** (6.6). Substantial, and should
   be driven by legal and regulatory requirements rather than by this document. Listed for
   completeness; do not sequence it from here.

Items 1 and 2 are the only ones I would recommend on the strength of this document alone.
Items 3 through 5 need an independent engineering case, and if they cannot get one they
should not happen.

## 15. Risks of this exercise

**Theological.** Using scripture instrumentally — mining a religious text for engineering
utility — is a real category of misuse, and reasonable people including believers find it
objectionable. The rejections in section 8 are partly a defence against it, but only
partly. Anyone who finds the whole exercise inappropriate has a case that this document
cannot answer.

**Engineering.** The largest risk is *post-hoc justification*: a decision gets made for
ordinary reasons, an entry from this catalogue gets attached to it afterwards, and the
decision becomes harder to challenge because challenging it now looks like challenging the
principle. This is why the rule at the top forbids scriptural appeal in commits, comments
and reviews. That prohibition is the single most important line in the document and it is
the one most likely to erode.

**Organisational.** A shared codebase is not a shared faith. Framing engineering standards
in religious terms in a professional setting excludes colleagues who do not share the
tradition and makes disagreement feel like something other than technical disagreement. The
catalogue's *conclusions* — independent verification, uniform standards, audited boundary
changes, hard ceilings — are entirely defensible in ordinary engineering language and
should always be stated that way in shared artefacts. This document is a private research
input, not a style guide.

**Epistemic.** I went looking for convergence and found it, across a corpus large enough to
supply material for almost any thesis. Sections 8 and 9 are the counterweight, and they are
the sections a sceptical reader should read first. If they are thinner than sections 5 and
6, that asymmetry is itself evidence about how hard I actually looked.

## 16. How to falsify the claims in this document

Each of these would refute something specific.

**On individual entries.** An entry fails if its citations are wrong at the verse level;
check them against a critical text. An entry fails Test 2 if you can describe a plausible
system that satisfies the principle and still does the thing it was supposed to forbid. An
entry fails Test 4 if the engineering recommendation cannot be defended in a design review
where scripture is not admissible — which is the test to apply if you only apply one.

**On the catalogue as a whole.** The strongest falsification is a *counter-catalogue*: take
the same corpus, look for principles supporting the opposite of each Tier 1 entry, and see
how easy it is. If a comparably strong set of nine can be assembled arguing for
default-allow, for single-witness sufficiency, for unbounded work and for unaudited
authority, then the method selects for the researcher's priors and this document's
convergence is worthless. I have gestured at this in section 9 with three cases and did not
attempt it systematically. **Somebody who did not write this document should.**

**On the historical claims.** Section 3 and the context notes are the weakest material.
A specialist reviewing them would likely find errors, and any error there weakens the
recurrence argument that Tier 1 rests on, since recurrence claims depend on the texts being
genuinely independent rather than one being a redaction of another.

**On the practical claim.** Section 13 asserts that the exercise generated two cheap audits
and one small feature. Run the two audits. If they find nothing, the practical yield of
this document is one moderate feature proposal, and that should be stated in any future
version.

## 17. The closing question, answered

> **Which biblical principles appear universal enough to strengthen UNDX regardless of the
> underlying AI model, programming language, or future technology?**

Four, and the reason they survive technological change is the same in each case: they
constrain **the relationship between an actor and the people affected by it**, and that
relationship does not change when the implementation does.

**First, that one witness does not establish a matter** (5.1). Whatever produces an action,
whatever language it is written in, the entity that performed the action is not a competent
witness to its own success. This is not a fact about software; it is a fact about the
structure of evidence, which is why a seventh-century BCE judiciary and a 2026 agent
runtime arrive at the same rule. It will apply to a model architecture nobody has invented
yet, because that model will still be reporting on itself.

**Second, that one standard of measure applies regardless of who it favours** (5.3). The
temptation to hold two thresholds — a strict one for claims that embarrass and a lax one
for claims that flatter — is not a property of any technology. It is a property of any
system that both acts and reports on its own action, and every AI system does both. The
diagnostic form is what makes it durable: *the presence of a second standard is the
finding*, before anyone asks which one is being applied. That check works on any codebase in
any language.

**Third, that a limit is a limit when it is inconvenient** (5.9). Every generation of
technology brings a new reason why this particular request should be allowed to exceed the
ceiling, and each reason is locally persuasive. The principle's specific contribution is
temporal rather than technical: limits set under load are not limits, so they must be set
in advance and be immune to the code path that wants to exceed them. That holds for token
budgets, for plan depth, for spend, and for whatever the equivalent resource is in ten
years.

**Fourth, that boundaries are recorded and moving the record is its own offence** (5.2).
Any system holding data for more than one party has boundaries between them, and the
boundary is always represented somewhere. Whatever the storage technology, whoever controls
the representation can move it silently — the offence is invisible by construction, which
is what makes it need a rule rather than a witness. This is the one I would carry forward
most confidently, because its independent attestation across several unrelated ancient
legal cultures suggests it is tracking something about how parties with asymmetric power
actually behave, rather than something about any one tradition's ethics.

**What these four share, and what it implies.** None of them is about capability, none is
about structure, and none would help anyone build anything. All four constrain what a
powerful actor may claim and may do when the constraint is costly. That is the only
category in which this corpus has durable engineering value, and — as section 12 argued —
the reason is that its authors were writing about agents and obligations rather than about
mechanisms.

Which suggests the honest general answer to the question. The principles that survive
technological change are the ones that were never about technology: they are about the
asymmetry between someone who can act and someone who must live with the action. UNDX has
that asymmetry at its centre, and will continue to have it under any model, any language,
and any future technology. **That, and not any particular verse, is why the exercise found
anything at all.**

---

## Appendix: entry index

| # | Principle | Primary sources | Tier |
|---|---|---|---|
| 5.1 | One witness is not enough | Deut 19:15; Num 35:30; Matt 18:16; 2 Cor 13:1; 1 Tim 5:19; Heb 10:28 | 1 |
| 5.2 | Do not move the boundary marker | Deut 19:14, 27:17; Prov 22:28, 23:10; Job 24:2; Hos 5:10 | 1 |
| 5.3 | One standard of measure | Lev 19:35–36; Deut 25:13–16; Prov 11:1, 20:10; Ezek 45:10; Mic 6:11; Amos 8:5 | 1 |
| 5.4 | Count the cost first | Luke 14:28–32; Prov 24:27 | 1 |
| 5.5 | The watchman must report | Ezek 3:16–21, 33:1–9; Isa 21:6–12; Hab 2:1 | 1 |
| 5.6 | Sealed deed, open copy, jar | Jer 32:9–15; Josh 4; Deut 31:24–26; Esth 6:1; Ezra 6:1–2 | 1 |
| 5.7 | Refuge before judgement | Num 35:9–34; Deut 19:1–13; Josh 20 | 1 |
| 5.8 | Test the claim, spare no source | Deut 18:21–22, 13:1–3; 1 Thess 5:21; 1 John 4:1; Acts 17:11 | 1 |
| 5.9 | A limit kept when inconvenient | Exod 20:8–11, 23:10–12, 34:21; Deut 5:12–15; Lev 25 | 1 |
| 6.1 | Escalation by weight of matter | Exod 18:13–26; Deut 1:9–18 | 2 |
| 6.2 | Specification, then conformance | Exod 25–31, 35–40; 1 Kgs 6–7; Ezek 40–43 | 2 |
| 6.3 | Restitution defined per offence | Exod 22:1–15; Lev 6:1–5; Num 5:5–8; Luke 19:8 | 2 |
| 6.4 | The edges of the field | Lev 19:9–10, 23:22; Deut 24:19–21; Ruth 2 | 2 |
| 6.5 | Answering before listening | Prov 18:13, 18:17, 20:25; Jas 1:19 | 2 |
| 6.6 | Release at a fixed interval | Deut 15:1–11; Lev 25:8–17 | 2 |
| 7.1–7.5 | Recorded, not recommended | see section 7 | 3 |
| 8.1–8.11 | Rejected | see section 8 | — |

*Verse references should be checked against a critical text before this document is quoted
anywhere outside the team. See section 2, "On my own reliability as a source".*




