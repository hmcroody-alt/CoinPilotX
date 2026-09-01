# Test Matrices

Every row names a test that exists and passes. Run commands are at the bottom.

## A. Crypto alert retrigger (16 items)

The governing rule: ABOVE fires on below/equal → above, BELOW on above/equal →
below, and neither fires again while the price stays on the same side.

| # | Behaviour under test | Test | File |
|---:|---|---|---|
| 1 | First observation arms; it does not fire, even if already past the threshold | `test_first_observation_arms_without_firing` | edge_trigger |
| 2 | One crossing produces exactly one notification | `test_one_crossing_sends_exactly_one_notification` | edge_trigger |
| 3 | Price sits past the threshold and never re-fires — the loop's exact shape | `test_flat_price_past_threshold_never_refires` | edge_trigger |
| 4 | Repeated identical observation never duplicates | `test_repeated_identical_observation_never_duplicates` | edge_trigger |
| 5 | Retreat *toward* the threshold, still on the same side, does not re-fire | `test_retreat_toward_threshold_does_not_refire` | edge_trigger |
| 6 | **Owner's worked example**: clearing the threshold re-arms; the next crossing fires again with a distinct key | `test_clearing_threshold_rearms_and_next_crossing_fires` | edge_trigger |
| 7 | Repeats are opt-in — the default is one notification per crossing | `test_default_repeat_mode_is_once_so_a_rule_speaks_once_per_crossing` | edge_trigger |
| 8 | The schema does not hand a repeat policy to rules that never chose one | `test_schema_does_not_hand_a_repeat_policy_to_rules_that_never_chose_one` | edge_trigger |
| 9 | **The production regression itself**: the owner's real rule shape does not walk the price down | `test_owner_production_rule_shape_does_not_walk_the_price_down` | edge_trigger |
| 10 | A duplicate worker cycle is idempotent — concurrent evaluators produce one event | `test_duplicate_worker_cycle_is_idempotent` | edge_trigger |
| 11 | Trigger identity is stable, not time-bucketed (repeated cycles ⇒ same key) | `test_trigger_key_is_stable_not_time_bucketed` | edge_trigger |
| 12 | Latch state survives a worker restart — persistence, not memory | `test_repeat_state_survives_a_worker_restart` | edge_trigger |
| 13 | ABOVE and BELOW rules on one symbol latch independently | `test_opposite_direction_rules_are_independent` | edge_trigger |
| 14 | A quote failure does not disturb the latch or fabricate a crossing | `test_quote_failure_does_not_disturb_latch` | edge_trigger |
| 15 | Delivery failure ≠ alert failure: the rule stays active and does not re-fire | `test_notification_failure_keeps_alert_active` | persistence |
| 16 | A stale evaluation cannot fire after the user pauses or deletes the rule | `test_stale_evaluation_cannot_fire_after_user_stops_rule` | persistence |

Supporting coverage not counted above: `test_concurrent_same_observation_notifies_once`
(DB-safe compare-and-set), `test_material_further_move_refires_while_latched` and
`test_repeat_respects_configured_rate_limit` (opt-in progress mode), and the
advanced/windowed rule equivalents in `advanced_rules`
(`test_advanced_rule_does_not_refire_while_latched_and_flat`,
`test_crossing_rule_fires_per_crossing_not_per_further_move`).

## B. Briefings (10 items)

| # | Behaviour under test | Test | Class |
|---:|---|---|---|
| 1 | A user with no registered device is still evaluated for a briefing | `test_a_user_without_a_registered_device_is_still_evaluated` | `ShadowModeTests` |
| 2 | The scheduler SQL LEFT JOINs push transport, and cannot regress to INNER | `test_scheduler_sql_left_joins_push_transport` | `HubBackendTests` |
| 3 | Notifications score by `type`, never by category name | `test_unscored_types_return_none` | `SignificanceTests` |
| 4 | The dual-write trap: one missed call written under both spellings counts once | `test_both_spellings_of_one_call_count_once` | `MissedCallSignificanceTests` |
| 5 | Two genuinely distinct misses still count as two | `test_distinct_misses_under_different_spellings_both_count` | `MissedCallSignificanceTests` |
| 6 | One missed call stays silent; two reach the threshold | `test_one_miss_is_recorded_but_does_not_send_and_two_do` | `MissedCallSignificanceTests` |
| 7 | Call *lifecycle* (started/accepted/ended/incoming) scores zero at production volume | `test_call_lifecycle_volume_scores_nothing` | `MissedCallSignificanceTests` |
| 8 | Briefings read `alert_rules`, not the legacy `crypto_alerts` table | `test_legacy_crypto_alerts_rows_are_not_read` | `WatchlistFactTests` |
| 9 | Rule liveness matches the engine's predicate exactly, both directions | `test_liveness_predicate_matches_the_engine_exactly` | `WatchlistFactTests` |
| 10 | **The 21-event window**: one rule firing 21 times is one episode scoring 12, not 252 | `test_the_owners_real_window_is_one_episode_not_twentyone` | `TriggeredAlertEpisodeTests` |

Supporting coverage not counted above: the 2026-07-18 decline burst staying
correctly silent (`test_the_production_decline_burst_stays_silent`), both
fingerprint behaviours — a new missed call defeats the dedupe hash
(`test_a_new_missed_call_breaks_the_dedupe_fingerprint`) while a new decline
does not (`test_declines_alone_do_not_break_the_fingerprint`) — a further move
within one episode not re-sending
(`test_a_further_move_in_the_same_episode_does_not_resend`), and fault
degradation when the notification table is unreadable.

## Running

```bash
# Briefings — unittest only, no pytest
python -m unittest tests.briefings.test_pulse_briefings      # 122 tests

# Crypto alerts — ONE FILE PER PROCESS. Each sets its own temp DATABASE_URL at
# import time, so batching them makes whichever module imported last win and
# produces a wall of false failures.
for f in tests/test_crypto_alert_*.py; do python -m pytest "$f" -q; done   # 186 tests

# Gates
python scripts/protection/run_protection_suite.py            # 225 checks / 21 suites
python scripts/realtime_audio_change_gate.py --base origin/main --head HEAD
```

All green as of the rebase onto `97b2b15a`.
