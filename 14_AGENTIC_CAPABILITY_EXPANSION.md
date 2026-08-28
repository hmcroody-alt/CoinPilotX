# Mission 5 — Governed Agentic Action Expansion

**Scope.** Expand the existing authoritative UNDX capability registry with governed
write actions across PulseSoc, wire each one end to end, and prove both halves of the
mandate: that UNDX can *act*, and that it can never act on another user's private
resources.

**Result.** The registry went from 87 capabilities to 101. Fourteen new actions are
wired executor → verifier → capability spec → production tool ledger → deep link. A
new 28-test suite drives every one of them through the real runtime against a real
database. Three defects were found by that suite and fixed at the source; two of them
would have shipped as silent data loss.

---

## What was added

Fourteen capabilities across five domains. Every one is `self_account_only`.

| Capability | Risk | Confirm | Verifier | Undo |
|---|---|---|---|---|
| `crypto.watchlist.list` | read_only | never | — | — |
| `crypto.watchlist.add` | reversible_write | contextual | `crypto_watchlist_contains` | `crypto.watchlist.remove` |
| `crypto.watchlist.remove` | reversible_write | contextual | `crypto_watchlist_contains` | `crypto.watchlist.add` |
| `crypto.portfolio.holdings.list` | read_only | never | — | — |
| `crypto.portfolio.holding.add` | reversible_write | always | `crypto_holding_exists` | `crypto.portfolio.holding.delete` |
| `crypto.portfolio.holding.update` | reversible_write | always | `crypto_holding_values` | — |
| `crypto.portfolio.holding.delete` | consequential_write | always | `crypto_holding_deleted` | — |
| `notifications.mark_read` | reversible_write | contextual | `notification_read_state` | — |
| `notifications.mark_all_read` | reversible_write | always | `notifications_unread_count` | — |
| `presence.privacy.update` | reversible_write | always | `presence_privacy_value` | itself, negated |
| `localization.region.update` | reversible_write | contextual | `region_preference_value` | — |
| `localization.translation.update` | reversible_write | contextual | `translation_preference_value` | — |
| `settings.privacy.audience.update` | consequential_write | always | `settings_preference_value` | — |
| `settings.appearance.theme.update` | reversible_write | contextual | `settings_preference_value` | — |

Supporting work: four new read helpers in `services/portfolio_service.py`, ten new
verifiers in `services/undx_verification.py`, fourteen rows in
`PRODUCTION_TOOL_REGISTRY`, and one new deep link (`Watchlists: "pulse/watchlists"`)
in `mobile-native/src/navigation/linking.ts`.

Final registry state: 101 capabilities, 29 of them writes (72 read-only, 23
reversible, 6 consequential); 101 executors, 22 verifiers, 117 ledger entries, zero
unregistered tools, zero missing executors or verifiers.

---

## Design decisions worth recording

**Undo is left empty rather than approximated.** `crypto.portfolio.holding.delete`
destroys an amount and a cost basis that the delete preserves nowhere, so a restore
would have to invent them. `notifications.mark_read` has no inverse because PulseSoc
has no mark-unread anywhere in the product. In both cases naming a nonexistent
capability would fail `_validate_undo_graph` and naming an approximate one would put a
button on the receipt that lies. An absent Undo is honest; a wrong one is not.

**`crypto.watchlist.remove` takes a symbol, not a row id.** Symbol-to-id resolution
happens server-side against the caller's own rows. A model that could propose
`item_id` could name anyone's row; a model that proposes `BTC` can only ever name one
of the caller's. This is enforcement by argument design rather than by a preceding
check, and it is tested directly.

**`security` is unreachable by construction.** `SETTINGS_WRITABLE_GROUPS` is
`{"appearance", "privacy"}`. Two-factor, biometric unlock, and "require password for
sensitive changes" all live in `security`. A capability that reached them would be
privilege escalation wearing a settings receipt. The group is checked twice — the
field enum protects the argument, `_settings_patch` protects the call.

**Ownership is `WHERE user_id=?`, never a preceding check.** Foreign and nonexistent
resources return the identical refusal ("UNDX could not find that holding on your
account"), so the capability cannot be used as a membership oracle to enumerate
another account's rows.

**`settings.privacy.audience.update` is consequential, not reversible.** Widening an
audience makes content visible to people who could not see it a moment ago, and that
is observable outside PulseSoc the instant it happens. Reverting the setting does not
revert the disclosure.

---

## The QA suite

`tests/undx_agent/test_stage6_action_pack.py` — 28 tests, 46 subtests, all passing.

It runs against a real temporary SQLite file rather than mocks, because the isolation
under test is a property of the SQL. Mock the database and the tests pass for reasons
that have nothing to do with production. The fixture creates `portfolio_items`,
`watchlist_items` and `user_activity` itself, since `portfolio_service` has no
`ensure_schema` and has always relied on tables built by `bot.init_db()`.

Three groups:

*Can UNDX act?* Fourteen tests drive each capability through `undx_agent_runtime.handle`,
walking the confirmation gate via `pulse_ai_service.confirm_action` where one appears —
the same two-phase path the native client uses, not a shortcut around it. Every
assertion lands on a **service read**, never on the agent's own receipt. A receipt is
the agent's claim; the service read is the evidence.

*Can UNDX act on someone else?* Six tests seed resources owned by `OTHER_ID` and then
ask the runtime, authenticated as `OWNER_ID`, to change them by real id — the exact
shape a confused or steered planner would emit. Each asserts twice: that the turn
refused, and that the victim's row is unchanged. The second assertion is the one that
matters, because a refusal that arrives after the write has landed is not a refusal.
One test confirms the refusal for a foreign row is byte-identical to the refusal for a
missing one. One confirms that an injected `user_id`/`on_behalf_of` argument is inert:
the write still lands on the caller.

*Structural guards.* Six no-database tests: no new capability declares a field in
`_ACTOR_NAMING_FIELDS`; `security` is absent from the writable groups and
`_settings_patch` rejects it explicitly; every write declares a target field and a
registered verifier; consequential writes always confirm; every capability appears in
the production tool ledger.

That last one is not ceremony. A tool present in the capability registry but absent
from `PRODUCTION_TOOL_REGISTRY` raises `tool_not_registered` deep inside the gateway,
and the transport silently turns that into chit-chat — a governed action that appears
to work and does nothing.

---

## Defects the suite caught

**1. `crypto.portfolio.holding.update` silently zeroed cost basis.** The spec reused
`_HOLDING_PRICE`, which carries `default=0.0`. That default is correct on a create —
a holding recorded with no stated cost basis genuinely is zero — but on a partial
update it materialised `average_buy_price=0.0` into the arguments, the executor put it
in the patch, and a user asking only to correct an amount lost their cost basis.

The severe part is what happened next: `verified_fields` lists the same column, so the
verifier read back the `0.0` it had just written and stamped the receipt **verified**.
The user would have been told the change was confirmed while their portfolio valuation
was quietly wrong. Fixed by giving `update` its own field specs with no defaults, so
"absent" stays absent all the way down. Covered by
`test_holding_update_changes_only_the_named_fields`.

**2. `localization.region.update` could never verify.** `update_preferences` accepts
`currency`; `get_preferences` reads it back as `preferred_currency`. The verifier keyed
the payload by the argument name, missed every time, and returned
`impossible_to_verify` — which is reserved for "no read path exists at all". Every
region change would have carried "PulseSoc accepted the change, but I could not read it
back to confirm it" for a change that is trivially checkable. Fixed with an explicit
`_REGION_READ_KEYS` bridge; all four settings are now exercised by
`test_region_update_verifies_rather_than_degrading`.

Both defects share a shape worth naming: nothing crashes, no test that existed at the
time would have gone red, and the damage is only visible by reading the row back
through a *different* function than the one that wrote it. That is precisely the
independent-read-back discipline this layer is built on, and it is why the happy-path
tests assert against services rather than receipts.

**3. A missing deep link.** `crypto.watchlist.*` pointed at `/pulse/crypto/watchlist`,
which no screen answers. `WatchlistsScreen` has been in `AppNavigator` since the
watchlist went native but had no path in `linking.ts`. Caught by the existing
`test_knowledge_map.py::test_registered_capability_routes_are_navigable`. Fixed by
adding the route and correcting seven capability paths. A receipt saying "added BTC to
your watchlist" has to be able to open the list it just changed — otherwise the one
screen that would let a user check the claim is the one screen the receipt cannot link
to.

---

## Verification

Full `tests/undx_agent` suite: **865 passed, 3127 subtests passed, 16 failed.**

All 16 failures are pre-existing and unrelated. This was proven rather than asserted:
a detached worktree at `HEAD` was created and the same three files run there,
producing the identical 16. Before this mission's route fix the suite stood at 17
failed / 836 passed; it now stands at 16 failed / 865 passed — one pre-existing
failure repaired, 28 tests added, zero new failures.

The pre-existing set is `test_saved_post_write_pack.py` (3 tests,
`recoverable_failure`), `test_content_graph_intelligence_pack.py` (1), and 12
subfailures in `test_knowledge_map_grounding.py` where `bot.py` line-number citations
have drifted.

`mobile-native/src/navigation/linking.ts` was confirmed absent from
`config/realtime-audio-protected-paths.json` before editing.

---

## Open items

- **The commit could not be made.** The sandbox cannot write to `.git` at all — a
  stale `.git/index.lock` blocks it and cannot be removed (`Operation not permitted`),
  as re-confirmed when `git worktree remove` failed the same way. Mission 4's commit is
  blocked on the same thing. The intended message is
  `feat(undx): expand governed agentic actions across PulseSoc`. All changes are on
  disk and staged-ready.
- **`CLAUDE.md` has a stale path.** Its `Paths` section names a bash mount
  (`/sessions/happy-sweet-brahmagupta/mnt/CoinPilotX`) that is no longer live.
- **Verifier coverage is 22 for 29 writes.** Seven writes still declare no verifier.
  Worth a pass of its own rather than a rushed one here.
