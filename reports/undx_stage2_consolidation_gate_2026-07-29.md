# UNDX Stage 2 — Research Consolidation and Capability Gate

**Date:** 2026-07-29
**Branch:** `release/undx-nexus-core-v4`
**Tracks consolidated:** social relationships, saved content, messaging
**Gate:** `classify_readiness()` in `services/undx_knowledge_map.py`
**Suite:** 256 tests in `tests/undx_agent/`, 0 failures

---

## Decision

**NO-GO on wiring any new write capability in the three researched tracks.**

Twenty-nine records span the three tracks. Twelve of them are `READY TO WIRE`, and **all
twelve are reads**. Every write in all three tracks is blocked, and the blockers are not
scheduling gaps — they are authorization defects, missing domain services, and writes that
cannot be read back.

The single exception worth naming is `conversations.mute`, which is the only genuinely
well-shaped write found anywhere in messaging: it sets a desired expiry from a named choice
rather than toggling, and the expiry is readable. It is still `VERIFIER REQUIRED`, because
the read path it would use routes through `_conversation_access`, which is the same function
that carries the existence oracle. Wiring it means writing a directed verifier first.

This decision is consistent with the standing mission constraint that no irreversible message
send be implemented in this phase, and it does not depend on that constraint: `messages.send`
would be blocked on its own merits regardless.

---

## The nineteen points

### 1. Scope of the consolidation

Three read-only research tracks, run against source and merged into one matrix. No
implementation code was edited during research, no capability was registered, no raw SQL was
added to UNDX, and the gateway was not weakened. The only implementation edits in this
document are to the gate itself and to the map's own validation, both described in points 16
through 18 and both made because the gate was misreporting.

### 2. Track sizes and outcomes

| Track | Records | Ready to wire | Blocked |
|---|---|---|---|
| Social relationships | 10 | 3 | 7 |
| Saved content | 4 | 2 | 2 |
| Messaging | 15 | 7 | 8 |
| **Total** | **29** | **12** | **17** |

### 3. Whole-map position

152 records, 80 registered. `READY TO WIRE` 94, `DOMAIN SERVICE REQUIRED` 39,
`VERIFIER REQUIRED` 10, `AUTHORIZATION DEFECT` 7, `UNSUPPORTED` 2, `TOGGLE HAZARD` 0.

`TOGGLE HAZARD` reads as empty and should not be read as absent. Both toggling records are
also service-missing, so under the mandated precedence they surface as `DOMAIN SERVICE
REQUIRED` — the thing that must be built first. Point 17 explains why that is safe.

### 4. Every ready-to-wire record in these tracks is a read

`saved.items.list`, `social.followers.list`, `social.follow`, `social.unfollow`,
`conversations.list`, `conversations.summarize`, `messages.list`, `messages.search`,
`messages.suggest`, `messages.draft`, `search.messages`, `saved.post.set`.

`social.follow`, `social.unfollow` and `saved.post.set` are writes and are verified — they
have domain operations and read-back verifiers, which is why they pass. They are already
registered; nothing new is being proposed here.

### 5. Authorization defect: the conversation existence oracle

`pulse_communications_v2/service.py:865-890` — `_conversation_access` loads the conversation
by global id **before** checking membership. A caller therefore learns whether an id exists
whether or not they are in it. Three capabilities inherit this: `conversations.get`,
`messages.send`, `messages.delete`. Any capability built on that function inherits it too,
which is why the defect is recorded against the function rather than only against the records.

### 6. Authorization defect: `send_message` joins you to rooms

`pulse_communications_v2/service.py:1205` passes `join_public=True` at line 1226. Sending to
a public room the caller is not in **silently joins them to it**. That is a membership change
the user did not ask for, produced by an operation that presents itself as a send. An agent
performing this on a user's behalf changes their group memberships as a side effect of
answering a question.

### 7. Authorization defect: `social.friend.decline` is unscoped

`bot.py:79817`. Decline omits the `AND status = 'pending'` guard that accept has at
`bot.py:79780`. It will transition a request that is already accepted or already declined, so
an agent retrying a decline **undoes an acceptance**. The contrast with accept is what makes
this a defect rather than a design: the correct guard exists twenty lines away.

### 8. Authorization defect: `voice_messages.send`

Previously classified `UNSUPPORTED` and therefore invisible. It carries the same existence
oracle as the rest of the messaging writes. Point 16 covers how it was hidden.

### 9. Verifier required: `is_blocked` is symmetric

`services/pulse_settings_routes.py:880`. It returns true when **either** party blocked the
other. Used as the verifier for `social.block.set`, it would report success for a block that
never landed, provided the other person had already blocked the caller. This is the sharpest
verifier defect found: the verification would be confidently wrong in exactly the case where
the user most needs it to be right.

### 10. Verifier required: `conversations.mark_read` mutates as a side effect of reading

`pulse_communications_v2/service.py:2439` — `list_messages` marks messages read. An agent
that reads a conversation merely to answer a question silently marks it read, which the user
did not request and cannot undo. This is a read that is secretly a write, which no amount of
verification downstream can correct.

### 11. Verifier required: `conversations.archive` is a one-way lockout

`pulse_communications_v2/service.py:2836`. No unarchive operation exists, and the archived
conversation drops out of the list the agent can see — so the agent cannot find it again even
to report on what it did. Irreversible *and* unobservable is the worst pairing available.

### 12. Domain service required: request-bound handlers

`social.block.set` and `social.mute.set` (`services/pulse_settings_routes.py:814`) read
`flask.request` directly. There is no operation taking `(user_id, target_user_id, blocked)`.
`social.friend.accept` (`bot.py:79780`) has the correct `status = 'pending'` guard but
performs the update inline in the handler. In all three cases the behaviour is correct and
the shape is wrong; the service has to be extracted before a capability can call it.

### 13. Domain service required: features that do not exist

`social.unfriend` — nothing removes a friend edge anywhere in the product, so there is no
undo and nothing to build on. `social.close_friends.set` — exists only as translated UI
strings in `mobile-native` i18n, with no route and no table writer. The second is recorded
deliberately: treating the presence of a translated string as evidence of a feature is
precisely the inference this map exists to prevent.

### 14. Domain service required: the two save toggles

`saved.reel.set` (`bot.py:78411`) and `saved.listing.set` (`bot.py:83106`). Both default to
`if want_saved is None: want_saved = not currently_saved`. Called twice with the same
arguments — which is exactly what a retry after a timeout does — the second call **unsaves
what the first saved**. The domain service, when written, must take an explicit desired state
and refuse a `None`.

`saved.post.set` is the counter-example and the template: it has
`services/saved_content_service.py:set_post_saved` paired with `get_post_saved`, and it is
`READY TO WIRE`.

### 15. Unsupported

`voice_messages.transcribe` — no transcription operation exists in
`pulse_communications_v2`. Recorded as unsupported after the authorization and service checks
found nothing more severe, which is the only position from which "unsupported" is an honest
label.

### 16. The gate was hiding defects behind `UNSUPPORTED`

`classify_readiness()` tested `UNSUPPORTED` first, inverting the two ends of the mandated
order. A capability that was both unsupported and authorization-defective classified as the
milder of the two, and the defect left the matrix entirely.

"Not building this yet" gets skimmed; "a caller can reach rows they do not own" does not — and
it stays true on the day the capability ships. Correcting the order moved
`voice_messages.send`, `moderation.queue.list` and `moderation.action.apply` out of
`UNSUPPORTED`, taking `AUTHORIZATION DEFECT` from 4 to 7 of 152.

`ReadinessPrecedenceTests` pins this, including a guard asserting that a record which is both
unsupported and defective actually exists — otherwise the precedence test would pass
vacuously.

### 17. The gate also inverted `TOGGLE HAZARD` and `DOMAIN SERVICE REQUIRED`

Found while writing this report, and it survived the first round of precedence work because
the test compared *sets* of class names. The tuple listed the mandated order; the classifier
evaluated two of its branches the other way round; every set-based assertion passed. A
precedence test that cannot see order is not a precedence test.

`test_the_classifier_tests_conditions_in_the_mandated_order` now reads the classifier's source
and asserts the branch order directly. Reading the source rather than synthesising records is
deliberate: a synthetic record has to be built from the same field semantics the classifier
uses, so getting them wrong produces a test that agrees with itself.

Restoring the order empties `TOGGLE HAZARD`, because both toggling records are also
service-missing. The hazard is not lost. `toggle_semantics` remains on the record and in
`known_limitations`, and `test_a_toggle_is_never_recorded_as_a_desired_state_write` still
fails if a toggling operation is registered or reaches `READY TO WIRE`. The class label names
what must be built *first*; the toggle is a constraint on how that service must then be
written, and point 14 is where it is written down.

### 18. The map's evidence did not resolve against the code it cited

Nine of twelve `bot.py` line citations had drifted — one named a `return jsonify(...)` in an
unrelated reel handler while claiming to be the friend-accept route. `bot.py` is roughly a
hundred thousand lines, so any edit above a cited line moves it silently.

This matters here more than anywhere, because points 5 through 15 are citations. A citation
that points at unrelated code is worse than no citation: it reads as verification and supplies
none. `tests/undx_agent/test_knowledge_map_grounding.py` now resolves every citation against
the real file. Details and the correction table are in
`reports/undx_phase3b_schema_grounding_audit_2026-07-29.md`.

Route validation was separately rejecting three correct records by comparing declared
*patterns* to concrete *paths*; `_route_matches()` now matches segment-wise while
`_LITERAL_OWNERS` keeps a record from filing itself under a catch-all when a more specific
screen owns the path.

### 19. What has to happen before any of this can be revisited

In dependency order, because several of these unblock more than one record:

1. Rewrite `_conversation_access` to check membership before loading, which clears the
   existence oracle from `conversations.get`, `messages.send`, `messages.delete` and
   `voice_messages.send` at once.
2. Remove `join_public=True` from the send path, or split it into an explicit join followed
   by a send, so that sending never changes membership.
3. Add the `AND status = 'pending'` guard to friend decline, matching accept.
4. Add a directed `has_blocked(actor, target)` alongside the symmetric `is_blocked`.
5. Extract domain operations for block, mute and friend accept, taking explicit arguments
   rather than reading `flask.request`.
6. Write `saved_content_service` operations for reels and listings on the `set_post_saved` /
   `get_post_saved` model, refusing a `None` desired state.
7. Stop `list_messages` from mutating read state; make `mark_read` an explicit operation.
8. Add `get_message(user_id, message_id)` so a send or delete can be read back at all.
9. Add an unarchive operation, or leave archive permanently out of scope.

Nothing in this list is speculative — each item is the specific thing named in the record it
unblocks, and each has a citation that now resolves.

---

## What this consolidation did not verify

- **No capability in these tracks was executed against a live database as part of this
  consolidation.** The tracks are source research; the readiness classes are claims about
  code shape, not about runtime behaviour. The eight repaired Phase 3B reads *were* executed
  live, and that is recorded separately in the schema grounding audit.
- **`social.follow` and `social.unfollow` are marked verified from the registry, not
  re-proven here.** They were verified in an earlier stage and are reported at that standing.
- **The count of 80 registered capabilities is unchanged by this work.** Consolidation
  registered nothing, per the mission constraint.
