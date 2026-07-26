# Mission 8 — Real-Time Presence & Last Seen: Verification Evidence

**Date:** 25 July 2026
**Scope of this report:** verification of the unified presence system — what was tested, what the tests proved, what they found, and what remains genuinely open.

---

## 1. What was being verified

The mission's objective was to eliminate every fake or simulated online indicator in PulseSoc and replace it with a server-authoritative presence system, under one hard rule: *no subsystem may maintain its own presence logic.*

That rule is what makes the system testable. If presence has exactly one source of truth, then the correctness property is not "does each screen look right" but something far stronger and far easier to falsify: **every surface must return the same answer as the service, for the same user, at the same instant, and no sequence of events may make them disagree.** The test suites below are built around that property rather than around line coverage.

A second property governs the client: when presence is ambiguous, the system must resolve to *offline*, never to *online*. This asymmetry is deliberate and is asserted repeatedly. Showing a live user as offline is a cosmetic miss that the user can correct by sending a message anyway. Showing an offline user as live is invisible to the person looking at it — they wait for a reply that is not coming, and nothing on screen reveals the error. Every ambiguity in the system is resolved in the safe direction, and the tests are weighted toward garbage input rather than toward the happy path for exactly that reason.

---

## 2. Architecture the tests exercise

The load-bearing decision is that **liveness is derived at read time, never stored as a flag.** A user is online if and only if they currently hold at least one session whose heartbeat has not expired. There is no `is_online` column, no reaper process whose failure would freeze the world, and no cleanup message that must arrive for correctness.

This is why the spec's hardest requirement — "presence must never become stale," including on app termination — needs no app-termination handling at all. A terminated app stops heartbeating; its session expires on the next read by anyone. Correctness does not depend on a dying process successfully sending a goodbye.

The service (`services/presence_service.py`, 927 lines) exposes `connect`, `heartbeat`, `set_activity`, `disconnect`, `disconnect_all`, `presence_for`, `presence_of`, `is_online`, `active_sessions`, `set_privacy`, `get_privacy`, `format_last_seen`, `sweep` and `health_snapshot`. Nine HTTP endpoints under `services/presence_routes.py` expose it to clients. Its tunable behaviour is environment-driven with clamped bounds: a 45-second heartbeat interval, a 90-second grace period (two missed heartbeats, so one dropped request or a brief tunnel does not flap a user offline), 300 seconds before `away`, and a 12-second TTL on transient activities.

The activity model splits into two classes, which is what makes "typing must never become stuck" a structural guarantee rather than a bug that was fixed. Transient activities (`typing`, `recording_voice`, `uploading_media`, `sending_files`) carry a short TTL and expire on their own; a client that dies mid-type cannot leave an indicator behind, because nothing needs to arrive to clear it. Session-bound activities (`in_audio_call`, `in_video_call`, `live_hosting`, `live_guest`, `live_watching`) survive the TTL — a two-hour call is not "stale" — but die with the session, so they cannot outlive the connection either.

---

## 3. Test method

The Python suites do not test copies of the production code. They run the **real** modules — `services/presence_service.py` and `pulse_communications_v2/service.py` — unmodified, against an in-memory SQLite database.

This was possible because `pulse_communications_v2` touches only two things from the application module: `bot.db()` and `bot.sqlite3`. That surface is narrow enough that stubbing it is honest rather than a fiction. The harness (`harness.py`) installs a `bot` stub in `sys.modules` and hands out a shared `:memory:` connection wrapped in a proxy whose `close()` is disarmed — necessary because the services close their handle in a `finally` block, which would otherwise destroy the database between calls.

The value of this approach is that a passing test is evidence about shipping code paths. The cost is the harness itself, which is test infrastructure and is documented as such.

### 3.1 Testing `bot.py`, which cannot be imported

`bot.py` is a single ~100,000-line Flask application whose import has side effects, so the stubbing trick above does not extend to it. The tempting fallback is to grep the source for the removed pattern, which proves only that a string is absent.

`test_platform_surfaces.py` does better. It parses `bot.py` into an AST, lifts the individual function definitions out of it, compiles them, and executes them against the harness database with their module globals supplied explicitly. The object under test *is* the production function — same source text, same bytecode. Only the names it closes over come from the test, and those are enumerated in the file so the substituted surface is visible. This was viable because a dependency analysis showed `pulse_conversation_presence_payload` has zero `bot.py` function dependencies and `pulse_live_metrics` needs only `db` and `realtime_engine`.

Sections 1 through 6 of that suite execute real code this way. Rooms and the Arena route pull in Flask and module-level configuration and cannot be run in isolation, so section 7 falls back to structural assertions over their source. Those are weaker — they prove a defect cannot silently return, not that behaviour is correct — and the suite says so in place rather than presenting all 64 assertions as equivalent.

One detail of the source assertions is worth recording, because getting it wrong would have corrupted the evidence. Each fix left a comment naming the construct it deleted, so a substring search over raw source matches the *explanation* as readily as a relapse. Three assertions failed for exactly this reason. The available fixes were to delete the explanatory comments, to weaken the assertions until they stopped matching, or to strip comments before asserting. The third was taken: a `strip_comments` helper blanks comment spans in place using the tokenizer, leaving every other character — including the SQL inside string literals, which is what most of these assertions actually inspect — byte-identical.

The client suites run under `jest` with the `jest-expo` preset against the real TypeScript modules.

---

## 4. Results

| Suite | Layer | Assertions | Result |
|---|---|---|---|
| `test_presence_core.py` | `services/presence_service.py` | 48 | **48 / 48 pass** |
| `test_messenger_integration.py` | `pulse_communications_v2` + service | 43 | **43 / 43 pass** |
| `test_platform_surfaces.py` | `bot.py` surfaces, executed from source | 86 | **86 / 86 pass** |
| `presence.test.ts` | `src/api/presence.ts` record normalizer | 22 | **pass** |
| `presenceNormalizers.test.ts` | `messenger.ts` + `domain.ts` list token | 27 | **pass** |
| Full `mobile-native` jest suite | regression | 876 | **865 pass, 11 unrelated failures in 2 suites** |
| `npx tsc --noEmit` | typecheck | — | **clean except the concurrently-edited file** |
| `python3 -m py_compile` on all touched Python | syntax | — | **clean** |

**On the unrelated failures.** The repository is being edited concurrently by another process throughout this work, and the red moves between runs as that work lands. This report has now recorded four different sets of failing suites across four runs — `PostDetailScreen.comments.test.tsx` and `zz-dbg.test.tsx`, then a transient `BottomNavVisibility.tsx` syntax error that vanished on re-run, then ten missing `common:navSubtitles.*` i18n keys, and at the time of writing `HomeScreen.actions.test.tsx` and `HomeScreen.dbg.test.tsx` failing on optimistic-update assertions for inline comments and follow buttons. Every one of them resolved without any change on this side. The `tsc` errors moved with it: the `ReelsScreen.tsx` refactor errors cleared, the typecheck was briefly clean, and it is now a single `Cannot find name 'fireEvent'` in `HomeScreen.actions.test.tsx` — a missing import in the same file that is failing under jest. The typecheck is clean everywhere else.

Rather than assert unrelatedness, the checks that establish it are narrow and were re-run each time: `npx jest presence` passes 49/49, the failing files and `HomeScreen.tsx` contain zero occurrences of "presence" (`grep -c`), no presence file appears anywhere in the `tsc` output, and `git status` shows the whole `src/screens/` directory being modified externally. The recurring `*.dbg.test.tsx` scratch files are the concurrent author's debugging artifacts.

A reader reproducing this later will most likely see none of these, and possibly others. That churn is the reason the server-side suites are the load-bearing evidence here: they run the real modules against an in-memory database with no dependency on the mobile tree at all, and have been stable at 177/177 across every run.

### 4.1 Core service coverage

Section 1 proves liveness is derived: sessions are aged directly in the database with **no reaper running**, and the reported status flips on the very next read. Section 2 covers multi-device — a user with an iPhone, iPad and web session stays online as devices close one at a time and goes offline only when the last one does, and a reconnect from the same `device_id` replaces its row rather than accumulating a duplicate. Section 3 proves both activity classes behave as designed. Section 4 covers the `away` transition. Section 5 is the privacy work, detailed below. Section 6 feeds unknown input — unrecognised statuses, unrecognised activities, malformed rows — and confirms none of it can produce an online result. Section 7 covers last-seen bucketing and locale formatting. Section 8 confirms that `sweep()` is pure housekeeping: running it changes no reported status anywhere, which is the assertion that proves the system does not secretly depend on it.

### 4.2 Cross-surface agreement

`test_messenger_integration.py` targets the mission's hard rule directly. Three Messenger surfaces previously kept their own presence copies, each ageing `comm_v2_presence` rows on its own schedule: the conversation list, the control-centre stats block, and `conversation_presence()`. Section 4 of that suite drives one user through a full lifecycle — connect, add a second device, expire — and after each step asserts that all three surfaces and the service report the same status. Any surface still holding its own copy would lag visibly here.

The list assertion is made unconditionally rather than behind an `if payload:` guard. A guard there would let the check silently evaporate the day the payload shape changed, which is precisely the day it would matter most. That guard's absence caught a real mistake during development: an early version of the test looked for presence under a `members` key that does not exist, and the unconditional assertion failed loudly instead of passing vacuously.

---

## 5. Defects found

### 5.1 Found by the audit and fixed before testing

An earlier draft of this report claimed "four categories of fabricated presence" without enumerating them, which is an unverifiable claim. The specific fabrications removed are listed in §5.4 and §5.5, each with the assertion that now holds it down. The pattern common to all of them is worth naming, because it is what made them hard to see: each asked a question that *correlates* with presence — did this person act recently? are they a member? did a page view touch their row? — and reported the answer as though it were presence.

### 5.2 Found by the tests

Three genuine defects surfaced during verification that were not caught by inspection.

**A privacy check evaluated against nobody.** `pulse_communications_v2/service.py` calls `_user_presence_by_ids` in four places. Three passed the viewer; the fourth, inside `conversation_control_center`, did not — so privacy was evaluated against an anonymous reader with viewer id 0. The effect was that any member using contacts-only visibility read as offline even to the people they actually shared the conversation with. Fixed by passing `viewer_user_id`. The same edit removed a redundant second inference path: `active_now` was being re-derived from the status string in addition to being read from the service's own flag, and two inference paths for one fact can drift.

**Block enforcement could fail silently.** `_blocked_pairs` reads whichever block table a deployment carries, and swallowed all exceptions. A missing table is expected — deployments carry one store or the other — but any *other* failure meant block enforcement had stopped applying while the surrounding read still returned normally. This is a privacy path failing open with no signal. Now a non-"no such table" error logs `PRESENCE_BLOCK_LOOKUP_FAILED` with the table and exception class, and a read that finds *no* usable block source at all logs `PRESENCE_BLOCK_LOOKUP_NO_SOURCE blocks_not_enforced=1`.

That second warning had to be rate-limited. Presence is read on nearly every page render, and the first implementation emitted four identical lines during one short test script — at production volume it would bury the signal it exists to raise. A `_warn_once` helper now emits it once per process.

This defect was itself found by a test failure that turned out to be my own fixture's fault: I had written the block fixture with a `user_id` column where production uses `blocker_user_id`. The fixture inserted rows the code could not see, so the code correctly reported no blocks and my test correctly failed. **The code was right and my test was wrong** — but the episode showed that a mistyped block store reads as "no blocks" rather than as a failure, which is what motivated making it observable.

**The client normalizer had a fail-open.** `normalizePresence` in `src/api/presence.ts` produced `{status: "offline", online: true}` for a payload of `{online: true}` with no status field. The record contradicted itself, and every downstream consumer resolved the contradiction in favour of the green dot. The fix keeps "was the status token recognised?" separate from "what is the status?", and requires a recognised, non-offline status *and* the online flag before anyone renders as online.

Two assertions in the pre-existing `presence.test.ts` failed after this fix, because those assertions encoded the fail-open. They were updated rather than the fix being reverted — a test that asserts a bug is evidence about the bug, not about the requirement.

### 5.3 Found by reading the code during test-writing

`ChatScreen.tsx` maintained its own `PRESENCE_ACTIVITY_LABELS` map — a private copy of the activity vocabulary that `presence.ts` already owned. Meanwhile `presence.ts`'s label helpers had *no production consumers at all*. This is the mission's forbidden pattern in miniature: two copies of one vocabulary, which is how Messenger and Live end up calling the same state different things. The duplicate map was deleted and `ChatScreen` now imports `presenceActivityText`.

While consolidating, the Live activity wording was corrected to match the spec exactly: "Hosting live" → **"Hosting Live"**, "On live" → **"Guest in Live"**, "Watching live" → **"Watching Live"**.

### 5.4 Fabricated indicators found in `bot.py` and removed

A second audit pass went through the remaining platform surfaces. Six defects were found, all in `bot.py`, and all now covered by `test_platform_surfaces.py`.

**Conversation presence was still fabricating — on a live Messenger route.** `pulse_conversation_presence_payload` served `/api/pulse/messages/<id>/presence`, so this was a Messenger surface that an earlier version of this report had already recorded as migrated. It `LEFT JOIN`ed `pulse_online_sessions` and fell back to `users.last_seen_at`, calling anyone seen within six minutes "online". Both inputs are wrong for the question. `users.last_seen_at` is touched by *any* authenticated page view, so closing the browser left a user reading as online for the remainder of the window; and the six-minute cutoff was a third grace period competing with the service's own. It now asks `presence_service.presence_for` and sorts the member list by the same answer it labels them with.

This one was not surfaced by the subagent audit, which had classified the function as internal. It was found by grepping the callers directly.

**Conversation presence floored `online_count` at one.** The payload returned `max(online_count, 1 if active_members else 0)`. Any conversation with a member list therefore reported at least one person online — an empty room always claimed one live participant, and a viewer could be looking at nothing but their own name while the header said someone was there. This is a fake online indicator in the most literal sense the spec means.

**Rooms counted membership as presence.** `pulse_ensure_default_rooms` derived `online_count` from `COUNT(*)` of participants who had never explicitly left. Someone who joined a room years ago and never returned was counted as online forever. The room's "energy" bar was computed from that same number.

**Two invented energy floors.** The energy expression started at `42 + …`, so a room with nobody in it and no messages still rendered a bar just under half full. This is outside presence proper but squarely inside the spec's "no simulated activity". Both floors are gone; energy is now `online_count * 12` (rooms) and `online_count * 7 + recent_count * 5` (conversations), where both inputs are real.

**Live metrics could only ever inflate.** `pulse_live_metrics` computed `online_users = max(active_users, realtime_health["online_users"])`. `active_users` counts distinct actors in the last minute of the event log — "who did something recently", not "who is connected" — so a user who reacted to a post 45 seconds ago and then killed the app was counted. Taking `max()` against the transport's own figure made it strictly worse, since the result could be pushed up but never corrected down. `online_users` now comes from `presence_service.health_snapshot`; the event-log figure is retained under the honest name `actors_last_minute` so the dashboard does not lose the signal.

**Arena listed players who were not there.** `api_arena_presence` returned a roster of recent profiles and decorated each with `arena_profiles.online_status`, a column set to `'training'` on activity and never reset — so it described a player forever. When the roster came back empty, the endpoint invented three lines of filler activity. The roster is now gated on `presence_service.presence_for`, the `online_status` column is not emitted, and an empty Arena reports an empty Arena. *This fix turned out to be incomplete; §5.6 records what it missed and why.*

Every one of these fixes carries an explicit fail-closed handler: if the presence store cannot be read, the roster empties and the counts go to zero rather than the surface falling back to the old inference. Section 3 of `test_platform_surfaces.py` proves this by pointing the real function at a database with no presence tables at all and asserting that it neither raises nor reports anyone online.

### 5.5 Two remaining second-sources, now retired

**Typing was a parallel subsystem.** `pulse_conversation_typing` was a table the Messenger surfaces owned outright: the typing endpoint wrote a row with its own eight-second `typing_until`, and two read paths — the conversation detail payload and the conversation list — each re-derived expiry against their own cutoff. Nothing here was *fabricated*; the state was real. The violation is the one the mission's hard rule is about, and the risk it carries is specific: a second opinion on staleness is how an indicator gets stuck.

Typing is now recorded through the presence service and read from it. Two entry points were added:

- `set_activity_for_user(cur, user_id, activity, context)` — for server-side callers that authenticate a user but never receive a session id. It applies the activity to every live session, which is the correct reading of the event (a person typing is typing, whichever of their devices the keystrokes came from). It refuses outright when the user has no live session: recording typing for someone who is not connected would be exactly the fabrication being removed, and §5's `a disconnected user cannot be given a typing indicator` asserts that refusal.
- `activity_by_context(cur, viewer, contexts)` — a batched read, because a conversation list needs the answer for every row at once. Without it the list would either issue one query per row or keep its own table, which is what it was doing.

Both paths exclude invisible sessions, so a typing bubble cannot leak presence that the presence payload itself would hide.

**The web page-view heartbeat bypassed the service.** `pulse_mark_online` — reached from `/api/pulse/heartbeat` and from ordinary page views — wrote a `pulse_online_sessions` row with `online_status='online'`, a flag nothing ever cleared, in a table that by the end of this work had no readers left anywhere. Web users were therefore either invisible to the presence system or, before the other fixes landed, permanently online in it.

It now calls a third new entry point, `touch_device(cur, user_id, device_id, …)`, which heartbeats the device's existing session or opens one if it has none. The distinction from `connect()` matters at page-view volume: `connect()` revokes and re-creates a row on every call. §5 of the platform suite asserts that five page views produce exactly one session row, that a second browser is correctly a second device, and — the property that separates this from the flag it replaced — that when traffic simply stops, with no logout and no cleanup event, the user goes offline on the shared clock.

### 5.6 The Arena fix was incomplete, and a third pass found the rest

§5.4 recorded the Arena roster as fixed. It was, but the fix was scoped to one endpoint while the defect lived in a function that endpoint merely happened to call. A third pass — walking every occurrence of `online_status` and every hardcoded presence string in `bot.py` rather than every presence *endpoint* — found three more.

**The shared Arena card was still carrying the flag.** `public_arena_player` builds the player card behind leaderboards, match rosters, chat cards, profile pages and the typing response — a dozen call sites. It emitted `arena_profiles.online_status`, the same never-reset `'training'` column §5.4 describes, on every one of them. Fixing `api_arena_presence` removed the flag from the roster and left it reaching everywhere else. This is a specific and repeatable mistake: the endpoint was audited, the shared builder underneath it was not.

The key was removed from the card outright rather than replaced with a real value. A card built from a profile row has no viewer in scope, so it cannot apply the privacy filter that Invisible Mode and blocking depend on; anything presence-shaped emitted from there would be unfiltered by construction. Surfaces wanting presence call `presence_service.presence_for` with the real viewer, as the roster now does.

Every read *and* every write of the column was then removed, leaving only the DDL. A write-only column is not a live defect, but it is a loaded one — the next person wanting a status flag finds it already populated. `arena_profiles.online_status has no reader and no writer left in bot.py` asserts the whole-file property; the earlier assertion, which only checked the roster function, is exactly the assertion that let this survive the second pass.

**The Arena chat header was a hardcoded string.** `arena_chat_page` rendered the literal `<span class="online-dot">Online / recently active</span>` into every thread, alongside a `data-typing` element permanently reading `Ready.` that no script on the page ever wrote to. Neither was derived from anything: both were true of every thread whether or not the other participant had ever connected. This is the most literal possible instance of a fake online indicator, and it is worth being clear that no amount of endpoint auditing would have found it — it is markup, not a query. It now renders from an `other_presence` object that `arena_chat_payload` obtains from `presence_service.presence_for`, filtered for the viewer, with the client falling back to `Offline` when the field is absent.

**The Arena typing endpoint stored nothing.** `/api/arena/chat/typing` echoed `status: "typing"` back to the sender — the one person already certain they were typing — and wrote to no store, so no peer could ever see it. It now records through `set_activity_for_user` under the namespaced context `arena:{thread_id}`, and returns `recorded`, which is false when the user has no live session, in place of the fixed string. The namespacing is asserted behaviourally: Arena thread 77 and Messenger conversation 77 do not light each other's indicators.

### 5.7 Two bugs an independent review caught in the Arena fix

The Arena chat work above was reviewed by a second agent given the diff and no other context. It confirmed the SQL edits by counting columns against placeholders against parameter tuples — the `get_or_create_arena_profile` INSERT is 18 columns, 18 values, 9 placeholders, 9 parameters — and found two real bugs in the new code.

**The client tested a field the server does not emit.** The fallback dict and the renderer both used `active_now`. `presence_for` emits the boolean as `online`; there is no `active_now` key. `p.active_now` was therefore always `undefined`, so a genuinely online peer fell through to the last-seen branch — and since the service blanks `last_seen_at` for anyone not offline, the header would have read the literal word "Offline" for someone who was online.

This is worth dwelling on, because it explains why nothing caught it. The bug failed *safe*: it under-reported presence, and every assertion in this suite is pointed at over-reporting. A test asking "does an offline user ever appear online" passes cleanly against code that can never show anyone as online at all. Fail-closed design makes a whole class of bug invisible to a fail-closed test suite. Four assertions now pin the contract from both ends: that the service's key is named `online`, that no `active_now` key exists to be confused with it, that it is `true` for a live peer, and that the client reads `p.online` rather than anything else.

The renderer was also changed to display `last_seen_text`, the service's locale-formatted string, instead of reformatting the raw timestamp client-side. The raw field is blanked when a peer hides last seen; rendering it directly would have leaked a value the server had deliberately withheld.

**A two-second poll that dropped the field.** The page loads presence from the full payload, then polls a delta route every two seconds. That route returned only messages, so `renderPresence` received `undefined` on every poll after the first. The route now carries `other_presence`, and the client leaves the header untouched on a missing object rather than asserting "Offline" — a dropped request is not evidence that a peer left.

**One thing the review found that was not fixed.** The Arena chat page body is unreachable: the route redirects whenever the thread row exists, and `arena_chat_payload` runs the identical query, so the page 404s whenever it is actually reached. The hardcoded "Online / recently active" string was therefore not being served to anyone. It was fixed regardless — a dead page is a template for a live one, and the string would have shipped the moment the redirect was removed — but this report should not claim a user-visible defect was repaired when it was a latent one.

### 5.8 A subagent finding that was wrong

An automated audit reported `bot.py`'s `user_presence` table as a surviving independent presence store. It is not: `read_local_presence` already derived status from `presence_service` and explicitly documented the table as a cache not trusted for liveness. The finding was checked against the source before any edit was made, and no edit was made.

The check did surface something smaller. `read_local_presence` fell back to `user_presence.last_seen_at` when the service returned none — a second last-seen authority, and one the Command Center worker also writes to on a different schedule. The fallback was removed. The function's docstring now also records that it is a *self* read (viewer and target are the same user), since a future caller passing someone else's id would bypass the privacy filtering entirely.

---

## 6. Privacy: what is proven, and the one thing that is not

Invisible Mode and blocked-user restriction are only meaningful if a hidden user is **indistinguishable** from a user who is simply offline. If any field in the payload differs, a client can diff the two and learn it has been blocked — which converts a privacy feature into a block-detection oracle.

The old `conversation_presence()` leaked exactly this. It emitted `status: "hidden"` for privacy-restricted users, a value that appears nowhere else in the vocabulary. Reading one field told a client it had been blocked. Section 2 of the integration suite now asserts that `"hidden"` appears nowhere in any payload, that an invisible peer reads as plain `offline` with `active_now: false`, no activity, and no last-seen timestamp — while separately asserting that the server itself still knows the user is online, which proves the hiding is happening at the presentation boundary rather than by breaking the underlying state.

The decisive test compares an invisible user's full payload field-by-field against two controls and requires **zero differing fields**. Both pass:

> `invisible is byte-identical to 'offline w/ hidden last seen'` — PASS
> `invisible is byte-identical to 'never connected'` — PASS

**The honest limit.** A hidden user is byte-identical to an offline user *whose last-seen is unavailable* — the state shared by everyone using Hide Last Seen and everyone who has never connected. That is a real and populated crowd to hide in. A hidden user is **not** identical to an offline user with a *visible* last-seen timestamp, because the hidden payload carries no timestamp.

Closing that final gap would require inventing a plausible-looking timestamp for someone who did not generate one — fabricating presence data, which is the precise thing this mission exists to remove. The gap is left open deliberately. The test documents it in place rather than asserting a weaker property quietly.

I initially claimed perfect indistinguishability. Testing disproved it. The claim was narrowed to what is actually achievable rather than the test being adjusted to keep the claim.

---

## 7. UNDX

The assistant is a service, not a person, and must never flow through the human online/last-seen renderer. It carries a dedicated `"assistant"` marker that sits outside the `online`/`away`/`offline` vocabulary entirely, with `active_now: false`.

`pulse_ai_service.py` cannot be imported in the harness — it transitively pulls in the Flask request stack. Rather than fall back to a substring grep, the test parses the module's AST and reads the literal value assigned to the `"presence"` key. This is strictly stronger than grep: a match cannot come from a comment, a docstring, or an unrelated line.

On the client, the marker is preserved through the conversation-list normalizer and renders as "Always available" via `presenceLabel`. A further property is asserted: the object path of `messenger.ts`'s normalizer deliberately does *not* honour `"assistant"`. The marker is a client-side constant this app stamps onto the one row it knows is UNDX — not a value a server payload may assert. Honouring it inside a status object would let a payload award a human row the "Always available" label, which is a fabricated availability claim of exactly the kind being removed.

---

## 8. Client-layer division of responsibility

The client decodes presence in two places, and both are now covered without overlap.

`src/api/presence.ts` owns the rich record — status, activity and last-seen — used by the chat header, and is covered by `presence.test.ts`. The conversation *list* carries something much smaller: a single token per row, normalized privately inside `messenger.ts` and rendered through the `domain.ts` helpers. That is a second decoding path, and a second path is exactly where a green dot can reappear after being removed from the first one. It is covered by `presenceNormalizers.test.ts`.

Both suites assert the same asymmetry against the same legacy tokens. `active`, `available`, `live`, `idle`, `typing`, `hidden`, capitalised garbage, numbers, arrays and nested objects all decode to unknown-or-offline, never to a live token. The list layer additionally asserts that `available: false` — how the server reports a privacy-restricted peer — is treated as *no presence at all* rather than as a status to read past, and that the resulting row is byte-identical to a row for someone we simply have no information about.

`domain.ts`'s two predicates are asserted to partition the vocabulary: no token can satisfy both `isActivePresence` and `isAssistantPresence`, so a bot cannot light a human presence dot and a human cannot receive the always-available label. `presenceLabel` renders empty for an unknown token rather than guessing — "we do not know" is correctly rendered as empty space, not as "Offline", which would be a claim the client cannot support.

---

## 9. Acceptance criteria

| Requirement | Status | Evidence |
|---|---|---|
| Unified service, no subsystem-local logic | Met for the migrated surfaces (§10 lists the rest) | `test_messenger_integration` §4 — three surfaces cannot disagree; `test_platform_surfaces` §7 source sweeps |
| No second presence *store* in `bot.py` | Met | §7 sweeps: no path reads or writes `pulse_conversation_typing`; no path writes `online_status='online'` |
| Online only with a live, non-expired session | Met | `test_presence_core` §1 — derived with no reaper |
| No fabricated counts or floors | Met | `test_platform_surfaces` §4 and §7 — no `42 + online_count`, no `1 if active_members else 0` |
| No hardcoded presence in server-rendered markup | Met | §7 — the Arena chat header's literal "Online / recently active" is gone and the page renders a server-supplied object |
| No never-reset status flags | Met | §7 — `arena_profiles.online_status` has no reader and no writer left |
| Locale-formatted Last Seen | Met | `test_presence_core` §7; `presence.test.ts` locale cases |
| Heartbeat, grace period, reconnect, recovery | Met | 45 s / 90 s clamped; §1 |
| Server-observed traffic feeds the same clock | Met | `test_platform_surfaces` §5 — page views heartbeat one session; traffic stopping expires it with no cleanup event |
| Never stale on background or termination | Met structurally | Lazy expiry — no cleanup message required |
| Multi-device | Met | `test_presence_core` §2; integration §5; platform §5 (two browsers = two devices) |
| Messenger activity states | Met | §3 (TTL) and integration §6 |
| Typing never stuck | Met, and now executed rather than argued | `test_platform_surfaces` §6 — an abandoned indicator clears itself with no stop event, and an expired session takes it with it |
| Typing is context-scoped | Met | §6 — typing in one conversation does not appear in another |
| Live presence vocabulary | Met | Spec wording asserted in `presence.test.ts` |
| Hide Last Seen, Invisible, blocked users | Met, with §6's documented limit | Byte-identity tests; platform §2 and §6 (invisible users broadcast no typing) |
| Fails closed | Met | Platform §3 — real functions against a database with no presence tables report nobody online and do not raise |
| Verified by testing with evidence | Met | This report |

The first row is the one that needs reading carefully. "Met for the migrated surfaces" is a weaker claim than the mission's hard rule, which admits no exceptions. What is now true without qualification is the second row: within `bot.py`, no surface keeps a presence store or an expiry rule of its own. The surfaces named in §10 are not in violation of that — they simply do not consume presence from the service yet, because they do not consume presence at all.

---

## 10. Remaining work

This is scope beyond the verified surface, listed so the boundary is explicit rather than implied. Two earlier drafts of this section were wrong in both directions — overstating what had been migrated and naming modules as violations without reading them — so each claim below states how it was checked.

**Migrated and covered.** Every call site in `bot.py` that answers "is this person here" now consumes the service: the self-presence read and its heartbeat helper, the Arena roster, the Arena chat payload and its delta route, the Arena typing endpoint, the Rooms member list, the conversation detail payload, the conversation list's batched typing read, the Messenger typing endpoint, the web page-view heartbeat, and the Live metrics health snapshot. Verified by `grep -n "presence_service\." bot.py` and by resolving each hit to its enclosing function with an AST walk rather than by eye.

**Not migrated, because they do not render presence.** Feed, Profiles, Search, Groups, Communities and Notifications were named in an earlier draft as consuming presence. Checked by sweeping `bot.py` for every emission of `is_online`, `"online"`, `active_now` and `last_seen`: outside the migrated functions there are three hits, and none is a user presence indicator. `admin_users_payload` emits `last_seen` as a raw timestamp in the admin user table; `api_admin_visitors_live` reports `active_now` for web analytics sessions including anonymous ones; `api_pulse_heartbeat` returns `{"online": true}` as an acknowledgement of its own POST. These surfaces are not in violation of the hard rule — they keep no presence logic because they display no presence. If presence is added to them later, it must come from the service.

**A previous claim retracted.** An earlier draft asserted that `services/live_presence_engine.py` and `services/world_presence_engine.py` "hold independent presence logic and remain in violation". Both were read. `live_presence_engine` exposes `audience_pulse`, `stream_energy_state` and `reaction_cloud` — viewer-count and reaction arithmetic for the Live stream UI, with no user identity in it. `world_presence_engine` builds a discovery feed from market quotes and platform events. Neither answers "is this person here". The word "presence" in the filenames is what drove the original claim, which is not evidence.

**A genuine second authority, out of process.** `services/command_center_worker/presence.py` maintains its own user presence with `AWAY_AFTER_MINUTES = 5` and `OFFLINE_AFTER_MINUTES = 15`, writing the `user_presence` table. This is a real second opinion on the same question and the clearest surviving instance of the hard rule being broken. It is scoped out here rather than dismissed: it is a separate microservice, is not imported by `bot.py` (verified by grep), and is reachable only through `/internal/command-center/…` endpoints and audit scripts. Nothing user-facing reads its verdict — §5.8 removed the last path that could have, the `user_presence.last_seen_at` fallback in `read_local_presence`. Reconciling the worker with the service means either pointing it at `presence_sessions` or retiring it, and that is a cross-service change with its own deployment story.

**Untouched.** The separate, older `mobile/` Expo client was not modified; only `mobile-native/` was. Two SQLite tables, `pulse_conversation_typing` and `pulse_online_sessions`, survive as DDL with no readers or writers left in application code — dropping a column or table is a migration rather than a code change. The `arena_profiles.online_status` column is in the same state.

**A dead page left in place.** The Arena chat page (§5.7) is unreachable behind a redirect. Its fabricated header was fixed rather than the page deleted, because deleting a route is a product decision and not this mission's to make. Whoever owns Arena should decide whether the page should exist at all.

**Unrelated red in the repository.** Covered in §4. At the time of writing it is two `HomeScreen` suites failing on optimistic-update assertions, in a directory being modified by a concurrent process; neither they nor `HomeScreen.tsx` mention presence. This has changed on every run and is recorded so a reader seeing red does not attribute it to this change.

---

## Appendix — reproduction

```
# Server-side suites (run the real modules against an in-memory DB)
cd tests/presence
python3 test_presence_core.py            # 48/48
python3 test_messenger_integration.py    # 43/43
python3 test_platform_surfaces.py        # 86/86   (executes bot.py functions via AST)

# Syntax check on the edited modules
cd ../.. && python3 -m py_compile bot.py services/presence_service.py

# Client suites
cd mobile-native
npx jest presence                         # 49/49 across 2 suites
npx jest                                  # full regression; see §10 on unrelated red
npx tsc --noEmit                          # see §10 on ReelsScreen.tsx
```

177 server-side assertions and 49 client-side. The suites are self-contained: `tests/presence/harness.py` derives the repository root from its own path, so no environment variable or working directory is required.
