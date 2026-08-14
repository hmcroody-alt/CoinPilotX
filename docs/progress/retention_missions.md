# Retention missions

A referral counter that stops at 30 turns a member into a lead-generation
instrument and then abandons them. `services/business_os/progress/missions.py`
is the part of Progress OS that exists so the journey does not end.

## Two populations

**Referrers** who finished the challenge get the `creator` track:

| Mission | Objective source |
|---|---|
| `host_first_live` | manual |
| `reach_100_followers` | followers |
| `build_creator_page` | manual |
| `start_selling` | manual |
| `grow_community` | manual |

**Referred members** — who until now have been an object in someone else's
reward program — get the `newcomer` track and become the subject of their own:

| Mission | Objective source |
|---|---|
| `complete_profile` | manual |
| `first_post` | posts |
| `post_another_day` | posting_days |
| `follow_five` | followers |
| `start_own_challenge` | qualified_referrals |

`start_own_challenge` is the loop closing: the person who was referred begins
their own Founding Member Challenge.

## Honesty rule

A mission's progress is either measured from a real source or it is reported as
zero. Nothing estimates, extrapolates, or shows a plausible-looking number to
make the screen feel alive. A fabricated "3 of 5 followers" is a lie the user
can act on.

Missions whose objective this deployment cannot measure automatically carry
`measurable: false` in the API response, and the Progress Center surfaces that
rather than rendering a bar that pretends to be live. An unmeasurable mission
that looks live is a promise the app cannot keep.

## Genericity is the point

A mission is `objective_type` + `target` + `current_progress` + `source`. There
is nothing referral-shaped in the engine. The next PulseSoc program can use it
without touching referrals at all — the mission brief asked for a foundation,
not a bigger referral screen.

## Copy

The server ships `title_key` (e.g. `progress.missions.hostFirstLive`) and never
display copy. The app owns every translated string in
`mobile-native/src/i18n/catalogs/*/extended.json` under `progress.missions`,
across all eleven locales. A server that shipped English strings would be an
English-only feature with extra steps.
