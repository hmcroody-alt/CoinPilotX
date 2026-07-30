# Live simulator evidence — UNDX writes against the local backend

Device: iPhone 17 Pro Max simulator (iOS 26.5), app `com.pulsesoc.nativeapp.dev`.
Backend: `bot.py` pid 27317 on `http://127.0.0.1:5050`, started by
`restart_undx_live_backend.command` at 15:31:21.
Metro: port 8082, started by `restart_metro_local_backend.command` with
`EXPO_PUBLIC_PULSE_API_BASE_URL=http://127.0.0.1:5050`.
Signed-in account: `user_id=10910211866` (`nativeqa_phase3b_0729`), a member of
`UNDX_AGENT_QA_USER_IDS`.

Policy surface read by the serving process, from its own log:

    2026-07-30 15:31:21,155 - undx_policy_flags where=import pid=27317
    {'agent_enabled': True, 'reads_enabled': True, 'writes_enabled': True,
     'writes_kill_switch': False, 'capability_allowlist': [],
     'capability_denylist': [], 'qa_cohort_configured': True}

## What changed since the last session

Every prior simulator observation in this session — including the repeated
"UNDX is currently read-only. It can look things up but not change them." — was
produced by **production**, not by the local server whose flags were being edited.
The installed app is an Expo development client with no `main.jsbundle`; its
JavaScript is fetched from Metro at launch, so `EXPO_PUBLIC_PULSE_API_BASE_URL` is
a property of the shell that started Metro. Metro had been started without it, and
`mobile-native/src/api/config.ts` falls back to `https://pulsesoc.com`. The served
bundle contained ten occurrences of `https://pulsesoc.com` and zero of
`127.0.0.1:5050`. See `reports/app_backend_link_diagnosis.txt`.

## Turn 1 — "pause my btc alert" — recoverable fault

Reply on screen:

> Something went wrong on PulseSoc's side before I could do that, so nothing has
> changed. Please try again.

Card: `NOT DONE` / "Pause one crypto alert so it stops triggering" / PulseSOC.

Server log:

    2026-07-30 15:32:47,377 - undx_turn_faulted capability=crypto.alerts.pause
    user=10910211866 error=OperationalError

This is the Batch 10 card rendering correctly: a pre-executor fault reported as
`RECOVERABLE_FAILURE`, stating plainly that nothing changed, rather than falling
through to an unrelated conversational answer.

Cause: two rows had been seeded into the legacy `crypto_alerts` table for this
account. `alert_engine.list_alert_rules` calls `reconcile_legacy_alerts`, which
imports `crypto_alerts` rows into `alert_rules` on every read, and that import
raised. The rows were removed; the canonical table `alert_rules` already held
exactly one BTC rule for this account (`id=29`).

## Turn 2 — "pause my btc alert" — verified write

Reply on screen:

> Done — the current value is paused, and I read it back from PulseSoc to confirm it.

Card: `VERIFIED RESULT` / "Pause one crypto alert so it stops triggering" / PulseSOC.

Database read back independently, after the fact:

    id=29  symbol=BTC  status=paused  active=0  updated_at=2026-07-30T22:35:14

The card timestamp on screen (3:35) and the row's `updated_at` agree. This is the
first write in this session that was proposed, authorised, executed, verified by
read-back and rendered natively against the local server.

No confirmation card appeared on this turn, and that is correct:
`crypto.alerts.pause` carries `ConfirmationPolicy.CONTEXTUAL`, and an explicitly
phrased instruction resolving to exactly one resource is its own approval
(`undx_agent_policy.evaluate`, reason `explicit_single_resource`).

## Defect found live: the completed-write sentence names nothing

"the current value is paused" is the last generic branch of
`_write_state_sentence` in `services/undx_response_intelligence.py`:

    if observed not in (None, "", {}):
        return f"the current value is {clean(observed, 60)}"

It is reached because `crypto.alerts.pause` declares no `verified_fields`, and
`crypto_alert_status` returns a bare string (`"paused"`) as `observed` rather than
a mapping. So the loop over `spec.verified_fields` finds nothing and the fallback
fires.

Batch 16 taught the *confirmation* card to name its subject — `describe_alert`
renders "BTC alert · above · $999,999", composed to match `choiceRowsOf` on the
client. The *result* sentence never learned the same lesson. A person who asked to
pause one of several alerts is told "the current value is paused" and cannot tell
from that sentence which alert it was.

This is the same failure Batch 16 was written to remove, one screen later.

## Batch 16 — the confirmation card names what it is about to change

Typed: **"can you pause my btc alert"**. The hedge prefix "can you" is in `_HEDGES`,
so `is_explicit` is false and the contextual policy resolves to
`REQUIRE_CONFIRMATION` rather than `explicit_single_resource`. On screen:

> I need you to confirm this before I make the change.

Card: `CONFIRM ACTION` / "Pause one crypto alert so it stops triggering" /

> **BTC alert · above · 999,999: active → paused**

> Approval expires 2026-07-30T22:45:50+00:00

with `Cancel` and `Confirm`. Both buttons were initially clipped behind the
composer; a drag on the message list revealed them. Tapping **Confirm** produced:

> Done — the current value is paused, and I read it back from PulseSoc to confirm it.

with `Undo · Resume` and `Open in PulseSoc`. Database, read back independently:

    id=29  symbol=BTC  status=paused  active=0  updated_at=2026-07-30T22:41:48

The card named its subject. The receipt one tap later did not — the defect
recorded in the section above, seen here in the same conversation.

## Batch 17 — the two honest non-answers to a chooser

A second alert (`id=30`, ETH, above 888,888) was created so that "pause my alert"
would be genuinely ambiguous. It drew the chooser:

> More than one of your alerts matches that description.
>
> 1  ETH alert · above · 888888 · active
> 2  BTC alert · above · 999999 · active

**"i dont know"** →

> Nothing has changed and these are still here. Open your alerts to check which one
> you mean, then tell me the number.

**"both of them"** →

> UNDX changes these one at a time, so it needs one of them. Which number?

Both are the verbatim `CHOICE_MISS_UNDECIDED` and `CHOICE_MISS_EVERY_ROW` strings,
and both re-drew the chooser rows, so the question stayed answerable. Note that the
undecided reply deliberately does **not** end in "Which number?" — the person has
just said they do not know, and repeating the demand would be the failure. Neither
turn wrote anything: both rows remained `active` with unchanged `updated_at`.

## Batch 18 — a negation means no with or without the apostrophe

Both alerts were reset to `active`. The chooser ordered them 1=ETH, 2=BTC.

**"dont pause the first one"** (no apostrophe) →

    id=29  BTC  paused  updated_at=2026-07-30T22:44:45     ← acted on
    id=30  ETH  active  updated_at=2026-07-30T22:43:00     ← untouched, unchanged

The excluded row is provably untouched: its `updated_at` still carries its creation
time. Both rows were reset to `active` and the chooser re-ordered to 1=BTC, 2=ETH.

**"don't pause the first one"** (with apostrophe) →

    id=30  ETH  paused  updated_at=2026-07-30T22:45:54     ← acted on
    id=29  BTC  active  updated_at=2026-07-30T22:44:45     ← untouched, unchanged

The mirror image. The second run acts on the *other* row precisely because the
chooser order changed, which is what distinguishes a read of the sentence from a
fixed answer. The apostrophe is irrelevant to the reading, which is the property
Batch 18 exists to hold: `_NEGATION` enumerates the contraction stems
(`do|does|did|is|are|was|were|has|have|had|ca|wo|sha|ai|…`) before `n'?t\b`,
because a bare `n'?t\b` would match every word ending in "nt" — "want", "recent",
"component".

## The fix, and its proof on the device

`describe_alert` and `format_amount` moved into `services/undx_agent_contracts.py`,
the one module in the graph that the runtime, the verifier and the prose layer can
all import. `services/undx_agent_runtime.py` now imports them and keeps `_amount`
as a re-export for its existing call sites. `undx_verification.crypto_alert_status`
publishes the name of the row it just read back:

    evidence={..., "subject": describe_alert(rule), ...}

and `_write_state_sentence` renders it. The subject travels on the *verification
evidence*, never composed in the renderer — because `validate_consistency` discards,
silently and totally, any sentence containing a digit absent from
`plan.allowed_numbers`, and `_allowed_numbers` builds that set by scraping
`verification.evidence`. A label like "BTC alert · above · 999,999" carries digits.
Composed in the prose layer, every named receipt would have been thrown away and
replaced by the last-resort line — a worse outcome than the defect being fixed.
`tests/undx_agent/test_receipt_names_subject.py` asserts that rather than assuming it.

After restarting the backend from `restart_undx_live_backend.command` so the serving
process held the fixed code, **"pause my btc alert"** on the same device produced:

> I confirmed this against your account after the change: **BTC alert · above ·
> 999,999 is now paused.**

The PulseSOC card carries the same sentence. Database, read back independently:

    id=29  BTC  paused  active=0  updated_at=2026-07-30T23:00:48
    id=30  ETH  paused  active=0  updated_at=2026-07-30T22:45:54   ← untouched

The receipt now names the row it changed, in the same words the confirmation card
used, so "is this the one I approved?" is answerable by comparing two sentences.

Full UNDX suite after the change: **653 tests, OK**.

## Note on driving the simulator

Synthetic keystrokes into the composer produced a single "A" and opened the iOS
accent picker, which then swallowed everything that followed — the key press is
delivered with a long enough duration to read as press-and-hold. Writing the text to
the macOS clipboard and pasting with `cmd+v` into the focused field is reliable and
is what was used for the turns above.
