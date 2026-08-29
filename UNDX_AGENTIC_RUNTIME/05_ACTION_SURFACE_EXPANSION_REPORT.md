# 05 — UNDX production action surface expansion

## Verdict

**BUILD COMPLETE AND VERIFIED LOCALLY. PRODUCTION QA BLOCKED ON ONE DEPLOY.**

Six new governed write capabilities are wired end to end through all five files the
runtime requires, and the registry now stands at **107 capabilities / 35 writes** with
`unregistered_tool_names() == []` — meaning no capability can reach the gateway, raise
`tool_not_registered`, and fall through to the language model.

The one thing this report cannot claim is the thing the mission asked for last: a real
production action per newly activated domain. Local `HEAD` is
`1ec72577caad41b744a1c1ce1e10d51b4c8b3ea8` — the SHA the previous acceptance run confirmed
deployed — and that commit contains none of the six new capability ids, while the working
tree contains fourteen occurrences of them across eight modified files. So the expansion
is not in production, and the argument for that does not depend on trusting a remote: it
is not committed anywhere it could have been deployed from. The
expansion is committed to nothing yet because the agent sandbox cannot complete a git
write — it can create `.git/index.lock` but the mount refuses `unlink`, so `git add`
returns 0, leaves the index untouched, and leaves the lock behind. Running
`scripts/commit_action_surface_expansion.sh` locally and pushing is the whole of the
remaining gap. Reporting the QA as done without that deploy would be exactly the fake
success the mission forbids.

## What was already there, and was left alone

The mission named nine packs. Five were already complete and are untouched: **CRYPTO**
(19 capabilities, 10 writes), **NOTIFICATIONS** (7 / 3), **SETTINGS** (5 / 2), **PROFILE**
follow, unfollow and preferred-language, and **REELS** like, unlike, save, unsave. Rebuilding
any of them would have violated *"BUILD ON THE VERIFIED PRODUCTION AGENT RUNTIME. DO NOT
REBUILD THE FOUNDATION."*

**MARKETPLACE** is read-only by three capabilities and stays that way. The paused card
payment path was not enabled, not probed, and not referenced by any new capability.

## The six new capabilities

| Capability | Risk | Confirmation | Domain service |
|---|---|---|---|
| `feed.posts.hide` | reversible write | contextual | `pulse_feed_engine.hide_post` |
| `messages.mark_read` | reversible write | contextual | `pulse_communications_v2.service.mark_read` |
| `messages.send` | consequential write | **always** | `pulse_communications_v2.service.send_message` |
| `business.campaign.pause` | reversible write | contextual | `business_os.advertising.operations.pause_campaign` |
| `business.campaign.resume` | reversible write | contextual | `business_os.advertising.operations.resume_campaign` |
| `business.profile.update` | consequential write | **always** | `business_os.profile.api.update_profile` |

Pause and resume are each other's undo, wired symmetrically through `undo_capability_id`
and `undo_argument_map`, so a receipt for either carries a working reversal button. The
two `always` writes carry no undo and are honest about it.

## The security finding, and the fix

`undx_agent_tools.messages_send` began life with a docstring promising it sends "into a
conversation the caller is already a member of" and no code enforcing it. That promise
mattered more than it looked. `pulse_communications_v2.service.send_message` (line 1207)
calls `_conversation_access(..., join_public=True)` at line 1228, and lines 885–887 of
that helper respond to a public room the caller is not in not by refusing but by calling
`_add_participant` and continuing. A UNDX send aimed at a public conversation would
therefore have enrolled the person in it as a side effect — a membership change that no
confirmation card described, hidden inside an action whose receipt would have said only
"message sent."

The fix is a membership pre-check through `messenger_intelligence_service.get_conversation_read_state`,
whose SQL requires `membership_state='active'`, an empty `left_at`, and an active
conversation. A foreign conversation, a departed one, and a non-existent one all read as
`None` and all refuse identically, so the refusal is not an existence oracle either. The
executor refuses *before* `send_message` is entered, because once entered the join has
already happened.

`mark_read` was checked against the same hazard and is safe: it reaches
`_conversation_access` with the default `join_public=False`.

This closes the defect that `undx_knowledge_map` had pinned `messages.send` on since
Stage 2. The record moved from `_mapped`/`PARTIALLY_IMPLEMENTED` to `_live`, and its
authorization scope from `EXISTENCE_ORACLE` to `MEMBERSHIP_SCOPED` — but the map still
says, in its own limitations field, that the branch is contained at the UNDX boundary and
remains live for every other caller of `send_message`. The service was not declared
innocent; the agent path was fixed.

## Two design decisions worth stating

**`feed.posts.hide` deliberately has no target fallback.** Every other post capability
falls through to `resolve_recent_post` when the sentence does not name one. Hide does not,
and it is not an oversight: `hide_post` refuses the caller's own posts with a 400, so the
fallback would turn every vague "hide that post" into a guaranteed `write_rejected`
against a row the person never named. Hiding is about somebody else's post. It has to be
pointed at. `test_hide_never_falls_back_to_the_callers_own_recent_post` patches
`resolve_recent_post` and asserts it is never called.

**`messages.send` has no conversation fallback either.** The most recent thread is a
plausible guess, and a plausible guess is the one thing a send must not be built on.
Leaving `conversation_id` unset makes the gateway ask.

Both follow the mission's *"Never guess when ambiguous."* Verified live through
`resolve_arguments`:

```
'send a message to conversation 12 saying running late' -> {'conversation_id': 12, 'body': 'running late'}  chose=False
'send a message saying hello'                           -> {'body': 'hello'}                                 unresolved
'hide post 2245 from my home feed'                      -> {'post_id': 2245}                                 chose=False
'hide that post'                                        -> {}                                                unresolved
```

`chose=False` throughout: the runtime never selected a target the person had not named.
When it does — as with a campaign matched by name — `agent_chose_target` is set and policy
step 6a upgrades the action to require confirmation regardless of the registry's setting.

## Test evidence

`tests/undx_agent/test_action_surface_expansion.py` — **37 tests, 38 subtests, all passing.**

The first class tests the five-file wiring contract itself rather than any behaviour,
because the characteristic failure of this architecture is a capability that looks
registered, passes every service-level test, and dies in the gateway. It asserts registry
membership, an executor in `EXECUTORS`, a verifier in `VERIFIERS`, a `tool_name` in
`PRODUCTION_TOOL_REGISTRY`, an empty `unregistered_tool_names()`, that each verifier reads
through a different module than its writer, that both unrecoverable writes carry `ALWAYS`,
that pause and resume undo each other, and that no write leaves a field it changed
unverified.

The behavioural classes cover the two properties a service test cannot see: that a write
never lands on a guessed row, and that a write which did not happen is never reported as
one — a field held for review is a failure, a rejected field is a failure, an unchanged
value is a success with `changed: False`, and a foreign campaign never reaches the writer.
`FeedHideWritePack` drives a real SQLite fixture and counts rows in `pulse_post_hides`
directly rather than through the service the verifier calls.

Full suite: **931 passed, 16 failed.** All 16 predate this work and were triaged:
`test_saved_post_write_pack` (3) and `test_content_graph_intelligence_pack` (1) fail in
isolation on a fixture schema gap — `pulse_saved_collections has no column named
description`, from `saved_content_service.py:141` — and `test_knowledge_map_grounding`
carries 11 stale `bot.py` line-number citations. None are in the expansion's path.

Real-time audio gate: **clean.** `scripts/realtime_audio_change_gate.py --base origin/main
--head HEAD` inspected 9 changed files and reports no protected path touched.

## Production QA — the procedure, ready to run

Once the deploy is live, one real action per newly activated domain, each read back
canonically and restored, using the existing QA account (PulseSocMusic, user 15, already
in `UNDX_AGENT_QA_USER_IDS`):

**FEED** — hide a post belonging to another account, confirm `pulse_post_hides` gained the
row and the post left the Home feed, then confirm the post itself is untouched
(`deleted_at IS NULL`, `status='published'` — hiding is viewer-scoped, not a delete).
Restore is manual; there is no `unhide_post` service, which is reported below.

**MESSAGING** — `mark_read` on a conversation with unread messages, confirm `unread_count`
reads back 0. Restoring an unread count is not possible and is not a mutation worth
reversing.

**BUSINESS OS** — pause a campaign, confirm `operational_status` reads `paused` with
`funding_status` and `delivering` unchanged beside it, then resume it and confirm the
original status is back. This is the only new domain whose QA fully restores itself.

**`messages.send` is excluded from production QA by explicit instruction** — *"Build it,
but do not run send QA."* It is built, registered, unit-tested, and gated behind an
`ALWAYS` confirmation. The first real send is the user's to trigger.

## Report fields

```
TOTAL READ                     72   (all confirmation=never; a read is never gated)
TOTAL ACTION                   22   (writes not unconditionally gated:
                                     17 contextual + 5 never)
TOTAL ACTION_CONFIRM           13   (writes carrying confirmation=ALWAYS)
TOTAL REGISTERED               107  (35 writes: 27 reversible, 8 consequential)
TOTAL FORBIDDEN                70   (knowledge-map records the gateway refuses by name)

  Note on the 22: "contextual" is not "unconfirmed". Policy step 6a upgrades any
  write whose target the runtime resolved itself to REQUIRE_CONFIRMATION, so a
  contextual write reached by "like my most recent post" is confirmed while the
  same write reached by "like post 2245" is not. The floor is 13; the ceiling
  is 35.

NEW PRODUCTION ACTIONS VERIFIED
  FEED            feed.posts.hide                              built, tested — QA pending deploy
  MESSAGING       messages.mark_read                           built, tested — QA pending deploy
  MESSAGING       messages.send                                built, tested — QA excluded by instruction
  BUSINESS OS     business.campaign.pause / .resume            built, tested — QA pending deploy
  BUSINESS OS     business.profile.update                      built, tested — QA pending deploy
  CRYPTO          (10 writes)                                  previously verified in production
  NOTIFICATIONS   (3 writes)                                   previously verified in production
  PROFILE / FEED / REELS / SETTINGS                            previously verified in production
  MARKETPLACE     read-only by design; card payments untouched

FAILED ACTIONS                 none
SECURITY FAILURES              none outstanding.
                               One found and fixed during the build: messages_send could
                               silently join the caller to a public room via
                               send_message's join_public=True. Closed by a membership
                               pre-check that refuses before the service is entered.

MISSING APIs (no service-layer function exists; skipped per instruction, not built)
  profile.bio.update           no service; bio lives in a route handler
  social.block.set / clear     route-inline _mutate_relationship, pulse_settings_routes.py:817-880
  social.unmute                no service function at all. Its counterpart mute_user does
                               exist (pulse_feed_engine.py:1748) but writes pulse_user_mutes
                               while the route writes pulse_muted_users
                               (pulse_settings_routes.py:875) — two competing tables, so a
                               mute capability could not be verified by reading either one
                               alone and its unmute could not be written at all
  feed.posts.unhide            hide_post has no inverse
  reels.delete                 no owner-scoped delete service
  reels.comment / react        nothing beyond like and save
  feed.posts.report            no service
  marketplace.listing.*        consumer listing lifecycle is route-only
  linking.ts has no Business OS paths — BusinessProfile and BusinessOsAdvertising are
    registered in AppNavigator.tsx but unreachable by URL, so the three business
    capabilities point their receipts at /pulse/undx/actions rather than declare a route
    the client does not serve
  RootStackParamList.BusinessOsAdvertising types campaignId as number, while
    business_os.advertising.service mints ids with uuid4().hex — a correct id cannot be
    carried by the screen that would receive it

FINAL VERDICT
  EXPANSION BUILT AND VERIFIED. PRODUCTION ACCEPTANCE PENDING ONE DEPLOY.
```

## Process boundary

No architecture changed. No authority model changed. `UNDX_AGENT_QA_USER_IDS` was not
read, written, or overwritten in this mission. `brain.qa_only` remains `true`. No product
gate was bypassed and no capability was granted a route, a permission, or a confirmation
level looser than the one its risk class earns.

The commit is staged in `scripts/commit_action_surface_expansion.sh` rather than executed,
for the sandbox reason given at the top. The script re-runs the tests, re-checks the
registry totals, and re-runs the audio gate before it will commit anything.

## Left alone

Real-time audio, calls, livestream transport, the paused Marketplace card payment path,
payout security, `UNDX_AGENT_QA_USER_IDS`, the cohort gate, the worker topology, and every
capability that was already verified in production.
