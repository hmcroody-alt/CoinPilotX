# Progress UI

The member-facing surface: one tile in Profile OS, one screen behind it.

## The tile

`mobile-native/src/components/ProfileHeader.tsx` — last in the grid, first thing
a member is likely to open, because it is the only tile whose label changes as
they make progress.

It is the **only** tile with a fixed `accent`. Every other tile is an ambient
panel that takes `colors.accent` or the profile owner's chosen
`theme.accent_color`; Progress uses `progressTheme.violet` and overrides both.
The exception is the point: this tile is private to the owner and carries money,
so it should not be recolourable into looking like everything else.

### Visitors do not see it

`mobile-native/src/profile/profileOsTiles.ts` gives Progress `visitor: null`, so
`visibleProfileOsTiles` drops it from a visitor's grid entirely rather than
rendering something tappable that then refuses.

That `null` is not a "not built yet" like the tiles above it. The Founding
Member Challenge exposes how many people someone invited, which of them
qualified, what they have earned and whether anything is under review. None of
that may reach another member, and a visitor destination that "just shows less"
would be one refactor away from leaking a count.

Three independent layers enforce this, deliberately:

1. The tile is hidden from visitors (routing).
2. `ProgressCenterScreen` refuses on a visitor route (rendering).
3. No Progress route accepts a target user (server). There is no request the
   client can construct that means "show me someone else's referral progress" —
   privacy is a property of the call signatures, not of a check a future caller
   has to remember.

## The screen

`mobile-native/src/screens/ProgressCenterScreen.tsx` — twelve layers on one
scroll: overview, milestones, referrals, referral detail, invite, rewards,
missions, badge, insights, activity, how it works, FAQ.

Sections rather than twelve routes, because the whole point of the surface is
that a member can see the relationship between their invites, their ladder
position and their earnings without navigating between screens to hold two
numbers in their head.

Registered as `ProgressCenter` in `AppNavigator`; the header title comes from
`common:screens.progressCenter` in all eleven locales.

### Rules the file follows without exception

**Nothing is calculated here.** Qualified counts, percentages, reward amounts,
milestone state, Live eligibility and badge eligibility all arrive decided. The
only arithmetic is clamping a bar width and dividing cents by 100 for the
currency formatter.

**Gold means earned.** `progressTheme.gold` is applied only to state the server
reports as `COMPLETED` / `COMPLETE` / `disbursed`. A milestone that is merely
*reached* but whose award row has not been written yet renders violet, not gold.
`IN_PROGRESS` covers both "the next rung" and "threshold met, award not yet
written" — both are honestly on the way, and only `COMPLETED` may be gold.

**Static layers may fail; live layers may not.** How-it-works and FAQ are
reference copy and are allowed to fail without taking the screen down. The
overview, milestones, rewards and missions calls are not: a member must never be
shown a stale or partial count.

## Theme tokens

`mobile-native/src/theme/progressTheme.ts` — violet for the program, Founders
Gold for earned state, plus the neutral surface tokens re-exported from
`colors` so the section shells match the rest of the app. Gold is reserved; if a
future screen wants a warm accent it should not reach for this token.

## i18n

`progress` is a new catalog namespace on the **extended** tier — the Progress
Center is reachable only from the profile, well after first frame, so it does
not belong in the core bundle.

The server ships stable keys (`progress.faq.whatCounts`,
`progress.missions.hostFirstLive`) and never display copy; `catalogKey()` in the
screen maps the first dot to the namespace separator. FAQ questions live beside
their answers under `progress.faq.questions.<leaf>`, with the answer as the
`defaultValue`, so a server key the app has no question for still renders a row
instead of disappearing.

All eleven locales carry the full namespace — `npm run i18n:validate` is a CI
gate and a missing key family fails the build, which is why the English catalog
was written with only two plural families rather than five.
