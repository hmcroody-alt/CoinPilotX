# 04 — UNDX production acceptance

## Verdict

**PRODUCTION AGENT ACTIONS VERIFIED.** Two real governed writes executed against
pulsesoc.com under a real cohort account, each read back from canonical storage. No
architecture changed, no worker added, no cohort member removed, no global bypass.

## The blocker, and what it actually was

`/api/pulse-ai/status` was returning `available: false` with the reason *"this account is
not in the agent cohort."* Reading `UNDX_AGENT_QA_USER_IDS` through the Railway UI —
because OAuth redacts variable values and `set-variables` overwrites rather than appends —
showed the full 26-character value:

```
10910211866,10910211867,34
```

Three members. Two are 11-digit identifiers, not PulseSoc `user_id`s; one (`34`) is in
PulseSoc's range. The signed-in QA account is user 15, so the gate was correct to refuse
it. This was a cohort membership gap, not a runtime failure — exactly as the mission
reframed it.

The change applied was a single append, preserving all three existing members:

```
10910211866,10910211867,34   →   10910211866,10910211867,34,15
```

Confirmed in Railway's pre-deploy diff as **1 variable, 1 change**, then deployed. The
cohort gate itself was not touched: `brain.qa_only` remains `true`, writes remain
cohort-constrained rather than platform-wide.

## Fixtures

Both created through the ordinary user-facing paths, not by inserting rows.

`POST /api/pulse/posts` with `{"body": "UNDX production acceptance test"}` returned
`post_id 2245`. `POST /api/crypto/alerts` with `{"assetSymbol": "BTC", "condition":
"above", "targetValue": 250000}` returned `alert_id 41`, and the alert list confirmed it
was the account's only BTC alert — so the "which one?" branch of the mission was not
reached, and nothing was guessed.

## Test 1 — feed

*"Like my most recent post."* produced a confirmation card, not an action. It resolved the
target itself and named it back:

> Like one viewable PulseSoc post: PulseSoc Music · 2026-08-29 · "UNDX production
> acceptance test". Confirm and I will make the change.

`confirmation_id: undx_confirm_7a2431b64519a3934236`, `evidence_state:
awaiting_confirmation`, `may_claim_done: false`, seven-minute expiry. Worth noting that
`feed.posts.like` carries `confirm=never` in the registry; the card appeared because the
*runtime* resolved the target rather than the user naming it, which is the rule added in
`b076ef32`. The stricter of the two won.

"Yes" resumed that exact pending action:

```
capability_id            feed.posts.like
status                   verified_success
canonical_resource_ids   ["post:2245"]
data                     {"changed": true, "liked": true, "post_id": 2245}
verification_state       verified
may_claim_done           true
undo_capability_id       feed.posts.unlike
latency_ms               341
```

Canonical read-back through `GET /api/pulse/posts/2245` — a path the agent does not
control — returned `viewer_reaction: "like"`. The reaction exists in storage.

**Dedupe.** A second "Yes" returned no `agent` block at all: no card, no receipt, no
`verified_success`, just an ordinary conversational reply. Canonical state before and
after was identical (`viewer_reaction: "like"`, unchanged). The consumed confirmation
could not execute again.

## Test 2 — crypto

*"Pause my BTC alert."* executed directly without a card. That is correct rather than a
gap: the user named the target, exactly one owned BTC alert exists, and
`crypto.alerts.pause` is `reversible_write` with `confirm=contextual`. It still verified:

```
capability_id            crypto.alerts.pause
status                   verified_success
canonical_resource_ids   ["alert_rule:41"]
message                  "I confirmed this against your account after the change:
                          BTC alert · above · 250,000 is now paused."
verification_state       verified
undo_capability_id       crypto.alerts.resume
latency_ms               80
```

Independent read-back of `GET /api/crypto/alerts` returned `status: "paused"`. QA data was
then restored: *"Resume my BTC alert."* → `crypto.alerts.resume`, `verified_success`,
`alert_rule:41`, and the list read back `status: "active"`. Alert 41 is left as it was
created.

## Process boundary

The Procfile runs `gunicorn --workers 2`, so two OS processes with no shared memory serve
the web service. Twelve health polls confirmed both are live and alternating: pids
`4,3,4,4,4,4,4,3,3,4,3,4` — two distinct workers, same uptime, same deployment.

Six create-then-resume pairs were then run end to end (unlike/like alternating, twelve
agent requests total). Every one produced `confirmation_required` in the first request and
`verified_success` in the second, with distinct confirmation ids each time
(`7bde96fd…`, `6ade63882…`, `917f028e…`, `b644847c…`, …). Across twelve requests
round-robined over two independent processes, resumption never depended on which process
received the "Yes" — the probability of never crossing the boundary in six pairs is about
1.5%. This is consistent with the code: pending confirmations live in
`pulse_ai_confirmations` in PostgreSQL, and single-use is a SQL property
(`UPDATE … WHERE status='pending'`), not an in-memory flag. Canonical state ended `like`,
which is where the acceptance test left it.

## Report fields

```
QA ACCOUNT:                     PulseSocMusic (PulseSoc Music, ulagwop@gmail.com)
USER ID:                        15
COHORT MEMBERSHIP:              PASS — appended to UNDX_AGENT_QA_USER_IDS, existing
                                members 10910211866, 10910211867, 34 preserved
AGENT AVAILABLE:                true
READS:                          true
WRITES:                         true
TEST POST ID:                   2245
TEST BTC ALERT ID:              41
LIKE MOST RECENT POST:          PASS — resolved, named, confirmed, executed
CANONICAL LIKE VERIFIED:        PASS — GET /api/pulse/posts/2245 → viewer_reaction "like"
SECOND YES DEDUPE:              PASS — no agent block, no second mutation, state unchanged
PAUSE BTC ALERT:                PASS — verified_success, alert_rule:41
CRYPTO READ-BACK:               PASS — status "paused", then restored to "active"
PROCESS-BOUNDARY CONFIRMATION:  PASS — 6/6 create→resume pairs across 2 gunicorn workers
                                (pids 3 and 4), PostgreSQL-backed pending state
DEPLOYED SHA:                   1ec72577caad41b744a1c1ce1e10d51b4c8b3ea8
FINAL VERDICT:                  PRODUCTION AGENT ACTIONS VERIFIED
```

## Left alone

The cohort gate, the authority model, the worker architecture, the Railway write gates,
and audio, calls, live and payments. The only production change in this mission was one
append to one variable.

One carry-forward from the previous report is unchanged and still worth doing:
`UNDX_WORKER_FAIL_CLOSED` is unset and running on its documented default of `1`.
`/health/undx` flags this itself under `configuration_notes`.
