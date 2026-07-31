# Batch 24 — a refused confirmation leaves something behind

## How this was found, including the part I got wrong

This batch began from a hypothesis that turned out to be false, and the false hypothesis
is worth recording because the real defect was only visible from inside it.

Batch 23 made a `CONTEXTUAL` approval actually get spent. That meant `consumed` became a
state a real approval could reach on the agent path for the first time. Batch 23's own
mutation work had also established that a replay never reaches the gateway —
`_agent_confirm` routes on `pending_confirmation_action`, which selects on
`status='pending'`, so a spent token returns `None` and the request falls through to the
legacy branch. My assumption was that it then hit the *legacy* 409 — "That confirmation
expired, was already used, or belongs to another account" — the exact sentence Batch 20
existed to delete.

That assumption was wrong. `confirm_action` answers a dead approval **before** the
V4/V5 gate, at what was then lines 1404–1408, and a probe against a real replay returned:

```
error       : confirmation_invalid
reason      : consumed
http_status : 409
message     : That confirmation was already used, so what it authorised has already
              been attempted. Check where things stand before confirming it again.
```

Batch 20's sentence, reached correctly. Nothing to fix.

What the probe also showed was the thing this batch is about:

```
correlation_id : None
log lines      : 0
rows written   : 0
```

That sentence tells a person to go and check where things stand. It gave them nothing to
check it *by*, and gave the server nothing at all. From the server's point of view, the
press did not happen.

## The defect

Three separate omissions, all of the same kind.

### 1. Seven of nine return paths discarded the id

`confirm_action` computes a `correlation_id` on its first line. It has nine return
paths. Two put it in the body — the `accepted_unverified` 202, and
`_agent_confirm_payload`. The other seven dropped it: every refusal, plus the legacy
success payload.

Nobody decided that. Each `return` was written on its own day and the id was not in
front of whoever wrote it. Which is exactly why the fix is not "add the id to seven
dictionaries" — that leaves the tenth path, whenever it is written, in the same
position.

### 2. A refusal was not recorded anywhere

No log line, no row. Batch 20's `consumed` answer is the sharpest case, but it applies to
every refusal: a fabricated token, a lapsed one, another account's. A support
conversation beginning "I pressed Confirm and it told me to check where things stand" had
no thread to pull.

### 3. The one log line that does run, ran blind

`pulse_communications_v2/routes.py::_timed_json` logged:

```python
payload.get("trace_id") if isinstance(payload, dict) else trace_id,
```

`pulse_ai_service` emits `"trace_id"` **zero** times and `"correlation_id"` **eleven**.
So the single request-level log line read `trace_id=None`, and the route's own freshly
computed `trace_id` was dead code, because these payloads are always dicts.

This is not scoped to the confirm endpoint. **88 route functions call `_timed_json`**
(AST count over `routes.py`), and every one of them whose payload comes from
`pulse_ai_service` logged `trace_id=None` for as long as that line existed. Proved
against real payloads before any change was made:

```
=== route functions calling _timed_json ===
88

SUCCESS   payload has correlation_id='3561ad436224'
          _timed_json logs  trace_id=None
          (route computed aabbccddeeff and discarded it: payload is a dict)

REJECTED  payload has correlation_id=None
          _timed_json logs  trace_id=None
          (route computed aabbccddeeff and discarded it: payload is a dict)
```

An earlier draft of this section said "89 endpoints … and all 89 logged
`trace_id=None`". Both halves were wrong, and the live log is what corrected them. The
count is 88, not 89. And it was never *all* of them: `pulse_communications_v2/service.py`
sets the key its own routes read — line 4238 `result.setdefault("trace_id", _trace())`,
plus lines 181, 185, 194 and 3955 — so `metric=api_active_calls` logged a real id
throughout, 3,885 times before the fix. The defect was confined to the endpoints whose
payload is built by `pulse_ai_service`, which emits `correlation_id` and never
`trace_id`. Stating it as "all 89" would have been a claim the evidence in
[the live demonstration](#the-live-demonstration) below contradicts on its own log file.

The correct precedence chain was **already written twelve lines below**, for the
call-route warning: `payload.get("correlation_id") or payload.get("trace_id") or
trace_id`. It was written once and not reused by the line above it.

### 4. And the audit row carried an id nothing else shared

`record_tool_result` was being handed `correlation_id=_trace()` — a freshly minted
second random id, for the audit row of an operation that already had one. The one record
that outlives the log retention window could not be joined to the request that caused it
or to the answer the person was given. An id nothing else shares is not a trace of
anything.

## What was built

`services/pulse_ai_service.py`

The body of `confirm_action` moved to `_confirm_action(user_id, payload, correlation_id)`
unchanged. `confirm_action` became a wrapper that:

* mints the `correlation_id` and passes it *in*, so the body still uses one id for the
  gateway call, the audit write and the two payloads that set it themselves;
* stamps the answer with `setdefault` — not assignment, so a path that already named its
  own trace keeps it, because such a payload is describing something the wrapper did not
  do and overwriting it would destroy the only pointer to it;
* logs one line for a refusal, and only for a refusal.

The audit write at what is now line 1537 takes the request's `correlation_id` instead of
`_trace()`.

`pulse_communications_v2/routes.py`

`_payload_trace(payload, fallback)` — the precedence chain, named once, and used by both
the timing line and the call-route warning. The timing line now passes
`_payload_trace(payload, trace_id)`.

### What is deliberately *not* logged

The token. A pending approval token is a live bearer credential — anyone holding it can
redeem the write — and it is the most obviously useful thing in scope when somebody
decides a refusal log looks thin. Its absence is asserted against a *live* token rather
than a dead one, and there is a mutation mode that puts it back.

`reason` is logged, and is safe to log, because the response already carries it and
`approval_state` is owner-scoped upstream: a fabricated token and another account's token
both report `unknown`. A test asserts that the log line for a foreign token is
byte-identical to the line for a fictional one, modulo the id — because a log that
distinguished them would undo Batch 20's indistinguishability property from behind, for
anybody who can read logs, on every probe an attacker cares to send.

## Tests

`tests/undx_agent/test_confirm_trace.py` — **27 tests**, all passing.

| class | asserts |
|---|---|
| `EveryAnswerCarriesTheIdTests` | six answer shapes — consumed, expired, fabricated, missing token, success, and two requests not sharing an id |
| `APathNobodyHasWrittenYetTests` | a return path this file has never seen is stamped anyway; a path naming its own id keeps it; the body and the answer agree |
| `TheRefusalIsRecordedTests` | exactly one line, joined by id, naming the shape; **never the token**; foreign and fictional logged identically; success not logged as refusal |
| `TheAuditRowSharesTheRequestIdTests` | the durable row carries the id the person was given |
| `TheLogLineReadsTheKeyThePayloadsCarryTests` | the resolver's precedence, its fallbacks, that it never returns `"None"`, and that the two ends keep the same key name |

Two things about this file are worth stating rather than leaving to be discovered.

**`_payload_trace` is compiled out of the shipped source, not imported.** `routes.py`
imports Flask at module scope and Flask is not installed in this sandbox. Stubbing an
entire web framework to reach one pure function would be a test of the stub. So the file
is parsed, the real `FunctionDef` is taken by name, and that is executed. It proves what
the helper computes. It does **not** prove `_timed_json` calls it — that second claim is
asserted separately, by walking the AST for the timing `logging.info` call and checking
its last argument, and the docstring says so in those words rather than somewhere it
could be mistaken for more.

**The audit test drives the legacy V4/V5 branch through a user outside the agent
cohort**, which is the only way that branch is reachable, and substitutes
`pulsesoc_notification_system` for the duration — because it opens its own connection and
writes through it while `_confirm_action` holds a write transaction on the same SQLite
file, which deadlocks one process doing what two normally do. Both facts are in the class
docstring. The branch being off wherever the agent runs is the reason the defect survived
this long, and is not a reason to skip proving the fix.

## Mutation results

`outputs/mutate24.py`, ten modes, **10/10 caught**.

| mode | destroys |
|---|---|
| `answer_is_not_stamped` | removes the stamp — the original defect, restored exactly |
| `stamp_overwrites_the_payloads_own_id` | `setdefault` → assignment; every answer still has an id, and a downstream trace is destroyed |
| `stamp_mints_a_second_id` | stamps a fresh id, so every answer has one and it matches nothing |
| `refusal_is_not_logged` | half the defect left standing |
| `every_answer_is_logged_as_a_refusal` | REFUSED next to a successful write — a false record, worse than none |
| `refusal_log_carries_the_token` | writes the bearer credential into the log |
| `audit_row_gets_its_own_id` | the durable row points at nothing |
| `timing_line_reads_the_old_key` | back to `trace_id=None` on all 89 endpoints |
| `resolver_prefers_the_key_nobody_emits` | swaps precedence — invisible except where it matters |
| `resolver_drops_the_routes_fallback` | logs the string `"None"` |

Five of these do not restore the original defect at all. `stamp_overwrites_...`,
`stamp_mints_a_second_id`, `every_answer_is_logged_as_a_refusal`,
`refusal_log_carries_the_token` and `resolver_prefers_...` restore the *mistakes this
fix invites*. Traceability is unusually easy to test badly: "the response has a
`correlation_id`" passes against a stamp that overwrites the id the payload already had,
against a stamp that matches nothing, and against a log line that leaks the token
alongside it. All three are worse than the defect being fixed.

### Two modes SURVIVED on the first run, and both found real holes in my own tests

This is the part of the batch worth the most, so it is not being smoothed over.

**`stamp_mints_a_second_id` SURVIVED.** The guard test asserted the body's id equalled
the answer's id — on the **success** path, where `_agent_confirm_payload` has already set
the key, so `setdefault` does nothing and a second minted id leaves no trace whatsoever.
The test could not fail on the property it named. It now asserts on a refusal, which is
the only place the property is observable, and the success case is a second test.

**`resolver_prefers_the_key_nobody_emits` SURVIVED.** The guard was
`test_it_prefers_the_key_the_services_actually_emit`, whose payload carried only
`correlation_id` — so swapping the precedence still returned it. The test asserted
*presence* and was named for *precedence*. It has been renamed
`test_a_payload_carrying_only_correlation_id_resolves`, and the guard now points at
`test_correlation_id_wins_when_a_payload_carries_both`, which is the only payload shape
that can observe the order. A test whose name claims more than its body checks is worse
than no test, because it is where somebody stops looking.

Both fixes are recorded in the test docstrings, naming the mutation mode that exposed
them, so the next reader knows why those tests are shaped the way they are.

Mechanics carried forward from mutate22/23: the untouched source is parked in
`outputs/.mutate24-original` before mutating and `heal()` runs first on any later
invocation; `run_suite` writes to a file rather than a pipe, because a child holding the
write end of an inherited pipe can block the read forever; and a run that executed zero
tests is refused outright, so a mis-typed guard name cannot read as SURVIVED. The
sidecar was verified empty and `git status` verified clean of source changes after the
final run.

## Regression

The whole of `tests/undx_agent` was re-run in three passes and **718 tests passed**.

| pass | tests | time |
|---|---|---|
| `test_[a-i]*.py` | 384 | 17.4 s |
| `test_[j-r]*.py` | 294 | 18.9 s |
| `test_[s-z]*.py` | 40 | 3.4 s |

Targeted runs of the suites nearest this change were also made individually before the
sweep: `test_confirm_path` + `test_dead_approval_says_which` + `test_spent_approval`
(52 tests), `test_adversarial` + `test_audit_durability` + `test_transport_wiring`
(54 tests), and `test_end_to_end` + `test_review_hardening` + `test_point_of_no_return`
+ `test_receipt_names_subject` (123 tests). All green.

**No Python test exercises `routes.py` at runtime**, here or anywhere in this
repository, because it cannot be imported without Flask. That is the honest bound on the
`_timed_json` half: its argument is asserted by AST, its helper is asserted by execution
of the real source, and the wiring between them is proven on the device rather than in
this suite.

## The live demonstration

Run on the iPhone 17 Pro Max simulator against the local backend, on the real
`/api/pulse-ai/actions/confirm` route. Both halves of this batch are things that either
appear in a log file or do not, so the evidence is the log file rather than a screenshot:
a correlation id is twelve hex characters, and reading twelve hex characters off a
screenshot and retyping them is a transcription, not a proof.

### Where the evidence actually lives

Not in the terminal. `bot.py:414` configures the root logger with a single
`RotatingFileHandler("coinpilotx.log")` at INFO, and the only stdout handler carries a
filter that passes lines containing `PUSH_TRACE`. So `UNDX_CONFIRM_REFUSED` and
`PULSE_COMM_V2_TIMING` go to `coinpilotx.log` and nowhere else. The backend launcher was
changed in this batch to tee its output to `logs/undx_backend.log` as well, which is
worth keeping for tracebacks that used to vanish with the window, but it is not where
this batch's evidence is and the report should not imply otherwise.

### Half one — the timing line, proved by a before and after in one file

The same log file spans both regimes, split at the 21:12 restart onto the fixed code.
Counted by metric:

```
=== BEFORE restart
    3885  metric=api_active_calls           REAL
       1  metric=conversations_list         REAL
       4  metric=pulse_ai_confirm_action    NONE
    6509  metric=pulse_ai_conversation      NONE
       4  metric=pulse_ai_message           NONE
=== AFTER restart
     437  metric=api_active_calls           REAL
       1  metric=pulse_ai_confirm_action    REAL
     731  metric=pulse_ai_conversation      REAL
       4  metric=pulse_ai_message           REAL
```

The BEFORE numbers are final; the AFTER numbers are a snapshot and keep climbing while
the app polls, which is the point — every one of them is REAL. 6,517 requests logged
`trace_id=None` before the fix and none after. `api_active_calls`
is the control: it was already REAL and stayed REAL, because its payload comes from
`pulse_communications_v2/service.py`, which sets `trace_id` itself. That row is what
disproves the earlier draft's "all 89", and it is also the useful half of the result —
the fix corrected the broken endpoints without disturbing the payloads that already
carried an id.

### Half two — one finger, two joined records, no token

"Change my ethereum alert to 777777" was typed into UNDX. Because
`crypto.alerts.update` is `risk: high, confirmation: True` in `services/undx_policy.py`,
it minted an approval rather than executing — row 10, `expires_at`
`2026-07-31T04:37:25+00:00`, which the card also printed as *Approval expires
2026-07-31T04:37:25+00:00*. The approval was left to lapse, and **Confirm** was pressed
at 04:38:33, sixty-eight seconds past the deadline, with the keyboard up — which is
Batch 22's fix still holding, since without it the first press would not have landed.

The app answered with Batch 20's `expired` sentence: *"That confirmation ran out of time
before it was used, so nothing changed. Ask again and confirm the new one."*

And the server, for the first time, recorded that this happened:

```
40405: 2026-07-30 21:38:33,136 - UNDX_CONFIRM_REFUSED user_id=10910211866 correlation_id=ff96be6afb90 error=confirmation_invalid reason=expired http_status=409
40406: 2026-07-30 21:38:33,136 - PULSE_COMM_V2_TIMING metric=pulse_ai_confirm_action duration_ms=112 method=POST path=/api/pulse-ai/actions/confirm ok=False status=None trace_id=ff96be6afb90
```

Two adjacent lines, same millisecond, one id — and `grep ff96be6afb90 coinpilotx.log`
returns exactly those two and nothing else. Before this batch the first line did not
exist and the second read `trace_id=None`, which is the pair of omissions this batch was
about, standing next to each other in one file.

What is *not* in that line was checked rather than assumed. The approval's
`confirmation_id`, its `token_hash`, and the strings `token` and `confirmation_token` are
all absent from the refusal line, and neither the id nor the hash appears anywhere in the
log. The token is a live bearer credential and this is the one place a refusal log is
tempting to fatten.

Two further facts, because "nothing changed" is a claim and not a courtesy. Row 10 is
still `status='pending'`, `consumed_at=NULL` — an expired approval is refused without
being spent, so the refusal did not quietly destroy anything either. And
`alert_rules` row 30 still reads `target_value=888888.0` with `updated_at` at
`04:30:10`, eight minutes before the press. The sentence the person was shown is
literally true, and now there is a server-side record they could be asked to quote.

All of those checks, with the commands that produce them, are written out in
`reports/evidence/batch24_live_log_extract.txt`. It is an extract rather than the log
itself, because `coinpilotx.log` is forty thousand lines and rotates at 50 MB — but every
line in it is quoted with its line number in the original, so the extract can be checked
against the source rather than believed.

### Two script defects found by running it rather than reasoning about it

Both in `restart_undx_live_backend.command`, both recorded in comments there.

`python bot.py | tee ... &` sets `$!` to the **tee** process, and `$!` is the entire
basis of the script's stale-server pid guard. That guard would have reported MISMATCH on
every healthy start and been turned off by the next person to hit it. Written with zsh
process substitution — `> >(tee -a ...)` — `$!` is still python's pid. Verified before
shipping it.

Then the run hung. Python line-buffers stdout when it is a tty and block-buffers it when
it is not, and the redirection made it a pipe: the terminal stopped mid-boot and the log
sat at 3,991 bytes while the server was healthy and serving. `python -u` fixes it. For a
log-shaped demonstration that failure is worse than no log, because the line you are
waiting for arrives after you have concluded it never will. I also read the stall wrong
at first — I took the last line, a `DeprecationWarning` from `bot.py:32857`, for a
crashed boot, when `bot.py:32857` is inside `api_pulse_native_sync_events()`, a request
handler. The server had been answering the whole time.
