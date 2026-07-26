# PulseSoc iOS notification banner — physical device validation

Target device: **iPhone 16 Pro `P3r7or`**, iOS 18.7.3
Branch: `release/undx-nexus-core-v4`

This checklist exists because the fix in `services/alert_engine.py` cannot be
proven on a simulator: the simulator does not receive real APNs traffic, and the
banner retraction behaviour under test is an OS presentation decision. Nothing
in the evidence report claims device validation — run this and record the
results below.

## Step 0 — Confirm the platform precondition first

Before touching anything else, on `P3r7or`:

> Settings → Notifications → PulseSoc → Banner Style

If **Banner Style** is set to **Persistent**, that alone explains a banner that
stays on screen until manually dismissed, and no application change can override
it — iOS owns this decision and exposes no API to force a Persistent banner to
behave as Temporary. Set it to **Temporary** before continuing. Record what it
was set to when you found it, because that is a direct cause and must be
documented as such.

Also confirm Allow Notifications is on, and that Lock Screen / Notification
Centre / Banners are all enabled, so the Notification Centre checks below are
meaningful.

## Step 1 — Baseline: one crossing, one banner

1. Create a price alert whose threshold is **below** the current market price is
   *not* a valid test any more — the engine now arms on first observation and
   only fires on a genuine crossing. Instead, create the alert with a threshold
   the market has **not** yet reached (e.g. BTC above a price ~0.5% above spot).
2. Wait for the alert worker to run one cycle (`ALERT_WORKER_INTERVAL_SECONDS`,
   default 45s). This first cycle **arms** the rule and must produce no banner.
   Confirm no notification arrives.
3. Wait for the market to cross the threshold (or move the threshold below spot,
   then let one cycle arm it, then move it back — see Step 5 for a controlled
   alternative that does not depend on the market).
4. On the crossing, record:
   - [ ] Exactly one banner appears.
   - [ ] The banner retracts on its own, without being touched.
   - [ ] Time from appearance to retraction: ______ seconds.
   - [ ] Exactly one entry exists in Notification Centre afterwards.
   - [ ] Body reads "Value at crossing: …" (not "Live observed value: …").

## Step 2 — No repeat while the price stays past the threshold

Leave the app and device alone for **at least 30 minutes** (two default cooldown
windows) while the price remains past the threshold.

- [ ] No further banners appeared.
- [ ] Notification Centre still holds exactly one entry for this alert.
- [ ] `SELECT COUNT(*) FROM alert_events WHERE alert_rule_id=<id> AND status='triggered'` returns 1.
- [ ] `SELECT condition_state, trigger_seq FROM alert_rules WHERE id=<id>` returns `latched`, `1`.

This is the specific regression being guarded. Under the previous engine this
step produced a new push every cooldown window indefinitely.

## Step 3 — Tap routing

- [ ] Tapping the Notification Centre entry opens PulseSoc.
- [ ] It lands on the correct alert/asset destination (`pulse://alerts/<alert_id>`).
- [ ] Tapping a second time does not double-navigate.

## Step 4 — App state matrix

Repeat Step 1 for each state and record banner behaviour in each:

| App state | Banner appears | Retracts automatically | One NC entry | Correct route on tap |
|---|---|---|---|---|
| Foregrounded | | | | |
| Backgrounded | | | | |
| Terminated (swiped away) | | | | |
| Locked device | | | | |
| Unlocked, different app in front | | | | |

Note on foreground: PulseSoc deliberately suppresses the OS heads-up banner when
foregrounded (`Notifications.setNotificationHandler` in
`mobile-native/src/api/push.ts`) and renders its own in-app banner instead, which
auto-dismisses after 4.5s (9s with VoiceOver on). Expect the in-app banner there,
not the system one — and expect exactly one of the two, never both.

## Step 5 — Controlled trigger without waiting on the market

`alert_engine.send_test_alert(rule_id, user_id)` dispatches through the real
delivery path without requiring a market crossing, and deliberately does **not**
touch latch state — so you can fire it repeatedly to test presentation. Note that
because each test send is a distinct event, repeated calls legitimately produce
repeated banners; use it to test *retraction*, not *deduplication*.

To test deduplication end-to-end, drive the price instead: set the rule's
threshold above spot, let one cycle arm it, then lower the threshold below spot
and let the next cycle fire it. Further cycles must stay silent.

## Step 6 — Regression sweep

Confirm the other notification categories still deliver and route:

- [ ] Message
- [ ] Call (should surface the full-screen incoming call layer, not a banner)
- [ ] Live
- [ ] Comment
- [ ] Reaction
- [ ] Follow
- [ ] Marketplace

## Findings

Record observations here, including the Banner Style value found in Step 0.

```
Date:
Tester:
iOS version:
Banner Style found in Step 0:
Result:
```
