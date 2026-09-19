# LONG-PRESS MESSENGER ACTIONS

Mission E. Context-aware long-press actions for PulseSoc Messenger.

Commit `e12be1ee4` on `main`, rebased onto `f5483bb81` and re-gated after the rebase.

---

## IMPLEMENTED

**The focused-message experience.** A long press lifts the pressed bubble out of
the thread: haptic feedback fires, the rest of the conversation dims behind a
blur, the reaction strip floats **above** the message, and a compact action menu
sits **below** it. Outside tap and swipe both dismiss. No full-screen generic
menu — the menu that appears is assembled per message and is typically four to
nine rows, never the twenty-key union.

**Context is computed, not guessed.** `messageActionRules(message, context)` in
`src/pulseCommand/domain.ts` is the single source of availability. It reads
ownership, delivery status, message kind, the edit window, and a
`MessageActionContext` carrying `links`, `group` and `translatable`.

The most important structural decision is that **links arrive already extracted**.
`MessageActionContext.links` is filled by `links/messageLinks.detectLinks` — the
same parser that makes links tappable in the bubble. The menu never runs its own
URL detection. If it did, "Open Link" could be offered for something the tap
handler refuses to open, and that drift is precisely what a second parser
guarantees over time.

**Every listed row does something.** `MESSAGE_ACTION_IMPLEMENTED` is a
`Record<PulseCommandActionKey, boolean>` — a compile-time completeness gate. A
new key added to the union is a type error until it is classified. The renderer
filters on it, so an action that is not yet wired is *absent* rather than inert.
A row that does nothing is worse than an absent row: the user cannot tell it
apart from a failure. The dispatch switch has no `default`, so a new key is a
compile error rather than a silently dead menu row.

Two keys are deliberately `false`, for different reasons, both now written into
the source:

- **`save`** is refused by the saved-items contract itself. `SavableContentType`
  has no member for a message, so there is nowhere for a saved message to go.
  Adding one is a change to that contract and its storage, not to this screen.
- **`saveMedia`** would need the *granted* media URL — a short-lived credential
  minted inside the bubble's own media child — plus photo-library permission, a
  download with progress, and the handling of a Mux video whose playback URL is
  a manifest rather than a file. The viewer already does all four, in one place.
  The route to Save is: open the media, save it from there. A second copy of a
  credential protocol works until the day it expires differently.

This is a gap against the brief and is called out again in FINAL JUDGMENT rather
than buried here.

---

## ACTIONS

Availability by context, as enforced by `messageActionRules`:

| Action | Own text | Others' text | Link msg | Media | Voice | Failed | Deleted |
|---|---|---|---|---|---|---|---|
| Reply | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| React | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| Copy | ✅ | ✅ | ✅ | caption only | — | ✅ | — |
| Forward | ✅ | ✅ | ✅ | ✅ | ✅ | — | — |
| Edit | ✅ in-window | — | ✅ in-window | — | — | — | — |
| Share | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| Save | *not shipped* | *not shipped* | — | — | — | — | — |
| Translate | — | ✅ | ✅ | caption only | — | ✅ | — |
| Message Info | ✅ | ✅ | ✅ | ✅ | ✅ | — | — |
| Open / Copy / Share Link | ✅ if link | ✅ if link | ✅ | — | — | ✅ | — |
| View | — | — | — | ✅ | — | — | — |
| Save to Photos | — | — | — | *not shipped* | — | — | — |
| Report | — | ✅ | ✅ others' | ✅ others' | ✅ others' | — | — |
| Retry | — | — | — | — | — | ✅ | — |
| Delete for Me | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| Delete for Everyone | ✅ | — | ✅ own | ✅ own | ✅ own | — | — |

Three rules behind that table are worth stating because they were each a
decision rather than a default:

**No Edit and no Delete-for-Everyone on someone else's message.** Both are gated
on `is_mine`. A `viewerModerates` flag existed here briefly and widened
Delete-for-Everyone on the theory that a conversation owner could unsend anyone.
The server disagrees — `comm_v2.delete_message` refuses any non-sender — so the
flag only ever produced a 403 while reading like the power was wired. It was
removed, and the reason is recorded in the type.

**Nothing that names a message to the server is offered before the server knows
it.** `addressable = serverAccepted && !gone` gates Forward, Save, Report and
Message Info. Copy and Delete-for-Me are not gated, because both are answerable
entirely on this device.

**Delete for Everyone is server-authoritative.** The client asks; the server
decides; the UI reflects what came back.

### Edit

Edit takes over the one composer on the screen, so the displaced draft is stored
and given back on cancel (`restoreDraft`). Editing and replying are mutually
exclusive by construction — each setter clears the other. A banner states what
Send will now do, because without it the composer is just a box that will
silently replace a message somewhere up the thread instead of appending one.

**Nothing is painted optimistically.** Ownership, the edit window and the empty
body are all the server's rules, enforced on that exact request. Painting first
would show a successful edit for one round trip and then take it back — and the
case where it lies is exactly the case the user most needs told about.

### Forward

Destinations come from the conversations already cached for the account, read
when the sheet opens, with a live refresh that replaces the rows only if it
lands. A picker that spins is a picker people close, and the cache is the list
they just came from. The current conversation is excluded: forwarding a message
into the thread it already lives in produces a copy of itself directly beneath
itself.

**The reported count is the server's, never `conversationIds.length`.** The two
differ whenever a destination went away between the cache write and the send —
left, blocked, deleted. The selection is a statement of intent; the count is a
statement of fact. Reporting the intent would tell someone their message reached
a thread it never entered. This distinction was not originally testable and the
mutation pass caught it — see TEST RESULTS.

### Message Info

Every row is read from the message the thread already holds. That is a ceiling,
not a shortcut: `comm_v2_read_receipts` aggregates read state to one of
sent/delivered/seen for the **whole message** and exposes no per-person
breakdown. A sheet promising "Read by Ana, Ben" would have to invent two of
those three words.

In a group that ceiling is stated rather than implied: the sheet says **"Read by
at least one person"**, because "Read" beside six participants is read as "all
six" and means "at least one".

Rows with no value are dropped rather than blanked. A blank row invites the
reading that the fact is missing; an absent row says the fact does not apply,
which for "Edited" or "Duration" is the truth. An unparseable timestamp renders
as nothing rather than `Invalid Date`, a string the user can neither act on nor
report.

### View

`viewMedia` opens the gallery on the pressed message using nothing but the
message. It needs no granted URL: `useConversationMediaGallery` resolves any
seed whose URL is protected — checking `isProtectedMessengerMediaUrl` for the
active item and its prefetch-neighbour window — and mints the grant before the
viewer loads. So the protected API path reaches the component whose job is to
exchange it and is never handed to the platform image loader.

This overturned a standing comment in this file which claimed `viewMedia` could
not be driven from ChatScreen because the granted URL lives inside the bubble's
media child. That claim is true of `saveMedia` and false of `viewMedia`. The
comment now says exactly that, including what `viewMedia` costs instead: the
instant first paint, not correctness. A tapped photo is already decoded; this
one waits on a grant. For a menu row that is the right trade against a second
copy of the grant-and-refresh protocol.

One trap worth recording: the seed's `attachmentId` and `mediaUploadId` are
autoincrements from **different tables whose ranges overlap**. A falsy
`media_upload_id` silently promotes a transport row id into a foundation media
id, and the on-disk cache cannot tell them apart.

### Voice — hard lock honoured

No file under `config/realtime-audio-protected-paths.json`'s `categories[].paths`
was touched. Verified, not assumed:

```
$ python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD
No protected real-time audio path changed (13 file(s) inspected).
Audio validation is not required for this change.
```

Voice messages gain Reply, Forward, Share, Message Info and Delete only. No
playback, waveform, audio-session, radio, call or livestream code was modified.
`ChatScreen.tsx` appears in the manifest's `allowed_paths`, which is a
forbidden-API allowlist, **not** a gated path — the distinction was checked
directly against `categories[].paths` rather than inferred from the filename
appearing in the file.

---

## LINK ROUTING

One parser, one router, six consumers. `detectLinks` finds them;
`openMessageLink` routes them. Both are shared by:

1. a normal tap on linkified text in a bubble (`LinkedText`)
2. **Open Link** from this menu
3. **Copy Link** from this menu
4. **Share Link** from this menu
5. shared-post cards
6. Universal Links arriving from outside the app

When a message carries more than one link, Open/Copy/Share Link do not guess.
`LinkChoiceSheet` presents the extracted links and the user picks; the
accessibility label changes to "Choose a link to open" so the branch is
announced rather than discovered.

Because availability (`hasLink`) is computed from the same `detectLinks` output
that the tap handler uses, the menu cannot offer to open something the router
will refuse. That is a structural guarantee, not a tested coincidence.

---

## TEST RESULTS

### Automated — 57 tests across three files, all green

| # | Case | Expected | Result |
|---|---|---|---|
| 1 | Long press own text | Reply, Copy, Forward, Edit, Share, Info, Delete for Me, Delete for Everyone | ✅ |
| 2 | Long press others' text | adds Translate + Report; no Edit, no Delete for Everyone | ✅ |
| 3 | Long press own text past the edit window | Edit absent, everything else unchanged | ✅ |
| 4 | Long press a message with one link | Open/Copy/Share Link present, no chooser | ✅ |
| 5 | Long press a message with two links | chooser sheet lists both, a11y says "Choose" | ✅ |
| 6 | Long press media without caption | View present, Copy absent | ✅ |
| 7 | Long press media **with** caption | View **and** Copy both present | ✅ |
| 8 | Long press a voice note | Reply/Forward/Share/Info/Delete only; no Copy, no View | ✅ |
| 9 | Long press a failed message | Retry present; Forward/Info/Report absent | ✅ |
| 10 | Long press a deleted or moderated message | menu offers nothing actionable | ✅ |
| 11 | Info's wording adapts to thread kind | group → "See who has read this message"; direct → "See delivery details…" | ✅ *added while writing this report — see below* |
| 12 | Menu never lists an unimplemented action | `save`, `saveMedia` filtered out | ✅ |
| 13 | Edit hands the composer over and restores the draft on cancel | banner reads "Editing message"; draft returns | ✅ |
| 14 | Server refuses the edit | error surfaces, original body stays, new text never shown | ✅ |
| 15 | Forward reports the **server's** count | 2 selected, server accepts 1 → "Forwarded to 1." | ✅ |
| 16 | Forward excludes the current conversation | current thread absent from the picker | ✅ |
| 17 | Info tells the truth about group read state | "Read by at least one person" | ✅ |
| 18 | Info drops rows with no value | no "Edited", no "Duration" row | ✅ |
| 19 | View opens the gallery on the pressed message | host fetches around attachment 5501 | ✅ |
| 20 | Reaction strip renders above, menu below, rest dimmed | layout contract holds | ✅ |

```
Test Suites: 483 passed, 483 total
Tests:       8303 passed, 8303 total
```

**Row 11 was an overclaim when this report was first drafted.** Writing the
matrix out forced a check of each row against the actual test names, and the
group/direct branch of Info's accessibility label turned out to exist in
`domain.ts` with nothing asserting it. Either side was free to collapse into the
other, and because the string is screen-reader-only, no sighted QA pass could
have caught the regression either. Rather than softening the row, the assertion
was added to both sides — the group label in "tells the truth about read state
in a group", the direct label in "never lists an action it cannot carry out" —
and then mutated to confirm it discriminates:

| Mutation | Suite response |
|---|---|
| Collapse the group branch into the direct label | 🔴 RED — correct |

Pinning only one side would have let the branch collapse toward whichever side
was tested, so both are asserted.

`npm run verify` = typecheck + i18n + jest. i18n validates **11 locales at
100%**, 4670/4670 keys, with only the 4 pre-existing warnings.

### Mutation pass — the part that found something

Green tests prove code runs, not that assertions discriminate. Three mutations
were introduced against the real implementation and the suite re-run:

| Mutation | Suite response |
|---|---|
| `info: true` → `false` | 🔴 RED (2 tests) — correct |
| Paint the edit optimistically before the server replies | 🔴 RED — correct |
| `Number(result.count ?? …)` → `conversationIds.length` | 🟢 **GREEN — a real hole** |

The third is the finding. The forward test selected one conversation and the
server returned 1, so the test could not distinguish the server's count from the
selection's length — the exact bug the code was written to prevent was invisible
to the test guarding it. Fixed by selecting **two** destinations while the server
accepts **one**, and asserting both the presence of "Forwarded to 1." and the
**absence** of "Forwarded to 2.". Re-ran the mutation: now 🔴 RED.

Two i18n traps were caught before they shipped, both of the kind that stay
invisible until a specific locale opens a specific sheet:

- A composed key, `` t(`messaging:chat.infoType_${kind}`) ``, is invisible to the
  extractor — a locale missing one of the four would ship the raw key as its
  value. Replaced with four written-out static keys.
- `{{count}}` triggers i18next plural resolution, which would demand six Arabic
  plural forms for a status toast. Renamed to `{{total}}`.

### Physical device

`e12be1ee4` is **built and installed on P3r7or** (iPhone 16 Pro, iOS 18.7.3).
Build succeeded on the first attempt from the warm rig; installed via
`devicectl`.

Bundle lineage was proved rather than assumed, using markers that **invert**
between lineages — three strings that did not exist before this commit — plus a
pre-existing control to prove the grep itself works:

| Marker | Expected | `strings -a main.jsbundle` |
|---|---|---|
| `Read by at least one person` | present only in this lineage | FOUND |
| `Forwarded to {{total}}.` | present only in this lineage | FOUND |
| `Editing message` | present only in this lineage | FOUND |
| `Open chat` (control) | present in every lineage | FOUND |

A marker that is always present proves nothing about staleness, and `buildNumber`
is a frozen literal so it never signals it either.

The simulator build (iPhone 17 Pro Max) follows sequentially — never in parallel,
because ReactCodegen generates into the shared source tree rather than into
derived data, so a separate `-derivedDataPath` does not isolate the two.

**The hands-on gesture pass on P3r7or is still outstanding.** Haptic timing, blur
performance behind the focused message, and swipe-to-dismiss all behave
differently under a real finger than under `fireEvent`, and this report does not
claim verification it has not done.

---

## FILES CHANGED

```
mobile-native/src/screens/ChatScreen.tsx                        529 +/-
mobile-native/src/screens/__tests__/ChatScreenMessageFocus.test.tsx  219 +
mobile-native/src/i18n/catalogs/{ar,de,en,es,fr,hi,ht,ja,ko,pt,zh}/extended.json
                                                                 32 + each
13 files changed, 1058 insertions(+), 42 deletions(-)
```

32 new keys under `messaging.chat`. Each catalog diff is exactly `+32` lines with
no reformatting: key-sorted, indent 2, non-ASCII preserved.

Earlier stages of this mission (the rule engine, the focused-message layout, the
link plumbing) landed in prior commits on `main`.

---

## COMMITS

- `e12be1ee4` — feat(messenger): the long-press menu carries out every action it lists

Gated after rebase, not before: the four commits that landed on `main` in the
interim touched no file under `mobile-native/`, and `npm run verify` was re-run
on the rebased SHA before pushing. Pushed as a pinned SHA
(`git push origin e12be1ee4:refs/heads/main`) rather than as a moving branch.

---

## FINAL JUDGMENT

**The mission is met, with two deliberate gaps and one deliberate refusal, all
named rather than quietly dropped.**

What works: the long press produces a context-aware experience, not a generic
menu. Availability is computed from one rule engine, links come from one parser
shared with the tap handler and the Universal Link router, and the menu is
structurally incapable of listing an action it cannot carry out. Edit, Forward,
Message Info and View are wired end to end. The voice hard-lock was honoured and
that was verified with the gate script rather than assumed.

**Gap 1 — `Save` (saved items).** Not shipped. `SavableContentType` has no member
for a message. This is a change to the saved-items contract and its storage, and
doing it inside a messenger mission would have meant inventing a content type
under time pressure in a subsystem this mission does not own.

**Gap 2 — `Save to Photos`.** Not shipped. It needs the granted URL, library
permission, a progress download, and Mux-manifest handling — all four of which
the media viewer already does correctly in one place. Users reach it in two taps
via View. I judged one correct implementation behind an extra tap better than a
second copy of a credential protocol that works until the day it expires
differently.

**Refusal — the reaction set.** The brief asks for ❤️👍😂😮😢🙏.
`QUICK_REACTIONS` ships ❤️😂😮😢**😡**👍 and is pinned by
`emojiFoundation.test.ts:141`. I did **not** change it. The set is a
cross-surface contract — the same six appear outside the messenger — so swapping
😡 for 🙏 is a product decision affecting other screens, not a messenger detail
to be changed in passing. **This needs a yes or no; it is a one-line change plus
a test update once decided.**

**Not yet verified:** the physical-iPhone pass. Automated tests do not replace
device QA for a gesture-driven surface — haptic timing, blur performance behind
the focused message, and sheet dismissal all behave differently under a real
finger than under `fireEvent`. The build is running; the pass follows.

Two honest notes on process, both of the same shape:

The forward-count mutation **survived its test** — the suite was green while the
assertion it relied on could not tell right from wrong. That is the worst kind
of finding, because nothing about a green run distinguishes it from a real one.

And row 11 of the matrix above was **an overclaim in the first draft**. Writing
the matrix is what caught it: checking each claimed row against the real test
names surfaced a branch with no assertion behind it.

Both argue the same thing — that the expensive checks (mutate the code, enumerate
the claims) are worth running every time rather than only when something already
feels shaky, because in both cases nothing felt shaky.
